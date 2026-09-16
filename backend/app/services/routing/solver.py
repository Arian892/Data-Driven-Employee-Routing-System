"""The routing algorithm — a faithful port of `data/routing_night.py`.

`routing_night.py` is the standalone twin of `data/testing.ipynb` and the
current authoritative algorithm. This module ports it into the backend's pure
solver shape, preserving every behavioural decision:

- **One chronological timeline** of interleaved pickup and drop-off events,
  anchored at 22:00 on the service date so ordering survives midnight.
- **Fleet state** per vehicle (`current_location`, `status`, `_free_at`)
  carries forward across the whole night, so a car starts from where it
  actually is. A drop-off tour ends at the LAST STOP, not the office.
- **Case A (22:00)**: fixed-route matching against the roster's stops, walking
  times from the OSRM foot network (≤ `walk_limit_min`), ad-hoc door stops for
  riders with no reachable stop, then `redistribute_case_a` for cap shedding.
- **Case B (23:00)**: greedy nearest-car door-to-door with a near-tie slack.
- **Case B-kmeans (00:00–06:00)**: capacity-constrained k-means over rider
  homes, exact cluster→car matching, `_spill_riders` safety net.
- **Exact fair ordering** (Held-Karp) for pickups (minimise total passenger
  ride time) and **exact shortest open tours** for drop-offs
  (price the closing leg at `dropoff_return_weight`).
- **Case C / Case D drop-offs**: door-to-door, or the 07:30 Agargaon Metro /
  main-road consolidation (Mirpur box + Uttara quad, Friday exception). The
  22:15/23:15 evening drop-offs use the least-squares (Hungarian) fit against
  each car's own fixed route.
- **Second chance**: every rider the first pass shed is offered one more car,
  in the shift's own policy order, before anything is reported.
- **Cap shedding**: enforce the 120-min passenger cap (and, for pickups, the
  car's free window) by dropping whole stops.

ML travel-time model: leg durations used for ordering and timing come from the
trained XGBoost bundle (`ml_model/inference_bundle.joblib`) instead of raw
OSRM durations, exactly as the previous port did. Distance and geometry are
unaffected — they still come from the injected `DistanceProvider`. Set
`use_ml=False` to reproduce the notebook byte-for-byte with raw OSRM durations
(this is what the parity test uses). The foot network is never ML-predicted.

Deviations from the script, all deliberate and all listed here:

1. The three crash sites are softened (a notebook may raise; a request handler
   may not): an event spanning several `shift_end_time`s is split instead of
   asserted, an unknown employee falls back to its email instead of raising
   `KeyError`, and vehicles with no parking coordinates are reported as warnings
   instead of vanishing.
2. Case D honours the "except Fridays" rule that the script's own header
   documents but its code omits. Controlled by `SolverConfig.apply_friday_exception`.
3. Route records carry an extra `zone_name` (the modal zone) so the caller can
   set `route.zone_id`, and drop-off records carry `parking_arrival`
   (= `tour_end`) so `writer`'s assignment insert keeps a real arrival time.
"""
from __future__ import annotations

import logging
import math
import os
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import permutations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from app.services.routing.config import (
    MAX_STOPS_FOR_EXACT,
    SolverConfig,
)
from app.services.routing.distance import (
    DistanceProvider,
    FootDistanceProvider,
    get_foot_provider,
    haversine_km,
)

logger = logging.getLogger("uvicorn.error")

Coord = Tuple[float, float]

# Friday is weekday() == 4. Dhaka Metro Rail does not run on Fridays, which is
# why BDS exempts that day from the Agargaon Metro consolidation.
_FRIDAY = 4

# Case B-kmeans applies to these pick-up shifts; 11 PM stays on the greedy loop.
KMEANS_PICKUP_SHIFTS = frozenset({
    "00:00:00", "01:00:00", "02:00:00", "03:00:00",
    "04:00:00", "05:00:00", "06:00:00",
})

# The evening drop-offs are fitted against each car's own fixed route by
# least-squares (Hungarian), not by the reuse/tier rule.
EVENING_FIT_EVENTS = frozenset({"22:15:00", "23:15:00"})

# k-means constants (fixed seed: two runs over one data set must agree).
CLUSTER_ZONE_PENALTY_KM = 5.0
CLUSTER_RESTARTS = 12
CLUSTER_SEED = 0

# Evening-fit prices: no roster curve (usable but last), and a dummy seat.
_NO_CURVE_COST = 1.0e6
_UNSEATABLE_COST = 1.0e9

# Case D (07:30) geography — the Mirpur / Uttara boundary, fixed as plain
# constants (no OSM dependency at runtime).
MIRPUR_BBOX = (23.80520, 90.35941, 23.83011, 90.38381)
UTTARA_QUAD = [(90.3725, 23.8943), (90.4022, 23.8931),
               (90.4085, 23.8512), (90.3662, 23.8585)]


# ──────────────────────────────────────────────────────────────────────────────
# ML travel-time model (GPS_TRACE_ML/trained-model/inference_bundle.joblib)
# ──────────────────────────────────────────────────────────────────────────────
#
# Leg *durations* for ordering and timing come from this trained XGBoost model
# instead of raw OSRM/haversine durations. Distance_km and route geometry are
# unaffected — they still come straight from the injected `DistanceProvider`,
# and the model itself needs that provider's own duration/distance as two of
# its input features (it is a correction layer on top of OSRM, not a
# replacement for it). Feature engineering here is a direct port of
# `GPS_TRACE_ML/test_inference_bundle.py`'s `build_feature_row`, kept
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

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


# ──────────────────────────────────────────────────────────────────────────────
# Result shape
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SolvedNight:
    """Mirrors `solved_routes_walk20_retw0.json` so the script output is usable
    as a fixture.

    - `routes`      → `route_summary`
    - `stops`       → `route_stops`
    - `passengers`  → `stop_passengers`
    - `unassigned`  → `unassigned`

    `warnings` is new: data-quality problems that are not unassigned requests.
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
    """Any clock representation → "HH:MM:SS"."""
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

    Instance state replaces the script's module-level globals (`fleet`,
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
        foot: Optional[FootDistanceProvider] = None,
        cfg: Optional[SolverConfig] = None,
        employee_names: Optional[Dict[str, str]] = None,
        use_ml: bool = True,
    ):
        self.cfg = cfg or SolverConfig()
        self.use_ml = use_ml
        self.provider = _MlDurationProvider(provider) if use_ml else provider
        self.foot = foot or get_foot_provider()
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
        # "dedicated vehicle" pool, the drop-off tier-1 pool, the evening fit
        # and the second chance.
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

        # Every roster route, keyed (plate, shift_time) -> its stops in
        # sequence_order. Built once from the same catalog Case A reads; it is
        # the fitting curve for the evening (22:15/23:15) drop-offs.
        self._route_by_car_shift: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for s in self.fixed_stops:
            self._route_by_car_shift.setdefault(
                (s["vehicle_plate"], normalise_clock(s["shift_time"])), []
            ).append(s)
        for stops in self._route_by_car_shift.values():
            stops.sort(key=lambda s: (s["sequence_order"] is None, s["sequence_order"]))

        # Case D main-road drops: the nearest *catalog* stop ("pickup points of
        # a car, but in reverse"). No walk rule and no ad-hoc home fallback at
        # 07:30 -- the catalog stop IS the main-road drop point.
        self._main_road_stops = [
            (s["location_name"], (s["pickup_lat"], s["pickup_lng"]))
            for s in self.fixed_stops
            if s.get("pickup_lat") is not None and s.get("pickup_lng") is not None
        ]

        # Case D fires the morning AFTER the service date, so Friday means
        # service date + 1 day.
        self._is_friday_dropoff = (
            datetime.strptime(service_date, "%Y-%m-%d") + timedelta(days=1)
        ).weekday() == _FRIDAY

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
        """Never raises. The script's `user_by_email[email]["name"]` would."""
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
            # The script asserts one shift_end_time per drop_time. Real data
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
                        "shift_time": shift_end_time,  # office departure label
                        "requests": group,
                    }
                )

        # _parse_time is night-anchored, so this sorts correctly across midnight.
        self.events.sort(key=lambda e: (self._parse_time(e["time"]), e["time"], e["shift_time"]))

    def _report_missing_coordinates(self) -> None:
        for pr in self.pickup_requests:
            if pr.get("pickup_lat") is None or pr.get("pickup_lng") is None:
                self.out.unassigned.append(
                    self._unassigned_row(pr, "pickup", pr.get("shift_start_time"), "no_coordinates")
                )
        for d in self.dropoff_requests:
            if d.get("drop_lat") is None or d.get("drop_lng") is None:
                self.out.unassigned.append(
                    self._unassigned_row(d, "dropoff", d.get("shift_end_time"), "no_coordinates")
                )

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
        a drop-off leaves the office at its `drop_time`. Comparing against the
        event clock would let a car be dispatched before its previous trip had
        ended.

        Releasing here sets `current_location` to where that trip ended, which
        is the whole cascade: a pick-up then starts from the last drop's final
        stop, not from the office it never went back to.
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

    def _pair_minutes(self, a: Coord, b: Coord) -> float:
        """Driving minutes for ONE leg (the provider caches on the coord tuple)."""
        if a == b:
            return 0.0
        durations, _ = self.provider.table([a, b])
        return durations[0][1]

    def _held_karp_order(self, durations, stop_idx, start_idx, end_idx, weights=None):
        """Cheapest path start_idx -> (every stop once, any order) -> end_idx.

        Held-Karp bitmask DP. dp[mask][k] = minimum cost of a path that leaves
        start_idx, visits exactly the stops in `mask`, and ends at stop_idx[k];
        parent[] records the move used so the winning order can be
        reconstructed. Directed durations are used as-is — no symmetry assumed.
        O(n^2 * 2^n): instant for the <= ~11 stops a trip actually carries.

        `weights` prices each leg: weights[j] multiplies the leg that arrives
        at the (j+1)-th stop, and weights[n] multiplies the closing leg to
        end_idx. All ones means "shortest total time"; anything else prices the
        legs by how much they cost the passengers rather than the fleet — see
        `_ride_weights` and `_best_stop_order`.
        """
        n = len(stop_idx)
        size = 1 << n
        INF = float("inf")
        if weights is None:
            weights = [1.0] * (n + 1)
        nbits = [0] * size
        for m in range(1, size):
            nbits[m] = nbits[m >> 1] + (m & 1)
        dp = [[INF] * n for _ in range(size)]
        parent = [[-1] * n for _ in range(size)]
        for k in range(n):
            dp[1 << k][k] = weights[0] * durations[start_idx][stop_idx[k]]
        for mask in range(1, size):
            leg_in = nbits[mask]
            for k in range(n):
                if not (mask & (1 << k)) or dp[mask][k] == INF:
                    continue
                for j in range(n):
                    if mask & (1 << j):
                        continue
                    nmask = mask | (1 << j)
                    cand = dp[mask][k] + weights[leg_in] * durations[stop_idx[k]][stop_idx[j]]
                    if cand < dp[nmask][j]:
                        dp[nmask][j] = cand
                        parent[nmask][j] = k
        full = size - 1
        best_last, best_cost = -1, INF
        for k in range(n):
            cand = dp[full][k] + weights[n] * durations[stop_idx[k]][end_idx]
            if cand < best_cost:
                best_cost, best_last = cand, k
        if best_last == -1:
            return list(stop_idx)
        order_rev, mask, k = [], full, best_last
        while k != -1:
            order_rev.append(stop_idx[k])
            prev = parent[mask][k]
            mask ^= (1 << k)
            k = prev
        return order_rev[::-1]

    @staticmethod
    def _ride_weights(n: int, kind: str) -> List[float]:
        """Leg prices: one unit per passenger aboard, but never less than one.

        Summing those prices over the legs gives the TOTAL TIME PASSENGERS
        SPEND IN THE CAR, so an order that minimises it is the order that
        minimises total riding — the fairness objective. The `max(..., 1)` floor
        keeps an empty repositioning leg from being free.

        pickups   leg 0 is the repositioning leg (empty, floor 1), leg j
                  carries j passengers, the final run carries everybody.
        drop-offs leg j carries n - j passengers, the closing run is empty (1).
        """
        if kind == "pickup":
            return [max(1.0, float(j)) for j in range(n + 1)]
        return [max(1.0, float(n - j)) for j in range(n)] + [1.0]

    def _best_stop_order(self, durations, stop_idx, start_idx, end_idx, fair=True, kind="pickup"):
        """Exact order of `stop_idx` between the two fixed anchors.

        `fair=True` minimises TOTAL PASSENGER RIDE TIME; `fair=False` minimises
        total route time. Both are exact — the DP enumerates every one of the
        n! orders implicitly either way.
        """
        n = len(stop_idx)
        if n > MAX_STOPS_FOR_EXACT:
            raise ValueError(
                "route has %d stops > MAX_STOPS_FOR_EXACT=%d: exact search would not "
                "finish; trips are capacity-bounded so this should be unreachable."
                % (n, MAX_STOPS_FOR_EXACT))
        weights = self._ride_weights(n, kind) if fair else None
        return self._held_karp_order(durations, stop_idx, start_idx, end_idx, weights)

    # ── Case A fixed-route helpers ───────────────────────────────────────────

    def _stops_for_shift(self, shift_time: str, vehicles_this_shift) -> List[Dict[str, Any]]:
        """Fixed stops for a shift: this shift's stops, on cars actually in service."""
        plates_in_service = {v["plate_no"] for v in vehicles_this_shift}
        return [
            s for s in self.fixed_stops
            if normalise_clock(s["shift_time"]) == shift_time
            and s["vehicle_plate"] in plates_in_service
        ]

    def _request_zone(self, pr: Dict[str, Any]) -> Optional[str]:
        """The zone a rider belongs to: their own label, else their car's."""
        z = pr.get("zone_name")
        if z:
            return z
        v = self.fleet.get(pr.get("vehicle_plate") or "")
        return v["zone_name"] if v else None

    # ── pickup: Case A / Case B / Case B-kmeans ──────────────────────────────

    def _place_on(self, v, pr, home, route_by_car):
        """(stop_key, stop_item) for putting `pr` on car `v` — WITHOUT mutating v.

        The nearest stop of THAT car's own fixed route within the walk limit,
        else an ad-hoc stop at the door. A stop keeps its identity across
        riders — keyed by its roster name, so everyone walking to a stop shares
        it. An ad-hoc stop carries `_rank` as the fallback order for when the
        exact placement (`_case_a_order`) cannot run.
        """
        route = route_by_car.get(v["plate_no"])
        if not route:
            return None
        best = None
        for i, s in enumerate(route):
            w = self.foot.walk_minutes(home, (s["pickup_lat"], s["pickup_lng"]))
            if w <= self.cfg.walk_limit_min and (best is None or w < best[0]):
                best = (w, i, s)
        if best is not None:
            _, i, s = best
            return s["location_name"], {
                "coord": (s["pickup_lat"], s["pickup_lng"]),
                "name": s["location_name"], "is_adhoc": False,
                "_rank": (i, 0, 0.0), "passengers": [pr]}
        anchor, anchor_km = 0, None
        for i, s in enumerate(route):
            km = haversine_km(home, (s["pickup_lat"], s["pickup_lng"]))
            if anchor_km is None or km < anchor_km:
                anchor, anchor_km = i, km
        return f"adhoc_{pr['employee_email']}", {
            "coord": home, "name": f"Ad-hoc ({self._employee_name(pr['employee_email'])})",
            "is_adhoc": True, "_rank": (anchor, 1, anchor_km), "passengers": [pr]}

    def _add_to(self, v, pr, home, route_by_car) -> bool:
        """Put `pr` on `v` in place. False if `v` has no route to put them on."""
        placed = self._place_on(v, pr, home, route_by_car)
        if placed is None:
            return False
        key, item = placed
        if key in v["_stops"]:
            v["_stops"][key]["passengers"].append(pr)
        else:
            v["_stops"][key] = item
        return True

    def _assign_pickup_event(self, event) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        shift_time = event["shift_time"]

        # Every FREE car is a candidate. The roster decides the ORDER cars are
        # tried in, not whether they may work at all.
        free_cars = [v for v in self.fleet.values() if v["status"] == "AVAILABLE"]
        if not free_cars:
            return [], list(event["requests"])   # fleet exhausted
        roster_plates = {v["plate_no"] for v in free_cars
                         if shift_time in self.vehicle_shifts.get(v["plate_no"], set())}

        for v in self.fleet.values():
            v["_stops"] = {}
            v["_remaining"] = 0        # a car that is out must never look like it has room
        for v in free_cars:
            v["_remaining"] = v["capacity"]

        requests_this_shift = event["requests"]
        unassigned: List[Dict[str, Any]] = []

        # --- Case A (10 PM only): the roster's own operation (Algorithm 1) ---
        if shift_time == "22:00:00":
            a_cars = [v for v in free_cars
                      if shift_time in self.vehicle_shifts.get(v["plate_no"], set())]

            # Rule 2: every car's designated route, in the roster's sequence_order.
            route_by_car = {}
            for s in self._stops_for_shift(shift_time, a_cars):
                route_by_car.setdefault(s["vehicle_plate"], []).append(s)
            for stops in route_by_car.values():
                stops.sort(key=lambda s: (s["sequence_order"] is None, s["sequence_order"]))
            plate_coords = {p: [(s["pickup_lat"], s["pickup_lng"]) for s in stops]
                            for p, stops in route_by_car.items()}

            # Rule 5: the cars a zone can be served by.
            cars_by_zone = {}
            for v in a_cars:
                if v["plate_no"] in route_by_car:
                    cars_by_zone.setdefault(v["zone_name"], []).append(v)
            zone_coords = {z: sorted({c for v in vs for c in plate_coords[v["plate_no"]]})
                           for z, vs in cars_by_zone.items()}

            def zone_of(pr):
                return self._request_zone(pr)

            def candidate_cars(pr):
                """The rider's designated car first, then the other 10 PM cars of
                their zone, nearest first."""
                home = (pr["pickup_lat"], pr["pickup_lng"])
                own = pr.get("vehicle_plate")
                out = []
                if own in route_by_car:
                    v = self.fleet.get(own)
                    if v is not None and v["status"] == "AVAILABLE":
                        out.append(v)
                rest = [v for v in cars_by_zone.get(zone_of(pr), [])
                        if v["plate_no"] != own]
                rest.sort(key=lambda v: haversine_km(home, v["current_location"]))
                return out + rest

            # Rule 3: walking times come from the foot network, batched up front.
            seen_homes = set()
            for pr in requests_this_shift:
                home = (pr["pickup_lat"], pr["pickup_lng"])
                if home in seen_homes:
                    continue
                seen_homes.add(home)
                self.foot.prefetch(home, set(zone_coords.get(zone_of(pr), []))
                                          | set(plate_coords.get(pr.get("vehicle_plate"), [])))

            # Rule 4: serve the most constrained employees first.
            def priority_key(pr):
                home = (pr["pickup_lat"], pr["pickup_lng"])
                walks_in_range = [w for w in
                                  (self.foot.walk_minutes(home, (s["pickup_lat"], s["pickup_lng"]))
                                   for s in route_by_car.get(pr.get("vehicle_plate"), []))
                                  if w <= self.cfg.walk_limit_min]
                return (len(walks_in_range), -min(walks_in_range, default=999))

            def serve(pr, v, home):
                self._add_to(v, pr, home, route_by_car)
                v["_remaining"] -= 1

            for pr in sorted(requests_this_shift, key=priority_key):
                home = (pr["pickup_lat"], pr["pickup_lng"])
                for v in candidate_cars(pr):
                    if v["_remaining"] <= 0:
                        continue
                    serve(pr, v, home)
                    break
                else:
                    unassigned.append(pr)
            return free_cars, unassigned

        # --- Case B-kmeans (12 AM - 6 AM): cluster the riders, then match cars ---
        if shift_time in KMEANS_PICKUP_SHIFTS:
            return self._assign_pickup_clustered(free_cars, requests_this_shift,
                                                 shift_time, roster_plates)

        # --- Case B (11 PM): door-to-door — one greedy nearest-car pass ---
        pending = sorted(requests_this_shift,
                         key=lambda pr: min(haversine_km((pr["pickup_lat"], pr["pickup_lng"]),
                                               v["current_location"]) for v in free_cars))
        for pr in pending:
            home = (pr["pickup_lat"], pr["pickup_lng"])
            zone = pr.get("zone_name")
            with_room = [x for x in free_cars if x["_remaining"] > 0]
            if not with_room:
                unassigned.append(pr)
                continue
            NEAR_TIE_SLACK = 1.25
            pool = [x for x in with_room if x["plate_no"] in roster_plates] or with_room
            dists = {x["plate_no"]: haversine_km(home, x["current_location"]) for x in pool}
            best = min(dists.values())
            near = [x for x in pool if dists[x["plate_no"]] <= max(best * NEAR_TIE_SLACK,
                                                                   best + 1.0)]
            v = min(near, key=lambda x: (0 if x["_stops"] else 1,
                                         dists[x["plate_no"]],
                                         0 if x["zone_name"] == zone else 1))
            v["_stops"].setdefault(f"home_{pr['employee_email']}", {
                "coord": home,
                "name": f"Home ({self._employee_name(pr['employee_email'])})",
                "is_adhoc": True, "passengers": [],
            })["passengers"].append(pr)
            v["_remaining"] -= 1
        return free_cars, unassigned

    def _order_stops_pickup(self, vehicle) -> List[Tuple[Any, Dict[str, Any]]]:
        """Exact FAIREST order: car.current_location -> stops -> OFFICE.

        The objective is total passenger ride time, not total route time. Case A
        is the exception: the roster's fixed stops are pinned in their own
        `sequence_order` and only the ad-hoc door stops are placed, exactly, by
        `_case_a_order`.
        """
        items = list(vehicle["_stops"].items())
        if len(items) <= 1:
            return items
        if all("_rank" in it for _, it in items):
            if (any(it["is_adhoc"] for _, it in items)
                    and len(items) <= MAX_STOPS_FOR_EXACT):
                return self._case_a_order(vehicle, items)
            return sorted(items, key=lambda kv: kv[1]["_rank"])
        coords = [vehicle["current_location"]] + [it["coord"] for _, it in items] + [self.office]
        START, END = 0, len(items) + 1
        durations, _ = self.provider.table(coords)
        stop_idx = list(range(1, len(items) + 1))
        ordered = self._best_stop_order(durations, stop_idx, START, END, kind="pickup")
        return [items[i - 1] for i in ordered]

    def _case_a_order(self, vehicle, items) -> List[Tuple[Any, Dict[str, Any]]]:
        """Case A: the roster's fixed stops in the roster's own order, with the
        ad-hoc (door) stops slotted optimally among them.

        The DP's state is (how many fixed stops are behind us, which doors are
        placed, where we are standing); the objective is the 120-min cap's own
        quantity (first pickup to office), ties broken on the full trip.
        """
        pinned = sorted(items, key=lambda kv: kv[1]["_rank"])
        fixed = [(k, it) for k, it in pinned if not it["is_adhoc"]]
        doors = [(k, it) for k, it in pinned if it["is_adhoc"]]
        if not doors:
            return pinned
        n, k = len(fixed), len(doors)
        coords = ([vehicle["current_location"]]
                  + [it["coord"] for _, it in fixed]
                  + [it["coord"] for _, it in doors]
                  + [self.office])
        durations, _ = self.provider.table(coords)
        START, END = 0, len(coords) - 1
        FIXED = list(range(1, 1 + n))
        DOOR = list(range(1 + n, 1 + n + k))
        DOMINATE = 1e4

        def leg(u, w):
            d = durations[u][w]
            return DOMINATE * (0.0 if u == START else d) + d

        size = 1 << k
        INF = float("inf")
        AT_FIXED = 0
        dp = [[[INF] * (k + 1) for _ in range(size)] for _ in range(n + 1)]
        back = [[[None] * (k + 1) for _ in range(size)] for _ in range(n + 1)]
        dp[0][0][AT_FIXED] = 0.0
        for i in range(n + 1):
            for mask in range(size):
                for j in range(k + 1):
                    cur = dp[i][mask][j]
                    if cur == INF:
                        continue
                    if j != AT_FIXED:
                        here = DOOR[j - 1]
                    else:
                        here = START if i == 0 else FIXED[i - 1]
                    if i < n:
                        nxt = cur + leg(here, FIXED[i])
                        if nxt < dp[i + 1][mask][AT_FIXED]:
                            dp[i + 1][mask][AT_FIXED] = nxt
                            back[i + 1][mask][AT_FIXED] = (i, mask, j)
                    for l in range(k):
                        if mask & (1 << l):
                            continue
                        nxt = cur + leg(here, DOOR[l])
                        if nxt < dp[i][mask | (1 << l)][l + 1]:
                            dp[i][mask | (1 << l)][l + 1] = nxt
                            back[i][mask | (1 << l)][l + 1] = (i, mask, j)

        full = size - 1
        best, best_j = INF, AT_FIXED
        for j in range(k + 1):
            if dp[n][full][j] == INF:
                continue
            if j == AT_FIXED:
                if n == 0:
                    continue
                here = FIXED[n - 1]
            else:
                here = DOOR[j - 1]
            cand = dp[n][full][j] + leg(here, END)
            if cand < best:
                best, best_j = cand, j

        seq, i, mask, j = [], n, full, best_j
        while back[i][mask][j] is not None:
            pi, pmask, pj = back[i][mask][j]
            if i != pi:
                seq.append(fixed[i - 1])
            else:
                seq.append(doors[(mask ^ pmask).bit_length() - 1])
            i, mask, j = pi, pmask, pj
        seq.reverse()
        return seq

    def _compute_timing_pickup(self, vehicle, ordered_stops, shift_time) -> Dict[str, Any]:
        deadline = self._parse_time(shift_time) - timedelta(minutes=self.cfg.office_buffer_min)
        coords = [vehicle["current_location"]] + [it["coord"] for _, it in ordered_stops] + [self.office]
        durations, distances = self.provider.table(coords)
        legs = [durations[i][i + 1] for i in range(len(coords) - 1)]
        # The full trip includes the deadhead leg from wherever the car actually
        # started; the 120-min cap measures the passenger journey (first pickup
        # -> office), i.e. legs[1:] plus one boarding buffer per stop.
        total = sum(legs) + self.cfg.boarding_buffer_min * len(ordered_stops)
        passenger_total = sum(legs[1:]) + self.cfg.boarding_buffer_min * len(ordered_stops)
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
            "passenger_total_minutes": passenger_total,
            "leg_minutes": legs,
            "leg_km": [distances[i][i + 1] for i in range(len(coords) - 1)],
            "stop_timestamps": timestamps,
        }

    def _pickup_window_minutes(self, vehicle, shift_time) -> float:
        """How long this car may actually spend on the road for this shift.

        A pickup is planned BACKWARD from `shift - 5 min`, so the trip really
        starts at `parking_departure` — which can precede the event clock by up
        to two hours. Availability therefore has to be checked over the whole
        window, not at the event instant.
        """
        deadline = self._parse_time(shift_time) - timedelta(minutes=self.cfg.office_buffer_min)
        free_at = vehicle.get("_free_at")
        if free_at is None:
            return float("inf")
        return (deadline - free_at).total_seconds() / 60.0

    def _enforce_cap_pickup(self, vehicle, shift_time):
        """Shed stops until the route fits BOTH the 120-min cap and the free window.

        The cap counts the passenger journey (first pickup stop -> office); the
        free-window check measures the FULL trip, deadhead included.
        """
        window = self._pickup_window_minutes(vehicle, shift_time)
        reason = ("vehicle_not_free_in_time" if window < self.cfg.max_route_minutes
                  else "dropped_for_120min_cap")
        dropped: List[Dict[str, Any]] = []
        while True:
            ordered = self._order_stops_pickup(vehicle)
            if not ordered:
                return ordered, None, dropped, reason
            timing = self._compute_timing_pickup(vehicle, ordered, shift_time)
            over_cap = timing["passenger_total_minutes"] - self.cfg.max_route_minutes
            over_free = timing["total_minutes"] - window
            if over_cap <= 0 and over_free <= 0:
                return ordered, timing, dropped, reason
            best_key, best_score = None, None
            for key, _ in ordered:
                saved = vehicle["_stops"]
                vehicle["_stops"] = {k: v for k, v in saved.items() if k != key}
                trial = self._order_stops_pickup(vehicle)
                if trial:
                    tt = self._compute_timing_pickup(vehicle, trial, shift_time)
                    score = max(tt["passenger_total_minutes"] - self.cfg.max_route_minutes,
                                tt["total_minutes"] - window)
                else:
                    score = 0
                vehicle["_stops"] = saved
                if best_score is None or score < best_score:
                    best_score, best_key = score, key
            dropped.extend(vehicle["_stops"].pop(best_key)["passengers"])

    def _redistribute_case_a(self, vehicles_this_shift, shift_time) -> List[Dict[str, Any]]:
        """Case A only: shed the stops that break the cap, then re-place their
        riders on another 22:00 car of the SAME zone.

        Returns the riders no car could take. Mutates `_stops` on the cars it uses.
        """
        a_cars = [v for v in vehicles_this_shift
                  if shift_time in self.vehicle_shifts.get(v["plate_no"], set())]
        if not a_cars:
            return []
        by_zone = {}
        for v in a_cars:
            by_zone.setdefault(v["zone_name"], []).append(v)
        route_by_car = {}
        for s in self._stops_for_shift(shift_time, a_cars):
            route_by_car.setdefault(s["vehicle_plate"], []).append(s)
        for stops in route_by_car.values():
            stops.sort(key=lambda s: (s["sequence_order"] is None, s["sequence_order"]))

        tried: Dict[str, set] = {}

        def _seats(v):
            return v["capacity"] - sum(len(it["passengers"]) for it in v["_stops"].values())

        def _time(v, stops):
            saved = v["_stops"]
            v["_stops"] = stops
            try:
                ordered = self._order_stops_pickup(v)
                if not ordered:
                    return None, None
                return ordered, self._compute_timing_pickup(v, ordered, shift_time)
            finally:
                v["_stops"] = saved

        def _fits(v, stops):
            ordered, t = _time(v, stops)
            if not ordered:
                return False
            return (t["passenger_total_minutes"] <= self.cfg.max_route_minutes
                    and t["total_minutes"] <= self._pickup_window_minutes(v, shift_time))

        def _shed():
            out = []
            for v in a_cars:
                while v["_stops"] and not _fits(v, v["_stops"]):
                    window = self._pickup_window_minutes(v, shift_time)
                    best_key, best_over = None, None
                    for key in list(v["_stops"]):
                        trial = {x: y for x, y in v["_stops"].items() if x != key}
                        if trial:
                            _, t = _time(v, trial)
                            over = max(t["passenger_total_minutes"] - self.cfg.max_route_minutes,
                                       t["total_minutes"] - window)
                        else:
                            over = 0.0
                        if best_over is None or over < best_over:
                            best_over, best_key = over, key
                    for pr in v["_stops"].pop(best_key)["passengers"]:
                        tried.setdefault(pr["employee_email"], set()).add(v["plate_no"])
                        out.append(pr)
            return out

        def _replace(pr):
            home = (pr["pickup_lat"], pr["pickup_lng"])
            done = tried.setdefault(pr["employee_email"], set())
            pool = [v for v in by_zone.get(self._request_zone(pr), [])
                    if v["plate_no"] not in done]
            pool.sort(key=lambda v: haversine_km(home, v["current_location"]))
            for v in pool:
                done.add(v["plate_no"])
                if _seats(v) <= 0:
                    continue
                placed = self._place_on(v, pr, home, route_by_car)
                if placed is None:
                    continue
                key, item = placed
                stops = dict(v["_stops"])
                if key in stops:
                    stops[key] = dict(stops[key],
                                      passengers=list(stops[key]["passengers"]) + [pr])
                else:
                    stops[key] = item
                if _fits(v, stops):
                    v["_stops"] = stops
                    return True
            return False

        pool = _shed()
        for _ in range(len(a_cars) + 2):
            if not pool:
                return []
            left = [pr for pr in pool if not _replace(pr)]
            if len(left) == len(pool):
                return left
            pool = left + _shed()
        return pool

    # ── Case B-kmeans internals ──────────────────────────────────────────────

    @staticmethod
    def _cluster_xy(homes):
        """Rider homes in local kilometres (a degree of longitude spans ~0.92
        of a degree of latitude at Dhaka's latitude)."""
        lat0 = sum(h[0] for h in homes) / len(homes)
        lng0 = sum(h[1] for h in homes) / len(homes)
        kx = 111.32 * math.cos(math.radians(lat0))
        return [((h[1] - lng0) * kx, (h[0] - lat0) * 110.57) for h in homes]

    @staticmethod
    def _kmeans_fill(xy, cents, cap):
        """Send every rider to a cluster: nearest first, never past `cap`."""
        n, k = len(xy), len(cents)
        room = [cap] * k
        who = [-1] * n
        pairs = sorted((math.hypot(x - cx, y - cy), i, c)
                       for i, (x, y) in enumerate(xy)
                       for c, (cx, cy) in enumerate(cents))
        for _d, i, c in pairs:
            if who[i] == -1 and room[c] > 0:
                who[i] = c
                room[c] -= 1
        for i in range(n):
            if who[i] == -1:
                x, y = xy[i]
                free = [c for c in range(k) if room[c] > 0]
                c = min(free, key=lambda c: math.hypot(x - cents[c][0], y - cents[c][1]))
                who[i] = c
                room[c] -= 1
        return who

    def _kmeans_riders(self, homes, k, cap):
        """Capacity-constrained k-means over the riders' homes.

        Returns the rider indices of each cluster, taken from the tightest of
        CLUSTER_RESTARTS seeded k-means++ starts. The seed is fixed on purpose.
        """
        xy = self._cluster_xy(homes)
        n = len(xy)
        if k <= 1:
            return [list(range(n))] if n else []
        best, best_sse = None, None
        for r in range(CLUSTER_RESTARTS):
            rng = random.Random(CLUSTER_SEED * 9973 + r)
            cents = [list(xy[rng.randrange(n)])]
            while len(cents) < k:
                d2 = [min((x - cx) ** 2 + (y - cy) ** 2 for cx, cy in cents)
                      for x, y in xy]
                tot = sum(d2)
                if tot <= 0.0:
                    cents.append(list(xy[rng.randrange(n)]))
                    continue
                t, acc, pick = rng.random() * tot, 0.0, 0
                for i, v in enumerate(d2):
                    acc += v
                    if acc >= t:
                        pick = i
                        break
                cents.append(list(xy[pick]))
            who = []
            for _ in range(40):
                who = self._kmeans_fill(xy, cents, cap)
                acc = [[0.0, 0.0, 0] for _ in range(k)]
                for i, c in enumerate(who):
                    acc[c][0] += xy[i][0]
                    acc[c][1] += xy[i][1]
                    acc[c][2] += 1
                nxt = [[acc[c][0] / acc[c][2], acc[c][1] / acc[c][2]] if acc[c][2]
                       else list(cents[c]) for c in range(k)]
                if nxt == cents:
                    break
                cents = nxt
            sse = sum((xy[i][0] - cents[c][0]) ** 2 + (xy[i][1] - cents[c][1]) ** 2
                      for i, c in enumerate(who))
            if best_sse is None or sse < best_sse - 1e-9:
                best_sse = sse
                best = [[i for i, c in enumerate(who) if c == j] for j in range(k)]
        return [cl for cl in best if cl]

    def _match_clusters_to_cars(self, clusters, homes, rzones, cars):
        """Cheapest pairing of clusters to cars, or (None, None) if there is none."""
        k = len(clusters)
        cents = [(sum(homes[i][0] for i in cl) / len(cl),
                  sum(homes[i][1] for i in cl) / len(cl)) for cl in clusters]
        czone = [Counter(rzones[i] for i in cl).most_common(1)[0][0] for cl in clusters]
        cost = {}
        for ci, cl in enumerate(clusters):
            for v in cars:
                if v["capacity"] < len(cl):
                    continue
                pen = 0.0 if v["zone_name"] == czone[ci] else CLUSTER_ZONE_PENALTY_KM
                cost[(ci, v["plate_no"])] = haversine_km(cents[ci], v["current_location"]) + pen
        short_plates = set()
        for ci in range(k):
            for p in sorted((p for p in cost if p[0] == ci), key=lambda p: cost[p])[:k]:
                short_plates.add(p[1])
        short = [v for v in cars if v["plate_no"] in short_plates]
        if len(short) < k:
            return None, None
        if k > 4:
            taken, left, pick = set(), set(range(k)), {}
            for (ci, plate) in sorted(cost, key=lambda p: cost[p]):
                if ci in left and plate not in taken:
                    left.discard(ci)
                    taken.add(plate)
                    pick[ci] = plate
            if left:
                return None, None
            got = [next(v for v in cars if v["plate_no"] == pick[ci]) for ci in range(k)]
            return got, sum(cost[(ci, pick[ci])] for ci in range(k))
        best, best_cost = None, None
        for perm in permutations(short, k):
            c, ok = 0.0, True
            for ci, v in enumerate(perm):
                key = (ci, v["plate_no"])
                if key not in cost:
                    ok = False
                    break
                c += cost[key]
            if ok and (best_cost is None or c < best_cost):
                best_cost, best = c, perm
        if best is None:
            return None, None
        return list(best), best_cost

    def _cluster_stops(self, cluster, riders, homes):
        """The door stops for one cluster: one ad-hoc stop per rider."""
        return {f"home_{riders[i]['employee_email']}": {
            "coord": homes[i],
            "name": f"Home ({self._employee_name(riders[i]['employee_email'])})",
            "is_adhoc": True, "passengers": [riders[i]]} for i in cluster}

    def _route_fits(self, v, stops, shift_time) -> bool:
        """Would this car's pickup route, with exactly these stops, clear both clocks?"""
        saved = v["_stops"]
        v["_stops"] = stops
        try:
            ordered = self._order_stops_pickup(v)
            if not ordered:
                return True
            t = self._compute_timing_pickup(v, ordered, shift_time)
            return (t["passenger_total_minutes"] <= self.cfg.max_route_minutes
                    and t["total_minutes"] <= self._pickup_window_minutes(v, shift_time))
        finally:
            v["_stops"] = saved

    def _place_clusters_greedy(self, clusters, homes, rzones, free_cars):
        """Last resort: biggest cluster first, onto the cheapest car that can hold it."""
        out, taken = [], set()
        for cl in sorted(clusters, key=len, reverse=True):
            cent = (sum(homes[i][0] for i in cl) / len(cl),
                    sum(homes[i][1] for i in cl) / len(cl))
            z = Counter(rzones[i] for i in cl).most_common(1)[0][0]
            best, best_key = None, None
            for v in free_cars:
                if v["plate_no"] in taken or v["capacity"] < len(cl):
                    continue
                key = (0 if v["zone_name"] == z else 1,
                       haversine_km(cent, v["current_location"]))
                if best_key is None or key < best_key:
                    best_key, best = key, v
            if best is not None:
                taken.add(best["plate_no"])
                out.append((cl, best))
        return out

    def _spill_riders(self, left, free_cars):
        """Safety net: put any rider the clustering could not place onto the
        nearest car that still has a free seat."""
        out = []
        for pr in left:
            home = (pr["pickup_lat"], pr["pickup_lng"])
            room = [v for v in free_cars if v["_remaining"] > 0]
            if not room:
                out.append(pr)
                continue
            v = min(room, key=lambda v: haversine_km(home, v["current_location"]))
            v["_stops"].update(self._cluster_stops([0], [pr], [home]))
            v["_remaining"] -= 1
        return out

    def _assign_pickup_clustered(self, free_cars, requests_this_shift, shift_time,
                                 roster_plates) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """12 AM - 6 AM pick-ups: cluster the riders, then match the clusters to cars.

        k is swept upward from the fewest cars that can physically hold
        everyone, and the FIRST k whose routes clear both clocks wins.
        """
        riders = list(requests_this_shift)
        n = len(riders)
        if not n:
            return free_cars, []
        homes = [(r["pickup_lat"], r["pickup_lng"]) for r in riders]
        rzones = [self._request_zone(r) for r in riders]
        caps = sorted((v["capacity"] for v in free_cars), reverse=True)
        rostered = [v for v in free_cars if v["plate_no"] in roster_plates]
        pools = [(label, p) for label, p in
                 ((f"rostered({len(rostered)})", rostered), ("anyone", free_cars)) if p]

        def _match(clusters):
            for label, pool in pools:
                got, cost = self._match_clusters_to_cars(clusters, homes, rzones, pool)
                if got is not None:
                    return got, cost, label
            return None, None, None

        tries, chosen = [], None
        if n <= sum(caps):
            for k in range(max(1, math.ceil(n / caps[0])), min(len(free_cars), n) + 1):
                clusters = self._kmeans_riders(homes, k, caps[k - 1])
                if not clusters:
                    continue
                got, cost, label = _match(clusters)
                if got is None:
                    tries.append(f"k={k}: no car can hold every cluster")
                    continue
                if not all(self._route_fits(v, self._cluster_stops(cl, riders, homes), shift_time)
                           for cl, v in zip(clusters, got)):
                    tries.append(f"k={k}: a route breaks the 120-min cap or the free window")
                    continue
                chosen = (clusters, got, cost, label)
                break
        if chosen is None:
            clusters = self._kmeans_riders(homes, max(1, min(len(free_cars), n)), caps[0])
            got, cost, label = _match(clusters) if clusters else (None, None, None)
            if got is not None:
                chosen = (clusters, got, cost, label)
            else:
                pairs = self._place_clusters_greedy(clusters, homes, rzones, free_cars)
                if not pairs:
                    logger.warning("[%s] %d riders: no free car to take them",
                                   shift_time, len(riders))
                    return free_cars, riders
                chosen = ([cl for cl, _ in pairs], [v for _, v in pairs], 0.0, "greedy")

        clusters, got, cost, label = chosen
        placed = set()
        for cl, v in zip(clusters, got):
            v["_stops"].update(self._cluster_stops(cl, riders, homes))
            v["_remaining"] -= len(cl)
            placed.update(cl)
        unassigned = [riders[i] for i in range(n) if i not in placed]
        if unassigned:
            unassigned = self._spill_riders(unassigned, free_cars)

        cross = sum(1 for cl, v in zip(clusters, got)
                    if Counter(rzones[i] for i in cl).most_common(1)[0][0] != v["zone_name"])
        logger.info("[%s] %d riders -> %d car(s) %s | pool=%s deadhead=%.1f km cross-zone=%d"
                    + (f" | {len(unassigned)} unassigned" if unassigned else ""),
                    shift_time, n, len(clusters),
                    ", ".join(str(len(c)) for c in clusters), label, cost, cross)
        return free_cars, unassigned

    # ── drop-off: Case C / Case D / evening fit ─────────────────────────────

    def _deadhead_to_office(self, v) -> float:
        """Minutes for this car to reach the office from where it currently is."""
        loc = v["current_location"]
        if loc == self.office:
            return 0.0
        return self._pair_minutes(loc, self.office)

    def _can_serve_dropoff(self, v, office_departure) -> Tuple[bool, float]:
        """A car may work a drop-off only if it can physically be at the office
        by the scheduled departure: free when its last trip ends, plus deadhead."""
        free_at = v.get("_free_at")
        if free_at is None:
            return True, 0.0
        dh = self._deadhead_to_office(v)
        return free_at + timedelta(minutes=dh) <= office_departure, dh

    def _fit_route(self, plate_no: str, shift_end_time: str) -> Optional[List[Coord]]:
        """The curve a car is fitted to at this shift end, as [(lat, lng), ...].

        The roster keys a route by the shift it STARTS at, so the route sharing
        this shift's end label is the natural curve. A car with no route at that
        label falls back to its 22:00 route, then its earliest route of the night.
        """
        for key in ((plate_no, shift_end_time), (plate_no, "22:00:00")):
            stops = self._route_by_car_shift.get(key)
            if stops:
                return [(s["pickup_lat"], s["pickup_lng"]) for s in stops]
        keys = [(t, v) for (p, t), v in self._route_by_car_shift.items() if p == plate_no]
        if not keys:
            return None
        return [(s["pickup_lat"], s["pickup_lng"])
                for s in min(keys, key=lambda kv: night_offset(self._raw_time(kv[0])))[1]]

    def _sse_residual(self, home: Coord, curve) -> float:
        """One rider's squared residual: (km to the nearest stop of `curve`)^2."""
        return min(haversine_km(home, s) for s in curve) ** 2

    def _sse_fit_assign(self, requests, cars, shift_end_time, allow_routeless=False):
        """Least-squares rider -> car assignment under each car's free seats.

        Exact (Hungarian), not greedy. Returns (placed, unplaced), where
        `placed` is [(car, [request, ...]), ...] — the shape
        `_assign_dropoff_event` merges by plate.
        """
        curves = {}
        pool = []
        for v in cars:
            curve = self._fit_route(v["plate_no"], shift_end_time)
            if curve is None and not allow_routeless:
                continue
            curves[v["plate_no"]] = curve
            pool.append(v)

        riders = list(requests)
        slots = []
        for v in pool:
            slots.extend([v] * self._free_seats(v))
        if not riders or not slots:
            return [], riders

        n, m = len(riders), len(slots)
        size = max(n, m)
        cost = [[0.0] * size for _ in range(size)]
        for i, d in enumerate(riders):
            home = (d["drop_lat"], d["drop_lng"])
            for j, v in enumerate(slots):
                curve = curves[v["plate_no"]]
                cost[i][j] = _NO_CURVE_COST if curve is None else self._sse_residual(home, curve)
        for j in range(m, size):
            for i in range(n):
                cost[i][j] = _UNSEATABLE_COST

        rows, cols = linear_sum_assignment(cost)
        placed, unplaced = {}, []
        for i, j in zip(rows, cols):
            if i >= n:
                continue                         # a dummy rider = an empty seat
            if j >= m or cost[i][j] >= _UNSEATABLE_COST:
                # A dummy seat is a rider no real car could take, so report them
                # rather than letting the assignment quietly drop them.
                unplaced.append(riders[i])
                continue
            v = slots[j]
            placed.setdefault(v["plate_no"], (v, []))[1].append(riders[i])

        for v, emps in placed.values():
            v["_used"] = v.get("_used", 0) + len(emps)
        return list(placed.values()), unplaced

    def _assign_evening(self, event, reachable) -> Tuple[List, List]:
        """Zone-strict least-squares fit, then a cross-zone spill only if needed."""
        shift_end_time = event["shift_time"]

        def eligible():
            return [v for v in self.fleet.values()
                    if v["plate_no"] in reachable and v["status"] == "AVAILABLE"
                    and self._free_seats(v) > 0]

        by_zone = {}
        for d in event["requests"]:
            by_zone.setdefault(d.get("zone_name"), []).append(d)

        assigned_vehicles, unassigned, spill = [], [], []
        for zone, emps in by_zone.items():
            cars = [v for v in eligible() if zone is not None and v["zone_name"] == zone]
            placed, left = self._sse_fit_assign(emps, cars, shift_end_time)
            assigned_vehicles.extend(placed)
            spill.extend(left)

        if spill:
            placed, left = self._sse_fit_assign(spill, eligible(), shift_end_time,
                                                allow_routeless=True)
            assigned_vehicles.extend(placed)
            unassigned.extend(left)

        return assigned_vehicles, unassigned

    def _assign_capacity(self, v, emps, assigned_vehicles, unassigned, allow, reachable,
                         ref_plate) -> None:
        """Place `emps` on `v`, spilling any overflow onto other eligible cars."""
        room = self._free_seats(v)

        if len(emps) <= room:
            v["_used"] = v.get("_used", 0) + len(emps)
            assigned_vehicles.append((v, emps))
            return

        keep, overflow = emps[:room], emps[room:]
        if keep:
            v["_used"] = v.get("_used", 0) + len(keep)
            assigned_vehicles.append((v, keep))

        extra = [x for x in allow
                 if x["plate_no"] in reachable and x["status"] == "AVAILABLE"
                 and x["plate_no"] != ref_plate and self._free_seats(x) > 0]

        # Fill the roomiest eligible car first, then the next, until the
        # overflow is placed (never all-or-nothing on ONE car).
        extra.sort(key=lambda x: -self._free_seats(x))
        still = list(overflow)
        for x in extra:
            if not still:
                break
            take = still[:self._free_seats(x)]
            del still[:len(take)]
            x["_used"] = x.get("_used", 0) + len(take)
            assigned_vehicles.append((x, take))
        unassigned.extend(still)

    def _point_in_ring(self, pt, ring) -> bool:
        """Ray-casting point-in-polygon over (lon, lat) pairs; any simple ring."""
        x, y = pt[0], pt[1]
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def _in_mirpur_uttara(self, home: Coord) -> bool:
        """True when `home` (lat, lng) lies in Mirpur or Uttara — the Case D
        metro bucket."""
        lat, lng = home
        in_mirpur = (MIRPUR_BBOX[0] <= lat <= MIRPUR_BBOX[2]
                     and MIRPUR_BBOX[1] <= lng <= MIRPUR_BBOX[3])
        return in_mirpur or self._point_in_ring((lng, lat), UTTARA_QUAD)

    def _nearest_catalog_stop(self, home: Coord):
        """(coord, name) of the nearest catalog stop to `home`, by haversine."""
        name, coord = min(self._main_road_stops, key=lambda t: haversine_km(home, t[1]))
        return coord, name

    def _dropoff_stop_for(self, d, is_0730: bool):
        """(coord, name) of the stop one drop-off rider gets off at.

        Case C is door-to-door; Case D (07:30) is main-road only. Shared by the
        stop-building loop in `_assign_dropoff_event` and by the second chance,
        so a re-placed 07:30 rider still gets the Agargaon Metro drop.
        """
        home = (d["drop_lat"], d["drop_lng"])
        if not is_0730:
            return home, f"Home ({self._employee_name(d['employee_email'])})"
        if not self._is_friday_dropoff and self._in_mirpur_uttara(home):
            return self.cfg.agargaon_metro, "Agargaon Metro Station (shared drop point)"
        return self._nearest_catalog_stop(home)

    def _assign_dropoff_event(self, event):
        shift_end_time = event["shift_time"]
        drop_time = event["time"]
        office_departure = self._parse_time(drop_time)
        is_0730 = (drop_time == "07:30:00")

        for v in self.fleet.values():
            v["_stops"] = {}
            v["_used"] = 0

        # Who can be at the office in time, and at what cost.
        reachable = {}
        for v in self.fleet.values():
            ok, dh = self._can_serve_dropoff(v, office_departure)
            if ok:
                reachable[v["plate_no"]] = dh
        allow = list(self.fleet.values())

        rostered = {v["plate_no"] for v in self.fleet.values()
                    if shift_end_time in self.vehicle_shifts.get(v["plate_no"], set())}

        def tier_pool(zone):
            elig = [v for v in self.fleet.values()
                    if v["plate_no"] in reachable and v["status"] == "AVAILABLE"
                    and self._free_seats(v) > 0]
            rostered_elig = [v for v in elig if v["plate_no"] in rostered]
            return [rostered_elig, elig]

        def pick(candidates, zone):
            return min(candidates, key=lambda x: (reachable[x["plate_no"]],
                                                  0 if x["zone_name"] == zone else 1))

        if drop_time in EVENING_FIT_EVENTS:
            assigned_vehicles, unassigned = self._assign_evening(event, reachable)
        else:
            groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
            for d in event["requests"]:
                pref_plate = self.pickup_vehicle_by_employee.get(d["employee_email"])
                groups.setdefault(pref_plate, []).append(d)

            assigned_vehicles = []
            unassigned: List[Dict[str, Any]] = []

            for pref_plate, emps in groups.items():
                zone = emps[0].get("zone_name")
                ref = (emps[0]["drop_lat"], emps[0]["drop_lng"])
                if (pref_plate and pref_plate in self.fleet
                        and self.fleet[pref_plate]["plate_no"] in reachable
                        and self.fleet[pref_plate]["status"] == "AVAILABLE"
                        and self._free_seats(self.fleet[pref_plate]) > 0):
                    v = self.fleet[pref_plate]
                else:
                    candidates = next((t for t in tier_pool(zone) if t), [])
                    if not candidates:
                        unassigned.extend(emps)
                        continue
                    v = pick(candidates, zone)
                self._assign_capacity(v, emps, assigned_vehicles, unassigned, allow,
                                      reachable, v["plate_no"])

        # One vehicle can legitimately receive more than one group (reuse +
        # borrow + overflow). Merge per plate BEFORE building stops.
        merged: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        for v, emps in assigned_vehicles:
            merged.setdefault(v["plate_no"], (v, []))[1].extend(emps)

        for v, emps in merged.values():
            v["_stops"] = {}
            for d in emps:
                coord, name = self._dropoff_stop_for(d, is_0730)
                v["_stops"].setdefault(coord, {
                    "coord": coord, "name": name,
                    "is_shared": is_0730, "is_adhoc": not is_0730, "passengers": [],
                })["passengers"].append(d)
        return [v for v, _ in merged.values()], unassigned

    def _order_stops_dropoff(self, vehicle) -> List[Tuple[Any, Dict[str, Any]]]:
        """Exact shortest OPEN path: OFFICE -> every stop, ending at the last home.

        The tour is open because the car does not come back. The closing leg is
        priced at `cfg.dropoff_return_weight` (0.0 by default): a tie-break for
        where the night ends, not a cost.
        """
        items = list(vehicle["_stops"].items())
        if len(items) <= 1:
            return items
        coords = [self.office] + [it["coord"] for _, it in items] + [self.office]
        START, END = 0, len(items) + 1
        durations, _ = self.provider.table(coords)
        stop_idx = list(range(1, len(items) + 1))
        weights = [1.0] * len(stop_idx) + [self.cfg.dropoff_return_weight]
        ordered = self._held_karp_order(durations, stop_idx, START, END, weights)
        return [items[i - 1] for i in ordered]

    def _compute_timing_dropoff(self, vehicle, ordered_stops, office_departure) -> Dict[str, Any]:
        """Forward timing of car.current_location -> OFFICE -> stops, leaving the
        office at `office_departure` (the drop_time, not the shift end — employees
        wait 15/30 min for the car).

        The tour ENDS AT THE LAST STOP. The 120-min cap measures the PASSENGER
        journey (office -> last stop); the deadhead in from the car's previous
        position is the car's own repositioning and is reported separately.
        """
        coords = [vehicle["current_location"], self.office] + [it["coord"] for _, it in ordered_stops]
        durations, distances = self.provider.table(coords)
        legs = [durations[i][i + 1] for i in range(len(coords) - 1)]
        deadhead = legs[0]
        timestamps = []
        t = office_departure
        for i, (key, _item) in enumerate(ordered_stops):
            t = t + timedelta(minutes=legs[i + 1])
            arrival = t
            t = t + timedelta(minutes=self.cfg.boarding_buffer_min)
            timestamps.append({"stop_key": key, "arrival": arrival, "departure": t})
        tour_end = t
        return_deadhead = (self._pair_minutes(ordered_stops[-1][1]["coord"], self.office)
                           if ordered_stops else 0.0)
        passenger_total = (tour_end - office_departure).total_seconds() / 60.0
        return {
            "office_departure": office_departure,
            "tour_end": tour_end,
            "trip_start": office_departure - timedelta(minutes=deadhead),
            "deadhead_minutes": deadhead,
            "return_deadhead_minutes": return_deadhead,
            "total_minutes": passenger_total + deadhead,
            "passenger_total_minutes": passenger_total,
            "end_location": ordered_stops[-1][1]["coord"] if ordered_stops else self.office,
            "leg_minutes": legs[1:],
            "leg_km": [distances[i][i + 1] for i in range(1, len(coords) - 1)],
            "stop_timestamps": timestamps,
        }

    def _enforce_cap_dropoff(self, vehicle, office_departure):
        """A drop-off runs FORWARD from a fixed office departure, so only the
        120-min passenger cap (office -> last stop) can bite here."""
        dropped: List[Dict[str, Any]] = []
        reason = "dropped_for_120min_cap"
        while True:
            ordered = self._order_stops_dropoff(vehicle)
            if not ordered:
                return ordered, None, dropped, reason
            timing = self._compute_timing_dropoff(vehicle, ordered, office_departure)
            if timing["passenger_total_minutes"] <= self.cfg.max_route_minutes:
                return ordered, timing, dropped, reason
            best_key, best_total = None, None
            for key, _ in ordered:
                saved = vehicle["_stops"]
                vehicle["_stops"] = {k: v for k, v in saved.items() if k != key}
                trial = self._order_stops_dropoff(vehicle)
                trial_total = (self._compute_timing_dropoff(vehicle, trial, office_departure)
                               ["passenger_total_minutes"] if trial else 0)
                vehicle["_stops"] = saved
                if best_total is None or trial_total < best_total:
                    best_total, best_key = trial_total, key
            dropped.extend(vehicle["_stops"].pop(best_key)["passengers"])

    # ── second chance ────────────────────────────────────────────────────────

    @staticmethod
    def _seats_left(v: Dict[str, Any]) -> int:
        """Seats left on `v` for THIS event, counted off the stops it has."""
        return v["capacity"] - sum(len(it["passengers"]) for it in v["_stops"].values())

    def _dropoff_fits(self, v, stops, office_departure) -> bool:
        """Would this car's drop-off, with exactly these stops, clear the cap?"""
        saved = v["_stops"]
        v["_stops"] = stops
        try:
            ordered = self._order_stops_dropoff(v)
            if not ordered:
                return True
            return (self._compute_timing_dropoff(v, ordered, office_departure)
                    ["passenger_total_minutes"] <= self.cfg.max_route_minutes)
        finally:
            v["_stops"] = saved

    def _second_chance_stop(self, d, home, v, is_pickup, shift_time, is_0730, route_by_car):
        """(stop_key, stop_item) for putting rider `d` on car `v`, or None.

        The stop rule of the shift, never a new one, so a re-placed rider lands
        where the roster would have sent them in the first place.
        """
        if is_pickup:
            if shift_time == "22:00:00":
                return self._place_on(v, d, home, route_by_car)
            return f"home_{d['employee_email']}", {
                "coord": home,
                "name": f"Home ({self._employee_name(d['employee_email'])})",
                "is_adhoc": True, "passengers": [d]}
        coord, name = self._dropoff_stop_for(d, is_0730)
        return coord, {"coord": coord, "name": name,
                       "is_shared": is_0730, "is_adhoc": not is_0730,
                       "passengers": [d]}

    def _second_chance(self, event, shift_time, left):
        """Offer every rider the first pass left behind one more car.

        `left` is those riders, in the order they are to be considered. Returns
        (still_left, touched): the riders no car could take, and the plates
        whose stops changed, so the caller knows which routes to time again.
        """
        if not left:
            return [], set()
        is_pickup = event["type"] == "pickup"
        drop_time = event["time"]
        is_0730 = (not is_pickup) and drop_time == "07:30:00"
        office_departure = None if is_pickup else self._parse_time(drop_time)

        # Case A confines a rider to their own zone (rule 5); every other shift
        # treats the zone as a tie-break.
        zone_gate = is_pickup and shift_time == "22:00:00"

        route_by_car = {}
        if zone_gate:
            for s in self._stops_for_shift(shift_time, list(self.fleet.values())):
                route_by_car.setdefault(s["vehicle_plate"], []).append(s)
            for stops in route_by_car.values():
                stops.sort(key=lambda s: (s["sequence_order"] is None, s["sequence_order"]))

        rostered = {v["plate_no"] for v in self.fleet.values()
                    if shift_time in self.vehicle_shifts.get(v["plate_no"], set())}

        def candidates(home, zone):
            out = []
            for v in self.fleet.values():
                if v["status"] != "AVAILABLE" or self._seats_left(v) <= 0:
                    continue
                if zone_gate and v["zone_name"] != zone:
                    continue
                if is_pickup:
                    rank = haversine_km(home, v["current_location"])
                else:
                    ok, dh = self._can_serve_dropoff(v, office_departure)
                    if not ok:
                        continue
                    rank = dh
                out.append((0 if v["plate_no"] in rostered else 1, rank,
                            0 if v["zone_name"] == zone else 1, v))
            out.sort(key=lambda t: t[:3])
            return [t[3] for t in out]

        still, touched = [], set()
        for d in left:
            home = ((d["pickup_lat"], d["pickup_lng"]) if is_pickup
                    else (d["drop_lat"], d["drop_lng"]))
            zone = self._request_zone(d) if is_pickup else d.get("zone_name")
            for v in candidates(home, zone):
                got = self._second_chance_stop(d, home, v, is_pickup, shift_time,
                                               is_0730, route_by_car)
                if got is None:
                    continue
                key, item = got
                stops = dict(v["_stops"])
                if key in stops:
                    stops[key] = dict(stops[key],
                                      passengers=list(stops[key]["passengers"]) + [d])
                else:
                    stops[key] = item
                fits = (self._route_fits(v, stops, shift_time) if is_pickup
                        else self._dropoff_fits(v, stops, office_departure))
                if fits:
                    v["_stops"] = stops
                    touched.add(v["plate_no"])
                    break
            else:
                still.append(d)
        return still, touched

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

        # Every rider this event has left behind so far, with the reason they
        # were left. NOT reported yet: the second chance below gets them all
        # first, and only the ones it cannot place are reported.
        left = [(pr, "no_vehicle_available", None) for pr in unassigned]

        if shift_time == "22:00:00":
            left += [(pr, "dropped_for_120min_cap", None)
                     for pr in self._redistribute_case_a(vehicles, shift_time)]

        # The cap and the free window, applied BEFORE a single route is written
        # — so the riders they shed are still free agents when the second
        # chance runs. Keyed by plate.
        enforced = {}
        for v in vehicles:
            if not v["_stops"]:
                continue
            result = self._enforce_cap_pickup(v, shift_time)
            enforced[v["plate_no"]] = result
            left += [(pr, result[3], v["plate_no"]) for pr in result[2]]

        still, touched = self._second_chance(event, shift_time,
                                             [pr for pr, _, _ in left])
        reason_of = {pr["employee_email"]: (r, pl) for pr, r, pl in left}
        for pr in still:
            reason, plate = reason_of[pr["employee_email"]]
            self.out.unassigned.append(
                self._unassigned_row(pr, "pickup", shift_time, reason, plate)
            )

        # Every car carrying someone now — the first pass's, plus any car the
        # second chance filled from empty. `recording` comes from `touched`, not
        # from "every car with stops": a car with stops is not necessarily a car
        # working THIS event.
        seen = {v["plate_no"] for v in vehicles}
        recording = [v for v in vehicles if v["_stops"]]
        recording += [self.fleet[p] for p in sorted(touched) if p not in seen]

        for v in recording:
            if v["plate_no"] in touched or v["plate_no"] not in enforced:
                ordered, timing, dropped, drop_reason = self._enforce_cap_pickup(v, shift_time)
                for pr in dropped:
                    self.out.unassigned.append(
                        self._unassigned_row(pr, "pickup", shift_time, drop_reason, v["plate_no"])
                    )
            else:
                ordered, timing = enforced[v["plate_no"]][:2]

            if not ordered or timing is None:
                continue

            # Record pickup->vehicle reuse only for passengers who survived the
            # 120-min cap, so drop-off never reuses a car that never carried them.
            for _key, item in ordered:
                for pr in item["passengers"]:
                    self.pickup_vehicle_by_employee[pr["employee_email"]] = v["plate_no"]

            full = [v["current_location"]] + [it["coord"] for _, it in ordered] + [self.office]
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
                "passenger_total_minutes": round(timing["passenger_total_minutes"], 1),
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
        drop_time = event["time"]
        # ML model prediction is time-of-day dependent: anchor every leg in
        # this event to the requests' own scheduled drop-off time (event["time"]).
        self.provider.query_time = self._parse_time(drop_time)
        # the car leaves the office at drop_time, not at shift end -- the
        # 15/30-min gap is the employees' wait for the car
        office_departure = self._parse_time(drop_time)
        self._update_fleet(office_departure)
        vehicles, unassigned = self._assign_dropoff_event(event)

        left = [(d, "no_vehicle_available", None) for d in unassigned]
        enforced = {}
        for v in vehicles:
            if not v["_stops"]:
                continue
            result = self._enforce_cap_dropoff(v, office_departure)
            enforced[v["plate_no"]] = result
            left += [(d, result[3], v["plate_no"]) for d in result[2]]

        still, touched = self._second_chance(event, shift_end_time,
                                             [d for d, _, _ in left])
        reason_of = {d["employee_email"]: (r, pl) for d, r, pl in left}
        for d in still:
            reason, plate = reason_of[d["employee_email"]]
            self.out.unassigned.append(
                self._unassigned_row(d, "dropoff", shift_end_time, reason, plate)
            )

        seen = {v["plate_no"] for v in vehicles}
        recording = [v for v in vehicles if v["_stops"]]
        recording += [self.fleet[p] for p in sorted(touched) if p not in seen]

        for v in recording:
            start_loc = v["current_location"]      # where the deadhead to the office begins
            if v["plate_no"] in touched or v["plate_no"] not in enforced:
                ordered, timing, dropped, drop_reason = self._enforce_cap_dropoff(v, office_departure)
                for d in dropped:
                    self.out.unassigned.append(
                        self._unassigned_row(d, "dropoff", shift_end_time, drop_reason, v["plate_no"])
                    )
            else:
                ordered, timing = enforced[v["plate_no"]][:2]

            if not ordered or timing is None:
                continue

            full = [start_loc, self.office] + [it["coord"] for _, it in ordered]
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
                "start_lat": start_loc[0],
                "start_lng": start_loc[1],
                "end_lat": timing["end_location"][0],
                "end_lng": timing["end_location"][1],
                "office_departure": iso(timing["office_departure"]),
                "parking_arrival": iso(timing["tour_end"]),
                "trip_start": iso(timing["trip_start"]),
                "tour_end": iso(timing["tour_end"]),
                "deadhead_minutes": round(timing["deadhead_minutes"], 1),
                "return_deadhead_minutes": round(timing["return_deadhead_minutes"], 1),
                "total_minutes": round(timing["total_minutes"], 1),
                "passenger_total_minutes": round(timing["passenger_total_minutes"], 1),
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

            # fleet state: the tour ENDS AT THE LAST STOP. The car is left out
            # on the road; its next pick-up starts from there.
            v["status"] = "IN_TRIP"
            v["current_location"] = timing["end_location"]
            v["_trip_end_time"] = timing["tour_end"]
            v["_trip_end_location"] = timing["end_location"]
            v["_free_at"] = timing["tour_end"]
            v["_used"] = 0


def solve_night(
    *,
    service_date: str,
    vehicles: Sequence[Dict[str, Any]],
    pickup_requests: Sequence[Dict[str, Any]],
    dropoff_requests: Sequence[Dict[str, Any]],
    fixed_stops: Sequence[Dict[str, Any]],
    provider: DistanceProvider,
    foot: Optional[FootDistanceProvider] = None,
    cfg: Optional[SolverConfig] = None,
    employee_names: Optional[Dict[str, str]] = None,
    use_ml: bool = True,
) -> SolvedNight:
    """Solve one whole service night.

    Whole-night is not a convenience: fleet state (where each car is, when it
    is next free) carries across every event, so pickups and drop-offs cannot
    be solved independently without leaving the fleet's end-of-night position
    undefined.

    Both the weekly pass and the nightly ad-hoc re-route go through here: the
    ad-hoc pass is the same whole-night solve re-run after 7 PM with the day's
    ad-hoc rows folded in (newest-wins per employee), so a change to this
    function upgrades both entry points.
    """
    return NightSolver(
        service_date=service_date,
        vehicles=vehicles,
        pickup_requests=pickup_requests,
        dropoff_requests=dropoff_requests,
        fixed_stops=fixed_stops,
        provider=provider,
        foot=foot,
        cfg=cfg,
        employee_names=employee_names,
        use_ml=use_ml,
    ).solve()