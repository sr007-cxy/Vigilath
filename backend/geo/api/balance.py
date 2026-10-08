"""Balance center endpoints — query balance, transactions, and the cost of one round.

The balance only covers **batch runs and publishing**; audits are not charged and still use the /api/users/me/usage membership quota.
The two systems coexist and answer separately; do not mix them in responses here.

The first visit lazily grants the signup bonus (see balance_service.get_or_create_account), so
GET /balance must commit — otherwise a new user sees ¥10 but no row is persisted and it disappears on refresh.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from geo.api.auth import get_current_user, require_admin
from geo.database import SessionLocal
from geo.utils.error_handler import AppException
from geo.models.balance import (
    PRICE_BATCH_CELL,
    PRICE_CONTENT_DOC,
    PRICE_SENTIMENT_DAILY,
    PRICE_SOLUTION,
    PRICE_SENTIMENT_ONCE,
    BalanceLedgerORM,
)
from geo.models.user import UserORM
from geo.services import balance_service, batch_billing
from geo.services.wechat_pay_service import TOPUP_MAX_CENTS, TOPUP_MIN_CENTS

router = APIRouter()
logger = logging.getLogger(__name__)


class BalanceOut(BaseModel):
    balance_cents: int
    granted_cents: int
    recharged_cents: int
    spent_cents: int
    # Unit prices are sent along, so the frontend can compute "approx. cost per round" without another request
    price_batch_cell_cents: int
    price_content_doc_cents: int
    price_sentiment_once_cents: int = 0
    price_sentiment_daily_cents: int = 0
    # Per GEO strategy optimization plan. Clicking the generate button charges immediately, so users must see this number before clicking.
    price_solution_cents: int = 0
    # Top-up min/max are sent too — a hard-coded frontend copy would inevitably drift from the backend
    topup_min_cents: int = 1000
    topup_max_cents: int = 500000
    # Billing start date for monitoring batch runs (Beijing date). None = still in shadow period; the page should say "not billed yet",
    # and not use the unit price to promise a charge that has not happened.
    batch_billing_start_date: str | None = None


class LedgerItem(BaseModel):
    id: int
    created_at: str
    direction: str
    kind: str
    amount_cents: int
    balance_after_cents: int
    topic_id: Optional[int] = None
    note: Optional[dict] = None


class LedgerList(BaseModel):
    items: List[LedgerItem]
    total: int
    page: int
    size: int


@router.get("/balance", response_model=BalanceOut)
async def get_balance(current_user: UserORM = Depends(get_current_user)):
    db = SessionLocal()
    try:
        acc = balance_service.get_or_create_account(db, current_user.id)
        out = BalanceOut(
            balance_cents=int(acc.balance_cents or 0),
            granted_cents=int(acc.granted_cents or 0),
            recharged_cents=int(acc.recharged_cents or 0),
            spent_cents=int(acc.spent_cents or 0),
            price_batch_cell_cents=balance_service.get_price(db, PRICE_BATCH_CELL),
            price_content_doc_cents=balance_service.get_price(db, PRICE_CONTENT_DOC),
            price_sentiment_once_cents=balance_service.get_price(db, PRICE_SENTIMENT_ONCE),
            price_sentiment_daily_cents=balance_service.get_price(db, PRICE_SENTIMENT_DAILY),
            price_solution_cents=balance_service.get_price(db, PRICE_SOLUTION),
            topup_min_cents=TOPUP_MIN_CENTS,
            topup_max_cents=TOPUP_MAX_CENTS,
            batch_billing_start_date=batch_billing.billing_start_date() or None,
        )
        # Lazy grant must be persisted — get_db's finally only closes without committing, so we must commit here
        db.commit()
        return out
    finally:
        db.close()


@router.get("/balance/ledger", response_model=LedgerList)
async def list_ledger(
    page: int = 1,
    size: int = 20,
    current_user: UserORM = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        size = max(1, min(int(size), 100))
        page = max(1, int(page))
        q = (
            db.query(BalanceLedgerORM)
            .filter(BalanceLedgerORM.user_id == current_user.id)
            .order_by(BalanceLedgerORM.id.desc())
        )
        total = q.count()
        rows = q.offset((page - 1) * size).limit(size).all()
        items = []
        for r in rows:
            note = None
            if r.note_json:
                try:
                    note = json.loads(r.note_json)
                except (TypeError, ValueError):
                    note = None
            items.append(LedgerItem(
                id=int(r.id),
                created_at=str(r.created_at),
                direction=r.direction,
                kind=r.kind,
                amount_cents=int(r.amount_cents or 0),
                balance_after_cents=int(r.balance_after_cents or 0),
                topic_id=r.topic_id,
                note=note,
            ))
        return LedgerList(items=items, total=total, page=page, size=size)
    finally:
        db.close()


# ── Ops credit ────────────────────────────────────────────────────────────────
# The wallet used to be query-only: credit() was written but had zero callers site-wide, so every balance was 0.
# A credit path must exist before charging is wired up, otherwise once charging starts every paid action online breaks immediately.
# This is the **manual adjustment** entry (top-up received, bonuses, refund compensation); self-service top-up and agent payments are separate.
class AdminCreditIn(BaseModel):
    user_id: int
    amount_cents: int                 # Positive, in cents
    kind: str = "admin_grant"         # admin_grant / topup_manual / refund_manual
    reason: str = ""                  # Required reason, goes into the transaction note for later review
    idem_key: str = ""                # If omitted, generated from (user, amount, reason) to prevent accidental duplicate credits


@router.post("/admin/balance/credit")
async def admin_credit(body: AdminCreditIn, admin: UserORM = Depends(require_admin)):
    """Credit an account. **Idempotent**: repeated submissions with the same idem_key take effect only once."""
    import hashlib

    from geo.services import balance_service

    if body.amount_cents <= 0:
        raise AppException(status_code=400, message="金额必须为正数(分)")
    if not body.reason.strip():
        raise AppException(status_code=400, message="必须填写理由 —— 加款要能事后对账")

    idem = body.idem_key.strip() or "adm_" + hashlib.sha256(
        f"{body.user_id}:{body.amount_cents}:{body.reason.strip()}".encode()
    ).hexdigest()[:32]

    db = SessionLocal()
    try:
        if db.get(UserORM, body.user_id) is None:
            raise AppException(status_code=404, message=f"账号 {body.user_id} 不存在")
        res = balance_service.credit(
            db, user_id=body.user_id, kind=body.kind, amount_cents=body.amount_cents,
            idem_key=idem, ref_type="admin", ref_id=str(admin.id),
            note={"reason": body.reason.strip(), "by": getattr(admin, "name", "") or admin.id},
        )
        db.commit()
        return {"status": res.status, "balance_cents": res.balance_cents,
                "amount_cents": res.amount_cents, "idem_key": idem,
                "duplicate": res.status == balance_service.DUPLICATE}
    finally:
        db.close()
