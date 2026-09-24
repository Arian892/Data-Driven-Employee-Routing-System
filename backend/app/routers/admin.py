from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import supabase
from app.dependencies import require_admin, TokenData
from app.models.common import paginate
from app.models.employee import (
    EmployeeCreate,
    EmployeeProfileResponse,
    MessageResponse,
    ResetPasswordRequest,
)
from app.models.route import (
    DropoffRoutingRunPayload,
    PickupRoutingInputResponse,
    PickupRoutingRunPayload,
    RouteAssignPayload,
    RouteAssignmentResponse,
    RouteDetailResponse,
    RoutingRunResponse,
    RunDayRoutingPayload,
    ScheduleSummaryResponse,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
    ZonesListResponse,
)
from app.scheduler import run_pending_routing
from app.services.employee_service import EmployeeService
from app.services.route_service import RouteService
from app.services.routing_service import DuplicateSolveError, RoutingService
from app.services.zone_service import ZoneService

router = APIRouter()


def _emp_svc() -> EmployeeService:
    return EmployeeService(supabase)


def _routing_svc() -> RoutingService:
    return RoutingService(supabase)


def _route_svc() -> RouteService:
    return RouteService(supabase)


def _zone_svc() -> ZoneService:
    return ZoneService(supabase)


@router.get("/admin/pickup-routing/input", response_model=PickupRoutingInputResponse)
def pickup_routing_input(
    service_date: str = Query(..., description="Service date in YYYY-MM-DD format"),
    shift_start_time: str | None = Query(None, description="Optional shift start time in HH:MM"),
    _: TokenData = Depends(require_admin),
    svc: RoutingService = Depends(_routing_svc),
):
    return svc.get_pickup_routing_input(service_date, shift_start_time)


@router.post("/admin/pickup-routing/run", response_model=RoutingRunResponse)
def run_pickup_routing(
    payload: PickupRoutingRunPayload,
    _: TokenData = Depends(require_admin),
    svc: RoutingService = Depends(_routing_svc),
):
    try:
        return svc.run_pickup_routing(payload)
    except DuplicateSolveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/admin/dropoff-routing/run", response_model=RoutingRunResponse)
def run_dropoff_routing(
    payload: DropoffRoutingRunPayload,
    _: TokenData = Depends(require_admin),
    svc: RoutingService = Depends(_routing_svc),
):
    try:
        return svc.run_dropoff_routing(payload)
    except DuplicateSolveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/admin/routing/auto-run")
def auto_run_routing(
    _: TokenData = Depends(require_admin),
):
    """Force-run the auto-routing pass for the current request cycle.

    Normally this runs on its own after Saturday 11:59 PM; this endpoint is for
    testing/troubleshooting. Safe to call repeatedly (idempotent).
    """
    return run_pending_routing(force=True)


@router.post("/admin/routing/run-day")
def run_day_routing(
    payload: RunDayRoutingPayload,
    _: TokenData = Depends(require_admin),
    svc: RoutingService = Depends(_routing_svc),
):
    """Force re-route one service date (pickup + dropoff together) on demand.

    Deletes and rewrites that date's routes from scratch, bypassing the
    "just solved this recently" dedup guard by default — the scheduler never
    calls this, so a fresh admin click for that date should always go through.
    """
    try:
        return svc.run_service_date(payload.service_date, force=payload.force)
    except DuplicateSolveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/admin/employees", response_model=EmployeeProfileResponse, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    _: TokenData = Depends(require_admin),
    svc: EmployeeService = Depends(_emp_svc),
):
    """Admin creates an employee account. The temporary password is handed to
    the employee offline — there is no public signup."""
    return svc.create_employee(payload)


@router.post("/admin/employees/{user_id}/reset-password", response_model=MessageResponse)
def reset_employee_password(
    user_id: int,
    payload: ResetPasswordRequest,
    _: TokenData = Depends(require_admin),
    svc: EmployeeService = Depends(_emp_svc),
):
    """Admin overwrites an employee's password with a new temporary value.
    The existing password is never read or returned."""
    return svc.reset_password(user_id, payload.new_password)


@router.delete("/admin/employees/{user_id}", response_model=MessageResponse)
def deactivate_employee(
    user_id: int,
    _: TokenData = Depends(require_admin),
    svc: EmployeeService = Depends(_emp_svc),
):
    """Soft-delete: the employee keeps their history but can no longer log in."""
    return svc.deactivate(user_id)


@router.get("/admin/schedule-summary", response_model=ScheduleSummaryResponse)
def schedule_summary(
    service_date: str = Query(..., description="Service date in YYYY-MM-DD format"),
    shift_time: str | None = Query(None, description="Optional shift time"),
    _: TokenData = Depends(require_admin),
    svc: RouteService = Depends(_route_svc),
):
    return svc.get_schedule_summary(service_date, shift_time)


@router.get("/admin/routes/{route_id}", response_model=RouteDetailResponse)
def get_route_detail(
    route_id: int,
    _: TokenData = Depends(require_admin),
    svc: RouteService = Depends(_route_svc),
):
    return svc.get_route_by_id(route_id)


@router.post("/admin/routes/{route_id}/assign", response_model=RouteAssignmentResponse)
def assign_route(
    route_id: int,
    payload: RouteAssignPayload,
    _: TokenData = Depends(require_admin),
    svc: RouteService = Depends(_route_svc),
):
    return svc.assign_route(route_id, payload)


@router.get("/zones", response_model=ZonesListResponse)
def list_zones(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=500),
    _: TokenData = Depends(require_admin),
    svc: ZoneService = Depends(_zone_svc),
):
    zones = svc.get_all()
    zones_page, pagination = paginate(zones, page, limit)
    return {"zones": zones_page, "pagination": pagination}


@router.post("/zones", response_model=ZoneResponse, status_code=201)
def create_zone(
    payload: ZoneCreate,
    _: TokenData = Depends(require_admin),
    svc: ZoneService = Depends(_zone_svc),
):
    return svc.create(payload)


@router.get("/zones/{zone_id}", response_model=ZoneResponse)
def get_zone(
    zone_id: int,
    _: TokenData = Depends(require_admin),
    svc: ZoneService = Depends(_zone_svc),
):
    return svc.get_by_id(zone_id)


@router.put("/zones/{zone_id}", response_model=ZoneResponse)
def update_zone(
    zone_id: int,
    payload: ZoneUpdate,
    _: TokenData = Depends(require_admin),
    svc: ZoneService = Depends(_zone_svc),
):
    return svc.update(zone_id, payload)


@router.delete("/zones/{zone_id}")
def delete_zone(
    zone_id: int,
    _: TokenData = Depends(require_admin),
    svc: ZoneService = Depends(_zone_svc),
):
    return svc.delete(zone_id)
