"""Suppression list: a global blacklist keyed by **email address** (计划-2026-09-11-定时发送邮件.md (scheduled email sending plan) §3.3 / §5.1).

Why the existing `consent_status` is not enough: that field hangs off the contact and is **per-lead**;
the same email address under another contact of another company bypasses it entirely. With manual per-email confirmation
human eyes catch such misses; with automatic sending there are no human eyes, so **this table must be checked again right before each send**.

Phase 1 has only two data sources:

1. **Hard bounces**: the remote server permanently rejects with 5xx (classified as "recipient side + permanent" by A1's error classification),
   written automatically by `_mark_delivery_failed()`, covering both manual and scheduled sending;
2. **Manual entry**: users add addresses themselves after a reply saying "stop emailing".

Self-service unsubscribe requires an unsubscribe link / `List-Unsubscribe`; not in phase 1 (noted in §8, to be re-evaluated as volume grows).

Domain-level blocking ("do not contact anyone at our company again") is open item 2 in §8; phase 1 only does email-level.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from agents.sales_agent.models import SalesAgentEmailSuppressionORM
from agents.sales_agent.service.common import SalesAgentError, _is_email, session_has_table

SUPPRESSION_REASON_UNSUBSCRIBED = "unsubscribed"
SUPPRESSION_REASON_BOUNCED_HARD = "bounced_hard"
SUPPRESSION_REASON_COMPLAINED = "complained"
SUPPRESSION_REASON_MANUAL = "manual"

SUPPRESSION_REASONS = (
    SUPPRESSION_REASON_UNSUBSCRIBED,
    SUPPRESSION_REASON_BOUNCED_HARD,
    SUPPRESSION_REASON_COMPLAINED,
    SUPPRESSION_REASON_MANUAL,
)

# Only manually added entries may be manually removed. Unsubscribes / complaints / hard bounces are the recipient's expressed wishes or their server's
# permanent rejection; letting users lift them with one click would be a "bring back people who unsubscribed" button, the opposite of
# why this table exists. A real removal must go through a DBA and leave a trace.
REMOVABLE_SUPPRESSION_REASONS = (SUPPRESSION_REASON_MANUAL,)

# Wording written to `sales_agent_send_schedule_items.skip_reason` on a hit.
# Stating the reason per item is the established rule (Sales Agent does not emit generic errors); "some companies cannot be sent" does not count.
_SKIP_REASON_TEXT = {
    SUPPRESSION_REASON_UNSUBSCRIBED: "对方已退订,不再发送",
    SUPPRESSION_REASON_BOUNCED_HARD: "该邮箱此前硬退信(地址不存在),已停止发送",
    SUPPRESSION_REASON_COMPLAINED: "对方投诉过此类邮件,不再发送",
    SUPPRESSION_REASON_MANUAL: "该邮箱已被你手动加入不再联系名单",
}


def normalize_suppression_email(email: str) -> str:
    """Lowercase + strip whitespace. **Both writes and queries must go through it**, otherwise `Info@` is added and `info@` is not found."""
    return str(email or "").strip().lower()[:255]


def _suppression_table_ready(db: Session) -> bool:
    """Do not crash the whole send path in environments where the migration has not run yet.

    When the table is missing, return False = treat as "nobody is on the suppression list". This degradation direction is debatable
    (the safe direction would be refusing to send), but the suppression list is an **extra** gate and the existing `consent_status`
    check remains; degrading to "cannot block" beats an environment without the migration being unable to send anything.
    """
    return session_has_table(db, SalesAgentEmailSuppressionORM.__tablename__)


def is_email_suppressed(db: Session, user_id: int, email: str) -> bool:
    """Whether this address is on the list. **Call once right before each send.**"""
    return suppression_entry(db, user_id, email) is not None


def suppression_entry(db: Session, user_id: int, email: str) -> SalesAgentEmailSuppressionORM | None:
    key = normalize_suppression_email(email)
    if not key or not _suppression_table_ready(db):
        return None
    return (
        db.query(SalesAgentEmailSuppressionORM)
        .filter(
            SalesAgentEmailSuppressionORM.user_id == int(user_id),
            SalesAgentEmailSuppressionORM.email == key,
        )
        .first()
    )


def suppressed_emails(db: Session, user_id: int, emails: Iterable[str]) -> dict[str, str]:
    """Batch lookup: {normalized address: reason}. Only hits are in the result.

    Compute the whole batch's skip list once when creating a plan, not email by email in a loop: 100 companies would be 100 round trips.
    The separate `is_email_suppressed()` right before sending cannot be omitted: hours pass between plan creation and actual sending,
    and someone may well unsubscribe in between.
    """
    keys = {normalize_suppression_email(e) for e in emails}
    keys.discard("")
    if not keys or not _suppression_table_ready(db):
        return {}
    rows = (
        db.query(SalesAgentEmailSuppressionORM)
        .filter(
            SalesAgentEmailSuppressionORM.user_id == int(user_id),
            SalesAgentEmailSuppressionORM.email.in_(sorted(keys)),
        )
        .all()
    )
    return {row.email: row.reason for row in rows}


def suppression_skip_reason(reason: str) -> str:
    """Skip reason shown to users. Even unrecognized reasons get a specific sentence; no vague wording."""
    return _SKIP_REASON_TEXT.get(str(reason or ""), "该邮箱在不再联系名单里")


def add_email_suppression(
    db: Session,
    user_id: int,
    email: str,
    reason: str = SUPPRESSION_REASON_MANUAL,
    source: str = "",
) -> SalesAgentEmailSuppressionORM | None:
    """Add to the list. Idempotent: if already present, return that row as-is.

    **Does not overwrite an existing row's reason**: the first entry records the real reason this address was first blocked,
    and overwriting it would make "did they unsubscribe or does the address not exist" impossible to tell. The list itself is a boolean gate;
    reason is only for explaining to users, so there is no question of "which is stronger".
    """
    key = normalize_suppression_email(email)
    if not key or not _is_email(key):
        raise SalesAgentError("邮箱地址格式不正确,无法加入不再联系名单。", status_code=400, public=True)
    if not _suppression_table_ready(db):
        return None
    existing = suppression_entry(db, user_id, key)
    if existing is not None:
        return existing
    row = SalesAgentEmailSuppressionORM(
        user_id=int(user_id),
        email=key,
        reason=reason if reason in SUPPRESSION_REASONS else SUPPRESSION_REASON_MANUAL,
        source=str(source or "")[:64],
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def remove_email_suppression(db: Session, user_id: int, email: str) -> bool:
    """Remove a manually added entry. Unsubscribes / complaints / hard bounces cannot be removed; see `REMOVABLE_SUPPRESSION_REASONS`."""
    row = suppression_entry(db, user_id, email)
    if row is None:
        return False
    if row.reason not in REMOVABLE_SUPPRESSION_REASONS:
        raise SalesAgentError(
            "这个邮箱是因为对方退订、投诉或地址不存在才进的名单,不能从这里移除。",
            status_code=400, public=True,
        )
    db.delete(row)
    db.flush()
    return True


def list_email_suppressions(db: Session, user_id: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if not _suppression_table_ready(db):
        return {"suppressions": [], "total": 0, "limit": limit, "offset": offset}
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    query = db.query(SalesAgentEmailSuppressionORM).filter(SalesAgentEmailSuppressionORM.user_id == int(user_id))
    total = query.count()
    rows = query.order_by(SalesAgentEmailSuppressionORM.id.desc()).offset(offset).limit(limit).all()
    return {
        "suppressions": [serialize_email_suppression(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def serialize_email_suppression(row: SalesAgentEmailSuppressionORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "email": row.email,
        "reason": row.reason,
        "reason_text": suppression_skip_reason(row.reason),
        "source": row.source,
        "removable": row.reason in REMOVABLE_SUPPRESSION_REASONS,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
