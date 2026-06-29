from fastapi import APIRouter, Depends
from app.database import supabase
from app.models.vehicle import VehicleCreate, VehicleUpdate, VehicleResponse
from app.services.vehicle_service import VehicleService
from app.dependencies import require_admin, TokenData
from typing import List

router = APIRouter(prefix="/vehicles", tags=["Admin — Vehicles"])

def _svc() -> VehicleService:
    return VehicleService(supabase)

@router.get("/", response_model=List[VehicleResponse])
def list_vehicles(
    _: TokenData = Depends(require_admin),
    svc: VehicleService = Depends(_svc)
):
    return svc.get_all()

@router.get("/{vehicle_id}", response_model=VehicleResponse)
def get_vehicle(
    vehicle_id: int,
    _: TokenData = Depends(require_admin),
    svc: VehicleService = Depends(_svc)
):
    return svc.get_by_id(vehicle_id)

@router.post("/", response_model=VehicleResponse, status_code=201)
def create_vehicle(
    body: VehicleCreate,
    _: TokenData = Depends(require_admin),
    svc: VehicleService = Depends(_svc)
):
    return svc.create(body)

@router.put("/{vehicle_id}", response_model=VehicleResponse)
def update_vehicle(
    vehicle_id: int,
    body: VehicleUpdate,
    _: TokenData = Depends(require_admin),
    svc: VehicleService = Depends(_svc)
):
    return svc.update(vehicle_id, body)

@router.delete("/{vehicle_id}")
def delete_vehicle(
    vehicle_id: int,
    _: TokenData = Depends(require_admin),
    svc: VehicleService = Depends(_svc)
):
    return svc.delete(vehicle_id)