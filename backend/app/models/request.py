from pydantic import BaseModel
from typing import Optional
from datetime import date, time

class PickupRequestResponse(BaseModel):
    pickup_id: int
    employee_id: int
    employee_name: Optional[str]
    zone_id: Optional[int]
    zone_name: Optional[str]
    pickup_lat: Optional[float]
    pickup_lng: Optional[float]
    shift_start_time: Optional[str]
    service_date: date
    request_type: Optional[str]
    status: str
    pickup_time: Optional[str]
    created_at: Optional[str]

class DropoffRequestResponse(BaseModel):
    dropoff_id: int
    employee_id: int
    employee_name: Optional[str]
    zone_id: Optional[int]
    zone_name: Optional[str]
    drop_lat: Optional[float]
    drop_lng: Optional[float]
    shift_end_time: Optional[str]
    service_date: date
    status: str
    drop_time: Optional[str]
    created_at: Optional[str]

class ApprovalRequest(BaseModel):
    reason: Optional[str] = None    # optional rejection reason