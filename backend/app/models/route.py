from datetime import date
from typing import Any, List, Optional

from pydantic import BaseModel

from app.models.common import Pagination


class ZoneCreate(BaseModel):
    zone_name: str
    description: Optional[str] = None


class ZoneUpdate(BaseModel):
    zone_name: Optional[str] = None
    description: Optional[str] = None


class ZoneResponse(BaseModel):
    zone_id: int
    zone_name: str
    description: Optional[str] = None


class ZonesListResponse(BaseModel):
    zones: List[ZoneResponse]
    pagination: Pagination


class PickupRoutingZoneCount(BaseModel):
    zone_id: Optional[int]
    zone_name: Optional[str]
    total_requests: int
    pending: int
    approved: int
    rejected: int


class PickupRoutingInputResponse(BaseModel):
    service_date: str
    shift_start_time: Optional[str] = None
    total_requests: int
    pending: int
    approved: int
    rejected: int
    zones: List[PickupRoutingZoneCount]


class PickupRoutingRunPayload(BaseModel):
    service_date: str
    shift_start_time: Optional[str] = None
    # Optional overrides. They default to week_service.OFFICE_LOCATION, which is
    # the single source of truth for where the office is.
    office_lat: Optional[float] = None
    office_lng: Optional[float] = None
    office_buffer_minutes: Optional[int] = 10
    stop_dwell_minutes: Optional[int] = 5
    # Bypasses the "just solved this date in the last 2 minutes" dedup guard —
    # for a deliberate manual re-run right after fixing routing-affecting data
    # (an employee's coordinates, a vehicle, office location, etc.). Never set
    # by the scheduler; only an admin explicitly asking for it should skip the
    # guard, since that guard is what stops accidental duplicate/retried runs.
    force: bool = False
    # Applies only to the haversine fallback; ignored when OSRM answers, because
    # OSRM's durations come from the road network.
    average_speed_kmph: Optional[float] = 40.0


class DropoffRoutingRunPayload(BaseModel):
    service_date: str
    shift_end_time: Optional[str] = None
    office_lat: Optional[float] = None
    office_lng: Optional[float] = None
    office_buffer_minutes: Optional[int] = 10
    stop_dwell_minutes: Optional[int] = 5
    average_speed_kmph: Optional[float] = 40.0
    # See PickupRoutingRunPayload.force.
    force: bool = False


class RunDayRoutingPayload(BaseModel):
    """Force re-route one service date (pickup + dropoff together) on demand.

    Deletes and rewrites that date's routes from scratch — the same idempotent
    replacement `run_service_date` always does — but always bypasses the
    "just solved this recently" dedup guard, since a deliberate per-day admin
    click is exactly the case that guard is meant to let through.
    """

    service_date: str
    force: bool = True


class UnassignedEntry(BaseModel):
    """One request the solver could not place, and why.

    The reason is the actionable half. `no_coordinates` is a data-quality problem
    for whoever maintains the roster; `no_vehicle_available` means the fleet is
    too small for that shift; `dropped_for_120min_cap` and
    `vehicle_not_free_in_time` are schedule pressure. Collapsing all four into a
    bare count of ids — as the old response did — threw that away.
    """

    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    employee_email: Optional[str] = None
    request_type: str
    shift_time: Optional[str] = None
    reason: str
    vehicle_id: Optional[int] = None
    plate_no: Optional[str] = None


class RoutingRunResponse(BaseModel):
    routes_created: int
    employees_assigned: int
    # Kept so existing callers and the frontend keep working unchanged.
    unassigned_pickup_ids: List[int] = []
    unassigned: List[UnassignedEntry] = []
    # "osrm" or "haversine" — which distance engine actually ran. Worth
    # surfacing: a silent fallback is why distances would look approximate.
    engine: Optional[str] = None
    # Data-quality problems that are NOT unassigned requests, e.g. a vehicle with
    # no parking coordinates, or requests with a NULL zone.
    warnings: List[str] = []
    message: Optional[str] = None


class RouteStopResponse(BaseModel):
    stop_id: int
    route_id: int
    latitude: float
    longitude: float
    sequence_order: int
    arrival_time: Optional[str]
    departure_time: Optional[str]
    # "Mazar Road Bus Stop", "Agargaon Metro Station (shared drop point)",
    # "Home (Rafiq Ahmed)" — until now the UI could only show a bare marker.
    stop_name: Optional[str] = None
    is_adhoc: Optional[bool] = None
    is_shared: Optional[bool] = None


class RouteAssignmentResponse(BaseModel):
    assignment_id: int
    route_id: int
    vehicle_id: int
    driver_id: Optional[int] = None
    departure_time: Optional[str]
    arrival_time: Optional[str]
    status: str


class RouteResponse(BaseModel):
    route_id: int
    zone_id: Optional[int]
    zone_name: Optional[str] = None
    route_type: str
    service_date: str
    shift_time: Optional[str] = None
    total_distance_km: Optional[float] = None
    total_travel_time_min: Optional[int] = None
    created_at: Optional[str] = None
    # The real driving path as [lat, lng] pairs, so the map draws roads instead
    # of straight lines between markers. Null when the haversine fallback ran.
    route_geometry: Optional[List[List[float]]] = None
    # Solver-assigned label, e.g. "P22:00:00::VDhaka-Metro-Cha-10-3269".
    route_code: Optional[str] = None


class ScheduleRouteStop(RouteStopResponse):
    passengers: List[dict[str, Any]] = []


class RouteDetailResponse(RouteResponse):
    stops: List[ScheduleRouteStop] = []
    assignment: Optional[RouteAssignmentResponse] = None


class ScheduleSummaryResponse(BaseModel):
    routes: List[RouteDetailResponse]


class RouteAssignPayload(BaseModel):
    vehicle_id: int
    driver_id: int
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    status: Optional[str] = "Scheduled"
