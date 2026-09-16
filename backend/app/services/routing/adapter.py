"""DB rows → solver input, and the ID maps needed to get back again.

The solver speaks the roster's language — `employee_email`, `plate_no`,
`zone_name` — because that is what the notebook was written against and keeping
it that way is what lets `solved_routes.json` stay a usable fixture. The
database speaks in surrogate keys. This module is the only translation layer,
and it holds both directions so `writer` never has to re-query.

Two loading decisions worth stating, because they are not the obvious ones:

1. **Requests are NOT filtered on `route_id IS NULL`.** The old per-shift solver
   relied on that as its idempotency trick, but a whole-night solve cannot: if
   half the night is already routed, re-solving the remainder gives the fleet
   simulation a truncated request set and it happily produces a schedule that
   contradicts the routes already in the table. So we load the whole night every
   time and let `writer` replace the day atomically.

2. **The solve happens before anything is deleted.** Loading here is read-only;
   `writer.persist` does the clear and the insert together. A solver crash
   leaves yesterday's routes intact rather than an empty table.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from supabase import Client

logger = logging.getLogger(__name__)

# Requests in this state are not candidates for routing.
EXCLUDED_STATUSES = {"Rejected"}


def _num(value: Any) -> Optional[float]:
    """PostgREST hands back `numeric` columns inconsistently (number or string).

    The solver does arithmetic on every coordinate, so coerce once here rather
    than discovering a str/float TypeError halfway through a 148-route solve.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clock(value: Any) -> Optional[str]:
    """Normalise a TIME column to "HH:MM:SS"."""
    if value in (None, ""):
        return None
    text = str(value)
    parts = text.split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1]}:00"
    if len(parts) >= 3:
        return f"{parts[0].zfill(2)}:{parts[1]}:{parts[2][:2]}"
    return text


@dataclass
class RoutingContext:
    """Everything the writer needs to turn solver output back into rows."""

    service_date: str
    solver_input: Dict[str, Any] = field(default_factory=dict)

    # natural key → surrogate key
    employee_id_by_email: Dict[str, int] = field(default_factory=dict)
    vehicle_id_by_plate: Dict[str, int] = field(default_factory=dict)
    driver_id_by_plate: Dict[str, Optional[int]] = field(default_factory=dict)
    zone_id_by_name: Dict[str, int] = field(default_factory=dict)
    pickup_id_by_email: Dict[str, int] = field(default_factory=dict)
    dropoff_id_by_email: Dict[str, int] = field(default_factory=dict)

    # data-quality problems that are not unassigned requests
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    def request_id(self, request_type: str, email: str) -> Optional[int]:
        table = self.pickup_id_by_email if request_type == "pickup" else self.dropoff_id_by_email
        return table.get(email)

    def zone_id(self, zone_name: Optional[str]) -> Optional[int]:
        return self.zone_id_by_name.get(zone_name) if zone_name else None


def _dedupe_latest_per_employee(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Newest request per employee wins — the ad-hoc supersedes the weekly row.

    Same rule as `RoutingService._dedupe_latest_per_employee`; duplicated here so
    the adapter has no dependency on the service that calls it. An employee whose
    ad-hoc moved their 22:00 shift to 23:00 must be routed once, at 23:00 — not
    twice.
    """
    latest: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        key = row.get("employee_id")
        current = latest.get(key)
        if current is None or (row.get("created_at") or "") > (current.get("created_at") or ""):
            latest[key] = row
    return list(latest.values())


def _email_of(row: Dict[str, Any]) -> Optional[str]:
    """Reach through the embedded employee → users join for the natural key."""
    return ((row.get("employee") or {}).get("users") or {}).get("email")


def _name_of(row: Dict[str, Any]) -> Optional[str]:
    return ((row.get("employee") or {}).get("users") or {}).get("name")


class RoutingAdapter:
    """Read one night out of Supabase in five queries."""

    REQUEST_SELECT = "*, employee(employee_id, home_lat, home_lng, users(name, email)), zone(zone_name)"
    VEHICLE_SELECT = "*, zone(zone_name), driver(driver_id, user_id, users(name, email))"

    def __init__(self, db: Client):
        self.db = db
        # email → name, accumulated across both request tables so the solver can
        # label unassigned entries and passengers without a sixth query.
        self._employee_names: Dict[str, str] = {}

    def load(self, service_date: str) -> RoutingContext:
        ctx = RoutingContext(service_date=service_date)

        zones = self._load_zones(ctx)
        vehicles = self._load_vehicles(ctx)
        fixed_stops = self._load_fixed_stops(ctx)
        pickups = self._load_requests(ctx, "pickup_request", service_date)
        dropoffs = self._load_requests(ctx, "dropoff_request", service_date)

        ctx.solver_input = {
            "vehicles": vehicles,
            "pickup_requests": pickups,
            "dropoff_requests": dropoffs,
            "fixed_stops": fixed_stops,
            "employee_names": self._employee_names,
        }
        ctx.stats = {
            "zones": len(zones),
            "vehicles": len(vehicles),
            "pickup_requests": len(pickups),
            "dropoff_requests": len(dropoffs),
            "fixed_stops": len(fixed_stops),
        }
        logger.info("routing input for %s: %s", service_date, ctx.stats)
        return ctx

    # ── zones ────────────────────────────────────────────────────────────────

    def _load_zones(self, ctx: RoutingContext) -> List[Dict[str, Any]]:
        rows = (self.db.table("zone").select("zone_id, zone_name").execute().data) or []
        ctx.zone_id_by_name = {
            r["zone_name"]: r["zone_id"] for r in rows if r.get("zone_name")
        }
        if not rows:
            # Not fatal, but Case D (the 07:30 Agargaon Metro consolidation) and
            # the zone-preferred vehicle tiers both go quiet — no error, just
            # worse routes. Say so out loud.
            ctx.warnings.append(
                "zone table is empty — zone-based rules (Agargaon Metro "
                "consolidation, zone-preferred vehicles) are disabled. "
                "Apply data/migrations/001_routing_integration.sql."
            )
        return rows

    # ── vehicles ─────────────────────────────────────────────────────────────

    def _load_vehicles(self, ctx: RoutingContext) -> List[Dict[str, Any]]:
        rows = (
            self.db.table("vehicle")
            .select(self.VEHICLE_SELECT)
            .eq("status", "Active")
            .execute()
            .data
        ) or []

        fleet: List[Dict[str, Any]] = []
        no_zone: List[str] = []
        no_driver: List[str] = []
        for row in rows:
            plate = row.get("plate_no")
            if not plate:
                continue
            driver = row.get("driver") or {}
            driver_user = driver.get("users") or {}
            zone_name = (row.get("zone") or {}).get("zone_name")

            ctx.vehicle_id_by_plate[plate] = row["vehicle_id"]
            ctx.driver_id_by_plate[plate] = driver.get("driver_id")
            if not zone_name:
                no_zone.append(plate)
            if not driver.get("driver_id"):
                no_driver.append(plate)

            fleet.append({
                "plate_no": plate,
                "capacity": row.get("capacity") or 1,
                "zone_name": zone_name,
                "parking_lat": _num(row.get("parking_lat")),
                "parking_lng": _num(row.get("parking_lng")),
                "status": row.get("status"),
                "driver_email": driver_user.get("email"),
                "driver_name": driver_user.get("name"),
            })

        if not fleet:
            ctx.warnings.append("no Active vehicles — nothing can be routed.")
        if no_zone:
            ctx.warnings.append(
                f"{len(no_zone)} vehicle(s) have no zone, so they only qualify as "
                f"last-resort spare capacity: {', '.join(sorted(no_zone)[:5])}"
                + (" …" if len(no_zone) > 5 else "")
            )
        if no_driver:
            # The route still gets built; it just has nobody to drive it, which a
            # dispatcher needs to see before the night starts.
            ctx.warnings.append(
                f"{len(no_driver)} vehicle(s) have no driver assigned: "
                f"{', '.join(sorted(no_driver)[:5])}" + (" …" if len(no_driver) > 5 else "")
            )
        return fleet

    # ── Case A fixed stops + vehicle_shifts ──────────────────────────────────

    def _load_fixed_stops(self, ctx: RoutingContext) -> List[Dict[str, Any]]:
        """`vehicle_pickup_location` — never read by the backend before now.

        Load-bearing twice: it supplies the named fixed stops the 22:00/23:00
        shifts match against (Case A), and the solver derives `vehicle_shifts`
        from it — which shifts each car works — gating both the pickup
        "assigned vehicle" pool and the drop-off tier-1 pool. Empty here is not
        an error and produces no exception; every event just falls through to
        borrow-from-anywhere and Case A degrades to door-to-door.
        """
        # The deployed Supabase was created from an older schema than
        # `data/schema.sql` and can be missing this table entirely (PostgREST
        # PGRST205). A missing table must not 500 the whole solve — it is the
        # same degradation as an empty one, so report it and carry on.
        try:
            rows = (
                self.db.table("vehicle_pickup_location")
                .select("vehicle_id, pickup_lat, pickup_lng, location_name, sequence_order, shift_time")
                .execute()
                .data
            ) or []
        except Exception as exc:  # noqa: BLE001 - a solve must still complete
            logger.warning("vehicle_pickup_location unreadable: %s", exc)
            ctx.warnings.append(
                "vehicle_pickup_location could not be read — apply "
                "001_routing_integration.sql, which creates it. Case A "
                "(fixed-route matching) is disabled until then."
            )
            return []

        plate_by_id = {vid: plate for plate, vid in ctx.vehicle_id_by_plate.items()}
        stops: List[Dict[str, Any]] = []
        orphaned = 0
        for row in rows:
            plate = plate_by_id.get(row.get("vehicle_id"))
            if plate is None:
                # Belongs to an inactive or deleted vehicle — not usable this night.
                orphaned += 1
                continue
            stops.append({
                "vehicle_plate": plate,
                "pickup_lat": _num(row.get("pickup_lat")),
                "pickup_lng": _num(row.get("pickup_lng")),
                "location_name": row.get("location_name"),
                "sequence_order": row.get("sequence_order"),
                "shift_time": _clock(row.get("shift_time")),
            })

        if not stops:
            ctx.warnings.append(
                "vehicle_pickup_location is empty — fixed-route matching (Case A) "
                "is disabled and every vehicle is treated as working every shift."
            )
        if orphaned:
            logger.info("%d fixed stop(s) skipped — vehicle not Active", orphaned)
        return stops

    # ── requests ─────────────────────────────────────────────────────────────

    def _load_requests(
        self, ctx: RoutingContext, table: str, service_date: str
    ) -> List[Dict[str, Any]]:
        rows = (
            self.db.table(table)
            .select(self.REQUEST_SELECT)
            .eq("service_date", service_date)
            .execute()
            .data
        ) or []

        rows = [r for r in rows if r.get("status") not in EXCLUDED_STATUSES]
        rows = _dedupe_latest_per_employee(rows)

        is_pickup = table == "pickup_request"
        id_field = "pickup_id" if is_pickup else "dropoff_id"
        id_map = ctx.pickup_id_by_email if is_pickup else ctx.dropoff_id_by_email

        out: List[Dict[str, Any]] = []
        no_email = 0
        no_zone = 0
        for row in rows:
            email = _email_of(row)
            if not email:
                # Without the natural key the solver's output cannot be mapped
                # back to this row, so routing it would lose the assignment.
                no_email += 1
                continue

            employee_id = row.get("employee_id")
            if employee_id is not None:
                ctx.employee_id_by_email[email] = employee_id
            id_map[email] = row[id_field]
            name = _name_of(row)
            if name:
                self._employee_names[email] = name

            zone_name = (row.get("zone") or {}).get("zone_name")
            if not zone_name:
                no_zone += 1

            if is_pickup:
                out.append({
                    "employee_email": email,
                    "zone_name": zone_name,
                    "pickup_lat": _num(row.get("pickup_lat")),
                    "pickup_lng": _num(row.get("pickup_lng")),
                    "shift_start_time": _clock(row.get("shift_start_time")),
                    "service_date": row.get("service_date"),
                    "request_type": row.get("request_type"),
                    "status": row.get("status"),
                    "vehicle_plate": row.get("vehicle_plate"),
                })
            else:
                out.append({
                    "employee_email": email,
                    "zone_name": zone_name,
                    "drop_lat": _num(row.get("drop_lat")),
                    "drop_lng": _num(row.get("drop_lng")),
                    "shift_end_time": _clock(row.get("shift_end_time")),
                    "drop_time": _clock(row.get("drop_time")),
                    "service_date": row.get("service_date"),
                    "status": row.get("status"),
                    "vehicle_plate": row.get("vehicle_plate"),
                })

        if no_email:
            ctx.warnings.append(
                f"{no_email} {table} row(s) skipped — no linked employee/user record."
            )
        if no_zone:
            # This is mismatch #10: with a NULL zone the 07:30 metro rule never
            # fires and the drop-off vehicle tiers collapse, silently.
            ctx.warnings.append(
                f"{no_zone} of {len(rows)} {table} row(s) have no zone_id — "
                "zone rules will not apply to them. "
                "Run data/migrations/002_zone_backfill.sql."
            )
        return out


def load(db: Client, service_date: str) -> RoutingContext:
    return RoutingAdapter(db).load(service_date)
