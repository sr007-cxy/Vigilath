"""System-level append-only audit events; all functions only flush and never commit themselves."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from geo.models.organization import OperationContextORM, SystemAuditLogORM


def append_event(
    db: Session,
    *,
    event_type: str,
    action: str,
    object_type: str,
    result: str,
    operation_context: OperationContextORM | None = None,
    object_id: int | str | None = None,
    reason: str | None = None,
    before: Any = None,
    after: Any = None,
    trace_id: str | None = None,
    actor_user_id: int | None = None,
    enterprise_user_id: int | None = None,
    organization_id: int | None = None,
    organization_member_id: int | None = None,
    organization_grant_id: int | None = None,
    organization_member_assignment_id: int | None = None,
) -> SystemAuditLogORM:
    if result not in {"success", "denied", "failed"}:
        raise ValueError(f"unsupported audit result: {result}")
    ctx = operation_context
    row = SystemAuditLogORM(
        operation_context_id=(ctx.id if ctx else None),
        trace_id=(ctx.trace_id if ctx else (trace_id or "unscoped")),
        organization_id=(ctx.organization_id if ctx else organization_id),
        organization_member_id=(ctx.organization_member_id if ctx else organization_member_id),
        organization_grant_id=(ctx.organization_grant_id if ctx else organization_grant_id),
        organization_member_assignment_id=(
            ctx.organization_member_assignment_id
            if ctx else organization_member_assignment_id
        ),
        event_type=event_type[:64],
        actor_user_id=(ctx.actor_user_id if ctx else actor_user_id),
        enterprise_user_id=(ctx.enterprise_user_id if ctx else enterprise_user_id),
        action=action[:64],
        object_type=object_type[:64],
        object_id=(str(object_id)[:64] if object_id is not None else None),
        result=result,
        reason=reason,
        before_snapshot_json=(json.dumps(before, ensure_ascii=False, default=str)
                              if before is not None else None),
        after_snapshot_json=(json.dumps(after, ensure_ascii=False, default=str)
                             if after is not None else None),
    )
    db.add(row)
    db.flush()
    return row
