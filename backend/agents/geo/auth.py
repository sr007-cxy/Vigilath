"""Independent JWT validation for the Agent service — reuses the main backend's SECRET_KEY + user_service,
but **does not import geo.api** (avoids pulling in the whole route layer written for the old FastAPI).

Same source as get_current_user in geo/api/auth.py: decode JWT (sub=username) -> fetch the user via user_service.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError

from geo.database import settings
from geo.services.user_service import user_service

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    user = user_service.get_user_by_username(username)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
