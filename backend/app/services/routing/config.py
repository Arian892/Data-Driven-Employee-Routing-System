"""Solver constants.

Lifted verbatim from `data/routing_night.py` (the standalone twin of
`data/testing.ipynb`, which implements `data/system_data/Algo_refined.md`) so
the ported solver reproduces that script's output. Changing any value here
changes the schedule, so treat them as policy, not tuning knobs.
"""
from dataclasses import dataclass

from app.services.week_service import OFFICE_LOCATION

# The office is the origin for every pickup route's end and every drop-off
# route's start. Single source of truth lives in week_service.
OFFICE = (OFFICE_LOCATION["lat"], OFFICE_LOCATION["lng"])

# BDS: employees walk to a designated pick-up point only if the foot-network
# walk is within this many minutes; past it the 10 PM car comes to the door.
WALK_LIMIT_MIN = 20

# Straight-line walk speed, used ONLY when the OSRM foot engine is unavailable.
WALK_SPEED_KMPH = 4.5

# BDS: a vehicle waits at most 5 minutes per stop for boarding/alighting.
BOARDING_BUFFER_MIN = 5

# Hard cap on a single route's on-road (passenger-journey) time.
MAX_ROUTE_MINUTES = 120

# A pickup must reach the office at least this early before the shift starts.
OFFICE_BUFFER_MIN = 5

# How much a drop-off ORDER cares about where the night ENDS. A drop-off tour
# is open -- the car drops its last rider and stops -- so the leg home is
# normally never driven, and pricing it at full cost makes the search buy a
# cheap return with real driven distance. 0.0 is the true shortest driven
# route. Raise it only if the 06:15 -> 07:30 chain starts stranding riders.
DROPOFF_RETURN_WEIGHT = 0.0

# Case D (07:30 drop-off): the shared main-road drop point (Agargaon Metro).
AGARGAON_METRO = (23.775518, 90.388407)

# Every timestamp is anchored to 22:00 on the service date, so the overnight
# timeline stays monotonic across midnight.
NIGHT_ANCHOR_HOUR = 22

# The drop-off event that triggers Case D (main-road consolidation).
MAIN_ROAD_DROP_TIME = "07:30:00"

# Stop ordering is an EXACT search (Held-Karp DP), exact over the objective
# (total passenger ride time for pickups, total route time for drop-offs). That
# is only affordable because a trip can never carry more stops than the vehicle
# has seats -- the fleet's largest capacity is 11, so n never grows large.
MAX_STOPS_FOR_EXACT = 16


@dataclass(frozen=True)
class SolverConfig:
    """Per-run knobs. Defaults reproduce `routing_night.py` exactly."""

    office: tuple[float, float] = OFFICE
    walk_speed_kmph: float = WALK_SPEED_KMPH
    walk_limit_min: float = WALK_LIMIT_MIN
    boarding_buffer_min: int = BOARDING_BUFFER_MIN
    max_route_minutes: float = MAX_ROUTE_MINUTES
    office_buffer_min: int = OFFICE_BUFFER_MIN
    dropoff_return_weight: float = DROPOFF_RETURN_WEIGHT
    agargaon_metro: tuple[float, float] = AGARGAON_METRO
    night_anchor_hour: int = NIGHT_ANCHOR_HOUR

    # BDS says the Agargaon Metro consolidation does not apply on Fridays. The
    # notebook omits this check; the backend knows the real service date, so it
    # can honour the rule. Set False to reproduce the notebook byte-for-byte on
    # a Friday service date.
    apply_friday_exception: bool = True