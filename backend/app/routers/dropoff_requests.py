from fastapi import APIRouter, Depends, Query
from app.database import supabase
from app.models.request import DropoffRequestResponse
from app.services.request_service import RequestService
from app.dependencies import require_admin, TokenData
from typing import List, Optional

router = APIRouter(prefix="/dropoff-requests", tags=["Admin — Dropoff Requests"])

def _svc() -> RequestService:
    return RequestService(supabase)

@router.get("/", response_model=List[DropoffRequestResponse])
def list_dropoffs(
    status: Optional[str] = Query(None, description="Filter by status: Pending/Approved/Rejected"),
    service_date: Optional[str] = Query(None, description="Filter by date e.g. 2025-01-15"),
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.get_all_dropoffs(status=status, service_date=service_date)

@router.get("/{dropoff_id}", response_model=DropoffRequestResponse)
def get_dropoff(
    dropoff_id: int,
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.get_dropoff_by_id(dropoff_id)

@router.post("/{dropoff_id}/approve")
def approve_dropoff(
    dropoff_id: int,
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.approve_dropoff(dropoff_id)

@router.post("/{dropoff_id}/reject")
def reject_dropoff(
    dropoff_id: int,
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.reject_dropoff(dropoff_id)