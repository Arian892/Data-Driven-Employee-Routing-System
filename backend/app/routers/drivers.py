from fastapi import APIRouter, Depends
from app.database import supabase
from app.models.driver import DriverCreate, DriverUpdate, DriverResponse
from app.services.driver_service import DriverService
from app.dependencies import require_admin, TokenData
from typing import List

router = APIRouter(prefix="/drivers", tags=["Admin — Drivers"])

def _svc() -> DriverService:
    return DriverService(supabase)

@router.get("/", response_model=List[DriverResponse])
def list_drivers(
    _: TokenData = Depends(require_admin),
    svc: DriverService = Depends(_svc)
):
    return svc.get_all()

@router.get("/{driver_id}", response_model=DriverResponse)
def get_driver(
    driver_id: int,
    _: TokenData = Depends(require_admin),
    svc: DriverService = Depends(_svc)
):
    return svc.get_by_id(driver_id)

@router.post("/", response_model=DriverResponse, status_code=201)
def create_driver(
    body: DriverCreate,
    _: TokenData = Depends(require_admin),
    svc: DriverService = Depends(_svc)
):
    return svc.create(body)

@router.put("/{driver_id}", response_model=DriverResponse)
def update_driver(
    driver_id: int,
    body: DriverUpdate,
    _: TokenData = Depends(require_admin),
    svc: DriverService = Depends(_svc)
):
    return svc.update(driver_id, body)

@router.delete("/{driver_id}")
def delete_driver(
    driver_id: int,
    _: TokenData = Depends(require_admin),
    svc: DriverService = Depends(_svc)
):
    return svc.delete(driver_id)