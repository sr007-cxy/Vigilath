"""Topic-level charges -- shared by batch runs / draft generation / images / compliance / re-tests.

**Who pays**: the topic owner (`topic.user_id`), not whoever clicked the button.

This differs from paid media placement and is worth spelling out:

- Paid media placement charges the **operator** (`rwj_channel._should_charge`), because that's a "whoever clicks orders"
  action; when ops clicks on a customer's behalf the platform pays, so ops is exempt.
- These items are **ongoing costs on the topic**: batch runs are this topic's monitoring, drafts are this topic's content.
  They can be triggered by cron / background threads / agent / ops console; charging "whoever clicked" would make the same
  cost drift between people depending on the trigger path, and the books wouldn't reconcile. So it's always the topic owner.

**Ops' own topics aren't charged**: skipped when the owner is admin / operator (internal topics, demo topics and
cross-check topics all live under ops accounts; charging them would be charging ourselves).

All prices go through `balance_prices`, **default 0**. `balance_service.charge` passes `<=0` straight through without
writing a ledger entry -- so hook points can go live fully first, be observed for missed/double charges, and then prices turned on one by one in the admin, no release.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

from geo.models.user import UserORM
from geo.services import balance_service

log = logging.getLogger(__name__)

_STAFF_ROLES = ("admin", "operator")


def enabled() -> bool:
    """Master gate. Off = all topic-level charges are computed but not charged (hook points still run, for troubleshooting)."""
    return (os.environ.get("GEO_TOPIC_CHARGE_ENABLED", "1") or "").strip().lower() \
        in ("1", "true", "yes")


def user_payer_id(db: Session, user_id: Optional[int]) -> Optional[int]:
    """Whether this person should be charged. Ops (admin/operator) returns None.

    For usage-based scenarios **without a topic** (one-click full diagnosis is one: it isn't attached to any topic,
    but it's still real-money engine calls).
    """
    if not user_id:
        return None
    user = db.get(UserORM, int(user_id))
    if user is None:
        return None
    if (getattr(user, "role", "") or "user") in _STAFF_ROLES:
        return None
    return int(user_id)


def payer_id(db: Session, topic) -> Optional[int]:
    """Who this topic's costs are charged to. Ops' own topics return None (no charge)."""
    return user_payer_id(db, getattr(topic, "user_id", None))


def price(db: Session, price_key: str) -> int:
    return balance_service.get_price(db, price_key)


def can_afford(db: Session, topic, amount_cents: int) -> bool:
    """Enough? Topics that aren't charged (ops / master gate off) are always True -- don't let the pre-check block them."""
    if not enabled() or amount_cents <= 0:
        return True
    uid = payer_id(db, topic)
    if uid is None:
        return True
    return balance_service.can_afford(db, uid, amount_cents)


def shortfall(db: Session, topic, amount_cents: int) -> Optional[tuple[int, int]]:
    """If insufficient returns (amount still needed, current balance); if enough / not charged returns None.

    For the "insufficient balance" error copy -- saying only "insufficient balance" without how much is missing and how much there is,
    ops wouldn't know how much to top up; previously this path didn't say anything at all (see _afford_or_stop).
    """
    if can_afford(db, topic, amount_cents):
        return None
    uid = payer_id(db, topic)
    if uid is None:
        return None
    return int(amount_cents), balance_service.get_balance(db, uid)


def charge(
    db: Session,
    topic,
    *,
    kind: str,
    amount_cents: int,
    idem_key: str,
    ref_type: str = "",
    ref_id: str = "",
    note: Optional[dict] = None,
) -> Optional[balance_service.BalanceResult]:
    """Charge the topic owner. **Does not commit**; follows the caller's transaction.

    Returning None means "shouldn't charge this time" (master gate off / ops topic / price 0); callers always treat it as allow --
    don't treat None as failure, or ops' topics couldn't even generate drafts.
    """
    if not enabled() or amount_cents <= 0:
        return None
    uid = payer_id(db, topic)
    if uid is None:
        return None
    return balance_service.charge(
        db, user_id=uid, kind=kind, amount_cents=amount_cents,
        idem_key=idem_key, topic_id=getattr(topic, "id", None),
        ref_type=ref_type, ref_id=ref_id, note=note,
    )


def refund(
    db: Session,
    topic,
    *,
    amount_cents: int,
    idem_key: str,
    ref_type: str = "",
    ref_id: str = "",
    note: Optional[dict] = None,
) -> None:
    """Refund. Refunds always use KIND_REFUND with the reason in note -- daily net spend is subtracted back by pairing
    (ref_type, ref_id); a separate *_refund string per charge type would break the pairing."""
    from geo.models.balance import KIND_REFUND

    if amount_cents <= 0:
        return
    uid = payer_id(db, topic)
    if uid is None:
        return
    balance_service.credit(
        db, user_id=uid, kind=KIND_REFUND, amount_cents=amount_cents,
        idem_key=idem_key, ref_type=ref_type, ref_id=ref_id, note=note,
    )
