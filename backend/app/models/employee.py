from typing import List, Optional

from pydantic import BaseModel
from app.models.common import Pagination


class EmployeeProfileResponse(BaseModel):
    user_id: int
    employee_id: int
    name: str
    email: str
    phone: Optional[str] = None
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    role: str
    status: str
    is_active: bool


class EmployeesListResponse(BaseModel):
    employees: list[EmployeeProfileResponse]
    pagination: Pagination


class EmployeeProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None


class EmployeeCreate(BaseModel):
    """Admin-created employee account. The admin picks the initial (temporary)
    password and hands the credentials to the employee offline."""

    name: str
    email: str
    phone: Optional[str] = None
    password: str


class ChangePasswordRequest(BaseModel):
    """Employee changes their own password — must know the current one."""

    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    """Admin overwrites an employee's password with a new temporary value.
    The existing password is never read or returned."""

    new_password: str


class MessageResponse(BaseModel):
    message: str


class StopInfo(BaseModel):
    stop_id: int
    sequence_order: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    # Named by the solver, so the employee sees "Mazar Road Bus Stop" rather than
    # a pair of coordinates. `is_shared` marks the Agargaon Metro consolidation
    # point, which is why several colleagues alight together there.
    stop_name: Optional[str] = None
    is_adhoc: Optional[bool] = None
    is_shared: Optional[bool] = None


class RouteStopInfo(StopInfo):
    """One stop on the full route, for drawing every stop on the employee's
    map (as opposed to `StopInfo`, which is only this employee's own stop)."""

    is_mine: bool = False


class DriverInfo(BaseModel):
    driver_id: int
    name: Optional[str] = None
    phone: Optional[str] = None


class VehicleInfo(BaseModel):
    vehicle_id: int
    plate_no: Optional[str] = None
    capacity: Optional[int] = None


class ScheduleLeg(BaseModel):
    """One half of an employee's night: the ride in, or the ride home."""
    route_id: int
    route_type: str  # "pickup" | "dropoff"
    shift_time: Optional[str] = None
    route_geometry: Optional[List[List[float]]] = None
    stop: StopInfo
    # Every stop on this route, in visiting order — every colleague sharing the
    # vehicle, not just this employee's own stop. Powers the sequential 1..N
    # map markers; the office isn't included (the solver never stores it as a
    # stop row — see routing/solver.py's "ends at the LAST STOP" note).
    stops: List[RouteStopInfo] = []
    driver: Optional[DriverInfo] = None
    vehicle: Optional[VehicleInfo] = None


class ScheduleResponse(BaseModel):
    service_date: str
    # An employee normally has TWO routes on a service date — the pickup that
    # brings them to the office and the dropoff that takes them home. Both carry
    # the same service_date, because Case C reuses the pickup's vehicle for the
    # dropoff and so needs them on one night.
    pickup: Optional[ScheduleLeg] = None
    dropoff: Optional[ScheduleLeg] = None
    # The flat fields mirror `pickup` (or `dropoff`, on a dropoff-only day) so
    # consumers written against the earlier single-leg shape keep working.
    route_id: Optional[int] = None
    route_type: Optional[str] = None
    shift_time: Optional[str] = None
    route_geometry: Optional[List[List[float]]] = None
    stop: Optional[StopInfo] = None
    driver: Optional[DriverInfo] = None
    vehicle: Optional[VehicleInfo] = None
    routing_done: bool  # True only if routing has been run and stop is assigned
