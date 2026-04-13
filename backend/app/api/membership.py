from fastapi import APIRouter, Depends
from typing import List
from app.models.membership import Membership, MembershipCreate, UserMembership, UserMembershipCreate, MembershipUpgrade
from app.services.membership_service import MembershipService
from app.api.auth import get_current_user
from app.models.user import User
from app.utils.error_handler import AppException

router = APIRouter()

# Initialize membership service
membership_service = MembershipService()

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