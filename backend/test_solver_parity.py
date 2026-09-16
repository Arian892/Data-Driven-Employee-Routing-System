"""Solver parity + invariant test — no database, no backend server needed.

Feeds `../data/full_test_data.json` straight through `solve_night` and checks the
result. Two passes:

1. **OSRM pass** (skipped when no OSRM server answers on localhost:5000) —
   asserts the exact counts the notebook produced, so the port is provably
   faithful rather than merely plausible.
2. **Haversine pass** (always runs) — asserts only the structural invariants,
   because straight-line distances legitimately produce a different schedule.
   This is what proves the no-OSRM deploy still emits a valid night.

Invariants checked in both passes:

- accounting: every request is either routed or reported unassigned, exactly once
- fleet state: no vehicle is on two overlapping trips
- capacity: no route carries more passengers than its vehicle's seats
- Case A: fixed-route stops exist for the 22:00/23:00 shifts
- Case D: the 07:30 drop-off consolidates onto Agargaon Metro
  (this one is load-bearing — it silently disappears if request zones are NULL)

Run:  python test_solver_parity.py
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.routing.config import SolverConfig
from app.services.routing.distance import HaversineProvider, OsrmProvider, FootOsrmProvider
from app.services.routing.solver import solve_night

# `data/` is a sibling of the app repo, not inside it.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FIXTURE = DATA_DIR / "full_test_data.json"
REFERENCE = DATA_DIR / "solved_routes.json"

# The script's own printed output for this fixture against local OSRM servers
# (car-night on :5000 AND foot on :5001, as `test-routing/run_osrm.sh` starts
# them). `data/solved_routes_walk20_retw0.json` is that run's artifact — this
# is the baseline the port must reproduce. The parity pass runs with
# `use_ml=False` so durations are raw OSRM exactly like the script; the
# production path keeps the XGBoost duration layer.
EXPECTED = {
    "routes": 246,
    "pickup_routes": 86,
    "dropoff_routes": 160,
    "stops": 1100,
    "passengers": 1295,
    "unassigned": 58,
}
EXPECTED_REASONS = {
    "no_coordinates": 36,
    "vehicle_not_free_in_time": 22,
}

FAILURES = []
CHECKS = 0


def check(label, ok, detail=""):
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
        FAILURES.append(label)


def build_input():
    """Fixture → solver kwargs.

    Deliberately bypasses the adapter: this test must fail for solver reasons
    only, never because of a DB mapping.
    """
    data = json.loads(FIXTURE.read_text())
    employee_names = {u["email"]: u["name"] for u in data["users"]}
    # The notebook anchors the night on the earliest pickup service_date.
    service_date = min(pr["service_date"] for pr in data["pickup_requests"] if pr.get("service_date"))
    return {
        "service_date": service_date,
        "vehicles": data["vehicles"],
        "pickup_requests": data["pickup_requests"],
        "dropoff_requests": data["dropoff_requests"],
        "fixed_stops": data["vehicle_pickup_locations"],
        "employee_names": employee_names,
    }, data


def check_invariants(solved, data, label):
    counts = solved.counts()
    print(f"\n{label}: {counts}")

    # --- accounting: routed ∪ reported must cover every request, no overlap gaps
    routed = {(p["type"], p["employee_id"]) for p in solved.passengers}
    reported = {(u["type"], u["employee_email"]) for u in solved.unassigned}
    expected = ({("pickup", p["employee_email"]) for p in data["pickup_requests"]}
                | {("dropoff", d["employee_email"]) for d in data["dropoff_requests"]})
    missing = expected - routed - reported
    check(f"[{label}] accounting: no request silently lost",
          not missing, f"{len(missing)} unaccounted, e.g. {sorted(missing)[:3]}")

    # --- fleet state: a vehicle may not be on two trips at once (spec sec.3)
    windows = {}
    for r in solved.routes:
        if r["type"] == "pickup":
            a, b = r["parking_departure"], r["office_arrival"]
        else:
            # A drop-off's duty starts with the deadhead to the office, so the
            # occupied window is trip_start -> tour_end (not office_departure).
            a, b = r["trip_start"], r["tour_end"]
        windows.setdefault(r["plate_no"], []).append((a, b, r["route_instance_id"]))
    overlaps = []
    for plate, spans in windows.items():
        spans.sort()
        for i in range(len(spans) - 1):
            if spans[i + 1][0] < spans[i][1]:
                overlaps.append((plate, spans[i][2], spans[i + 1][2]))
    check(f"[{label}] fleet state: no vehicle double-booked",
          not overlaps, f"{len(overlaps)} overlaps, e.g. {overlaps[:2]}")

    # --- capacity
    over = [r for r in solved.routes if r["assigned_passengers"] > r["capacity"]]
    check(f"[{label}] capacity respected on every route",
          not over, f"{len(over)} over capacity, e.g. {[r['route_instance_id'] for r in over][:3]}")

    # --- 120-minute cap (the PASSENGER journey: first pickup / office -> last stop)
    cfg = SolverConfig()
    too_long = [r for r in solved.routes
                if r.get("passenger_total_minutes", r["total_minutes"]) > cfg.max_route_minutes + 1e-6]
    check(f"[{label}] no route exceeds the {cfg.max_route_minutes:.0f}-min cap",
          not too_long, f"{len(too_long)} over, e.g. {[(r['route_instance_id'], r.get('passenger_total_minutes')) for r in too_long][:3]}")

    # --- Case A: the 22:00/23:00 shifts must use named fixed stops, not homes
    fixed_stop_names = {s["location_name"] for s in data["vehicle_pickup_locations"]}
    case_a = [s for s in solved.stops
              if s["type"] == "pickup" and s["shift_time"] in ("22:00:00", "23:00:00")
              and s["stop_name"] in fixed_stop_names]
    check(f"[{label}] Case A: fixed-route stops used for 22:00/23:00",
          len(case_a) > 0, "no named fixed stop appears — vehicle_pickup_locations not loaded?")

    # --- Case D: silently disappears if request zone_name is NULL (mismatch #10)
    metro = [s for s in solved.stops
             if s["type"] == "dropoff" and "Agargaon Metro" in (s["stop_name"] or "")]
    check(f"[{label}] Case D: 07:30 drop-off consolidates onto Agargaon Metro",
          len(metro) > 0, "no metro stop — request zone_name did not resolve")
    if metro:
        shared = sum(1 for s in metro if s.get("is_shared"))
        check(f"[{label}] Case D: metro stops flagged is_shared",
              shared == len(metro), f"{shared}/{len(metro)} flagged")

    # --- geometry present for the map
    no_geom = [r for r in solved.routes if not r.get("route_geometry")]
    check(f"[{label}] every route carries drawable geometry",
          not no_geom, f"{len(no_geom)} routes without geometry")

    return counts


def main():
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 2
    kwargs, data = build_input()
    print(f"Fixture: {FIXTURE}")
    print(f"Service date (night anchor): {kwargs['service_date']}")
    print(f"Inputs: {len(kwargs['vehicles'])} vehicles, "
          f"{len(kwargs['pickup_requests'])} pickup, "
          f"{len(kwargs['dropoff_requests'])} dropoff, "
          f"{len(kwargs['fixed_stops'])} fixed stops")

    # ── pass 1: OSRM (exact parity) ──────────────────────────────────────────
    # Needs BOTH engines: the car graph for durations/geometry and the foot
    # graph for Case A walk times. `use_ml=False` keeps durations raw OSRM so
    # the counts are comparable to the script's artifact.
    osrm = OsrmProvider()
    foot = FootOsrmProvider()
    if osrm.healthy() and foot.healthy():
        solved = solve_night(provider=osrm, foot=foot, use_ml=False, **kwargs)
        counts = check_invariants(solved, data, "osrm")
        for key, want in EXPECTED.items():
            check(f"[osrm] parity {key} == {want}", counts[key] == want, f"got {counts[key]}")
        reasons = Counter(u["reason"] for u in solved.unassigned)
        for reason, want in EXPECTED_REASONS.items():
            check(f"[osrm] parity reason {reason} == {want}",
                  reasons.get(reason, 0) == want, f"got {reasons.get(reason, 0)}")
        for w in solved.warnings:
            print(f"  warn  {w}")
    else:
        print(f"\nSKIP osrm pass: need car OSRM at {osrm.base_url} and foot OSRM at {foot.base_url}")
        print("     Start both with `test-routing/run_osrm.sh`. Exact parity is UNVERIFIED without them.")
    osrm.close()
    foot.close()

    # ── pass 2: haversine (invariants only) ──────────────────────────────────
    solved = solve_night(provider=HaversineProvider(), **kwargs)
    check_invariants(solved, data, "haversine")
    for w in solved.warnings:
        print(f"  warn  {w}")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
