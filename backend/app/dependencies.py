from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.config import settings
from app.models.auth import TokenData

bearer_scheme = HTTPBearer()

def _decode_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> TokenData:
    """Decodes JWT and returns token payload. Raises 401 if invalid."""
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        user_id: int = payload.get("user_id")
        role: str = payload.get("role")
        name: str = payload.get("name")

        if user_id is None or role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )
        return TokenData(user_id=user_id, role=role, name=name)

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(token_data: TokenData = Depends(_decode_token)) -> TokenData:
    """Any authenticated user."""
    return token_data

def require_admin(token_data: TokenData = Depends(_decode_token)) -> TokenData:
    """Only Admin role passes through. All admin endpoints use this."""
    if token_data.role != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return token_data