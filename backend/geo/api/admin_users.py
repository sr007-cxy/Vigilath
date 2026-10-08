"""User role management (admin-only).

Endpoints (prefix /admin/users):
- GET   /admin/users                      - user list (with bound gateway tenant + current membership tier)
- POST  /admin/users                      - create a user (random password if none given; plaintext echoed only once)
- PATCH /admin/users/{id}/role            - change the standalone role; agency roles are maintained via the organization member API
- PATCH /admin/users/{id}/membership      - set the membership tier (free tier = cancel paid membership)
- POST  /admin/users/{id}/reset-password  - reset password (random if no new password given; plaintext echoed only once)
- GET   /admin/users/{id}                 - user details (profile + membership + balance + payment/audit summary)
- GET   /admin/users/{id}/payments        - this user's payment records (paginated)
- GET   /admin/users/{id}/detections      - this user's audit records (paginated, including deleted)

The single source of truth for bindings is api_tenants.user_id (a telemetry-service table; the two services share a DB,
and only read-only raw SQL queries are done here; all writes go through the /admin/gateway reverse proxy).
"""
import logging
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from geo.api.auth import require_admin
from geo.api.ai_telemetry import get_db
from geo.models.balance import UserBalanceORM
from geo.models.detection import DetectionRecordORM
from geo.models.membership import MembershipORM, UserMembershipORM
from geo.models.payment import PaymentSessionORM
from geo.models.user import (
    AGENCY_ROLES,
    ALLOWED_ROLES,
    DIRECT_ASSIGNABLE_ROLES,
    User,
    UserCreate,
    UserORM,
)
from geo.services.membership_service import membership_service
from geo.services.user_service import user_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users")


class AdminUserItem(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool
    tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    membership_id: Optional[int] = None          # No paid record = None (free tier)
    membership_name: Optional[str] = None
    membership_end_date: Optional[datetime] = None


class CreateUserPayload(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    role: str = "user"
    password: Optional[str] = None   # Omitted -> generated randomly by the server

    # Copy-paste often brings leading/trailing spaces, which EmailStr rejects outright -> clean before validating (same as the sign-up entry)
    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return v.strip() if isinstance(v, str) else v


class CreateUserResult(BaseModel):
    user: AdminUserItem
    initial_password: str   # Plaintext echoed only once in this response


class UpdateRolePayload(BaseModel):
    role: str


class UpdateMembershipPayload(BaseModel):
    membership_id: int


class ResetPasswordPayload(BaseModel):
    new_password: Optional[str] = None   # Omitted -> generated randomly by the server


def _bound_tenants(db: Session, user_ids: list[int]) -> dict[int, tuple[int, str]]:
    """user_id -> (tenant_id, tenant_name). Degrades to empty when the api_tenants table is missing (tolerates split DBs)."""
    if not user_ids:
        return {}
    try:
        rows = db.execute(
            text("SELECT id, name, user_id FROM api_tenants WHERE user_id IS NOT NULL"),
        ).fetchall()
    except Exception:
        log.warning("query api_tenants failed; tenant binding info degraded", exc_info=True)
        return {}
    wanted = set(user_ids)
    return {r[2]: (r[0], r[1]) for r in rows if r[2] in wanted}


def _active_memberships(
    db: Session, user_ids: list[int],
) -> dict[int, tuple[int, str, datetime]]:
    """user_id -> (membership_id, name, end_date), only unexpired paid records."""
    if not user_ids:
        return {}
    rows = (
        db.query(UserMembershipORM, MembershipORM.name)
        .join(MembershipORM, MembershipORM.id == UserMembershipORM.membership_id)
        .filter(
            UserMembershipORM.user_id.in_(user_ids),
            UserMembershipORM.is_active == True,  # noqa: E712
            UserMembershipORM.end_date > datetime.utcnow(),
        )
        .all()
    )
    return {um.user_id: (um.membership_id, name, um.end_date) for um, name in rows}


def _to_item(db: Session, u: UserORM) -> AdminUserItem:
    t = _bound_tenants(db, [u.id]).get(u.id)
    m = _active_memberships(db, [u.id]).get(u.id)
    return AdminUserItem(
        id=u.id,
        email=u.email,
        name=u.name,
        role=getattr(u, "role", None) or ("admin" if u.is_admin else "user"),
        is_active=bool(u.is_active),
        tenant_id=t[0] if t else None,
        tenant_name=t[1] if t else None,
        membership_id=m[0] if m else None,
        membership_name=m[1] if m else None,
        membership_end_date=m[2] if m else None,
    )


def _resolve_password(raw: Optional[str]) -> str:
    """Validate the password entered by the admin; if empty, generate a random 12-character password."""
    pwd = (raw or "").strip()
    if not pwd:
        return secrets.token_urlsafe(9)   # 12-character random password
    if len(pwd) < 8:
        raise HTTPException(422, "密码至少 8 位")
    if len(pwd.encode("utf-8")) > 72:
        raise HTTPException(422, "密码过长(bcrypt 上限 72 字节)")
    return pwd


@router.get("", response_model=list[AdminUserItem])
def list_users(
    search: Optional[str] = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(UserORM)
    if search and search.strip():
        like = f"%{search.strip()}%"
        q = q.filter((UserORM.email.ilike(like)) | (UserORM.name.ilike(like)))
    users = q.order_by(UserORM.id.desc()).all()
    ids = [u.id for u in users]
    bound = _bound_tenants(db, ids)
    members = _active_memberships(db, ids)
    out: list[AdminUserItem] = []
    for u in users:
        t = bound.get(u.id)
        m = members.get(u.id)
        out.append(AdminUserItem(
            id=u.id,
            email=u.email,
            name=u.name,
            role=getattr(u, "role", None) or ("admin" if u.is_admin else "user"),
            is_active=bool(u.is_active),
            tenant_id=t[0] if t else None,
            tenant_name=t[1] if t else None,
            membership_id=m[0] if m else None,
            membership_name=m[1] if m else None,
            membership_end_date=m[2] if m else None,
        ))
    return out


@router.post("", response_model=CreateUserResult, status_code=201)
def create_user(
    payload: CreateUserPayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin opens an account directly.

    - the role is specified by the admin (default regular user), not the sign-up flow's fixed role="user"
    - if the password is empty it is generated randomly; plaintext is echoed only once in this response, never persisted or logged
    - tenant binding / membership tier are not set here; change them in the list after creation (the binding source of truth is api_tenants)
    """
    role = (payload.role or "user").strip()
    if role not in ALLOWED_ROLES:
        raise HTTPException(422, "无效的角色")
    if role not in DIRECT_ASSIGNABLE_ROLES:
        raise HTTPException(409, "代理角色必须通过组织成员管理设置")

    email = str(payload.email)
    if db.query(UserORM).filter(UserORM.email == email).first():
        raise HTTPException(400, "该邮箱已注册")

    pwd = _resolve_password(payload.password)
    try:
        created = user_service.create_user(
            UserCreate(email=email, name=payload.name, password=pwd), role=role,
        )
    except IntegrityError:   # Concurrent creation with the same email; the users.email unique constraint is the fallback
        log.warning("[admin-users] account creation hit the unique constraint email=%s", email, exc_info=True)
        raise HTTPException(400, "该邮箱已注册")

    log.info("[admin-users] admin=%s created user=%s (role=%s)", admin.id, created.id, role)
    # user_service writes via a separate session; end the current transaction of this request's session
    # so the read-back below sees the just-committed row (especially necessary under SQLite snapshot isolation)
    db.commit()
    target = db.get(UserORM, created.id)
    if not target:
        raise HTTPException(500, "创建成功但回读失败,请刷新列表")
    return CreateUserResult(user=_to_item(db, target), initial_password=pwd)


@router.patch("/{user_id}/role", response_model=AdminUserItem)
def update_role(
    user_id: int,
    payload: UpdateRolePayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    role = (payload.role or "").strip()
    if role not in ALLOWED_ROLES:
        raise HTTPException(422, "无效的角色")
    if role not in DIRECT_ASSIGNABLE_ROLES:
        raise HTTPException(409, "代理角色必须通过组织成员管理设置")

    target = db.get(UserORM, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")

    current_role = getattr(target, "role", None) or ("admin" if target.is_admin else "user")
    if current_role in AGENCY_ROLES:
        raise HTTPException(409, "代理角色必须通过组织成员管理修改或移除")
    if current_role == "admin" and role != "admin":
        # Safeguard 1: you cannot demote yourself out of admin (avoid locking yourself out of the last entry point)
        if target.id == admin.id:
            raise HTTPException(400, "不能取消自己的管理员权限")
        # Safeguard 2: the last admin cannot be demoted
        admin_count = db.query(UserORM).filter(
            (UserORM.role == "admin") | (UserORM.is_admin == True)  # noqa: E712
        ).count()
        if admin_count <= 1:
            raise HTTPException(400, "至少保留一名管理员")

    target.role = role
    target.is_admin = (role == "admin")
    db.commit()
    db.refresh(target)
    return _to_item(db, target)


@router.patch("/{user_id}/membership", response_model=AdminUserItem)
def update_membership(
    user_id: int,
    payload: UpdateMembershipPayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set the account's membership tier.

    - set the free tier (slug=free) -> cancel the current paid membership, fall back to free
    - set a paid tier -> go through upgrade_membership: validity generated by the tier period (30 days for monthly),
      setting the same tier repeatedly extends from the current expiry date
    """
    target = db.get(UserORM, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")

    membership = membership_service.get_membership_by_id(payload.membership_id)
    if not membership:
        raise HTTPException(404, "会员等级不存在")

    if membership.slug == "free":
        membership_service.cancel_membership(user_id)
    else:
        granted = membership_service.upgrade_membership(user_id, payload.membership_id)
        if not granted:
            raise HTTPException(500, "会员设置失败")

    log.info(
        "[admin-users] admin=%s set the membership of user=%s to %s (id=%s)",
        admin.id, target.id, membership.slug, membership.id,
    )
    # membership_service writes via a separate session; end the current transaction of this request's session
    # so the read-back below sees the just-committed membership record (especially necessary under SQLite snapshot isolation)
    db.commit()
    return _to_item(db, target)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: ResetPasswordPayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reset the user's password. If new_password is omitted it is generated randomly; plaintext is echoed only once in this response,
    handed to the user by the admin, never persisted or logged."""
    import bcrypt

    target = db.get(UserORM, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")

    pwd = _resolve_password(payload.new_password)
    target.password_hash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.commit()
    log.info("[admin-users] admin=%s reset the password of user=%s", admin.id, target.id)
    return {"id": target.id, "email": target.email, "new_password": pwd}


# ── User details: profile / membership / balance / payment and audit summaries ────────────────────────
# The list endpoint returns everything at once and is not suited to stuffing these heavy queries into each row; details get a separate endpoint,
# fetched only when the drawer opens, so the list page cost is unchanged.

class UserBalanceOut(BaseModel):
    balance_cents: int = 0
    granted_cents: int = 0
    recharged_cents: int = 0
    spent_cents: int = 0
    # False when the balance table cannot be read (split DB / old DB without the migration): the frontend must show "temporarily unavailable"
    # rather than treating it as "balance 0"; these mean completely different things for customer support.
    available: bool = True


class AdminUserDetail(AdminUserItem):
    phone: Optional[str] = None
    phone_verified_at: Optional[datetime] = None
    wechat_nickname: Optional[str] = None
    wechat_avatar: Optional[str] = None
    wechat_bound: bool = False
    wechat_subscribed: bool = False
    created_at: Optional[datetime] = None        # None for legacy accounts, see migration a2d7f5c81e94
    membership_start_date: Optional[datetime] = None
    balance: UserBalanceOut = UserBalanceOut()
    detection_count: int = 0
    detection_last_at: Optional[datetime] = None
    payment_count: int = 0                       # All orders (including unpaid)
    payment_paid_count: int = 0
    # Currency -> cumulative paid amount (in cents). **Not summed together**: the DB stores usd / cny mixed,
    # and adding them gives a unitless number.
    payment_paid_cents: dict[str, int] = {}
    payment_last_paid_at: Optional[datetime] = None


class AdminPaymentItem(BaseModel):
    id: int
    purpose: str                       # membership | topup
    membership_slug: Optional[str] = None
    membership_name: Optional[str] = None
    amount_cents: int
    currency: str
    status: str                        # pending | paid | expired | canceled
    provider: str                      # stripe | wechat | moltspay
    tx_hash: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AdminPaymentList(BaseModel):
    items: list[AdminPaymentItem]
    total: int
    page: int
    size: int


class AdminDetectionItem(BaseModel):
    id: int
    url: str
    score: Optional[int] = None
    grade: Optional[str] = None
    tier: Optional[str] = None
    mode: str                          # free | advanced
    created_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None   # Records deleted by the user themselves are still visible in the admin backend


class AdminDetectionList(BaseModel):
    items: list[AdminDetectionItem]
    total: int
    page: int
    size: int


def _page_args(page: int, size: int) -> tuple[int, int]:
    return max(1, int(page)), max(1, min(int(size), 100))


def _balance_of(db: Session, user_id: int) -> UserBalanceOut:
    """Read-only balance. **Does not go through balance_service.get_or_create_account**: that lazily grants
    the sign-up bonus, and an admin opening someone's details should not give them money out of thin air."""
    try:
        acc = db.get(UserBalanceORM, user_id)
    except Exception:
        log.warning("query user_balances failed; balance info degraded", exc_info=True)
        return UserBalanceOut(available=False)
    if acc is None:
        return UserBalanceOut()      # Account not opened yet = everything is 0, not "unreadable"
    return UserBalanceOut(
        balance_cents=int(acc.balance_cents or 0),
        granted_cents=int(acc.granted_cents or 0),
        recharged_cents=int(acc.recharged_cents or 0),
        spent_cents=int(acc.spent_cents or 0),
    )


@router.get("/{user_id}", response_model=AdminUserDetail)
def get_user_detail(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The full picture of an account: basic profile + current membership + balance + payment/audit summaries.

    Paginated details go through /payments and /detections separately; only summary numbers are returned here, so opening the drawer shows everything.
    """
    u = db.get(UserORM, user_id)
    if not u:
        raise HTTPException(404, "用户不存在")

    base = _to_item(db, u)

    # The currently active paid membership (_to_item already has id/name/end_date; start_date is added here)
    start_date = None
    if base.membership_id:
        um = (
            db.query(UserMembershipORM)
            .filter(
                UserMembershipORM.user_id == u.id,
                UserMembershipORM.membership_id == base.membership_id,
                UserMembershipORM.is_active == True,  # noqa: E712
            )
            .order_by(UserMembershipORM.id.desc())
            .first()
        )
        start_date = um.start_date if um else None

    det_q = db.query(DetectionRecordORM).filter(DetectionRecordORM.user_id == u.id)
    det_count = det_q.count()
    det_last = det_q.order_by(DetectionRecordORM.created_at.desc()).first()

    pays = (
        db.query(PaymentSessionORM)
        .filter(PaymentSessionORM.user_id == u.id)
        .all()
    )
    paid = [p for p in pays if p.status == "paid"]
    paid_cents: dict[str, int] = {}
    for p in paid:
        cur = (p.currency or "usd").lower()
        paid_cents[cur] = paid_cents.get(cur, 0) + int(p.amount_cents or 0)
    last_paid = max(
        (p.completed_at for p in paid if p.completed_at is not None), default=None,
    )

    return AdminUserDetail(
        **base.model_dump(),
        phone=u.phone,
        phone_verified_at=u.phone_verified_at,
        wechat_nickname=u.wechat_nickname,
        wechat_avatar=u.wechat_avatar,
        wechat_bound=bool(u.wechat_openid),
        wechat_subscribed=bool(u.wechat_subscribed),
        created_at=getattr(u, "created_at", None),
        membership_start_date=start_date,
        balance=_balance_of(db, u.id),
        detection_count=det_count,
        detection_last_at=det_last.created_at if det_last else None,
        payment_count=len(pays),
        payment_paid_count=len(paid),
        payment_paid_cents=paid_cents,
        payment_last_paid_at=last_paid,
    )


@router.get("/{user_id}/payments", response_model=AdminPaymentList)
def list_user_payments(
    user_id: int,
    page: int = 1,
    size: int = 10,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """This user's payment records. All three payment channels (stripe / WeChat / moltspay) land in
    payment_sessions, so this one table is the complete order ledger; purpose distinguishes membership from top-ups."""
    if not db.get(UserORM, user_id):
        raise HTTPException(404, "用户不存在")
    page, size = _page_args(page, size)

    q = (
        db.query(PaymentSessionORM, MembershipORM.slug, MembershipORM.name)
        .outerjoin(MembershipORM, PaymentSessionORM.membership_id == MembershipORM.id)
        .filter(PaymentSessionORM.user_id == user_id)
        .order_by(PaymentSessionORM.created_at.desc(), PaymentSessionORM.id.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    items = [
        AdminPaymentItem(
            id=ps.id,
            purpose=getattr(ps, "purpose", None) or "membership",
            membership_slug=slug,
            membership_name=name,
            amount_cents=int(ps.amount_cents or 0),
            currency=(ps.currency or "usd").lower(),
            status=ps.status,
            provider=ps.provider or "stripe",
            tx_hash=ps.tx_hash,
            created_at=ps.created_at,
            completed_at=ps.completed_at,
        )
        for ps, slug, name in rows
    ]
    return AdminPaymentList(items=items, total=total, page=page, size=size)


@router.get("/{user_id}/detections", response_model=AdminDetectionList)
def list_user_detections(
    user_id: int,
    page: int = 1,
    size: int = 10,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """This user's audit records. **Including ones the user deleted** (non-empty deleted_at): the admin backend must be able to
    reconstruct issues like "they say they ran it but it is not in the list", so this does not reuse the /account filtering.
    result_snapshot is the whole report JSON and is excluded from the list, so one page does not drag out several MB."""
    if not db.get(UserORM, user_id):
        raise HTTPException(404, "用户不存在")
    page, size = _page_args(page, size)

    q = (
        db.query(DetectionRecordORM)
        .filter(DetectionRecordORM.user_id == user_id)
        .order_by(DetectionRecordORM.created_at.desc(), DetectionRecordORM.id.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    items = [
        AdminDetectionItem(
            id=r.id, url=r.url, score=r.score, grade=r.grade, tier=r.tier,
            mode=r.mode, created_at=r.created_at, deleted_at=r.deleted_at,
        )
        for r in rows
    ]
    return AdminDetectionList(items=items, total=total, page=page, size=size)
