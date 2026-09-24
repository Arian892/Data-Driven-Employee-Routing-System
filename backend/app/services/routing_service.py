"""Routing orchestration: load → solve → persist.

This module used to *be* the router: it sliced requests into capacity-sized
chunks, ordered them nearest-neighbour, and wrote a row per stop. That
placeholder is what the frontend has been displaying. The real algorithm now
lives in `app.services.routing` and this file is the thin seam that connects it
to the database.

**Why every entry point solves the whole night.** The algorithm simulates fleet
state across the entire service date — where each car is, when it is next free,
how many seats are left. A pickup route's end position determines which car can
serve which drop-off six hours later. Solving pickups alone would leave every
vehicle's end-of-night position undefined and make the drop-off pass infeasible,
so `run_pickup_routing` runs the full solve and reports its pickup half. The
admin UI is labelled accordingly.

The public surface is unchanged, so `app/scheduler.py`, `app/routers/admin.py`,
`route_service`, the driver views and the frontend all keep working.
"""
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from supabase import Client

from app.models.route import (
    DropoffRoutingRunPayload,
    PickupRoutingInputResponse,
    PickupRoutingRunPayload,
    RoutingRunResponse,
    UnassignedEntry,
)
from app.services.routing import adapter as routing_adapter
from app.services.routing import writer as routing_writer
from app.services.routing.config import SolverConfig
from app.services.routing.distance import HaversineProvider, get_foot_provider, get_provider
from app.services.routing.solver import solve_night
from app.services.week_service import OFFICE_LOCATION

logger = logging.getLogger("uvicorn.error")

# A solve deletes the whole service date before rewriting it, so two concurrent
# runs can interleave one run's delete with the other's insert and leave the day
# half-built. Every entry point takes this lock.
#
# Reentrant on purpose: `scheduler.run_pending_routing` holds it across a loop of
# service dates and then calls `run_service_date`, which takes it again on the
# same thread.
ROUTING_LOCK = threading.RLock()

# Guards against a date being re-solved immediately after it just succeeded —
# observed in practice: a slow solve (minutes) outlives an nginx proxy timeout
# (seconds), and either the client or the proxy retries the same request, or an
# unrelated caller (scheduler tick, admin click) fires for the same date while
# the first attempt is still running. `ROUTING_LOCK` only prevents the two
# attempts from literally overlapping their DB writes — it does nothing to stop
# the SECOND one from queuing up and redoing the exact same work once the first
# finishes, which is a second independent roll of the dice on OSRM being
# reachable at that moment. This is a separate, lightweight lock: checked before
# any expensive work starts, keyed by service_date, protecting every entry point
# (scheduler and admin-triggered alike) uniformly.
_DEDUP_LOCK = threading.Lock()
_IN_PROGRESS: set[str] = set()
_LAST_SOLVED_AT: Dict[str, float] = {}
_DEDUP_WINDOW_SECONDS = 120.0


class DuplicateSolveError(RuntimeError):
    """A solve for this service date is already running or just completed."""


class RoutingService:
    def __init__(self, db: Client):
        self.db = db

    def get_pickup_routing_input(self, service_date: str, shift_start_time: Optional[str] = None) -> PickupRoutingInputResponse:
        query = self.db.table("pickup_request").select("*, employee(employee_id, users(name)), zone(zone_name)")
        query = query.eq("service_date", service_date)
        if shift_start_time:
            query = query.eq("shift_start_time", shift_start_time)
        res = query.execute()
        rows = res.data or []

        total = len(rows)
        pending = sum(1 for row in rows if row.get("status") == "Pending")
        approved = sum(1 for row in rows if row.get("status") == "Approved")
        rejected = sum(1 for row in rows if row.get("status") == "Rejected")

        zone_counts: Dict[Optional[int], Dict[str, Any]] = {}
        for row in rows:
            zone = row.get("zone") or {}
            zid = row.get("zone_id")
            if zid not in zone_counts:
                zone_counts[zid] = {
                    "zone_id": zid,
                    "zone_name": zone.get("zone_name"),
                    "total_requests": 0,
                    "pending": 0,
                    "approved": 0,
                    "rejected": 0,
                }
            zone_counts[zid]["total_requests"] += 1
            status = row.get("status")
            if status == "Pending":
                zone_counts[zid]["pending"] += 1
            elif status == "Approved":
                zone_counts[zid]["approved"] += 1
            elif status == "Rejected":
                zone_counts[zid]["rejected"] += 1

        return PickupRoutingInputResponse(
            service_date=service_date,
            shift_start_time=shift_start_time,
            total_requests=total,
            pending=pending,
            approved=approved,
            rejected=rejected,
            zones=list(zone_counts.values()),
        )

    # ── public run entry points ──────────────────────────────────────────────

    def run_pickup_routing(self, payload: PickupRoutingRunPayload) -> RoutingRunResponse:
        """Solve the service date and report the pickup half.

        `shift_start_time` no longer narrows the solve — the fleet simulation is
        whole-night by construction — but it still narrows what is *reported*, so
        an admin checking one shift sees only that shift.
        """
        solved, ctx, summary, engine = self._solve(
            payload.service_date, payload.office_lat, payload.office_lng, payload.average_speed_kmph,
            force=payload.force,
        )
        return self._response(
            solved, ctx, summary, engine,
            request_type="pickup",
            shift_filter=payload.shift_start_time,
            message=f"Routing complete for {payload.service_date} (pickup view).",
        )

    def run_dropoff_routing(self, payload: DropoffRoutingRunPayload) -> RoutingRunResponse:
        """Solve the service date and report the drop-off half."""
        solved, ctx, summary, engine = self._solve(
            payload.service_date, payload.office_lat, payload.office_lng, payload.average_speed_kmph,
            force=payload.force,
        )
        return self._response(
            solved, ctx, summary, engine,
            request_type="dropoff",
            shift_filter=payload.shift_end_time,
            message=f"Routing complete for {payload.service_date} (drop-off view).",
        )

    # ── Auto-run driver (used by the scheduler + admin override) ──

    def pending_counts(self, service_date: str) -> dict:
        """How many un-routed pickup/dropoff requests exist for a service date."""
        def _count(table: str) -> int:
            res = (
                self.db.table(table)
                .select("*")
                .eq("service_date", service_date)
                .eq("status", "Pending")
                .is_("route_id", None)
                .execute()
            )
            return len(res.data or [])
        return {
            "pickup": _count("pickup_request"),
            "dropoff": _count("dropoff_request"),
        }

    def has_routes(self, service_date: str) -> bool:
        """Whether this service date has already been solved at least once.

        Almost every night leaves a handful of requests genuinely unroutable
        (bad coordinates, cap-shed, no vehicle available) — the same reasons a
        re-solve would hit again, since nothing about the inputs changed. That
        means `pending_counts()` almost never reaches zero, so using it as the
        "is this date done" signal makes the scheduler re-solve the WHOLE date
        (clear + rewrite, a fresh gamble on OSRM's availability) every time the
        backend process restarts and its in-memory `_processed_*` guards reset.
        Checking for existing routes instead is restart-proof: once a date has
        been solved, only a genuinely new trigger (the 7pm ad-hoc pass, or an
        admin's manual re-run) should touch it again — not a routine restart.
        """
        res = (
            self.db.table("route")
            .select("route_id")
            .eq("service_date", service_date)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def latest_route_created_at(self, service_date: str) -> Optional[str]:
        """ISO timestamp of the most recently created route for this date, if any."""
        res = (
            self.db.table("route")
            .select("created_at")
            .eq("service_date", service_date)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0]["created_at"] if res.data else None

    def run_service_date(
        self,
        service_date: str,
        office_lat: Optional[float] = None,
        office_lng: Optional[float] = None,
        force: bool = False,
    ) -> dict:
        """Route every request for one service date, pickups and drop-offs together.

        Idempotent by replacement, not by omission: the previous solve for this
        date is deleted and rewritten. The old implementation skipped requests
        that already carried a `route_id`, which is unsafe here — a half-routed
        day would re-solve against a truncated request set and produce a fleet
        schedule contradicting the routes already stored.

        `force` (see `_claim_solve_slot`) is only ever set by an explicit admin
        re-run of a single day; the scheduler's own calls always leave it False.
        """
        solved, ctx, summary, engine = self._solve(service_date, office_lat, office_lng, None, force=force)
        return {
            "service_date": service_date,
            "engine": engine,
            "counts": solved.counts(),
            "db_calls": summary.get("db_calls"),
            "pickup": self._response(
                solved, ctx, summary, engine,
                request_type="pickup",
                shift_filter=None,
                message=f"Pickup routing complete for {service_date}.",
            ),
            "dropoff": self._response(
                solved, ctx, summary, engine,
                request_type="dropoff",
                shift_filter=None,
                message=f"Dropoff routing complete for {service_date}.",
            ),
        }

    # ── internals ────────────────────────────────────────────────────────────

    def _solve(
        self,
        service_date: str,
        office_lat: Optional[float],
        office_lng: Optional[float],
        average_speed_kmph: Optional[float],
        force: bool = False,
    ):
        """Load, solve, persist — under the lock. Returns (solved, ctx, summary, engine)."""
        self._claim_solve_slot(service_date, force=force)
        try:
            office = (
                office_lat if office_lat is not None else OFFICE_LOCATION["lat"],
                office_lng if office_lng is not None else OFFICE_LOCATION["lng"],
            )
            cfg = SolverConfig(office=office)

            provider = get_provider()
            # `average_speed_kmph` is meaningful only for the straight-line fallback;
            # OSRM's durations come from the road network and overriding them would
            # be a lie about how long the trip takes.
            if average_speed_kmph and isinstance(provider, HaversineProvider):
                provider = HaversineProvider(average_speed_kmph=average_speed_kmph)
            # The pedestrian network (Case A walk times) is a separate engine; it
            # degrades to straight-line walking times if no foot server answers.
            foot = get_foot_provider()
            engine = getattr(provider, "name", "unknown")

            try:
                with ROUTING_LOCK:
                    ctx = routing_adapter.load(self.db, service_date)
                    logger.info(
                        "routing %s: engine=%s inputs=%s", service_date, engine, ctx.stats
                    )
                    solved = solve_night(
                        service_date=service_date,
                        provider=provider,
                        foot=foot,
                        cfg=cfg,
                        **ctx.solver_input,
                    )
                    summary = routing_writer.persist(self.db, solved, ctx, engine)
                logger.info("routing %s: %s", service_date, summary)
                return solved, ctx, summary, engine
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
                close_foot = getattr(foot, "close", None)
                if callable(close_foot):
                    close_foot()
        finally:
            self._release_solve_slot(service_date)

    @staticmethod
    def _claim_solve_slot(service_date: str, force: bool = False) -> None:
        """Reject a solve for `service_date` if one is already running, or one
        just finished within `_DEDUP_WINDOW_SECONDS`.

        A full solve takes minutes; an nginx proxy timeout is typically under a
        minute. That gap is exactly what let a retried or duplicate request
        silently redo — and sometimes worsen — a solve that had already
        succeeded, which is the actual failure mode observed in production.
        This makes that impossible: only one attempt per date can ever be live
        or freshly completed at a time, regardless of which caller (scheduler
        tick, admin click, a retried HTTP request) triggers it.

        `force` (admin-only, via `payload.force`) skips only the "just solved
        recently" check — a deliberate re-run right after fixing
        routing-affecting data is legitimate and shouldn't wait out the window.
        It never skips the "already running" check: two solves for the same
        date overlapping their clear-then-rewrite is a correctness issue, not
        just a wasted duplicate, so that guard is absolute regardless of force.
        """
        with _DEDUP_LOCK:
            if service_date in _IN_PROGRESS:
                raise DuplicateSolveError(
                    f"A solve for {service_date} is already running — refusing to start a second one."
                )
            if not force:
                last = _LAST_SOLVED_AT.get(service_date)
                if last is not None and (time.monotonic() - last) < _DEDUP_WINDOW_SECONDS:
                    raise DuplicateSolveError(
                        f"{service_date} was just solved {time.monotonic() - last:.0f}s ago — "
                        f"refusing to immediately re-solve (likely a retried/duplicate request). "
                        f"Pass force=true to override for a deliberate manual re-run."
                    )
            _IN_PROGRESS.add(service_date)

    @staticmethod
    def _release_solve_slot(service_date: str) -> None:
        with _DEDUP_LOCK:
            _IN_PROGRESS.discard(service_date)
            _LAST_SOLVED_AT[service_date] = time.monotonic()

    def _response(
        self,
        solved,
        ctx: routing_adapter.RoutingContext,
        summary: Dict[str, Any],
        engine: str,
        request_type: str,
        shift_filter: Optional[str],
        message: str,
    ) -> RoutingRunResponse:
        """One half of a whole-night solve, shaped as the old per-type response."""
        needle = str(shift_filter)[:5] if shift_filter else None

        def in_scope(shift_time: Optional[str]) -> bool:
            return needle is None or str(shift_time or "")[:5] == needle

        routes = [
            r for r in solved.routes
            if r["type"] == request_type and in_scope(r["shift_time"])
        ]
        route_codes = {r["route_instance_id"] for r in routes}
        assigned = sum(
            1 for p in solved.passengers
            if p["type"] == request_type and p["route_instance_id"] in route_codes
        )

        unassigned: List[UnassignedEntry] = []
        legacy_ids: List[int] = []
        for u in solved.unassigned:
            if u["type"] != request_type or not in_scope(u.get("shift_time")):
                continue
            email = u.get("employee_email")
            request_id = ctx.request_id(request_type, email) if email else None
            if request_id is not None:
                legacy_ids.append(request_id)
            unassigned.append(UnassignedEntry(
                employee_id=ctx.employee_id_by_email.get(email) if email else None,
                employee_name=u.get("employee_name"),
                employee_email=email,
                request_type=request_type,
                shift_time=u.get("shift_time"),
                reason=u["reason"],
                vehicle_id=ctx.vehicle_id_by_plate.get(u["vehicle_id"]) if u.get("vehicle_id") else None,
                plate_no=u.get("vehicle_id"),
            ))

        return RoutingRunResponse(
            routes_created=len(routes),
            employees_assigned=assigned,
            unassigned_pickup_ids=legacy_ids,
            unassigned=unassigned,
            engine=engine,
            warnings=list(summary.get("warnings") or []),
            message=message,
        )
