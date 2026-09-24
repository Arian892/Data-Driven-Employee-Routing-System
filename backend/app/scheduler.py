"""Automatic routing scheduler.

Two unattended passes, driven by a background loop started from the FastAPI
lifespan:

- Weekly: the current calendar week is always safe to route — its Friday/
  Saturday request window closed at the end of last week. The first loop tick
  of each new week (within a minute of the Saturday deadline passing) routes
  the whole week's pending requests in advance.
- Ad-hoc: each service day is re-routed at/just after 7 PM (when ad-hoc
  submissions close, three hours before the 10 PM shift) to fold in the day's
  ad-hoc requests.

An admin override endpoint can trigger the weekly pass on demand.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.database import supabase
from app.services.routing_service import ROUTING_LOCK, DuplicateSolveError, RoutingService
from app.services.week_service import ADHOC_CUTOFF_TIME, current_week_start

logger = logging.getLogger("uvicorn.error")

# Shared with RoutingService so an admin-triggered run and a scheduled one cannot
# interleave. It is an RLock, so holding it here and calling run_service_date —
# which takes it again on this thread — is fine.
_routing_lock = ROUTING_LOCK
# Service-week keys (target Sunday ISO date) already routed this process run.
_processed_weeks: set[str] = set()
# Service dates already rebuilt this process run by the ad-hoc (7 PM) pass.
# Needed because requests the solver cannot place (no_coordinates, cap drops)
# stay `Pending` with no route_id, so `pending_counts` never reaches zero for a
# solved date — without this guard the 60 s loop would re-solve today forever.
_processed_dates: set[str] = set()


def run_pending_routing(force: bool = False) -> dict:
    """Route the current calendar week's pending requests, once.

    The current week (Sunday → Saturday) is always locked and safe to route: its
    Friday/Saturday request window closed at the end of LAST week, so every
    regular request for it is already in the DB. Routing therefore needs no
    deadline comparison — it just must not repeat. `pending_counts` only counts
    un-routed rows, so already-solved dates (this pass earlier, or a process
    restart) are skipped automatically.

    Idempotent by replacement: each service date's previous solve is deleted and
    rewritten, so re-runs converge rather than duplicating routes.

    Note this can take minutes per service date — the solver simulates the whole
    night and queries OSRM — so never call it directly from the event loop. Both
    call sites use `asyncio.to_thread`.
    """
    with _routing_lock:
        now = datetime.now()
        start = current_week_start(now.date())
        week_key = start.isoformat()
        if not force and week_key in _processed_weeks:
            return {"ran": False, "reason": "current week already routed this process"}

        svc = RoutingService(supabase)
        summary = {"ran": False, "weeks": []}

        today = now.date().isoformat()
        for i in range(7):
            iso = (start + timedelta(days=i)).isoformat()
            if iso < today:
                # earlier this week is already being served — leave its routes
                # (and any ad-hoc rebuilds) exactly as they were solved
                continue
            # Restart-proof "already done" check — see `has_routes`'s docstring.
            # A date that already has routes is left alone here even if a few
            # requests are still stuck Pending (permanently unroutable ones,
            # not new work); today's date gets its own separate re-solve via
            # the 7pm ad-hoc pass below, and anything else is an explicit
            # admin action, not something a routine restart should trigger.
            if svc.has_routes(iso):
                continue
            counts = svc.pending_counts(iso)
            if counts["pickup"] == 0 and counts["dropoff"] == 0:
                continue
            try:
                result = svc.run_service_date(iso)
            except DuplicateSolveError as exc:
                logger.info("routing %s: skipped — %s", iso, exc)
                continue
            summary["ran"] = True
            summary["weeks"].append(
                {
                    "service_date": iso,
                    "counts": counts,
                    "result": result,
                }
            )

        _processed_weeks.add(week_key)
        return summary


def run_daily_rerouting(force: bool = False) -> dict:
    """Re-route today just after 7 PM so the day's ad-hoc requests get a route.

    The weekly pass routes the whole week in advance on its first tick; the
    nightly pass then fires at/just after 7 PM — when ad-hoc submissions close
    (three hours before the 10 PM shift) — picks up the day's ad-hoc rows and
    rebuilds the day. The ad-hoc supersedes the weekly request via the
    newest-wins rule, and because the solve replaces the whole date, the
    superseded weekly route disappears with it. Once a day is rebuilt,
    `pending_counts` drops to zero, so the loop won't repeat the work.
    """
    with _routing_lock:
        now = datetime.now()
        if not force and now.time() < ADHOC_CUTOFF_TIME:
            return {"ran": False, "reason": "ad-hoc re-routing runs after 7 PM (ad-hoc closes at 7 PM)"}

        today = now.date().isoformat()
        if not force and today in _processed_dates:
            return {"ran": False, "reason": "today already re-routed this process", "service_date": today}

        svc = RoutingService(supabase)
        counts = svc.pending_counts(today)
        if counts["pickup"] == 0 and counts["dropoff"] == 0:
            return {"ran": False, "reason": "nothing pending for today", "service_date": today}

        try:
            result = svc.run_service_date(today)
        except DuplicateSolveError as exc:
            return {"ran": False, "reason": str(exc), "service_date": today}

        _processed_dates.add(today)
        return {
            "ran": True,
            "service_date": today,
            "counts": counts,
            "result": result,
        }


async def start_routing_scheduler() -> None:
    """Periodic background loop, spawned from the FastAPI lifespan."""
    while True:
        try:
            await asyncio.to_thread(run_pending_routing)
            await asyncio.to_thread(run_daily_rerouting)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("routing scheduler iteration failed")
        await asyncio.sleep(settings.routing_check_interval_seconds)
