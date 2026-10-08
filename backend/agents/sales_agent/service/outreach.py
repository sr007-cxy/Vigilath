"""Sales Agent service layer: outreach: email copy context, compliance hard gate, draft generation and confirmation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import hashlib
import html
import json
import re
from typing import Any
from sqlalchemy import or_
from sqlalchemy.orm import Session, object_session
from agents.sales_agent.constants import BLOCKED_CONTACT_CONSENT, OUTREACH_CHANNELS
from agents.sales_agent.discovery import normalize_domain
from agents.sales_agent.models import (
    SalesAgentAccountICPORM,
    SalesAgentBuyerPersonaORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentCustomerProfileORM,
    SalesAgentLeadActivityORM,
    SalesAgentLeadEvidenceORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)
from geo.models.full_diagnosis import FullDiagnosisReportORM

from agents.sales_agent.service.common import SalesAgentError, _dumps, _is_email, _iso, _loads, _profile_phrase, record_compliance_event
from agents.sales_agent.service.entities import _campaign_or_latest, _company_contact_summary, _lead_company, serialize_company, serialize_contact
from agents.sales_agent.service.reports import (
    _active_report_share_for_lead,
    _create_report_share,
    _full_report_by_id,
    _full_report_for_share,
    _full_report_summary,
    _latest_matching_full_report,
    _report_detail_points,
    _report_summary_matches_company,
    _sync_lead_report_snapshot,
    serialize_report_share,
)
from agents.sales_agent.service.profiles import (
    _buyer_personas_for_context,
    _latest_account_icp,
    _latest_seller_profile,
    _legacy_account_icp_payload,
    _legacy_buyer_persona_payloads,
    _legacy_seller_profile_payload,
    _profile_or_latest,
    serialize_account_icp,
    serialize_buyer_persona,
    serialize_lead_evidence,
    serialize_profile,
    serialize_recommendation_campaign,
    serialize_seller_profile,
)
from agents.sales_agent.service.brandprofile import brand_profile_payload, brand_report_capable
from agents.sales_agent.service.recommend import (
    _account_icp_by_legacy,
    _campaign_industries,
    _campaign_regions,
    _seller_profile_by_legacy,
    _service_scope_for_campaign,
    serialize_lead,
)
from agents.sales_agent.service.recommendation_profiles import recommendation_profile_for_campaign
from agents.sales_agent.service.email_send import (
    _latest_email_delivery_for_draft,
    send_sales_agent_email_delivery,
    serialize_sales_agent_email_delivery,
)
from agents.sales_agent.service.sms_send import (
    _latest_sms_delivery_for_draft,
    _resolve_sms_recipient,
    normalize_msisdn,
    send_sales_agent_sms_delivery,
    serialize_sales_agent_sms_delivery,
)

# Commercial SMS in China requires prior recipient consent (Provisions on the Administration of Short Message Services). Numbers scraped from websites
# are `public` / `unknown`, which means "no consent"; cold sending only earns complaints, and then the signature and channel get suspended together.
# Email has no such gate because the regulatory metric definition differs; do not copy this check over there.
SMS_CN_ALLOWED_CONSENT = {"consented", "business"}


def _usable_email_contact_for_lead(db: Session, lead: SalesAgentLeadRecommendationORM) -> SalesAgentCompanyContactORM | None:
    rows = (
        db.query(SalesAgentCompanyContactORM)
        .filter(SalesAgentCompanyContactORM.company_id == lead.company_id, SalesAgentCompanyContactORM.email != "")
        .order_by(SalesAgentCompanyContactORM.is_reachable.desc(), SalesAgentCompanyContactORM.id.desc())
        .limit(20)
        .all()
    )
    for row in rows:
        if row.consent_status in BLOCKED_CONTACT_CONSENT:
            continue
        # Explicit user requirement: a company contact can get generated/sent emails as long as it has a valid email; no longer blocked by the source field.
        if _is_email(row.email):
            return row
    return None


def _contact_for_lead(db: Session, lead: SalesAgentLeadRecommendationORM, contact_id: int | None, channel: str = "") -> SalesAgentCompanyContactORM | None:
    if contact_id is None:
        if channel == "email":
            return _usable_email_contact_for_lead(db, lead)
        if channel == "sms":
            # SMS needs a "mobile number", not just "a number": 400 numbers/landlines make up a sizable share of the DB,
            # and picking them means the draft is doomed never to send the moment it is generated.
            sms_contact, _, _, _ = _resolve_sms_recipient(db, lead)
            return sms_contact
        return None
    contact = db.query(SalesAgentCompanyContactORM).filter(SalesAgentCompanyContactORM.id == int(contact_id), SalesAgentCompanyContactORM.company_id == lead.company_id).first()
    if contact is None:
        raise SalesAgentError("联系方式不存在或不属于该线索企业。", status_code=404)
    return contact


def _lead_profile(db: Session, user_id: int, lead: SalesAgentLeadRecommendationORM) -> SalesAgentCustomerProfileORM | None:
    if lead.profile_id:
        profile = (
            db.query(SalesAgentCustomerProfileORM)
            .filter(SalesAgentCustomerProfileORM.id == lead.profile_id, SalesAgentCustomerProfileORM.user_id == user_id)
            .first()
        )
        if profile is not None:
            return profile
    if lead.campaign_id:
        campaign = _campaign_or_latest(db, user_id, lead.campaign_id)
        if campaign is not None and campaign.legacy_profile_id:
            profile = _profile_or_latest(db, user_id, campaign.legacy_profile_id)
            if profile is not None:
                return profile
    return _profile_or_latest(db, user_id)


def _lead_evidences(db: Session, user_id: int, lead: SalesAgentLeadRecommendationORM) -> list[SalesAgentLeadEvidenceORM]:
    conditions = [SalesAgentLeadEvidenceORM.lead_recommendation_id == lead.id]
    if lead.campaign_id:
        conditions.append((SalesAgentLeadEvidenceORM.campaign_id == lead.campaign_id) & (SalesAgentLeadEvidenceORM.company_id == lead.company_id))
    rows = (
        db.query(SalesAgentLeadEvidenceORM)
        .filter(SalesAgentLeadEvidenceORM.user_id == user_id, or_(*conditions))
        .order_by(SalesAgentLeadEvidenceORM.created_at.asc(), SalesAgentLeadEvidenceORM.id.asc())
        .limit(30)
        .all()
    )
    seen: set[int] = set()
    out: list[SalesAgentLeadEvidenceORM] = []
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        out.append(row)
    return out


def _lead_structured_context(
    db: Session,
    user_id: int,
    lead: SalesAgentLeadRecommendationORM,
) -> tuple[
    SalesAgentCustomerProfileORM | None,
    SalesAgentSellerProfileORM | None,
    SalesAgentAccountICPORM | None,
    SalesAgentRecommendationCampaignORM | None,
    list[SalesAgentBuyerPersonaORM],
    list[SalesAgentLeadEvidenceORM],
]:
    profile = _lead_profile(db, user_id, lead)
    campaign = _campaign_or_latest(db, user_id, lead.campaign_id) if lead.campaign_id else None
    seller = _latest_seller_profile(db, user_id, campaign.seller_profile_id) if campaign and campaign.seller_profile_id else None
    if seller is None:
        seller = _seller_profile_by_legacy(db, user_id, profile.id if profile else None) or _latest_seller_profile(db, user_id)
    account_icp = _latest_account_icp(db, user_id, campaign.account_icp_id, seller.id if seller else None) if campaign and campaign.account_icp_id else None
    if account_icp is None:
        account_icp = _account_icp_by_legacy(db, user_id, profile.id if profile else None, seller.id if seller else None) or _latest_account_icp(db, user_id, seller_profile_id=seller.id if seller else None)
    personas = _buyer_personas_for_context(
        db,
        user_id,
        seller_profile_id=seller.id if seller else None,
        account_icp_id=account_icp.id if account_icp else None,
    )
    evidences = _lead_evidences(db, user_id, lead)
    return profile, seller, account_icp, campaign, personas, evidences


def _outreach_profile_context(
    profile: SalesAgentCustomerProfileORM | None,
    company: SalesAgentCompanyORM,
    seller_profile: SalesAgentSellerProfileORM | None = None,
    campaign: SalesAgentRecommendationCampaignORM | None = None,
    buyer_personas: list[SalesAgentBuyerPersonaORM] | None = None,
    account_icp: SalesAgentAccountICPORM | None = None,
) -> dict[str, Any]:
    scopes = _service_scope_for_campaign(campaign, seller_profile)
    if not scopes and profile is not None:
        scopes = _loads(profile.service_scope_json, [])
    industries = _campaign_industries(campaign)
    if not industries and account_icp is not None:
        industries = _loads(account_icp.target_industries_json, [])
    if not industries and profile is not None:
        industries = _loads(profile.industries_json, [])
    regions = _campaign_regions(campaign)
    if not regions and account_icp is not None:
        regions = _loads(account_icp.target_regions_json, [])
    if not regions and profile is not None:
        regions = _loads(profile.regions_json, [])
    personas = buyer_personas or []
    persona_roles = [p.role_name for p in personas if p.role_name]
    if not persona_roles and seller_profile is not None:
        persona_roles = _loads(seller_profile.default_buyer_roles_json, [])
    if not persona_roles and profile is not None:
        persona_roles = _loads(profile.buyer_roles_json, [])
    messaging_angles: list[str] = []
    for persona in personas:
        messaging_angles.extend(_loads(persona.messaging_angles_json, []))

    # The fallback string used to be hard-coded as "website AI visibility / GEO diagnostics", which is what Vigilath itself sells.
    # Customers selling something else would get a business that is not theirs as soon as their profile was empty. Better to leave it empty: downstream sentence
    # building skips empty values, and `brand_profile` is the real source material for writing.
    service = _profile_phrase(scopes, "", limit=2)
    industry = _profile_phrase(industries, company.industry or "", limit=2)
    region = _profile_phrase(regions, company.region or "", limit=2, skip_national=True)
    buyer = _profile_phrase(persona_roles, "市场/品牌/增长负责人", limit=2)

    if industry and region:
        segment = f"{region}的{industry}企业"
    elif industry:
        segment = f"{industry}企业"
    elif region:
        segment = f"{region}企业"
    else:
        segment = ""
    return {
        "service": service,
        "industry": industry,
        "region": region,
        "buyer": buyer,
        "segment": segment,
        "seller_summary": seller_profile.summary if seller_profile else "",
        # The fallback angle also drops "real audit report": brands that do not do audits should not be nudged in that direction
        "messaging_angle": _profile_phrase(messaging_angles, "结合对方公开信息谈具体业务价值", limit=2),
        "campaign_id": str(campaign.id) if campaign else "",
        "context_rule": "service_scope 只表示当前用户卖什么;industry/region 来自本次 RecommendationCampaign 或 ICP,不能混为长期用户画像。",
    }


def _strip_platform_sender(text: str) -> str:
    out = str(text or "").strip()
    for phrase in (
        "你好,这里是 Vigilath。",
        "你好，这里是 Vigilath。",
        "这里是 Vigilath。",
        "这里是 Vigilath,",
        "这里是 Vigilath，",
        "我们是 Vigilath,",
        "我们是 Vigilath，",
        "我们是 Vigilath。",
    ):
        out = out.replace(phrase, "你好,")
    return out.strip()


_EMAIL_FOOTER_PATTERNS = (
    re.compile(r"\n*\s*[（(]\s*注[：:].*?(?:AI|Ai|ai)\s*生成.*?[）)]\s*$", re.DOTALL),
    re.compile(r"\n+\s*如不(?:希望|方便)接收.*?(?:退订|停止联系).*?$", re.DOTALL),
)


def _strip_email_draft_footer(content: str) -> str:
    text = str(content or "").strip()
    previous = None
    while text and text != previous:
        previous = text
        for pattern in _EMAIL_FOOTER_PATTERNS:
            text = pattern.sub("", text).strip()
    return text


def _ensure_unsubscribe_line(content: str, channel: str, content_mode: str = "") -> str:
    text = _strip_platform_sender(content)
    if channel == "email":
        return _strip_email_draft_footer(text)
    if channel == "sms" and content_mode == "template":
        # For template channels the unsubscribe text is in the registered template body, and the platform sends the registered content verbatim.
        # Appending another sentence here would only make the draft preview differ from the real SMS.
        return text
    if any(word in text for word in ("退订", "不再联系", "停止联系")):
        return text
    if channel == "phone_script":
        return text.rstrip("。") + "。如对方表示不需要,记录为不再联系。"
    if channel == "sms":
        # SMS is billed per 70 characters, so shorter unsubscribe text is better; TD is the carrier-wide unsubscribe keyword.
        return text.rstrip("。") + "。回复TD退订"
    return text.rstrip("。") + "。如不方便接收,请回复退订。"


def _fallback_outreach_subject(company: SalesAgentCompanyORM, profile_ctx: dict[str, str]) -> str:
    # If the profile lists no service, do not force one in: this used to fall back to "website AI visibility / GEO diagnostics",
    # and customers selling something else got a business they do not do popping up in the subject line.
    service = str(profile_ctx.get("service") or "").strip()
    return f"关于 {company.name} {service} 的沟通" if service else f"关于 {company.name} 业务合作的沟通"


def _outreach_guard(
    db: Session,
    user_id: int,
    lead: SalesAgentLeadRecommendationORM,
    contact: SalesAgentCompanyContactORM | None,
    channel: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    if lead.status in {"Unsubscribed", "Blacklisted"}:
        reasons.append(f"线索状态为 {lead.status}")
    if channel not in OUTREACH_CHANNELS:
        reasons.append("触达渠道不受支持")
    if contact is not None:
        if contact.consent_status in BLOCKED_CONTACT_CONSENT:
            reasons.append(f"联系人 consent_status={contact.consent_status}")
        if channel == "email" and not contact.email:
            warnings.append("email 渠道缺少邮箱,待审批前补充收件人")
        if channel == "sms" and not contact.phone:
            warnings.append("sms 渠道缺少电话,待审批前补充号码")
        elif channel == "sms" and contact.phone:
            phone, country, is_mobile = normalize_msisdn(contact.phone)
            if not phone or not is_mobile:
                warnings.append(f"该联系人的号码 {contact.phone} 不是手机号(400/800/座机收不了短信),待审批前补充手机号")
            elif country == "CN" and contact.consent_status not in SMS_CN_ALLOWED_CONSENT:
                # Block at draft generation rather than waiting for send: writing an email that cannot be sent is waste,
                # and the cost of cold SMS in China is a suspended signature, not "this one failed".
                reasons.append(
                    f"国内手机号触达需要接收方同意,当前 consent_status={contact.consent_status or 'unknown'};"
                    "请先取得授权(把联系人标记为 consented/business)或改用邮件渠道"
                )
    if reasons:
        record_compliance_event(db, user_id, "generate_outreach_draft", "high", "blocked", "；".join(reasons), {"lead_id": lead.id, "contact_id": contact.id if contact else None, "channel": channel})
        return "blocked", reasons
    if contact is None:
        return "needs_approval", ["未绑定具体合规联系方式,草稿只能作为内部话术准备"]
    return "needs_approval", warnings or ["触达前必须人工审批"]


def _has_unsubscribe_instruction(text: str) -> bool:
    return any(word in str(text or "") for word in ("退订", "不再联系", "停止联系"))


REPORT_EMAIL_PROMPT_VERSION = "report-email-v27"




def outreach_brand_settings(context: dict[str, Any]) -> dict[str, Any]:
    """Get "which brand this email is sent as" from the outreach context.

    Pure function, no DB query: the brand columns are already in `context["seller_profile"]`,
    and seller comes from `campaign.seller_profile_id`, so what we get here is always
    **the brand used when this lead was recommended**, not "the brand the user has selected now". This matters:
    a lead recommended under brand A six months ago should still be written as A when following up today.

    Since 2026-08-27 only id + name remain: brand-level email settings (style/tone/extra requirements/signature)
    were removed together with `/sales-agent/admin`, and those four columns have no write path. Email wording now comes entirely from
    the brand profile (`email_material`) plus the perspective chosen when sending.
    """
    payload = context.get("seller_profile") if isinstance(context, dict) else None
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": int(payload.get("id") or 0),
        "name": str(payload.get("name") or "").strip(),
    }

REPORT_EMAIL_STYLE_OPTIONS: dict[str, dict[str, str]] = {
    "leijun_pitch": {
        "label": "综合负责人",
        "description": "默认,发给公司相关负责人。",
    },
    "executive_view": {
        "label": "管理层",
        "description": "偏商业判断和新入口影响。",
    },
    "marketing_brand_view": {
        "label": "市场品牌",
        "description": "偏品牌呈现和可见度。",
    },
    "sales_growth_view": {
        "label": "销售增长",
        "description": "偏新客户发现和线索入口。",
    },
    "digital_ops_view": {
        "label": "数字化官网",
        "description": "偏官网信号和可优化项。",
    },
}


def normalize_report_email_style_key(style_key: str) -> str:
    key = (style_key or "").strip().lower().replace("-", "_")
    return key if key in REPORT_EMAIL_STYLE_OPTIONS else "leijun_pitch"


def report_email_style_options() -> list[dict[str, str]]:
    return [{"key": key, **value} for key, value in REPORT_EMAIL_STYLE_OPTIONS.items()]


# v2: the prompt's output format changed from JSON to "first line SUBJECT: + blank line + body". JSON cannot be streamed while writing:
# the body is escaped, and splitting a half-received payload on `\n\n` would cut at escape characters. Bumping the version invalidates cached drafts for all four
# email types (including the zero-token "standard intro"), so they are regenerated on next open.
NO_REPORT_EMAIL_PROMPT_VERSION = "no-report-intro-v2"

# "Email types" for intro emails without a report. Split by **angle of approach**, not by writing style: each type changes the opening angle and main thread,
# not short/long versions of the same email.
#
# We do not use the 5 "recipient perspectives" of report emails because the metric definition differs: when a no-report email goes out we usually do not know
# who the recipient is (the lead only has a company, and `_outreach_guard` only validates company-level contacts), so splitting by role would mean
# making the user guess. The role dimension is left to the report path, where a contact person already exists.
#
# `requirements` are numbered requirements fed to the model, with a uniform structure: 1-2 set the opening, 3-4 the main thread, 5 the closing question,
# 6 the length. Adding a type = writing another section in this structure; the prompt skeleton needs no change.
NO_REPORT_EMAIL_STYLES: dict[str, dict[str, str]] = {
    "standard": {
        "label": "标准介绍",
        "description": "按品牌资料拼,措辞固定,不过模型。",
        "mode": "template",
        "requirements": "",
    },
    "scenario_fit": {
        "label": "场景切入",
        "description": "先说对方可能正遇到的处境,再说能力。",
        "mode": "llm",
        "requirements": """1. 前两段完全不介绍自己,先描述对方这类企业在当前阶段可能正遇到的具体处境。
2. 场景素材从草稿里挑与对方行业最贴合的 1-2 项,用你自己的话转述成对方的处境,不要照抄草稿原句,不要顿号罗列。
3. 第三段才带出品牌名,只接 1-2 条最能回应上面场景的能力,和前面的处境一一对应。
4. 全篇不出现“我们的优势是”“我们提供以下服务”这类自述式表达。
5. 结尾一个提问:确认这个场景在贵司是否成立。
6. 正文 500-600 个中文字,分 4-5 段,每段不超过 3 句。宁可少说一个场景、把一个说透,不要靠加形容词凑字数。""",
    },
    "industry_view": {
        "label": "行业观察",
        "description": "从对方行业正在发生的变化聊到自己。",
        "mode": "llm",
        "requirements": """1. 开头从对方所在行业当前正在发生的公开变化切入(获客方式、客户决策路径、信息渠道的迁移),先给判断,再往下走。
2. 这个判断只能停在常识层面能站住的趋势上。不得出现任何具体数据、百分比、调研报告、机构名称、时间点断言——你没有数据来源,编一个就废了整封信。
3. 第二段把趋势收到对方这类企业身上:这个变化对他们的业务意味着什么。
4. 第三段才带出品牌名,说明我们正好在这个变化里做哪一段,能力取自草稿。
5. 结尾一个提问:想听听贵司这边怎么看这个变化。
6. 正文 500-600 个中文字,分 4-5 段。只谈行业,不得评价对方公司本身做得好不好。""",
    },
    "need_check": {
        "label": "需求确认",
        "description": "不铺垫,直接问有没有这类需求。",
        "mode": "llm",
        "requirements": """1. 开头一句说清我是谁、做什么,第二句直接说明来意:想确认贵司有没有这类需求需要处理。
2. 不写行业背景、不写趋势判断、不写恭维话,全篇不出现“冒昧打扰”“看到贵司官网”。
3. 中间用 2-3 个“如果……”式的判断句帮对方自查,每句对应草稿里的一条真实能力。
4. 这些句子只能是开放问法,不得写成“贵司一定存在 X 问题”“贵司目前缺少 Y”这类断言——我们没有任何依据判断对方现状。
5. 结尾一个提问,并明确给出说不的出口:这几条里有一条成立就回信细聊,都不成立也请直接告诉我,不再打扰。
6. 正文 350-450 个中文字,段落短,两分钟能读完。""",
    },
}


def normalize_no_report_email_style_key(style_key: str) -> str:
    key = (style_key or "").strip().lower().replace("-", "_")
    return key if key in NO_REPORT_EMAIL_STYLES else "standard"


def no_report_email_style_options() -> list[dict[str, str]]:
    return [
        {"key": key, "label": value["label"], "description": value["description"], "mode": value["mode"]}
        for key, value in NO_REPORT_EMAIL_STYLES.items()
    ]


def no_report_email_style_is_llm(style_key: str) -> bool:
    return NO_REPORT_EMAIL_STYLES[normalize_no_report_email_style_key(style_key)]["mode"] == "llm"


def report_email_fingerprint(
    lead_id: int,
    channel: str,
    style_key: str,
    report_context: dict[str, Any],
    public_report_url: str = "",
    brand: dict[str, Any] | None = None,
    tone: str = "",
    no_report: bool = False,
) -> str:
    """Draft cache key. **The brand must be included.**

    When business lines A and B email the same lead, the lead, report and perspective are identical, but their brand profiles
    differ, so the email material (`email_material`) is entirely different. Without the brand, B would hit
    the draft A already generated and send someone else's self-introduction.

    With `no_report=True` keys are normalized with the intro-email set. **This switch cannot be omitted**: the two key spaces differ,
    and normalizing with the report set would make `scenario_fit` / `industry_view` / `need_check` all fall back to
    `leijun_pitch`, so three email types would share one cache key and switching type would hit the previous type's draft.
    """
    brand_payload = brand if isinstance(brand, dict) else {}
    payload = {
        "version": NO_REPORT_EMAIL_PROMPT_VERSION if no_report else REPORT_EMAIL_PROMPT_VERSION,
        "lead_id": int(lead_id),
        "channel": channel or "email",
        "style_key": normalize_no_report_email_style_key(style_key) if no_report else normalize_report_email_style_key(style_key),
        "tone": (tone or "").strip(),
        "public_report_url": public_report_url or "",
        "brand_id": int(brand_payload.get("id") or 0),
        "report_context": report_context or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def report_email_metadata(style_key: str, variant_index: int, report_fingerprint: str, cache_key: str, cached: bool = False) -> dict[str, Any]:
    key = normalize_report_email_style_key(style_key)
    style = REPORT_EMAIL_STYLE_OPTIONS[key]
    return {
        "kind": "report_email",
        "style_key": key,
        "style_label": style["label"],
        "variant_index": int(variant_index),
        "report_fingerprint": report_fingerprint,
        "cache_key": cache_key,
        "prompt_version": REPORT_EMAIL_PROMPT_VERSION,
        "cached": bool(cached),
    }


# Only when these words appear in the profile do we conclude "the sender is Vigilath itself". Intro email bodies are always generated from each brand's own
# profile, and Vigilath no longer injects any hard-coded wording or support contacts; the only use here is the compliance
# gate below (`_outreach_copy_policy_violations`): allow Vigilath's own signature, block other customers
# impersonating it. Prefer false negatives: a miss at most blocks one of Vigilath's own emails, a false hit lets impersonation through.
_VIGILATH_MARKERS = ("vigilath", "威吉力")


class NoBrandProfileError(SalesAgentError):
    """The profile is too empty to write an intro email. The caller catches it and prompts to fill in the profile, rather than sending an empty email."""


def _brand_is_vigilath(material: dict[str, Any]) -> bool:
    blob = " ".join([
        str(material.get("brand_name") or ""),
        str(material.get("company_full_name") or ""),
        str((material.get("contacts") or {}).get("website") or ""),
    ]).casefold()
    return any(marker in blob for marker in _VIGILATH_MARKERS)


# Truncation residue: only one or two characters left after punctuation ("...precise outreach capability, and"). Profile options were once hard-truncated by length,
# and such half-sentences still exist in stored profiles; putting them straight into the body makes the whole email obviously machine-assembled.
# Only short tails after commas/semicolons/colons count: the enumeration comma 「、」 is normal listing, and stripping it would eat the user's real last item.
_EMAIL_BULLET_DANGLING_TAIL_RE: re.Pattern[str] = re.compile(r"[，,；;：:]\s*[^，,、；;：:。]{1,2}$")
# If X in 「主要提供 X 等服务」 ("mainly provides X and other services") carries its own 「服务能力」 ("service capability") tail, it reads as "provides ... service capability and other services".
_EMAIL_BULLET_TOPIC_SUFFIXES: tuple[str, ...] = ("服务能力", "的能力", "等服务", "能力", "服务")


def _polish_email_bullet(value: Any) -> str:
    """Strip residue from bullet points so every one that goes into the body is a complete phrase."""
    text = str(value or "").strip().strip("，,。；;、：: ")
    match = _EMAIL_BULLET_DANGLING_TAIL_RE.search(text)
    # Something informative must remain after stripping; otherwise keep the original.
    if match and match.start() >= 4:
        text = text[: match.start()].strip("，,。；;、：: ")
    return text


def _bullet_as_topic_phrase(value: Any) -> str:
    """Compress a bullet into a phrase that fits 「主要提供 X 等服务」 ("mainly provides X and other services"), removing its own 「服务/能力」 ("service/capability") tail."""
    text = _polish_email_bullet(value)
    for suffix in _EMAIL_BULLET_TOPIC_SUFFIXES:
        if len(text) > len(suffix) + 2 and text.endswith(suffix):
            return text[: -len(suffix)].strip("，,。；;、：: ")
    return text


def _no_report_bullets(material: dict[str, Any]) -> list[str]:
    """Three bullet points for the intro email body. Taken as "what we do -> how we do it -> why trust us"; use as many as are available."""
    bullets: list[str] = []
    bullets.extend(material.get("service_features") or [])
    if not bullets:
        bullets.extend(material.get("brand_diff_tags") or [])
    bullets.extend(material.get("service_process") or [])
    bullets.extend(material.get("service_guarantees") or [])
    bullets.extend(material.get("core_credentials") or [])
    bullets.extend(material.get("case_stories") or [])
    out: list[str] = []
    seen: set[str] = set()
    for item in bullets:
        text = _polish_email_bullet(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out[:5]


def _contact_lines(material: dict[str, Any], contact_email: str = "") -> list[tuple[str, str]]:
    """Signature contact info. **Whitelist**: anything not filled in the profile never appears, and must not fall back to platform support."""
    contacts = material.get("contacts") if isinstance(material.get("contacts"), dict) else {}
    override = str(contact_email or "").strip()
    pairs = [
        ("联系邮箱", override or str(contacts.get("email") or "")),
        ("联系电话", str(contacts.get("phone") or "")),
        ("微信", str(contacts.get("wechat") or "")),
        ("官网", str(contacts.get("website") or "")),
    ]
    return [(label, value.strip()) for label, value in pairs if value.strip()]


def _ensure_contact_block(content: str, material: dict[str, Any], contact_email: str = "") -> str:
    """If the model left out contact info, fill it in from the profile; same approach as the `public_url` fallback.

    The prompt rule (`copy_rules`) is a **whitelist**: "only use those listed in contacts", not
    "must write". The model omitting the signature entirely is not a violation, so the recipient gets no way to reply and the email is wasted.
    Filling it in deterministically is more reliable than adding another line to the prompt.

    Dedup by **value**, not label: the model may write "Email: x@y" instead of "Contact email: x@y",
    and checking by label would append duplicates. Nothing filled in the profile -> add nothing; that is the other half of the whitelist.
    """
    lines = _contact_lines(material, contact_email)
    missing = [(label, value) for label, value in lines if value not in (content or "")]
    if not missing:
        return content
    return (content or "").rstrip() + "\n\n" + "\n".join(f"{label}：{value}" for label, value in missing)


def _ensure_contact_block_html(html_content: str, material: dict[str, Any], contact_email: str = "") -> str:
    """HTML version of `_ensure_contact_block`. The two are two renderings of the same text and must be changed together."""
    lines = _contact_lines(material, contact_email)
    missing = [(label, value) for label, value in lines if value not in (html_content or "")]
    if not missing:
        return html_content
    esc = html.escape
    block = "<br>".join(f"<strong>{esc(label)}：</strong>{esc(value)}" for label, value in missing)
    return (html_content or "").rstrip() + f"<p>{block}</p>"


_PITCH_SELF_SUBJECTS = ("我们", "我方", "我司", "本公司", "本品牌")
# A self-reference only "ends" when followed by these, which prevents cutting a brand name mid-word:
# a brand named 「官网通」 with a sentence 「官网通用方案…」 would, without a boundary, be cut into 「我这边是官网通,用方案…」 (a mis-split of "Guanwangtong").
_PITCH_LEAD_BOUNDARY = tuple(" \t　，,。.：:；;、-—·|/")


def _normalize_brand_token(text: str) -> str:
    """For brand name comparison: erase differences in case, hyphens, spaces and dots.

    The "core one-liner" is filled in by the customer, and when it repeats the brand name the spelling often does not match the brand name field:
    the production GEO-TAG profile wrote "GEO Tag" in its one-liner. A literal comparison would miss it.
    """
    return re.sub(r"[\s　\-_·・.．]+", "", str(text or "")).lower()


def _self_reference_lead_len(text: str, sender: str) -> int:
    """If the text starts with a self-reference (the brand name or a subject like 「我们」 ("we")), return its character count, else 0.

    Compares the normalized result while accumulating character by character, because the two spellings of the brand name may differ even in hyphen count
    and cannot be cut by length directly. A match must also pass a boundary check; see `_PITCH_LEAD_BOUNDARY`.
    """
    for candidate in (sender, *_PITCH_SELF_SUBJECTS):
        target = _normalize_brand_token(candidate)
        if not target:
            continue
        for i in range(1, len(text) + 1):
            acc = _normalize_brand_token(text[:i])
            if len(acc) > len(target):
                break
            if acc != target:
                continue
            rest = text[i:]
            if not rest or rest[0] in _PITCH_LEAD_BOUNDARY or rest.startswith(("是", "为")):
                return i
            break
    return 0


def _fit_pitch_to_intro(pitch: str, sender: str) -> tuple[str, str]:
    """Attach the "core one-liner" after 「我这边是<品牌>」 ("this is <brand>"), returning (connector, body).

    This sentence is customer-written and comes in many shapes:

    - appositive              「AI搜索可见性全链路解决方案」        -> join with a comma, read as 「我这边是X,<appositive>。」
    - contains the brand name 「GEO Tag 是GEO领域首个中立计量工具」 -> cut 「GEO Tag 是」 ("GEO Tag is"), use the rest as an appositive
    - full sentence, new subject 「我们帮品牌做全链路计量」            -> if it cannot be cut, start a new sentence instead of gluing it into a broken one

    On 2026-08-27 production hit the second case: a hard comma join produced 「我这边是GEO-TAG，GEO Tag 是GEO领域首个
    中立计量工具。」 (brand named twice, spelled differently each time, and the second half comma-spliced onto the first).
    """
    text = str(pitch or "").strip().rstrip("。.")
    if not text:
        return "，", ""
    lead = _self_reference_lead_len(text, sender)
    if lead:
        rest = text[lead:].lstrip()
        for token in ("是", "为", "：", ":", "，", ",", "-", "—"):
            if rest.startswith(token):
                rest = rest[len(token):].lstrip()
                break
        # The whole sentence is just a self-reference (the one-liner only contains the brand name), with no information left; drop it.
        return "，", rest
    if text.startswith(_PITCH_SELF_SUBJECTS):
        return "。", text
    return "，", text


_PLACEHOLDER_BRAND_NAMES = {"默认品牌", "未命名品牌", "我的品牌"}


def _usable_brand_sender(sender: str) -> str:
    """Emails may only use the real brand name; system placeholder names must not be used as the sender."""
    text = str(sender or "").strip()
    return "" if text in _PLACEHOLDER_BRAND_NAMES else text


def _short_join(items: list[str], limit: int = 3) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    return "、".join(values[:limit])


def _short_email_subject_pitch(pitch_body: str, material: dict[str, Any]) -> str:
    """Short positioning for the email subject: the core one-liner can be long; the subject only takes the part readable at a glance.

    The body carries the full value proposition; the subject only needs to be clear and short in list cards. The production GEO-TAG line
    「GEO领域首个中立计量工具,实现了全链路数据的统一口径:从 AI 提及、URL 引用…」 ("the first neutral measurement tool in GEO, unifying full-funnel metrics: from AI mentions, URL citations...")
    took up three or four lines after being stuffed into the subject whole. The target subject the user gave was:

        GEO-TAG - GEO领域首个中立计量工具，有效记录ROI ("the first neutral measurement tool in GEO, effectively tracking ROI")

    So keep the short positioning before the comma; when the profile mentions signals like ROI/conversion value/reconciliation, add a short benefit.
    """
    text = str(pitch_body or "").strip().rstrip("。.")
    if not text:
        return "服务介绍"
    first = re.split(r"[，,。；;\n]", text, maxsplit=1)[0].strip()
    if "：" in first or ":" in first:
        left, right = re.split(r"[：:]", first, maxsplit=1)
        # In patterns like 「十年只做一件事:xxx」 ("ten years doing one thing: xxx") the part before the colon is a lead-in; take the substance after it;
        # patterns like 「统一口径:从 A 到 B」 ("unified metrics: from A to B") are already cut after the comma and never reach here.
        if len(left.strip()) <= 12 and right.strip():
            first = right.strip()
    # Truncate an extremely long first segment once more as a fallback, so English/unpunctuated long sentences do not blow up the subject.
    if len(first) > 28:
        first = first[:28].rstrip() + "…"

    blob = json.dumps(material, ensure_ascii=False).casefold() if isinstance(material, dict) else ""
    if "roi" in blob or "转化金额" in blob or "可对账" in blob or "对账" in blob:
        if "roi" not in first.casefold():
            first = f"{first}，有效记录ROI"
    return first or "服务介绍"


def _no_report_email_inputs(context: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields the intro email needs from the context. Shared by the middle section, subject and assembly entry points."""
    company = context.get("company") if isinstance(context.get("company"), dict) else {}
    company_name = str(company.get("name") or "贵司").strip() or "贵司"
    brand = context.get("brand_profile") if isinstance(context.get("brand_profile"), dict) else {}
    material = brand.get("email_material") if isinstance(brand.get("email_material"), dict) else {}

    sender_raw = str(material.get("brand_name") or "").strip()
    sender = _usable_brand_sender(sender_raw)
    pitch = str(material.get("core_message") or material.get("brand_slogan") or "").strip()
    # If the one-liner only contains the brand name, `_fit_pitch_to_intro` cuts it entirely, so it provided
    # no information and cannot count as evidence that "the profile is filled". Placeholder brand names are only used to strip
    # 「默认品牌是...」 ("the default brand is...") prefixes from the profile and must never actually appear in emails.
    sep, pitch_body = _fit_pitch_to_intro(pitch, sender or sender_raw)
    return {
        "company_name": company_name,
        "material": material,
        "sender": sender,
        "pitch": pitch,
        "sep": sep,
        "pitch_body": pitch_body,
        "overview": str(material.get("core_service_overview") or "").strip(),
        "bullets": _no_report_bullets(material),
        "scenarios": [str(x).strip() for x in (material.get("target_scenarios") or []) if str(x).strip()],
    }


def assert_brand_material_usable(context: dict[str, Any]) -> None:
    """Exit early when the profile is too empty to write an intro email, raising `NoBrandProfileError` so the caller prompts to fill in the profile.

    Both the assembly and model paths must pass this check first. **The model path needs it most**: with an empty profile it can still write
    an empty "hello, looking forward to working together, best regards" email; sending it is worse than not sending, and neither recipient nor sender can tell
    the root cause is an unfilled profile.
    """
    parts = _no_report_email_inputs(context)
    if not (parts["pitch_body"] or parts["overview"] or parts["bullets"] or parts["scenarios"]):
        raise NoBrandProfileError(
            "这个品牌还没有可用的品牌资料,写不出介绍信。"
            "请先在客户工作台补齐「核心一句话」「核心业务线」「服务核心特点」再生成邮件。"
        )


def build_no_report_default_body_paragraphs(context: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The **middle section** of the intro email, excluding greeting, contact info and signature, which `assemble_intro_email` assembles.

    Returns (plain-text paragraphs, HTML paragraphs). The two are renderings of the same text; copy changes must be made in both;
    the HTML version is maintained separately because bullets are `- x` in the body but `<ul><li>` in rich text.

    The model rewrite path takes exactly this plain text: facts like brand positioning, service capabilities and target scenarios are already
    in the middle section, and the model only reorganizes and polishes with no room to fabricate; safer than letting it pick material from the profile itself.
    """
    assert_brand_material_usable(context)
    parts = _no_report_email_inputs(context)
    company_name = parts["company_name"]
    material = parts["material"]
    sender = parts["sender"]
    pitch_body = parts["pitch_body"]
    overview = parts["overview"]
    bullets = parts["bullets"]
    scenarios = parts["scenarios"]

    if sender:
        intro = f"我这边是{sender}"
        if pitch_body:
            intro += f"{parts['sep']}{pitch_body}"
        intro += "。"
    elif pitch_body:
        intro = f"看到{company_name}的业务方向后,我们这边主要围绕{pitch_body}提供服务。"
    elif bullets:
        # Bullets are mostly written as "...service capability"; plugging them into "provides X and other services" reads as "provides ... service capability and other services".
        intro = f"看到{company_name}的业务方向后,我们这边主要做{_short_join([_bullet_as_topic_phrase(b) for b in bullets], 2)}这类事。"
    else:
        intro = f"看到{company_name}的业务方向后,我们这边希望围绕相关服务场景做一次初步沟通。"
    if overview and overview not in (parts["pitch"], pitch_body):
        intro += overview if overview.endswith(("。", ".")) else f"{overview}。"

    paragraphs = [intro]
    paragraphs.append(
        "这次联系不是想直接打扰贵司推进一个固定方案,而是希望先确认是否存在可以合作的具体场景。"
        "如果方向合适,我们可以基于贵司当前业务目标、团队投入和已有基础,把需求拆成更容易判断优先级的几项。"
    )
    if bullets:
        paragraphs.append("结合贵司这类业务场景,我们可以优先从以下方向协助：\n" + "\n".join(f"- {item}" for item in bullets))
    if scenarios:
        paragraphs.append(
            "从我们更适合服务的客户类型看," + "、".join(scenarios[:4])
            + "这类情况通常更容易先找到切入点。"
            "如果贵司当前也在类似阶段,可以先从一个最明确的问题开始沟通,避免一开始就把范围拉得过大。"
        )
    paragraphs.append(
        "首次沟通可以先围绕三件事展开:一是贵司当前最想解决的业务问题,二是已有团队或外部资源的配合方式,"
        "三是希望看到的阶段性交付。这样后续如果继续推进,方案会更贴近贵司真实情况,也方便判断投入产出是否值得。"
    )
    paragraphs.append("如果贵司这边有相关需求,欢迎直接回复本邮件。我可以先整理一版简要合作思路,包括重点场景、推进步骤、双方需要准备的信息和下一步建议。")

    esc = html.escape
    html_parts = [f"<p>{esc(intro)}</p>"]
    html_parts.append(
        "<p>这次联系不是想直接打扰贵司推进一个固定方案,而是希望先确认是否存在可以合作的具体场景。"
        "如果方向合适,我们可以基于贵司当前业务目标、团队投入和已有基础,把需求拆成更容易判断优先级的几项。</p>"
    )
    if bullets:
        html_parts.append(
            "<p>结合贵司这类业务场景,我们可以优先从以下方向协助：</p><ul>"
            + "".join(f"<li>{esc(item)}</li>" for item in bullets)
            + "</ul>"
        )
    if scenarios:
        html_parts.append(
            "<p>从我们更适合服务的客户类型看,"
            + esc("、".join(scenarios[:4]))
            + "这类情况通常更容易先找到切入点。"
            "如果贵司当前也在类似阶段,可以先从一个最明确的问题开始沟通,避免一开始就把范围拉得过大。</p>"
        )
    html_parts.append("<p>首次沟通可以先围绕三件事展开:一是贵司当前最想解决的业务问题,二是已有团队或外部资源的配合方式,三是希望看到的阶段性交付。这样后续如果继续推进,方案会更贴近贵司真实情况,也方便判断投入产出是否值得。</p>")
    html_parts.append("<p>如果贵司这边有相关需求,欢迎直接回复本邮件。我可以先整理一版简要合作思路,包括重点场景、推进步骤、双方需要准备的信息和下一步建议。</p>")
    return paragraphs, html_parts


def default_intro_subject(context: dict[str, Any]) -> str:
    """Template subject for the intro email: real brand name + short positioning.

    `标准介绍` ("standard intro") uses it directly; model-type subjects that are too long or hit advertising words also fall back to it
    (see `sanitize_intro_subject`). System placeholder names like `默认品牌` ("default brand") must not enter the subject,
    or the first thing the recipient sees is a template email.
    """
    parts = _no_report_email_inputs(context)
    sender = parts["sender"]
    bullets = parts["bullets"]
    subject_pitch = _short_email_subject_pitch(parts["pitch_body"], parts["material"])
    if sender:
        subject = f"{sender} - {subject_pitch}"
    elif subject_pitch != "服务介绍":
        subject = f"关于{subject_pitch}的服务介绍"
    elif bullets:
        subject = f"关于{_short_join([_bullet_as_topic_phrase(b) for b in bullets], 2)}的服务介绍"
    else:
        subject = "关于业务合作的沟通"
    return subject[:120]


def intro_greeting_line(context: dict[str, Any]) -> str:
    """Greeting line of the intro email. Without a company name fall back to 「您好：」 ("Hello:"), not filler like 「贵司相关负责人」 ("to the person in charge at your company").

    Extracted separately for the streaming path: the greeting does not depend on the model and can be sent to the frontend for rendering as soon as the connection opens,
    so users do not stare at a blank waiting for the model's first character.
    """
    company_name = _no_report_email_inputs(context)["company_name"]
    return f"{company_name}相关负责人您好：" if company_name != "贵司" else "您好："


def _intro_body_html_parts(paragraphs: list[str]) -> list[str]:
    """The model's middle section is plain text; the rich-text version is converted here; we do not ask the model for HTML.

    Model HTML would need another pass through a tag whitelist, and it likes inserting things like `<div style=...>` that get
    stripped wholesale, after which the two versions no longer match (recipients see the rich-text one).
    """
    esc = html.escape
    return [f"<p>{esc(text).replace(chr(10), '<br>')}</p>" for text in paragraphs if str(text).strip()]


def assemble_intro_email(
    context: dict[str, Any],
    body_paragraphs: list[str],
    body_html_parts: list[str] | None = None,
    contact_email: str = "",
) -> dict[str, str]:
    """Wrap the middle section with header and footer. **Greeting, contact info and signature are always assembled by code, never by the model.**

    Contact info is a whitelist (`_contact_lines`); contact info the model improvises is fabricated;
    a greeting left to the model would have it append job titles after the company name on its own (the report email path has a dedicated
    prompt rule for this). With these two sections fixed in code, the intro email prompt no longer needs to manage them.

    When `body_html_parts` is None it is converted from plain text; the model path goes here. The assembly path passes its own,
    because bullets are `<ul><li>` in rich text and cannot be derived from plain text.
    """
    parts = _no_report_email_inputs(context)
    greeting = intro_greeting_line(context)
    contacts = _contact_lines(parts["material"], contact_email)

    paragraphs = [greeting, *[str(p) for p in body_paragraphs if str(p).strip()]]
    if contacts:
        paragraphs.append("\n".join(f"{label}：{value}" for label, value in contacts))
    paragraphs.append("祝好")

    esc = html.escape
    html_parts = [f"<p>{esc(greeting)}</p>"]
    html_parts.extend(body_html_parts if body_html_parts is not None else _intro_body_html_parts(body_paragraphs))
    if contacts:
        html_parts.append(
            "<p>" + "<br>".join(f"<strong>{esc(label)}：</strong>{esc(value)}" for label, value in contacts) + "</p>"
        )
    html_parts.append("<p>祝好</p>")
    return {
        "content": "\n\n".join(paragraphs),
        "html_content": "".join(html_parts),
    }


def build_no_report_default_email_copy(context: dict[str, Any], contact_email: str = "") -> dict[str, str]:
    """Intro email when there is no real report: **generated from the sender's own brand profile**, not a fixed template.

    This used to be an entirely hard-coded Vigilath sales email: a customer selling restaurant franchises sent emails promoting
    Vigilath, signed with Vigilath's phone number. After the report gate launched, brands without report capability **always**
    take this branch, so the problem went from occasional to guaranteed; hence it now assembles from the profile.

    This implements the "standard intro" type and is also the degraded result when the model types fail to generate.
    """
    paragraphs, html_parts = build_no_report_default_body_paragraphs(context)
    return {
        "subject": default_intro_subject(context),
        **assemble_intro_email(context, paragraphs, html_parts, contact_email),
    }


# The whole line is just a greeting (「XX公司相关负责人您好：」「您好,」, i.e. "Hello, person in charge at XX company:" / "Hello,"), with no body content.
_INTRO_GREETING_LINE_RE = re.compile(r"^.{0,40}您好\s*[：:，,！!]?\s*$")
# Sign-off. 「此致 / 敬礼」 ("Sincerely / Regards") often spans two lines, so strip line by line.
_INTRO_SIGNOFF_LINE_RE = re.compile(r"^\s*(?:此致|敬礼|祝好|顺颂商祺|顺祝商祺|祝商祺|谢谢|感谢)\s*[！!。.]?\s*$")
# Contact info on its own line, whatever the label looks like.
_INTRO_CONTACT_LINE_RE = re.compile(
    r"^\s*(?:联系邮箱|联系电话|联系方式|联系人|邮箱|电话|手机|微信|官网|网址|网站|website|e-?mail|tel|wechat)\s*[：:]",
    re.IGNORECASE,
)


def strip_intro_body_affixes(text: str, context: dict[str, Any] | None = None, contact_email: str = "") -> str:
    """Strip greeting, sign-off and contact lines the model wrote on its own initiative.

    The prompt already says "these three are assembled by the system, do not write them", but the model's habit of writing business emails is very strong,
    and several times out of ten it writes them anyway. Without stripping, the assembled email has two greetings and two sign-offs.

    Contact info is matched by **value** (the ones filled in the profile) plus **line-leading label**: the model may copy the profile's
    email verbatim, or invent one that is well-formed but fake. Fake ones in the middle of the body cannot be safely
    identified, so we only strip consecutive lines at the end; ones appearing in the middle are caught by the
    `_outreach_copy_policy_violations` gate as a fallback.
    """
    lines = str(text or "").strip().splitlines()
    contact_values = {
        value for _, value in _contact_lines(
            (_no_report_email_inputs(context)["material"] if isinstance(context, dict) else {}),
            contact_email,
        )
    }

    while lines and (not lines[0].strip() or _INTRO_GREETING_LINE_RE.match(lines[0].strip())):
        lines.pop(0)
    while lines:
        tail = lines[-1].strip()
        if not tail or _INTRO_SIGNOFF_LINE_RE.match(tail) or _INTRO_CONTACT_LINE_RE.match(tail):
            lines.pop()
            continue
        if contact_values and any(value in tail for value in contact_values):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def split_intro_body_paragraphs(text: str) -> list[str]:
    """Split the model's middle section into paragraphs on blank lines. If there are none, split by single lines so the email is not one lump."""
    body = str(text or "").strip()
    if not body:
        return []
    blocks = [block.strip() for block in re.split(r"\n{2,}", body) if block.strip()]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in body.splitlines() if line.strip()] or blocks
    return blocks


_INTRO_SUBJECT_MAX_CHARS = 20
# 「！」 (full-width "!") here is not an ad word but tone: a first B2B email whose subject has an exclamation mark looks like a mass mailing at a glance.
_SUBJECT_ALWAYS_BANNED = ("！", "!")
_SUBJECT_AD_WORDS = ("紧急", "最后机会", "免费", "立减", "爆单", "颠覆", "第一", "唯一", "最好", "领先", "首个")


def sanitize_intro_subject(subject: str, context: dict[str, Any]) -> str:
    """Gate for model subjects: fall back to the template subject if too long or ad-like.

    Ad words are only blocked when **the profile does not say so itself**. If the brand's own positioning is 「GEO 领域首个中立计量
    工具」 ("the first neutral measurement tool in GEO"), that "first" is wording the customer endorsed, and blocking it would erase the real positioning; only
    "leading/only" added by the model itself is the problem.
    """
    text = str(subject or "").strip().strip('"“”《》 ')
    if not text or len(text) > _INTRO_SUBJECT_MAX_CHARS:
        return default_intro_subject(context)
    if any(word in text for word in _SUBJECT_ALWAYS_BANNED):
        return default_intro_subject(context)
    material = _no_report_email_inputs(context)["material"]
    blob = json.dumps(material, ensure_ascii=False, default=str) if isinstance(material, dict) else ""
    if any(word in text and word not in blob for word in _SUBJECT_AD_WORDS):
        return default_intro_subject(context)
    return text


# The subject line on the first line. The model occasionally writes it in Chinese as 「标题：」 ("Subject:"); accept that too.
_INTRO_STREAM_SUBJECT_RE = re.compile(r"^\s*(?:subject|标题)\s*[:：]\s*", re.IGNORECASE)

# In per-character preview, the start of a line must hold back some characters before releasing: sign-off and contact lines are removed whole by
# `strip_intro_body_affixes`, so releasing them means "type it out and then erase it". How many to hold is determined by the length
# of those two regexes:
#
# - `_INTRO_SIGNOFF_LINE_RE` is anchored to the whole line; the longest, 「顺颂商祺」 ("best wishes for your business") + punctuation, is only 5 characters;
# - `_INTRO_CONTACT_LINE_RE` is a **line-start** anchored "label + colon"; the longest label is website (7)
#   plus surrounding whitespace and colon, so it is always decidable within about ten characters.
#
# So once a line has 14 characters without matching either, it can no longer be a sign-off or contact line and can be released.
_INTRO_AFFIX_PROBE = 14

# Greeting lines need more holdback: `_INTRO_GREETING_LINE_RE` allows 40 characters before 「您好」 ("hello")
# (「广西华原过滤系统股份有限公司相关负责人您好：」, a long company name + "person in charge, hello:"), which cannot be ruled out before 48 characters.
# Only used on **the very first line of the email**: a greeting can only appear there; holding back other paragraphs too is wasted.
_INTRO_GREETING_PROBE = 48


@dataclass
class IntroStreamUpdate:
    """New output produced by one `push()`. All four may be empty; releasing nothing at all is normal.

    `chunks` and `paragraphs` are **two different things**; do not mix them up:

    - `chunks` are raw fragments that can be safely released while a paragraph is half-written, **for preview only**. They do not go into
      `self.paragraphs` and do not participate in assembling the email. The frontend appends them in order to the "pending paragraph",
      which together produce the typewriter effect. A fragment may be a single character, or a batch accumulated for a moment.
    - `paragraphs` are paragraphs that are finished, stripped, and going into the final email. When the frontend receives one it replaces the
      whole pending paragraph; the stripped version is authoritative, because some lines in between may have been stripped.
    """

    subject: str = ""
    chunks: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


class IntroBodyStream:
    """Release the model's streaming output **character by character**, while ensuring every released character has passed the gate.

    Released characters cannot be taken back: the frontend has already drawn them on screen, and erasing causes a flicker. So before releasing any character
    we must be able to prove two things:

    1. **It will not be blocked by the compliance gate**. The gate is substring matching: 「检测报告显」 is not a violation but 「检测报告显示」 ("audit report shows")
       is, so releasing per character would leak the first five characters. The fix is a holdback window at the end
       (`_INTRO_PREVIEW_HOLDBACK`, computed from the longest entry in the banned-word list); characters in the window are held
       until later characters arrive and the whole segment passes the gate together.
    2. **It will not be deleted by stripping**. `strip_intro_body_affixes` removes greeting, sign-off and
       contact lines whole. All three regexes are whole-line or line-start anchored with bounded length, so once a line has
       `_INTRO_AFFIX_PROBE` / `_INTRO_GREETING_PROBE` characters without matching, it will never
       match; only from that character on do we dare release per character.

    Characters are only the **preview granularity**; paragraphs remain the **persistence granularity**: stripping needs the whole paragraph (greeting at the start, sign-off and
    contact info at the end), and only after a paragraph is complete do we know which lines to strip. So after each paragraph completes we also send the stripped
    full paragraph, and the frontend uses it to replace that paragraph's preview.

    Why per-paragraph gating is equivalent to whole-email gating on this path: intro emails take the `can_reference_report=False`
    email branch, and under this branch **every applicable rule in `_outreach_copy_policy_violations` is
    a "violation on presence" substring match** (the two "violation on absence" rules, unsubscribe text for non-email channels
    and the report link when a report exists, do not apply here). Violation-on-presence is monotonic over prefixes: once the full text is dirty,
    the prefix at the moment it became dirty is already dirty. So per-paragraph blocking blocks exactly the same as whole-email blocking, just earlier.
    **This does not hold for the report email path** (a missing link is a violation, and the link is usually in the last paragraph; prefix gating would judge an
    unfinished email as violating), so do not copy this over there.

    Stripping (`strip_intro_body_affixes`) runs **per paragraph** here, not once over the whole email: streaming cannot know
    which is "the last paragraph", so every paragraph is treated as possibly the sign-off. The cost is that a lone 「谢谢」 ("thanks") line in the middle of the body is also
    stripped as a sign-off; it should not be its own paragraph anyway, so we accept that.
    """

    def __init__(self, context: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
        self._context = context if isinstance(context, dict) else {}
        self._metadata = metadata if isinstance(metadata, dict) else {}
        self._buffer = ""
        self._seen_subject = False
        # Number of characters in `self._buffer` already released as preview. Reset to zero at each paragraph boundary.
        self._sent_len = 0
        # Whether this paragraph has released any characters. Used instead of `_sent_len == 0` to detect "paragraph start":
        # blank/greeting lines at paragraph start are skipped, after which `_sent_len` is no longer 0 but we are still at the start.
        self._block_emitted = False
        # The real contact details filled in the profile. In preview, hold the line as soon as one appears: if this line is the paragraph's
        # last line, `strip_intro_body_affixes` removes it whole. Computed once; the profile does not change.
        self._contact_values = {
            value for _, value in _contact_lines(
                (_no_report_email_inputs(self._context)["material"] if self._context else {}),
                "",
            )
        }
        self.raw = ""
        self.json_mode: bool | None = None
        self.subject = ""
        self.paragraphs: list[str] = []
        self.violations: list[str] = []

    def push(self, delta: str) -> IntroStreamUpdate:
        """Feed one model delta; return the subject / paragraphs / violations newly determined by it."""
        text = str(delta or "")
        self.raw += text
        self._buffer += text
        if self.json_mode is None:
            head = self._buffer.lstrip()
            if not head:
                return IntroStreamUpdate()
            # When the model does not follow the `SUBJECT:` format and falls back to JSON or a code block, accumulate everything without sending,
            # and at `finish()` hand it to the caller to parse as JSON. No streaming effect in this case, but there is an email.
            self.json_mode = head[0] in "{[`"
        if self.json_mode or self.violations:
            return IntroStreamUpdate()
        return self._drain(final=False)

    def finish(self) -> IntroStreamUpdate:
        """Finish: also send the remaining half paragraph in the buffer. In JSON mode nothing is sent."""
        if self.json_mode or self.violations:
            return IntroStreamUpdate()
        return self._drain(final=True)

    def _drain(self, final: bool) -> IntroStreamUpdate:
        update = IntroStreamUpdate()
        if not self._seen_subject and ("\n" in self._buffer or final):
            update.subject = self._take_subject()
        blocks: list[str] = []
        while "\n\n" in self._buffer:
            head, _, self._buffer = self._buffer.partition("\n\n")
            self._sent_len = 0
            self._block_emitted = False
            blocks.append(head)
        if final:
            tail, self._buffer = self._buffer, ""
            self._sent_len = 0
            self._block_emitted = False
            # When the model did not output a single blank line, split by single lines so the email is not one lump.
            blocks.extend(split_intro_body_paragraphs(tail) if not blocks and not self.paragraphs else [tail])
        for block in blocks:
            self._consume(block, update)
            if self.violations:
                update.violations = list(self.violations)
                break
        # No preview on the final call: all content has already been sent as paragraphs; sending again would duplicate it.
        if not final and not self.violations:
            self._emit_preview(update)
        return update

    def _emit_preview(self, update: IntroStreamUpdate) -> None:
        """Release the characters of the current **unfinished** paragraph that can be safely released.

        "Safe to release" is determined jointly by two things; see `_preview_frontier`: the end holds back a fallback
        window (the compliance gate is substring matching), and the line start holds back enough characters (the kinds of lines stripped whole must not be typed out
        and then erased). Beyond those two, release as much as the model emits; the frontend sees it character by character.

        The gate checks "committed paragraphs + **all** raw text of this paragraph so far", not just up to the boundary:
        a banned word may straddle the boundary, and gating only the released part would miss it. The raw text is not stripped, but stripping only removes
        greeting/sign-off/contact lines, which contain no banned words, so gating the raw text is only stricter.
        """
        # Gate first, regardless of whether anything can be released this time: violations should be blocked as early as possible, rather than letting the user watch an email
        # slowly finish only to be withdrawn entirely. Release safety is guaranteed by "the gate sees all raw text".
        self.violations = _outreach_copy_policy_violations(
            self.subject,
            "\n\n".join([*self.paragraphs, self._buffer.strip()]),
            "email",
            can_reference_report=False,
            metadata=self._metadata,
        )
        if self.violations:
            update.violations = list(self.violations)
            return
        self._skip_leading_stripped_lines()
        frontier = self._preview_frontier()
        if frontier <= self._sent_len:
            return
        piece = self._buffer[self._sent_len:frontier]
        self._sent_len = frontier
        # Leading whitespace in a paragraph is eventually `strip()`-ed; do not show it in the preview either.
        if not self._block_emitted:
            piece = piece.lstrip()
        if not piece:
            return
        self._block_emitted = True
        update.chunks.append(piece)

    def _skip_leading_stripped_lines(self) -> None:
        """Skip blank and greeting lines at paragraph start that will be stripped, so they are not typed out and then erased.

        Mirrors the head loop of `strip_intro_body_affixes`: strip consecutively from the start, stop at body text.
        Only a complete line (newline seen) can be judged; an unfinished one waits.
        """
        if self._block_emitted:
            return
        while True:
            nl = self._buffer.find("\n", self._sent_len)
            if nl < 0:
                return
            line = self._buffer[self._sent_len:nl].strip()
            if line and not _INTRO_GREETING_LINE_RE.match(line):
                return
            self._sent_len = nl + 1

    def _preview_frontier(self) -> int:
        """Compute how far into this paragraph's raw text we may release. Take the tightest of three constraints."""
        buf = self._buffer
        # 1. Holdback window: hold the last few characters until later ones arrive and the whole passes the gate.
        # Without it 「检测报告显」 (a prefix of "audit report shows") would show first, and only when the next character arrives would we find a banned word, too late.
        gate = len(buf) - _INTRO_PREVIEW_HOLDBACK
        if gate <= self._sent_len:
            return self._sent_len
        line_begin = buf.rfind("\n", 0, self._sent_len) + 1
        nl = buf.find("\n", line_begin)
        line_end = nl if nl >= 0 else len(buf)
        line = buf[line_begin:line_end]
        # 2. Line start: release nothing from the kinds of lines stripped whole. Only judged while this line has not released anything yet;
        # once judged it is settled, no need to rejudge on every character.
        if self._sent_len == line_begin and not self._line_may_start(line, complete=nl >= 0):
            return self._sent_len
        # 3. Contact info: stop right there when the line contains a real email/URL from the profile. If this line is the paragraph's last,
        # it will be stripped whole; whether it is the last is only known when the paragraph completes, so hold it until then.
        if self._contact_values and any(value in line for value in self._contact_values):
            return self._sent_len
        # Release the trailing newline too: in-paragraph newlines are kept in the final paragraph. After this line is released,
        # the next call reruns the line-start check on the next line.
        return min(gate, line_end + 1 if nl >= 0 else line_end)

    def _line_may_start(self, line: str, complete: bool) -> bool:
        """Whether this line can start releasing characters.

        Stripping only removes three kinds of whole lines: greeting lines, sign-off lines, and standalone contact lines. All three regexes are
        whole-line or line-start anchored with bounded length, so once a line reaches the probe length without matching it will **never**
        match; that is exactly why we dare release per character before the line is complete.
        """
        text = line.strip()
        # A greeting can only appear on the first line of the whole email; holding back 48 characters on other lines is wasted.
        probe = _INTRO_GREETING_PROBE if not self.paragraphs and not self._block_emitted else _INTRO_AFFIX_PROBE
        # Measure the length after strip: all three regexes match against `text`, and `.{0,40}` bounds that too.
        # Measuring raw line length, leading whitespace alone could push past the window, and a following 「您好：」 ("Hello:") would still match.
        if len(text) > probe:
            return True
        if not complete:
            return False  # Neither long enough nor complete yet; cannot decide, wait
        if not text:
            return False  # Blank lines are stripped anyway
        return not (
            _INTRO_SIGNOFF_LINE_RE.match(text)
            or _INTRO_CONTACT_LINE_RE.match(text)
            or _INTRO_GREETING_LINE_RE.match(text)
        )

    def _take_subject(self) -> str:
        """The subject is taken by **line**, not by paragraph: models often put only one newline after the SUBJECT line."""
        self._seen_subject = True
        head, sep, rest = self._buffer.partition("\n")
        matched = _INTRO_STREAM_SUBJECT_RE.match(head.strip())
        if matched:
            self._buffer = rest.lstrip("\n") if sep else ""
            self._sent_len = 0
            self._block_emitted = False
            self.subject = sanitize_intro_subject(head.strip()[matched.end():], self._context)
        else:
            # The model wrote no subject line, so there is no subject to use: use the template one and keep the first line as body.
            self.subject = default_intro_subject(self._context)
        return self.subject

    def _consume(self, block: str, update: IntroStreamUpdate) -> None:
        text = str(block or "").strip()
        if not text:
            return
        cleaned = strip_intro_body_affixes(text, self._context)
        if not cleaned:
            # The whole paragraph is greeting / sign-off / contact lines, nothing left after stripping: those three are assembled by code.
            return
        self.paragraphs.append(cleaned)
        update.paragraphs.append(cleaned)
        self.violations = _outreach_copy_policy_violations(
            self.subject,
            "\n\n".join(self.paragraphs),
            "email",
            can_reference_report=False,
            metadata=self._metadata,
        )


def intro_stream_from_text(
    context: dict[str, Any],
    subject: str,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> IntroBodyStream:
    """Feed an already parsed whole middle section into `IntroBodyStream` at once and take its stripping + gate results.

    Used by the branch where "the model did not output plain text and fell back to JSON": there is no streaming then (JSON must wait
    for the closing brace to parse), but stripping and the compliance gate must not use a separate implementation: two implementations will sooner or later
    disagree on some edge case, and that one time is a non-compliant email going out.
    """
    stream = IntroBodyStream(context, metadata)
    stream.json_mode = False
    stream.push(f"SUBJECT: {str(subject or '').strip()}\n\n{str(body or '')}")
    stream.finish()
    return stream


def cached_report_email_drafts(db: Session, lead_id: int, channel: str, cache_key: str, limit: int = 4, draft_kind: str = "report_email") -> list[SalesAgentOutreachLogORM]:
    rows = (
        db.query(SalesAgentOutreachLogORM)
        .filter(
            SalesAgentOutreachLogORM.lead_id == int(lead_id),
            SalesAgentOutreachLogORM.channel == (channel or "email"),
            SalesAgentOutreachLogORM.send_status.in_(["draft", "pending_approval", "sent"]),
        )
        .order_by(SalesAgentOutreachLogORM.id.desc())
        .limit(30)
        .all()
    )
    matched: list[SalesAgentOutreachLogORM] = []
    for row in rows:
        meta = _loads(getattr(row, "metadata_json", "{}"), {})
        if meta.get("kind") == draft_kind and meta.get("cache_key") == cache_key:
            matched.append(row)
    return list(reversed(matched[: max(1, min(int(limit or 1), 6))]))


def _safe_report_copy_context(report: dict[str, Any], can_reference_report: bool, report_share: dict[str, Any] | None = None) -> dict[str, Any]:
    share = report_share or {}
    public_url = str(share.get("public_url") or "").strip()
    status = str(report.get("status") or share.get("report_status") or "").strip()
    if not can_reference_report:
        return {
            "has_real_detection": False,
            "status": status,
            "public_url": public_url,
            "rule": "不得引用检测分数、等级、具体问题或报告结论;只能说可基于公开官网做初步诊断。",
        }
    return {
        "has_real_detection": True,
        "status": status or "ready",
        "url": report.get("url") or "",
        "total_score": report.get("total_score"),
        "grade": report.get("grade") or "",
        "executive_summary": report.get("executive_summary") or "",
        "competitor_domains": (report.get("competitor_domains") or report.get("competitors") or [])[:8],
        "key_findings": (report.get("key_findings") or [])[:6],
        "quick_wins": (report.get("quick_wins") or [])[:6],
        "dimensions": (report.get("dimensions") or [])[:8],
        "detail_points": (report.get("detail_points") or _report_detail_points(report))[:12],
        "subresults_keys": (report.get("subresults_keys") or [])[:20],
        "public_url": public_url,
        "share_token": share.get("share_token") or share.get("public_token") or "",
        "rule": "只能引用这里列出的已完成真实报告摘要、competitor_domains、dimensions 和 detail_points,不得补充不存在的问题、竞品、提及率或分数;邮件正文必须包含 public_url。",
    }


def _outreach_context_payload(
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    contact: SalesAgentCompanyContactORM | None,
    profile: SalesAgentCustomerProfileORM | None,
    seller_profile: SalesAgentSellerProfileORM | None,
    account_icp: SalesAgentAccountICPORM | None,
    campaign: SalesAgentRecommendationCampaignORM | None,
    buyer_personas: list[SalesAgentBuyerPersonaORM],
    evidences: list[SalesAgentLeadEvidenceORM],
    profile_ctx: dict[str, Any],
    report: dict[str, Any],
    can_reference_report: bool,
    channel: str,
    report_share: dict[str, Any] | None = None,
    public_base_url: str = "",
) -> dict[str, Any]:
    evidence_payload = [x for x in (serialize_lead_evidence(row) for row in evidences) if x]
    db = object_session(lead)
    share_payload = report_share or {}
    contact_payload = [serialize_contact(contact)] if contact else (_company_contact_summary(db, company.id) if db is not None else [])
    lead_payload = serialize_lead(lead, company, contact_payload, evidence_payload, public_base_url)
    lead_payload["evidence"] = evidence_payload
    # Who the sender is, what they sell, which contact to leave: all from the brand profile uploaded by the customer.
    # This is the **only** source material for writing; `seller_profile` is a mirror kept only for legacy callers.
    brand_profile = brand_profile_payload(db, lead.user_id, seller_profile.id if seller_profile else None) if db is not None else {}
    recommendation_profile = recommendation_profile_for_campaign(db, lead.user_id, campaign.id if campaign else None) if db is not None else None
    return {
        "lead": lead_payload,
        "company": serialize_company(company),
        "contact": serialize_contact(contact) if contact else None,
        "profile": serialize_profile(profile) if profile else None,
        "brand_profile": brand_profile,
        "seller_profile": serialize_seller_profile(seller_profile) or _legacy_seller_profile_payload(profile),
        "recommendation_profile": recommendation_profile,
        "account_icp": serialize_account_icp(account_icp) or _legacy_account_icp_payload(profile),
        "buyer_personas": [x for x in (serialize_buyer_persona(row) for row in buyer_personas) if x] or _legacy_buyer_persona_payloads(profile),
        "campaign": serialize_recommendation_campaign(campaign),
        "lead_evidence": evidence_payload,
        "profile_context": profile_ctx,
        "report_context": _safe_report_copy_context(report, can_reference_report, share_payload),
        "report_share": share_payload,
        "public_report_url": str(share_payload.get("public_url") or ""),
        "channel": channel,
        "copy_rules": [
            "正文必须由大模型根据本上下文生成,后端只负责校验和保存。",
            "以当前登录用户自己的业务口吻写,不要介绍 Vigilath,不要写“这里是 Vigilath/我们是 Vigilath”。",
            "发信方是谁、卖什么、有什么案例资质,一律以 brand_profile(客户上传的品牌资料)为准;"
            "seller_profile 是它的镜像,两者冲突时以 brand_profile 为准。",
            "落款联系方式只能用 brand_profile.email_material.contacts 里列出的;"
            "那里没有的电话/邮箱/网址一个都不许出现 —— 编出来的联系方式发出去就是事故。",
            "不得违反 brand_profile.email_material.content_redlines 里列的内容红线。",
            "SellerProfile/service_scope 只表示用户卖什么;RecommendationCampaign 的 target_industries/target_regions 才是本次推荐筛选条件。",
            "如果 recommendation_profile 不为空,说明这条线索来自聊天保存的本次推荐画像;邮件内容要优先参考 recommendation_profile 的服务能力、适合客户、目标行业、目标地区和目标企业规模,但仍不得把它写回或说成品牌画像。",
            "触达角度优先结合 recommendation_profile、BuyerPersona.messaging_angles 与 LeadEvidence,不要把历史 industries/regions 当长期画像。",
            "email 渠道根据报告状态生成:有真实报告时可按 AI-GEO 审计简报结构输出并包含公开报告链接;没有真实报告时只写基于公开官网和业务场景的初步沟通,不得写核心数据差距、检测结论或报告链接。不要在正文末尾添加 AI 生成标识、草稿审批说明或退订说明。",
            "没有已完成且属于该企业官网的真实报告或明确证据时,不得写具体百分比、倍数、排名、竞品名、检测分数、等级、llms.txt 缺失、结构化数据不足等具体诊断结论;只能写待审计确认或可进一步量化。",
            "只生成草稿,不得承诺已经发送或自动群发。",
            "email 草稿不要包含退订/不再联系/停止联系说明;其它渠道必须包含退订/不再联系/停止联系说明。",
        ],
    }


_OUTREACH_URL_RE = re.compile(r"https?://[^\s，,；;。)）]+|/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")


def _outreach_text_without_urls(text: str) -> str:
    """Check the platform signature after removing links, allowing emails to carry public report links."""
    return _OUTREACH_URL_RE.sub("", str(text or ""))


# The three banned-word lists below used to be literals inside `_outreach_copy_policy_violations`.
# They are module-level because the intro email's **per-character preview** measures them: the gate is substring matching, and released characters must fall
# within "the range that has already passed the gate as a whole", otherwise 「检测报告显」 (a prefix of "audit report shows") would show first and only the next character would reveal
# a banned word. The holdback window takes the longest entry's length; see `_INTRO_PREVIEW_HOLDBACK`.
_OUTREACH_IMPERSONATION_PHRASES = ("这里是 Vigilath", "我是 Vigilath", "我们是 Vigilath", "Vigilath 官方", "官方合作")
_OUTREACH_AUTOSEND_PHRASES = ("已发送", "自动发送", "自动群发", "批量群发")
_OUTREACH_UNVERIFIED_CLAIMS = (
    "检测报告显示", "诊断报告显示", "报告显示", "已完成诊断", "已经检测",
    "完整诊断报告", "查看完整诊断报告", "报告链接", "公开报告", "诊断结论",
    "总分", "评分为", "等级为", "等级是", "llms.txt 缺失", "结构化数据不足",
    "你们官网存在", "官网存在明显问题", "官网存在严重问题", "ai visibility 弱", "可见性很弱",
)

# Holdback window for per-character preview: this many characters at the end of the buffer are **not sent yet**, until later ones arrive and the whole passes the gate.
# Computed from the banned-word lists themselves, not hard-coded: if longer words are added later, the window widens automatically,
# avoiding "added a word but the preview still leaks half of it", a bug only found in production.
# Also includes "vigilath": the signature gate matches that word itself.
_INTRO_PREVIEW_HOLDBACK = max(
    len(phrase)
    for phrase in (
        "vigilath",
        *_OUTREACH_IMPERSONATION_PHRASES,
        *_OUTREACH_AUTOSEND_PHRASES,
        *_OUTREACH_UNVERIFIED_CLAIMS,
    )
)


def _outreach_copy_policy_violations(subject: str, content: str, channel: str, can_reference_report: bool, public_report_url: str = "", metadata: dict[str, Any] | None = None) -> list[str]:
    text = f"{subject}\n{content}"
    sender_text = _outreach_text_without_urls(text)
    sender_lower = sender_text.casefold()
    lower = text.casefold()
    meta = metadata if isinstance(metadata, dict) else {}
    # The old exemption was "this is the no-report default template", back when the template itself was a hard-coded Vigilath sales email.
    # Now intro emails are generated from the sender's own profile, so the exemption became "the sender **really is** Vigilath":
    # this flag is computed by `_brand_is_vigilath()` from the brand profile and the model cannot touch it,
    # so Vigilath appearing in any other customer's email is still a violation.
    sender_is_vigilath = bool(meta.get("sender_is_vigilath"))
    violations: list[str] = []
    if "vigilath" in sender_lower and not sender_is_vigilath:
        violations.append("触达内容不能介绍或署名 Vigilath,应使用当前用户自己的口吻")
    if any(phrase in sender_text for phrase in _OUTREACH_IMPERSONATION_PHRASES):
        violations.append("触达内容不能冒充平台或官方合作方")
    if any(phrase in text for phrase in _OUTREACH_AUTOSEND_PHRASES):
        violations.append("触达草稿不能声称已经发送或自动群发")
    sms_template_mode = channel == "sms" and str(meta.get("sms_content_mode") or "") == "template"
    if channel == "sms" and not sms_template_mode and len(content) > 320:
        # Hard-block only when too long. "Will split into N billed segments" is not a violation: a violation 400s the whole draft,
        # while paying for an extra segment is the user's own trade-off; the segment count is echoed in the delivery record's segments.
        violations.append("短信草稿过长,请压缩为一条短消息")
    if sms_template_mode:
        # Template channel bodies are registered with the platform; length and unsubscribe text are determined by the registered content,
        # and checking them again with free-copy rules here would only flag compliant templates as violations.
        pass
    elif channel != "email" and not _has_unsubscribe_instruction(content):
        violations.append("触达草稿必须包含退订/不再联系/停止联系说明")
    if not can_reference_report:
        if any(phrase.casefold() in lower for phrase in _OUTREACH_UNVERIFIED_CLAIMS):
            violations.append("没有该企业已完成真实报告时,不能写具体诊断结论、分数或等级")
    elif channel == "email":
        url = (public_report_url or "").strip()
        if not url:
            violations.append("报告型邮件必须包含可公开访问的 Sales Agent 报告链接")
        elif url not in content:
            violations.append("报告型邮件正文必须包含公开报告链接")
    return violations


def _lead_report_context_for_outreach(
    db: Session,
    user_id: int,
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    seller_profile_id: int | None = None,
    commit_share: bool = False,
    public_base_url: str = "",
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    report = _loads(lead.report_snapshot, {})
    share_payload: dict[str, Any] = {}
    report_row: FullDiagnosisReportORM | None = None
    share = _active_report_share_for_lead(db, user_id, lead.id)
    created_share = False
    changed = False

    if share is not None:
        report_row = _full_report_for_share(db, share)
    if report_row is None and lead.source_report_table == "full_diagnosis_reports" and lead.source_report_id:
        candidate = _full_report_by_id(db, user_id, lead.source_report_id)
        if candidate is not None and normalize_domain(candidate.url) == normalize_domain(company.website) and candidate.status in {"generating", "ready"}:
            report_row = candidate
    if report_row is None:
        candidate = _latest_matching_full_report(db, user_id, company)
        if candidate is not None and candidate.status in {"generating", "ready"}:
            report_row = candidate

    if report_row is not None:
        report = _full_report_summary(report_row)
        _sync_lead_report_snapshot(lead, report_row)
        changed = True

    # Report emails are only allowed when "the current brand sells audits/diagnostics". Only checking whether the lead/company has a historical report
    # leaks across brands: in the same account brand A did a website audit and brand B sells no reports, yet B's lead emails would be written
    # with A's audit report structure. report_capable in the brand profile is a hard gate, and both it and a real report are required.
    can_reference_report = bool(
        report.get("has_real_detection")
        and _report_summary_matches_company(company, report)
        and brand_report_capable(db, user_id, seller_profile_id)
    )
    if can_reference_report and report_row is not None:
        if share is None:
            share = _create_report_share(db, user_id, lead.id, company.id, report_row.id)
            created_share = True
        share_payload = serialize_report_share(share, report_row, public_base_url)
    else:
        share_payload = {}

    if (created_share or changed) and commit_share:
        db.commit()
        db.refresh(lead)
        if share is not None:
            db.refresh(share)
            if can_reference_report:
                share_payload = serialize_report_share(share, report_row, public_base_url)
    return report, share_payload, can_reference_report


@dataclass(frozen=True)
class OutreachStructuredContext:
    """Profile / campaign / report context for a lead, fetched in one go.

    It exists to **avoid querying twice**: `/outreach/drafts/auto` first calls `get_outreach_context()` to get the context
    for the model, then after generation calls `generate_outreach_draft()` to persist to DB, and the latter used to re-query the whole set
    (inside a loop over the number of variants). It is pure read and the same whoever queries, so the endpoint passes it down after querying.

    **Excludes** lead / company / contact / compliance gate: those must be re-fetched before persisting. Between fetching the context
    and persisting there is a model call (a dozen seconds or so), during which the lead may have just unsubscribed, so the gate must see the latest.
    """

    profile: Any
    seller: Any
    account_icp: Any
    campaign: Any
    personas: Any
    evidences: Any
    report: dict[str, Any]
    report_share: dict[str, Any]
    can_reference_report: bool
    profile_ctx: dict[str, Any]


def _build_outreach_structured_context(
    db: Session,
    user_id: int,
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    public_base_url: str = "",
    commit_share: bool = False,
) -> OutreachStructuredContext:
    profile, seller, account_icp, campaign, personas, evidences = _lead_structured_context(db, user_id, lead)
    report, report_share, can_reference_report = _lead_report_context_for_outreach(
        db,
        user_id,
        lead,
        company,
        seller_profile_id=seller.id if seller else None,
        commit_share=commit_share,
        public_base_url=public_base_url,
    )
    return OutreachStructuredContext(
        profile=profile,
        seller=seller,
        account_icp=account_icp,
        campaign=campaign,
        personas=personas,
        evidences=evidences,
        report=report,
        report_share=report_share,
        can_reference_report=can_reference_report,
        profile_ctx=_outreach_profile_context(profile, company, seller, campaign, personas, account_icp),
    )


def get_outreach_context_with_structured(
    db: Session,
    user_id: int,
    lead_id: int,
    channel: str = "email",
    contact_id: int | None = None,
    public_base_url: str = "",
) -> tuple[dict[str, Any], OutreachStructuredContext]:
    """Get the outreach context, and also hand back the structured context that was queried as-is.

    Callers can pass the second return value to `generate_outreach_draft(structured=...)` to skip re-querying.
    """
    lead, company = _lead_company(db, user_id, lead_id)
    ch = (channel or "email").strip()
    contact = _contact_for_lead(db, lead, contact_id, ch)
    decision, reasons = _outreach_guard(db, user_id, lead, contact, ch)
    structured = _build_outreach_structured_context(db, user_id, lead, company, public_base_url, commit_share=True)
    payload = _outreach_context_payload(
        lead, company, contact, structured.profile, structured.seller, structured.account_icp,
        structured.campaign, structured.personas, structured.evidences, structured.profile_ctx,
        structured.report, structured.can_reference_report, ch, structured.report_share, public_base_url,
    )
    payload.update({"blocked": decision == "blocked", "decision": decision, "reasons": reasons})
    return payload, structured


def get_outreach_context(
    db: Session,
    user_id: int,
    lead_id: int,
    channel: str = "email",
    contact_id: int | None = None,
    public_base_url: str = "",
) -> dict[str, Any]:
    return get_outreach_context_with_structured(db, user_id, lead_id, channel, contact_id, public_base_url)[0]


def generate_outreach_draft(
    db: Session,
    user_id: int,
    lead_id: int,
    channel: str = "email",
    contact_id: int | None = None,
    tone: str = "professional",
    subject: str = "",
    content: str = "",
    public_base_url: str = "",
    metadata: dict[str, Any] | None = None,
    structured: OutreachStructuredContext | None = None,
) -> dict[str, Any]:
    """`structured` is the context the caller just queried; when passed, no re-query happens (see `OutreachStructuredContext`).

    lead / contact and the compliance gate are not reused and are re-fetched every time: that is the last gate before persisting.
    """
    lead, company = _lead_company(db, user_id, lead_id)
    ch = (channel or "email").strip()
    contact = _contact_for_lead(db, lead, contact_id, ch)
    decision, reasons = _outreach_guard(db, user_id, lead, contact, ch)
    if decision == "blocked":
        return {"blocked": True, "decision": decision, "reasons": reasons, "hint": "已阻断,不会生成外部触达草稿。"}

    ctx = structured or _build_outreach_structured_context(db, user_id, lead, company, public_base_url)
    profile, seller, account_icp = ctx.profile, ctx.seller, ctx.account_icp
    campaign, personas, evidences = ctx.campaign, ctx.personas, ctx.evidences
    report, report_share, can_reference_report = ctx.report, ctx.report_share, ctx.can_reference_report
    profile_ctx = ctx.profile_ctx

    raw_content = str(content or "").strip()
    if not raw_content:
        context = _outreach_context_payload(lead, company, contact, profile, seller, account_icp, campaign, personas, evidences, profile_ctx, report, can_reference_report, ch, report_share, public_base_url)
        raise SalesAgentError(
            "触达正文必须先由大模型根据客户画像、推荐企业和上下文生成,后端不再使用固定行业短信/邮件模板。"
            f"请按 context 生成 content 后重新保存。context={json.dumps(context, ensure_ascii=False, default=str)}",
            status_code=400,
        )

    body = _ensure_unsubscribe_line(raw_content, ch, str((metadata or {}).get("sms_content_mode") or ""))
    subject_text = _strip_platform_sender(str(subject or "").strip())
    if ch == "email" and not subject_text:
        subject_text = _fallback_outreach_subject(company, profile_ctx)
    if ch != "email":
        subject_text = ""

    metadata_payload = dict(metadata or {})
    if contact is not None and not metadata_payload.get("contact_id"):
        metadata_payload["contact_id"] = contact.id
    violations = _outreach_copy_policy_violations(subject_text, body, ch, can_reference_report, str(report_share.get("public_url") or ""), metadata_payload)
    if violations:
        record_compliance_event(
            db,
            user_id,
            "generate_outreach_draft",
            "high",
            "blocked",
            "；".join(violations),
            {"lead_id": lead.id, "contact_id": contact.id if contact else None, "channel": ch, "tone": tone},
        )
        raise SalesAgentError("触达草稿未通过合规校验:" + "；".join(violations), status_code=400)

    draft = SalesAgentOutreachLogORM(
        lead_id=lead.id,
        channel=ch,
        subject=subject_text[:255],
        content=body,
        send_status="draft",
        metadata_json=_dumps(metadata_payload),
        requires_approval=True,
        has_unsubscribe=_has_unsubscribe_instruction(body),
    )
    lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
    db.add(draft)
    db.commit()
    db.refresh(draft)
    record_compliance_event(
        db,
        user_id,
        "generate_outreach_draft",
        "medium",
        decision,
        "保存大模型生成的触达草稿但不发送",
        {"lead_id": lead.id, "draft_id": draft.id, "channel": ch, "tone": tone, "reasons": reasons},
    )
    contacts = _company_contact_summary(db, company.id)
    return {
        "blocked": False,
        "decision": decision,
        "draft": serialize_draft(draft),
        "lead": serialize_lead(lead, company, contacts, public_base_url=public_base_url),
        "company": serialize_company(company, contacts),
        "contact": serialize_contact(contact) if contact else None,
        "report_share": report_share,
        "public_report_url": str(report_share.get("public_url") or ""),
        "reasons": reasons,
        "policy": "llm_generated_draft_only_human_approval_required",
    }


def serialize_draft(row: SalesAgentOutreachLogORM) -> dict[str, Any]:
    delivery_payload = None
    delivery = None
    is_sms = row.channel == "sms"
    db = object_session(row)
    if db is not None and row.id:
        # Fetch delivery records by the draft's own channel: the two tables are parallel, and querying the email table for an SMS draft always returns nothing,
        # so the frontend would always show "not sent".
        if is_sms:
            delivery = _latest_sms_delivery_for_draft(db, row.id)
            delivery_payload = serialize_sales_agent_sms_delivery(delivery) if delivery is not None else None
        else:
            delivery = _latest_email_delivery_for_draft(db, row.id)
            delivery_payload = serialize_sales_agent_email_delivery(delivery) if delivery is not None else None
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "channel": row.channel,
        "subject": row.subject,
        "content": row.content,
        "send_status": row.send_status,
        "approved_by": row.approved_by,
        "metadata": _loads(getattr(row, "metadata_json", "{}"), {}),
        "requires_approval": row.requires_approval,
        "has_unsubscribe": row.has_unsubscribe,
        "delivery": delivery_payload,
        # These top-level fields are flat fields the frontend draft card reads directly. SMS has no recipient_email / from_email /
        # read_at (no opens); leave them empty rather than stuffing SMS values in, since the frontend label says "recipient email".
        "recipient_email": getattr(delivery, "recipient_email", "") if not is_sms and delivery is not None else "",
        "recipient_phone": getattr(delivery, "recipient_phone", "") if is_sms and delivery is not None else "",
        "from_email": getattr(delivery, "from_email", "") if not is_sms and delivery is not None else "",
        "failure_reason": (delivery.failure_reason or "") if delivery is not None else "",
        "read_at": _iso(delivery.read_at) if not is_sms and delivery is not None else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _draft_lead_company(db: Session, user_id: int, draft_id: int) -> tuple[SalesAgentOutreachLogORM, SalesAgentLeadRecommendationORM, SalesAgentCompanyORM]:
    draft = (
        db.query(SalesAgentOutreachLogORM)
        .join(SalesAgentLeadRecommendationORM, SalesAgentOutreachLogORM.lead_id == SalesAgentLeadRecommendationORM.id)
        .filter(SalesAgentOutreachLogORM.id == int(draft_id), SalesAgentLeadRecommendationORM.user_id == user_id)
        .first()
    )
    if draft is None:
        raise SalesAgentError("触达草稿不存在或不属于当前用户。", status_code=404)
    lead, company = _lead_company(db, user_id, draft.lead_id)
    return draft, lead, company


# Resend cap for the same email. `sent` used to be a confirmable status, and the frontend changed the button to "send again",
# so the same email could be really sent to the same recipient unlimited times, hurting the sending domain's reputation and inviting blacklisting.
RESEND_MAX_TIMES = 3
RESEND_COOLDOWN_SECONDS = 300


def _confirm_policy_label(channel: str) -> str:
    """Which config the send confirmation uses. SMS and email have different config pages; get it wrong and the user goes to the wrong place."""
    return "manual_confirmation_database_sms_config" if channel == "sms" else "manual_confirmation_database_email_config"


def _guard_resend(db: Session, draft: SalesAgentOutreachLogORM) -> None:
    """Resending an already sent draft must pass the cooldown and count cap."""
    if draft.send_status != "sent":
        return
    from agents.sales_agent.models import SalesAgentEmailDeliveryORM, SalesAgentSmsDeliveryORM

    if draft.channel == "sms":
        rows = (
            db.query(SalesAgentSmsDeliveryORM)
            .filter(SalesAgentSmsDeliveryORM.outreach_log_id == draft.id,
                    SalesAgentSmsDeliveryORM.status.in_(("sent", "delivered")))
            .order_by(SalesAgentSmsDeliveryORM.id.desc())
            .all()
        )
        noun = "条短信"
    else:
        rows = (
            db.query(SalesAgentEmailDeliveryORM)
            .filter(SalesAgentEmailDeliveryORM.outreach_log_id == draft.id,
                    SalesAgentEmailDeliveryORM.status == "sent")
            .order_by(SalesAgentEmailDeliveryORM.id.desc())
            .all()
        )
        noun = "封"
    if not rows:
        return
    if len(rows) >= RESEND_MAX_TIMES:
        raise SalesAgentError(f"这{noun}已经发出 {len(rows)} 次,不能再发了。要继续跟进请改内容后重新生成草稿。")
    last = rows[0].sent_at or rows[0].created_at
    if last is not None:
        waited = (datetime.utcnow() - last).total_seconds()
        if waited < RESEND_COOLDOWN_SECONDS:
            raise SalesAgentError(f"刚刚已经发过,请等 {int((RESEND_COOLDOWN_SECONDS - waited) / 60) + 1} 分钟再重发。")


def confirm_outreach_send(
    db: Session,
    user_id: int,
    draft_id: int,
    confirmed: bool = True,
    actor: str = "",
    note: str = "",
    public_base_url: str = "",
) -> dict[str, Any]:
    """Manually confirm the outreach action; for email, resolve the sender mailbox and official company recipient from the DB config."""
    draft, lead, company = _draft_lead_company(db, user_id, draft_id)
    if draft.send_status not in {"draft", "rejected", "pending_approval", "sent", "failed", "disabled", "no_recipient"}:
        raise SalesAgentError(f"当前草稿状态为 {draft.send_status},不能重复确认。")
    if confirmed:
        _guard_resend(db, draft)
    metadata = _loads(getattr(draft, "metadata_json", "{}"), {})
    try:
        metadata_contact_id = int(metadata.get("contact_id") or 0) or None
    except Exception:  # noqa: BLE001
        metadata_contact_id = None
    contact = None
    if metadata_contact_id:
        contact = db.query(SalesAgentCompanyContactORM).filter(
            SalesAgentCompanyContactORM.id == metadata_contact_id,
            SalesAgentCompanyContactORM.company_id == company.id,
        ).first()
    if contact is None:
        contacts = _company_contact_summary(db, company.id, limit=1)
        if contacts:
            contact = db.get(SalesAgentCompanyContactORM, contacts[0]["id"])
    target_warnings: list[str] = []
    if draft.channel in {"sms", "phone_script"} and (contact is None or not contact.phone):
        target_warnings.append(f"{draft.channel} 渠道缺少可确认的联系电话,确认后会进入待审批并标记为待补充号码")
    if draft.channel == "contact_form" and (contact is None or not contact.contact_form_url):
        target_warnings.append("contact_form 渠道缺少可确认的官网表单地址,确认后会进入待审批并标记为待补充表单地址")
    decision, reasons = _outreach_guard(db, user_id, lead, contact, draft.channel)
    if decision == "blocked":
        draft.send_status = "rejected"
        lead.status = "Invalid" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
        db.commit()
        record_compliance_event(db, user_id, "confirm_outreach_send", "high", "blocked", "；".join(reasons), {"draft_id": draft.id, "lead_id": lead.id, "channel": draft.channel})
        return {"blocked": True, "draft": serialize_draft(draft), "reasons": reasons, "policy": _confirm_policy_label(draft.channel)}
    if not confirmed:
        draft.send_status = "rejected"
        lead.status = "Paused" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
        db.commit()
        record_compliance_event(db, user_id, "confirm_outreach_send", "medium", "blocked", note or "人工取消发送", {"draft_id": draft.id, "lead_id": lead.id})
        return {"blocked": True, "draft": serialize_draft(draft), "reasons": ["人工取消发送"], "policy": _confirm_policy_label(draft.channel)}

    delivery = None
    status_note = "人工确认触达草稿,等待人工/外部系统发送。"
    compliance_decision = "needs_approval"
    if draft.channel == "email":
        delivery = send_sales_agent_email_delivery(db, user_id, draft, lead, company, public_base_url=public_base_url)
        if delivery.status == "sent":
            draft.send_status = "sent"
            lead.status = "Contacted" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = f"人工确认后邮件已发送到企业收件邮箱 {delivery.recipient_email}"
            compliance_decision = "allowed"
        elif delivery.status == "disabled":
            draft.send_status = "disabled"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "邮件发送已关闭,未真实外发。"
        elif delivery.status == "no_recipient":
            draft.send_status = "no_recipient"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "目标企业缺少可用公开收件邮箱,未真实外发。"
        else:
            draft.send_status = "failed"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "邮件发送失败,请检查邮箱配置后重试。"
    elif draft.channel == "sms":
        delivery = send_sales_agent_sms_delivery(db, user_id, draft, lead, company, public_base_url=public_base_url)
        if delivery.status == "sent":
            draft.send_status = "sent"
            lead.status = "Contacted" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = f"人工确认后短信已下发到 {delivery.recipient_phone}(计费 {delivery.segments} 条)"
            compliance_decision = "allowed"
        elif delivery.status == "disabled":
            draft.send_status = "disabled"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "短信发送已关闭,未真实外发。"
        elif delivery.status == "no_recipient":
            draft.send_status = "no_recipient"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "目标企业没有可发短信的手机号,未真实外发。"
        elif delivery.status == "rejected":
            # Rate limiting / missing template / missing variables all mean "not sending this time", not "send failed";
            # separate statuses let users know whether to fix config or wait for cooldown, instead of retrying repeatedly.
            draft.send_status = "rejected"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "短信未通过发送前校验,未真实外发。"
        else:
            draft.send_status = "failed"
            lead.status = "Draft Ready" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
            status_note = delivery.failure_reason or "短信下发失败,请检查短信通道配置后重试。"
    else:
        draft.send_status = "pending_approval"
        lead.status = "Pending Approval" if lead.status not in {"Unsubscribed", "Blacklisted"} else lead.status
        status_note = "；".join(target_warnings) if target_warnings else status_note
    draft.approved_by = actor or f"user:{user_id}"
    activity = SalesAgentLeadActivityORM(
        lead_id=lead.id,
        activity_type="outreach_confirm",
        from_status="Draft Ready",
        to_status=lead.status,
        reason="manual",
        note=note or status_note,
        actor=draft.approved_by,
    )
    db.add(activity)
    db.commit()
    db.refresh(draft)
    if delivery is not None:
        db.refresh(delivery)
    record_compliance_event(
        db,
        user_id,
        "confirm_outreach_send",
        "medium",
        compliance_decision,
        status_note,
        {
            "draft_id": draft.id,
            "lead_id": lead.id,
            "channel": draft.channel,
            "delivery_id": delivery.id if delivery else None,
            "delivery_status": delivery.status if delivery else "",
            "recipient_email": getattr(delivery, "recipient_email", "") if delivery else "",
            "recipient_phone": getattr(delivery, "recipient_phone", "") if delivery else "",
        },
    )
    if delivery is None:
        delivery_payload = None
    elif draft.channel == "sms":
        delivery_payload = serialize_sales_agent_sms_delivery(delivery)
    else:
        delivery_payload = serialize_sales_agent_email_delivery(delivery)
    return {
        "blocked": False,
        "draft": serialize_draft(draft),
        "lead": serialize_lead(lead, company, _company_contact_summary(db, company.id), public_base_url=public_base_url),
        "delivery": delivery_payload,
        "reasons": [*target_warnings, *reasons],
        "policy": _confirm_policy_label(draft.channel),
        "hint": status_note,
    }
