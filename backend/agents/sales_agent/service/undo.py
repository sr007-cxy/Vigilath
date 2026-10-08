"""Sales Agent session undo checkpoints.

Borrows the idea from code editors / the Claude harness: save a restorable snapshot before each editable card
moves to the next step. On undo the backend restores session state from the checkpoint and rolls back the unconfirmed
recommendation data this session produced after that point.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func

from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.service.common import _dumps, _loads, record_compliance_event
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentUndoCheckpointORM,
)

_EDITABLE_LEAD_FLOW_STEPS = {
    "sales_agent_entry_action",
    "brand",
    "seller_profile",
    "seller_capabilities",
    "custom_capability",
    "seller_profile_summary",
    "typical_customers",
    "target_company_size",
    "target_industries",
    "custom_industry",
    "target_regions",
}
_UNDOABLE_LEAD_STATUSES = {"New", "Recommended", "Diagnosed"}


def _max_id(deps: SalesAgentDeps, model, *filters) -> int:
    query = deps.db.query(func.max(model.id)).filter(model.user_id == deps.user_id)
    for condition in filters:
        query = query.filter(condition)
    return int(query.scalar() or 0)


def _message_watermark_id(deps: SalesAgentDeps) -> tuple[int, int | None]:
    """Return up to which message the checkpoint should keep, and the user message that triggered this round's change."""
    if deps.session_id is None:
        return 0, None
    latest = (
        deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id)
        .order_by(SalesAgentChatMessageORM.id.desc())
        .first()
    )
    if latest is None:
        return 0, None
    if latest.role == "user":
        previous_id = (
            deps.db.query(func.max(SalesAgentChatMessageORM.id))
            .filter(
                SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.id < latest.id,
            )
            .scalar()
        )
        return int(previous_id or 0), int(latest.id)
    return int(latest.id), None


def _snapshot(deps: SalesAgentDeps, flow: dict[str, object], step: str) -> dict[str, Any] | None:
    if deps.session_id is None:
        return None
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if row is None or row.user_id != deps.user_id:
        return None
    message_watermark_id, source_message_id = _message_watermark_id(deps)
    campaign_watermark_id = _max_id(
        deps,
        SalesAgentRecommendationCampaignORM,
        SalesAgentRecommendationCampaignORM.session_id == deps.session_id,
    )
    lead_watermark_id = _max_id(deps, SalesAgentLeadRecommendationORM)
    tool_state = dict(deps.tool_state or {})
    tool_state["lead_flow"] = dict(flow or {})
    return {
        "kind": "lead_flow",
        "step": step,
        "tool_state": tool_state,
        "lead_flow": dict(flow or {}),
        "messages_json": row.messages_json or "[]",
        "current_intent": row.current_intent or "",
        "message_watermark_id": message_watermark_id,
        "source_message_id": source_message_id,
        "campaign_watermark_id": campaign_watermark_id,
        "lead_watermark_id": lead_watermark_id,
    }


def _checkpoint_key(session_id: int, step: str, snapshot: dict[str, Any]) -> str:
    material = _dumps({"session_id": session_id, "step": step, "snapshot": snapshot})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_lead_flow_checkpoint(deps: SalesAgentDeps, flow: dict[str, object], step: str = "", label: str = "") -> None:
    """Record the current editable card state; a later undo returns to this card."""
    if deps.session_id is None:
        return
    normalized_step = str(step or flow.get("step") or "").strip()
    if normalized_step not in _EDITABLE_LEAD_FLOW_STEPS:
        return
    snapshot = _snapshot(deps, flow, normalized_step)
    if not snapshot:
        return
    key = _checkpoint_key(deps.session_id, normalized_step, snapshot)
    existing = (
        deps.db.query(SalesAgentUndoCheckpointORM.id)
        .filter(SalesAgentUndoCheckpointORM.session_id == deps.session_id, SalesAgentUndoCheckpointORM.checkpoint_key == key)
        .first()
    )
    if existing is not None:
        return
    row = SalesAgentUndoCheckpointORM(
        session_id=deps.session_id,
        user_id=deps.user_id,
        source_message_id=snapshot.get("source_message_id"),
        checkpoint_key=key,
        kind="lead_flow",
        label=label or normalized_step,
        step=normalized_step,
        state_json=_dumps(snapshot),
    )
    deps.db.add(row)
    deps.db.commit()


def _rollback_side_effects_after_checkpoint(deps: SalesAgentDeps, snapshot: dict[str, Any]) -> None:
    if deps.session_id is None:
        return
    message_watermark_id = int(snapshot.get("message_watermark_id") or 0)
    campaign_watermark_id = int(snapshot.get("campaign_watermark_id") or 0)

    deps.db.query(SalesAgentChatMessageORM).filter(
        SalesAgentChatMessageORM.session_id == deps.session_id,
        SalesAgentChatMessageORM.id > message_watermark_id,
    ).delete(synchronize_session=False)

    campaigns = (
        deps.db.query(SalesAgentRecommendationCampaignORM)
        .filter(
            SalesAgentRecommendationCampaignORM.user_id == deps.user_id,
            SalesAgentRecommendationCampaignORM.session_id == deps.session_id,
            SalesAgentRecommendationCampaignORM.id > campaign_watermark_id,
            SalesAgentRecommendationCampaignORM.status != "undone",
        )
        .all()
    )
    campaign_ids = [int(row.id) for row in campaigns]
    for campaign in campaigns:
        campaign.status = "undone"
        campaign.updated_at = datetime.utcnow()
    if campaign_ids:
        leads = (
            deps.db.query(SalesAgentLeadRecommendationORM)
            .filter(
                SalesAgentLeadRecommendationORM.user_id == deps.user_id,
                SalesAgentLeadRecommendationORM.campaign_id.in_(campaign_ids),
                SalesAgentLeadRecommendationORM.status.in_(_UNDOABLE_LEAD_STATUSES),
            )
            .all()
        )
        for lead in leads:
            lead.status = "Undone"
            lead.status_reason = "other"
            lead.status_note = "已随 Sales Agent 会话撤销"
            lead.status_updated_by = f"user:{deps.user_id}:undo"
            lead.status_updated_at = datetime.utcnow()


def restore_lead_flow_checkpoint(deps: SalesAgentDeps, checkpoint_id: int | None = None) -> dict[str, Any]:
    """Restore the given checkpoint; if none is given, restore the most recent unconsumed checkpoint."""
    if deps.session_id is None:
        return {"restored": False, "message": "当前没有可撤销步骤。"}
    session = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if session is None or session.user_id != deps.user_id:
        return {"restored": False, "message": "当前没有可撤销步骤。"}
    query = deps.db.query(SalesAgentUndoCheckpointORM).filter(
        SalesAgentUndoCheckpointORM.session_id == deps.session_id,
        SalesAgentUndoCheckpointORM.user_id == deps.user_id,
        SalesAgentUndoCheckpointORM.kind == "lead_flow",
        SalesAgentUndoCheckpointORM.consumed_at.is_(None),
    )
    if checkpoint_id is not None:
        query = query.filter(SalesAgentUndoCheckpointORM.id == int(checkpoint_id))
    checkpoint = query.order_by(SalesAgentUndoCheckpointORM.id.desc()).first()
    if checkpoint is None:
        return {"restored": False, "message": "这一步不能撤销或已经撤销过。" if checkpoint_id is not None else "当前没有可撤销步骤。"}

    snapshot = _loads(checkpoint.state_json, {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    tool_state = snapshot.get("tool_state") if isinstance(snapshot.get("tool_state"), dict) else {}
    lead_flow = snapshot.get("lead_flow") if isinstance(snapshot.get("lead_flow"), dict) else {}
    if lead_flow and "lead_flow" not in tool_state:
        tool_state = {**tool_state, "lead_flow": lead_flow}
    context = _loads(session.context_json, {}) or {}
    if not isinstance(context, dict):
        context = {}
    context["tool_state"] = tool_state

    _rollback_side_effects_after_checkpoint(deps, snapshot)
    session.context_json = json.dumps(context, ensure_ascii=False, default=str)
    session.messages_json = str(snapshot.get("messages_json") or "[]")
    session.current_intent = str(snapshot.get("current_intent") or "")
    session.updated_at = datetime.utcnow()
    deps.tool_state = dict(tool_state)

    now = datetime.utcnow()
    deps.db.query(SalesAgentUndoCheckpointORM).filter(
        SalesAgentUndoCheckpointORM.session_id == deps.session_id,
        SalesAgentUndoCheckpointORM.user_id == deps.user_id,
        SalesAgentUndoCheckpointORM.id >= checkpoint.id,
        SalesAgentUndoCheckpointORM.consumed_at.is_(None),
    ).update({SalesAgentUndoCheckpointORM.consumed_at: now}, synchronize_session=False)
    record_compliance_event(
        deps.db,
        deps.user_id,
        "undo_lead_flow_checkpoint",
        "low",
        "allowed",
        "恢复 Sales Agent 会话 checkpoint",
        {
            "session_id": deps.session_id,
            "checkpoint_id": checkpoint.id,
            "step": checkpoint.step or snapshot.get("step") or "",
            "message_watermark_id": snapshot.get("message_watermark_id"),
            "campaign_watermark_id": snapshot.get("campaign_watermark_id"),
        },
        commit=False,
    )
    deps.db.commit()
    return {
        "restored": True,
        "checkpoint_id": checkpoint.id,
        "step": str(checkpoint.step or snapshot.get("step") or ""),
        "label": checkpoint.label or checkpoint.step or "这一步",
        "message_watermark_id": snapshot.get("message_watermark_id"),
    }


def restore_latest_lead_flow_checkpoint(deps: SalesAgentDeps) -> dict[str, Any]:
    """Restore the most recent unconsumed checkpoint and roll back session data after it."""
    return restore_lead_flow_checkpoint(deps)
