from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str
    user_id: int

class TokenData(BaseModel):
    user_id: Optional[int] = None
    role: Optional[str] = None
    name: Optional[str] = None