from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from geo.api.auth import get_current_user
from geo.database import SessionLocal, settings
from geo.models.payment import PaymentSessionORM
from geo.models.membership import MembershipORM
from geo.models.user import UserORM
from geo.services.membership_service import membership_service
from geo.utils.error_handler import AppException

router = APIRouter()

MOLTSPAY_WALLET = getattr(settings, "MOLTSPAY_WALLET_ADDRESS", "")
MOLTSPAY_CHAIN = getattr(settings, "MOLTSPAY_CHAIN", "base")

SLUG_TO_SERVICE_ID = {
    "pro": "membership-detector",
    "starter": "membership-starter",
    "growth": "membership-growth",
}


class CreateMoltsPayRequest(BaseModel):
    slug: str


class FulfillRequest(BaseModel):
    user_id: int
    membership_slug: str


@router.post("/moltspay/create")
async def create_moltspay_payment(
    body: CreateMoltsPayRequest,
    current_user: UserORM = Depends(get_current_user),
):
    if not getattr(settings, "MOLTSPAY_ENABLED", False):
        raise AppException(status_code=503, message="MoltsPay is not enabled")

    db = SessionLocal()
    try:
        membership = (
            db.query(MembershipORM).filter(MembershipORM.slug == body.slug).first()
        )
        if not membership:
            raise AppException(status_code=404, message=f"Membership '{body.slug}' not found")
        if membership.price <= 0:
            raise AppException(status_code=400, message="Free tier does not require payment")

        service_id = SLUG_TO_SERVICE_ID.get(body.slug)
        if not service_id:
            raise AppException(status_code=400, message=f"MoltsPay not available for plan '{body.slug}'")

        existing = (
            db.query(PaymentSessionORM)
            .filter(
                PaymentSessionORM.user_id == current_user.id,
                PaymentSessionORM.membership_id == membership.id,
                PaymentSessionORM.provider == "moltspay",
                PaymentSessionORM.status == "pending",
            )
            .order_by(PaymentSessionORM.created_at.desc())
            .first()
        )
        if existing:
            age_minutes = (datetime.utcnow() - existing.created_at).total_seconds() / 60
            if age_minutes < 30:
                return {
                    "payment_id": existing.id,
                    "amount_usdc": float(membership.price),
                    "chain": MOLTSPAY_CHAIN,
                    "service_id": service_id,
                    "wallet_address": MOLTSPAY_WALLET,
                    "status": "pending",
                }
            existing.status = "expired"
            db.commit()

        amount_cents = int(round(float(membership.price) * 100))

        row = PaymentSessionORM(
            user_id=current_user.id,
            membership_id=membership.id,
            amount_cents=amount_cents,
            currency="usdc",
            status="pending",
            provider="moltspay",
            chain=MOLTSPAY_CHAIN,
            wallet_address=MOLTSPAY_WALLET,
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        return {
            "payment_id": row.id,
            "amount_usdc": float(membership.price),
            "chain": MOLTSPAY_CHAIN,
            "service_id": service_id,
            "wallet_address": MOLTSPAY_WALLET,
            "status": "pending",
        }
    finally:
        db.close()


@router.get("/moltspay/status/{payment_id}")
async def get_moltspay_status(
    payment_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        row = (
            db.query(PaymentSessionORM)
            .filter(
                PaymentSessionORM.id == payment_id,
                PaymentSessionORM.user_id == current_user.id,
                PaymentSessionORM.provider == "moltspay",
            )
            .first()
        )
        if not row:
            raise AppException(status_code=404, message="Payment not found")

        return {
            "payment_id": row.id,
            "status": row.status,
            "tx_hash": row.tx_hash,
            "completed_at": str(row.completed_at) if row.completed_at else None,
        }
    finally:
        db.close()


@router.post("/moltspay/fulfill")
async def moltspay_fulfill(request: Request, body: FulfillRequest):
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1"):
        raise AppException(status_code=403, message="Forbidden")

    db = SessionLocal()
    try:
        membership = (
            db.query(MembershipORM).filter(MembershipORM.slug == body.membership_slug).first()
        )
        if not membership:
            raise AppException(status_code=404, message="Membership not found")

        row = (
            db.query(PaymentSessionORM)
            .filter(
                PaymentSessionORM.user_id == body.user_id,
                PaymentSessionORM.membership_id == membership.id,
                PaymentSessionORM.provider == "moltspay",
                PaymentSessionORM.status == "pending",
            )
            .order_by(PaymentSessionORM.created_at.desc())
            .first()
        )
        if not row:
            raise AppException(status_code=404, message="No pending MoltsPay session found")

        if row.status == "paid":
            return {"success": True, "payment_id": row.id}

        row.status = "paid"
        row.completed_at = datetime.utcnow()
        db.commit()

        membership_service.upgrade_membership(body.user_id, membership.id)

        return {"success": True, "payment_id": row.id}
    finally:
        db.close()
