"""Persist a solved night: whole-day replace, batched.

The old path inserted one row per stop and one per passenger — for a 148-route
night that is roughly 1,600 sequential HTTP round trips to Supabase. This does
the same work in a low double-digit number of calls.

Two correctness points that matter more than the speed:

**The clear is a real clear.** `schema.sql` has no `ON DELETE CASCADE`, so the
order below is not stylistic — deleting a `route` before its `route_stop`
children raises a foreign-key violation. The previous implementation leaned on
`route_id IS NULL` filtering as implicit idempotency, which is unsafe for a
whole-night solve: a partially-routed day would re-solve against a truncated
request set and produce a fleet schedule contradicting the routes already
stored.

**Ids are correlated by natural key, never by array position.** Routes match on
`route_code`, stops on `(route_id, sequence_order)`. Trusting the order of a
batched insert's returned rows would be an undocumented dependency on
PostgREST's behaviour that fails silently — every stop attached to the wrong
route, no error anywhere.
"""
import logging
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from postgrest.exceptions import APIError
from supabase import Client

from app.services.routing.adapter import RoutingContext

logger = logging.getLogger(__name__)

# PostgREST's "column not in schema cache" — thrown if `route.geometry_source`
# hasn't been added yet (see the migration note on `_write_routes`). PostgREST
# validates against its own cached schema before touching Postgres, so this is
# its own error code, not the raw Postgres "undefined_column" (42703). Caught
# narrowly so any other insert failure still surfaces normally.
_UNKNOWN_COLUMN = "PGRST204"

# PostgREST puts filter values in the URL, so a long `.in_()` list can blow past
# the server's URL length limit. Chunk well below it.
FILTER_CHUNK = 150

# Rows per insert call. Large enough that a whole night is a handful of calls,
# small enough to stay under request body limits.
INSERT_CHUNK = 500


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _time_only(iso_timestamp: Optional[str]) -> Optional[str]:
    """"2026-01-05T20:36:41" → "20:36:41".

    `route_stop.arrival_time` and `departure_time` are bare TIME columns, but the
    solver plans across midnight, so the date half is dropped here. Every
    existing consumer already reads these as wall-clock and infers the day from
    `route.service_date` plus the overnight convention, so this matches current
    behaviour rather than introducing a new ambiguity. (Storing true instants
    would mean a TIMESTAMPTZ column alongside — deliberately out of scope.)
    """
    if not iso_timestamp:
        return None
    text = str(iso_timestamp)
    return text.split("T", 1)[1][:8] if "T" in text else text[:8]


class RoutingWriter:
    def __init__(self, db: Client):
        self.db = db
        self.calls = 0  # round-trip counter, reported so regressions are visible

    def _count(self, n: int = 1) -> None:
        self.calls += n

    # ── clear ────────────────────────────────────────────────────────────────

    def clear(self, service_date: str) -> Dict[str, int]:
        """Remove every route for one service date and unlink its requests."""
        routes = (
            self.db.table("route").select("route_id").eq("service_date", service_date).execute().data
        ) or []
        self._count()
        route_ids = [r["route_id"] for r in routes]
        deleted = {"routes": len(route_ids), "stops": 0, "passengers": 0}

        # Unlink requests and put them back in play. Rejected requests are left
        # alone — they were never routed and must not be resurrected as Pending.
        for table in ("pickup_request", "dropoff_request"):
            (
                self.db.table(table)
                .update({"route_id": None, "status": "Pending"})
                .eq("service_date", service_date)
                .neq("status", "Rejected")
                .execute()
            )
            self._count()

        if route_ids:
            stop_ids: List[int] = []
            for chunk in _chunks(route_ids, FILTER_CHUNK):
                rows = (
                    self.db.table("route_stop")
                    .select("stop_id")
                    .in_("route_id", list(chunk))
                    .execute()
                    .data
                ) or []
                self._count()
                stop_ids.extend(r["stop_id"] for r in rows)
            deleted["stops"] = len(stop_ids)

            # deepest children first — no cascade exists to do it for us
            for chunk in _chunks(stop_ids, FILTER_CHUNK):
                self.db.table("stop_passenger").delete().in_("stop_id", list(chunk)).execute()
                self._count()
            for chunk in _chunks(route_ids, FILTER_CHUNK):
                self.db.table("route_stop").delete().in_("route_id", list(chunk)).execute()
                self._count()
            for chunk in _chunks(route_ids, FILTER_CHUNK):
                self.db.table("route_assignment").delete().in_("route_id", list(chunk)).execute()
                self._count()

            # pickup_request.route_id and dropoff_request.route_id both reference
            # route(route_id), and schema.sql has no ON DELETE CASCADE — the link
            # is broken above, so the routes can go now. This only bites when
            # re-solving an already-routed date, which the weekly auto-pass does.
            for chunk in _chunks(route_ids, FILTER_CHUNK):
                self.db.table("route").delete().in_("route_id", list(chunk)).execute()
                self._count()

        logger.info("cleared %s for %s", deleted, service_date)
        return deleted

    # ── write ────────────────────────────────────────────────────────────────

    def _insert(self, table: str, rows: List[Dict[str, Any]]) -> None:
        for chunk in _chunks(rows, INSERT_CHUNK):
            self.db.table(table).insert(list(chunk)).execute()
            self._count()

    def _write_routes(self, solved, ctx: RoutingContext, engine: Optional[str] = None) -> Dict[str, int]:
        """Insert routes, then read back `route_code → route_id`."""
        payload = []
        for r in solved.routes:
            is_pickup = r["type"] == "pickup"
            payload.append({
                "route_code": r["route_instance_id"],
                "zone_id": ctx.zone_id(r.get("zone_name")),
                "route_type": r["type"],
                "service_date": ctx.service_date,
                "shift_time": r["shift_time"],
                "total_distance_km": r["total_distance_km"],
                # the column is an integer minute count
                "total_travel_time_min": int(round(r["total_minutes"])),
                "route_geometry": r.get("route_geometry"),
                # Which DistanceProvider actually produced this route's geometry
                # ("osrm" = real road path, "haversine" = straight-line fallback).
                # Without this, telling the two apart after the fact means
                # forensically counting geometry points against created_at —
                # this makes it a one-column query instead.
                "geometry_source": engine,
            })
        try:
            self._insert("route", payload)
        except APIError as exc:
            if exc.code != _UNKNOWN_COLUMN or "geometry_source" not in str(exc):
                raise
            # Schema migration (`ALTER TABLE route ADD COLUMN geometry_source
            # text;`) hasn't been applied yet. Degrade instead of failing the
            # whole solve over a column that's purely for after-the-fact
            # auditing — but say so loudly, since silence is exactly how the
            # straight-line-geometry bug went unnoticed for so long.
            logger.error(
                "route.geometry_source column does not exist yet — retrying this "
                "insert without it. Run the pending migration to add it "
                "(ALTER TABLE route ADD COLUMN geometry_source text;) so future "
                "solves record which engine produced each route's geometry."
            )
            for row in payload:
                row.pop("geometry_source", None)
            self._insert("route", payload)

        code_to_id: Dict[str, int] = {}
        rows = (
            self.db.table("route")
            .select("route_id, route_code")
            .eq("service_date", ctx.service_date)
            .execute()
            .data
        ) or []
        self._count()
        for row in rows:
            if row.get("route_code"):
                code_to_id[row["route_code"]] = row["route_id"]

        missing = [r["route_instance_id"] for r in solved.routes if r["route_instance_id"] not in code_to_id]
        if missing:
            raise RuntimeError(
                f"{len(missing)} route(s) did not come back after insert, e.g. {missing[:3]}. "
                "Has data/migrations/001_routing_integration.sql been applied "
                "(route.route_code)?"
            )
        return code_to_id

    def _write_stops(self, solved, code_to_id: Dict[str, int]) -> Dict[Tuple[int, int], int]:
        """Insert stops, then read back `(route_id, sequence_order) → stop_id`."""
        payload = []
        for s in solved.stops:
            payload.append({
                "route_id": code_to_id[s["route_instance_id"]],
                "latitude": s["stop_lat"],
                "longitude": s["stop_lng"],
                "sequence_order": s["sequence_order"],
                "arrival_time": _time_only(s.get("arrival_time")),
                "departure_time": _time_only(s.get("departure_time")),
                "stop_name": s.get("stop_name"),
                "is_adhoc": bool(s.get("is_adhoc")),
                "is_shared": bool(s.get("is_shared")),
            })
        self._insert("route_stop", payload)

        route_ids = sorted(set(code_to_id.values()))
        key_to_stop: Dict[Tuple[int, int], int] = {}
        for chunk in _chunks(route_ids, FILTER_CHUNK):
            rows = (
                self.db.table("route_stop")
                .select("stop_id, route_id, sequence_order")
                .in_("route_id", list(chunk))
                .execute()
                .data
            ) or []
            self._count()
            for row in rows:
                key_to_stop[(row["route_id"], row["sequence_order"])] = row["stop_id"]
        return key_to_stop

    def _write_passengers(
        self,
        solved,
        ctx: RoutingContext,
        code_to_id: Dict[str, int],
        key_to_stop: Dict[Tuple[int, int], int],
    ) -> Tuple[int, List[str]]:
        payload = []
        warnings: List[str] = []
        unknown = 0
        for p in solved.passengers:
            route_id = code_to_id[p["route_instance_id"]]
            stop_id = key_to_stop.get((route_id, p["sequence_order"]))
            employee_id = ctx.employee_id_by_email.get(p["employee_id"])
            if stop_id is None or employee_id is None:
                unknown += 1
                continue
            payload.append({
                "stop_id": stop_id,
                "employee_id": employee_id,
                "boarded_status": False,
            })
        if unknown:
            warnings.append(f"{unknown} passenger row(s) could not be linked to a stop/employee.")
        self._insert("stop_passenger", payload)
        return len(payload), warnings

    def _write_assignments(self, solved, ctx: RoutingContext, code_to_id: Dict[str, int]) -> int:
        payload = []
        for r in solved.routes:
            plate = r["plate_no"]
            vehicle_id = ctx.vehicle_id_by_plate.get(plate)
            if vehicle_id is None:
                continue
            if r["type"] == "pickup":
                departure, arrival = r.get("parking_departure"), r.get("office_arrival")
            else:
                departure, arrival = r.get("office_departure"), r.get("parking_arrival")
            payload.append({
                "route_id": code_to_id[r["route_instance_id"]],
                "vehicle_id": vehicle_id,
                "driver_id": ctx.driver_id_by_plate.get(plate),
                # the old code left these NULL; the solver knows them, so store them
                "departure_time": _time_only(departure),
                "arrival_time": _time_only(arrival),
                "status": "Scheduled",
            })
        self._insert("route_assignment", payload)
        return len(payload)

    def _link_requests(self, solved, ctx: RoutingContext, code_to_id: Dict[str, int]) -> Dict[str, int]:
        """Point each routed request at its route.

        Grouped by (table, route_id): one UPDATE per route rather than one per
        passenger. A bulk upsert keyed on the primary key would collapse this
        further, but it would also happily INSERT a brand-new row for any id that
        no longer exists — not a trade worth making on the write path that
        decides what employees see.
        """
        groups: Dict[Tuple[str, int], List[int]] = {}
        counts = {"pickup": 0, "dropoff": 0}
        for p in solved.passengers:
            request_id = ctx.request_id(p["type"], p["employee_id"])
            if request_id is None:
                continue
            route_id = code_to_id[p["route_instance_id"]]
            table = "pickup_request" if p["type"] == "pickup" else "dropoff_request"
            groups.setdefault((table, route_id), []).append(request_id)
            counts[p["type"]] += 1

        for (table, route_id), ids in groups.items():
            id_field = "pickup_id" if table == "pickup_request" else "dropoff_id"
            for chunk in _chunks(sorted(set(ids)), FILTER_CHUNK):
                (
                    self.db.table(table)
                    .update({"route_id": route_id, "status": "Approved"})
                    .in_(id_field, list(chunk))
                    .execute()
                )
                self._count()
        return counts

    # ── entry point ──────────────────────────────────────────────────────────

    def persist(self, solved, ctx: RoutingContext, engine: Optional[str] = None) -> Dict[str, Any]:
        """Replace the service date's routes with this solve. Returns a summary."""
        self.calls = 0
        cleared = self.clear(ctx.service_date)

        warnings = list(ctx.warnings) + list(solved.warnings)
        if not solved.routes:
            logger.warning("solve for %s produced no routes", ctx.service_date)
            return {
                "service_date": ctx.service_date,
                "cleared": cleared,
                "routes_created": 0,
                "stops_created": 0,
                "passengers_created": 0,
                "assignments_created": 0,
                "linked": {"pickup": 0, "dropoff": 0},
                "db_calls": self.calls,
                "warnings": warnings,
            }

        code_to_id = self._write_routes(solved, ctx, engine)
        key_to_stop = self._write_stops(solved, code_to_id)
        passengers, passenger_warnings = self._write_passengers(solved, ctx, code_to_id, key_to_stop)
        assignments = self._write_assignments(solved, ctx, code_to_id)
        linked = self._link_requests(solved, ctx, code_to_id)
        warnings.extend(passenger_warnings)

        summary = {
            "service_date": ctx.service_date,
            "cleared": cleared,
            "routes_created": len(code_to_id),
            "stops_created": len(solved.stops),
            "passengers_created": passengers,
            "assignments_created": assignments,
            "linked": linked,
            "db_calls": self.calls,
            "warnings": warnings,
        }
        logger.info("persisted %s", summary)
        return summary


def persist(db: Client, solved, ctx: RoutingContext, engine: Optional[str] = None) -> Dict[str, Any]:
    return RoutingWriter(db).persist(solved, ctx, engine)


def clear(db: Client, service_date: str) -> Dict[str, int]:
    return RoutingWriter(db).clear(service_date)


__all__ = ["RoutingWriter", "persist", "clear"]
