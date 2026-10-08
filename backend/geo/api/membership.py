from fastapi import APIRouter, Depends
from typing import List
from datetime import datetime
from geo.models.membership import (
    Membership,
    UserMembership,
    ContactSalesRequest,
    UsageResponse,
    SalesLeadORM,
)
from geo.services.membership_service import membership_service
from geo.services.price_override import get_cn_price_cny
from geo.services.quota_service import get_usage_full
from geo.database import SessionLocal, settings
from geo.api.auth import get_current_user
from geo.models.user import User
from geo.services.email_service import email_service
from geo.utils.error_handler import AppException

router = APIRouter()


def _localize_price(m: Membership) -> Membership:
    """The CN site (REGION=cn) converts tier prices to RMB before returning them to the frontend.

    The CN site and global site **share one database**, and DB prices are always in USD — so CN site
    RMB prices can only be overridden at this layer, never by changing the table (that would also change .com).

    Price resolution order:
      1. price_override.get_cn_price_cny(slug) — independent CN site pricing (authoritative)
      2. Tiers without their own price fall back to conversion via settings.USD_TO_CNY_RATE

    Both paths share a source with the WeChat amount actually charged (wechat_pay_service._resolve_amount_fen uses
    the same function and rate), guaranteeing "the page shows exactly what the QR payment charges".
    """
    if not settings.is_cn or m.price <= 0:
        return m

    cn_price = get_cn_price_cny(m.slug)
    if cn_price is not None:
        return m.model_copy(update={"price": cn_price, "currency": "cny"})

    if (m.currency or "usd").lower() != "usd":
        return m
    return m.model_copy(
        update={
            "price": round(m.price * settings.USD_TO_CNY_RATE, 2),
            "currency": "cny",
        }
    )

@router.get("/memberships", response_model=List[Membership])
async def get_memberships():
    """Get all membership tiers.

    Also marks the "granted on login" tier (login_granted) — the product page uses it to show "free for a limited time".
    The criterion is the single constant membership_service.LOGIN_GRANTED_TIER; the frontend does not hard-code tier names:
    when the grant is turned off, set the constant back to "free" and the label disappears automatically.
    """
    from geo.services.membership_service import LOGIN_GRANTED_TIER
    return [
        _localize_price(m).model_copy(update={"login_granted": m.slug == LOGIN_GRANTED_TIER})
        for m in membership_service.get_all_memberships()
    ]

@router.get("/memberships/{membership_id}", response_model=Membership)
async def get_membership(membership_id: int):
    """Get membership by ID"""
    membership = membership_service.get_membership_by_id(membership_id)
    if not membership:
        raise AppException(status_code=404, message="Membership not found")
    return _localize_price(membership)

@router.get("/user-membership", response_model=UserMembership)
async def get_user_membership(current_user: User = Depends(get_current_user)):
    """Return the logged-in user's effective membership.

    `user_memberships` only stores paid subscription rows — free users have no
    row at all. The old behaviour of 404'ing in that case was wrong: a logged-in
    user should always have at least the free tier (login is not the gate, the
    monthly check quota is). Fall back to `get_effective_membership` (which now
    resolves to the login-granted tier when no active paid row exists) and synthesize a
    pseudo `UserMembership` with `id=0` so the response shape stays stable.

    The frontend relies on the semantics of `id`, do not change it: `id > 0` = real paid subscription, `id == 0` = synthesized
    login-granted row. Subscription services like sentiment monitoring use it to distinguish "granted" from "really paid",
    while audit features only look at `slug`.
    """
    user_membership = membership_service.get_user_membership(current_user.id)
    if user_membership:
        paid = membership_service.get_membership_by_id(user_membership.membership_id)
        return user_membership.model_copy(
            update={"slug": paid.slug if paid else None}
        )
    effective = membership_service.get_effective_membership(current_user.id)
    now = datetime.utcnow()
    return UserMembership(
        id=0,
        user_id=current_user.id,
        membership_id=effective.id,
        start_date=now,
        end_date=datetime(9999, 12, 31),
        is_active=True,
        slug=effective.slug,
    )

@router.post("/cancel-membership")
async def cancel_membership(current_user: User = Depends(get_current_user)):
    """Cancel user's membership"""
    success = membership_service.cancel_membership(current_user.id)
    if not success:
        raise AppException(status_code=404, message="User membership not found")
    return {"message": "Membership canceled successfully"}


@router.get("/users/me/usage", response_model=UsageResponse)
async def get_my_usage(current_user: User = Depends(get_current_user)):
    """Return the current user's monthly check quota and remaining count."""
    membership = membership_service.get_effective_membership(current_user.id)
    quota, used, remaining, year_month, daily = get_usage_full(current_user.id, membership)
    return UsageResponse(
        quota=quota, used=used, remaining=remaining, year_month=year_month,
        daily_quota=daily["cap"], daily_used=daily["used"],
        daily_remaining=daily["remaining"], bj_date=daily["bj_date"],
    )


@router.post("/contact-sales")
async def contact_sales(body: ContactSalesRequest):
    """Accept sales leads from the /products-services contact form.

    Writes the lead to sales_leads, notifies the internal sales inbox, and
    sends an acknowledgement to the submitter. Email failures are logged but
    not fatal — the lead is persisted first.
    """
    db = SessionLocal()
    lead_id = None
    try:
        lead = SalesLeadORM(
            name=body.name,
            email=body.email,
            website=body.website,
            tier_slug=body.tier_slug,
            message=body.message,
            created_at=datetime.utcnow(),
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        lead_id = lead.id
    finally:
        db.close()

    email_service.send_sales_notification_email(
        kind="sales-lead",
        name=body.name,
        email=body.email,
        website=body.website,
        message=body.message or "",
        tier_slug=body.tier_slug,
        submission_id=lead_id,
    )
    email_service.send_sales_lead_confirmation_email(
        body.email, body.name, body.tier_slug
    )

    return {
        "message": "咨询已提交，销售将在 1 个工作日内联系您。",
        "lead_id": lead_id,
    }


