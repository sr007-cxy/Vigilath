"""Membership purchase fulfillment -- activate the tier + credit an equal amount of credits. Shared by all three payment gateways.

**Metric definition**: buying membership = getting the tier + equal credits. The tier decides audit quota and advanced mode access; credits are used for
media placement / sentiment monitoring / drafting -- neither replaces the other, so it is "plus", not "either-or".
A 39.9 yuan tier -> 39.9 credits (1 credit = 1 yuan, the same number shown on the page and actually received via WeChat).

**Why a separate module**: there are three fulfillment points (WeChat `wechat_pay_service._fulfill_if_needed`,
Stripe `stripe_service._fulfill`, and the two confirmation entries in MoltsPay `api/moltspay_payment`).
Previously only the WeChat one added credits; the other two only upgraded -- three implementations of the same thing will inevitably drift.

**Amount is the amount actually received** (`payment_sessions.amount_cents`), not the tier table price: test accounts have
`price_override.get_test_price_override` (actual charge pushed down to ¥1), and crediting by tier price would mean paying 1 yuan
and receiving 39.9.

**Currency**: credits are counted in "RMB cents", while `memberships.price` is stored in USD and the CN and global sites share one DB.
So non-RMB orders must be converted to cents at `USD_TO_CNY_RATE` before crediting (the same rate as `_localize_price` /
`wechat_pay_service._resolve_amount_fen`), otherwise $9.99 would be treated as ¥9.99.
Unrecognized currencies are **not credited** and trigger an alert -- better to give less than give wrongly.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import settings
from geo.models.balance import KIND_RECHARGE
from geo.services import balance_service
from geo.services.membership_service import membership_service

logger = logging.getLogger(__name__)


def wallet_cents_for(amount_cents: int, currency: str) -> int:
    """Amount received -> cents credited to the credits account. Returns 0 for unrecognized currencies (= not credited)."""
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        return 0
    cur = (currency or "cny").strip().lower()
    if cur == "cny":
        return amount_cents
    # USDC/USDT are 1:1 with USD (MoltsPay's on-chain receipts record the currency as usdc)
    if cur in ("usd", "usdc", "usdt"):
        return int(round(amount_cents * settings.USD_TO_CNY_RATE))
    logger.warning("membership grant: unknown currency %s, no credits for this order (amount_cents=%d)",
                   cur, amount_cents)
    return 0


def fulfill_paid_membership(
    db: Session,
    row,
    *,
    provider: str,
    order_ref: str,
) -> Optional[balance_service.BalanceResult]:
    """Activate the tier + credit equal credits. **The caller guarantees row is already set to paid and committed.**

    `row` is a PaymentSessionORM; `order_ref` is this gateway's order number (WeChat out_trade_no /
    Stripe session id / the session primary key for MoltsPay), only used to build the idempotency key.

    Idempotency key `memb:{order_ref}` is separate from the top-up `wxpay:` prefix -- the same money is never counted both as
    a top-up and as membership. Callback re-pushes, frontend polling and webhook collisions are all caught by `balance_ledger`'s unique key.

    Returning None means nothing was credited (amount 0 / unrecognized currency), **not a failure** -- the tier is already activated.
    """
    if not getattr(row, "membership_id", None):
        # Only top-up orders lack membership_id, and top-up orders should not reach here (callers route by purpose).
        # If one does, do not blow up: the money is already received, log an alert and skip the tier.
        logger.error("membership fulfill: order %s has no membership_id, skipping activation", order_ref)
    else:
        membership_service.upgrade_membership(row.user_id, row.membership_id)

    cents = wallet_cents_for(row.amount_cents, getattr(row, "currency", "") or "")
    if cents <= 0:
        db.commit()
        return None

    res = balance_service.credit(
        db, user_id=row.user_id, kind=KIND_RECHARGE, amount_cents=cents,
        idem_key=f"memb:{order_ref}",
        ref_type="payment_session", ref_id=str(row.id),
        note={"provider": provider, "order": order_ref,
              "membership_id": row.membership_id,
              "paid_cents": int(row.amount_cents or 0),
              "paid_currency": (getattr(row, "currency", "") or "cny").lower(),
              "grant": "membership_equivalent"},
    )
    db.commit()
    logger.info("membership fulfilled: user_id=%s membership_id=%s provider=%s "
                "order=%s points=+%d status=%s",
                row.user_id, row.membership_id, provider, order_ref, cents, res.status)
    return res
