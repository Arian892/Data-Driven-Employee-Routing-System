r"""Manual smoke test: solve one service night with the XGBoost-driven solver
and print the routing result so it can be eyeballed. No API/server involved.

Pulls pickup/dropoff requests for a single service date out of
`data/full_test_data.json` (via the same `FakeDB` + `adapter.load` path the
real backend uses), runs them through `solve_night` (which now predicts leg
durations with `GPS_TRACE_ML/trained-model/inference_bundle.joblib` instead of
raw OSRM/haversine durations — see `app/services/routing/solver.py`), and
prints:

  - the solver's own INFO log line (`routing: solved <date> -> {...}`)
  - overall counts (routes / stops / passengers / unassigned)
  - a handful of full routes, stop-by-stop, with ML-predicted leg times
  - a sample of unassigned requests and why

Run:  python test_ml_routing_demo.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Solver logs via logging.getLogger("uvicorn.error") — show it on stdout.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)

from fake_supabase import FakeDB
from app.services.routing import adapter as routing_adapter
from app.services.routing.config import SolverConfig
from app.services.routing.distance import HaversineProvider
from app.services.routing.solver import solve_night
from test_routing_pipeline import load_fixture_into, FIXTURE


def show_route(solved, route) -> None:
    print(f"\n--- {route['route_instance_id']}  ({route['type']}, shift {route['shift_time']}) ---")
    print(
        f"  vehicle={route['plate_no']}  capacity={route['capacity']}  "
        f"passengers={route['assigned_passengers']}  stops={route['stop_count']}  "
        f"total_minutes={route['total_minutes']}  total_distance_km={route['total_distance_km']}"
    )
    stops = sorted(
        (s for s in solved.stops if s["route_instance_id"] == route["route_instance_id"]),
        key=lambda s: s["sequence_order"],
    )
    for s in stops:
        print(
            f"    [{s['sequence_order']}] {s['stop_name']:<38} "
            f"arrive={s['arrival_time']}  depart={s['departure_time']}  "
            f"leg={s['leg_minutes_from_previous']:>5.1f} min / {s['leg_km_from_previous']:>5.2f} km  "
            f"pax={s['passenger_count']}"
        )


def main() -> int:
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 2

    db = FakeDB()
    service_date = load_fixture_into(db)
    print(f"\nService date (one night): {service_date}")

    ctx = routing_adapter.load(db, service_date)
    si = ctx.solver_input
    print(
        f"Pulled from dataset: {len(si['pickup_requests'])} pickup requests, "
        f"{len(si['dropoff_requests'])} dropoff requests, {len(si['vehicles'])} vehicles, "
        f"{len(si['fixed_stops'])} fixed stops"
    )

    # No OSRM server needed for this smoke test — haversine stands in for the
    # "raw" distance/duration feature the ML model also needs internally.
    solved = solve_night(
        service_date=service_date,
        provider=HaversineProvider(),
        cfg=SolverConfig(),
        **si,
    )

    print("\n=== Solve summary ===")
    print(solved.counts())
    for w in solved.warnings:
        print("WARNING:", w)

    pickup_routes = [r for r in solved.routes if r["type"] == "pickup"]
    dropoff_routes = [r for r in solved.routes if r["type"] == "dropoff"]

    print(f"\n{'=' * 70}\nSample PICKUP routes (showing 3 of {len(pickup_routes)})\n{'=' * 70}")
    for r in pickup_routes[:3]:
        show_route(solved, r)

    print(f"\n{'=' * 70}\nSample DROPOFF routes (showing 3 of {len(dropoff_routes)})\n{'=' * 70}")
    for r in dropoff_routes[:3]:
        show_route(solved, r)

    print(f"\n{'=' * 70}\nUnassigned sample (10 of {len(solved.unassigned)})\n{'=' * 70}")
    for u in solved.unassigned[:10]:
        print(f"  {u['employee_name']:<28} type={u['type']:<8} shift={u['shift_time']} reason={u['reason']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
