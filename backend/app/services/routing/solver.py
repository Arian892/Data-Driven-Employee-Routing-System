"""The routing algorithm — a faithful port of `data/routing_unified.ipynb`.

This module is **pure**: no Supabase client, no `httpx`, no `datetime.now()`.
Everything it needs arrives as arguments, and the only I/O it performs goes
through the injected `DistanceProvider`. That is what makes it testable offline
and comparable against the notebook's `solved_routes.json` fixture.

Structure mirrors `system_data/Algo_refined.md`:

- **One chronological timeline** of interleaved pickup and drop-off events,
  anchored at 22:00 on the service date so ordering survives midnight.
- **Fleet state** per vehicle (`current_location`, `status`, `_free_at`) carries
  forward across the whole night, so a car starts from where it actually is.
- **Vehicle reuse**: the car that picked an employee up also drops them off,
  with borrowing when that car is busy.
- Service mode by shift time — Case A/B for pickup, Case C/D for drop-off.

Deviations from the notebook, all deliberate and all listed here:

1. The three crash sites are softened (a notebook may raise; a request handler
   may not): an event spanning several `shift_end_time`s is split instead of
   asserted, an unknown employee falls back to its email instead of raising
   `KeyError`, and vehicles with no parking coordinates are reported as warnings
   instead of vanishing.
2. Case D honours the "except Fridays" rule that the notebook's own header
   documents but its code omits. Controlled by `SolverConfig.apply_friday_exception`.
3. Route records carry an extra `zone_name` so the caller can set `route.zone_id`.

Nothing else differs: given the same inputs and the same OSRM server this
reproduces the notebook's 148 routes / 680 stops / 788 passengers / 40 unassigned.
"""
from __future__ import annotations

import logging
import math
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd

from app.services.routing.config import (
    FIXED_ROUTE_SHIFTS,
    MAIN_ROAD_DROP_TIME,
    MAIN_ROAD_ZONES,
    SolverConfig,
)
from app.services.routing.distance import DistanceProvider, haversine_km, walk_minutes

logger = logging.getLogger("uvicorn.error")

Coord = Tuple[float, float]

# Friday is weekday() == 4. Dhaka Metro Rail does not run on Fridays, which is
# why BDS exempts that day from the Agargaon Metro consolidation.
_FRIDAY = 4


# ──────────────────────────────────────────────────────────────────────────────
# ML travel-time model (GPS_TRACE_ML/trained-model/inference_bundle.joblib)
# ──────────────────────────────────────────────────────────────────────────────
#
# Leg *durations* for ordering (2-opt) and timing come from this trained
# XGBoost model instead of raw OSRM/haversine durations. Distance_km and route
# geometry are unaffected — they still come straight from the injected
# `DistanceProvider`, and the model itself needs that provider's own
# duration/distance as two of its input features (it is a correction layer on
# top of OSRM, not a replacement for it). Feature engineering here is a direct
# port of `GPS_TRACE_ML/test_inference_bundle.py`'s `build_feature_row`, kept
# self-contained since that project lives outside this backend package.

_ML_BUNDLE_ENV_VAR = "ROUTING_ML_MODEL_PATH"
_ML_BUNDLE_DEFAULT_PATH = Path(__file__).resolve().parent / "ml_model" / "inference_bundle.joblib"

_ml_bundle_cache: Optional[Dict[str, Any]] = None

_ML_FIXED_HOLIDAYS_MD = {(2, 21), (3, 26), (4, 14), (5, 1), (8, 15), (12, 16), (12, 25)}


def _load_ml_bundle() -> Dict[str, Any]:
    """Loads `inference_bundle.joblib` once per process."""
    global _ml_bundle_cache
    if _ml_bundle_cache is None:
        path = os.environ.get(_ML_BUNDLE_ENV_VAR, str(_ML_BUNDLE_DEFAULT_PATH))
        _ml_bundle_cache = joblib.load(path)
    return _ml_bundle_cache


def _ml_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _ml_traffic_bucket(hour: float) -> str:
    if hour < 6:
        return "night"
    elif hour < 8:
        return "morning_offpeak"
    elif hour < 10:
        return "morning_rush"
    elif hour < 17:
        return "midday"
    elif hour < 21:
        return "evening_rush"
    return "evening_winddown"


def _ml_grid_cell(lat: float, lon: float, bundle: Dict[str, Any]) -> int:
    gp = bundle["grid_params"]
    r = min(int((lat - gp["min_lat"]) / gp["lat_step"]), gp["n_rows"] - 1)
    c = min(int((lon - gp["min_lon"]) / gp["lon_step"]), gp["n_cols"] - 1)
    return r * gp["n_cols"] + c


def _ml_feature_row(
    src: Coord,
    dst: Coord,
    query_time: datetime,
    osrm_route_distance_km: float,
    osrm_free_flow_duration_sec: float,
    bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """One (src, dst, query_time) trip -> the exact feature row the model expects."""
    src_lat, src_lon = src
    dst_lat, dst_lon = dst
    haversine_distance_km = haversine_km(src, dst)
    bearing_degrees = _ml_bearing_deg(src_lat, src_lon, dst_lat, dst_lon)
    route_directness_ratio = (
        haversine_distance_km / osrm_route_distance_km if osrm_route_distance_km > 0 else float("nan")
    )

    src_zone_id = _ml_grid_cell(src_lat, src_lon, bundle)
    dst_zone_id = _ml_grid_cell(dst_lat, dst_lon, bundle)
    od_zone_pair_id = f"{src_zone_id}_{dst_zone_id}"

    hour = query_time.hour + query_time.minute / 60.0 + query_time.second / 3600.0
    day_of_week = query_time.strftime("%A")
    is_friday = day_of_week == "Friday"
    is_saturday = day_of_week == "Saturday"
    is_weekend = is_friday or is_saturday
    rush_hour_flag = (8 <= hour < 10) or (17 <= hour < 21)
    bucket = _ml_traffic_bucket(hour)
    is_holiday = (query_time.month, query_time.day) in _ML_FIXED_HOLIDAYS_MD

    lvl1, lvl2, lvl3 = bundle["lvl1"], bundle["lvl2"], bundle["lvl3"]
    key1 = (od_zone_pair_id, bucket)
    if key1 in lvl1.index and lvl1.loc[key1, "count"] >= bundle["min_support"]:
        historical_avg_speed_kmh = lvl1.loc[key1, "mean"]
    elif od_zone_pair_id in lvl2.index and lvl2.loc[od_zone_pair_id, "count"] >= bundle["min_support"]:
        historical_avg_speed_kmh = lvl2.loc[od_zone_pair_id, "mean"]
    elif bucket in lvl3.index and lvl3.loc[bucket, "count"] >= bundle["min_support"]:
        historical_avg_speed_kmh = lvl3.loc[bucket, "mean"]
    else:
        historical_avg_speed_kmh = bundle["global_mean"]

    return {
        "src_zone_id": src_zone_id, "dst_zone_id": dst_zone_id, "od_zone_pair_id": od_zone_pair_id,
        "day_of_week": day_of_week, "traffic_period_bucket": bucket,
        "haversine_distance_km": haversine_distance_km, "bearing_degrees": bearing_degrees,
        "osrm_route_distance_km": osrm_route_distance_km,
        "osrm_free_flow_duration_sec": osrm_free_flow_duration_sec,
        "route_directness_ratio": route_directness_ratio,
        "hour_sin": math.sin(2 * math.pi * hour / 24.0), "hour_cos": math.cos(2 * math.pi * hour / 24.0),
        "is_friday": int(is_friday), "is_saturday": int(is_saturday), "is_weekend": int(is_weekend),
        "is_holiday": int(is_holiday), "rush_hour_flag": int(rush_hour_flag),
        "historical_avg_speed_kmh": historical_avg_speed_kmh,
    }


def _ml_predict_minutes_batch(rows: List[Dict[str, Any]], bundle: Dict[str, Any]) -> List[float]:
    """Batched XGBoost prediction: one `.predict()` call for every leg in a matrix."""
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    for c in bundle["categorical_cols"]:
        frame[c] = pd.Categorical(frame[c].astype(str), categories=bundle["cat_categories"][c])
    for c in ["is_friday", "is_saturday", "is_weekend", "is_holiday", "rush_hour_flag"]:
        frame[c] = frame[c].astype(int)
    frame = frame[bundle["feature_cols"]]
    pred_seconds = np.exp(bundle["xgb_model"].predict(frame))
    return [float(s) / 60.0 for s in pred_seconds]


class _MlDurationProvider:
    """Decorates a `DistanceProvider`: durations come from the XGBoost model,
    distance_km and route geometry pass straight through unchanged.

    `query_time` must be set by the solver before each event is processed —
    the model's prediction is time-of-day/day-of-week dependent, and `table`/
    `route` carry no such argument in the `DistanceProvider` protocol.
    """

    name = "xgboost_ml"

    def __init__(self, inner: DistanceProvider):
        self.inner = inner
        self.query_time: Optional[datetime] = None
        self.bundle = _load_ml_bundle()

    def table(self, coords: Sequence[Coord]):
        raw_durations, distances = self.inner.table(coords)
        if self.query_time is None:
            return raw_durations, distances
        n = len(coords)
        pairs = [(i, j) for i in range(n) for j in range(n) if i != j]
        rows = [
            _ml_feature_row(
                coords[i], coords[j], self.query_time,
                distances[i][j], raw_durations[i][j] * 60.0, self.bundle,
            )
            for i, j in pairs
        ]
        predicted = _ml_predict_minutes_batch(rows, self.bundle)
        durations = [row[:] for row in raw_durations]
        for (i, j), minutes in zip(pairs, predicted):
            durations[i][j] = minutes
        return durations, distances

    def route(self, coords: Sequence[Coord]):
        return self.inner.route(coords)


# ──────────────────────────────────────────────────────────────────────────────
# Result shape
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SolvedNight:
    """Mirrors `solved_routes.json` so the notebook output is a usable fixture.

    - `routes`      → `route_summary`
    - `stops`       → `route_stops`
    - `passengers`  → `stop_passengers`
    - `unassigned`  → `unassigned`

    `warnings` is new: data-quality problems that are not unassigned requests
    (e.g. a vehicle with no parking coordinates), which the notebook printed and
    then forgot.
    """

    routes: List[Dict[str, Any]] = field(default_factory=list)
    stops: List[Dict[str, Any]] = field(default_factory=list)
    passengers: List[Dict[str, Any]] = field(default_factory=list)
    unassigned: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        return {
            "routes": len(self.routes),
            "pickup_routes": sum(1 for r in self.routes if r["type"] == "pickup"),
            "dropoff_routes": sum(1 for r in self.routes if r["type"] == "dropoff"),
            "stops": len(self.stops),
            "passengers": len(self.passengers),
            "unassigned": len(self.unassigned),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Overnight time algebra
# ──────────────────────────────────────────────────────────────────────────────

def normalise_clock(value: Any) -> str:
    """Any clock representation → "HH:MM:SS".

    Postgres `time` comes back as "22:00:00", the fixture uses the same, but a
    payload may carry "22:00" and a `datetime.time` may arrive from pydantic.
    """
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    parts = str(value).strip().split(":")
    parts = (parts + ["00", "00"])[:3]
    return ":".join(f"{int(float(p)):02d}" for p in parts)


def night_offset(t) -> timedelta:
    """Elapsed time since 10 PM, wrapping past midnight."""
    td = timedelta(hours=t.hour, minutes=t.minute)
    start = timedelta(hours=22)
    return td - start if td >= start else td + timedelta(days=1) - start


def iso(dt: datetime) -> str:
    """Lossless serialisation: keeps the date, so overnight order survives."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# Solver
# ──────────────────────────────────────────────────────────────────────────────

class NightSolver:
    """One whole-night solve. Construct, call `solve()`, discard.

    Instance state replaces the notebook's module-level globals (`fleet`,
    `events`, `pickup_vehicle_by_employee`, the four output lists), so two
    solves can never contaminate each other.
    """

    def __init__(
        self,
        *,
        service_date: str,
        vehicles: Sequence[Dict[str, Any]],
        pickup_requests: Sequence[Dict[str, Any]],
        dropoff_requests: Sequence[Dict[str, Any]],
        fixed_stops: Sequence[Dict[str, Any]],
        provider: DistanceProvider,
        cfg: Optional[SolverConfig] = None,
        employee_names: Optional[Dict[str, str]] = None,
    ):
        self.cfg = cfg or SolverConfig()
        self.provider = _MlDurationProvider(provider)
        self.office: Coord = self.cfg.office
        self.service_date = service_date
        self.pickup_requests = list(pickup_requests)
        self.dropoff_requests = list(dropoff_requests)
        self.fixed_stops = [s for s in fixed_stops if s.get("pickup_lat") is not None]
        self._employee_names = employee_names or {}

        self.night_anchor = datetime.strptime(service_date, "%Y-%m-%d").replace(
            hour=self.cfg.night_anchor_hour
        )

        self.out = SolvedNight()

        # A vehicle's assigned shifts = the distinct shift_time of its fixed
        # pickup stops. Load-bearing well beyond Case A: it gates the pickup
        # "dedicated vehicle" pool and the drop-off tier-1 pool.
        self.vehicle_shifts: Dict[str, set] = {}
        for s in self.fixed_stops:
            self.vehicle_shifts.setdefault(s["vehicle_plate"], set()).add(
                normalise_clock(s["shift_time"])
            )
        if not self.vehicle_shifts:
            self.out.warnings.append(
                "No vehicle pickup locations found: every vehicle is treated as "
                "unassigned to any shift, so all routing falls back to "
                "borrow-from-anywhere and no fixed-route (Case A) stops exist."
            )

        self.fleet: Dict[str, Dict[str, Any]] = {}
        self._build_fleet(vehicles)

        # Which vehicle picked up which employee — drives drop-off reuse.
        self.pickup_vehicle_by_employee: Dict[str, str] = {}

        self.events: List[Dict[str, Any]] = []

    # ── setup ────────────────────────────────────────────────────────────────

    def _build_fleet(self, vehicles: Sequence[Dict[str, Any]]) -> None:
        skipped: List[str] = []
        for v in vehicles:
            # A vehicle with no parking coordinates would poison every distance
            # computation it touches (current_location = (None, None)).
            if v.get("parking_lat") is None or v.get("parking_lng") is None:
                skipped.append(str(v.get("plate_no")))
                continue
            plate = v["plate_no"]
            self.fleet[plate] = {
                "plate_no": plate,
                "capacity": int(v["capacity"]),
                "zone_name": v.get("zone_name"),      # = route_area
                "driver_email": v.get("driver_email"),
                "parking_lat": float(v["parking_lat"]),
                "parking_lng": float(v["parking_lng"]),
                "current_location": (float(v["parking_lat"]), float(v["parking_lng"])),
                "status": "AVAILABLE",
                "_trip_end_time": None,       # clock time this trip ends
                "_trip_end_location": None,
                "_free_at": None,             # earliest this car may start a NEW trip
                "_stops": {},
                "_remaining": int(v["capacity"]),
                "_used": 0,
            }
        if skipped:
            self.out.warnings.append(
                f"{len(skipped)} vehicle(s) excluded from the fleet — no parking "
                f"coordinates: {', '.join(sorted(skipped))}"
            )

    def _employee_name(self, email: Optional[str]) -> str:
        """Never raises. The notebook's `user_by_email[email]["name"]` would."""
        if not email:
            return "Unknown employee"
        return self._employee_names.get(email) or str(email)

    def _raw_time(self, s: Any):
        return datetime.strptime(normalise_clock(s), "%H:%M:%S").time()

    def _parse_time(self, s: Any) -> datetime:
        """Clock string → night-anchored datetime (monotonic across midnight)."""
        return self.night_anchor + night_offset(self._raw_time(s))

    # ── timeline ─────────────────────────────────────────────────────────────

    def _build_timeline(self) -> None:
        """One chronological timeline, GROUPED by shift (one event per shift).

        Requests without coordinates are excluded here and reported as
        `no_coordinates` — they must never reach the geometry.
        """
        pickup_by_shift: Dict[str, List[Dict[str, Any]]] = {}
        for pr in self.pickup_requests:
            if pr.get("pickup_lat") is None or pr.get("pickup_lng") is None:
                continue
            pickup_by_shift.setdefault(normalise_clock(pr["shift_start_time"]), []).append(pr)
        for shift_time, reqs in pickup_by_shift.items():
            self.events.append(
                {"type": "pickup", "time": shift_time, "shift_time": shift_time, "requests": reqs}
            )

        dropoff_by_shift: Dict[str, List[Dict[str, Any]]] = {}
        for d in self.dropoff_requests:
            if d.get("drop_lat") is None or d.get("drop_lng") is None:
                continue
            dropoff_by_shift.setdefault(normalise_clock(d["drop_time"]), []).append(d)

        for drop_time, reqs in dropoff_by_shift.items():
            # The notebook asserts one shift_end_time per drop_time. Real data
            # will eventually violate that; splitting the event is correct and
            # keeps the office-departure timing exact for each sub-group.
            by_end: Dict[str, List[Dict[str, Any]]] = {}
            for r in reqs:
                by_end.setdefault(normalise_clock(r["shift_end_time"]), []).append(r)
            if len(by_end) > 1:
                self.out.warnings.append(
                    f"drop_time {drop_time} spans {len(by_end)} shift end times "
                    f"({', '.join(sorted(by_end))}); split into separate events."
                )
            for shift_end_time, group in by_end.items():
                self.events.append(
                    {
                        "type": "dropoff",
                        "time": drop_time,             # scheduled drop time
                        "shift_time": shift_end_time,  # office departure time
                        "requests": group,
                    }
                )

        # _parse_time is night-anchored, so this sorts correctly across midnight.
        self.events.sort(key=lambda e: (self._parse_time(e["time"]), e["time"], e["shift_time"]))

    def _report_missing_coordinates(self) -> None:
        for pr in self.pickup_requests:
            if pr.get("pickup_lat") is None or pr.get("pickup_lng") is None:
                self.out.unassigned.append(self._unassigned_row(pr, "pickup", pr.get("shift_start_time"), "no_coordinates"))
        for d in self.dropoff_requests:
            if d.get("drop_lat") is None or d.get("drop_lng") is None:
                self.out.unassigned.append(self._unassigned_row(d, "dropoff", d.get("shift_end_time"), "no_coordinates"))

    def _unassigned_row(
        self,
        request: Dict[str, Any],
        request_type: str,
        shift_time: Any,
        reason: str,
        plate_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        email = request.get("employee_email")
        return {
            "employee_email": email,
            "employee_name": self._employee_name(email),
            "type": request_type,
            "shift_time": normalise_clock(shift_time) if shift_time else None,
            "reason": reason,
            "vehicle_id": plate_no,
        }

    # ── fleet state ──────────────────────────────────────────────────────────

    def _update_fleet(self, trip_start: datetime) -> None:
        """Release cars whose previous trip has finished by `trip_start`.

        `trip_start` is when the NEXT trip actually begins, not when the event
        fires — those differ. A pickup is planned backward from `shift - 5 min`;
        a drop-off leaves the office at `shift_end`, a full 15 min before its
        `drop_time`. Comparing against the event clock let a car be dispatched
        before its previous trip had ended (spec sec.3).
        """
        for v in self.fleet.values():
            if v["status"] == "IN_TRIP" and v.get("_trip_end_time") and v["_trip_end_time"] <= trip_start:
                v["status"] = "AVAILABLE"
                v["current_location"] = v["_trip_end_location"]

    @staticmethod
    def _free_seats(v: Dict[str, Any]) -> int:
        """Seats left for THIS drop-off event (never the stale pickup _remaining)."""
        return v["capacity"] - v.get("_used", 0)

    # ── ordering primitives ──────────────────────────────────────────────────

    def _two_opt(self, ordered_idx: List[int], durations, start_idx: int, end_idx: int) -> List[int]:
        """2-opt on the TRUE route cost: start -> s1 -> ... -> sn -> end.

        Both anchors matter. Scoring only `office -> s1 -> ... -> sn` (omitting
        the final leg and anchoring the wrong end) optimises a pickup route
        backwards and reverses the furthest-first order that Algorithm 2 step 9
        deliberately establishes.
        """
        def cost(seq: List[int]) -> float:
            c = durations[start_idx][seq[0]]
            for a, b in zip(seq, seq[1:]):
                c += durations[a][b]
            return c + durations[seq[-1]][end_idx]

        best = ordered_idx[:]
        best_cost = cost(best)
        improved = True
        while improved:
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                    cand_cost = cost(cand)
                    if cand_cost < best_cost - 1e-6:
                        best, best_cost, improved = cand, cand_cost, True
        return best

    @staticmethod
    def _nearest_neighbour(seed: int, remaining: Iterable[int], durations) -> List[int]:
        """Algorithm 2 steps 11-13: repeatedly append the closest unvisited stop."""
        ordered = [seed]
        remaining = [i for i in remaining if i != seed]
        while remaining:
            cur = ordered[-1]
            nxt = min(remaining, key=lambda i: durations[cur][i])
            ordered.append(nxt)
            remaining.remove(nxt)
        return ordered

    def _stops_for_shift(self, shift_time: str, vehicles_this_shift) -> List[Dict[str, Any]]:
        """Fixed stops for a shift: this shift's stops, on cars actually in service."""
        plates_in_service = {v["plate_no"] for v in vehicles_this_shift}
        return [
            s for s in self.fixed_stops
            if normalise_clock(s["shift_time"]) == shift_time
            and s["vehicle_plate"] in plates_in_service
        ]

    # ── pickup: Case A (fixed route) / Case B (door-to-door) ─────────────────

    def _assign_pickup_event(self, event) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        shift_time = event["shift_time"]
        assigned = [
            v for v in self.fleet.values()
            if shift_time in self.vehicle_shifts.get(v["plate_no"], set())
        ]
        available = [v for v in assigned if v["status"] == "AVAILABLE"]
        if not available:                     # dedicated cars busy -> borrow
            available = [v for v in self.fleet.values() if v["status"] == "AVAILABLE"]
        if not available:
            return [], list(event["requests"])  # fleet exhausted

        requests_this_shift = event["requests"]
        for v in available:
            v["_remaining"] = v["capacity"]
            v["_stops"] = {}
        unassigned: List[Dict[str, Any]] = []
        walk_limit = self.cfg.walk_limit_min
        walk_speed = self.cfg.walk_speed_kmph

        # --- Case A (10 PM / 11 PM): fixed-route matching (Algorithm 1) ---
        if shift_time in FIXED_ROUTE_SHIFTS:
            candidate_stops = self._stops_for_shift(shift_time, available)

            # Serve the most constrained employees first: fewest reachable
            # stops, then longest walk. Employees with no option at all go first.
            def priority_key(pr):
                home = (float(pr["pickup_lat"]), float(pr["pickup_lng"]))
                walks_in_range = [
                    w for w in (
                        walk_minutes(home, (float(s["pickup_lat"]), float(s["pickup_lng"])), walk_speed)
                        for s in candidate_stops
                    ) if w <= walk_limit
                ]
                return (len(walks_in_range), -min(walks_in_range, default=999))

            for pr in sorted(requests_this_shift, key=priority_key):
                home = (float(pr["pickup_lat"]), float(pr["pickup_lng"]))
                # Algorithm 1 steps 2-9: closest stop within a 5-min walk whose
                # car still has a free seat.
                candidates = []
                for s in candidate_stops:
                    w = walk_minutes(home, (float(s["pickup_lat"]), float(s["pickup_lng"])), walk_speed)
                    if w > walk_limit:
                        continue
                    vv = self.fleet.get(s["vehicle_plate"])
                    if vv is None or vv["_remaining"] <= 0:
                        continue
                    candidates.append((w, s, vv))
                if candidates:
                    w, s, v = min(candidates, key=lambda c: c[0])
                    v["_stops"].setdefault(s["location_name"], {
                        "coord": (float(s["pickup_lat"]), float(s["pickup_lng"])),
                        "name": s["location_name"],
                        "is_adhoc": False,
                        "is_shared": False,
                        "passengers": [],
                    })["passengers"].append(pr)
                    v["_remaining"] -= 1
                else:
                    # Algorithm 1 steps 10-13: create a new pickup point.
                    # Capacity is a hard constraint here too — a new point on a
                    # full car would silently overfill it.
                    with_room = [x for x in available if x["_remaining"] > 0]
                    if not with_room:
                        unassigned.append(pr)
                        continue
                    v = min(with_room, key=lambda x: haversine_km(home, x["current_location"]))
                    v["_stops"].setdefault(f"adhoc_{pr['employee_email']}", {
                        "coord": home,
                        "name": f"Ad-hoc ({self._employee_name(pr['employee_email'])})",
                        "is_adhoc": True,
                        "is_shared": False,
                        "passengers": [],
                    })["passengers"].append(pr)
                    v["_remaining"] -= 1
            return available, unassigned

        # --- Case B (after 11 PM): door-to-door clustering (Algorithm 2) ---
        pending = sorted(
            requests_this_shift,
            key=lambda pr: min(
                haversine_km((float(pr["pickup_lat"]), float(pr["pickup_lng"])), v["current_location"])
                for v in available
            ),
        )
        for pr in pending:
            home = (float(pr["pickup_lat"]), float(pr["pickup_lng"]))
            # Algorithm 2 step 4: nearest cluster *with available capacity*.
            with_room = [x for x in available if x["_remaining"] > 0]
            if not with_room:
                unassigned.append(pr)
                continue
            v = min(with_room, key=lambda x: haversine_km(home, x["current_location"]))
            v["_stops"].setdefault(f"home_{pr['employee_email']}", {
                "coord": home,
                "name": f"Home ({self._employee_name(pr['employee_email'])})",
                "is_adhoc": True,
                "is_shared": False,
                "passengers": [],
            })["passengers"].append(pr)
            v["_remaining"] -= 1
        return available, unassigned

    def _order_stops_pickup(self, vehicle) -> List[Tuple[Any, Dict[str, Any]]]:
        """Furthest-from-office first, nearest-neighbour, then true-cost 2-opt."""
        items = list(vehicle["_stops"].items())
        if len(items) <= 1:
            return items
        # Same anchors the timing uses: wherever the car actually is -> stops -> office.
        coords = [vehicle["current_location"]] + [it["coord"] for _, it in items] + [self.office]
        START, END = 0, len(items) + 1
        durations, _ = self.provider.table(coords)
        stop_idx = list(range(1, len(items) + 1))
        # Algorithm 2 step 9 / "last point first": begin furthest from the office.
        seed = max(stop_idx, key=lambda i: durations[i][END])
        ordered = self._nearest_neighbour(seed, stop_idx, durations)
        ordered = self._two_opt(ordered, durations, START, END)
        return [items[i - 1] for i in ordered]

    def _compute_timing_pickup(self, vehicle, ordered_stops, shift_time) -> Dict[str, Any]:
        deadline = self._parse_time(shift_time) - timedelta(minutes=self.cfg.office_buffer_min)
        # Start from where the car actually is, so a car reused after an earlier
        # trip is timed from the office rather than from its parking lot.
        coords = [vehicle["current_location"]] + [it["coord"] for _, it in ordered_stops] + [self.office]
        durations, distances = self.provider.table(coords)
        legs = [durations[i][i + 1] for i in range(len(coords) - 1)]
        total = sum(legs) + self.cfg.boarding_buffer_min * len(ordered_stops)
        parking_departure = deadline - timedelta(minutes=total)
        timestamps = []
        t = parking_departure
        for i, (key, _item) in enumerate(ordered_stops):
            t = t + timedelta(minutes=legs[i])
            arrival = t
            t = t + timedelta(minutes=self.cfg.boarding_buffer_min)
            timestamps.append({"stop_key": key, "arrival": arrival, "departure": t})
        office_arrival = t + timedelta(minutes=legs[-1])
        return {
            "parking_departure": parking_departure,
            "office_arrival": office_arrival,
            "total_minutes": total,
            "leg_minutes": legs,
            "leg_km": [distances[i][i + 1] for i in range(len(coords) - 1)],
            "stop_timestamps": timestamps,
        }

    def _pickup_window_minutes(self, vehicle, shift_time) -> float:
        """How long this car may actually spend on the road for this shift.

        A pickup is planned BACKWARD from `shift - 5 min`, so the trip really
        starts at `parking_departure` — which can precede the event clock by up
        to two hours. Availability therefore has to be checked over the whole
        window, not at the event instant: spec sec.3 says an IN_TRIP car is
        unavailable, and a car released at 05:28 cannot depart at 05:07.
        """
        deadline = self._parse_time(shift_time) - timedelta(minutes=self.cfg.office_buffer_min)
        free_at = vehicle.get("_free_at")
        if free_at is None:
            return float("inf")
        return (deadline - free_at).total_seconds() / 60.0

    def _enforce_cap_pickup(self, vehicle, shift_time):
        """Shed stops until the route fits BOTH the 120-min cap and the free window."""
        window = self._pickup_window_minutes(vehicle, shift_time)
        cap = min(self.cfg.max_route_minutes, window)
        reason = "dropped_for_120min_cap" if cap == self.cfg.max_route_minutes else "vehicle_not_free_in_time"
        dropped: List[Dict[str, Any]] = []
        while True:
            ordered = self._order_stops_pickup(vehicle)
            if not ordered:
                return ordered, None, dropped, reason
            timing = self._compute_timing_pickup(vehicle, ordered, shift_time)
            if timing["total_minutes"] <= cap:
                return ordered, timing, dropped, reason
            # Drop the stop whose removal reduces total time the most.
            best_key, best_total = None, None
            for key, _ in ordered:
                saved = vehicle["_stops"]
                vehicle["_stops"] = {k: v for k, v in saved.items() if k != key}
                trial = self._order_stops_pickup(vehicle)
                trial_total = self._compute_timing_pickup(vehicle, trial, shift_time)["total_minutes"] if trial else 0
                vehicle["_stops"] = saved
                if best_total is None or trial_total < best_total:
                    best_total, best_key = trial_total, key
            dropped.extend(vehicle["_stops"].pop(best_key)["passengers"])

    # ── drop-off: Case C (door-to-door) / Case D (main road) ─────────────────

    def _main_road_applies(self, drop_time: str) -> bool:
        """Case D fires at 07:30 — except on Fridays, when the metro is closed.

        The notebook's header documents the Friday exception but its code omits
        the check. The backend knows the real calendar date, so it can honour
        the rule: `_parse_time` is night-anchored, so a 07:30 drop already
        resolves to the *following* morning's date, which is the day the metro
        would actually have to be running.
        """
        if drop_time != MAIN_ROAD_DROP_TIME:
            return False
        if not self.cfg.apply_friday_exception:
            return True
        return self._parse_time(drop_time).date().weekday() != _FRIDAY

    def _assign_dropoff_event(self, event):
        shift_end_time = event["shift_time"]
        is_main_road = self._main_road_applies(event["time"])

        for v in self.fleet.values():
            v["_used"] = 0

        # --- group employees by their pickup vehicle (reuse), then by zone ---
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for d in event["requests"]:
            pref_plate = self.pickup_vehicle_by_employee.get(d["employee_email"])
            groups.setdefault(pref_plate, []).append(d)

        vehicles_this_shift = [
            v for v in self.fleet.values()
            if shift_end_time in self.vehicle_shifts.get(v["plate_no"], set())
        ]
        assigned_vehicles: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
        unassigned: List[Dict[str, Any]] = []

        for pref_plate, emps in groups.items():
            zone = emps[0].get("zone_name")
            ref = (float(emps[0]["drop_lat"]), float(emps[0]["drop_lng"]))
            # preferred = the vehicle that picked them up (reuse)
            if (pref_plate and pref_plate in self.fleet
                    and self.fleet[pref_plate]["status"] == "AVAILABLE"
                    and self._free_seats(self.fleet[pref_plate]) > 0):
                v = self.fleet[pref_plate]
            else:
                # Case C step 1: an available vehicle serving their zone, else
                # the NEAREST available vehicle by distance — crossing zone
                # boundaries when this zone has nothing left, using each
                # vehicle's own current_location as the neighbourhood proxy.
                tiers = [
                    [v for v in vehicles_this_shift
                     if v["zone_name"] == zone and v["status"] == "AVAILABLE" and self._free_seats(v) > 0],
                    [v for v in self.fleet.values()
                     if v["zone_name"] == zone and v["status"] == "AVAILABLE" and self._free_seats(v) > 0],
                    [v for v in self.fleet.values()
                     if v["status"] == "AVAILABLE" and self._free_seats(v) > 0],
                ]
                candidates = next((t for t in tiers if t), [])
                if not candidates:
                    unassigned.extend(emps)
                    continue
                v = min(candidates, key=lambda x: haversine_km(ref, x["current_location"]))

            room = self._free_seats(v)
            if len(emps) > room:
                keep, overflow = emps[:room], emps[room:]
                if keep:
                    v["_used"] = v.get("_used", 0) + len(keep)
                    assigned_vehicles.append((v, keep))
                # try to place overflow on another available vehicle, nearest first
                extra = [
                    x for x in self.fleet.values()
                    if x["status"] == "AVAILABLE" and x is not v and self._free_seats(x) >= len(overflow)
                ]
                if extra:
                    v2 = min(extra, key=lambda x: haversine_km(ref, x["current_location"]))
                    v2["_used"] = v2.get("_used", 0) + len(overflow)
                    assigned_vehicles.append((v2, overflow))
                else:
                    unassigned.extend(overflow)
            else:
                v["_used"] = v.get("_used", 0) + len(emps)
                assigned_vehicles.append((v, emps))

        # One vehicle can legitimately receive more than one group (reuse +
        # borrow + overflow). Merge per plate BEFORE building stops — resetting
        # _stops once per (vehicle, group) pair would wipe every group but the
        # last and route the survivor twice.
        merged: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        for v, emps in assigned_vehicles:
            merged.setdefault(v["plate_no"], (v, []))[1].extend(emps)

        for v, emps in merged.values():
            v["_stops"] = {}
            for d in emps:
                zone = d.get("zone_name")
                if is_main_road and zone in MAIN_ROAD_ZONES:
                    # BDS: Agargaon Metro for Mirpur & Uttara
                    coord = self.cfg.agargaon_metro
                    name = "Agargaon Metro Station (shared drop point)"
                    shared = True
                else:
                    coord = (float(d["drop_lat"]), float(d["drop_lng"]))
                    name = f"Home ({self._employee_name(d['employee_email'])})"
                    shared = False
                v["_stops"].setdefault(coord, {
                    "coord": coord,
                    "name": name,
                    "is_shared": shared,
                    "is_adhoc": not shared,
                    "passengers": [],
                })["passengers"].append(d)
        return [v for v, _ in merged.values()], unassigned

    def _order_stops_dropoff(self, vehicle):
        """Mirror of the pickup order: nearest-to-office first, then true-cost 2-opt."""
        items = list(vehicle["_stops"].items())
        if len(items) <= 1:
            return items
        coords = [self.office] + [it["coord"] for _, it in items] + [
            (vehicle["parking_lat"], vehicle["parking_lng"])
        ]
        START, END = 0, len(items) + 1
        durations, _ = self.provider.table(coords)
        stop_idx = list(range(1, len(items) + 1))
        seed = min(stop_idx, key=lambda i: durations[START][i])   # nearest-to-office FIRST
        ordered = self._nearest_neighbour(seed, stop_idx, durations)
        ordered = self._two_opt(ordered, durations, START, END)
        return [items[i - 1] for i in ordered]

    def _compute_timing_dropoff(self, vehicle, ordered_stops, shift_end_time) -> Dict[str, Any]:
        office_departure = self._parse_time(shift_end_time)
        coords = [self.office] + [it["coord"] for _, it in ordered_stops] + [
            (vehicle["parking_lat"], vehicle["parking_lng"])
        ]
        durations, distances = self.provider.table(coords)
        legs = [durations[i][i + 1] for i in range(len(coords) - 1)]
        timestamps = []
        t = office_departure
        for i, (key, _item) in enumerate(ordered_stops):
            t = t + timedelta(minutes=legs[i])
            arrival = t
            t = t + timedelta(minutes=self.cfg.boarding_buffer_min)
            timestamps.append({"stop_key": key, "arrival": arrival, "departure": t})
        parking_arrival = t + timedelta(minutes=legs[-1])
        total = (parking_arrival - office_departure).total_seconds() / 60.0
        return {
            "office_departure": office_departure,
            "parking_arrival": parking_arrival,
            "total_minutes": total,
            "leg_minutes": legs,
            "leg_km": [distances[i][i + 1] for i in range(len(coords) - 1)],
            "stop_timestamps": timestamps,
        }

    def _enforce_cap_dropoff(self, vehicle, shift_end_time):
        """Drop-offs run FORWARD from a fixed office departure, so eligibility is
        already exact once the car is known to be free at `shift_end_time`; only
        the 120-min cap can bite here."""
        dropped: List[Dict[str, Any]] = []
        reason = "dropped_for_120min_cap"
        while True:
            ordered = self._order_stops_dropoff(vehicle)
            if not ordered:
                return ordered, None, dropped, reason
            timing = self._compute_timing_dropoff(vehicle, ordered, shift_end_time)
            if timing["total_minutes"] <= self.cfg.max_route_minutes:
                return ordered, timing, dropped, reason
            best_key, best_total = None, None
            for key, _ in ordered:
                saved = vehicle["_stops"]
                vehicle["_stops"] = {k: v for k, v in saved.items() if k != key}
                trial = self._order_stops_dropoff(vehicle)
                trial_total = self._compute_timing_dropoff(vehicle, trial, shift_end_time)["total_minutes"] if trial else 0
                vehicle["_stops"] = saved
                if best_total is None or trial_total < best_total:
                    best_total, best_key = trial_total, key
            dropped.extend(vehicle["_stops"].pop(best_key)["passengers"])

    # ── main loop ────────────────────────────────────────────────────────────

    def solve(self) -> SolvedNight:
        self._report_missing_coordinates()
        self._build_timeline()

        for event in self.events:
            if event["type"] == "pickup":
                self._run_pickup_event(event)
            else:
                self._run_dropoff_event(event)

        logger.info(
            "routing: solved %s -> %s", self.service_date, self.out.counts()
        )
        return self.out

    @staticmethod
    def _modal_zone(ordered_stops) -> Optional[str]:
        """The zone most of this route's passengers belong to → `route.zone_id`."""
        zones = Counter(
            pr.get("zone_name")
            for _k, item in ordered_stops
            for pr in item["passengers"]
            if pr.get("zone_name")
        )
        return zones.most_common(1)[0][0] if zones else None

    def _run_pickup_event(self, event) -> None:
        shift_time = event["shift_time"]
        # ML model prediction is time-of-day dependent: anchor every leg in
        # this event to the requests' own pickup/shift-start time (event["time"]).
        self.provider.query_time = self._parse_time(event["time"])
        # latest instant the trip could start (it ends at the office deadline)
        self._update_fleet(self._parse_time(shift_time) - timedelta(minutes=self.cfg.office_buffer_min))
        vehicles, unassigned = self._assign_pickup_event(event)
        self.out.unassigned.extend(
            self._unassigned_row(pr, "pickup", shift_time, "no_vehicle_available") for pr in unassigned
        )

        for v in vehicles:
            if not v["_stops"]:
                continue
            ordered, timing, dropped, drop_reason = self._enforce_cap_pickup(v, shift_time)
            for pr in dropped:
                self.out.unassigned.append(
                    self._unassigned_row(pr, "pickup", shift_time, drop_reason, v["plate_no"])
                )
            if not ordered or timing is None:
                continue

            # Record pickup->vehicle reuse only for passengers who survived the
            # 120-min cap, so drop-off never tries to reuse a car that never
            # carried them.
            for _key, item in ordered:
                for pr in item["passengers"]:
                    self.pickup_vehicle_by_employee[pr["employee_email"]] = v["plate_no"]

            full = [v["current_location"]] + [it["coord"] for _, it in ordered] + [self.office]
            # NOTE: the provider's own duration is deliberately discarded —
            # route timing comes from the table legs above, distance from here.
            dist_km, _dur_min, geometry = self.provider.route(full)
            rid = f"P{shift_time}::V{v['plate_no']}"
            self.out.routes.append({
                "route_instance_id": rid,
                "type": "pickup",
                "shift_time": shift_time,
                "service_date": self.service_date,
                "zone_name": self._modal_zone(ordered),
                "vehicle_id": v["plate_no"],
                "plate_no": v["plate_no"],
                "driver_id": v.get("driver_email"),
                "capacity": v["capacity"],
                "assigned_passengers": sum(len(it["passengers"]) for _, it in ordered),
                "stop_count": len(ordered),
                "parking_lat": v["parking_lat"],
                "parking_lng": v["parking_lng"],
                "start_lat": v["current_location"][0],
                "start_lng": v["current_location"][1],
                "parking_departure": iso(timing["parking_departure"]),
                "office_arrival": iso(timing["office_arrival"]),
                "total_minutes": round(timing["total_minutes"], 1),
                "total_distance_km": round(dist_km, 2),
                "route_geometry": geometry,
            })

            for seq, ((key, item), ts) in enumerate(zip(ordered, timing["stop_timestamps"]), start=1):
                self.out.stops.append({
                    "route_instance_id": rid,
                    "type": "pickup",
                    "shift_time": shift_time,
                    "vehicle_id": v["plate_no"],
                    "sequence_order": seq,
                    "stop_name": item["name"],
                    "stop_lat": item["coord"][0],
                    "stop_lng": item["coord"][1],
                    "is_adhoc": item["is_adhoc"],
                    "is_shared": item.get("is_shared", False),
                    "arrival_time": iso(ts["arrival"]),
                    "departure_time": iso(ts["departure"]),
                    "leg_minutes_from_previous": round(timing["leg_minutes"][seq - 1], 1),
                    "leg_km_from_previous": round(timing["leg_km"][seq - 1], 2),
                    "passenger_count": len(item["passengers"]),
                })
                for pr in item["passengers"]:
                    self.out.passengers.append({
                        "route_instance_id": rid,
                        "type": "pickup",
                        "sequence_order": seq,
                        "stop_name": item["name"],
                        "employee_id": pr["employee_email"],
                        "employee_name": self._employee_name(pr["employee_email"]),
                        "board_time": iso(ts["departure"]),
                    })

            # fleet state: vehicle now IN_TRIP, ends at OFFICE
            v["status"] = "IN_TRIP"
            v["_trip_end_time"] = timing["office_arrival"]
            v["_trip_end_location"] = self.office
            v["_free_at"] = timing["office_arrival"]
            v["_used"] = 0

    def _run_dropoff_event(self, event) -> None:
        shift_end_time = event["shift_time"]
        # ML model prediction is time-of-day dependent: anchor every leg in
        # this event to the requests' own scheduled drop-off time (event["time"]).
        self.provider.query_time = self._parse_time(event["time"])
        # the car leaves the office at shift_end — that is the trip start
        self._update_fleet(self._parse_time(shift_end_time))
        vehicles, unassigned = self._assign_dropoff_event(event)
        self.out.unassigned.extend(
            self._unassigned_row(d, "dropoff", shift_end_time, "no_vehicle_available") for d in unassigned
        )

        for v in vehicles:
            if not v["_stops"]:
                continue
            ordered, timing, dropped, drop_reason = self._enforce_cap_dropoff(v, shift_end_time)
            for d in dropped:
                self.out.unassigned.append(
                    self._unassigned_row(d, "dropoff", shift_end_time, drop_reason, v["plate_no"])
                )
            if not ordered or timing is None:
                continue

            full = [self.office] + [it["coord"] for _, it in ordered] + [
                (v["parking_lat"], v["parking_lng"])
            ]
            dist_km, _dur_min, geometry = self.provider.route(full)
            rid = f"D{shift_end_time}::V{v['plate_no']}"
            self.out.routes.append({
                "route_instance_id": rid,
                "type": "dropoff",
                "shift_time": shift_end_time,
                "service_date": self.service_date,
                "zone_name": self._modal_zone(ordered),
                "vehicle_id": v["plate_no"],
                "plate_no": v["plate_no"],
                "driver_id": v.get("driver_email"),
                "capacity": v["capacity"],
                "assigned_passengers": sum(len(it["passengers"]) for _, it in ordered),
                "stop_count": len(ordered),
                "parking_lat": v["parking_lat"],
                "parking_lng": v["parking_lng"],
                "start_lat": self.office[0],
                "start_lng": self.office[1],
                "office_departure": iso(timing["office_departure"]),
                "parking_arrival": iso(timing["parking_arrival"]),
                "total_minutes": round(timing["total_minutes"], 1),
                "total_distance_km": round(dist_km, 2),
                "route_geometry": geometry,
            })

            for seq, ((key, item), ts) in enumerate(zip(ordered, timing["stop_timestamps"]), start=1):
                self.out.stops.append({
                    "route_instance_id": rid,
                    "type": "dropoff",
                    "shift_time": shift_end_time,
                    "vehicle_id": v["plate_no"],
                    "sequence_order": seq,
                    "stop_name": item["name"],
                    "stop_lat": item["coord"][0],
                    "stop_lng": item["coord"][1],
                    "is_adhoc": item["is_adhoc"],
                    "is_shared": item["is_shared"],
                    "arrival_time": iso(ts["arrival"]),
                    "departure_time": iso(ts["departure"]),
                    "leg_minutes_from_previous": round(timing["leg_minutes"][seq - 1], 1),
                    "leg_km_from_previous": round(timing["leg_km"][seq - 1], 2),
                    "passenger_count": len(item["passengers"]),
                })
                for d in item["passengers"]:
                    self.out.passengers.append({
                        "route_instance_id": rid,
                        "type": "dropoff",
                        "sequence_order": seq,
                        "stop_name": item["name"],
                        "employee_id": d["employee_email"],
                        "employee_name": self._employee_name(d["employee_email"]),
                        "alight_time": iso(ts["arrival"]),
                    })

            # fleet state: vehicle now IN_TRIP, at office, ends at parking
            v["status"] = "IN_TRIP"
            v["current_location"] = self.office
            v["_trip_end_time"] = timing["parking_arrival"]
            v["_trip_end_location"] = (v["parking_lat"], v["parking_lng"])
            v["_free_at"] = timing["parking_arrival"]
            v["_used"] = 0


def solve_night(
    *,
    service_date: str,
    vehicles: Sequence[Dict[str, Any]],
    pickup_requests: Sequence[Dict[str, Any]],
    dropoff_requests: Sequence[Dict[str, Any]],
    fixed_stops: Sequence[Dict[str, Any]],
    provider: DistanceProvider,
    cfg: Optional[SolverConfig] = None,
    employee_names: Optional[Dict[str, str]] = None,
) -> SolvedNight:
    """Solve one whole service night.

    Whole-night is not a convenience: fleet state (where each car is, when it
    is next free) carries across every event, so pickups and drop-offs cannot
    be solved independently without leaving the fleet's end-of-night position
    undefined.
    """
    return NightSolver(
        service_date=service_date,
        vehicles=vehicles,
        pickup_requests=pickup_requests,
        dropoff_requests=dropoff_requests,
        fixed_stops=fixed_stops,
        provider=provider,
        cfg=cfg,
        employee_names=employee_names,
    ).solve()
