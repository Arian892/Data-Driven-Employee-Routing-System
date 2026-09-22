"""Distance/duration providers for the routing solver.

Two interchangeable implementations behind one protocol:

- `OsrmProvider` — real road network via a local OSRM server. Accurate, and the
  only way to get drawable road geometry.
- `HaversineProvider` — straight-line fallback. No dependencies, no server, so
  the solver still runs anywhere (e.g. Render, CI, a laptop with no OSRM).

`get_provider()` picks one from settings and degrades to haversine whenever OSRM
is unreachable, so a solve always completes.

The caches are not an optimisation, they are a requirement: `enforce_cap_*`
re-orders a route once per candidate stop per shed iteration, and each of those
re-requests the same matrix. Without caching a full-night solve does not finish.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Callable, Iterable, Protocol, Sequence

import httpx

from app.config import settings as app_settings

logger = logging.getLogger("uvicorn.error")

# A solve commits to one engine for its entire batch (150+ routes on a full
# night), so a single unlucky probe — a backend that just restarted and hasn't
# warmed up its connection, OSRM momentarily busy, a network blip — must not
# be allowed to silently degrade the whole day to straight-line geometry.
# Retrying a few times, with a short pause, absorbs exactly that kind of
# transient without meaningfully slowing down the (already minutes-long) solve.
_HEALTH_CHECK_ATTEMPTS = 3
_HEALTH_CHECK_RETRY_DELAY_SECONDS = 1.5


def _probe_with_retries(label: str, base_url: str, healthy: Callable[[], bool]) -> bool:
    for attempt in range(1, _HEALTH_CHECK_ATTEMPTS + 1):
        if healthy():
            if attempt > 1:
                logger.info("routing: %s at %s answered on attempt %d/%d", label, base_url, attempt, _HEALTH_CHECK_ATTEMPTS)
            return True
        if attempt < _HEALTH_CHECK_ATTEMPTS:
            logger.warning(
                "routing: %s at %s did not answer (attempt %d/%d), retrying in %.1fs",
                label, base_url, attempt, _HEALTH_CHECK_ATTEMPTS, _HEALTH_CHECK_RETRY_DELAY_SECONDS,
            )
            time.sleep(_HEALTH_CHECK_RETRY_DELAY_SECONDS)
    return False

Coord = tuple[float, float]          # (lat, lng)
Matrix = list[list[float]]

# Cache keys round coordinates to ~1 m so float noise doesn't cause misses.
_COORD_PRECISION = 5


def _cache_key(coords: Sequence[Coord]) -> tuple:
    return tuple((round(lat, _COORD_PRECISION), round(lng, _COORD_PRECISION)) for lat, lng in coords)


def haversine_km(a: Coord, b: Coord) -> float:
    """Great-circle distance in km. Ported verbatim from the notebook."""
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def walk_minutes(a: Coord, b: Coord, walk_speed_kmph: float) -> float:
    """Straight-line walking time between two points (fallback only).

    `routing_night.py` reads Case A walking times from the OSRM foot network
    (real pedestrian graph, no assumed speed). This function is the haversine
    stand-in used when that foot engine is unreachable, so a solve still
    completes. Kept as a free function so `HaversineWalkProvider` can reuse it.
    """
    return haversine_km(a, b) / walk_speed_kmph * 60.0


# ──────────────────────────────────────────────────────────────────────────────
# Foot provider: real pedestrian-network walking times (OSRM foot profile).
# `routing_night.py` Case A asks "can this rider walk to this fixed stop within
# WALK_LIMIT_MIN" against the foot graph, batching one /table call per employee
# home vs. the candidate stops. `prefetch` fills the cache in chunks; `walk_minutes`
# then reads it back pair-by-pair. Cache keys round to ~1 m like the driving side.
# ──────────────────────────────────────────────────────────────────────────────


class FootDistanceProvider(Protocol):
    """Walking times between coordinates on the pedestrian network.

    A solve is still allowed to fall back to straight-line estimates when no
    foot engine answers, so the solver only ever sees this narrow surface.
    """

    name: str

    def walk_minutes(self, a: Coord, b: Coord) -> float:
        """Minutes to walk a -> b. float("inf") when the pair is unroutable."""
        ...

    def prefetch(self, home: Coord, stops: Iterable[Coord]) -> None:
        """Warm the walk cache for `home` vs. every stop (batched)."""
        ...


def _walk_key(a: Coord, b: Coord) -> tuple:
    return (round(a[0], 6), round(a[1], 6), round(b[0], 6), round(b[1], 6))


class HaversineWalkProvider:
    """Straight-line fallback for the foot engine (no server, no assumptions)."""

    name = "haversine_walk"

    def __init__(self, walk_speed_kmph: float | None = None):
        self.walk_speed_kmph = walk_speed_kmph or app_settings.routing_walk_speed_kmph

    def walk_minutes(self, a: Coord, b: Coord) -> float:
        return walk_minutes(a, b, self.walk_speed_kmph)

    def prefetch(self, home: Coord, stops: Iterable[Coord]) -> None:
        return None


class FootOsrmProvider:
    """Pedestrian-network walking times via a local OSRM foot server.

    Mirrors `routing_night.py`'s `_foot_table`/`prefetch_walk`/`walk_minutes`:
    batched `/table/v1/foot/` calls (OSRM caps a table at ~100 coordinates),
    chunked against the stop set, with every pair cached for the life of the
    instance. A null matrix entry (unroutable on foot) becomes inf.
    """

    name = "osrm_foot"

    # OSRM caps a /table call at 100 coordinates, so prefetch chunks stops as
    # home + up to 90 per call -- the same budget routing_night.py uses.
    _PREFETCH_CHUNK = 90

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or app_settings.osrm_foot_base_url).rstrip("/")
        self.timeout = timeout or app_settings.osrm_timeout_seconds
        self._client = httpx.Client(timeout=self.timeout)
        self._cache: dict[tuple, float] = {}

    @staticmethod
    def _coord_str(coords: Sequence[Coord]) -> str:
        return ";".join(f"{lng},{lat}" for lat, lng in coords)

    def _table(self, coords: Sequence[Coord]) -> Matrix:
        """One OSRM foot /table call -> durations in minutes (inf = unroutable)."""
        response = self._client.get(
            f"{self.base_url}/table/v1/foot/{self._coord_str(coords)}",
            params={"annotations": "duration"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok":
            raise RuntimeError(f"OSRM foot error: {payload.get('code')} {payload.get('message', '')}")
        return [
            [value / 60.0 if value is not None else float("inf") for value in row]
            for row in payload["durations"]
        ]

    def prefetch(self, home: Coord, stop_coords: Iterable[Coord]) -> None:
        """Warm the walk cache: one batched foot call per chunk of stops.

        Case A walks every employee home against every candidate stop, so asking
        pair-by-pair would be tens of thousands of HTTP calls. Chunking keeps it
        one call per ~90 stops; the minutes land in the same cache `walk_minutes`
        reads back.
        """
        stops = list(stop_coords)
        for i in range(0, len(stops), self._PREFETCH_CHUNK):
            chunk = stops[i:i + self._PREFETCH_CHUNK]
            durations = self._table([home] + chunk)
            for j, c in enumerate(chunk):
                self._cache[_walk_key(home, c)] = durations[0][j + 1]

    def walk_minutes(self, a: Coord, b: Coord) -> float:
        key = _walk_key(a, b)
        got = self._cache.get(key)
        if got is not None:
            return got
        mins = self._table([a, b])[0][1]
        self._cache[key] = mins
        return mins

    def healthy(self) -> bool:
        """Cheap probe against a known-good coordinate pair."""
        probe = httpx.Client(timeout=app_settings.osrm_probe_timeout_seconds)
        try:
            response = probe.get(
                f"{self.base_url}/table/v1/foot/"
                f"{self._coord_str([(23.7702, 90.4085), (23.7702, 90.4095)])}",
                params={"annotations": "duration"},
            )
            return response.status_code == 200 and response.json().get("code") == "Ok"
        except Exception:
            return False
        finally:
            probe.close()

    def close(self) -> None:
        self._client.close()


def get_foot_provider(prefer_osrm: bool | None = None) -> FootDistanceProvider:
    """Best available foot provider. Never raises -- a solve must always complete."""
    if prefer_osrm is None:
        prefer_osrm = app_settings.prefers_osrm

    if not prefer_osrm:
        return HaversineWalkProvider()

    candidate = FootOsrmProvider()
    if _probe_with_retries("OSRM foot", candidate.base_url, candidate.healthy):
        logger.info("routing: using OSRM foot at %s", candidate.base_url)
        return candidate

    candidate.close()
    logger.warning(
        "routing: OSRM foot unreachable at %s after %d attempts, falling back to "
        "straight-line walking times (Case A walk limits will be approximate)",
        candidate.base_url, _HEALTH_CHECK_ATTEMPTS,
    )
    return HaversineWalkProvider()


class DistanceProvider(Protocol):
    """Driving distances/durations between coordinates.

    Note the return order, which mirrors the notebook: `table` yields durations
    first, `route` yields distance first. `route`'s duration is deliberately
    unused by the solver — route timing is summed from `table` legs plus
    boarding buffers, while total distance comes from `route`. Swapping either
    source changes the emitted schedule.
    """

    name: str

    def table(self, coords: Sequence[Coord]) -> tuple[Matrix, Matrix]:
        """Full square matrix: (durations_min, distances_km)."""
        ...

    def route(self, coords: Sequence[Coord]) -> tuple[float, float, list[Coord]]:
        """(distance_km, duration_min, geometry as [lat, lng] points)."""
        ...


class HaversineProvider:
    """Straight-line fallback.

    Geometry is just the waypoints joined up, so a map drawn from it shows
    direct lines rather than roads — honest about being an approximation.
    """

    name = "haversine"

    def __init__(self, average_speed_kmph: float | None = None):
        self.average_speed_kmph = average_speed_kmph or app_settings.routing_average_speed_kmph

    def _minutes(self, km: float) -> float:
        return km / self.average_speed_kmph * 60.0

    def table(self, coords: Sequence[Coord]) -> tuple[Matrix, Matrix]:
        distances = [[haversine_km(a, b) for b in coords] for a in coords]
        durations = [[self._minutes(km) for km in row] for row in distances]
        return durations, distances

    def route(self, coords: Sequence[Coord]) -> tuple[float, float, list[Coord]]:
        km = sum(haversine_km(a, b) for a, b in zip(coords, coords[1:]))
        return km, self._minutes(km), [tuple(c) for c in coords]


class OsrmProvider:
    """Road-network distances via a local OSRM server (car profile).

    Caches every matrix and route response for the lifetime of the instance —
    one instance per solve, so the cache is naturally scoped to a service date.
    """

    name = "osrm"

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or app_settings.osrm_base_url).rstrip("/")
        self.timeout = timeout or app_settings.osrm_timeout_seconds
        self._client = httpx.Client(timeout=self.timeout)
        self._table_cache: dict[tuple, tuple[Matrix, Matrix]] = {}
        self._route_cache: dict[tuple, tuple[float, float, list[Coord]]] = {}

    # OSRM speaks lng,lat — every conversion goes through here so the flip is
    # impossible to forget at a call site.
    @staticmethod
    def _coord_str(coords: Sequence[Coord]) -> str:
        return ";".join(f"{lng},{lat}" for lat, lng in coords)

    def _get(self, path: str, params: dict) -> dict:
        response = self._client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok":
            raise RuntimeError(f"OSRM error at {path}: {payload.get('code')} {payload.get('message', '')}")
        return payload

    def table(self, coords: Sequence[Coord]) -> tuple[Matrix, Matrix]:
        key = _cache_key(coords)
        if key in self._table_cache:
            return self._table_cache[key]
        payload = self._get(
            f"/table/v1/driving/{self._coord_str(coords)}",
            {"annotations": "duration,distance"},
        )
        durations = [[value / 60.0 for value in row] for row in payload["durations"]]
        distances = [[value / 1000.0 for value in row] for row in payload["distances"]]
        self._table_cache[key] = (durations, distances)
        return durations, distances

    def route(self, coords: Sequence[Coord]) -> tuple[float, float, list[Coord]]:
        key = _cache_key(coords)
        if key in self._route_cache:
            return self._route_cache[key]
        payload = self._get(
            f"/route/v1/driving/{self._coord_str(coords)}",
            {"overview": "full", "geometries": "geojson"},
        )
        leg = payload["routes"][0]
        out = (
            leg["distance"] / 1000.0,
            leg["duration"] / 60.0,
            [(lat, lng) for lng, lat in leg["geometry"]["coordinates"]],
        )
        self._route_cache[key] = out
        return out

    def healthy(self) -> bool:
        """Cheap probe against a known-good coordinate pair."""
        probe = httpx.Client(timeout=app_settings.osrm_probe_timeout_seconds)
        try:
            response = probe.get(
                f"{self.base_url}/route/v1/driving/{self._coord_str([(23.77, 90.40), (23.78, 90.39)])}",
                params={"overview": "false"},
            )
            return response.status_code == 200 and response.json().get("code") == "Ok"
        except Exception:
            return False
        finally:
            probe.close()

    def close(self) -> None:
        self._client.close()


def get_provider(prefer_osrm: bool | None = None) -> DistanceProvider:
    """Best available provider. Never raises — a solve must always complete."""
    if prefer_osrm is None:
        prefer_osrm = app_settings.prefers_osrm

    if not prefer_osrm:
        logger.info("routing: using haversine engine (ROUTING_ENGINE=%s)", app_settings.routing_engine)
        return HaversineProvider()

    candidate = OsrmProvider()
    if _probe_with_retries("OSRM", candidate.base_url, candidate.healthy):
        logger.info("routing: using OSRM at %s", candidate.base_url)
        return candidate

    candidate.close()
    logger.warning(
        "routing: OSRM unreachable at %s after %d attempts, falling back to haversine "
        "(distances and geometry will be approximate)",
        candidate.base_url, _HEALTH_CHECK_ATTEMPTS,
    )
    return HaversineProvider()
