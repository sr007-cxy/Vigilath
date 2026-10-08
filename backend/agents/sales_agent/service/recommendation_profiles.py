"""Sales Agent chat recommendation profiles: one per brand, updated in place; plus a usage log.

A recommendation profile is **state** (the conditions this brand currently recommends with), not a ledger, so it collapses to one row per brand
and is overwritten in place each round. The immutable snapshot of each round's filters is in `sales_agent_recommendation_campaigns`
(target_industries/target_regions/target_company_size); resending old emails reads that snapshot
and does not rely on this table keeping history; that is the precondition for in-place updates being safe.

This module only reads/writes the recommendation profile's own two tables and **neither reads nor writes `sales_agent_seller_profiles`**;
`seller_profile_id` is only an ownership marker, and the profile content always comes from `_flow_payload(flow)`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agents.sales_agent.models import (
    SalesAgentRecommendationProfileUsageORM,
    SalesAgentRecommendationProfileVersionORM,
)
from agents.sales_agent.service.common import SalesAgentError, _clean_list, _dumps, _loads

RECOMMENDATION_PROFILE_EVENTS = frozenset({"created", "revised", "renamed", "archived"})
RECOMMENDATION_PROFILE_BRAND_REF_SELLER = "seller_profile"
RECOMMENDATION_PROFILE_BRAND_REF_NONE = "none"
RECOMMENDATION_PROFILE_BRAND_REF_CHAT_DRAFT = "chat_brand_draft"

_PROFILE_LIST_FIELDS = {
    "service_capabilities_json": "service_capabilities",
    "service_scope_json": "service_scope",
    "typical_customers_json": "typical_customers",
    "target_industries_json": "target_industries",
    "target_regions_json": "target_regions",
    "business_hint_json": "business_hint",
}


def _json_list(row: SalesAgentRecommendationProfileVersionORM, attr: str) -> list[str]:
    return _clean_list(_loads(getattr(row, attr, "[]"), []), 30)


def serialize_recommendation_profile_version(row: SalesAgentRecommendationProfileVersionORM | None) -> dict[str, Any] | None:
    """Convert a recommendation profile version into a structure usable by the API/cards."""
    if row is None:
        return None
    data: dict[str, Any] = {
        "id": row.id,
        "profile_key": row.profile_key,
        "user_id": row.user_id,
        "seller_profile_id": row.seller_profile_id,
        "brand_ref_type": row.brand_ref_type,
        "brand_ref_key": row.brand_ref_key,
        "brand_name_snapshot": row.brand_name_snapshot,
        "brand_context": _loads(row.brand_context_json, {}),
        "version": row.version,
        "event_type": row.event_type,
        "name": row.name,
        "source": row.source,
        "company_size": row.company_size,
        "target_company_size": row.target_company_size,
        "summary": row.summary,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    for attr, key in _PROFILE_LIST_FIELDS.items():
        data[key] = _json_list(row, attr)
    return data


def recommendation_profile_default_name(flow: dict[str, Any] | None = None, brand_name: str = "") -> str:
    """Generate an editable default name for a recommendation profile.

    The name **must not include filter conditions**: a brand has only one profile, conditions get overwritten in place by later rounds, but the name stays
    put; if round one is called "manufacturing recommendation profile" and the conditions later change to healthcare, the name stays and no longer matches.
    So it follows the brand only. `flow` is kept only for compatibility with existing call signatures.
    """
    name = str(brand_name or "").strip()
    return f"{name}的推荐条件"[:128] if name else "本次推荐画像"


def recommendation_profile_summary_from_flow(flow: dict[str, Any] | None) -> str:
    """Generate the short description on the selection card."""
    flow = flow or {}
    parts: list[str] = []
    industries = "、".join(_clean_list(flow.get("target_industries"), 3))
    regions = "、".join(_clean_list(flow.get("target_regions"), 3))
    size = str(flow.get("target_company_size") or "").strip()
    capabilities = "、".join(_clean_list(flow.get("seller_capabilities"), 2))
    if capabilities:
        parts.append(f"能力:{capabilities}")
    if industries:
        parts.append(f"行业:{industries}")
    if regions:
        parts.append(f"地区:{regions}")
    if size:
        parts.append(f"规模:{size}")
    return "；".join(parts)[:240]


def get_recommendation_profile_version(
    db: Session,
    user_id: int,
    profile_key: str,
    version_id: int | None = None,
) -> SalesAgentRecommendationProfileVersionORM | None:
    """Get a user-visible recommendation profile version; does not return an archived latest profile."""
    key = str(profile_key or "").strip()
    if not key:
        return None
    query = db.query(SalesAgentRecommendationProfileVersionORM).filter(
        SalesAgentRecommendationProfileVersionORM.user_id == user_id,
        SalesAgentRecommendationProfileVersionORM.profile_key == key,
    )
    if version_id is not None:
        query = query.filter(SalesAgentRecommendationProfileVersionORM.id == int(version_id))
    row = query.order_by(SalesAgentRecommendationProfileVersionORM.version.desc(), SalesAgentRecommendationProfileVersionORM.id.desc()).first()
    return row if row is not None and row.event_type != "archived" else None


def _latest_rows_by_profile_key(rows: list[SalesAgentRecommendationProfileVersionORM]) -> list[SalesAgentRecommendationProfileVersionORM]:
    latest: dict[str, SalesAgentRecommendationProfileVersionORM] = {}
    for row in rows:
        key = str(row.profile_key or "").strip()
        if not key:
            continue
        prev = latest.get(key)
        if prev is None or (int(row.version or 0), int(row.id or 0)) > (int(prev.version or 0), int(prev.id or 0)):
            latest[key] = row
    return [row for row in latest.values() if row.event_type != "archived"]


def list_active_recommendation_profiles(
    db: Session,
    user_id: int,
    seller_profile_id: int | None = None,
    *,
    include_unbound: bool = True,
    brand_ref_type: str | None = None,
    brand_ref_key: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List the latest valid recommendation profile per profile_key.

    When `seller_profile_id` is passed, generic profiles are included by default, for the "brand profile + generic recommendation profile" selection card;
    `brand_ref_type/key` is a filter entry reserved for future multi-brand chat drafts.
    """
    query = db.query(SalesAgentRecommendationProfileVersionORM).filter(
        SalesAgentRecommendationProfileVersionORM.user_id == user_id,
    )
    if brand_ref_type is not None:
        query = query.filter(SalesAgentRecommendationProfileVersionORM.brand_ref_type == str(brand_ref_type or ""))
    if brand_ref_key is not None:
        query = query.filter(SalesAgentRecommendationProfileVersionORM.brand_ref_key == str(brand_ref_key or ""))
    rows = query.order_by(SalesAgentRecommendationProfileVersionORM.created_at.desc(), SalesAgentRecommendationProfileVersionORM.id.desc()).all()
    active = _latest_rows_by_profile_key(rows)
    if seller_profile_id is not None:
        sid = int(seller_profile_id)
        active = [row for row in active if row.seller_profile_id == sid or (include_unbound and row.seller_profile_id is None)]
    payloads = [payload for payload in (serialize_recommendation_profile_version(row) for row in active) if payload]
    payloads.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)), reverse=True)
    return payloads[: max(1, int(limit or 50))]


def _flow_payload(flow: dict[str, Any] | None) -> dict[str, Any]:
    flow = flow or {}
    return {
        "service_capabilities": _clean_list(flow.get("seller_capabilities"), 30),
        "service_scope": _clean_list(flow.get("service_scope"), 30),
        "company_size": str(flow.get("seller_team") or flow.get("seller_team_raw") or "").strip(),
        "typical_customers": _clean_list(flow.get("typical_customers"), 30),
        "target_industries": _clean_list(flow.get("target_industries"), 30),
        "target_regions": _clean_list(flow.get("target_regions"), 30),
        "target_company_size": str(flow.get("target_company_size") or "").strip(),
        "business_hint": _clean_list(flow.get("business_hint"), 20),
    }


def _brand_ref_for_seller(seller_profile_id: int | None, brand_ref_type: str, brand_ref_key: str) -> tuple[str, str]:
    if seller_profile_id:
        return RECOMMENDATION_PROFILE_BRAND_REF_SELLER, brand_ref_key or f"seller_profile:{int(seller_profile_id)}"
    return (brand_ref_type or RECOMMENDATION_PROFILE_BRAND_REF_NONE), (brand_ref_key or "")


def _ensure_flow_payload_savable(payload: dict[str, Any]) -> None:
    """Two hard validations before saving; shared by create and upsert so error wording stays consistent."""
    if not payload["service_capabilities"] and not payload["business_hint"]:
        raise SalesAgentError("还没有可保存的服务能力或业务方向,请先确认你们主要提供什么服务或产品。")
    if not payload["target_industries"] or not payload["target_regions"] or not payload["target_company_size"]:
        raise SalesAgentError("请先确认目标行业、目标地区和目标企业规模,再保存为本次推荐画像。")


def _apply_flow_payload_to_row(
    row: SalesAgentRecommendationProfileVersionORM,
    payload: dict[str, Any],
    flow: dict[str, Any] | None,
) -> None:
    """Write the flow's 8 fields + summary into the profile row in place; does not touch name/version/created_at."""
    row.service_capabilities_json = _dumps(payload["service_capabilities"])
    row.service_scope_json = _dumps(payload["service_scope"])
    row.company_size = payload["company_size"][:128]
    row.typical_customers_json = _dumps(payload["typical_customers"])
    row.target_industries_json = _dumps(payload["target_industries"])
    row.target_regions_json = _dumps(payload["target_regions"])
    row.target_company_size = payload["target_company_size"][:128]
    row.business_hint_json = _dumps(payload["business_hint"])
    row.summary = recommendation_profile_summary_from_flow(flow)


def _active_recommendation_profile_row(
    db: Session,
    user_id: int,
    brand_ref_type: str,
    brand_ref_key: str,
) -> SalesAgentRecommendationProfileVersionORM | None:
    """Get this brand's current active recommendation profile.

    Uses `_latest_rows_by_profile_key` instead of filtering `event_type != 'archived'` directly in SQL:
    in legacy data of users not yet cleaned up, a profile_key may still hold an old version chain of v1 created + v2 archived,
    and row-level filtering would pick up v1 of an archived profile as active.
    """
    rows = (
        db.query(SalesAgentRecommendationProfileVersionORM)
        .filter(
            SalesAgentRecommendationProfileVersionORM.user_id == user_id,
            SalesAgentRecommendationProfileVersionORM.brand_ref_type == str(brand_ref_type or ""),
            SalesAgentRecommendationProfileVersionORM.brand_ref_key == str(brand_ref_key or ""),
        )
        .all()
    )
    active = _latest_rows_by_profile_key(rows)
    if not active:
        return None
    # Legacy data may have several per brand (only some user_ids were cleaned before launch): take the latest; old ones stay frozen and stop growing.
    active.sort(key=lambda row: (row.created_at or datetime.min, int(row.id or 0)), reverse=True)
    return active[0]


def create_recommendation_profile_from_flow(
    db: Session,
    user_id: int,
    flow: dict[str, Any] | None,
    name: str = "",
    *,
    seller_profile_id: int | None = None,
    brand_ref_type: str = RECOMMENDATION_PROFILE_BRAND_REF_NONE,
    brand_ref_key: str = "",
    brand_name_snapshot: str = "",
    brand_context: dict[str, Any] | None = None,
    source: str = "ai_assisted",
    commit: bool = True,
) -> SalesAgentRecommendationProfileVersionORM:
    """Create a new recommendation profile; INSERT only.

    Only reached when the user explicitly clicks save (REST `POST /recommendation-profiles`, the save action in chat).
    The automatic path on each successful recommendation should use `upsert_recommendation_profile_from_flow`, otherwise a brand
    accumulates N nearly identical profiles nobody reads.
    """
    payload = _flow_payload(flow)
    _ensure_flow_payload_savable(payload)
    ref_type, ref_key = _brand_ref_for_seller(seller_profile_id, brand_ref_type, brand_ref_key)
    row = SalesAgentRecommendationProfileVersionORM(
        profile_key=str(uuid.uuid4()),
        user_id=user_id,
        seller_profile_id=seller_profile_id,
        brand_ref_type=ref_type,
        brand_ref_key=ref_key,
        brand_name_snapshot=(brand_name_snapshot or "")[:128],
        brand_context_json=_dumps(brand_context or {}),
        version=1,
        event_type="created",
        name=(name or recommendation_profile_default_name(flow, brand_name_snapshot))[:128],
        source=(source or "chat")[:32],
        service_capabilities_json=_dumps(payload["service_capabilities"]),
        service_scope_json=_dumps(payload["service_scope"]),
        company_size=payload["company_size"][:128],
        typical_customers_json=_dumps(payload["typical_customers"]),
        target_industries_json=_dumps(payload["target_industries"]),
        target_regions_json=_dumps(payload["target_regions"]),
        target_company_size=payload["target_company_size"][:128],
        business_hint_json=_dumps(payload["business_hint"]),
        summary=recommendation_profile_summary_from_flow(flow),
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def upsert_recommendation_profile_from_flow(
    db: Session,
    user_id: int,
    flow: dict[str, Any] | None,
    name: str = "",
    *,
    seller_profile_id: int | None = None,
    brand_ref_type: str = RECOMMENDATION_PROFILE_BRAND_REF_NONE,
    brand_ref_key: str = "",
    brand_name_snapshot: str = "",
    brand_context: dict[str, Any] | None = None,
    source: str = "ai_assisted",
    commit: bool = True,
) -> SalesAgentRecommendationProfileVersionORM:
    """One per brand: update in place if the brand has an active profile, create only if not.

    The automatic path (each successful recommendation) goes here. In-place overwrites do not affect old emails: resend filters read
    the immutable snapshot in `sales_agent_recommendation_campaigns`, not this table.

    **Does not change name on existing rows**: the name is generated only on first creation and changes only via user rename,
    so no "has it been renamed" flag column is needed.
    """
    payload = _flow_payload(flow)
    _ensure_flow_payload_savable(payload)
    ref_type, ref_key = _brand_ref_for_seller(seller_profile_id, brand_ref_type, brand_ref_key)
    row = _active_recommendation_profile_row(db, user_id, ref_type, ref_key)
    if row is None:
        return create_recommendation_profile_from_flow(
            db,
            user_id,
            flow,
            name,
            seller_profile_id=seller_profile_id,
            brand_ref_type=brand_ref_type,
            brand_ref_key=brand_ref_key,
            brand_name_snapshot=brand_name_snapshot,
            brand_context=brand_context,
            source=source,
            commit=commit,
        )
    _apply_flow_payload_to_row(row, payload, flow)
    if seller_profile_id:
        row.seller_profile_id = int(seller_profile_id)
    if brand_name_snapshot:
        row.brand_name_snapshot = str(brand_name_snapshot)[:128]
    if brand_context:
        row.brand_context_json = _dumps(brand_context)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def _active_profile_row_or_error(db: Session, user_id: int, profile_key: str) -> SalesAgentRecommendationProfileVersionORM:
    row = get_recommendation_profile_version(db, user_id, profile_key)
    if row is None:
        raise SalesAgentError("推荐画像不存在或已归档。", status_code=404)
    return row


def _commit_profile_row(db: Session, row: SalesAgentRecommendationProfileVersionORM, commit: bool) -> SalesAgentRecommendationProfileVersionORM:
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def revise_recommendation_profile_from_flow(db: Session, user_id: int, profile_key: str, flow: dict[str, Any], *, commit: bool = True) -> SalesAgentRecommendationProfileVersionORM:
    """Update profile content in place; no longer appends a version."""
    row = _active_profile_row_or_error(db, user_id, profile_key)
    _apply_flow_payload_to_row(row, _flow_payload(flow), flow)
    row.event_type = "revised"
    return _commit_profile_row(db, row, commit)


def rename_recommendation_profile(db: Session, user_id: int, profile_key: str, name: str, *, commit: bool = True) -> SalesAgentRecommendationProfileVersionORM:
    """Rename in place; no longer appends a version."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise SalesAgentError("推荐画像名称不能为空。")
    row = _active_profile_row_or_error(db, user_id, profile_key)
    row.name = clean_name[:128]
    row.event_type = "renamed"
    return _commit_profile_row(db, row, commit)


def archive_recommendation_profile(db: Session, user_id: int, profile_key: str, *, commit: bool = True) -> SalesAgentRecommendationProfileVersionORM:
    """Mark archived in place; list queries hide the profile and the table gains no rows."""
    row = _active_profile_row_or_error(db, user_id, profile_key)
    row.event_type = "archived"
    return _commit_profile_row(db, row, commit)


def record_recommendation_profile_usage(
    db: Session,
    user_id: int,
    profile_key: str,
    profile_version_id: int,
    *,
    session_id: int | None = None,
    campaign_id: int | None = None,
    commit: bool = True,
) -> SalesAgentRecommendationProfileUsageORM:
    """Insert a recommendation profile usage log entry, replacing last_used_at updates."""
    row = SalesAgentRecommendationProfileUsageORM(
        profile_key=str(profile_key or "").strip(),
        profile_version_id=int(profile_version_id),
        user_id=user_id,
        session_id=session_id,
        campaign_id=campaign_id,
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def recommendation_profile_for_campaign(db: Session, user_id: int, campaign_id: int | None) -> dict[str, Any] | None:
    """Get the recommendation profile snapshot used by a recommendation task; reads usage/version only, does not update the profile."""
    if not campaign_id:
        return None
    usage = (
        db.query(SalesAgentRecommendationProfileUsageORM)
        .filter(
            SalesAgentRecommendationProfileUsageORM.user_id == user_id,
            SalesAgentRecommendationProfileUsageORM.campaign_id == int(campaign_id),
        )
        .order_by(SalesAgentRecommendationProfileUsageORM.used_at.desc(), SalesAgentRecommendationProfileUsageORM.id.desc())
        .first()
    )
    if usage is None:
        return None
    row = (
        db.query(SalesAgentRecommendationProfileVersionORM)
        .filter(
            SalesAgentRecommendationProfileVersionORM.user_id == user_id,
            SalesAgentRecommendationProfileVersionORM.id == usage.profile_version_id,
        )
        .first()
    )
    return serialize_recommendation_profile_version(row)


def apply_recommendation_profile_to_flow(flow: dict[str, Any], profile: dict[str, Any] | SalesAgentRecommendationProfileVersionORM) -> None:
    """Write the recommendation profile snapshot into the current lead_flow, for echo and this round's recommendation."""
    data = serialize_recommendation_profile_version(profile) if isinstance(profile, SalesAgentRecommendationProfileVersionORM) else profile
    if not isinstance(data, dict):
        return
    mapping = {
        "service_capabilities": "seller_capabilities",
        "service_scope": "service_scope",
        "company_size": "seller_team",
        "typical_customers": "typical_customers",
        "target_company_size": "target_company_size",
        "target_industries": "target_industries",
        "target_regions": "target_regions",
        "business_hint": "business_hint",
    }
    for source, target in mapping.items():
        value = data.get(source)
        if isinstance(value, list):
            cleaned = _clean_list(value, 30)
            if cleaned:
                flow[target] = cleaned
        elif str(value or "").strip():
            flow[target] = str(value or "").strip()
    if flow.get("seller_team"):
        flow["seller_team_raw"] = str(flow.get("seller_team") or "")
    flow["recommendation_profile_key"] = str(data.get("profile_key") or "")
    flow["recommendation_profile_version_id"] = int(data.get("id") or 0)
