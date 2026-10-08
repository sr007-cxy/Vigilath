"""Sales Agent service layer: profiles: read/write and copy for seller profile / target customer ICP / buyer personas."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from sqlalchemy import or_
from sqlalchemy.orm import Session
from agents.sales_agent.constants import DEFAULT_BUYER_ROLES, DEFAULT_EXCLUDE_RULES, DEFAULT_PROFILE_REGIONS
from agents.sales_agent.models import (
    SalesAgentAccountICPORM,
    SalesAgentBuyerPersonaORM,
    SalesAgentCustomerProfileORM,
    SalesAgentLeadEvidenceORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)

from agents.sales_agent.service.common import (
    SalesAgentError,
    _clean_list,
    _clean_profile_option_list,
    _dumps,
    _loads,
    _normalize_report_table,
    _profile_phrase,
    record_compliance_event,
    texts_are_report_capable,
    usable_brand_name,
)
from agents.sales_agent.service.entities import _campaign_or_latest
from agents.sales_agent.service.companies import _usable_profile_industries
from agents.sales_agent.service.reports import get_report_summary


def _profile_summary_text(
    service_scope: list[Any],
    company_size: str,
    typical_customers: list[Any],
    buyer_roles: list[Any],
    service_capabilities: list[Any] | None = None,
) -> str:
    """Profile summary, fed into the email generation context.

    `service_capabilities` was added later: previously, with no team size filled, it always said "team size or professional capability not configured",
    but capability and size are different things; saying a long list of capabilities is "not configured" is a **factual error**,
    and this sentence goes verbatim into the email prompt. Size is optional (any of capability/scope/evidence suffices),
    so capabilities taking its place is more accurate.
    """
    services = _profile_phrase(service_scope, "服务范围待补充", limit=4)
    scale = (company_size or "").strip()
    capability_phrase = _profile_phrase(service_capabilities or [], "", limit=3)
    if scale:
        scale_clause = f"规模为{scale}"
    elif capability_phrase:
        scale_clause = f"团队具备{capability_phrase}的能力"
    else:
        scale_clause = "团队规模或专业能力未配置"
    customers = _profile_phrase(typical_customers, "典型客户待补充", limit=3)
    roles = _profile_phrase(buyer_roles, "决策角色待补充", limit=3)
    return f"您是一家提供{services}的公司/团队,{scale_clause},可围绕这些服务设计获客切入点;更适合服务{customers},通常对接{roles}。"


def serialize_profile(row: SalesAgentCustomerProfileORM) -> dict[str, Any]:
    service_scope = _loads(row.service_scope_json, [])
    industries = _loads(row.industries_json, [])
    regions = _loads(row.regions_json, [])
    buyer_roles = _loads(row.buyer_roles_json, [])
    exclude_rules = _loads(row.exclude_rules_json, [])
    typical_customers = _loads(row.typical_customers_json, [])
    summary = _profile_summary_text(service_scope, row.company_size, typical_customers, buyer_roles)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "source_report_table": row.source_report_table,
        "source_report_id": row.source_report_id,
        "report_snapshot": _loads(row.report_snapshot, {}),
        "service_scope": service_scope,
        "industries": industries,
        "regions": regions,
        "company_size": row.company_size,
        "buyer_roles": buyer_roles,
        "exclude_rules": exclude_rules,
        "typical_customers": typical_customers,
        "self_profile": {
            "description": "用户自身服务画像,用于回答“我是谁/我的公司是什么样/能提供什么服务”。",
            "service_scope": service_scope,
            "company_scale": row.company_size,
            "typical_customers": typical_customers,
            "buyer_roles": buyer_roles,
            "exclude_rules": exclude_rules,
            "summary": summary,
        },
        "targeting_context": {
            "description": "推荐企业时的临时筛选条件,不等同于用户自身画像;新需求需要重新确认。",
            "industries": industries,
            "regions": regions,
        },
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_seller_profile(row: SalesAgentSellerProfileORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    service_scope = _loads(row.service_scope_json, [])
    typical_customers = _loads(row.typical_customers_json, [])
    buyer_roles = _loads(row.default_buyer_roles_json, [])
    summary = row.summary or _profile_summary_text(
        service_scope, row.company_size, typical_customers, buyer_roles, _loads(row.service_capabilities_json, [])
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "legacy_profile_id": row.legacy_profile_id,
        "topic_id": int(row.topic_id) if row.topic_id else None,
        # Both items below are written by brandprofile's syncer from the brand profile; read-only here.
        "has_profile": bool(row.topic_id),
        "report_capable": bool(row.report_capable),
        "name": row.name,
        "brand_key": row.brand_key or "",
        "sort_order": row.sort_order or 0,
        "archived": row.archived_at is not None,
        "source_report_table": row.source_report_table,
        "source_report_id": row.source_report_id,
        "report_snapshot": _loads(row.report_snapshot, {}),
        "service_scope": service_scope,
        "service_capabilities": _loads(row.service_capabilities_json, []),
        "company_size": row.company_size,
        "typical_customers": typical_customers,
        "proof_points": _loads(row.proof_points_json, []),
        "default_buyer_roles": buyer_roles,
        "default_exclude_rules": _loads(row.default_exclude_rules_json, []),
        "summary": summary,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_account_icp(row: SalesAgentAccountICPORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "seller_profile_id": row.seller_profile_id,
        "legacy_profile_id": row.legacy_profile_id,
        "name": row.name,
        "target_company_types": _loads(row.target_company_types_json, []),
        "target_industries": _loads(row.target_industries_json, []),
        "target_regions": _loads(row.target_regions_json, []),
        "target_company_size": row.target_company_size,
        "target_stage": _loads(row.target_stage_json, []),
        "observable_signals": _loads(row.observable_signals_json, []),
        "pain_signals": _loads(row.pain_signals_json, []),
        "budget_signals": _loads(row.budget_signals_json, []),
        "negative_icp": _loads(row.negative_icp_json, []),
        "scoring_rubric": _loads(row.scoring_rubric_json, {}),
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_buyer_persona(row: SalesAgentBuyerPersonaORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "seller_profile_id": row.seller_profile_id,
        "account_icp_id": row.account_icp_id,
        "role_name": row.role_name,
        "titles": _loads(row.titles_json, []),
        "decision_role": row.decision_role,
        "goals": _loads(row.goals_json, []),
        "kpis": _loads(row.kpis_json, []),
        "pains": _loads(row.pains_json, []),
        "objections": _loads(row.objections_json, []),
        "messaging_angles": _loads(row.messaging_angles_json, []),
        "proof_needed": _loads(row.proof_needed_json, []),
        "content_preferences": _loads(row.content_preferences_json, []),
        "priority": row.priority,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_recommendation_campaign(row: SalesAgentRecommendationCampaignORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "seller_profile_id": row.seller_profile_id,
        "account_icp_id": row.account_icp_id,
        "legacy_profile_id": row.legacy_profile_id,
        "source_report_table": row.source_report_table,
        "source_report_id": row.source_report_id,
        "request_text": row.request_text,
        "target_industries": _loads(row.target_industries_json, []),
        "target_regions": _loads(row.target_regions_json, []),
        "target_company_size": row.target_company_size,
        "service_scope_override": _loads(row.service_scope_override_json, []),
        "limit": row.limit,
        "seed_companies": _loads(row.seed_companies_json, []),
        "contact_preference": _loads(row.contact_preference_json, []),
        "status": row.status,
        "search_available": row.search_available,
        "requested_limit": row.requested_limit,
        "actual_count": row.actual_count,
        "shortfall": row.shortfall,
        "missing_config": _loads(row.missing_config_json, []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_lead_evidence(row: SalesAgentLeadEvidenceORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "campaign_id": row.campaign_id,
        "lead_recommendation_id": row.lead_recommendation_id,
        "company_id": row.company_id,
        "evidence_type": row.evidence_type,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_report_table": row.source_report_table,
        "source_report_id": row.source_report_id,
        "evidence_text": row.evidence_text,
        "scoring_category": row.scoring_category,
        "score_delta": row.score_delta,
        "confidence": row.confidence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _latest_seller_profile(db: Session, user_id: int, seller_profile_id: int | None = None) -> SalesAgentSellerProfileORM | None:
    """Fetch by id when named; otherwise take the latest **active** brand.

    "Archived brands are only excluded when not named" is intentional: historical recommendation tasks / sent emails carry
    `campaign.seller_profile_id`, and that brand must still be readable after being archived, otherwise the "our profile"
    section in old reports goes blank. Named = we know exactly which row we want; archive status should not block it.
    """
    q = db.query(SalesAgentSellerProfileORM).filter(SalesAgentSellerProfileORM.user_id == user_id)
    if seller_profile_id is not None:
        q = q.filter(SalesAgentSellerProfileORM.id == int(seller_profile_id))
    else:
        q = q.filter(SalesAgentSellerProfileORM.archived_at.is_(None))
    return q.order_by(SalesAgentSellerProfileORM.id.desc()).first()


def _latest_account_icp(db: Session, user_id: int, account_icp_id: int | None = None, seller_profile_id: int | None = None) -> SalesAgentAccountICPORM | None:
    """The two filter conditions are **combined**, not either/or.

    This used to be `if account_icp_id / elif seller_profile_id`, so in the five places passing both
    (recommend.py 528/598/732/942, outreach.py:140) `seller_profile_id` was a dead parameter:
    it looked like brand isolation but only validated `user_id`. And `account_icp_id` is **echoed back by the model**
    (tools.py requires the LLM to carry it throughout the same recommendation flow); in long multi-brand sessions the model repeated
    the id from brand A's earlier round, and brand B's recommendation silently ran on A's ICP. Same failure mode as da38e54e's
    "two brands reading the same row", through a different door.

    With combined filters, an id from the wrong brand simply is not found -> takes the caller's existing 404 branch, no guessing.

    `seller_profile_id IS NULL` is also allowed: those are legacy rows from before the seller_profile table existed;
    having no owner does not mean belonging to another brand, and blocking them would 404 old accounts.
    """
    q = db.query(SalesAgentAccountICPORM).filter(SalesAgentAccountICPORM.user_id == user_id)
    if account_icp_id is not None:
        q = q.filter(SalesAgentAccountICPORM.id == int(account_icp_id))
    if seller_profile_id is not None:
        q = q.filter(
            or_(
                SalesAgentAccountICPORM.seller_profile_id == int(seller_profile_id),
                SalesAgentAccountICPORM.seller_profile_id.is_(None),
            )
        )
    return q.order_by(SalesAgentAccountICPORM.id.desc()).first()


def _buyer_personas_for_context(
    db: Session,
    user_id: int,
    seller_profile_id: int | None = None,
    account_icp_id: int | None = None,
    limit: int = 10,
) -> list[SalesAgentBuyerPersonaORM]:
    q = db.query(SalesAgentBuyerPersonaORM).filter(SalesAgentBuyerPersonaORM.user_id == user_id)
    if account_icp_id is not None:
        q = q.filter(SalesAgentBuyerPersonaORM.account_icp_id == int(account_icp_id))
    elif seller_profile_id is not None:
        q = q.filter(SalesAgentBuyerPersonaORM.seller_profile_id == int(seller_profile_id))
    return q.order_by(SalesAgentBuyerPersonaORM.priority.asc(), SalesAgentBuyerPersonaORM.id.asc()).limit(limit).all()


def _legacy_seller_profile_payload(profile: SalesAgentCustomerProfileORM | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    service_scope = _loads(profile.service_scope_json, [])
    typical_customers = _loads(profile.typical_customers_json, [])
    buyer_roles = _loads(profile.buyer_roles_json, [])
    return {
        "id": None,
        "user_id": profile.user_id,
        "legacy_profile_id": profile.id,
        "name": "旧版卖家服务画像",
        "source_report_table": profile.source_report_table,
        "source_report_id": profile.source_report_id,
        "report_snapshot": _loads(profile.report_snapshot, {}),
        "service_scope": service_scope,
        "service_capabilities": [],
        "company_size": profile.company_size,
        "typical_customers": typical_customers,
        "proof_points": [],
        "default_buyer_roles": buyer_roles,
        "default_exclude_rules": _loads(profile.exclude_rules_json, []),
        "summary": _profile_summary_text(service_scope, profile.company_size, typical_customers, buyer_roles),
        "source": "legacy_customer_profile",
    }


def _legacy_account_icp_payload(profile: SalesAgentCustomerProfileORM | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "id": None,
        "user_id": profile.user_id,
        "seller_profile_id": None,
        "legacy_profile_id": profile.id,
        "name": "旧版目标公司 ICP",
        "target_company_types": _loads(profile.typical_customers_json, []),
        "target_industries": _loads(profile.industries_json, []),
        "target_regions": _loads(profile.regions_json, []),
        "target_company_size": "",
        "target_stage": [],
        "observable_signals": [],
        "pain_signals": [],
        "budget_signals": [],
        "negative_icp": _loads(profile.exclude_rules_json, []),
        "scoring_rubric": {},
        "source": "legacy_customer_profile",
    }


def _legacy_buyer_persona_payloads(profile: SalesAgentCustomerProfileORM | None) -> list[dict[str, Any]]:
    if profile is None:
        return []
    roles = _loads(profile.buyer_roles_json, []) or DEFAULT_BUYER_ROLES
    return [
        {
            "id": None,
            "user_id": profile.user_id,
            "seller_profile_id": None,
            "account_icp_id": None,
            "role_name": role,
            "titles": [role],
            "decision_role": "influencer",
            "goals": [],
            "kpis": [],
            "pains": [],
            "objections": [],
            "messaging_angles": [],
            "proof_needed": [],
            "content_preferences": [],
            "priority": idx + 1,
            "source": "legacy_customer_profile",
        }
        for idx, role in enumerate(roles)
    ]


# --------------------------------------------------------------------------- brands
# One seller_profile row = one brand (= one business line). When an account sells both A and B,
# their capabilities/target customers/service scopes are completely different, so the profile is not an account-level singleton.

BRAND_NAME_MAX = 64
BRAND_LIMIT = 20
# Limit for brand-specific email instructions. Truncation is required: this text is spliced verbatim into the generation prompt,
# and without a limit we would hand over control of the whole prompt, pushing out the hard compliance rules.


def _active_brand_query(db: Session, user_id: int):
    return (
        db.query(SalesAgentSellerProfileORM)
        .filter(
            SalesAgentSellerProfileORM.user_id == user_id,
            SalesAgentSellerProfileORM.archived_at.is_(None),
        )
        .order_by(SalesAgentSellerProfileORM.sort_order.asc(), SalesAgentSellerProfileORM.id.asc())
    )


def _new_brand_key(db: Session, user_id: int) -> str:
    """A stable reference unique within the account.

    Archived rows count as taken too: the unique index `(user_id, brand_key)` ignores `archived_at`,
    and reusing an archived brand's key would hit the index immediately.
    """
    used = {
        str(key or "")
        for (key,) in db.query(SalesAgentSellerProfileORM.brand_key)
        .filter(SalesAgentSellerProfileORM.user_id == user_id)
        .all()
    }
    idx = len(used) + 1
    # The prefix deliberately differs from the migration backfill's `brand-{id}`, so the two formats never collide
    while f"brand-n{idx}" in used:
        idx += 1
    return f"brand-n{idx}"


def _next_brand_sort_order(db: Session, user_id: int) -> int:
    rows = _active_brand_query(db, user_id).all()
    return max((row.sort_order or 0) for row in rows) + 10 if rows else 100


# The gray line on cards for legacy brands that have not claimed a brand profile. These brands can still be selected, run recommendations and send emails;
# their content just relies on manual sales-side input. The label tells users where to fill it in; it does not disable the brand.
BRAND_NO_PROFILE_HEADLINE = "未关联品牌资料"


def _brand_headline(capabilities: list[str], company_size: str) -> str:
    """The gray line on the brand card. Empty parts take no space: "· ·" looks worse than nothing."""
    parts = [text for text in ("、".join(capabilities[:3]), company_size) if text]
    return " · ".join(parts) or "画像待补充"


def serialize_brand(row: SalesAgentSellerProfileORM | None) -> dict[str, Any] | None:
    """Slim payload for brand cards / brand lists, lighter than the full profile.

    The five content fields are a **mirror**; the source of truth is `ai_telemetry_topics.profile_json`. Whatever is read here
    is what the syncer last wrote; do not recompute it here.
    """
    payload = serialize_seller_profile(row)
    if payload is None:
        return None
    capabilities = _usable_profile_list(payload.get("service_capabilities"), 30)
    scope = _usable_profile_list(payload.get("service_scope"), 20)
    company_size = _usable_profile_text(payload.get("company_size"))
    has_profile = bool(payload.get("has_profile"))
    return {
        "id": payload.get("id"),
        "name": payload.get("name") or "默认品牌",
        "brand_key": payload.get("brand_key") or "",
        "sort_order": payload.get("sort_order") or 0,
        "archived": bool(payload.get("archived")),
        "topic_id": payload.get("topic_id"),
        "has_profile": has_profile,
        "report_capable": bool(payload.get("report_capable")),
        "service_capabilities": capabilities,
        "service_scope": scope,
        "company_size": company_size,
        "typical_customers": _usable_profile_list(payload.get("typical_customers"), 20),
        "proof_points": _usable_profile_list(payload.get("proof_points"), 30),
        "summary": _usable_profile_text(payload.get("summary")),
        # Only join content, without mixing in "no linked brand profile": that is status, provided separately by `has_profile`,
        # and including it would push out the few entries an orphan brand filled in manually in /admin. The brand card subtitle
        # combines the two in `_brand_picker_subtitle`.
        "headline": _brand_headline(capabilities or scope, company_size),
        "updated_at": payload.get("updated_at"),
    }


def list_brands(db: Session, user_id: int, include_archived: bool = False) -> list[dict[str, Any]]:
    if include_archived:
        rows = (
            db.query(SalesAgentSellerProfileORM)
            .filter(SalesAgentSellerProfileORM.user_id == user_id)
            .order_by(SalesAgentSellerProfileORM.sort_order.asc(), SalesAgentSellerProfileORM.id.asc())
            .all()
        )
    else:
        rows = _active_brand_query(db, user_id).all()
    return [payload for payload in (serialize_brand(row) for row in rows) if payload]


def active_brand_count(db: Session, user_id: int) -> int:
    return _active_brand_query(db, user_id).count()


def resolve_active_brand_id(db: Session, user_id: int, flow: dict[str, Any] | None = None) -> int | None:
    """Which brand to use this time: the one selected in the session -> the only active brand -> None (show the selection card).

    The id from the session **must be re-validated against the DB**: `context_json` can be tampered with,
    and using it directly to fetch a profile would let someone read another account's brand. Archived brands are not accepted either:
    if a brand is archived mid-session, asking again is better than continuing with a deleted brand.
    """
    raw = 0
    if isinstance(flow, dict):
        try:
            raw = int(flow.get("brand_id") or 0)
        except (TypeError, ValueError):
            raw = 0
    rows = _active_brand_query(db, user_id).all()
    if raw:
        for row in rows:
            if row.id == raw:
                return row.id
    if len(rows) == 1:
        return rows[0].id
    return None


def _refresh_orphan_report_capability(row: SalesAgentSellerProfileORM) -> None:
    """After an orphan brand fills in capabilities, recompute whether it gets the report entry.

    Rows that claimed a profile are untouched: those are computed by `sync_brand_from_topic`.

    Without this step orphan brands are a dead end: `report_capable` defaults to False on row creation, and in the whole repo
    only the syncer writes it; legacy brands have no profile, the syncer never touches them, and the report button
    could never appear. With `/sales-agent/admin` removed, adding capabilities in chat is their only way out,
    so this recompute must follow `create_or_update_seller_profile`.
    """
    if row.topic_id:
        return
    row.report_capable = texts_are_report_capable([
        row.name or "",
        *_loads(row.service_capabilities_json, []),
        *_loads(row.service_scope_json, []),
    ])


def create_or_update_seller_profile(
    db: Session,
    user_id: int,
    seller_profile_id: int | None = None,
    legacy_profile_id: int | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
    report_snapshot: dict[str, Any] | None = None,
    service_scope: list[str] | None = None,
    service_capabilities: list[str] | None = None,
    company_size: str = "",
    typical_customers: list[str] | None = None,
    proof_points: list[str] | None = None,
    default_buyer_roles: list[str] | None = None,
    default_exclude_rules: list[str] | None = None,
    name: str = "",
    create_new: bool = False,
    commit: bool = True,
) -> SalesAgentSellerProfileORM:
    """Write the profile. **Defaults to the current active brand's row; no new row.**

    This used to be "if `seller_profile_id is None`, `db.add` a row", while the vast majority of callers
    (the per-round profile snapshot in the find-customers flow) cannot pass an id, so every profile edit added a row,
    which is how one user ended up with several rows in the existing DB. With multiple brands those rows would be listed as real brands,
    so this must be cut off: only an explicit `create_new=True` (really clicking 「新增品牌」 "add brand") inserts a row.

    `name` defaults to an empty string rather than 「默认品牌」 ("default brand"): none of the three callers pass name, and a hard-coded default would
    overwrite the user's brand name ("Brand A") on every chat turn. Empty = do not rename.

    **Rows that claimed a brand profile (non-empty `topic_id`) only write non-content columns**: the content's source of truth is
    `ai_telemetry_topics.profile_json`, and only `sync_brand_from_topic` may write it.
    """
    row = _latest_seller_profile(db, user_id, seller_profile_id) if seller_profile_id is not None else None
    if seller_profile_id is not None and row is None:
        raise SalesAgentError("卖家服务画像不存在或不属于当前用户。", status_code=404)
    if row is None and not create_new:
        # Not named -> use the current only/latest active brand; create one only if none exists
        row = _latest_seller_profile(db, user_id)
    scope = _clean_profile_option_list(service_scope, 20)
    typical = _clean_profile_option_list(typical_customers, 20)
    roles = _clean_list(default_buyer_roles, 20) or DEFAULT_BUYER_ROLES
    excludes = _clean_list(default_exclude_rules, 20) or DEFAULT_EXCLUDE_RULES
    capabilities = _clean_profile_option_list(service_capabilities, 30)
    summary = _profile_summary_text(scope, company_size, typical, roles, capabilities)
    if row is None:
        row = SalesAgentSellerProfileORM(
            user_id=user_id,
            brand_key=_new_brand_key(db, user_id),
            sort_order=_next_brand_sort_order(db, user_id),
        )
        db.add(row)
    row.legacy_profile_id = legacy_profile_id
    row.source_report_table = source_report_table or ""
    row.source_report_id = str(source_report_id or "")
    row.report_snapshot = _dumps(report_snapshot or {})
    # Buyer personas and exclusion rules belong to the sales side, with no counterpart in the profile form; written even when a profile is claimed.
    row.default_buyer_roles_json = _dumps(roles)
    row.default_exclude_rules_json = _dumps(excludes)
    if row.topic_id:
        # For rows that claimed a brand profile, the content is owned exclusively by `sync_brand_from_topic`.
        # This function **overwrites the whole row**, and the recommendation flow (recommend.py) calls it every round with
        # service_scope / company_size from the chat; without this guard one find-customers run
        # would change the service scope and team size synced from the profile to whatever was casually said in chat,
        # and the sales side has no way left to edit content, so users would see it but could not change it back.
        # No error raised: the caller was never trying to write content;
        # it wants the legacy_profile_id / report snapshot columns, so silently skipping content is the semantics it wants.
        row.summary = _profile_summary_text(
            _loads(row.service_scope_json, []),
            row.company_size or "",
            _loads(row.typical_customers_json, []),
            roles,
            _loads(row.service_capabilities_json, []),
        )
    else:
        row.name = (name or row.name or "默认品牌")[:128]
        row.service_scope_json = _dumps(scope)
        row.service_capabilities_json = _dumps(capabilities)
        row.company_size = (company_size or "").strip()
        row.typical_customers_json = _dumps(typical)
        row.proof_points_json = _dumps(_clean_list(proof_points, 30))
        row.summary = summary
        # Capabilities added in chat count too: orphan brands only have this and /admin as entry points.
        _refresh_orphan_report_capability(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def create_or_update_account_icp(
    db: Session,
    user_id: int,
    seller_profile_id: int | None = None,
    account_icp_id: int | None = None,
    legacy_profile_id: int | None = None,
    target_company_types: list[str] | None = None,
    target_industries: list[str] | None = None,
    target_regions: list[str] | None = None,
    target_company_size: str = "",
    negative_icp: list[str] | None = None,
    name: str = "默认目标公司 ICP",
    source: str = "manual",
    commit: bool = True,
) -> SalesAgentAccountICPORM:
    row = _latest_account_icp(db, user_id, account_icp_id) if account_icp_id is not None else None
    if account_icp_id is not None and row is None:
        raise SalesAgentError("目标公司 ICP 不存在或不属于当前用户。", status_code=404)
    if row is None:
        row = SalesAgentAccountICPORM(user_id=user_id)
        db.add(row)
    row.seller_profile_id = seller_profile_id
    row.legacy_profile_id = legacy_profile_id
    row.name = (name or "默认目标公司 ICP")[:128]
    row.target_company_types_json = _dumps(_clean_list(target_company_types, 30))
    row.target_industries_json = _dumps(_clean_list(target_industries, 30))
    row.target_regions_json = _dumps(_clean_list(target_regions, 30))
    row.target_company_size = (target_company_size or "").strip()
    row.negative_icp_json = _dumps(_clean_list(negative_icp, 30))
    row.source = (source or "manual")[:64]
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def create_default_buyer_personas(
    db: Session,
    user_id: int,
    seller_profile_id: int | None,
    account_icp_id: int | None,
    buyer_roles: list[str] | None,
    commit: bool = True,
) -> list[SalesAgentBuyerPersonaORM]:
    roles = _clean_list(buyer_roles, 20) or DEFAULT_BUYER_ROLES
    rows: list[SalesAgentBuyerPersonaORM] = []
    for idx, role in enumerate(roles):
        row = (
            db.query(SalesAgentBuyerPersonaORM)
            .filter(
                SalesAgentBuyerPersonaORM.user_id == user_id,
                SalesAgentBuyerPersonaORM.seller_profile_id == seller_profile_id,
                SalesAgentBuyerPersonaORM.account_icp_id == account_icp_id,
                SalesAgentBuyerPersonaORM.role_name == role,
            )
            .first()
        )
        if row is None:
            row = SalesAgentBuyerPersonaORM(
                user_id=user_id,
                seller_profile_id=seller_profile_id,
                account_icp_id=account_icp_id,
                role_name=role[:128],
                titles_json=_dumps([role]),
                decision_role="economic_buyer" if any(x in role for x in ("负责人", "CEO", "创始人", "CMO")) else "influencer",
                goals_json=_dumps(["提升获客效率", "提升品牌与内容可见度"]),
                pains_json=_dumps(["不确定品牌在 AI 搜索和传统搜索中的可见度", "缺少可直接行动的官网诊断证据"]),
                messaging_angles_json=_dumps(["先用真实检测报告定位官网与 AI 可见度机会", "用竞品和公开信号解释优化优先级"]),
                proof_needed_json=_dumps(["真实检测报告", "公开来源证据", "可执行优化建议"]),
                priority=idx + 1,
            )
            db.add(row)
        rows.append(row)
    db.flush()
    if commit:
        db.commit()
        for row in rows:
            db.refresh(row)
    return rows


def create_customer_profile(
    db: Session,
    user_id: int,
    source_report_table: str = "",
    source_report_id: str = "",
    service_scope: list[str] | None = None,
    service_capabilities: list[str] | None = None,
    industries: list[str] | None = None,
    regions: list[str] | None = None,
    company_size: str = "",
    buyer_roles: list[str] | None = None,
    exclude_rules: list[str] | None = None,
    typical_customers: list[str] | None = None,
    manual_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manual = manual_profile or {}
    report = get_report_summary(db, user_id, source_report_table, source_report_id) if (source_report_table or source_report_id) else {}
    report_ready = bool(report.get("has_real_detection"))
    scope = _clean_profile_option_list(service_scope or manual.get("service_scope"), 20)
    capabilities = _clean_profile_option_list(service_capabilities or manual.get("service_capabilities"), 30)
    if not scope and report_ready:
        scope = ["GEO/AEO 优化", "官网 SEO 诊断整改", "AI Visibility 提升"]
    inds = _clean_list(industries or manual.get("industries"), 20) or _clean_list(report.get("industry_terms"), 20) or []
    regs = _clean_list(regions or manual.get("regions"), 20)
    roles = _clean_list(buyer_roles or manual.get("buyer_roles"), 20) or DEFAULT_BUYER_ROLES
    excludes = _clean_list(exclude_rules or manual.get("exclude_rules"), 20) or DEFAULT_EXCLUDE_RULES
    typical = _clean_list(typical_customers or manual.get("typical_customers"), 20)
    row = SalesAgentCustomerProfileORM(
        user_id=user_id,
        source_report_table=report.get("source_report_table") or _normalize_report_table(source_report_table) if source_report_table else "",
        source_report_id=str(report.get("source_report_id") or source_report_id or ""),
        report_snapshot=_dumps(report),
        service_scope_json=_dumps(scope),
        industries_json=_dumps(inds),
        regions_json=_dumps(regs),
        company_size=(company_size or manual.get("company_size") or "").strip(),
        buyer_roles_json=_dumps(roles),
        exclude_rules_json=_dumps(excludes),
        typical_customers_json=_dumps(typical),
    )
    db.add(row)
    db.flush()
    seller = create_or_update_seller_profile(
        db,
        user_id,
        legacy_profile_id=row.id,
        source_report_table=row.source_report_table,
        source_report_id=row.source_report_id,
        report_snapshot=report,
        service_scope=scope,
        service_capabilities=capabilities,
        company_size=row.company_size,
        typical_customers=typical,
        default_buyer_roles=roles,
        default_exclude_rules=excludes,
        commit=False,
    )
    account_icp = create_or_update_account_icp(
        db,
        user_id,
        seller_profile_id=seller.id,
        legacy_profile_id=row.id,
        target_company_types=typical,
        target_industries=inds,
        target_regions=regs,
        target_company_size="",
        negative_icp=excludes,
        source="legacy_profile_split",
        commit=False,
    ) if (inds or regs or typical or excludes) else None
    personas = create_default_buyer_personas(db, user_id, seller.id, account_icp.id if account_icp else None, roles, commit=False)
    db.commit()
    db.refresh(row)
    db.refresh(seller)
    if account_icp is not None:
        db.refresh(account_icp)
    for persona in personas:
        db.refresh(persona)
    record_compliance_event(db, user_id, "create_customer_profile", "low", "allowed", "保存卖家服务画像并兼容旧客户画像", {"profile_id": row.id, "seller_profile_id": seller.id, "account_icp_id": account_icp.id if account_icp else None})
    data = serialize_profile(row)
    # Explicitly return the new and old profile IDs so the same recommendation flow can pass the just-saved profile to recommendation orchestration.
    data["profile_id"] = row.id
    data["seller_profile_id"] = seller.id
    data["account_icp_id"] = account_icp.id if account_icp else None
    data["seller_profile"] = serialize_seller_profile(seller)
    data["account_icp"] = serialize_account_icp(account_icp)
    data["buyer_personas"] = [serialize_buyer_persona(x) for x in personas]
    return data


def _public_profile_payload(profile: SalesAgentCustomerProfileORM | None) -> dict[str, Any]:
    return {
        "industries": _usable_profile_industries(profile),
        "regions": _loads(profile.regions_json, []) if profile else DEFAULT_PROFILE_REGIONS,
        "service_scope": _loads(profile.service_scope_json, []) if profile else [],
        "company_size": profile.company_size if profile else "",
    }


def _profile_or_latest(db: Session, user_id: int, profile_id: int | None = None) -> SalesAgentCustomerProfileORM | None:
    q = db.query(SalesAgentCustomerProfileORM).filter(SalesAgentCustomerProfileORM.user_id == user_id)
    if profile_id is not None:
        q = q.filter(SalesAgentCustomerProfileORM.id == int(profile_id))
    return q.order_by(SalesAgentCustomerProfileORM.id.desc()).first()


def get_current_customer_profile(
    db: Session,
    user_id: int,
    profile_id: int | None = None,
    seller_profile_id: int | None = None,
) -> dict[str, Any]:
    """Read the current user's latest or specified service profile; read-only, never creates or modifies.

    `seller_profile_id` is "which brand is being asked about". Without it everything below falls back to
    "the account's latest row"; in multi-brand accounts that is always the most recently created brand, so a user
    asking "what are my target customer types" in brand A's session would get B's
    (2026-08-26 user testing: two brands sharing one brand's customer types).
    The caller (tools.py) resolves it from the brand selected in the session and passes it in.
    """
    profile = _profile_or_latest(db, user_id, profile_id)
    if profile_id is not None and profile is None:
        raise SalesAgentError("客户画像不存在或不属于当前用户。", status_code=404)
    seller_row = _latest_seller_profile(db, user_id, seller_profile_id)
    account_row = _latest_account_icp(db, user_id, seller_profile_id=seller_row.id if seller_row else None)
    persona_rows = _buyer_personas_for_context(
        db,
        user_id,
        seller_profile_id=seller_row.id if seller_row else None,
        account_icp_id=account_row.id if account_row else None,
    )
    seller_profile = serialize_seller_profile(seller_row) or _legacy_seller_profile_payload(profile)
    account_icp = serialize_account_icp(account_row) or _legacy_account_icp_payload(profile)
    buyer_personas = [serialize_buyer_persona(x) for x in persona_rows] if persona_rows else _legacy_buyer_persona_payloads(profile)
    active_brand_id = seller_row.id if seller_row else None
    latest_campaign = _campaign_or_latest(db, user_id, seller_profile_id=active_brand_id)
    if latest_campaign is None and active_brand_id is not None:
        # Old accounts' recommendation tasks may have no brand at all (`seller_profile_id` is nullable). Those without a brand
        # cannot be "another brand's", so allow them; ones attached to another brand are still hidden.
        fallback = _campaign_or_latest(db, user_id)
        if fallback is not None and not fallback.seller_profile_id:
            latest_campaign = fallback
    if profile is None and seller_profile is None:
        return {
            "status": "none",
            "profile": None,
            "seller_profile": None,
            "account_icp": None,
            "buyer_personas": [],
            "latest_campaign": serialize_recommendation_campaign(latest_campaign),
            # The profile's source of truth has moved to the brand profile in the customer workspace. This sentence is the only fallback the model will repeat,
            # and still saying "please add service scope/team size" would make it collect them again in chat; what it collects
            # lands on the mirror row while the profile form stays empty.
            "hint": "当前还没有品牌资料,请先到客户工作台上传品牌资料(核心一句话、核心业务线、服务核心特点、团队规模、服务覆盖场景),销售助理会自动读取。",
            "answer_guidance": "回答用户画像时,应该说明用户是一家什么样的公司/团队、提供什么服务、规模和专业能力;不要展示目标行业/服务地区表格。缺内容时引导用户去客户工作台补品牌资料,不要在对话里逐项追问。",
        }
    data = serialize_profile(profile) if profile is not None else None
    if data is not None:
        snapshot = data.get("report_snapshot") or {}
        data["has_report_snapshot"] = bool(snapshot)
        data["has_real_detection"] = bool(snapshot.get("has_real_detection")) if isinstance(snapshot, dict) else False
        data["profile_source"] = "report" if data["has_real_detection"] else "manual"
        data["seller_profile"] = seller_profile
        data["account_icp"] = account_icp
        data["buyer_personas"] = buyer_personas
    return {
        "status": "ok",
        "profile": data,
        "seller_profile": seller_profile,
        "account_icp": account_icp,
        "buyer_personas": buyer_personas,
        "latest_campaign": serialize_recommendation_campaign(latest_campaign),
        "answer_guidance": (
            "优先用 seller_profile.summary 回答‘我是谁/我的公司是什么样’;用 account_icp 说明适合的目标公司;"
            "buyer_personas 只表示买方角色;latest_campaign/targeting_context 只表示最近一次推荐筛选条件,不是长期用户画像。"
            # The model used to say "tell me your service scope and I'll note it"; it noted it on the mirror row,
            # gone the next time the syncer refreshes from the profile. Whatever is missing, always point back to the profile form.
            "seller_profile.has_profile 为 true 时,这些内容来自用户在客户工作台上传的品牌资料:"
            "要改要补都请他去那里改,不要提议在对话里重新收集。"
        ),
    }


def _latest_report_summary(db: Session, user_id: int) -> dict[str, Any]:
    report = get_report_summary(db, user_id)
    return report if report.get("status") != "none" else {}


def _profile_has_usable_scope(profile: SalesAgentCustomerProfileORM | None) -> bool:
    if profile is None:
        return False
    return any(str(x).strip() and "待补充" not in str(x) for x in _loads(profile.service_scope_json, []))


def _has_usable_profile_values(items: list[Any] | None) -> bool:
    return any(str(x or "").strip() and "待补充" not in str(x or "") for x in (items or []))


def _seller_profile_missing_fields(
    seller_profile: SalesAgentSellerProfileORM | None,
    legacy_profile: SalesAgentCustomerProfileORM | None = None,
) -> list[str]:
    scope = _loads(seller_profile.service_scope_json, []) if seller_profile else []
    size = (seller_profile.company_size if seller_profile else "") or ""
    capabilities = _loads(seller_profile.service_capabilities_json, []) if seller_profile else []
    proof_points = _loads(seller_profile.proof_points_json, []) if seller_profile else []
    if legacy_profile is not None:
        scope = scope or _loads(legacy_profile.service_scope_json, [])
        size = size or legacy_profile.company_size or ""
    missing: list[str] = []
    # Ask about company/team size or professional capability first: service direction is "what we sell this time" and must not pass for professional capability.
    # Any one of team size, capability description, or supporting material is enough; do not repeatedly ask users for missing cases/strengths.
    has_team_or_capability = (
        bool(str(size or "").strip() and "待补充" not in str(size))
        or _has_usable_profile_values(capabilities)
        or _has_usable_profile_values(proof_points)
    )
    if not has_team_or_capability:
        missing.append("公司/团队规模或专业能力")
    # "Customer types we suit" is **not a missing item of our profile**. Our profile answers "who I am":
    # professional capability, service scope, team size, which may not change even once a year; "which customers to find this time" answers
    # "who I am looking for", on par with target industry/region/size, and must be re-selected on every find-customers run
    # (2026-08-26 user: "professional capability and company size need not be redone each time; target customers do").
    # Keeping it here backfires both ways: filled -> profile card prefilled -> router skips -> card never shows again;
    # not filled -> every recommendation is judged need_more_info and takes the guidance branch instead of actually recommending.
    # This question is now asked every round via the typical_customers slot in SLOT_ORDER (see flow/router.py).
    if not _has_usable_profile_values(scope):
        missing.append("你准备推广/销售的服务范围")
    return missing


def _ensure_seller_profile_ready(
    seller_profile: SalesAgentSellerProfileORM | None,
    legacy_profile: SalesAgentCustomerProfileORM | None = None,
) -> list[str]:
    """Return missing profile items; the chat layer guides the user to fill them, no hard error in the backend."""
    return _seller_profile_missing_fields(seller_profile, legacy_profile)


def _usable_profile_list(value: Any, limit: int = 20) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    return [item for item in _clean_profile_option_list(raw, limit) if "待补充" not in item]


def _usable_profile_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if not text or "待补充" in text else text


def seller_profile_for_lead_entry(db: Session, user_id: int, seller_profile_id: int | None = None) -> dict[str, Any]:
    """Read the brand profile to use this time, so the find-customers entry can decide whether to skip basic follow-up questions.

    When `seller_profile_id` is empty, fall back to "the latest active brand";
    single-brand accounts (all legacy ones are) behave exactly as before the change.
    """
    seller_row = _latest_seller_profile(db, user_id, seller_profile_id)
    legacy_profile = _profile_or_latest(db, user_id)
    seller_profile = serialize_seller_profile(seller_row) or _legacy_seller_profile_payload(legacy_profile)
    service_scope = _usable_profile_list((seller_profile or {}).get("service_scope"), 20)
    service_capabilities = _usable_profile_list((seller_profile or {}).get("service_capabilities"), 30)
    company_size = _usable_profile_text((seller_profile or {}).get("company_size"))
    typical_customers = _usable_profile_list((seller_profile or {}).get("typical_customers"), 20)
    missing: list[str] = []
    if not service_capabilities:
        missing.append("专业能力")
    if not service_scope:
        missing.append("服务范围")
    if not company_size:
        missing.append("企业员工规模")
    return {
        "ready": not missing,
        "seller_profile": seller_profile,
        # The card title must include the brand name, otherwise with multiple brands two "our profile" cards look identical
        "brand_id": seller_row.id if seller_row is not None else 0,
        "brand_name": (seller_row.name or "").strip() if seller_row is not None else "",
        # The two items below determine **wording**, not content: for brands that claimed a brand profile, all the values above are
        # mirrors synced from `ai_telemetry_topics.profile_json`, and missing fields should guide users back to the workspace;
        # orphan brands can only be filled manually in /admin, so they keep the old wording. The content itself is identical on both paths.
        "topic_id": int(seller_row.topic_id) if seller_row is not None and seller_row.topic_id else None,
        "has_profile": bool(seller_row is not None and seller_row.topic_id),
        "service_capabilities": service_capabilities,
        "service_scope": service_scope,
        "company_size": company_size,
        "typical_customers": typical_customers,
        "missing": missing,
    }


def build_seller_profile_summary_card(profile_payload: dict[str, Any]) -> dict[str, Any]:
    """Build the saved/basic seller-profile card for the lead entry flow."""
    service_capabilities = _usable_profile_list(profile_payload.get("service_capabilities"), 30)
    service_scope = _usable_profile_list(profile_payload.get("service_scope"), 20)
    company_size = _usable_profile_text(profile_payload.get("company_size"))
    typical_customers = _usable_profile_list(profile_payload.get("typical_customers"), 20)
    missing = _usable_profile_list(profile_payload.get("missing"), 10)
    ready = not missing
    capability_text = "、".join(service_capabilities) or "专业能力待补充"
    size_text = company_size or "企业员工规模待补充"
    if service_capabilities and company_size:
        summary_parts = [f"我们具备{capability_text}等专业能力，当前企业员工规模为{size_text}。"]
    elif service_capabilities:
        summary_parts = [f"我们具备{capability_text}等专业能力，企业员工规模待补充。"]
    elif company_size:
        summary_parts = [f"当前企业员工规模为{size_text}，专业能力待补充。"]
    else:
        summary_parts = ["还需要补充专业能力和企业员工规模。"]
    service_scope_text = "、".join(service_scope)
    if service_scope and service_scope_text != capability_text:
        summary_parts.append(f"本次可推广的服务方向是{service_scope_text}。")
    # The card does **not print** "better suited to prioritize serving...". This is the "our profile" card and only says who I am (capability/service direction/size);
    # "which customers we suit" is a filter re-selected for each task, and printing it here would make users think it is already decided,
    # while the next step asks for it again anyway (2026-08-26 user metric definition). The field itself stays in data as a fallback for callers.
    has_profile = bool(profile_payload.get("has_profile"))
    if missing and has_profile:
        # Content is now managed by the brand profile; saying "later steps will keep filling it in" would steer people into chat:
        # they fill it here, the profile form is still empty next time, and the two sides diverge again.
        summary_parts.append(f"还有{'、'.join(missing)}待补充，请到客户工作台的品牌资料里补齐。")
    elif missing:
        summary_parts.append(f"还有{'、'.join(missing)}待补充，后续流程会继续补齐。")
    else:
        summary_parts.append("确认后继续选择本次目标客户条件。")
    summary_text = "".join(summary_parts)
    # For single-brand accounts the caller clears brand_name, so the title stays "our profile": zero change for legacy accounts.
    # Deciding "is it multi-brand" requires a DB query, and this function is pure (test_service_layering guards that), so it lives in the caller.
    brand_name = _usable_profile_text(profile_payload.get("brand_name"))
    title = f"我方画像 · {brand_name}" if brand_name else "我方画像"
    if not has_profile:
        # The marker tells users this content is not backed by a profile (edits will not flow back to the workspace); it does not disable it.
        title = f"{title}（{BRAND_NO_PROFILE_HEADLINE}）"
    data = {
        "status": "profile_ready" if ready else "profile_partial",
        "title": title,
        "brand_id": profile_payload.get("brand_id") or 0,
        "brand_name": brand_name,
        "topic_id": profile_payload.get("topic_id") or None,
        "has_profile": has_profile,
        "source": ("品牌资料" if has_profile else "已保存我方画像") if ready else "待补充我方画像",
        "message": summary_text,
        "summary_text": summary_text,
        "service_capabilities": service_capabilities,
        "service_scope": service_scope,
        "company_size": company_size,
        "typical_customers": typical_customers,
        "missing": missing,
        "items": [],
        "hide_items": True,
        "actions": [
            {"label": "继续", "text": "继续"},
            {"label": "更新画像", "text": "更新画像"},
        ],
    }
    return {"tool": "sales_agent_seller_profile_summary", "data": data}


def build_seller_profile_snapshot_card(profile_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only seller-profile card for profile lookup answers."""
    service_capabilities = _usable_profile_list(profile_payload.get("service_capabilities"), 30)
    service_scope = _usable_profile_list(profile_payload.get("service_scope"), 20)
    company_size = _usable_profile_text(profile_payload.get("company_size"))
    typical_customers = _usable_profile_list(profile_payload.get("typical_customers"), 20)
    missing = _usable_profile_list(profile_payload.get("missing"), 10)
    ready = not missing
    has_profile = bool(profile_payload.get("has_profile"))
    # The read-only card's title is always "our profile": placeholder brand names (default brand) and internal states like "no linked brand profile"
    # mean nothing to users, and putting them in the title only makes people think they missed some configuration. What the profile is missing is
    # stated by "to be filled" in summary_text; the title need not repeat it.
    brand_name = usable_brand_name(_usable_profile_text(profile_payload.get("brand_name")))
    title = f"我方画像 · {brand_name}" if brand_name else "我方画像"
    # Users repeatedly ask "what is the profile" because they cannot tell the two layers apart. Explain it once here:
    #   our profile = who you are and what you sell; the source of truth is the brand topic in the console;
    #   conditions set per find-customers round = who you are looking for this time; only affects that round's recommendations.
    # Neither overrides the other (recommendation conditions are not written back to the brand topic), so "configure the topic" is the right way to improve recommendation precision,
    # and users need not worry that a configured topic gets overwritten by some recommendation's conditions.
    _TUNING_HINT = (
        "想让推荐更准,去控制台把品牌主题配细一些 —— 主题是长期的我方资料,"
        "每次找客户设的条件只作用于当轮推荐,两边互不覆盖。"
    )
    if missing and has_profile:
        summary_text = f"这是你当前保存的我方画像,还差{'、'.join(missing)}。{_TUNING_HINT}"
    elif missing:
        summary_text = (
            f"这是你当前保存的我方画像,还差{'、'.join(missing)}。"
            f"点右下角的更新画像可以先补上。{_TUNING_HINT}"
        )
    else:
        summary_text = f"这是你当前保存的我方画像,找客户时会按它来匹配。{_TUNING_HINT}"
    data = {
        "status": "profile_ready" if ready else "profile_partial",
        "title": title,
        "brand_id": profile_payload.get("brand_id") or 0,
        "brand_name": brand_name,
        "topic_id": profile_payload.get("topic_id") or None,
        "has_profile": has_profile,
        "source": ("品牌资料" if has_profile else "已保存我方画像") if ready else "待补充我方画像",
        "message": summary_text,
        "summary_text": summary_text,
        "service_capabilities": service_capabilities,
        "service_scope": service_scope,
        "company_size": company_size,
        "typical_customers": typical_customers,
        "missing": missing,
        "items": [],
        "hide_items": True,
        "readonly": True,
        "actions": [
            {"label": "更新画像", "text": "更新画像"},
        ],
    }
    return {"tool": "sales_agent_seller_profile_snapshot", "data": data}


def append_account_icp_preferences(
    db: Session,
    user_id: int,
    exclude: list[str] | None = None,
    signals: list[str] | None = None,
    seller_profile_id: int | None = None,
) -> dict[str, Any]:
    """Persist exclusions / demand signals mentioned in the conversation into AccountICP.

    This is the most lacking of the three memory layers: short-term (in-session slots) and mid-term (profile) exist,
    but "stated preferences" were never persisted: a user says "no education industry" and it is forgotten next round.
    Appends with dedup, never overwrites existing entries.

    `seller_profile_id` = the brand speaking this time. Without it we would write to "the account's latest ICP",
    while the read side (`get_current_customer_profile`) has always queried by brand,
    so "no education industry" said under brand A lands on B's ICP, and A itself cannot read it back.
    """
    row = _latest_account_icp(db, user_id, seller_profile_id=seller_profile_id)
    if row is None:
        row = SalesAgentAccountICPORM(user_id=user_id, seller_profile_id=seller_profile_id)
        db.add(row)
        db.flush()

    added: dict[str, list[str]] = {"exclude": [], "signals": []}
    for key, incoming, bucket in (
        ("negative_icp_json", exclude or [], "exclude"),
        ("observable_signals_json", signals or [], "signals"),
    ):
        current = [str(x).strip() for x in _loads(getattr(row, key), []) if str(x).strip()]
        for item in incoming:
            text = str(item).strip()
            if text and text not in current:
                current.append(text)
                added[bucket].append(text)
        setattr(row, key, _dumps(current[:50]))

    db.commit()
    db.refresh(row)
    return {
        "account_icp_id": row.id,
        "added": added,
        "negative_icp": _loads(row.negative_icp_json, []),
        "observable_signals": _loads(row.observable_signals_json, []),
    }
