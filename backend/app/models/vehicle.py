from pydantic import BaseModel
from typing import Optional

class VehicleCreate(BaseModel):
    plate_no: str
    capacity: int
    parking_lat: Optional[float] = None
    parking_lng: Optional[float] = None
    status: Optional[str] = "Active"
    driver_id: Optional[int] = None     # assign driver on creation

class VehicleUpdate(BaseModel):
    plate_no: Optional[str] = None
    capacity: Optional[int] = None
    parking_lat: Optional[float] = None
    parking_lng: Optional[float] = None
    status: Optional[str] = None
    driver_id: Optional[int] = None     # reassign driver

class VehicleResponse(BaseModel):
    vehicle_id: int
    plate_no: str
    capacity: int
    parking_lat: Optional[float]
    parking_lng: Optional[float]
    status: str
    driver_id: Optional[int]
    driver_name: Optional[str] = None   # joined from users
    license_no: Optional[str] = None    # joined from driver