"""Sales Agent service layer: companies and contact info: candidate persistence, dedup, public contact normalization."""
from __future__ import annotations

from datetime import datetime, timedelta
import json

from typing import Any
from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import Session
from agents.sales_agent.constants import (
    BLOCKED_CONTACT_CONSENT,
    CONTACT_CONFIDENCE,
    CONTACT_CONSENT_STATUSES,
    CONTACT_SOURCES,
    DEFAULT_RECOMMENDATIONS,
    DISCOVERY_SOURCES,
)
from agents.sales_agent.discovery import normalize_domain
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyImportBatchORM,
    SalesAgentCompanyORM,
    SalesAgentCompanySourceORM,
    SalesAgentCustomerProfileORM,
)

from agents.sales_agent.service.common import _clamp_limit, _dumps, _is_email, _loads, record_compliance_event
from agents.sales_agent.service.entities import _company_contact_summary, serialize_company, serialize_contact


def _public_contact_text(company: dict[str, Any]) -> str:
    values: list[str] = []
    form_urls: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)

    def add_form(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in form_urls:
            form_urls.append(text)

    contacts = [c for c in (company.get("contacts") or []) if isinstance(c, dict)]
    for contact in contacts:
        if contact.get("consent_status") in BLOCKED_CONTACT_CONSENT:
            continue
        if contact.get("is_reachable") is False:
            continue
        add(contact.get("email"))
        add(contact.get("phone"))
        add_form(contact.get("contact_form_url"))
    if not values and not form_urls:
        add(company.get("email"))
        add(company.get("phone"))
        add_form(company.get("contact_form_url"))
    if form_urls:
        add("官网联系表单" if len(form_urls) == 1 else f"官网联系表单 ×{len(form_urls)}")
    return "、".join(values) or "未发现公开公司级联系方式"


def _company_has_email(company: dict[str, Any]) -> bool:
    if _is_email(str(company.get("email") or "")):
        return True
    contacts = [c for c in (company.get("contacts") or []) if isinstance(c, dict)]
    for contact in contacts:
        if contact.get("consent_status") in BLOCKED_CONTACT_CONSENT:
            continue
        # Email sending only checks for a valid email; no longer downgraded to unavailable for empty/non-whitelisted source or is_reachable=False.
        if _is_email(str(contact.get("email") or "")):
            return True
    return False


def _company_has_email_clause():
    return exists().where(
        SalesAgentCompanyContactORM.company_id == SalesAgentCompanyORM.id,
        SalesAgentCompanyContactORM.email != "",
        SalesAgentCompanyContactORM.email.ilike("%@%.%"),
        ~SalesAgentCompanyContactORM.consent_status.in_(BLOCKED_CONTACT_CONSENT),
    )


def _company_email_order_expr():
    return case((_company_has_email_clause(), 1), else_=0)


_INDUSTRY_TERM_SUFFIXES = ("行业", "企业", "公司")
_INDUSTRY_SYNONYM_TERMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("律师", "法律", "法务", "律所", "合规"), ("法律服务", "律师事务所", "律所", "法务服务", "企业法务", "合规服务", "商务服务")),
)


def _industry_synonyms(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    matched: list[str] = []
    for triggers, expansions in _INDUSTRY_SYNONYM_TERMS:
        if any(trigger in text for trigger in triggers):
            matched.extend(expansions)
    return matched


def industry_search_terms(values: list[str] | None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        candidates = [value, *_industry_synonyms(value)]
        for suffix in _INDUSTRY_TERM_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix) + 1:
                candidates.append(value[: -len(suffix)].strip())
        for term in candidates:
            text = str(term or "").strip()
            if text and text not in seen:
                seen.add(text)
                terms.append(text)
    return terms


def company_industry_match_clauses(terms: list[str] | None):
    clauses = []
    for term in terms or []:
        text = str(term or "").strip()
        if not text:
            continue
        clauses.extend([
            SalesAgentCompanyORM.industry.ilike(f"%{text}%"),
            SalesAgentCompanyORM.industry_primary.ilike(f"%{text}%"),
            SalesAgentCompanyORM.industry_tags_json.ilike(f"%{text}%"),
        ])
    return clauses


def _markdown_table_cell(value: Any) -> str:
    text = str(value or "-").strip().replace("\n", " ").replace("|", "｜")
    return text or "-"


def _recommendation_table_rows(leads: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for lead in leads:
        company = lead.get("company") if isinstance(lead.get("company"), dict) else {}
        name = str(company.get("name") or lead.get("company_id") or "企业").strip()
        website = str(company.get("website") or "").strip()
        website_display = normalize_domain(website) or website or "-"
        rows.append({
            "企业": name,
            "官网": website_display,
            "公开联系方式": _public_contact_text(company),
        })
    return rows


def _short_card_text(value: Any, max_len: int = 140) -> str:
    text = str(value or "").strip()
    return f"{text[:max_len]}…" if len(text) > max_len else text


def _recommendation_card_payload(
    leads: list[dict[str, Any]],
    requested_limit: int = DEFAULT_RECOMMENDATIONS,
    actual_count: int | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    """Backend describes the recommended-company UI card; the frontend only renders per the schema."""
    items: list[dict[str, Any]] = []
    for i, lead in enumerate(leads[:_clamp_limit(requested_limit)]):
        company = lead.get("company") if isinstance(lead.get("company"), dict) else {}
        website = str(company.get("website") or "").strip()
        website_display = normalize_domain(website) or website or "-"
        industry = str(company.get("industry") or "").strip()
        region = str(company.get("region") or "").strip()
        employee_size = str(company.get("employee_size") or "").strip()
        meta = [
            f"目标行业:{industry}" if industry else "",
            f"目标地区:{region}" if region else "",
            f"企业规模:{employee_size}" if employee_size else "",
        ]
        scoring = lead.get("scoring") if isinstance(lead.get("scoring"), dict) else {}
        match_score = int(lead.get("match_score") or lead.get("score") or 0)
        opportunity_score = lead.get("opportunity_score")
        report_share = lead.get("report_share") if isinstance(lead.get("report_share"), dict) else {}
        items.append({
            "title": str(company.get("name") or lead.get("company_id") or f"企业 {i + 1}").strip(),
            "lead_id": lead.get("id"),
            "rank": int(lead.get("rank") or i + 1),
            "score": match_score,
            "match_score": match_score,
            "opportunity_score": opportunity_score,
            "opportunity_score_label": str(lead.get("opportunity_score_label") or scoring.get("opportunity_score_label") or "待评估"),
            "confidence": str(lead.get("confidence") or scoring.get("confidence") or "low"),
            "confidence_level": str(lead.get("confidence_level") or scoring.get("confidence_level") or "低"),
            "recommended_action": str(lead.get("recommended_action") or scoring.get("recommended_action") or "先补充公开证据后再触达。"),
            "score_summary": str(lead.get("score_summary") or scoring.get("score_summary") or ""),
            "report_status": str(lead.get("report_status") or report_share.get("report_status") or ""),
            "report_id": str(lead.get("report_id") or report_share.get("report_id") or lead.get("source_report_id") or ""),
            "public_report_url": str(lead.get("public_report_url") or report_share.get("public_url") or ""),
            "share_token": str(lead.get("share_token") or report_share.get("share_token") or report_share.get("public_token") or ""),
            "has_email": bool(lead.get("has_email") if "has_email" in lead else _company_has_email(company)),
            "report_share": report_share,
            "scoring": scoring,
            "meta": " · ".join([x for x in meta if x]),
            "badges": [x for x in meta if x],
            "reason": _short_card_text(lead.get("reason"), 160),
            "website": website_display,
            "contact": _public_contact_text(company),
        })
    count = len(leads) if actual_count is None else int(actual_count)
    return {
        "kind": "company_recommendations",
        "layout": "card_grid",
        "title": "企业推荐预览",
        "subtitle": f"已推荐 {count} / 目标 {_clamp_limit(requested_limit)}",
        "status": status or "ok",
        "items": items,
    }


def _recommendation_markdown_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    lines = ["| 企业 | 官网 | 公开联系方式 |", "|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('企业'))} | {_markdown_table_cell(row.get('官网'))} | {_markdown_table_cell(row.get('公开联系方式'))} |"
        )
    return "\n".join(lines)


def _company_domain(row: SalesAgentCompanyORM) -> str:
    return normalize_domain(row.website)


def _candidate_domain(item: dict[str, Any]) -> str:
    # Only the company's official website domain may be a DB dedup key; a public directory's source_url domain maps to many companies
    # and must not be used to merge company records.
    return normalize_domain(str(item.get("website") or ""))


def _source_rank(source: str) -> int:
    return {
        "public_company_site": 5,
        "public_registry": 4,
        "official_directory": 3,
        "authorized_partner": 3,
        "user_provided": 2,
        "manual": 2,
        "internal_seed": 0,
    }.get(source, 0)


_MUNICIPALITIES = ("北京", "上海", "天津", "重庆", "香港", "澳门")
_PROVINCE_SUFFIXES = ("省", "自治区", "特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区")


def _canonical_company_name(name: str) -> str:
    text = (name or "").strip()
    for lb, rb in (("（", "）"), ("(", ")"), ("[", "]"), ("【", "】")):
        while lb in text and rb in text[text.index(lb):]:
            start = text.index(lb)
            end = text.index(rb, start)
            text = (text[:start] + text[end + 1:]).strip()
    return "".join(text.split())[:255]


def _split_company_region(region: str) -> tuple[str, str]:
    text = (region or "").strip()
    if not text:
        return "", ""
    parts = [p for p in text.replace("/", " ").replace(",", " ").replace("，", " ").split() if p]
    if not parts:
        return "", ""
    head = parts[0]
    if any(head.startswith(m) for m in _MUNICIPALITIES):
        return head[:64], (parts[1][:64] if len(parts) > 1 else head[:64])
    if len(parts) >= 2:
        return head[:64], parts[1][:64]
    if any(head.endswith(s) for s in _PROVINCE_SUFFIXES):
        return head[:64], ""
    return "", head[:64]


def _primary_company_industry(industry: str) -> str:
    text = (industry or "").strip()
    if not text:
        return ""
    for sep in ("/", "|", "、", ",", "，", ">"):
        if sep in text:
            text = text.split(sep)[0]
            break
    return text.strip()[:64]


def _company_industry_tags(industry: str) -> list[str]:
    text = (industry or "").strip()
    if not text:
        return []
    for sep in ("|", "、", ",", "，", ">"):
        text = text.replace(sep, "/")
    return [t.strip()[:64] for t in text.split("/") if t.strip()]


SALES_AGENT_SEED_SOURCE_NAME = "Sales Agent 默认候选企业池"


def _apply_company_seed_fields(company: SalesAgentCompanyORM, item: dict[str, Any], source_id: int | None = None, import_batch_id: int | None = None) -> None:
    industry = str(item.get("industry") or company.industry or "").strip()
    region = str(item.get("region") or company.region or "").strip()
    province, city = _split_company_region(region)
    domain = _candidate_domain(item) or _company_domain(company)
    if not company.canonical_name:
        company.canonical_name = _canonical_company_name(company.name)
    if not company.normalized_domain and domain:
        company.normalized_domain = domain
    if not company.industry_primary and industry:
        company.industry_primary = _primary_company_industry(industry)
    if not _loads(company.industry_tags_json, []) and industry:
        company.industry_tags_json = json.dumps(_company_industry_tags(industry), ensure_ascii=False)
    if not company.province and province:
        company.province = province
    if not company.city and city:
        company.city = city
    if not company.source_url and item.get("source_url"):
        company.source_url = str(item.get("source_url") or "").strip()
    if not company.confidence and item.get("confidence"):
        company.confidence = str(item.get("confidence") or "medium").strip()
    if source_id and not company.source_id:
        company.source_id = source_id
    if import_batch_id and not company.import_batch_id:
        company.import_batch_id = import_batch_id
    if not company.data_status:
        company.data_status = "active"


def _phone_key(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("86") and len(digits) > 11:
        digits = digits[2:]
    return digits


def _url_key(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def _contact_dicts_from_item(item: dict[str, Any], discovery_source: str) -> list[dict[str, str]]:
    raw_contacts = item.get("contacts") if isinstance(item.get("contacts"), list) else []
    raw_items = [x for x in raw_contacts if isinstance(x, dict)]
    if any(str(item.get(k) or "").strip() for k in ("email", "phone", "contact_form_url")):
        raw_items.insert(0, item)

    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    default_source = discovery_source if discovery_source in CONTACT_SOURCES else ""
    for raw in raw_items:
        email = str(raw.get("email") or "").strip().lower()
        phone = str(raw.get("phone") or "").strip()
        form_url = str(raw.get("contact_form_url") or "").strip()
        if not any((email, phone, form_url)):
            continue
        contact_source = str(raw.get("contact_source") or raw.get("source") or item.get("contact_source") or default_source).strip()
        source_url = str(
            raw.get("contact_source_url")
            or raw.get("source_url")
            or item.get("contact_source_url")
            or item.get("source_url")
            or ""
        ).strip()
        confidence = str(raw.get("confidence") or item.get("confidence") or "medium").strip()
        consent = str(raw.get("consent_status") or item.get("consent_status") or "unknown").strip()
        key = (email, _phone_key(phone), _url_key(form_url))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "contact_name": str(raw.get("contact_name") or item.get("contact_name") or "").strip(),
            "position": str(raw.get("position") or item.get("position") or "").strip(),
            "email": email,
            "phone": phone,
            "contact_form_url": form_url,
            "contact_source": contact_source,
            "source_url": source_url,
            "confidence": confidence if confidence in CONTACT_CONFIDENCE else "medium",
            "consent_status": consent if consent in CONTACT_CONSENT_STATUSES else "unknown",
        })
    return out


def _find_matching_contact(rows: list[SalesAgentCompanyContactORM], email: str, phone: str, form_url: str) -> SalesAgentCompanyContactORM | None:
    email_key = email.strip().lower()
    phone_key = _phone_key(phone)
    form_key = _url_key(form_url)
    for row in rows:
        if email_key and row.email.strip().lower() == email_key:
            return row
        if phone_key and _phone_key(row.phone) == phone_key:
            return row
        if form_key and _url_key(row.contact_form_url) == form_key:
            return row
    return None


def _upsert_company_contact(
    db: Session,
    company: SalesAgentCompanyORM,
    contact_data: dict[str, str],
    source_id: int | None = None,
) -> SalesAgentCompanyContactORM:
    rows = db.query(SalesAgentCompanyContactORM).filter(SalesAgentCompanyContactORM.company_id == company.id).all()
    contact = _find_matching_contact(rows, contact_data["email"], contact_data["phone"], contact_data["contact_form_url"])
    if contact is None:
        contact = SalesAgentCompanyContactORM(
            company_id=company.id,
            contact_name=contact_data["contact_name"],
            position=contact_data["position"],
            email=contact_data["email"],
            phone=contact_data["phone"],
            contact_form_url=contact_data["contact_form_url"],
            source=contact_data["contact_source"],
            source_url=contact_data["source_url"],
            confidence=contact_data["confidence"],
            is_reachable=contact_data["consent_status"] not in BLOCKED_CONTACT_CONSENT,
            consent_status=contact_data["consent_status"],
            source_id=source_id,
        )
        db.add(contact)
        db.flush()
        return contact

    if not contact.contact_name and contact_data["contact_name"]:
        contact.contact_name = contact_data["contact_name"]
    if not contact.position and contact_data["position"]:
        contact.position = contact_data["position"]
    if not contact.email and contact_data["email"]:
        contact.email = contact_data["email"]
    if not contact.phone and contact_data["phone"]:
        contact.phone = contact_data["phone"]
    if not contact.contact_form_url and contact_data["contact_form_url"]:
        contact.contact_form_url = contact_data["contact_form_url"]
    if contact_data["source_url"] and contact.source_url != contact_data["source_url"]:
        contact.source_url = contact_data["source_url"]
    if contact_data["contact_source"] in CONTACT_SOURCES:
        contact.source = contact_data["contact_source"]
    if source_id and not contact.source_id:
        contact.source_id = source_id
    contact.confidence = contact_data["confidence"]
    contact.consent_status = contact_data["consent_status"]
    contact.is_reachable = contact.consent_status not in BLOCKED_CONTACT_CONSENT
    db.flush()
    return contact


def _usable_profile_industries(profile: SalesAgentCustomerProfileORM | None) -> list[str]:
    if profile is None:
        return []
    return [str(x).strip() for x in _loads(profile.industries_json, []) if str(x).strip() and "待补充" not in str(x)]


def _usable_profile_regions(profile: SalesAgentCustomerProfileORM | None) -> list[str]:
    if profile is None:
        return []
    return [str(x).strip() for x in _loads(profile.regions_json, []) if str(x).strip() and str(x) != "全国" and "待补充" not in str(x)]


def _profile_company_filter(q, profile: SalesAgentCustomerProfileORM | None):
    industries = _usable_profile_industries(profile)
    regions = _usable_profile_regions(profile)
    if industries:
        q = q.filter(or_(*[SalesAgentCompanyORM.industry.ilike(f"%{industry}%") for industry in industries]))
    if regions:
        q = q.filter(or_(*[SalesAgentCompanyORM.region.ilike(f"%{region}%") for region in regions]))
    return q


def _store_company_candidates(
    db: Session,
    user_id: int,
    candidates: list[dict[str, Any]],
    industries: list[str],
    regions: list[str],
    source_id: int | None = None,
    import_batch_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    created: list[dict[str, Any]] = []
    seen_company_ids: set[int] = set()
    blocked = 0
    known_domains = {
        _company_domain(c): c
        for c in db.query(SalesAgentCompanyORM).filter(SalesAgentCompanyORM.website != "").all()
        if _company_domain(c)
    }

    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        source = str(item.get("source") or "manual").strip()
        if source not in DISCOVERY_SOURCES:
            blocked += 1
            record_compliance_event(db, user_id, "discover_company", "medium", "blocked", "候选企业来源不在授权范围", {"name": name, "source": source})
            continue
        website = str(item.get("website") or "").strip()
        region = str(item.get("region") or (regions[0] if regions else "") or "").strip()
        domain = _candidate_domain(item)
        company = known_domains.get(domain) if domain else None
        if company is None:
            company = (
                db.query(SalesAgentCompanyORM)
                .filter(SalesAgentCompanyORM.name == name, SalesAgentCompanyORM.region == region)
                .first()
            )
        if company is None:
            company = SalesAgentCompanyORM(
                name=name,
                industry=str(item.get("industry") or (industries[0] if industries else "") or "").strip(),
                region=region,
                website=website,
                employee_size=str(item.get("employee_size") or "").strip(),
                source=source,
                status="New",
            )
            db.add(company)
            db.flush()
            if domain:
                known_domains[domain] = company
        else:
            if not company.website and website:
                company.website = website
            if not company.industry and (item.get("industry") or industries):
                company.industry = str(item.get("industry") or industries[0]).strip()
            if not company.region and region:
                company.region = region
            if not company.employee_size and item.get("employee_size"):
                company.employee_size = str(item.get("employee_size") or "").strip()
            if _source_rank(source) > _source_rank(company.source):
                company.source = source
            if domain:
                known_domains[domain] = company

        _apply_company_seed_fields(company, item, source_id=source_id, import_batch_id=import_batch_id)

        saved_contacts: list[dict[str, Any]] = []
        for contact_data in _contact_dicts_from_item(item, source):
            # Explicit user requirement: a company contact can be emailed as long as it has an email, so contact persistence no longer blocks by contact_source whitelist.
            contact = _upsert_company_contact(db, company, contact_data, source_id=source_id)
            saved_contacts.append(serialize_contact(contact))
        if company.id not in seen_company_ids:
            seen_company_ids.add(company.id)
            data = serialize_company(company)
            if item.get("source_url"):
                data["source_url"] = str(item.get("source_url") or "")
            if item.get("evidence_title"):
                data["evidence_title"] = str(item.get("evidence_title") or "")
            if item.get("query"):
                data["query"] = str(item.get("query") or "")
            if saved_contacts:
                data["contacts"] = saved_contacts
                primary_contact = saved_contacts[0]
                data["email"] = primary_contact.get("email", "")
                data["phone"] = primary_contact.get("phone", "")
                data["contact_form_url"] = primary_contact.get("contact_form_url", "")
            created.append(data)

    for data in created:
        contacts = _company_contact_summary(db, int(data["id"]))
        if contacts:
            data["contacts"] = contacts
            primary_contact = contacts[0]
            data["email"] = primary_contact.get("email", "")
            data["phone"] = primary_contact.get("phone", "")
            data["contact_form_url"] = primary_contact.get("contact_form_url", "")
    return created, blocked


def _company_seed_source(db: Session) -> SalesAgentCompanySourceORM:
    row = (
        db.query(SalesAgentCompanySourceORM)
        .filter(
            SalesAgentCompanySourceORM.source_type == "manual_import",
            SalesAgentCompanySourceORM.source_name == SALES_AGENT_SEED_SOURCE_NAME,
            SalesAgentCompanySourceORM.owner_user_id.is_(None),
        )
        .first()
    )
    if row is None:
        row = SalesAgentCompanySourceORM(
            source_type="manual_import",
            source_name=SALES_AGENT_SEED_SOURCE_NAME,
            source_url="agents/sales_agent/seed_companies.py",
            owner_user_id=None,
            trust_level="high",
            weight=100,
            is_enabled=True,
            terms_note="只包含官网公开公司级联系方式;幂等导入,不覆盖人工编辑。",
        )
        db.add(row)
        db.flush()
    return row


def import_sales_agent_seed_companies(
    db: Session,
    user_id: int | None = None,
    seeds: list[dict[str, Any]] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Import the default 130 candidate companies into the DB; idempotent, does not overwrite existing manual edits."""
    from agents.sales_agent.seed_companies import sales_agent_seed_companies

    seed_items = [dict(x) for x in (seeds if seeds is not None else sales_agent_seed_companies())]
    source = _company_seed_source(db)
    before_company_ids = {int(row[0]) for row in db.query(SalesAgentCompanyORM.id).all()}
    before_contact_count = db.query(SalesAgentCompanyContactORM).count()
    batch = SalesAgentCompanyImportBatchORM(
        user_id=user_id,
        source_id=source.id,
        batch_type="seed_import",
        request_params_json=_dumps({"source": SALES_AGENT_SEED_SOURCE_NAME, "count": len(seed_items)}),
        status="running",
        requested_count=len(seed_items),
        discovered_count=len(seed_items),
    )
    db.add(batch)
    db.flush()
    created, blocked = _store_company_candidates(
        db,
        int(user_id or 0),
        seed_items,
        [],
        [],
        source_id=source.id,
        import_batch_id=batch.id,
    )
    after_company_ids = {int(row[0]) for row in db.query(SalesAgentCompanyORM.id).all()}
    inserted = len(after_company_ids - before_company_ids)
    batch.inserted_count = inserted
    batch.updated_count = max(0, len(created) - inserted)
    batch.duplicate_count = max(0, len(seed_items) - len(created) - blocked)
    batch.blocked_count = blocked
    batch.failed_count = 0
    batch.status = "completed" if blocked == 0 else "completed_with_warnings"
    batch.completed_at = datetime.utcnow()
    if commit:
        db.commit()
        db.refresh(batch)
    return {
        "source_id": source.id,
        "import_batch_id": batch.id,
        "requested_count": batch.requested_count,
        "discovered_count": batch.discovered_count,
        "inserted_count": batch.inserted_count,
        "updated_count": batch.updated_count,
        "duplicate_count": batch.duplicate_count,
        "blocked_count": batch.blocked_count,
        "contact_count_delta": max(0, db.query(SalesAgentCompanyContactORM).count() - before_contact_count),
        "status": batch.status,
    }


def _db_seed_company_query(db: Session):
    return (
        db.query(SalesAgentCompanyORM)
        .outerjoin(SalesAgentCompanySourceORM, SalesAgentCompanyORM.source_id == SalesAgentCompanySourceORM.id)
        .filter(
            SalesAgentCompanyORM.data_status == "active",
            SalesAgentCompanyORM.name != "",
            or_(
                SalesAgentCompanySourceORM.source_name == SALES_AGENT_SEED_SOURCE_NAME,
                SalesAgentCompanyORM.import_batch_id.isnot(None),
                SalesAgentCompanyORM.source.in_(["internal_seed", "public_company_site", "official_directory", "authorized_partner", "public_registry", "manual", "user_provided"]),
                SalesAgentCompanyORM.website != "",
                SalesAgentCompanyORM.industry != "",
                SalesAgentCompanyORM.region != "",
                _company_has_email_clause(),
            ),
        )
    )


def load_company_candidates_from_db(
    db: Session,
    industries: list[str] | None = None,
    regions: list[str] | None = None,
    limit: int = DEFAULT_RECOMMENDATIONS,
) -> list[dict[str, Any]]:
    """Read the default candidate pool from sales_agent_companies / contacts, no longer from the frontend or a hard-coded list."""
    n = max(_clamp_limit(limit), DEFAULT_RECOMMENDATIONS)
    q = _db_seed_company_query(db)
    filterable_industries = [str(x).strip() for x in (industries or []) if str(x).strip() and str(x).strip() not in {"不限行业", "不限制行业", "行业不限", "全行业", "不限"}]
    filterable_regions = [str(x).strip() for x in (regions or []) if str(x).strip() and str(x).strip() not in {"全国", "不限地区", "不限制地区", "地区不限"}]
    industry_clauses = company_industry_match_clauses(industry_search_terms(filterable_industries))
    if industry_clauses:
        q = q.filter(or_(*industry_clauses))
    if filterable_regions:
        q = q.filter(or_(*[SalesAgentCompanyORM.region.ilike(f"%{region}%") for region in filterable_regions]))
    rows = q.order_by(_company_email_order_expr().desc(), SalesAgentCompanyORM.updated_at.desc(), SalesAgentCompanyORM.id.desc()).limit(n * 2).all()
    if len(rows) < n and (filterable_industries or filterable_regions):
        # The DB default candidate pool must not return 0 just because industry/region literals do not match exactly.
        # Put matches first, then top up from the pool if below the default count; later ranking still gives matches a bonus.
        seen_ids = {int(row.id) for row in rows}
        fallback_q = _db_seed_company_query(db)
        if seen_ids:
            fallback_q = fallback_q.filter(~SalesAgentCompanyORM.id.in_(seen_ids))
        rows.extend(
            fallback_q.order_by(_company_email_order_expr().desc(), SalesAgentCompanyORM.updated_at.desc(), SalesAgentCompanyORM.id.desc())
            .limit(max(0, n * 2 - len(rows)))
            .all()
        )
    return [serialize_company(row, _company_contact_summary(db, row.id)) for row in rows]


def agent_recommended_companies(
    db: Session,
    user_id: int,
    company_name: str = "",
    industry: str = "",
    region: str = "",
    status: str = "",
    days: int = 0,
    limit: int = 20,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """"Which companies were recommended", for the Agent to read. Read-only: does not re-recommend or change state.

    Fields trimmed to the few that can be stated directly: no evidence chain, no report snapshot, to avoid stuffing it all into context.
    """
    from agents.sales_agent.models import SalesAgentLeadRecommendationORM

    q = (
        db.query(SalesAgentLeadRecommendationORM, SalesAgentCompanyORM)
        .join(SalesAgentCompanyORM, SalesAgentLeadRecommendationORM.company_id == SalesAgentCompanyORM.id)
        .filter(
            SalesAgentLeadRecommendationORM.user_id == user_id,
            SalesAgentLeadRecommendationORM.status != "Undone",
        )
    )
    if company_name.strip():
        q = q.filter(SalesAgentCompanyORM.name.ilike(f"%{company_name.strip()}%"))
    if industry.strip():
        q = q.filter(SalesAgentCompanyORM.industry.ilike(f"%{industry.strip()}%"))
    if region.strip():
        q = q.filter(SalesAgentCompanyORM.region.ilike(f"%{region.strip()}%"))
    if status.strip():
        q = q.filter(SalesAgentLeadRecommendationORM.status == status.strip())
    if since is not None:
        q = q.filter(SalesAgentLeadRecommendationORM.created_at >= since)
    elif days and int(days) > 0:
        q = q.filter(SalesAgentLeadRecommendationORM.created_at >= datetime.utcnow() - timedelta(days=int(days)))
    if until is not None:
        q = q.filter(SalesAgentLeadRecommendationORM.created_at < until)

    total = q.count()
    rows = q.order_by(SalesAgentLeadRecommendationORM.id.desc()).limit(max(1, min(int(limit or 20), 50))).all()

    items = []
    leads_payload = []
    for lead, company in rows:
        recommended_at = (lead.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + " (北京时间)" if lead.created_at else ""
        items.append({
            "lead_id": lead.id,
            "company_name": company.name,
            "industry": company.industry or "",
            "region": company.region or "",
            "employee_size": company.employee_size or "",
            "website": company.website or "",
            "match_score": lead.score,
            "status": lead.status,
            "has_report": bool(lead.source_report_id),
            "recommended_at": recommended_at,
        })
        # Same shape as recommend_companies: the frontend preview card with pagination/search/details consumes this structure directly,
        # so the model need not hand-write a markdown table (2026-08-23: listing 100 companies, the model listed only 6 rows plus an ellipsis).
        leads_payload.append({
            "id": lead.id,
            "company": {
                "name": company.name,
                "industry": company.industry or "",
                "region": company.region or "",
                "employee_size": company.employee_size or "",
                "website": company.website or "",
                "public_email": getattr(company, "public_email", "") or "",
                "public_phone": getattr(company, "public_phone", "") or "",
            },
            "match_score": lead.score,
            "score": lead.score,
            "status": lead.status,
            "report_status": "ready" if lead.source_report_id else "",
            "source_report_id": lead.source_report_id or "",
            "reason": lead.reason or "",
            "recommended_at": recommended_at,
        })

    return {
        "total_matched": total,
        "returned": len(items),
        "summary": {
            "with_report": sum(1 for x in items if x["has_report"]),
            "with_website": sum(1 for x in items if x["website"]),
        },
        "leads": leads_payload,
        "companies": items,
        "actual_count": len(items),
        "requested_limit": max(1, min(int(limit or 20), 50)),
        "shortfall": 0,
        "mode": "history",
        "status": "ok",
        "filters": {"company_name": company_name, "industry": industry, "region": region, "status": status, "days": int(days or 0), "since": (since + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + " (北京时间)" if since else "", "until": (until + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + " (北京时间)" if until else ""},
    }


# ---------------------------------------------------------------- recommendation batches

# Legacy data without a campaign uses this number. **Do not use None as the key**: it passes through HTTP and a frontend radio button,
# and keeping a comparable scalar all the way is safer than writing a null check in three places.
LEGACY_BATCH_ID = 0


def resolve_batch_lead_ids(db: Session, user_id: int, campaign_ids: list[int]) -> list[int]:
    """Batch -> lead_ids. **This is the single authoritative interpretation of the word "this batch".**

    Before this it had no authoritative interpretation: the model cannot get lead_ids (the whitelist in `runtime/payload.py` does not allow them),
    and could only call `agent_recommended_companies`, which is flat by `lead.id desc`, default limit 20,
    with no batch marker; after two consecutive batches of 10, they came back mixed on one page and the model's pick was pure luck.
    So this function lives on the server side, not going through the model.

    `Undone` excluded: withdrawn recommendations should not be fished back by "send again".
    """
    from agents.sales_agent.models import SalesAgentLeadRecommendationORM

    ids = {int(x) for x in (campaign_ids or [])}
    if not ids:
        return []
    q = db.query(SalesAgentLeadRecommendationORM.id).filter(
        SalesAgentLeadRecommendationORM.user_id == int(user_id),
        SalesAgentLeadRecommendationORM.status != "Undone",
    )
    real = {x for x in ids if x != LEGACY_BATCH_ID}
    clauses = []
    if real:
        clauses.append(SalesAgentLeadRecommendationORM.campaign_id.in_(real))
    if LEGACY_BATCH_ID in ids:
        clauses.append(SalesAgentLeadRecommendationORM.campaign_id.is_(None))
    rows = q.filter(or_(*clauses)).order_by(SalesAgentLeadRecommendationORM.id.asc()).all()
    return [int(r[0]) for r in rows]


def session_recommendation_batches(db: Session, user_id: int, session_id: int | None, limit: int = 10) -> dict[str, Any]:
    """Which batches were recommended in the current session. Used by the "send to which batch?" dialog.

    **Numbered by created_at ascending**: when the user says "the first batch" they mean the earliest one, not the latest.
    The dialog preselects `default_campaign_id` (the latest batch), because "this batch" in Chinese by default
    refers to the batch just recommended.

    Only the current session: "this batch" is an in-session reference; listing batches from other sessions last week only lengthens the options.
    """
    from agents.sales_agent.models import SalesAgentLeadRecommendationORM, SalesAgentRecommendationCampaignORM

    n = max(1, min(int(limit or 10), 50))
    campaigns = (
        db.query(SalesAgentRecommendationCampaignORM)
        .filter(
            SalesAgentRecommendationCampaignORM.user_id == int(user_id),
            SalesAgentRecommendationCampaignORM.session_id == int(session_id),
            SalesAgentRecommendationCampaignORM.status != "undone",
        )
        .order_by(SalesAgentRecommendationCampaignORM.created_at.asc(), SalesAgentRecommendationCampaignORM.id.asc())
        .all()
    ) if session_id else []

    # Get all counts in one GROUP BY; do not loop per batch, 10 batches would be 10 round trips.
    counts: dict[int, int] = {}
    legacy_count = 0
    rows = (
        db.query(SalesAgentLeadRecommendationORM.campaign_id, func.count(SalesAgentLeadRecommendationORM.id))
        .filter(
            SalesAgentLeadRecommendationORM.user_id == int(user_id),
            SalesAgentLeadRecommendationORM.status != "Undone",
        )
        .group_by(SalesAgentLeadRecommendationORM.campaign_id)
        .all()
    )
    for campaign_id, count in rows:
        if campaign_id is None:
            legacy_count = int(count or 0)
        else:
            counts[int(campaign_id)] = int(count or 0)

    batches: list[dict[str, Any]] = []
    for campaign in campaigns:
        count = counts.get(int(campaign.id), 0)
        if not count:
            # Campaigns whose conditions are incomplete or that produced no companies are not options: selecting them could not send anything.
            continue
        batches.append({
            "campaign_id": int(campaign.id),
            "created_at_text": (campaign.created_at + timedelta(hours=8)).strftime("%m-%d %H:%M") if campaign.created_at else "",
            "lead_count": count,
        })
    for index, batch in enumerate(batches, start=1):
        batch["index"] = index
        batch["label"] = f"第 {index} 批"

    # Legacy data with empty campaign_id goes into its own bucket, placed first (it is necessarily older than this session's batches).
    if legacy_count and not batches:
        # Only offer this fallback bucket when the session has no batches: mixing the whole account's historical
        # recommendations into "send to which batch" when session batches exist means a misclick resends to companies long since followed up.
        batches.insert(0, {
            "campaign_id": LEGACY_BATCH_ID,
            "created_at_text": "",
            "lead_count": legacy_count,
            "index": 0,
            "label": "更早的推荐",
        })

    batches = batches[-n:]
    return {
        "batches": batches,
        "default_campaign_id": batches[-1]["campaign_id"] if batches else 0,
        "total_batches": len(batches),
    }
