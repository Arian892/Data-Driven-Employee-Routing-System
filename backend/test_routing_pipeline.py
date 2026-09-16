"""End-to-end plug-in test: adapter → solve_night → writer, no database.

`test_solver_parity.py` proves the algorithm is a faithful port. This proves the
part the notebook never had to do — that solver output survives the round trip
into and out of the schema:

- the adapter reconstructs solver input from schema-shaped rows (including the
  `zone(zone_name)` join Case D depends on)
- the writer's delete order does not orphan a child row (the fake refuses,
  because `schema.sql` has no ON DELETE CASCADE)
- route ids are correlated by `route_code` and stop ids by
  `(route_id, sequence_order)` — never by the position of a returned array
- every routed request ends up pointing at the route that carries it
- re-running is idempotent: same counts, no duplicates, nothing orphaned
- the write is batched, not one call per row

Run:  python test_routing_pipeline.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.routing import adapter as routing_adapter
from app.services.routing import writer as routing_writer
from app.services.routing.config import SolverConfig
from app.services.routing.distance import HaversineProvider, get_provider
from app.services.routing.solver import solve_night
from fake_supabase import FakeDB

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FIXTURE = DATA_DIR / "full_test_data.json"

ZONE_DESCRIPTIONS = {
    "Zone-1": "Mirpur / Agargaon corridor",
    "Zone-2": "Uttara / Airport corridor",
    "Zone-3": "Dhanmondi / Mohammadpur corridor",
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


def load_fixture_into(db: FakeDB) -> str:
    """Fixture → schema-shaped rows, as `001` + `002` + the fixed generator leave them.

    Zones are seeded and every zone_id resolved, which is the post-migration
    state. A pre-migration database (zone_id NULL everywhere) is covered by the
    separate degradation check below.
    """
    data = json.loads(FIXTURE.read_text())

    zone_id = {}
    for name, description in ZONE_DESCRIPTIONS.items():
        row = db.seed("zone", {"zone_name": name, "description": description})
        zone_id[name] = row["zone_id"]

    user_id = {}
    for u in data["users"]:
        row = db.seed("users", {
            "name": u["name"], "email": u["email"], "phone": u.get("phone"),
            "password_hash": "x", "role": u.get("role", "Employee"), "status": "Active",
        })
        user_id[u["email"]] = row["user_id"]

    employee_id = {}
    for e in data["employees"]:
        row = db.seed("employee", {
            "user_id": user_id[e["user_email"]],
            "home_lat": e.get("home_lat"), "home_lng": e.get("home_lng"),
            "is_active": True,
        })
        employee_id[e["user_email"]] = row["employee_id"]

    driver_id = {}
    for d in data["drivers"]:
        row = db.seed("driver", {
            "user_id": user_id[d["user_email"]],
            "license_no": d.get("license_no", "L-0000"), "status": "Available",
        })
        driver_id[d["user_email"]] = row["driver_id"]

    vehicle_id = {}
    for v in data["vehicles"]:
        row = db.seed("vehicle", {
            "plate_no": v["plate_no"], "capacity": v["capacity"],
            "parking_lat": v.get("parking_lat"), "parking_lng": v.get("parking_lng"),
            "status": v.get("status", "Active"),
            "driver_id": driver_id.get(v.get("driver_email")),
            "zone_id": zone_id.get(v.get("zone_name")),
        })
        vehicle_id[v["plate_no"]] = row["vehicle_id"]

    for loc in data["vehicle_pickup_locations"]:
        db.seed("vehicle_pickup_location", {
            "vehicle_id": vehicle_id.get(loc["vehicle_plate"]),
            "pickup_lat": loc.get("pickup_lat"), "pickup_lng": loc.get("pickup_lng"),
            "location_name": loc.get("location_name"),
            "sequence_order": loc.get("sequence_order") or 1,
            "shift_time": loc.get("shift_time"),
        })

    service_date = min(pr["service_date"] for pr in data["pickup_requests"] if pr.get("service_date"))

    for i, pr in enumerate(data["pickup_requests"]):
        db.seed("pickup_request", {
            "employee_id": employee_id.get(pr["employee_email"]),
            "zone_id": zone_id.get(pr.get("zone_name")),
            "route_id": None,
            "pickup_lat": pr.get("pickup_lat"), "pickup_lng": pr.get("pickup_lng"),
            "shift_start_time": pr.get("shift_start_time"),
            "service_date": pr["service_date"],
            "request_type": pr.get("request_type", "Regular"),
            "status": pr.get("status", "Pending"),
            "pickup_time": pr.get("pickup_time"),
            "created_at": f"2026-01-01T00:00:{i % 60:02d}",
        })

    for i, dr in enumerate(data["dropoff_requests"]):
        db.seed("dropoff_request", {
            "employee_id": employee_id.get(dr["employee_email"]),
            "zone_id": zone_id.get(dr.get("zone_name")),
            "route_id": None,
            "drop_lat": dr.get("drop_lat"), "drop_lng": dr.get("drop_lng"),
            "shift_end_time": dr.get("shift_end_time"),
            # `001` normalises drop-offs onto the ROSTER day; the fixture still
            # carries the old next-morning date, so apply the same rule here.
            "service_date": service_date,
            "status": dr.get("status", "Pending"),
            "drop_time": dr.get("drop_time"),
            "created_at": f"2026-01-01T00:00:{i % 60:02d}",
        })

    return service_date


def make_provider():
    """The same selection the running backend makes.

    With OSRM up this exercises the production path — real road distances and
    real drawable geometry through the writer. With OSRM down `get_provider`
    degrades to haversine, so this test still runs on a laptop or in CI with no
    routing server. Either way the round-trip assertions below hold; only the
    counts differ, which is why none of them are hard-coded to OSRM's numbers.
    """
    provider = get_provider()
    if isinstance(provider, HaversineProvider):
        return HaversineProvider(average_speed_kmph=40.0)
    return provider


def run_once(db: FakeDB, service_date: str, provider):
    ctx = routing_adapter.load(db, service_date)
    solved = solve_night(
        service_date=service_date,
        provider=provider,
        cfg=SolverConfig(),
        **ctx.solver_input,
    )
    summary = routing_writer.persist(db, solved, ctx)
    return ctx, solved, summary


def main():
    if not FIXTURE.exists():
        print(f"FIXTURE MISSING: {FIXTURE}")
        return 2

    provider = make_provider()
    engine = getattr(provider, "name", "unknown")

    db = FakeDB()
    service_date = load_fixture_into(db)
    print(f"Fixture: {FIXTURE}")
    print(f"Engine:  {engine}")
    print(f"Service date: {service_date}")
    print(f"Seeded: {len(db.rows['vehicle'])} vehicles, "
          f"{len(db.rows['pickup_request'])} pickup, "
          f"{len(db.rows['dropoff_request'])} dropoff, "
          f"{len(db.rows['vehicle_pickup_location'])} fixed stops")

    # ── adapter ──────────────────────────────────────────────────────────────
    print("\nadapter")
    ctx = routing_adapter.load(db, service_date)
    inp = ctx.solver_input
    check("loads the whole night, not just un-routed rows",
          len(inp["pickup_requests"]) == 414 and len(inp["dropoff_requests"]) == 939,
          f"{len(inp['pickup_requests'])} pickup / {len(inp['dropoff_requests'])} dropoff")
    check("vehicle_pickup_location is loaded (Case A + vehicle_shifts)",
          len(inp["fixed_stops"]) == 186, f"{len(inp['fixed_stops'])} fixed stops")
    check("every request resolved a zone_name (Case D depends on it)",
          all(r["zone_name"] for r in inp["pickup_requests"])
          and all(r["zone_name"] for r in inp["dropoff_requests"]))
    check("every vehicle resolved a zone_name",
          all(v["zone_name"] for v in inp["vehicles"]), )
    check("coordinates are floats, not PostgREST strings",
          all(isinstance(r["pickup_lat"], (float, type(None))) for r in inp["pickup_requests"]))
    check("id maps cover every request",
          len(ctx.pickup_id_by_email) == 414 and len(ctx.dropoff_id_by_email) == 939)
    check("driver map is populated",
          sum(1 for v in ctx.driver_id_by_plate.values() if v) > 0)

    # ── solve + persist ──────────────────────────────────────────────────────
    print("\nsolve + persist")
    ctx, solved, summary = run_once(db, service_date, provider)
    counts = solved.counts()
    print(f"  solver: {counts}")
    print(f"  writer: routes={summary['routes_created']} stops={summary['stops_created']} "
          f"passengers={summary['passengers_created']} "
          f"assignments={summary['assignments_created']} db_calls={summary['db_calls']}")

    check("every solved route was written",
          summary["routes_created"] == counts["routes"],
          f"{summary['routes_created']} vs {counts['routes']}")
    check("every solved stop was written",
          len(db.rows["route_stop"]) == counts["stops"],
          f"{len(db.rows['route_stop'])} vs {counts['stops']}")
    check("every passenger was written",
          len(db.rows["stop_passenger"]) == counts["passengers"],
          f"{len(db.rows['stop_passenger'])} vs {counts['passengers']}")
    check("one assignment per route",
          len(db.rows["route_assignment"]) == counts["routes"])

    # id correlation: a stop's route must be the route the solver put it on
    by_code = {r["route_code"]: r for r in db.rows["route"]}
    check("route_code is unique per route", len(by_code) == counts["routes"])
    mismatched = 0
    for s in solved.stops:
        route_id = by_code[s["route_instance_id"]]["route_id"]
        stored = next((x for x in db.rows["route_stop"]
                       if x["route_id"] == route_id and x["sequence_order"] == s["sequence_order"]), None)
        if stored is None or stored["stop_name"] != s["stop_name"]:
            mismatched += 1
    check("stops correlate to the right route by (route_id, sequence_order)",
          mismatched == 0, f"{mismatched} mismatched")

    # geometry + labels actually landed
    check("routes carry geometry",
          all(r.get("route_geometry") for r in db.rows["route"]))
    check("stops carry names",
          all(s.get("stop_name") for s in db.rows["route_stop"]))
    if engine == "osrm":
        vertices = sum(len(r["route_geometry"]) for r in db.rows["route"])
        check("stored geometry is a road path, not a straight-line stand-in",
              vertices > counts["stops"] * 5,
              f"{vertices} vertices for {counts['stops']} stops — too few to be road-following")
        check("OSRM run reproduces the notebook's counts through the DB round trip",
              (counts["routes"], counts["stops"], counts["passengers"], counts["unassigned"])
              == (148, 680, 788, 40),
              f"{counts} != 148/680/788/40")

    metro = [s for s in db.rows["route_stop"] if "Agargaon Metro" in (s.get("stop_name") or "")]
    check("Case D metro stops persisted and flagged is_shared",
          bool(metro) and all(s.get("is_shared") for s in metro), f"{len(metro)} metro stops")

    # request back-links
    routed_pickups = [r for r in db.rows["pickup_request"] if r.get("route_id")]
    routed_dropoffs = [r for r in db.rows["dropoff_request"] if r.get("route_id")]
    check("routed requests are linked and Approved",
          all(r["status"] == "Approved" for r in routed_pickups + routed_dropoffs))
    check("unrouted requests are Pending with no route_id",
          all(r["status"] == "Pending"
              for r in db.rows["pickup_request"] + db.rows["dropoff_request"]
              if not r.get("route_id")))
    linked = len(routed_pickups) + len(routed_dropoffs)
    check("link count matches passengers routed",
          linked == summary["linked"]["pickup"] + summary["linked"]["dropoff"],
          f"{linked} linked vs {summary['linked']}")

    # a linked request must point at a route that actually carries that employee
    email_by_employee = {}
    for e in db.rows["employee"]:
        user = next(u for u in db.rows["users"] if u["user_id"] == e["user_id"])
        email_by_employee[e["employee_id"]] = user["email"]
    wrong = 0
    for row in routed_pickups:
        code = next(r["route_code"] for r in db.rows["route"] if r["route_id"] == row["route_id"])
        email = email_by_employee[row["employee_id"]]
        if not any(p["route_instance_id"] == code and p["employee_id"] == email
                   for p in solved.passengers):
            wrong += 1
    check("each linked pickup points at the route carrying that employee",
          wrong == 0, f"{wrong} wrong")

    # batching
    check("write is batched, not one call per row",
          summary["db_calls"] < 400,
          f"{summary['db_calls']} calls for {counts['stops']} stops + "
          f"{counts['passengers']} passengers (old path: ~1,600)")

    # ── idempotency ──────────────────────────────────────────────────────────
    print("\nre-solve (idempotency)")
    ctx2, solved2, summary2 = run_once(db, service_date, provider)
    check("re-solve produces the same route count, not double",
          len(db.rows["route"]) == counts["routes"], f"{len(db.rows['route'])} routes")
    check("re-solve leaves no orphaned stops",
          all(s["route_id"] in {r["route_id"] for r in db.rows["route"]}
              for s in db.rows["route_stop"]))
    check("re-solve leaves no orphaned passengers",
          all(p["stop_id"] in {s["stop_id"] for s in db.rows["route_stop"]}
              for p in db.rows["stop_passenger"]))
    check("re-solve is deterministic",
          solved2.counts() == counts, f"{solved2.counts()} vs {counts}")
    check("re-solve cleared the previous day",
          summary2["cleared"]["routes"] == counts["routes"],
          f"cleared {summary2['cleared']}")

    # ── the silent-failure guard ─────────────────────────────────────────────
    # Strip request zones the way a pre-migration database has them and confirm
    # the adapter says so instead of quietly producing worse routes.
    print("\npre-migration degradation is reported, not silent")
    bare = FakeDB()
    bare_date = load_fixture_into(bare)
    for row in bare.rows["pickup_request"] + bare.rows["dropoff_request"]:
        row["zone_id"] = None
    bare_ctx = routing_adapter.load(bare, bare_date)
    check("NULL request zones raise a warning naming the fix",
          any("002_zone_backfill" in w for w in bare_ctx.warnings),
          f"warnings: {bare_ctx.warnings}")

    print(f"\nwarnings from the real run: {len(ctx.warnings) + len(solved.warnings)}")
    for w in list(ctx.warnings) + list(solved.warnings):
        print(f"  warn  {w}")
    print(f"unassigned by reason: {dict(Counter(u['reason'] for u in solved.unassigned))}")

    close = getattr(provider, "close", None)
    if callable(close):
        close()

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
