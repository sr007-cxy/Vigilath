"""Compliance audit log -- thin append-only wrapper.

Each service calls record() explicitly at action points (written in the same transaction, committed by the caller);
failures do not block the main flow but must log an error. No DB triggers -- design doc section 11.5.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from geo.models.compliance import ComplianceAuditLogORM

log = logging.getLogger(__name__)

EVENT_TYPES = frozenset({
    "admission", "qualification_expiry", "scan", "block", "release", "review",
    "claim_verify", "lexicon_upgrade", "alert", "ticket_open", "ticket_action",
    "retest", "ticket_close", "export", "external_scan",
    # Audit trail for paid media channel (Ruanwenjiang, etc.) charge actions: preview / order / reconcile, distinguished by detail.action
    "paid_publish",
    # Audit trail for free distribution channel (aitoearn etc. publishing to own accounts) actions: publish / reconcile, distinguished by detail.action
    "channel_publish",
    # Audit trail for agent actions on behalf of operators (agents/geo/audit.py): detail.tool / risk / confirmed / turn_id
    # distinguishes "clicked by an operator on the page" from "clicked by the agent", detail.via == "agent"
    "agent_action",
})


def record(
    db: Session,
    *,
    topic_id: int,
    event_type: str,
    object_type: str = "",
    object_id: Optional[int] = None,
    actor_type: str = "system",
    actor_id: Optional[int] = None,
    actor_name: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append one audit log entry (does not commit; follows the caller's transaction).

    event_type must be in EVENT_TYPES -- the enum is defined centrally to avoid sprawl.
    Any exception is swallowed with a warning only: an audit trail failure should not interrupt the main business flow.
    """
    try:
        if event_type not in EVENT_TYPES:
            log.error("[compliance-audit] unknown event_type=%s (topic=%s)", event_type, topic_id)
            return
        db.add(ComplianceAuditLogORM(
            topic_id=topic_id,
            event_type=event_type,
            object_type=object_type or "",
            object_id=object_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=(actor_name or "")[:64],
            detail_json=json.dumps(detail or {}, ensure_ascii=False, default=str),
        ))
    except Exception:  # noqa: BLE001
        log.exception("[compliance-audit] record failed topic=%s event=%s", topic_id, event_type)
