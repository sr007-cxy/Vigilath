"""Sales Agent service layer: recommendation: recommendation tasks, scoring, evidence chains, discovery and ranking orchestration."""
from __future__ import annotations

import logging
import re
from typing import Any
from sqlalchemy import or_
from sqlalchemy.orm import Session, object_session
from agents.sales_agent.constants import (
    BLOCKED_CONTACT_CONSENT,
    DEFAULT_BUYER_ROLES,
    DEFAULT_EXCLUDE_RULES,
    DEFAULT_PROFILE_REGIONS,
    DEFAULT_RECOMMENDATIONS,
    MAX_RECOMMENDATIONS,
)
from agents.sales_agent.discovery import discover_public_companies, normalize_domain, search_capability
from agents.sales_agent.models import (
    SalesAgentAccountICPORM,
    SalesAgentCompanyORM,
    SalesAgentCompanyContactORM,
    SalesAgentCustomerProfileORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadEvidenceORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)

from agents.sales_agent.service.common import (
    SalesAgentError,
    _clamp_limit,
    _clean_list,
    _company_size_bucket_distance,
    _dumps,
    _is_email,
    _loads,
    _normalize_company_size_bucket,
    _profile_phrase,
    record_compliance_event,
)
from agents.sales_agent.service.entities import _campaign_or_latest, _company_contact_summary, serialize_company
from agents.sales_agent.service.companies import (
    _company_email_order_expr,
    _recommendation_card_payload,
    _recommendation_markdown_table,
    _recommendation_table_rows,
    _store_company_candidates,
    _usable_profile_industries,
    company_industry_match_clauses,
    industry_search_terms,
    load_company_candidates_from_db,
)
from agents.sales_agent.service.reports import (
    _active_report_share_for_lead,
    _create_report_share,
    _full_report_by_id,
    _full_report_for_share,
    _full_report_summary,
    _latest_matching_full_report,
    get_report_summary,
    serialize_report_share,
)
from agents.sales_agent.service.profiles import (
    _buyer_personas_for_context,
    _ensure_seller_profile_ready,
    _latest_account_icp,
    _latest_seller_profile,
    _profile_or_latest,
    _public_profile_payload,
    _seller_profile_missing_fields,
    create_default_buyer_personas,
    create_or_update_account_icp,
    create_or_update_seller_profile,
    serialize_account_icp,
    serialize_lead_evidence,
    serialize_profile,
    serialize_recommendation_campaign,
    serialize_seller_profile,
)
from agents.sales_agent.service.guidance import _campaign_guidance_payload, _profile_guidance_payload, _recommendation_requirements_message
from agents.sales_agent.service.brandprofile import brand_report_capable




def _seller_profile_payload_for_recommendation(db: Session, user_id: int, seller: SalesAgentSellerProfileORM | None) -> dict[str, Any] | None:
    payload = serialize_seller_profile(seller)
    if payload is not None:
        payload["report_capable"] = brand_report_capable(db, user_id, seller.id if seller else None)
    return payload


def _report_industries(report: dict[str, Any]) -> list[str]:
    vals: list[Any] = []
    vals.extend(report.get("industry_terms") or [])
    raw_snapshot = report.get("geo_summary") or {}
    if isinstance(raw_snapshot, dict):
        vals.extend(raw_snapshot.get("industry_terms") or [])
    return _clean_list(vals, 8)


def _campaign_industries(campaign: SalesAgentRecommendationCampaignORM | None) -> list[str]:
    if campaign is None:
        return []
    return [str(x).strip() for x in _loads(campaign.target_industries_json, []) if str(x).strip() and "待补充" not in str(x)]


def _filterable_industries(values: list[str]) -> list[str]:
    unlimited = {"不限行业", "不限制行业", "行业不限", "全行业", "不限"}
    return [value for value in values if value not in unlimited]


def _campaign_regions(campaign: SalesAgentRecommendationCampaignORM | None, include_national: bool = True) -> list[str]:
    if campaign is None:
        return []
    raw_values = [str(x).strip() for x in _loads(campaign.target_regions_json, []) if str(x).strip() and "待补充" not in str(x)]
    values: list[str] = []
    for value in raw_values:
        if value == "一线城市":
            values.extend(["北京", "上海", "广州", "深圳"])
        else:
            values.append(value)
    if not include_national:
        values = [x for x in values if x not in {"全国", "不限地区", "不限制地区", "地区不限"}]
    return _clean_list(values, 30)


def create_recommendation_campaign(
    db: Session,
    user_id: int,
    seller_profile_id: int | None,
    account_icp_id: int | None,
    legacy_profile_id: int | None,
    industries: list[str],
    regions: list[str],
    limit: int,
    service_scope_override: list[str] | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
    seed_companies: list[dict[str, Any]] | None = None,
    target_company_size: str = "",
    request_text: str = "",
    session_id: int | None = None,
    commit: bool = True,
) -> SalesAgentRecommendationCampaignORM:
    row = SalesAgentRecommendationCampaignORM(
        user_id=user_id,
        session_id=session_id,
        seller_profile_id=seller_profile_id,
        account_icp_id=account_icp_id,
        legacy_profile_id=legacy_profile_id,
        source_report_table=source_report_table or "",
        source_report_id=str(source_report_id or ""),
        request_text=request_text or "",
        target_industries_json=_dumps(_clean_list(industries, 30)),
        target_regions_json=_dumps(_clean_list(regions, 30)),
        target_company_size=(target_company_size or "").strip(),
        service_scope_override_json=_dumps(_clean_list(service_scope_override, 20)),
        limit=_clamp_limit(limit),
        seed_companies_json=_dumps(seed_companies or []),
        requested_limit=_clamp_limit(limit),
        status="discovering",
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def _campaign_public_profile_payload(
    campaign: SalesAgentRecommendationCampaignORM,
    seller_profile: SalesAgentSellerProfileORM | None = None,
    account_icp: SalesAgentAccountICPORM | None = None,
) -> dict[str, Any]:
    service_scope = _loads(campaign.service_scope_override_json, [])
    if not service_scope and seller_profile is not None:
        service_scope = _loads(seller_profile.service_scope_json, [])
    return {
        "industries": _campaign_industries(campaign),
        "regions": _campaign_regions(campaign) or DEFAULT_PROFILE_REGIONS,
        "service_scope": service_scope,
        "company_size": campaign.target_company_size or (account_icp.target_company_size if account_icp else ""),
    }


def _condition_match_meta(campaign, leads: list[dict[str, Any]], filtered: bool) -> dict[str, Any]:
    """How well results match the conditions the user selected.

    When candidates come from the hard-coded seed pool, industry/region do not filter and only affect ranking, so "education & training + Beijing" returned
    a batch of medical devices and semiconductors while the UI said "recommended 20 companies per current filters" and "gap: 0"
    (production 2026-08-23). Consistent counts do not mean correct results; here the match rate is computed and handed to the validation layer.
    """
    industries = [x for x in _filterable_industries(_campaign_industries(campaign)) if x]
    industry_terms = industry_search_terms(industries)
    regions = [x for x in _campaign_regions(campaign, include_national=False) if x]

    def _hit(values: list[str], *fields: str) -> int:
        if not values:
            return len(leads)
        count = 0
        for lead in leads:
            company = lead.get("company") if isinstance(lead.get("company"), dict) else lead
            text = " ".join(str(company.get(f) or "") for f in fields)
            checks = industry_terms if fields == ("industry",) else values
            if any(v in text for v in checks):
                count += 1
        return count

    return {
        "filter_applied": bool(filtered),
        "requested_industries": industries,
        "requested_regions": regions,
        "industry_match_count": _hit(industries, "industry"),
        "region_match_count": _hit(regions, "region"),
        # A website is not a filter (it only adds 5 points to the score); when the user says "recommend ones with websites" the model must be able to say truthfully how many in this batch actually have one.
        "website_count": sum(
            1 for lead in leads
            if str((lead.get("company") if isinstance(lead.get("company"), dict) else lead).get("website") or "").strip()
        ),
    }


_EXCLUDED_DELIVERY_STATUSES = {"sent", "failed"}


def _already_sent_company_ids(db: Session, user_id: int) -> set[int]:
    """Companies this account has already attempted to email; new recommendations exclude them by default."""
    try:
        rows = (
            db.query(SalesAgentEmailDeliveryORM.company_id)
            .filter(
                SalesAgentEmailDeliveryORM.user_id == user_id,
                SalesAgentEmailDeliveryORM.status.in_(_EXCLUDED_DELIVERY_STATUSES),
                SalesAgentEmailDeliveryORM.company_id.isnot(None),
            )
            .distinct()
            .all()
        )
    except Exception:  # noqa: BLE001 - auxiliary dedup failure must not interrupt the main recommendation flow
        logging.getLogger(__name__).warning("Sales Agent sent-company exclusion lookup failed", exc_info=True)
        db.rollback()
        return set()
    return {int(row[0]) for row in rows if row[0] is not None}


def _already_recommended_company_ids(db: Session, user_id: int, exclude_campaign_id: int | None = None) -> set[int]:
    """Companies this account has recommended historically; follow-up batches exclude them by default."""
    try:
        q = (
            db.query(SalesAgentLeadRecommendationORM.company_id)
            .filter(
                SalesAgentLeadRecommendationORM.user_id == user_id,
                SalesAgentLeadRecommendationORM.company_id.isnot(None),
            )
        )
        if exclude_campaign_id is not None:
            q = q.filter(SalesAgentLeadRecommendationORM.campaign_id != int(exclude_campaign_id))
        rows = q.distinct().all()
    except Exception:  # noqa: BLE001 - auxiliary dedup failure must not interrupt the main recommendation flow
        logging.getLogger(__name__).warning("Sales Agent recommended-company exclusion lookup failed", exc_info=True)
        db.rollback()
        return set()
    return {int(row[0]) for row in rows if row[0] is not None}


def _exclude_company_ids(q, company_ids: set[int] | None):
    ids = {int(x) for x in (company_ids or set()) if x}
    if ids:
        q = q.filter(~SalesAgentCompanyORM.id.in_(ids))
    return q


def _exclude_already_sent_companies(q, db: Session, user_id: int):
    sent_company_ids = _already_sent_company_ids(db, user_id)
    return _exclude_company_ids(q, sent_company_ids)


def _campaign_company_filter(q, campaign: SalesAgentRecommendationCampaignORM | None, account_icp: SalesAgentAccountICPORM | None = None):
    industries = _filterable_industries(_campaign_industries(campaign))
    regions = _campaign_regions(campaign, include_national=False)
    if not industries and account_icp is not None:
        industries = _filterable_industries([str(x).strip() for x in _loads(account_icp.target_industries_json, []) if str(x).strip()])
    industry_clauses = company_industry_match_clauses(industry_search_terms(industries))
    if industry_clauses:
        q = q.filter(or_(*industry_clauses))
    if regions:
        q = q.filter(or_(*[SalesAgentCompanyORM.region.ilike(f"%{region}%") for region in regions]))
    return _apply_negative_icp(q, account_icp)


def _apply_negative_icp(q, account_icp: SalesAgentAccountICPORM | None):
    """Apply the ICP's exclusions to the query.

    Before this, `negative_icp` was **written but never read**: the user said "no education industry" and the next round recommended it anyway.
    Only contains-matching on industry / region / company name; unrecognized exclusions (e.g. "low budget") are not handled here
    and are left to scoring and manual screening.
    """
    if account_icp is None:
        return q
    rules = [str(x).strip() for x in _loads(account_icp.negative_icp_json, []) if str(x).strip()]
    for rule in rules[:20]:
        q = q.filter(
            ~SalesAgentCompanyORM.industry.ilike(f"%{rule}%"),
            ~SalesAgentCompanyORM.region.ilike(f"%{rule}%"),
            ~SalesAgentCompanyORM.name.ilike(f"%{rule}%"),
        )
    return q


def _service_scope_for_campaign(campaign: SalesAgentRecommendationCampaignORM | None, seller_profile: SalesAgentSellerProfileORM | None) -> list[str]:
    scope = _loads(campaign.service_scope_override_json, []) if campaign else []
    if not scope and seller_profile is not None:
        scope = _loads(seller_profile.service_scope_json, [])
    return _clean_list(scope, 20)


def _company_has_sendable_email(db: Session, company_id: int) -> bool:
    rows = (
        db.query(SalesAgentCompanyContactORM.email)
        .filter(
            SalesAgentCompanyContactORM.company_id == int(company_id),
            SalesAgentCompanyContactORM.email != "",
            ~SalesAgentCompanyContactORM.consent_status.in_(BLOCKED_CONTACT_CONSENT),
        )
        .limit(20)
        .all()
    )
    return any(_is_email(str(row[0] or "")) for row in rows)


def _score_company_for_campaign(
    company: SalesAgentCompanyORM,
    seller_profile: SalesAgentSellerProfileORM | None,
    account_icp: SalesAgentAccountICPORM | None,
    campaign: SalesAgentRecommendationCampaignORM | None,
    report: dict[str, Any],
    can_reference_report: bool,
) -> tuple[int, str, list[dict[str, Any]]]:
    score = 50
    reasons: list[str] = []
    evidences: list[dict[str, Any]] = []
    industries = [str(x).lower() for x in industry_search_terms(_filterable_industries(_campaign_industries(campaign)))]
    regions = [str(x).lower() for x in _campaign_regions(campaign)]
    services = _service_scope_for_campaign(campaign, seller_profile)
    company_industry_text = (company.industry or "").lower()
    if company_industry_text and any(x and x in company_industry_text for x in industries):
        score += 25
        reasons.append("符合目标行业")
        evidences.append({"evidence_type": "firmographic", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": f"企业行业为 {company.industry},匹配本次目标行业。", "scoring_category": "campaign_industry_fit", "score_delta": 25, "confidence": "medium"})
    if company.region and any(x and x != "全国" and x in company.region.lower() for x in regions):
        score += 15
        reasons.append("符合目标地区")
        evidences.append({"evidence_type": "firmographic", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": f"企业地区为 {company.region},匹配本次目标地区。", "scoring_category": "campaign_region_fit", "score_delta": 15, "confidence": "medium"})
    target_size = (campaign.target_company_size if campaign else "") or (account_icp.target_company_size if account_icp else "")
    target_buckets = [
        bucket for bucket in (_normalize_company_size_bucket(part) for part in _clean_list(re.split(r"[、,，;；\n]+", target_size), 10))
        if bucket
    ]
    company_bucket = _normalize_company_size_bucket(company.employee_size)
    distances = [_company_size_bucket_distance(bucket, company_bucket) for bucket in target_buckets] if company_bucket else []
    distances = [distance for distance in distances if distance is not None]
    distance = min(distances) if distances else None
    target_bucket_text = "、".join(target_buckets)
    if distance == 0:
        score += 10
        reasons.append(f"公司规模匹配本次目标规模“{target_bucket_text}”")
        evidences.append({"evidence_type": "firmographic", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": f"公开信息显示企业规模为 {company.employee_size or company_bucket},匹配本次目标公司规模 {target_bucket_text}。", "scoring_category": "target_company_size_fit", "score_delta": 10, "confidence": "medium"})
    elif distance == 1:
        score += 4
        reasons.append(f"公司规模接近本次目标规模“{target_bucket_text}”")
        evidences.append({"evidence_type": "firmographic", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": f"公开信息显示企业规模为 {company.employee_size or company_bucket},接近本次目标公司规模 {target_bucket_text}。", "scoring_category": "target_company_size_fit", "score_delta": 4, "confidence": "low"})
    if company.website:
        score += 5
        reasons.append("有官网可诊断")
        evidences.append({"evidence_type": "contact", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": "企业存在公开官网,可作为公开诊断和销售切入依据。", "scoring_category": "public_website", "score_delta": 5, "confidence": "medium"})
    if services:
        reasons.append(f"可结合“{_profile_phrase(services, limit=2)}”切入")
    if account_icp is not None:
        signals = _loads(account_icp.observable_signals_json, [])[:2]
        if signals:
            reasons.append("目标公司 ICP 有可复用的公开信号判断维度")
    if can_reference_report:
        total = report.get("total_score")
        try:
            total_num = int(total)
            delta = max(0, min(15, (100 - total_num) // 4))
            score += delta
        except Exception:  # noqa: BLE001
            delta = 8
            score += delta
        reasons.append("目标企业与真实报告匹配,可做最终推荐")
        evidences.append({"evidence_type": "report", "source_type": "detection_report", "source_url": str(report.get("url") or company.website or ""), "source_report_table": report.get("source_report_table") or "", "source_report_id": str(report.get("source_report_id") or ""), "evidence_text": "推荐企业与已完成真实检测报告匹配,允许引用该报告摘要。", "scoring_category": "real_report_match", "score_delta": delta, "confidence": "high"})
    elif report.get("has_real_detection"):
        reasons.append("报告不匹配,仅初步推荐")
    else:
        reasons.append("无真实报告,仅初步推荐")
    return max(0, min(100, score)), "；".join(reasons), evidences


def _opportunity_budget_score(company: SalesAgentCompanyORM | None) -> tuple[int, str]:
    bucket = _normalize_company_size_bucket(company.employee_size if company else "")
    if bucket == "1001+ 名员工":
        return 25, "公开规模显示具备较高采购承载力"
    if bucket == "501-1000 名员工":
        return 22, "公开规模显示具备较高采购承载力"
    if bucket == "101-500 名员工":
        return 18, "公开规模显示有明确数字化获客投入空间"
    if bucket == "11-100 名员工":
        return 12, "公开规模显示可能有增长型获客需求"
    if bucket == "1-10 名员工":
        return 6, "公开规模偏小,采购承载力需进一步确认"
    return 0, "缺少公开规模信息,投入能力待确认"


def _opportunity_contact_score(contacts: list[dict[str, Any]] | None) -> tuple[int, list[str], bool]:
    usable = [
        c for c in (contacts or [])
        if isinstance(c, dict)
        and c.get("consent_status") not in BLOCKED_CONTACT_CONSENT
        and c.get("is_reachable") is not False
    ]
    has_email = any(str(c.get("email") or "").strip() for c in usable)
    has_phone = any(str(c.get("phone") or "").strip() for c in usable)
    has_form = any(str(c.get("contact_form_url") or "").strip() for c in usable)
    named_contact = any(str(c.get("contact_name") or c.get("position") or "").strip() for c in usable)
    score = 0
    signals: list[str] = []
    if has_email:
        score += 10
        signals.append("有公开公司级邮箱")
    if has_phone:
        score += 8
        signals.append("有公开公司级电话")
    if has_form:
        score += 5
        signals.append("有官网联系表单")
    if named_contact:
        score += 2
        signals.append("有公开联系人/岗位信息")
    return min(25, score), signals, bool(has_email or has_phone or has_form)


def _opportunity_need_score(company: SalesAgentCompanyORM | None, report: dict[str, Any], can_reference_report: bool) -> tuple[int, str]:
    if can_reference_report:
        total = report.get("total_score")
        try:
            total_num = int(total)
            gap_score = max(8, min(25, (100 - total_num) // 3))
        except Exception:  # noqa: BLE001
            gap_score = 14
        return gap_score, "已关联真实检测报告,可基于报告差距判断需求"
    if company is not None and company.website:
        return 8, "有官网,可先做 AI-GEO 审计确认需求"
    return 0, "缺少官网或真实报告,需求强度待确认"


def _lead_scoring_fields(
    row: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM | None,
    company_contacts: list[dict[str, Any]] | None = None,
    evidence_payload: list[dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    match_score = max(0, min(100, int(row.score or 0)))
    evidence = [x for x in (evidence_payload or []) if isinstance(x, dict)]
    categories = {str(x.get("scoring_category") or "") for x in evidence}
    firmographic_count = len(categories & {"campaign_industry_fit", "campaign_region_fit", "target_company_size_fit"})
    report = _loads(row.report_snapshot, {})
    can_reference_report = bool(row.source_report_table or row.source_report_id or report.get("has_real_detection"))
    budget_score, budget_note = _opportunity_budget_score(company)
    contact_score, contact_signals, has_contact = _opportunity_contact_score(company_contacts)
    need_score, need_note = _opportunity_need_score(company, report, can_reference_report)
    timing_score = 0
    if can_reference_report:
        timing_score += 10
    if has_contact:
        timing_score += 8
    if firmographic_count >= 2:
        timing_score += 5
    elif firmographic_count == 1:
        timing_score += 3
    if row.campaign_id:
        timing_score += 2
    timing_score = min(25, timing_score)
    observable_signals = firmographic_count
    if company is not None:
        observable_signals += int(bool(company.website)) + int(bool(company.employee_size)) + int(bool(company.industry)) + int(bool(company.region))
    observable_signals += int(has_contact) + int(can_reference_report)
    raw_opportunity_score = budget_score + contact_score + need_score + timing_score
    opportunity_score = raw_opportunity_score if observable_signals >= 2 else None
    confidence_points = firmographic_count
    if company is not None:
        confidence_points += int(bool(company.website)) + int(bool(company.employee_size))
    if has_contact:
        confidence_points += 2
    if can_reference_report:
        confidence_points += 3
    confidence = "high" if confidence_points >= 7 else ("medium" if confidence_points >= 4 else "low")
    confidence_level = {"high": "高", "medium": "中", "low": "低"}[confidence]
    if company is None or not company.website:
        action = "先补官网,再诊断触达。"
    elif not has_contact:
        action = "先确认公开联系方式。"
    elif can_reference_report:
        action = "生成报告邮件,可引用真实报告。"
    elif confidence == "low":
        action = "先补证据或检测官网。"
    else:
        action = "生成报告邮件,用审慎措辞。"
    opportunity_label = f"{max(0, min(100, opportunity_score))}分" if opportunity_score is not None else "待评估"
    scoring = {
        "match_score": match_score,
        "match_score_label": f"{match_score}分",
        "opportunity_score": max(0, min(100, opportunity_score)) if opportunity_score is not None else None,
        "opportunity_score_label": opportunity_label,
        "confidence": confidence,
        "confidence_level": confidence_level,
        "recommended_action": action,
        "score_summary": "匹配度排优先级;机会分为保守估算。",
        "opportunity_breakdown": [
            {"label": "投入能力", "score": budget_score, "max": 25, "note": budget_note},
            {"label": "触达通路", "score": contact_score, "max": 25, "note": "、".join(contact_signals) or "缺少公开公司级联系方式"},
            {"label": "需求信号", "score": need_score, "max": 25, "note": need_note},
            {"label": "跟进时机", "score": timing_score, "max": 25, "note": "基于报告、联系方式和本次筛选证据估算"},
        ],
    }
    return {
        "match_score": scoring["match_score"],
        "opportunity_score": scoring["opportunity_score"],
        "opportunity_score_label": scoring["opportunity_score_label"],
        "confidence": scoring["confidence"],
        "confidence_level": scoring["confidence_level"],
        "recommended_action": scoring["recommended_action"],
        "score_summary": scoring["score_summary"],
        "scoring": scoring,
    }


def create_lead_evidence(
    db: Session,
    user_id: int,
    campaign_id: int | None,
    lead_id: int | None,
    company_id: int | None,
    evidence: dict[str, Any],
) -> SalesAgentLeadEvidenceORM:
    text = str(evidence.get("evidence_text") or "").strip()
    category = str(evidence.get("scoring_category") or "").strip()
    row = (
        db.query(SalesAgentLeadEvidenceORM)
        .filter(
            SalesAgentLeadEvidenceORM.user_id == user_id,
            SalesAgentLeadEvidenceORM.campaign_id == campaign_id,
            SalesAgentLeadEvidenceORM.lead_recommendation_id == lead_id,
            SalesAgentLeadEvidenceORM.company_id == company_id,
            SalesAgentLeadEvidenceORM.scoring_category == category,
        )
        .first()
    )
    if row is None:
        row = SalesAgentLeadEvidenceORM(user_id=user_id, campaign_id=campaign_id, lead_recommendation_id=lead_id, company_id=company_id)
        db.add(row)
    row.evidence_type = str(evidence.get("evidence_type") or "firmographic")[:64]
    row.source_type = str(evidence.get("source_type") or "manual_seed")[:64]
    row.source_url = str(evidence.get("source_url") or "")[:512]
    row.source_report_table = str(evidence.get("source_report_table") or "")[:64]
    row.source_report_id = str(evidence.get("source_report_id") or "")[:64]
    row.evidence_text = text
    row.scoring_category = category[:64]
    try:
        row.score_delta = int(evidence.get("score_delta") or 0)
    except Exception:  # noqa: BLE001
        row.score_delta = 0
    row.confidence = str(evidence.get("confidence") or "medium")[:16]
    db.flush()
    return row


def _seller_profile_by_legacy(db: Session, user_id: int, legacy_profile_id: int | None) -> SalesAgentSellerProfileORM | None:
    if legacy_profile_id is None:
        return None
    return (
        db.query(SalesAgentSellerProfileORM)
        .filter(SalesAgentSellerProfileORM.user_id == user_id, SalesAgentSellerProfileORM.legacy_profile_id == int(legacy_profile_id))
        .order_by(SalesAgentSellerProfileORM.id.desc())
        .first()
    )


def _account_icp_by_legacy(db: Session, user_id: int, legacy_profile_id: int | None, seller_profile_id: int | None = None) -> SalesAgentAccountICPORM | None:
    if legacy_profile_id is None:
        return None
    q = db.query(SalesAgentAccountICPORM).filter(SalesAgentAccountICPORM.user_id == user_id, SalesAgentAccountICPORM.legacy_profile_id == int(legacy_profile_id))
    if seller_profile_id is not None:
        q = q.filter(SalesAgentAccountICPORM.seller_profile_id == int(seller_profile_id))
    return q.order_by(SalesAgentAccountICPORM.id.desc()).first()


def _profile_for_recommendation(
    db: Session,
    user_id: int,
    profile_id: int | None,
    service_scope: list[str] | None,
    industries: list[str] | None,
    regions: list[str] | None,
    limit: int = DEFAULT_RECOMMENDATIONS,
    source_report_table: str = "",
    source_report_id: str = "",
    seller_profile_id: int | None = None,
    account_icp_id: int | None = None,
    campaign_id: int | None = None,
    target_company_size: str = "",
    service_scope_override: list[str] | None = None,
    seed_companies: list[dict[str, Any]] | None = None,
    company_size: str = "",
    typical_customers: list[str] | None = None,
    session_id: int | None = None,
) -> tuple[SalesAgentCustomerProfileORM | None, SalesAgentSellerProfileORM | None, SalesAgentAccountICPORM | None, SalesAgentRecommendationCampaignORM, dict[str, Any]]:
    # Recommendation requests by default only use profile/filter conditions explicitly given in the current conversation; do not silently reuse the user's latest historical profile.
    profile = _profile_or_latest(db, user_id, profile_id) if profile_id is not None else None
    if profile_id is not None and profile is None:
        raise SalesAgentError("客户画像不存在或不属于当前用户。", status_code=404)
    if campaign_id is not None:
        campaign = _campaign_or_latest(db, user_id, campaign_id)
        if campaign is None:
            raise SalesAgentError("推荐任务不存在或不属于当前用户。", status_code=404)
        seller = _latest_seller_profile(db, user_id, campaign.seller_profile_id) if campaign.seller_profile_id else None
        account_icp = _latest_account_icp(db, user_id, campaign.account_icp_id, seller.id if seller else None) if campaign.account_icp_id else None
        if campaign.legacy_profile_id:
            profile = _profile_or_latest(db, user_id, campaign.legacy_profile_id)
        report = get_report_summary(db, user_id, campaign.source_report_table, campaign.source_report_id) if (campaign.source_report_table or campaign.source_report_id) else {}
        campaign_industries = _campaign_industries(campaign)
        campaign_regions = _campaign_regions(campaign)
        if not campaign_industries or not campaign_regions:
            raise SalesAgentError(
                _recommendation_requirements_message(
                    missing_industry=not campaign_industries,
                    missing_region=not campaign_regions,
                ),
                status_code=400,
            )
        return profile, seller, account_icp, campaign, report

    scope = _clean_list(service_scope, 20)
    inds = _clean_list(industries, 20)
    regs = _clean_list(regions, 20)
    inline_company_size = str(company_size or "").strip()
    inline_typical = _clean_list(typical_customers, 20)
    report = get_report_summary(db, user_id, source_report_table, source_report_id) if (source_report_table or source_report_id) else {}
    if not inds or not regs:
        raise SalesAgentError(_recommendation_requirements_message(missing_industry=not inds, missing_region=not regs), status_code=400)

    # The trailing `or _latest_seller_profile(db, user_id)` cannot be omitted: **reads and writes must resolve to the same row**.
    # When called from the find-customers flow there is neither seller_profile_id nor a legacy profile, so this reads None,
    # while create_or_update_seller_profile below falls back to "the current active brand" when the id is empty:
    # reading from "empty" and writing to "the real brand" with a full-row overwrite meant every recommendation run cleared
    # the professional capabilities, evidence and target customers in /admin (test_running_a_recommendation_keeps_the_profile_body).
    seller = (
        _latest_seller_profile(db, user_id, seller_profile_id)
        if seller_profile_id is not None
        else (_seller_profile_by_legacy(db, user_id, profile.id if profile else None) or _latest_seller_profile(db, user_id))
    )
    if seller_profile_id is not None and seller is None:
        raise SalesAgentError("卖家服务画像不存在或不属于当前用户。", status_code=404)
    seller_scope = scope or (_loads(seller.service_scope_json, []) if seller else []) or (_loads(profile.service_scope_json, []) if profile else [])
    seller_company_size = inline_company_size or (seller.company_size if seller else "") or (profile.company_size if profile else "")
    seller_row_typical = _loads(seller.typical_customers_json, []) if seller else []
    seller_typical = inline_typical or seller_row_typical or (_loads(profile.typical_customers_json, []) if profile else [])
    # The recommendation flow never collects capabilities and evidence, but create_or_update_seller_profile **overwrites the whole row**
    # (`row.service_capabilities_json = _dumps(capabilities)`, writing an empty array when not passed);
    # without reading the existing values and carrying them along here, every find-customers run would clear the admin page's "our professional capabilities"
    # and evidence, right after the previous step _persist_lead_seller_profile_snapshot wrote them.
    seller_capabilities = _loads(seller.service_capabilities_json, []) if seller else []
    seller_proof_points = _loads(seller.proof_points_json, []) if seller else []
    # The brand row's "target customers" are **carried back as-is, not overwritten with this round's value**. Under the old full-row overwrite,
    # every find-customers run changed the /admin value to whatever was casually ticked in chat, and "target customers" is
    # re-selected each round, i.e. overwritten each round. This round's value goes through `seller_typical` for this task
    # and lands in the ICP's target_company_types below (the target customer side), not in the brand profile.
    seller_persist_typical = seller_row_typical
    if seller is None or scope or inline_company_size or inline_typical:
        seller = create_or_update_seller_profile(
            db,
            user_id,
            seller_profile_id=seller.id if seller else None,
            legacy_profile_id=profile.id if profile else None,
            source_report_table=str(report.get("source_report_table") or source_report_table or ""),
            source_report_id=str(report.get("source_report_id") or source_report_id or ""),
            report_snapshot=report,
            service_scope=seller_scope,
            service_capabilities=seller_capabilities,
            company_size=seller_company_size,
            typical_customers=seller_persist_typical,
            proof_points=seller_proof_points,
            default_buyer_roles=_loads(profile.buyer_roles_json, []) if profile else DEFAULT_BUYER_ROLES,
            default_exclude_rules=_loads(profile.exclude_rules_json, []) if profile else DEFAULT_EXCLUDE_RULES,
            commit=False,
        )
    missing_profile_fields = _ensure_seller_profile_ready(seller, profile)

    # The trailing `or _latest_account_icp(..., seller_profile_id=seller.id)` is the same fallback as on the seller side
    # above; previously only the seller half was fixed. When called from the find-customers flow both `account_icp_id` and
    # the legacy profile are empty, and `_account_icp_by_legacy` short-circuits to None when legacy_profile_id is None,
    # so every recommendation run created a new `SalesAgentAccountICPORM(user_id=...)` row.
    #
    # On the surface nothing looks wrong (read paths all take the latest row via `id.desc()`, and "target customers" are re-selected each round anyway),
    # but two things actually suffer:
    #   - the new row's negative_icp_json / scoring_rubric_json / observable_signals_json are all
    #     column defaults; these are **not** per-round brand-level settings, so each recommendation run silently cleared them
    #   - buyer_persona is looked up by account_icp_id (profiles.py's _buyer_personas_for_context),
    #     so a new ICP id finds nothing and a whole new set of sales_agent_buyer_personas rows gets rebuilt
    #
    # Fall back only by seller.id here, and **do not** degrade to the brand-less `_latest_account_icp(db, user_id)`:
    # that is exactly the da38e54e "guess the brand with the largest id" approach (see _brand_is_ambiguous in api.py).
    # When seller is empty (the account has no brand at all) there is no row to attribute to, so we must create one.
    account_icp = (
        _latest_account_icp(db, user_id, account_icp_id, seller.id if seller else None)
        if account_icp_id is not None
        else (
            _account_icp_by_legacy(db, user_id, profile.id if profile else None, seller.id if seller else None)
            or (_latest_account_icp(db, user_id, seller_profile_id=seller.id) if seller is not None else None)
        )
    )
    if account_icp_id is not None and account_icp is None:
        raise SalesAgentError("目标公司 ICP 不存在或不属于当前用户。", status_code=404)
    if account_icp is None:
        account_icp = create_or_update_account_icp(
            db,
            user_id,
            seller_profile_id=seller.id if seller else None,
            legacy_profile_id=profile.id if profile else None,
            target_company_types=seller_typical,
            target_industries=[],
            target_regions=[],
            target_company_size=(target_company_size or "").strip(),
            negative_icp=_loads(profile.exclude_rules_json, []) if profile else DEFAULT_EXCLUDE_RULES,
            source="campaign_context",
            commit=False,
        )
    elif inline_typical:
        # "Target customers" are re-selected each round and belong to the **target customer** side (the ICP's target_company_types);
        # the brand profile row is untouched. Must assign directly here: create_or_update_account_icp overwrites the whole row,
        # and using it would clear industry/region/size/exclusion rules along with it.
        account_icp.target_company_types_json = _dumps(_clean_list(inline_typical, 30))

    if seller is not None and not _buyer_personas_for_context(db, user_id, seller.id, account_icp.id if account_icp else None):
        create_default_buyer_personas(
            db,
            user_id,
            seller.id,
            account_icp.id if account_icp else None,
            _loads(profile.buyer_roles_json, []) if profile else DEFAULT_BUYER_ROLES,
            commit=False,
        )

    campaign = create_recommendation_campaign(
        db,
        user_id,
        seller_profile_id=seller.id if seller else None,
        account_icp_id=account_icp.id if account_icp else None,
        legacy_profile_id=profile.id if profile else None,
        industries=inds,
        regions=regs,
        limit=limit,
        service_scope_override=service_scope_override or scope,
        source_report_table=str(report.get("source_report_table") or source_report_table or ""),
        source_report_id=str(report.get("source_report_id") or source_report_id or ""),
        seed_companies=seed_companies,
        target_company_size=(target_company_size or (account_icp.target_company_size if account_icp else "") or "").strip(),
        session_id=session_id,
        commit=False,
    )
    if missing_profile_fields:
        campaign.status = "need_more_info"
        campaign.missing_config_json = _dumps(missing_profile_fields)
    db.commit()
    db.refresh(campaign)
    if seller is not None:
        db.refresh(seller)
    if account_icp is not None:
        db.refresh(account_icp)
    return profile, seller, account_icp, campaign, report


def _discovery_candidate_payload(c: Any) -> dict[str, Any]:
    return {
        "name": c.name,
        "industry": c.industry,
        "region": c.region,
        "website": c.website,
        "employee_size": c.employee_size,
        "source": c.source,
        "source_url": c.source_url,
        "confidence": c.confidence,
        "evidence_title": c.evidence_title,
        "evidence_snippet": c.evidence_snippet,
        "query": c.query,
        "email": c.email,
        "phone": c.phone,
        "contact_form_url": c.contact_form_url,
        "contact_source": c.contact_source,
        "contact_source_url": c.contact_source_url,
        "consent_status": c.consent_status,
        "contacts": c.contacts,
    }


def _discovery_status_payload(
    mode: str,
    requested_limit: int,
    actual_count: int,
    blocked: int,
    queries: list[str] | None = None,
    errors: list[str] | None = None,
    search_available: bool | None = None,
    missing_config: list[str] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    capability = search_capability() if search_available is None else {
        "search_available": search_available,
        "missing_config": missing_config or [],
        "status": status,
    }
    shortfall = max(0, requested_limit - actual_count)
    return {
        "status": status,
        "requested_limit": requested_limit,
        "actual_count": actual_count,
        "shortfall": shortfall,
        "can_expand": bool(capability.get("search_available")) and shortfall > 0,
        "search_available": bool(capability.get("search_available")),
        "missing_config": capability.get("missing_config") or [],
        "blocked": blocked,
        "queries": queries or [],
        "errors": errors or [],
    }


def discover_companies(
    db: Session,
    user_id: int,
    profile_id: int | None = None,
    limit: int = MAX_RECOMMENDATIONS,
    seed_companies: list[dict[str, Any]] | None = None,
    campaign_id: int | None = None,
    exclude_company_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Discover candidate companies. Explicit seeds are only for manual/authorized import; by default read from the DB candidate pool."""
    n = _clamp_limit(limit)
    profile = _profile_or_latest(db, user_id, profile_id) if profile_id is not None else None
    if profile_id is not None and profile is None:
        raise SalesAgentError("客户画像不存在或不属于当前用户。", status_code=404)
    campaign = _campaign_or_latest(db, user_id, campaign_id) if campaign_id is not None else None
    if campaign_id is not None and campaign is None:
        raise SalesAgentError("推荐任务不存在或不属于当前用户。", status_code=404)
    if profile is None and campaign is not None and campaign.legacy_profile_id:
        profile = _profile_or_latest(db, user_id, campaign.legacy_profile_id)
    seller = _latest_seller_profile(db, user_id, campaign.seller_profile_id) if campaign and campaign.seller_profile_id else None
    account_icp = _latest_account_icp(db, user_id, campaign.account_icp_id, seller.id if seller else None) if campaign and campaign.account_icp_id else None
    seeds = [dict(x) for x in (seed_companies or [])]
    if campaign is not None:
        industries = _campaign_industries(campaign)
        regions = _campaign_regions(campaign) or DEFAULT_PROFILE_REGIONS
        discovery_context = _campaign_public_profile_payload(campaign, seller, account_icp)
    else:
        industries = _usable_profile_industries(profile)
        regions = _loads(profile.regions_json, []) if profile else DEFAULT_PROFILE_REGIONS
        discovery_context = _public_profile_payload(profile)
    if not seeds and not industries:
        raise SalesAgentError(_recommendation_requirements_message(missing_industry=True, missing_region=False, include_limit=False), status_code=400)
    explicit_seed = bool(seeds)
    mode = "seed" if explicit_seed else "db_pool"
    excluded_company_ids = {int(x) for x in (exclude_company_ids or set()) if x}
    queries: list[str] = []
    errors: list[str] = []
    capability = search_capability()
    search_available = bool(capability["search_available"])
    missing_config: list[str] = list(capability.get("missing_config") or [])
    discovery_status = "seed" if explicit_seed else "db_pool"

    if explicit_seed:
        # Explicit seeds are the manual authorized-import channel for the API/tools; default recommendations no longer take this path.
        created, blocked = _store_company_candidates(db, user_id, seeds, industries, regions)
    else:
        sent_company_ids = _already_sent_company_ids(db, user_id)
        created = [
            item for item in load_company_candidates_from_db(db, industries=industries, regions=regions, limit=n)
            if int(item.get("id") or 0) not in sent_company_ids and int(item.get("id") or 0) not in excluded_company_ids
        ]
        blocked = 0
        if len(created) < n and search_available:
            # When the in-DB candidates are empty, web search tries at least 5 companies so an empty result does not end things outright.
            public_limit = max(5, n - len(created)) if not created else n - len(created)
            public = discover_public_companies(discovery_context, public_limit)
            queries = list(public.queries or [])
            errors = list(public.errors or [])
            search_available = bool(public.search_available)
            missing_config = list(public.missing_config or [])
            public_created, public_blocked = _store_company_candidates(
                db,
                user_id,
                [_discovery_candidate_payload(c) for c in public.candidates],
                industries,
                regions,
            )
            blocked += public_blocked
            seen_ids = {int(item.get("id") or 0) for item in created if item.get("id")}
            for item in public_created:
                company_id = int(item.get("id") or 0)
                if company_id and company_id not in sent_company_ids and company_id not in excluded_company_ids and company_id not in seen_ids:
                    created.append(item)
                    seen_ids.add(company_id)
            if not created:
                discovery_status = public.status
            elif len(created) < n:
                discovery_status = "partial"
        if not created:
            discovery_status = "empty"

    status_payload = _discovery_status_payload(
        mode,
        n,
        len(created),
        blocked,
        queries,
        errors,
        search_available=search_available,
        missing_config=missing_config,
        status=discovery_status,
    )
    if campaign is not None:
        campaign.search_available = bool(status_payload["search_available"])
        campaign.actual_count = len(created)
        campaign.shortfall = int(status_payload["shortfall"])
        campaign.missing_config_json = _dumps(status_payload["missing_config"])
        campaign.status = "ranking" if created else status_payload["status"]
    db.commit()
    record_compliance_event(
        db,
        user_id,
        "discover_companies",
        "low",
        "allowed",
        "仅使用授权/公开/手动来源生成候选,并只保存公司级公开联系方式",
        {"count": len(created), "blocked": blocked, "mode": mode, "campaign_id": campaign.id if campaign else None, **status_payload},
    )
    payload = {
        "companies": created,
        "count": len(created),
        "blocked": blocked,
        "discovery_mode": mode,
        "queries": queries,
        "errors": errors,
        "shortfall": status_payload["shortfall"],
        "requested_limit": n,
        "actual_count": len(created),
        "can_expand": status_payload["can_expand"],
        "search_available": status_payload["search_available"],
        "missing_config": status_payload["missing_config"],
        "status": status_payload["status"],
        "source_policy": "manual/public/authorized only",
    }
    if campaign is not None:
        db.refresh(campaign)
        payload["campaign"] = serialize_recommendation_campaign(campaign)
    return payload


def serialize_lead(
    row: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM | None = None,
    company_contacts: list[dict[str, Any]] | None = None,
    evidence_payload: list[dict[str, Any] | None] | None = None,
    public_base_url: str = "",
) -> dict[str, Any]:
    report_snapshot = _loads(row.report_snapshot, {})
    has_email = any(
        _is_email(str(c.get("email") or ""))
        for c in (company_contacts or [])
        if isinstance(c, dict) and c.get("consent_status") not in BLOCKED_CONTACT_CONSENT
    )
    payload = {
        "id": row.id,
        "user_id": row.user_id,
        "company_id": row.company_id,
        "company": serialize_company(company, company_contacts) if company else None,
        "has_email": bool(has_email),
        "profile_id": row.profile_id,
        "campaign_id": row.campaign_id,
        "source_report_table": row.source_report_table,
        "source_report_id": row.source_report_id,
        "report_snapshot": report_snapshot,
        "report_status": str(report_snapshot.get("status") or ""),
        "report_id": row.source_report_id,
        "score": row.score,
        "rank": row.rank,
        "reason": row.reason,
        "status": row.status,
        "status_reason": row.status_reason,
        "status_note": row.status_note,
        "status_updated_by": row.status_updated_by,
        "status_updated_at": row.status_updated_at.isoformat() if row.status_updated_at else None,
        "next_followup_at": row.next_followup_at.isoformat() if row.next_followup_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    payload.update(_lead_scoring_fields(row, company, company_contacts, evidence_payload))
    db = object_session(row)
    if db is not None:
        share = _active_report_share_for_lead(db, row.user_id, row.id)
        if share is not None:
            report = _full_report_for_share(db, share)
            share_payload = serialize_report_share(share, report, public_base_url)
            payload["report_share"] = share_payload
            payload["report_status"] = share_payload.get("report_status") or payload.get("report_status") or ""
            payload["report_id"] = share_payload.get("report_id") or payload.get("report_id") or ""
            payload["public_report_url"] = share_payload.get("public_url") or ""
            payload["share_token"] = share_payload.get("share_token") or ""
    return payload


def _name_tokens(value: str) -> list[str]:
    text = str(value or "").strip().casefold()
    if not text:
        return []
    suffixes = ("股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "集团", "公司", "官网", "官方网站")
    for suffix in suffixes:
        text = text.replace(suffix.casefold(), " ")
    parts = re.split(r"[\s,，/｜|、()（）·._\-]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def _report_matches_company(company: SalesAgentCompanyORM, report: dict[str, Any]) -> bool:
    if not report.get("has_real_detection"):
        return False
    report_domain = normalize_domain(str(report.get("url") or ""))
    company_domain = normalize_domain(company.website)
    if report_domain and company_domain and report_domain == company_domain:
        return True
    company_name = (company.name or "").casefold()
    brand_values = [str(report.get("brand_name") or "")]
    brand_values.extend(str(x) for x in (report.get("brand_terms") or []))
    for brand in brand_values:
        for token in _name_tokens(brand):
            if token and token in company_name:
                return True
    return False


def _score_company(
    company: SalesAgentCompanyORM,
    profile: SalesAgentCustomerProfileORM | None,
    report: dict[str, Any],
    can_reference_report: bool,
) -> tuple[int, str]:
    score = 50
    reasons: list[str] = []
    if profile is not None:
        industries = [str(x).lower() for x in _loads(profile.industries_json, [])]
        regions = [str(x).lower() for x in _loads(profile.regions_json, [])]
        if company.industry and any(x and x in company.industry.lower() for x in industries):
            score += 20
            reasons.append("行业与 ICP 匹配")
        if company.region and any(x and x in company.region.lower() for x in regions):
            score += 10
            reasons.append("地区与 ICP 匹配")
    if company.website:
        score += 5
        reasons.append("有官网可诊断")
    if can_reference_report:
        total = report.get("total_score")
        try:
            total_num = int(total)
            score += max(0, min(15, (100 - total_num) // 4))
        except Exception:  # noqa: BLE001
            score += 8
        reasons.append("目标企业与真实报告匹配,可做最终推荐")
    elif report.get("has_real_detection"):
        reasons.append("报告不匹配,仅初步推荐")
    else:
        reasons.append("无真实报告,仅初步推荐")
    return max(0, min(100, score)), "；".join(reasons)


def rank_companies(
    db: Session,
    user_id: int,
    profile_id: int | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
    limit: int = MAX_RECOMMENDATIONS,
    auto_discover: bool = True,
    candidate_company_ids: list[int] | None = None,
    campaign_id: int | None = None,
    exclude_company_ids: set[int] | None = None,
) -> dict[str, Any]:
    n = _clamp_limit(limit)
    profile = _profile_or_latest(db, user_id, profile_id) if profile_id is not None else None
    if profile_id is not None and profile is None:
        raise SalesAgentError("客户画像不存在或不属于当前用户。", status_code=404)
    campaign = _campaign_or_latest(db, user_id, campaign_id) if campaign_id is not None else None
    if campaign_id is not None and campaign is None:
        raise SalesAgentError("推荐任务不存在或不属于当前用户。", status_code=404)
    seller = _latest_seller_profile(db, user_id, campaign.seller_profile_id) if campaign and campaign.seller_profile_id else _seller_profile_by_legacy(db, user_id, profile.id if profile else None)
    account_icp = _latest_account_icp(db, user_id, campaign.account_icp_id, seller.id if seller else None) if campaign and campaign.account_icp_id else (_account_icp_by_legacy(db, user_id, profile.id if profile else None, seller.id if seller else None) if profile else None)
    if campaign is None:
        inds = _usable_profile_industries(profile)
        regs = _loads(profile.regions_json, []) if profile else []
        if not inds or not regs:
            raise SalesAgentError(_recommendation_requirements_message(missing_industry=not inds, missing_region=not regs, include_limit=False), status_code=400)
        campaign = create_recommendation_campaign(
            db,
            user_id,
            seller_profile_id=seller.id if seller else None,
            account_icp_id=account_icp.id if account_icp else None,
            legacy_profile_id=profile.id if profile else None,
            industries=inds,
            regions=regs,
            limit=n,
            service_scope_override=[],
            source_report_table=source_report_table,
            source_report_id=source_report_id,
            target_company_size=(account_icp.target_company_size if account_icp else ""),
            commit=False,
        )
        db.commit()
        db.refresh(campaign)
    if not _campaign_industries(campaign):
        raise SalesAgentError(_recommendation_requirements_message(missing_industry=True, missing_region=False, include_limit=False), status_code=400)
    report = get_report_summary(db, user_id, source_report_table, source_report_id) if (source_report_table or source_report_id) else {}
    if not report and campaign is not None and (campaign.source_report_table or campaign.source_report_id):
        report = get_report_summary(db, user_id, campaign.source_report_table, campaign.source_report_id)
    if not report and profile is not None:
        report = _loads(profile.report_snapshot, {})
    report_table = report.get("source_report_table") or ""
    report_id = str(report.get("source_report_id") or "")
    candidate_filter_active = candidate_company_ids is not None
    sent_company_ids = _already_sent_company_ids(db, user_id)
    excluded_company_ids = {int(x) for x in (exclude_company_ids or set()) if x}
    candidate_ids = [int(x) for x in (candidate_company_ids or []) if str(x).strip() and int(x) not in sent_company_ids and int(x) not in excluded_company_ids]
    if candidate_filter_active:
        if candidate_ids:
            # When candidate_ids come from the DB candidate pool, industry/region only feed score explanations
            # and no longer filter the candidate range, ensuring any industry takes the default count from this candidate batch.
            existing_count = db.query(SalesAgentCompanyORM).filter(SalesAgentCompanyORM.id.in_(candidate_ids)).count()
        else:
            existing_count = 0
    else:
        base_q = _exclude_company_ids(_exclude_already_sent_companies(_campaign_company_filter(db.query(SalesAgentCompanyORM), campaign, account_icp), db, user_id), excluded_company_ids)
        existing_count = base_q.count()
    discovery_meta: dict[str, Any] = {"triggered": False}
    if auto_discover and existing_count < n:
        discovery_meta = discover_companies(db, user_id, profile_id=profile.id if profile else None, limit=n - existing_count, campaign_id=campaign.id, exclude_company_ids=excluded_company_ids)
        discovery_meta["triggered"] = True
    if candidate_filter_active and not candidate_ids:
        companies = []
    elif candidate_ids:
        companies = (
            _exclude_company_ids(_exclude_already_sent_companies(db.query(SalesAgentCompanyORM), db, user_id), excluded_company_ids)
            .filter(SalesAgentCompanyORM.id.in_(candidate_ids))
            .order_by(_company_email_order_expr().desc(), SalesAgentCompanyORM.updated_at.desc(), SalesAgentCompanyORM.id.desc())
            .limit(n * 2)
            .all()
        )
    else:
        query = _exclude_company_ids(_exclude_already_sent_companies(_campaign_company_filter(db.query(SalesAgentCompanyORM), campaign, account_icp), db, user_id), excluded_company_ids)
        # When querying the DB directly by campaign, companies with email must come first; otherwise truncating to n*2 by update time
        # lets newly imported companies without email push companies with email out of the candidate window.
        query = query.order_by(_company_email_order_expr().desc(), SalesAgentCompanyORM.updated_at.desc(), SalesAgentCompanyORM.id.desc()).limit(n * 2)
        companies = query.all()
    scored = []
    for company in companies:
        matched_report = report if _report_matches_company(company, report) else {}
        if not matched_report:
            history_report = _latest_matching_full_report(db, user_id, company)
            if history_report is not None:
                matched_report = _full_report_summary(history_report)
        can_reference_report = _report_matches_company(company, matched_report)
        score, reason, evidences = _score_company_for_campaign(company, seller, account_icp, campaign, matched_report or report, can_reference_report)
        has_sendable_email = _company_has_sendable_email(db, company.id)
        if has_sendable_email:
            score = min(100, score + 12)
            reason = f"{reason}；有企业邮箱可直接发送" if reason else "有企业邮箱可直接发送"
            evidences.append({"evidence_type": "contact", "source_type": company.source or "public_company_site", "source_url": company.website, "evidence_text": "企业存在有效邮箱,可直接生成并发送邮件。", "scoring_category": "sendable_email", "score_delta": 12, "confidence": "medium"})
        scored.append((has_sendable_email, score, reason, can_reference_report, company, evidences, matched_report))
    if candidate_ids:
        candidate_order = {company_id: idx for idx, company_id in enumerate(candidate_ids)}
        scored.sort(key=lambda x: (not x[0], candidate_order.get(int(x[4].id), len(candidate_order))))
    else:
        scored.sort(key=lambda x: (not x[0], -x[1], x[4].id))
    leads: list[dict[str, Any]] = []
    final_count = 0
    evidence_summary: list[dict[str, Any]] = []
    for rank, (has_sendable_email, score, reason, can_reference_report, company, evidences, matched_report) in enumerate(scored[:n], start=1):
        lead = (
            db.query(SalesAgentLeadRecommendationORM)
            .filter(
                SalesAgentLeadRecommendationORM.user_id == user_id,
                SalesAgentLeadRecommendationORM.company_id == company.id,
                SalesAgentLeadRecommendationORM.campaign_id == campaign.id,
            )
            .first()
        )
        recommendation_type = "最终推荐" if can_reference_report else "初步推荐"
        if can_reference_report:
            final_count += 1
        safe_reason = f"{recommendation_type}:{reason}。"
        if not can_reference_report:
            safe_reason += "未用该企业检测结论。"
        if lead is None:
            lead = SalesAgentLeadRecommendationORM(
                user_id=user_id,
                company_id=company.id,
                profile_id=profile.id if profile else None,
                campaign_id=campaign.id,
                status="Recommended",
            )
            db.add(lead)
        lead.profile_id = profile.id if profile else lead.profile_id
        lead.campaign_id = campaign.id
        active_report = None
        if can_reference_report:
            active_report_id = str(matched_report.get("source_report_id") or report_id or "")
            active_report = _full_report_by_id(db, user_id, active_report_id) if active_report_id else None
            lead.source_report_table = "full_diagnosis_reports"
            lead.source_report_id = active_report_id
            lead.report_snapshot = _dumps(matched_report or report)
        else:
            lead.source_report_table = ""
            lead.source_report_id = ""
            lead.report_snapshot = _dumps({})
        lead.score = score
        lead.rank = rank
        lead.reason = safe_reason
        if lead.status == "New":
            lead.status = "Recommended"
        db.flush()
        if active_report is not None and active_report.status in {"generating", "ready"}:
            _create_report_share(db, user_id, lead.id, company.id, active_report.id)
            if active_report.status == "ready" and lead.status == "Recommended":
                lead.status = "Diagnosed"
            db.flush()
        saved_evidence = [create_lead_evidence(db, user_id, campaign.id, lead.id, company.id, ev) for ev in evidences]
        serialized_evidence = [serialize_lead_evidence(ev) for ev in saved_evidence]
        company_contacts = _company_contact_summary(db, company.id)
        data = serialize_lead(lead, company, company_contacts, serialized_evidence)
        data["evidence"] = serialized_evidence
        leads.append(data)
        evidence_summary.extend([x for x in serialized_evidence if x])
    initial_count = len(leads) - final_count
    shortfall = max(0, n - len(leads))
    mode = "final" if leads and final_count == len(leads) else ("mixed" if final_count else "initial")
    discovery_shortfall = int(discovery_meta.get("shortfall") or 0) if discovery_meta.get("triggered") else 0
    search_available = discovery_meta.get("search_available")
    missing_config = discovery_meta.get("missing_config") or []
    if search_available is None:
        capability = search_capability()
        search_available = capability["search_available"]
        missing_config = capability.get("missing_config") or []
    can_expand = bool(search_available) and (shortfall > 0 or discovery_shortfall > 0)
    campaign.actual_count = len(leads)
    campaign.shortfall = shortfall
    campaign.search_available = bool(search_available)
    campaign.missing_config_json = _dumps(missing_config)
    campaign.status = "completed" if len(leads) >= n else ("partial" if leads else "empty")
    table_rows = _recommendation_table_rows(leads)
    result_card = _recommendation_card_payload(leads, requested_limit=n, actual_count=len(leads), status="ok" if len(leads) >= n else ("partial" if leads else "empty"))
    db.commit()
    db.refresh(campaign)
    record_compliance_event(
        db,
        user_id,
        "rank_companies",
        "low",
        "allowed",
        "基于本次推荐任务生成推荐排序和证据链",
        {
            "count": len(leads),
            "campaign_id": campaign.id,
            "has_real_detection": bool(report.get("has_real_detection")),
            "report_matched_count": final_count,
            "requested_limit": n,
            "shortfall": shortfall,
            "discovery": discovery_meta,
        },
    )
    return {
        "leads": leads,
        "count": len(leads),
        "actual_count": len(leads),
        "requested_limit": n,
        "shortfall": shortfall,
        "mode": mode,
        "final_count": final_count,
        "initial_count": initial_count,
        "report_matched_count": final_count,
        "search_available": bool(search_available),
        "missing_config": missing_config,
        "can_expand": can_expand,
        "status": "ok" if len(leads) >= n else ("partial" if leads else "empty"),
        "recommendation_table": table_rows,
        "markdown_table": _recommendation_markdown_table(table_rows),
        "result_card": result_card,
        "table_columns": ["企业", "官网", "公开联系方式"],
        "discovery": discovery_meta,
        "campaign": serialize_recommendation_campaign(campaign),
        "seller_profile": _seller_profile_payload_for_recommendation(db, user_id, seller),
        "account_icp": serialize_account_icp(account_icp),
        "evidence_summary": evidence_summary[:50],
    }


def continue_recommend_companies_workflow(
    db: Session,
    user_id: int,
    limit: int | None = None,
    seller_profile_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Continue recommending a new batch with the previous round's conditions, excluding historically recommended/sent companies."""
    previous = _campaign_or_latest(db, user_id, seller_profile_id=seller_profile_id)
    requested = _clamp_limit(limit or (previous.requested_limit if previous else 0) or (previous.limit if previous else 0) or DEFAULT_RECOMMENDATIONS)
    if previous is None:
        payload = _campaign_guidance_payload(True, True, requested_limit=requested)
        payload["message"] = "还没有找到上一轮推荐条件。请先告诉我目标行业和地区,我再开始推荐。"
        payload["continued_from_campaign_id"] = None
        return payload

    industries = _campaign_industries(previous)
    regions = _campaign_regions(previous)
    if not industries or not regions:
        payload = _campaign_guidance_payload(
            missing_industry=not industries,
            missing_region=not regions,
            requested_limit=requested,
            campaign=previous,
        )
        payload["message"] = "上一轮推荐条件不完整。请先补充目标行业和地区,我再继续推荐。"
        payload["continued_from_campaign_id"] = previous.id
        return payload

    seller = _latest_seller_profile(db, user_id, previous.seller_profile_id) if previous.seller_profile_id else None
    service_scope = _loads(previous.service_scope_override_json, [])
    if not service_scope and seller is not None:
        service_scope = _loads(seller.service_scope_json, [])
    excluded = _already_recommended_company_ids(db, user_id)
    result = recommend_companies_workflow(
        db,
        user_id,
        profile_id=previous.legacy_profile_id,
        limit=requested,
        service_scope=service_scope,
        industries=industries,
        regions=regions,
        company_size=seller.company_size if seller is not None else "",
        typical_customers=None,
        seed_companies=[],
        source_report_table=previous.source_report_table,
        source_report_id=previous.source_report_id,
        seller_profile_id=previous.seller_profile_id,
        account_icp_id=previous.account_icp_id,
        target_company_size=previous.target_company_size,
        service_scope_override=service_scope,
        session_id=session_id,
        exclude_company_ids=excluded,
    )
    result["continued_from_campaign_id"] = previous.id
    result["excluded_recommended_count"] = len(excluded)
    campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
    if campaign is not previous and isinstance(campaign, dict):
        campaign["continued_from_campaign_id"] = previous.id
    return result


def recommend_companies_workflow(
    db: Session,
    user_id: int,
    profile_id: int | None = None,
    limit: int = DEFAULT_RECOMMENDATIONS,
    service_scope: list[str] | None = None,
    industries: list[str] | None = None,
    regions: list[str] | None = None,
    company_size: str = "",
    typical_customers: list[str] | None = None,
    seed_companies: list[dict[str, Any]] | None = None,
    source_report_table: str = "",
    source_report_id: str = "",
    seller_profile_id: int | None = None,
    account_icp_id: int | None = None,
    campaign_id: int | None = None,
    target_company_size: str = "",
    service_scope_override: list[str] | None = None,
    question_options: list[Any] | None = None,
    session_id: int | None = None,
    exclude_company_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Deterministic orchestration of company recommendation: current filters -> recommendation task -> DB candidate pool -> ranking and evidence chain."""
    n = _clamp_limit(limit)
    seeds = [dict(x) for x in (seed_companies or [])]
    if campaign_id is None and seller_profile_id is None and profile_id is None:
        inline_missing: list[str] = []
        # New recommendations first confirm company/team size or professional capability, so we do not keep asking "what do you sell" after the user stated a service direction.
        if not str(company_size or "").strip() and not _clean_list(service_scope, 20):
            inline_missing.append("公司/团队规模或专业能力")
        elif not _clean_list(typical_customers, 20):
            inline_missing.append("典型客户或适合服务的客户类型")
        elif not _clean_list(service_scope, 20):
            inline_missing.append("你准备推广/销售的服务范围")
        if inline_missing:
            return _profile_guidance_payload(inline_missing, requested_limit=n, question_options=question_options)
    if campaign_id is None and (not _clean_list(industries, 20) or not _clean_list(regions, 20)):
        return _campaign_guidance_payload(
            missing_industry=not _clean_list(industries, 20),
            missing_region=not _clean_list(regions, 20),
            requested_limit=n,
            question_options=question_options,
        )
    profile, seller, account_icp, campaign, report = _profile_for_recommendation(
        db,
        user_id,
        profile_id,
        service_scope,
        industries,
        regions,
        n,
        source_report_table,
        source_report_id,
        seller_profile_id=seller_profile_id,
        account_icp_id=account_icp_id,
        campaign_id=campaign_id,
        target_company_size=target_company_size,
        service_scope_override=service_scope_override,
        seed_companies=seeds,
        company_size=company_size,
        typical_customers=typical_customers,
        session_id=session_id,
    )
    missing_profile_fields = _seller_profile_missing_fields(seller, profile)
    campaign.limit = n
    campaign.requested_limit = n
    campaign.seed_companies_json = _dumps(seeds)
    if missing_profile_fields:
        campaign.status = "need_more_info"
        campaign.missing_config_json = _dumps(missing_profile_fields)
    db.commit()
    db.refresh(campaign)
    if missing_profile_fields:
        record_compliance_event(
            db,
            user_id,
            "recommend_companies_workflow",
            "low",
            "allowed",
            "当前对话未获得用户自己的服务画像,返回聊天引导而非复用历史画像",
            {"campaign_id": campaign.id, "missing_profile_fields": missing_profile_fields},
        )
        return _profile_guidance_payload(missing_profile_fields, seller, profile, account_icp, campaign, n, question_options=question_options)
    discovery = discover_companies(
        db,
        user_id,
        profile_id=profile.id if profile else None,
        limit=n,
        seed_companies=seeds,
        campaign_id=campaign.id,
        exclude_company_ids=exclude_company_ids,
    )
    report_table = str(report.get("source_report_table") or source_report_table or campaign.source_report_table or "")
    report_id = str(report.get("source_report_id") or source_report_id or campaign.source_report_id or "")
    discovered_ids = [int(x.get("id")) for x in (discovery.get("companies") or []) if isinstance(x, dict) and x.get("id")]
    # discovery is only a preferred candidate set; an empty set must not lock subsequent ranking.
    # In production the DB candidate pool had 20k+ companies yet discovery returned empty; we should then fall back to
    # querying and ranking the DB by campaign conditions, rather than passing candidate_company_ids=[] down and getting 0 companies.
    ranking_candidate_ids = discovered_ids if discovered_ids else None
    ranking = rank_companies(
        db,
        user_id,
        profile_id=profile.id if profile else None,
        source_report_table=report_table,
        source_report_id=report_id,
        limit=n,
        auto_discover=False,
        candidate_company_ids=ranking_candidate_ids,
        campaign_id=campaign.id,
        exclude_company_ids=exclude_company_ids,
    )
    actual_count = int(ranking.get("actual_count") or ranking.get("count") or 0)
    shortfall = max(0, n - actual_count)
    search_available = bool(discovery.get("search_available"))
    if discovery.get("discovery_mode") == "seed" and shortfall:
        status = "seed_partial"
    elif not search_available and shortfall:
        status = "search_unavailable"
    elif shortfall:
        status = "partial"
    else:
        status = "ok"
    result_card = ranking.get("result_card") or _recommendation_card_payload(ranking.get("leads") or [], requested_limit=n, actual_count=actual_count, status=status)
    result = {
        "profile": serialize_profile(profile) if profile else None,
        "seller_profile": ranking.get("seller_profile") or _seller_profile_payload_for_recommendation(db, user_id, seller),
        "account_icp": ranking.get("account_icp") or serialize_account_icp(account_icp),
        "campaign": ranking.get("campaign") or serialize_recommendation_campaign(campaign),
        "discovery": discovery,
        "ranking": ranking,
        "leads": ranking.get("leads") or [],
        "count": actual_count,
        "actual_count": actual_count,
        "requested_limit": n,
        "shortfall": shortfall,
        "mode": ranking.get("mode") or "initial",
        "final_count": ranking.get("final_count") or 0,
        "initial_count": ranking.get("initial_count") or actual_count,
        "report_matched_count": ranking.get("report_matched_count") or 0,
        "search_available": search_available,
        "missing_config": discovery.get("missing_config") or [],
        "can_expand": bool(search_available and shortfall),
        "status": status,
        "recommendation_table": ranking.get("recommendation_table") or _recommendation_table_rows(ranking.get("leads") or []),
        "markdown_table": ranking.get("markdown_table") or _recommendation_markdown_table(_recommendation_table_rows(ranking.get("leads") or [])),
        "result_card": result_card,
        "table_columns": ["企业", "官网", "公开联系方式"],
        "source_policy": "manual/public/authorized only",
        "evidence_summary": ranking.get("evidence_summary") or [],
        # When candidates are limited to discovery results, industry/region only affect ranking and do not filter (see the comment at candidate_ids above).
        # Without stating this, results would not match the conditions the user just selected while the UI says "recommended per current filters".
        **_condition_match_meta(campaign, ranking.get("leads") or [], filtered=ranking_candidate_ids is not None),
    }
    record_compliance_event(
        db,
        user_id,
        "recommend_companies_workflow",
        "low",
        "allowed",
        "基于 RecommendationCampaign 执行推荐企业确定性编排",
        {k: result[k] for k in ("requested_limit", "actual_count", "shortfall", "mode", "status", "search_available", "missing_config")},
    )
    # Deterministic validation: count, dedup, count consistency. When it fails, label it truthfully; do not silently let it pass.
    from agents.sales_agent.runtime.verify import verify_recommendation

    problems = verify_recommendation(result)
    if problems:
        result["verification_problems"] = problems
        record_compliance_event(
            db, user_id, "recommend_companies_workflow", "medium", "needs_approval",
            "推荐结果未通过自洽性校验:" + ";".join(problems),
            {"campaign_id": campaign.id, "problems": problems},
        )
    return result
