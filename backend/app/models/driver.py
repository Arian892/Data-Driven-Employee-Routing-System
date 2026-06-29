from pydantic import BaseModel
from typing import Optional

class DriverCreate(BaseModel):
    # User fields
    name: str
    email: str
    phone: Optional[str] = None
    password: str           # stored as-is in password_hash column
    # Driver fields
    license_no: str
    status: Optional[str] = "Available"

class DriverUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    license_no: Optional[str] = None
    status: Optional[str] = None

class DriverResponse(BaseModel):
    driver_id: int
    user_id: int
    name: str
    email: str
    phone: Optional[str]
    license_no: str
    status: str
    user_status: Optional[str]