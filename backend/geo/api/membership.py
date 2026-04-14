from fastapi import APIRouter, Depends
from typing import List
from datetime import datetime
from geo.models.membership import (
    Membership,
    MembershipCreate,
    UserMembership,
    UserMembershipCreate,
    MembershipUpgrade,
    ContactSalesRequest,
    UsageResponse,
    SubscribeRequest,
    SalesLeadORM,
)
from geo.services.membership_service import membership_service
from geo.services.quota_service import get_usage
from geo.database import SessionLocal
from geo.api.auth import get_current_user
from geo.models.user import User
from geo.utils.error_handler import AppException

router = APIRouter()

@router.get("/memberships", response_model=List[Membership])
async def get_memberships():
    """Get all membership tiers"""
    return membership_service.get_all_memberships()

@router.get("/memberships/{membership_id}", response_model=Membership)
async def get_membership(membership_id: int):
    """Get membership by ID"""
    membership = membership_service.get_membership_by_id(membership_id)
    if not membership:
        raise AppException(status_code=404, message="Membership not found")
    return membership

@router.post("/memberships", response_model=Membership)
async def create_membership(membership: MembershipCreate):
    """Create a new membership tier"""
    return membership_service.create_membership(membership)

@router.put("/memberships/{membership_id}", response_model=Membership)
async def update_membership(membership_id: int, membership: MembershipCreate):
    """Update membership tier"""
    updated_membership = membership_service.update_membership(membership_id, membership)
    if not updated_membership:
        raise AppException(status_code=404, message="Membership not found")
    return updated_membership

@router.delete("/memberships/{membership_id}")
async def delete_membership(membership_id: int):
    """Delete membership tier"""
    success = membership_service.delete_membership(membership_id)
    if not success:
        raise AppException(status_code=404, message="Membership not found")
    return {"message": "Membership deleted successfully"}

@router.get("/user-membership", response_model=UserMembership)
async def get_user_membership(current_user: User = Depends(get_current_user)):
    """Get current user's membership"""
    user_membership = membership_service.get_user_membership(current_user.id)
    if not user_membership:
        raise AppException(status_code=404, message="User membership not found")
    return user_membership

@router.post("/user-membership", response_model=UserMembership)
async def create_user_membership(user_membership: UserMembershipCreate, current_user: User = Depends(get_current_user)):
    """Create a new user membership"""
    # Only allow users to create membership for themselves
    if user_membership.user_id != current_user.id:
        raise AppException(status_code=403, message="You can only create membership for yourself")
    return membership_service.create_user_membership(user_membership)

@router.post("/upgrade-membership", response_model=UserMembership)
async def upgrade_membership(upgrade: dict, current_user: User = Depends(get_current_user)):
    """Upgrade user's membership"""
    # 从token中获取用户ID
    user_id = current_user.id
    new_membership_id = upgrade.get("new_membership_id")
    if not new_membership_id:
        raise AppException(status_code=400, message="New membership ID is required")
    upgraded_membership = membership_service.upgrade_membership(user_id, new_membership_id)
    if not upgraded_membership:
        raise AppException(status_code=404, message="Membership not found")
    return upgraded_membership

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
    quota, used, remaining, year_month = get_usage(current_user.id, membership)
    return UsageResponse(quota=quota, used=used, remaining=remaining, year_month=year_month)


@router.post("/contact-sales")
async def contact_sales(body: ContactSalesRequest):
    """Accept sales leads from the /products-services contact form.

    Writes the lead to the sales_leads table. Email notification is intentionally
    a TODO — we log to stdout so ops can tail logs until the pipeline is wired.
    """
    db = SessionLocal()
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
        print(
            f"[sales-lead] id={lead.id} name={body.name!r} email={body.email!r} "
            f"tier={body.tier_slug!r} website={body.website!r}"
        )
        return {
            "message": "咨询已提交，销售将在 1 个工作日内联系您。",
            "lead_id": lead.id,
        }
    finally:
        db.close()


@router.post("/subscribe")
async def subscribe(body: SubscribeRequest, current_user: User = Depends(get_current_user)):
    """Stub subscribe endpoint — real payment integration is deferred.

    Returns a pending status so the frontend can show a "支付功能即将上线"
    prompt. Does NOT activate any user_membership rows.
    """
    membership = membership_service.get_membership_by_slug(body.slug)
    if not membership:
        raise AppException(status_code=404, message=f"Membership '{body.slug}' not found")
    if membership.tier_type != "saas":
        raise AppException(
            status_code=400,
            message=f"{membership.name} 需联系销售开通，不支持自助订阅",
        )
    return {
        "status": "pending",
        "message": "支付功能即将上线，暂时无法自助开通。请稍后再试或联系客服。",
        "tier_slug": body.slug,
    }