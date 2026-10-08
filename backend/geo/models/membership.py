from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from geo.database import Base

# SQLAlchemy ORM Models
class MembershipORM(Base):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    # ISO 4217 lowercased — drives the currency passed to Stripe Checkout.
    # 'cny' for domestic SaaS tiers, 'usd' for overseas service tiers.
    currency = Column(String(3), nullable=False, default="usd", server_default="usd")
    period = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    popular = Column(Boolean, default=False)
    features = Column(String, nullable=False)          # JSON string (feature list for display)
    not_included = Column(String, nullable=False)      # JSON string
    tier_type = Column(String, nullable=False, default="saas")          # 'saas' | 'service'
    monthly_check_quota = Column(Integer, nullable=False, default=3)    # 0 = unlimited
    allowed_check_categories = Column(Text, nullable=True)              # JSON array, NULL = all 23 items
    features_json = Column(Text, nullable=True)                         # Structured JSON of 18 managed-service entitlements (for the service tier)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserMembershipORM(Base):
    __tablename__ = "user_memberships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    membership_id = Column(Integer, ForeignKey("memberships.id"), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)

class UserCheckUsageORM(Base):
    __tablename__ = "user_check_usage"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    year_month = Column(String, primary_key=True)  # '2026-04'
    used_count = Column(Integer, nullable=False, default=0)

class UserCheckUsageDailyORM(Base):
    """**Daily** usage of basic audits. A second gate alongside the monthly quota.

    Why a daily cap: audits run on an egress-limited engine pool (25 deepseek egresses x 10 per egress
    per day = 250 per day platform-wide); the monthly quota cannot stop "burning a month's quota in one day" and exhausting capacity.
    All three tiers are 10/day, so growth's "unlimited monthly" is actually <= 300/month.

    Bucketed by **Beijing date** (same metric definition as balance_ledger.bj_date), computed at write time so the query side
    does not have to deal with different SQLite / PG timezone functions.
    """

    __tablename__ = "user_check_usage_daily"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    bj_date = Column(String(10), primary_key=True)   # '2026-08-25'
    used_count = Column(Integer, nullable=False, default=0)


class AnonymousCheckUsageORM(Base):
    __tablename__ = "anonymous_check_usage"

    # Opaque per-browser identifier set by the backend as an HTTP cookie
    # (`geo_anon_id`). UUID4 fits in 36 chars; widen for safety. Composite PK
    # with year_month so monthly buckets reset naturally.
    client_id = Column(String(64), primary_key=True)
    year_month = Column(String, primary_key=True)  # '2026-04'
    used_count = Column(Integer, nullable=False, default=0)

class SalesLeadORM(Base):
    __tablename__ = "sales_leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    website = Column(String, nullable=True)
    tier_slug = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ContactSubmissionORM(Base):
    __tablename__ = "contact_submissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    website = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class MembershipBase(BaseModel):
    name: str
    price: float
    currency: str = "usd"
    period: str
    description: str
    popular: bool = False

class MembershipCreate(MembershipBase):
    slug: str
    features: List[str]
    not_included: List[str]
    tier_type: str = "saas"
    monthly_check_quota: int = 3
    allowed_check_categories: Optional[List[str]] = None
    features_json: Optional[Dict[str, Any]] = None
    display_order: int = 0

class Membership(MembershipBase):
    # Whether this tier is currently "granted on login". Follows membership_service.LOGIN_GRANTED_TIER,
    # not a DB column -- if the grant is turned off (constant set back to "free"), the frontend "limited-time free"
    # label disappears automatically without touching the page or the DB.
    login_granted: bool = False
    id: int
    slug: str
    features: List[str]
    not_included: List[str]
    tier_type: str = "saas"
    monthly_check_quota: int = 3
    allowed_check_categories: Optional[List[str]] = None
    features_json: Optional[Dict[str, Any]] = None
    display_order: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ContactSalesRequest(BaseModel):
    name: str
    email: str
    website: Optional[str] = None
    tier_slug: Optional[str] = None
    message: Optional[str] = None

class UsageResponse(BaseModel):
    quota: int        # 0 = unlimited
    used: int
    remaining: int    # -1 = unlimited
    year_month: str
    # Daily cap: same for all three tiers; it is a capacity guardrail, not a tier entitlement, so growth's "unlimited" is bound by it too
    daily_quota: int = 0
    daily_used: int = 0
    daily_remaining: int = 0
    bj_date: str = ""

class UserMembership(BaseModel):
    id: int
    user_id: int
    membership_id: int
    start_date: datetime
    end_date: datetime
    is_active: bool = True
    # Tier slug -- used by the frontend for "tier >= pro". With only membership_id the frontend would have to look up
    # the tier table to know its own tier, which is fragile. Filled by the API layer; not a column on the ORM.
    slug: Optional[str] = None

    class Config:
        from_attributes = True

class UserMembershipCreate(BaseModel):
    user_id: int
    membership_id: int
    start_date: datetime
    end_date: datetime

class MembershipUpgrade(BaseModel):
    user_id: int
    new_membership_id: int