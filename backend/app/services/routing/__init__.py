"""Routing engine: the real solver behind the admin "run routing" actions.

    from app.services.routing import solve_night, get_provider

Layering is deliberate and worth keeping:

- `config`   — policy constants, no logic
- `solver`   — PURE algorithm; no DB, no HTTP, no clock. Offline-testable, which
               is what makes `test_solver_parity.py` possible.
- `distance` — the only module that talks to the network (OSRM, with a
               haversine fallback)
- `adapter`  — DB rows in
- `writer`   — DB rows out

`routing_service.py` is the thin orchestrator over these.
"""
from app.services.routing.adapter import RoutingContext
from app.services.routing.config import SolverConfig
from app.services.routing.distance import (
    FootOsrmProvider,
    HaversineProvider,
    HaversineWalkProvider,
    OsrmProvider,
    get_foot_provider,
    get_provider,
)
from app.services.routing.solver import SolvedNight, solve_night

__all__ = [
    "solve_night",
    "SolvedNight",
    "SolverConfig",
    "RoutingContext",
    "get_provider",
    "get_foot_provider",
    "OsrmProvider",
    "HaversineProvider",
    "FootOsrmProvider",
    "HaversineWalkProvider",
]
