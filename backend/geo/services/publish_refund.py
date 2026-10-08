"""Refunds for paid placements -- **the single refund entry point**.

Before this there was only one refund path: "full refund if the order fails on the spot" in `rwj_channel._publish_one`.
But an order failure is just one of three ways value isn't delivered; the other two never refunded:

  1. **Order result unknown, later reconciliation finds no order was created** (`rwj_order_poller` decides revert_planned)
     -- charged, no order, task back to pending publish, user can publish again. Same article charged twice.
  2. **Media rejected the draft** (receipt carries rejection_info) -- the article won't go out, so no charge.
  3. **Link dead after publishing, appeal refund succeeded** (`rwj_liveness`) -- upstream refunds our supplier account,
     but **the user gets nothing back**. Upstream refunding us and us refunding the user are two independent ledgers.

All three converge here. Three constraints:

- **Idempotency key pins task + reason**: `rwjpub:{task_id}:refund:{reason}`. Each reason refunds once;
  different reasons (e.g. refunded as no-order, then appeal later succeeds) don't clobber each other, but the amount is the net,
  so the second one naturally refunds nothing.
- **Refunds always use `KIND_REFUND`**, reason goes in note. If each charge type had its own `*_refund` string,
  `spent_today_cents` couldn't subtract it back by (ref_type, ref_id).
- **Refund the net amount**: if part was already refunded, refund only the rest; never refund the original price again.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Refund reason -> user-facing wording
REASONS = {
    "order_not_found": "下单结果未确认,经对账未成单",
    "rejected": "媒体拒稿",
    "appeal": "文章链接失效,申诉退款成功",
    "cancelled": "投放已取消",
    "publish_failed": "下单失败,未发出",
}


def charged_cents(db: Session, task_id: int) -> int:
    """How much this task is still actually charging the user (net). Already refunded parts aren't refunded again."""
    from geo.models.balance import KIND_REFUND, BalanceLedgerORM

    rows = (db.query(BalanceLedgerORM)
            .filter(BalanceLedgerORM.ref_type == "publish_task",
                    BalanceLedgerORM.ref_id == str(task_id)).all())
    total = 0
    for r in rows:
        amt = int(r.amount_cents or 0)
        total += -amt if r.kind == KIND_REFUND else amt
    return max(0, total)


def refund_task(db: Session, task, reason: str, *,
                actor_id: Optional[int] = None) -> Optional[int]:
    """Refund this task's money to **whoever was originally charged**. Returns cents refunded, or None if nothing refunded.

    **Does not commit**; follows the caller's transaction -- refund and task status change must land in the same commit;
    refunding without changing status, or changing status without refunding, are both incidents.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM
    from geo.models.balance import KIND_PUBLISH_MEDIA, KIND_REFUND
    from geo.services import balance_service

    amount = charged_cents(db, task.id)
    if amount <= 0:
        return None                      # never charged (gate closed / ops published on behalf)

    # Refund whom: whoever was charged. Look it up from the ledger, don't guess -- when ops publishes on behalf the charged user is None,
    # and later changes to the topic owner don't matter
    payer = _payer_of(db, task.id)
    if payer is None:
        payer = _topic_owner(db, task)
    if payer is None:
        log.warning("[refund] task=%s no refund target found, skipping", task.id)
        return None

    topic = db.get(AiTelemetryTopicORM, task.topic_id)
    balance_service.credit(
        db, user_id=payer, kind=KIND_REFUND, amount_cents=amount,
        idem_key=f"rwjpub:{task.id}:refund:{reason}",
        ref_type="publish_task", ref_id=str(task.id),
        note={"reason": reason, "reason_text": REASONS.get(reason, reason),
              "refund_of": KIND_PUBLISH_MEDIA, "media": task.media_name,
              "topic_id": getattr(topic, "id", None), "by": actor_id},
    )
    # Zero out actual paid. cost_cents is "actual paid amount, for report aggregation" (publish_attribution is
    # summing it for the placement cost report) -- leaving it after a full refund counts an expense that never happened.
    task.cost_cents = 0

    # Must tell the user the money came back -- otherwise they only see "placement failed" and assume the money is gone.
    # A failed notification write doesn't affect the refund (notify.push swallows its own exceptions).
    try:
        from agents.geo import notify

        notify.refunded(db, task, topic, cents=amount,
                        reason_text=REASONS.get(reason, reason))
    except Exception:  # noqa: BLE001
        log.debug("[refund] notification persist to DB failed task=%s", task.id, exc_info=True)

    log.info("[refund] task=%s refunded %d cents to user=%s (%s)",
             task.id, amount, payer, REASONS.get(reason, reason))
    return amount


def _payer_of(db: Session, task_id: int) -> Optional[int]:
    from geo.models.balance import KIND_PUBLISH_MEDIA, BalanceLedgerORM

    row = (db.query(BalanceLedgerORM)
           .filter(BalanceLedgerORM.ref_type == "publish_task",
                   BalanceLedgerORM.ref_id == str(task_id),
                   BalanceLedgerORM.kind == KIND_PUBLISH_MEDIA)
           .order_by(BalanceLedgerORM.id.asc()).first())
    return int(row.user_id) if row is not None else None


def _topic_owner(db: Session, task) -> Optional[int]:
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    t = db.get(AiTelemetryTopicORM, task.topic_id)
    return int(t.user_id) if t is not None and t.user_id else None
