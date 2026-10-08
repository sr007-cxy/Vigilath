from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from sqlalchemy import CheckConstraint, Column, Integer, String, Boolean, DateTime, false
from geo.database import Base

# Role set: role is the single source of truth for permissions; is_admin is only a compatibility column kept in sync
# (is_admin = (role == "admin"); synced at write points in user_service).
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_USER = "user"
ROLE_TENANT = "tenant"
ROLE_MAINTAINER = "maintainer"
ROLE_AGENCY_ADMIN = "agency_admin"
ROLE_AGENCY_OPERATOR = "agency_operator"

ALLOWED_ROLES = (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_USER,
    ROLE_TENANT,
    ROLE_MAINTAINER,
    ROLE_AGENCY_ADMIN,
    ROLE_AGENCY_OPERATOR,
)

# Agency roles are not plain account attributes; they must be maintained in the same transaction as an active organization membership.
# The generic user API only allows the standalone roles below; the organization domain service uses AGENCY_ROLES directly.
AGENCY_ROLES = (ROLE_AGENCY_ADMIN, ROLE_AGENCY_OPERATOR)
DIRECT_ASSIGNABLE_ROLES = (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_USER,
    ROLE_TENANT,
    ROLE_MAINTAINER,
)

# SQLAlchemy ORM Model
class UserORM(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'operator', 'user', 'tenant', 'maintainer', "
            "'agency_admin', 'agency_operator')",
            name="ck_users_role_allowed",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # Kept for legacy rows, but registration no longer collects a name —
    # user_service.create_user falls back to the email when absent.
    name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    role = Column(String, nullable=False, default="user", server_default="user", index=True)
    # WeChat QR login identity. openid is unique "within this official account"; unionid only exists when bound to
    # the open platform -- so look users up by unionid first, falling back to openid.
    wechat_openid = Column(String(length=64), unique=True, index=True, nullable=True)
    wechat_unionid = Column(String(length=64), index=True, nullable=True)
    wechat_nickname = Column(String(length=128), nullable=True)
    wechat_avatar = Column(String(length=512), nullable=True)
    # Whether the user still follows the official account. Unfollow events set False, but **do not disable the account** -- unfollowing should not
    # lock people out; they just need to follow again on the next QR login.
    wechat_subscribed = Column(Boolean, default=False, nullable=False, server_default=false())
    # Phone number (11 digits, E.164 without +86). Required for WeChat-registered accounts; it carries real account primary-key semantics.
    phone = Column(String(length=32), unique=True, index=True, nullable=True)
    phone_verified_at = Column(DateTime, nullable=True)
    # Registration time. Column added only on 2026-08-26 -- older accounts are NULL and not backfilled (see migration
    # a2d7f5c81e94: no evidence in the DB to infer registration time). The display side renders NULL as "—".
    created_at = Column(DateTime, nullable=True, default=datetime.utcnow)

# Pydantic Models
class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None

    # Copy-paste often brings leading/trailing spaces, which EmailStr rejects outright -> clean before validation
    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return v.strip() if isinstance(v, str) else v

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return v.strip() if isinstance(v, str) else v

class User(UserBase):
    id: int
    is_active: bool = True
    is_admin: bool = False
    role: str = "user"
    # Emails of WeChat-registered accounts are placeholders; the frontend uses these two fields to show the real identity
    phone: Optional[str] = None
    wechat_nickname: Optional[str] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
