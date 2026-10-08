"""Sales Agent service layer: sessions: history normalization, report status backfill, session bundles."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from agents.sales_agent.a2ui import build_card_a2ui
from agents.sales_agent.constants import LEAD_STATUS_REASONS, LEAD_STATUSES
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentLeadActivityORM,
    SalesAgentUndoCheckpointORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
)

from agents.sales_agent.service.common import SalesAgentError, _clean_profile_option_list, _loads, record_compliance_event, session_has_table
from agents.sales_agent.service.entities import _campaign_or_latest, _company_contact_summary, _lead_company
from agents.sales_agent.service.reports import _active_report_share_for_lead, _full_report_by_id, _full_report_for_share, serialize_report_share
from agents.sales_agent.service.profiles import _latest_account_icp, _latest_seller_profile, resolve_active_brand_id
from agents.sales_agent.service.guidance import _apply_lead_flow_selected_values_to_guidance
from agents.sales_agent.service.recommend import _campaign_industries, _campaign_regions, serialize_lead
from agents.sales_agent.service.outreach import serialize_draft


def _parse_dt(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception as e:  # noqa: BLE001
        raise SalesAgentError("next_followup_at 必须是 ISO 时间字符串。") from e


def update_lead_status(
    db: Session,
    user_id: int,
    lead_id: int,
    status: str,
    reason: str = "manual",
    note: str = "",
    next_followup_at: str = "",
    actor: str = "",
) -> dict[str, Any]:
    lead, company = _lead_company(db, user_id, lead_id)
    to_status = (status or "").strip()
    if to_status not in LEAD_STATUSES:
        raise SalesAgentError(f"不支持的线索状态:{to_status}")
    status_reason = (reason or "manual").strip()
    if status_reason not in LEAD_STATUS_REASONS:
        status_reason = "other"
    from_status = lead.status
    lead.status = to_status
    lead.status_reason = status_reason
    lead.status_note = note or ""
    lead.status_updated_by = actor or f"user:{user_id}"
    lead.status_updated_at = datetime.utcnow()
    lead.next_followup_at = _parse_dt(next_followup_at)
    activity = SalesAgentLeadActivityORM(
        lead_id=lead.id,
        activity_type="status_change",
        from_status=from_status,
        to_status=to_status,
        reason=status_reason,
        note=note or "",
        actor=lead.status_updated_by,
    )
    db.add(activity)
    db.commit()
    db.refresh(lead)
    record_compliance_event(db, user_id, "update_lead_status", "low", "allowed", "更新线索状态", {"lead_id": lead.id, "from": from_status, "to": to_status})
    return {"lead": serialize_lead(lead, company, _company_contact_summary(db, company.id)), "activity_id": activity.id}


def _normalize_session_public_id(session_id: str | None) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    try:
        return str(uuid.UUID(raw))
    except ValueError as e:
        raise SalesAgentError("Sales Agent 会话 ID 无效", status_code=400) from e


def _serialize_session(row: SalesAgentChatSessionORM) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "title": row.title,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _session_page(db: Session, user_id: int, limit: int = 15, offset: int = 0) -> dict[str, Any]:
    """Read one page of the session list; the left-side history loads 15 at a time, fetching 1 extra to tell if there is a next page."""
    page_limit = max(1, min(int(limit or 15), 15))
    page_offset = max(0, int(offset or 0))
    rows = (
        db.query(SalesAgentChatSessionORM)
        .filter(SalesAgentChatSessionORM.user_id == user_id)
        .order_by(SalesAgentChatSessionORM.updated_at.desc(), SalesAgentChatSessionORM.id.desc())
        .offset(page_offset)
        .limit(page_limit + 1)
        .all()
    )
    sessions_rows = rows[:page_limit]
    has_more = len(rows) > page_limit
    return {
        "rows": sessions_rows,
        "sessions": [_serialize_session(s) for s in sessions_rows],
        "sessions_has_more": has_more,
        "sessions_next_offset": page_offset + len(sessions_rows) if has_more else None,
        "sessions_limit": page_limit,
        "sessions_offset": page_offset,
    }


def list_sessions_page(db: Session, user_id: int, limit: int = 15, offset: int = 0) -> dict[str, Any]:
    """Return only paginated session history, for the frontend's "load more"."""
    page = _session_page(db, user_id, limit=limit, offset=offset)
    return {k: v for k, v in page.items() if k != "rows"}


_RECOMMENDATION_EMAIL_DRAFT_STATUSES = {
    "draft",
    "pending_approval",
    "sent",
    "failed",
    "disabled",
    "no_recipient",
}
_RECOMMENDATION_EMAIL_DRAFT_KINDS = {"report_email", "no_report_default_email"}


def _latest_report_email_draft_for_lead(db: Session, lead_id: int) -> SalesAgentOutreachLogORM | None:
    rows = (
        db.query(SalesAgentOutreachLogORM)
        .filter(
            SalesAgentOutreachLogORM.lead_id == int(lead_id),
            SalesAgentOutreachLogORM.channel == "email",
            SalesAgentOutreachLogORM.send_status.in_(sorted(_RECOMMENDATION_EMAIL_DRAFT_STATUSES)),
        )
        .order_by(SalesAgentOutreachLogORM.id.desc())
        .limit(30)
        .all()
    )
    fallback = rows[0] if rows else None
    for row in rows:
        meta = _loads(getattr(row, "metadata_json", "{}"), {})
        if meta.get("kind") in _RECOMMENDATION_EMAIL_DRAFT_KINDS:
            return row
    return fallback


def _history_report_state_for_lead(db: Session, user_id: int, lead_id: Any, public_base_url: str = "") -> dict[str, Any]:
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return {}
    lead = db.query(SalesAgentLeadRecommendationORM).filter(SalesAgentLeadRecommendationORM.id == lid, SalesAgentLeadRecommendationORM.user_id == user_id).first()
    if lead is None:
        return {}
    snapshot = _loads(lead.report_snapshot, {})
    state: dict[str, Any] = {
        "report_status": str(snapshot.get("status") or ""),
        "report_id": str(lead.source_report_id or ""),
        "source_report_table": lead.source_report_table or "",
        "source_report_id": lead.source_report_id or "",
    }
    share = _active_report_share_for_lead(db, user_id, lid)
    if share is not None:
        report = _full_report_for_share(db, share)
        share_payload = serialize_report_share(share, report, public_base_url)
        state.update({
            "report_share": share_payload,
            "report_status": share_payload.get("report_status") or state.get("report_status") or "",
            "report_id": share_payload.get("report_id") or state.get("report_id") or "",
            "source_report_table": share_payload.get("source_report_table") or state.get("source_report_table") or "",
            "source_report_id": share_payload.get("source_report_id") or state.get("source_report_id") or "",
            "public_report_url": share_payload.get("public_url") or "",
            "share_token": share_payload.get("share_token") or "",
        })
    elif lead.source_report_table == "full_diagnosis_reports" and lead.source_report_id:
        report = _full_report_by_id(db, user_id, lead.source_report_id)
        if report is not None:
            state["report_status"] = report.status or state.get("report_status") or ""
    draft = _latest_report_email_draft_for_lead(db, lid)
    if draft is not None:
        state["latest_report_email_draft"] = serialize_draft(draft)
        state["has_report_email_draft"] = True
    return {k: v for k, v in state.items() if v not in ("", None, {})}


def _apply_history_report_state(record: dict[str, Any], report_state: dict[str, Any]) -> dict[str, Any]:
    if not report_state:
        return record
    updated = dict(record)
    for key in ("report_status", "report_id", "source_report_table", "source_report_id", "public_report_url", "share_token", "report_share", "latest_report_email_draft", "has_report_email_draft"):
        if report_state.get(key) not in (None, "", {}):
            updated[key] = report_state[key]
    return updated


def _refresh_recommendation_report_state_for_session(db: Session, user_id: int, data: dict[str, Any], public_base_url: str = "") -> tuple[dict[str, Any], bool]:
    refreshed = dict(data)
    changed = False
    report_state_cache: dict[int, dict[str, Any]] = {}

    def state_for(lead_id: Any) -> dict[str, Any]:
        try:
            lid = int(lead_id)
        except (TypeError, ValueError):
            return {}
        if lid not in report_state_cache:
            report_state_cache[lid] = _history_report_state_for_lead(db, user_id, lid, public_base_url)
        return report_state_cache[lid]

    def refresh_target(target: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        target_updated = dict(target)
        target_changed = False
        leads = target.get("leads")
        if isinstance(leads, list):
            next_leads = []
            for lead in leads:
                if not isinstance(lead, dict):
                    next_leads.append(lead)
                    continue
                updated_lead = _apply_history_report_state(lead, state_for(lead.get("id")))
                target_changed = target_changed or updated_lead != lead
                next_leads.append(updated_lead)
            target_updated["leads"] = next_leads
        result_card = target.get("result_card")
        if isinstance(result_card, dict):
            card_updated = dict(result_card)
            items = result_card.get("items")
            if isinstance(items, list):
                next_items = []
                for item in items:
                    if not isinstance(item, dict):
                        next_items.append(item)
                        continue
                    updated_item = _apply_history_report_state(item, state_for(item.get("lead_id") or item.get("id")))
                    target_changed = target_changed or updated_item != item
                    next_items.append(updated_item)
                card_updated["items"] = next_items
            target_updated["result_card"] = card_updated
        return target_updated, target_changed

    direct, direct_changed = refresh_target(refreshed)
    refreshed = direct
    changed = changed or direct_changed
    ranking = refreshed.get("ranking")
    if isinstance(ranking, dict):
        updated_ranking, ranking_changed = refresh_target(ranking)
        refreshed["ranking"] = updated_ranking
        changed = changed or ranking_changed
    return refreshed, changed


# DB/profile defaults and this session's real selections are echoed separately:
#   - seller_capabilities / typical_customers are /admin profile fields; on the normal path they are prefilled by the profile card and
#     skipped by the router; if a card actually appears, the admin page is missing values, and we must not pre-tick the DB values for users to overwrite by mistake.
#   - these two items in lead_flow may also be values prefilled by the profile card, likewise not used to pre-tick these two cards.
_DB_ECHO_DISABLED_SLOTS = {"seller_capabilities", "typical_customers"}
_FLOW_ECHO_DISABLED_SLOTS = {"seller_capabilities", "typical_customers"}


def _guidance_selected_values_context(db: Session | None, user_id: int | None, session: SalesAgentChatSessionORM | None) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    seller = None
    account_icp = None
    campaign = None
    # The brand selected in the session must be read **before** querying the DB. This used to be at the end of the function, and everything above went through
    # `_latest_seller_profile(db, user_id)`; without an id that means "the active brand with the largest id".
    # With multiple brands, whichever brand the session selected, the model was always fed **the most recently created one**:
    # with Vigilath (id 75) selected, the model held GEO-TAG's (id 76) capabilities, and this context
    # is exactly what the model bases improvised options on, so the whole card's options and copy used the other brand's metric definition
    # (production 2026-08-26).
    lead_flow: dict[str, Any] = {}
    if session is not None:
        context = _loads(session.context_json, {})
        tool_state = context.get("tool_state") if isinstance(context, dict) and isinstance(context.get("tool_state"), dict) else {}
        lead_flow = tool_state.get("lead_flow") if isinstance(tool_state.get("lead_flow"), dict) else {}
    if db is not None and user_id is not None:
        # Resolution goes through resolve_active_brand_id: it re-validates ownership and archive status against the DB;
        # context_json can be tampered with, and querying with its id directly would let someone read another account's brand.
        brand_id = resolve_active_brand_id(db, user_id, lead_flow)
        seller = _latest_seller_profile(db, user_id, brand_id)
        if seller is not None:
            seller_capabilities = _clean_profile_option_list(_loads(seller.service_capabilities_json, []), 30)
            if seller_capabilities and "seller_capabilities" not in _DB_ECHO_DISABLED_SLOTS:
                selected["seller_capabilities"] = seller_capabilities
            service_scope = _clean_profile_option_list(_loads(seller.service_scope_json, []), 20)
            if service_scope:
                selected["service_scope"] = service_scope
            if seller.company_size:
                selected["seller_team"] = seller.company_size
            typical = _loads(seller.typical_customers_json, [])
            if typical and "typical_customers" not in _DB_ECHO_DISABLED_SLOTS:
                selected["typical_customers"] = typical
        account_icp = _latest_account_icp(db, user_id, seller_profile_id=seller.id if seller is not None else None)
        # Tasks are also fetched by brand: without that, brand A's session would read brand B's industry/region from the last round,
        # the same problem as the profile cross-contamination above.
        # Task filters are each find-customers round's answers, not brand profile defaults. Historical campaign/account_icp
        # may only serve as a candidate source and must not prefill a new session's question_card. In production the previous round's region 「全国」 ("nationwide")
        # was carried into a new session's target industry card, producing requested_industries=["全国"], regions=[].
        # This round's real selections are still echoed below, overridden by lead_flow.
    # Slots already filled in the session override the DB values: what was just selected this round is newer than the historical profile.
    # These are the user's real selections this round; historical cards must echo them and must not be blocked by the DB-default disabling policy.
    for key, value in lead_flow.items():
        if value and key not in _FLOW_ECHO_DISABLED_SLOTS:
            selected[key] = value
    return selected


def _undo_checkpoint_for_message(db: Session | None, user_id: int | None, session_id: int | None, message_id: int | None) -> dict[str, Any]:
    if db is None or user_id is None or session_id is None or message_id is None:
        return {}
    if not session_has_table(db, SalesAgentUndoCheckpointORM.__tablename__):
        return {}
    rows = (
        db.query(SalesAgentUndoCheckpointORM)
        .filter(
            SalesAgentUndoCheckpointORM.session_id == session_id,
            SalesAgentUndoCheckpointORM.user_id == user_id,
            SalesAgentUndoCheckpointORM.kind == "lead_flow",
            SalesAgentUndoCheckpointORM.consumed_at.is_(None),
        )
        .order_by(SalesAgentUndoCheckpointORM.id.desc())
        .limit(200)
        .all()
    )
    for row in rows:
        snapshot = _loads(row.state_json, {})
        if isinstance(snapshot, dict) and int(snapshot.get("message_watermark_id") or 0) == int(message_id):
            return {"checkpoint_id": int(row.id), "step": row.step or "", "label": row.label or row.step or "这一步"}
    return {}


def _normalize_tool_card_for_session(card: Any, db: Session | None = None, user_id: int | None = None, session_id: int | None = None, message_id: int | None = None, public_base_url: str = "", lead_flow: dict[str, Any] | None = None) -> dict[str, Any] | None:
    record = card if isinstance(card, dict) else None
    if not record:
        return None
    tool = str(record.get("tool") or "")
    data = record.get("data")
    if not tool or not isinstance(data, dict):
        return None
    normalized = dict(record)
    card_changed = False
    undo_ref = _undo_checkpoint_for_message(db, user_id, session_id, message_id)
    if undo_ref and "undo_checkpoint" not in data:
        data = {**data, "undo_checkpoint": undo_ref}
        normalized["data"] = data
        card_changed = True
    if db is not None and user_id is not None and re.search(r"rank_companies|recommend_companies", tool, flags=re.IGNORECASE):
        data, report_changed = _refresh_recommendation_report_state_for_session(db, user_id, data, public_base_url)
        data, selected_changed = _apply_lead_flow_selected_values_to_guidance(data, lead_flow)
        card_changed = card_changed or report_changed or selected_changed
        normalized["data"] = data
    if card_changed or not isinstance(normalized.get("a2ui"), dict):
        a2ui = build_card_a2ui(tool, data)
        if a2ui:
            normalized["a2ui"] = a2ui
    return normalized


def _normalize_message_metadata_for_session(metadata: Any, db: Session | None = None, user_id: int | None = None, session_id: int | None = None, message_id: int | None = None, public_base_url: str = "", lead_flow: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    normalized = dict(metadata)
    cards = [_normalize_tool_card_for_session(card, db, user_id, session_id, message_id, public_base_url, lead_flow) for card in (metadata.get("tool_cards") or [])]
    cards = [card for card in cards if card]
    if cards:
        normalized["tool_cards"] = cards
        normalized.pop("preview_cards", None)
    return normalized


def session_bundle(
    db: Session,
    user_id: int,
    session_id: str | None = None,
    sessions_limit: int = 15,
    sessions_offset: int = 0,
    public_base_url: str = "",
) -> dict[str, Any]:
    sessions_page = _session_page(db, user_id, limit=sessions_limit, offset=sessions_offset)
    sessions_rows = sessions_page["rows"]
    key = _normalize_session_public_id(session_id)
    if key:
        session = next((s for s in sessions_rows if s.public_id == key), None)
        if session is None:
            session = (
                db.query(SalesAgentChatSessionORM)
                .filter(SalesAgentChatSessionORM.public_id == key, SalesAgentChatSessionORM.user_id == user_id)
                .first()
            )
        if session is None:
            raise SalesAgentError("Sales Agent 会话不存在或不属于当前用户", status_code=404)
    else:
        session = sessions_rows[0] if sessions_rows else None
    messages = []
    if session is not None:
        selected_context = _guidance_selected_values_context(db, user_id, session)
        rows = (
            db.query(SalesAgentChatMessageORM)
            .filter(SalesAgentChatMessageORM.session_id == session.id)
            .order_by(SalesAgentChatMessageORM.id.asc())
            .limit(200)
            .all()
        )
        messages = [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "metadata": _normalize_message_metadata_for_session(_loads(r.metadata_json, {}), db, user_id, session.id, r.id, public_base_url, selected_context),
            }
            for r in rows
        ]
    return {
        "session": _serialize_session(session) if session else None,
        "sessions": sessions_page["sessions"],
        "sessions_has_more": sessions_page["sessions_has_more"],
        "sessions_next_offset": sessions_page["sessions_next_offset"],
        "sessions_limit": sessions_page["sessions_limit"],
        "sessions_offset": sessions_page["sessions_offset"],
        "messages": messages,
    }
