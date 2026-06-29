from fastapi import APIRouter, Depends
from app.database import supabase
from app.services.request_service import RequestService
from app.dependencies import require_admin, TokenData

router = APIRouter(prefix="/pickup-requests", tags=["Admin — Pickup Requests"])

def _svc() -> RequestService:
    return RequestService(supabase)

@router.post("/{pickup_id}/approve")
def approve_pickup(
    pickup_id: int,
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.approve_pickup(pickup_id)

@router.post("/{pickup_id}/reject")
def reject_pickup(
    pickup_id: int,
    _: TokenData = Depends(require_admin),
    svc: RequestService = Depends(_svc)
):
    return svc.reject_pickup(pickup_id)