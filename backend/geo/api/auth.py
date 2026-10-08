from fastapi import APIRouter, Depends, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt
from jwt import PyJWTError as JWTError
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr
from typing import Optional
from geo.models.user import User, UserCreate, UserLogin, Token
from geo.services.user_service import user_service
from geo.services.email_service import email_service
from geo.utils.error_handler import AppException
from geo.database import settings

router = APIRouter()


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

# Security configuration — key comes from .env (settings.SECRET_KEY).
# Never hard-code a fallback here: any committed placeholder becomes a
# known signing key for anyone with the source (the repo is public),
# letting them forge a JWT for any account.
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
# 7 days. Short TTLs (30m) silently convert every long-idle tab into a
# "zombie login" — UI still shows the email (read from localStorage.user)
# while every authed API call 401s. Proper refresh-token rotation is a
# later phase; 7d is a reasonable stop-gap for a SaaS dashboard.
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
RESET_TOKEN_EXPIRE_MINUTES = 15

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_reset_token(data: dict):
    expires_delta = timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    return create_access_token(data, expires_delta)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise AppException(status_code=401, message="Could not validate credentials")
    except JWTError:
        raise AppException(status_code=401, message="Could not validate credentials")
    user = user_service.get_user_by_username(username)
    if user is None:
        raise AppException(status_code=401, message="Could not validate credentials")
    return user


def _user_role(user) -> str:
    """role is the single source of truth for permissions; legacy data without role falls back to is_admin"""
    role = getattr(user, "role", None)
    if role:
        return role
    return "admin" if getattr(user, "is_admin", False) else "user"


def is_staff(user) -> bool:
    """Workbench staff (admin | operator); all cross-user read checks go through here"""
    return _user_role(user) in ("admin", "operator")


def require_roles(*roles):
    """Role-check dependency factory: Depends(require_roles("admin", "operator"))"""
    async def _dep(current_user = Depends(get_current_user)):
        if _user_role(current_user) not in roles:
            raise AppException(status_code=403, message="权限不足")
        return current_user
    return _dep


async def require_admin(current_user = Depends(get_current_user)):
    """Reject non-admin users with 403. Wrap any admin-only endpoint with this."""
    if _user_role(current_user) != "admin":
        raise AppException(status_code=403, message="Admin only")
    return current_user


# Workbench level (admin | operator): project progress / profile / copy review / audit reports / batch-run results
require_workbench = require_roles("admin", "operator")

# Maintenance roles (admin | maintainer): platform rules / AI crawler analysis / dispatch center / external gateway
require_maintainer = require_roles("admin", "maintainer")

# Monitoring data (engine measurements): available to workbench staff + maintenance roles
require_workbench_or_maintainer = require_roles("admin", "operator", "maintainer")

def _bearer_user(authorization: Optional[str]):
    """Best-effort resolve the user from the Authorization header; return None if it cannot be resolved (no raise).

    /register is now both the anonymous signup entry (off by default) and the workbench create-account-for-customer entry, the latter carrying
    an admin token. Depends(get_current_user) would make anonymous requests 401 immediately, never getting
    the correct message "registration is closed", so we parse manually here.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1].strip(),
                             SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    email = payload.get("sub")
    return user_service.get_user_by_email(email) if email else None


@router.post("/register", response_model=User)
async def register(user: UserCreate, authorization: Optional[str] = Header(default=None)):
    """Register a new user.

    Email registration is off by default (settings.EMAIL_REGISTER_ENABLED=False): public signup goes through
    WeChat QR + phone number. **Email login is unaffected**; existing accounts work as usual. Workbench admins creating accounts for customers
    still use this endpoint, allowed through by the admin token.
    """
    if not settings.EMAIL_REGISTER_ENABLED:
        actor = _bearer_user(authorization)
        if not (actor and _user_role(actor) == "admin"):
            raise AppException(
                status_code=403,
                message="邮箱注册已关闭,请使用微信扫码登录",
            )

    # Check if user already exists
    existing_user = user_service.get_user_by_email(user.email)
    if existing_user:
        raise AppException(status_code=400, message="Email already registered")
    
    # Limit password length to 72 bytes for bcrypt
    user_data = user.model_dump()
    user_data["password"] = user_data["password"][:72]
    user_with_truncated_password = UserCreate(**user_data)
    
    # Create user (password is hashed in user_service)
    new_user = user_service.create_user(user_with_truncated_password)
    return new_user

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token"""
    # Find user by username (email)
    user = user_service.get_user_by_email(form_data.username)
    if not user:
        raise AppException(status_code=401, message="Incorrect email or password")
    
    # Verify password
    if not user_service.verify_password(form_data.password, user.password_hash):
        raise AppException(status_code=401, message="Incorrect email or password")
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user

@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    """Send password reset email"""
    email = body.email
    # Emails of WeChat-registered accounts are placeholder addresses (see user_service.WECHAT_EMAIL_DOMAIN);
    # that subdomain does not receive mail, so sending would vanish — tell the user to scan the QR code instead of waiting.
    if email.lower().endswith("@" + user_service.WECHAT_EMAIL_DOMAIN):
        raise AppException(status_code=400, message="该账号请使用微信扫码登录")
    user = user_service.get_user_by_email(email)
    if not user:
        raise AppException(status_code=404, message="Email not found")

    reset_token = create_reset_token({"sub": user.email, "type": "reset"})

    email_sent = email_service.send_password_reset_email(email, reset_token)
    if not email_sent:
        raise AppException(status_code=500, message="Failed to send password reset email")

    return {"message": "Password reset email sent"}

@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    """Reset password"""
    token = body.token
    new_password = body.new_password
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    email: str = payload.get("sub")
    token_type: str = payload.get("type")
    
    if email is None or token_type != "reset":
        raise AppException(status_code=400, message="Invalid reset token")
    
    # Find user by email
    user = user_service.get_user_by_email(email)
    if not user:
        raise AppException(status_code=404, message="User not found")
    
    # Create a UserCreate object to update the password
    user_update = UserCreate(
        email=user.email,
        name=user.name,
        password=new_password
    )
    
    # Update user password
    updated_user = user_service.update_user(user.id, user_update)
    if not updated_user:
        raise AppException(status_code=404, message="User not found")
    
    return {"message": "Password reset successfully"}