"""/api/sales-agent/* routes."""
from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import os
import json
import logging
import re
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from agents.sales_agent.agent import SALES_AGENT_GUIDE_REPLY, build_sales_agent, is_low_value_chat, route_intent, should_use_guide_reply
from agents.sales_agent.methods import _dumps, _find_user_session, append_chat_message, load_message_history, reset_conversation, resolve_account, save_message_history
from agents.sales_agent.model import _model_request_timeout, get_sales_agent_model, get_sales_agent_model_settings, resolve_sales_agent_model_config
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentRecommendationCampaignORM,
)
from agents.sales_agent.runtime_log import duration_ms, model_config_fields, new_model_call_id, now_monotonic, write_model_call_log
from agents.sales_agent.time_parse import (
    bj_day_start_utc,
    describe_utc,
    has_schedule_marker,
    has_time_expression,
    parse_when_text,
    to_local,
    to_utc,
    when_followup_text,
)
from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS, TARGET_INDUSTRY_OPTIONS, TARGET_REGION_OPTIONS
from agents.sales_agent.flow import LeadState, SlotCatalog, coarse_intent_for, extract_slots, intent_is_read_only, next_step, normalize_intent, resolve_intent
from agents.sales_agent.flow.router import BRAND, CUSTOM_INDUSTRY, READY
from agents.sales_agent.flow.state import SLOT_ORDER
from agents.sales_agent.runtime.guard import SalesAgentGuardDenied, check_user as guard_check_user
from agents.sales_agent.runtime.verify import (
    FLOW_ACTION_TOKENS,
    identity_or_reference_kind,
    is_casual_chat,
    is_detail_followup,
    resolve_delivery_status_followup,
    resolve_detection_action,
    resolve_lead_action,
    resolve_orphan_flow_action,
    resolve_stats_domain_correction,
    verify_delivery_status_reply,
    verify_delivery_target_reply,
    verify_detection_keywords_answer,
    verify_slot_answer,
)
from agents.sales_agent.runtime.limits import SalesAgentTimeout, public_message, run_guarded, timeout_for, usage_limits_for
from agents.sales_agent.runtime.payload import card_payload
from pydantic_ai.exceptions import UsageLimitExceeded
from agents.sales_agent.schemas import (
    ChatIn,
    CustomerProfileIn,
    DiscoverCompaniesIn,
    GenerateReportIn,
    LeadStatusIn,
    OutreachAutoDraftIn,
    OutreachConfirmIn,
    OutreachDraftIn,
    RankCompaniesIn,
    RecommendCompaniesIn,
    SalesAgentEmailAccountIn,
    SalesAgentEmailAccountPatchIn,
    SalesAgentEmailSettingsIn,
    SalesAgentEmailTestIn,
    SalesAgentOptionIn,
    SalesAgentOptionPatchIn,
    SalesAgentRecommendationProfileIn,
    SalesAgentRecommendationProfileRenameIn,
    SalesAgentRecommendationProfileUsageIn,
    SalesAgentSmsAccountIn,
    SalesAgentSmsAccountPatchIn,
    SalesAgentSmsSettingsIn,
    SalesAgentSmsTemplateIn,
    SalesAgentSmsTemplatePatchIn,
    SalesAgentSmsTestIn,
    SalesAgentUndoIn,
    SendScheduleConfirmIn,
    SendScheduleCreateIn,
    SendSchedulePatchIn,
    SendSchedulePreviewIn,
)
from agents.sales_agent.service.industry_mapping import map_target_industries_to_available
from agents.sales_agent.service import (
    CAPABILITY,
    INTERNAL_ERROR_RE,
    SAFE_RUNTIME_ERROR,
    public_error_text,
    COMPANY_SIZE_BUCKET_LABELS,
    INDUSTRY,
    REGION,
    SalesAgentError,
    TYPICAL_CUSTOMER,
    _apply_lead_flow_selected_values_to_guidance,
    _brand_ref_for_seller,
    _clean_list,
    RECOMMENDATION_PROFILE_BRAND_REF_NONE,
    archive_recommendation_profile,
    create_recommendation_profile_from_flow,
    get_recommendation_profile_version,
    list_active_recommendation_profiles,
    record_recommendation_profile_usage,
    rename_recommendation_profile,
    serialize_recommendation_profile_version,
    upsert_recommendation_profile_from_flow,
    _latest_account_icp,
    BRAND_NEW_TOKEN,
    BRAND_NO_PROFILE_HEADLINE,
    WEBSITE_SCOPE_ONLY_OPTION,
    WEBSITE_SCOPE_PROFILE_OPTION,
    _brand_is_vigilath,
    _ensure_contact_block,
    _ensure_contact_block_html,
    _latest_seller_profile,
    _normalize_company_size_bucket,
    _clean_profile_option_list,
    active_brand_count,
    add_custom_options,
    archive_custom_option,
    archive_custom_option_value,
    assemble_intro_email,
    brand_profile_payload,
    build_brand_picker_card,
    build_entry_guidance_card,
    build_guidance_card_from_result,
    build_missing_info_guidance_card,
    build_no_report_default_body_paragraphs,
    build_seller_profile_snapshot_card,
    build_seller_profile_summary_card,
    build_slot_catalog,
    build_website_scope_clarify_card,
    cached_report_email_drafts,
    confirm_outreach_send,
    create_custom_option,
    create_customer_profile,
    create_or_update_seller_profile,
    create_sales_agent_email_account,
    disable_sales_agent_email_account,
    discover_companies,
    generate_lead_report_share,
    generate_outreach_draft,
    get_outreach_context_with_structured,
    get_public_report_share,
    get_report_summary,
    get_sales_agent_email_settings,
    list_brands,
    list_option_catalog,
    list_sales_agent_email_accounts,
    create_sales_agent_sms_account,
    create_sales_agent_sms_template,
    disable_sales_agent_sms_account,
    disable_sales_agent_sms_template,
    get_sales_agent_sms_settings,
    list_sales_agent_sms_accounts,
    _sales_agent_public_base_url,
    apply_sales_agent_sms_callback,
    list_sales_agent_sms_deliveries,
    list_sales_agent_sms_templates,
    sales_agent_sms_stats,
    test_sales_agent_sms_account,
    track_sms_short_link,
    update_sales_agent_sms_account,
    update_sales_agent_sms_settings,
    update_sales_agent_sms_template,
    list_sales_agent_email_deliveries,
    list_sessions_page,
    load_card_options,
    normalize_report_email_style_key,
    # The "letter type" set of no-report intro letters and the "recipient perspective" of report emails are two separate key spaces.
    NO_REPORT_EMAIL_PROMPT_VERSION,
    NO_REPORT_EMAIL_STYLES,
    IntroBodyStream,
    default_intro_subject,
    intro_stream_from_text,
    no_report_email_style_is_llm,
    no_report_email_style_options,
    normalize_no_report_email_style_key,
    sanitize_intro_subject,
    split_intro_body_paragraphs,
    strip_intro_body_affixes,
    outreach_brand_settings,
    rank_companies,
    recommend_companies_workflow,
    continue_recommend_companies_workflow,
    record_compliance_event,
    record_lead_flow_checkpoint,
    restore_lead_flow_checkpoint,
    report_email_fingerprint,
    report_email_metadata,
    report_capability_denial,
    report_email_style_options,
    remove_agent_profile_option,
    resolve_active_brand_id,
    running_report_progress,
    arm_send_schedule,
    cancel_send_schedule,
    drop_send_schedule_items,
    evaluate_send_candidates,
    get_send_schedule_detail,
    list_send_schedules,
    plan_send_schedule,
    resolve_batch_lead_ids,
    session_recommendation_batches,
    reschedule_send_schedule,
    sales_agent_email_stats,
    agent_delivery_status,
    agent_recommended_companies,
    seller_profile_for_lead_entry,
    serialize_draft,
    session_bundle,
    start_detection_report,
    suggest_profile_options,
    sync_brand_from_topic,
    test_sales_agent_email_account,
    topic_for_brand,
    track_sales_agent_email_open,
    update_custom_option,
    update_lead_status,
    update_sales_agent_email_account,
    update_sales_agent_email_settings,
    write_agent_updates_to_topic,
)
from agents.geo.auth import get_current_user
from geo.database import SessionLocal, settings
from geo.models.user import UserORM

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales-agent")

_SAFE_SERVICE_ERROR = "Sales Agent 服务暂不可用,请稍后再试。"
# The word list and this fallback sentence moved to `service/common.py` — scheduled sending also needs to sanitize when writing skip_reason
# in the scheduler process, and that path doesn't go through api.py. The old name stays here; the value is the same.
_SAFE_RUNTIME_ERROR = SAFE_RUNTIME_ERROR
_TRANSPARENT_PIXEL = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x8d\xf7\xb6\x00\x00\x00\x00IEND\xaeB`\x82"
_INTERNAL_ERROR_RE = INTERNAL_ERROR_RE

CARD_TOOLS = {
    "generate_detection_report",
    "summarize_report",
    "build_customer_profile",
    "discover_companies",
    "rank_companies",
    "recommend_companies",
    # History lookups also produce a card: same shape as recommend_companies, directly reusing that preview card with paging/search/details.
    # Without a card the model can only hand-copy a table in the body — in production on 2026-08-23 a list of 100 companies showed only 6 rows plus an ellipsis.
    "get_recommended_companies",
    "get_outreach_copy_context",
    "generate_outreach_copy",
    "update_lead_status",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _public_error_message(error: object, fallback: str = _SAFE_RUNTIME_ERROR) -> str:
    """Error copy shown to users. Those with `public=True` skip the word list; the rest are sanitized as usual.

    Takes the exception object rather than `str(e)` — "can this be shown to users verbatim" is only known at the raise site;
    by the time a string gets here it has lost its origin. See the notes on `SalesAgentError`.
    """
    return public_error_text(error, fallback)


def _http_error(e: SalesAgentError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=_public_error_message(e))


def _request_public_base_url(request: Request) -> str:
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    host = forwarded_host or (request.headers.get("host") or "").split(",", 1)[0].strip()
    if not host:
        return ""
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto or request.url.scheme or "https"
    if scheme not in {"http", "https"}:
        scheme = "https"
    if scheme == "http" and not (host.startswith("localhost") or host.startswith("127.0.0.1") or host.startswith("[::1]")):
        scheme = "https"
    return f"{scheme}://{host}".rstrip("/")


def _request_ip_hash(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    raw = forwarded or (request.client.host if request.client else "")
    if not raw:
        return ""
    material = f"{settings.SECRET_KEY}:{raw}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_staff_user(user: UserORM) -> bool:
    role = getattr(user, "role", None) or ("admin" if getattr(user, "is_admin", False) else "user")
    return role in {"admin", "operator"}


def _require_staff_user(user: UserORM) -> None:
    if not _is_staff_user(user):
        raise HTTPException(status_code=403, detail="权限不足")


_INTENT_CLASSIFIER = None


def _intent_classifier():
    """Built lazily, only once. If it can't be built (missing key, provider failure), return None and use keywords."""
    global _INTENT_CLASSIFIER
    if _INTENT_CLASSIFIER is None:
        try:
            from agents.sales_agent.flow.intent import build_intent_classifier
            _INTENT_CLASSIFIER = build_intent_classifier()
        except Exception:  # noqa: BLE001
            logger.warning("Sales Agent intent classifier build failed; falling back to keywords", exc_info=True)
            return None
    return _INTENT_CLASSIFIER


# ---------------------------------------------------------------- Scheduled sending ①
# High-confidence phrase checks of the rule Guard (design 4.7 ①). All three pre-checks recognize only **phrases**, not single-character verbs —
# a lesson copied from `_LEAD_KW`: relying on generic verbs like 「发」 ("send") inevitably misfires, and misfiring here costs real emails sent.

# 「能不能定时发?」 ("can it send on a schedule?") and 「定时发送多少钱?」 ("how much is scheduled sending?") ask about capability and price, not to send.
# Without filtering them out first, they'd fall into send_schedule_lookup and get 「你当前没有定时任务」 ("you have no scheduled tasks") —
# an irrelevant answer, running right into the "Sales Agent must not give generic errors" rule.
_SEND_CAPABILITY_RE = re.compile(
    r"(能不能|能否|可不可以|可以吗|支持吗|支持不支持|会不会|怎么用|怎么弄|如何使用|多少钱|价格|收费|费用|套餐)"
)

# Send verbs. **Must be phrases**; a bare 「发」 ("send") counts only at the end of a sentence (「明早九点发」 "send at 9 tomorrow morning"),
# because the 「发」 in 「今天发了几封」 ("how many sent today") and 「发送失败」 ("send failed") refers to things already done.
_SEND_VERBS = (
    "发出去", "发出来", "发送出去", "发邮件", "发封邮件", "发这批", "发那批", "发一下",
    "发一批", "发给", "发过去", "开始发", "把邮件发", "全发", "都发", "投递出去", "发出",
)
# Sentence-final bare verbs: 「明早九点发」「三点发吧」「下班前发一下」 ("send at 9 tomorrow", "send at 3", "send before end of day").
# `发了` is not among the optional suffixes — a bare 「昨天发了」 ("sent yesterday") states a result, not an instruction.
_SEND_TAIL_RE = re.compile(r"(?:发送|发)(?:出去|出来)?\s*(?:一下|一批|吧|呗)?\s*[。.!!~]*$")
# 「查一下今天的发送」 ("check today's sends") and 「发送记录」 ("send history") are questions, not instructions — a sentence-final 「发送」 can't always be treated as a verb.
_SEND_LOOKUP_HINT_RE = re.compile(r"(查一下|查查|查询|看一下|看看|列一下|列表|记录|统计|汇总)")
# 「发送这批邮件」 ("send this batch of emails"): the verb takes an object directly, mid-sentence rather than at the end.
# One modifier is allowed before the object (「发跟进邮件」「发触达邮件」 "send follow-up/outreach emails"). Modifiers **must be enumerated**;
# can't write `发.{0,4}邮件` — the wildcard would also count 「今天发了几封邮件」 ("how many emails sent today") as a send instruction,
# which asks about results, and misjudging here costs a batch of emails really sent.
_SEND_MODIFIER = r"(?:这批|那批|这些|那些|全部|所有|剩下的|跟进|触达|外联)"
_SEND_OBJECT_RE = re.compile(rf"(?:发送|发)\s*{_SEND_MODIFIER}?\s*(?:邮件|草稿)|(?:发送|发)\s*{_SEND_MODIFIER}")
# 「把…发了」: an imperative with 「把」 is an instruction, not a question about results.
# 「定时把这批发了」 ("send this batch on schedule") relies entirely on this one — with only `_SEND_TAIL_RE` it would fall to chat.
_SEND_IMPERATIVE_RE = re.compile(r"(把|将).{0,12}(发了|发出|发掉|发走|发完|发给)")

# Plan nouns. Alone they don't count as a query; they need a question/view word (see `_is_send_schedule_query`).
_SCHEDULE_NOUN_RE = re.compile(r"(定时任务|定时发送|发送计划|发送任务|发送安排|排期|待发|已排|排好的)")
# Phrasings that directly ask about results, no question word needed.
_SCHEDULE_QUERY_RE = re.compile(
    r"(发了吗|发了没|发出去了吗|发出去了没|发送了吗|发完了吗|发到哪了|发了几封|发送进度|发送状态|排上了吗|排了吗)"
)
_SCHEDULE_QUERY_MARKER_RE = re.compile(r"(吗|嘛|么|没有|查|看|哪些|多少|几个|几封|什么时候|状态|进度|情况|列表|[?？])")
# Cancel/pause. 「别发了」 ("stop sending") is the most common phrasing in production and must be caught — otherwise the emails really go out.
_SCHEDULE_CANCEL_RE = re.compile(
    r"(别发了|先别发|不要发了|不用发了|取消发送|取消定时|取消排期|取消计划|取消今天|停掉|停止发送|暂停发送|撤销发送|撤回排期|算了.{0,4}别发)"
)


def _is_mass_send_request(message: str) -> bool:
    """「把这批群发一下」 ("mass-send this batch") — crosses the compliance red line, but the effect the user wants is itself allowed.

    It's distinguished from 「帮我爬手机号」 ("scrape phone numbers for me") in order to offer a workable path (see the reply in the compliance branch).
    """
    text = str(message or "").strip().lower()
    if not text:
        return False
    if not any(k in text for k in ("群发", "批量发", "一起发", "统一发", "一次性发", "全部发", "都发给")):
        return False
    return any(k in text for k in ("邮件", "信", "邮箱", "发", "触达", "这批", "名单"))


def _is_send_schedule_query(message: str) -> bool:
    """Asking about plan status: 「三点那批发了吗」 ("did the 3 o'clock batch go out"), 「我有哪些定时任务」 ("what scheduled tasks do I have"). Read-only."""
    text = str(message or "").strip().lower()
    if not text or _SEND_CAPABILITY_RE.search(text):
        return False
    if _SCHEDULE_QUERY_RE.search(text):
        return True
    return bool(_SCHEDULE_NOUN_RE.search(text) and _SCHEDULE_QUERY_MARKER_RE.search(text))


def _is_send_schedule_cancel(message: str) -> bool:
    """Cancel a plan: 「别发了」 ("stop sending"), 「取消今天的发送计划」 ("cancel today's send plan").

    Classified as `send_schedule_lookup` (a read-only intent), because the cancellation itself is done by a tool; routing shouldn't send this sentence
    into the write branch that "really schedules a batch" — the user said 「别发」 ("don't send"), and escalating it to a write goes the opposite way.
    """
    text = str(message or "").strip().lower()
    if not text or _SEND_CAPABILITY_RE.search(text):
        return False
    if not _SCHEDULE_CANCEL_RE.search(text):
        return False
    # 「别发了」 ("stop sending") only counts when related to sending/plans, so 「这个方案别发了」 ("don't send this proposal", i.e. to the customer) isn't caught.
    return bool("发" in text or _SCHEDULE_NOUN_RE.search(text))


def _is_send_schedule_request(message: str) -> bool:
    """Wants to send this batch out: 「下午三点把这批发一下」 ("send this batch at 3pm"), 「定时发」 ("send on schedule"). Really schedules a batch.

    Two conditions **must both hold**:
    - a send verb phrase (not a bare 「发」, except at sentence end);
    - a time expression **∨** a scheduling word.

    A scheduling word alone also counts as a hit, because 「定时把这批发了」 has clear scheduling intent but no time — once caught,
    `schedule_outreach_send` judges it vague and asks for a specific time, which beats letting it go here: letting it go would
    fall back to writing drafts, and the user's 「定时发」 ("send on schedule") would be ignored (last row of design 4.7).

    Note where this check sits: after delivery_lookup / recommendation_lookup —
    「今天发了几封邮件」 ("how many emails sent today") is picked up by email stats first and never reaches here.
    """
    text = str(message or "").strip().lower()
    if not text or _SEND_CAPABILITY_RE.search(text):
        return False
    # Lookup and cancel take priority: 「三点那批发了吗」 ("did the 3 o'clock batch go out") has both a time and a send verb, but isn't asking to send again.
    if _is_send_schedule_query(text) or _is_send_schedule_cancel(text) or _SEND_LOOKUP_HINT_RE.search(text):
        return False
    # Mass sending is a compliance red line, left to the compliance branch to refuse; don't schedule it here in passing.
    if "群发" in text:
        return False
    if not _has_send_verb(text):
        return False
    return has_time_expression(text) or has_schedule_marker(text)


def _has_send_verb(text: str) -> bool:
    """Whether the sentence has a verb phrase meaning "send the emails out". A bare 「发」 counts only at sentence end, see `_SEND_TAIL_RE`."""
    return (
        any(verb in text for verb in _SEND_VERBS)
        or bool(_SEND_TAIL_RE.search(text))
        or bool(_SEND_IMPERATIVE_RE.search(text))
        or bool(_SEND_OBJECT_RE.search(text))
    )


# 「今天发了几封」 ("how many sent today") and 「发送情况怎么样」 ("how's sending going") ask about results, not instructions.
_SEND_QUESTION_MARKER_RE = re.compile(r"(吗|嘛|么|多少|几封|几家|几个|哪些|哪几|没有|情况|记录|状态|进度|明细|列表|[?？])")


def _looks_like_send_command(message: str) -> bool:
    """Sounds like an instruction to "send it out", not a question of "was it sent".

    **Judges only the mood, not the time.** Whether there's a time is decided by `_is_send_schedule_request` (①) and
    `flow/intent.py:_post_check` (②b); this only keeps the stats branch from swallowing the instruction whole:
    「把这批邮件发出去」 ("send this batch of emails out") has no time, so ① correctly doesn't catch it (no plan without a time), but the downgrade target should be
    asking for a specific time or falling back to drafting, not throwing out send statistics.
    """
    text = str(message or "").strip().lower()
    if not text or _SEND_CAPABILITY_RE.search(text):
        return False
    if _is_send_schedule_query(text) or _is_send_schedule_cancel(text) or _SEND_LOOKUP_HINT_RE.search(text):
        return False
    if _SEND_QUESTION_MARKER_RE.search(text):
        return False
    return _has_send_verb(text)


def _rule_guard_intent(message: str) -> tuple[str, str, bool]:
    """Rule Guard: only catches high-confidence entries. Returns (intent, reason, final)."""
    text = str(message or "").strip().lower()
    if not text:
        return "chat", "empty_message", True
    if _is_seller_profile_query(message):
        return "seller_profile_lookup", "seller_profile_query", True
    if _is_update_profile_action(message):
        return "seller_profile_update", "seller_profile_update", True
    if _is_delivery_detail_query(message):
        return "delivery_lookup", "delivery_detail_query", True
    if _is_delivery_count_query(message, "lookup") or _is_email_open_query_text(text) or _is_report_visit_query_text(text):
        return "delivery_lookup", "delivery_status_query", True
    if _is_recommendation_detail_query(message, "lookup") or _is_recommendation_stats_query(message, "lookup"):
        return "recommendation_lookup", "recommendation_history_query", True
    if resolve_detection_action(message) or _is_detection_entry(message) or (_url_from_text(message) and not _is_email_delivery_text(text)):
        return "report_detection", "report_detection_entry", True
    if _is_continue_recommendation_request(message):
        return "lead_recommendation", "continue_recommendation", True
    if resolve_lead_action(message) or _is_find_customer_entry(message):
        return "lead_recommendation", "lead_action", True
    # ① Scheduled sending. Placed before `route_intent()`: in keyword routing 「发」 belongs to outreach,
    # so by that step 「三点把这批发一下」 ("send this batch at 3") would be judged as drafting — the user waits for emails to go out while the system edits copy.
    if _is_send_schedule_query(message):
        return "send_schedule_lookup", "send_schedule_query", True
    if _is_send_schedule_cancel(message):
        return "send_schedule_lookup", "send_schedule_cancel", True
    if _is_send_schedule_request(message):
        return "send_schedule_create", "send_schedule_request", True

    keyword = normalize_intent(route_intent(message))
    if keyword == "profile":
        return "seller_profile_update", "profile_keyword", True
    if keyword == "report":
        return "report_detection", "report_keyword", True
    if keyword == "outreach":
        # ② No longer final. Reaching here means ① didn't hit (no send verb phrase, or no time/scheduling word),
        # but keyword routing recognizes this is about email — whether to draft or send is left to the Router Agent.
        # If the model judges `send_schedule_create` but no time can be extracted from the sentence, ②b forces it back down
        # (`flow/intent.py:_post_check`). **② and ②b must be merged in the same PR**:
        # changing only this hands the "should we send" decision entirely to the model, with no hard gate at all.
        return "outreach_draft", "outreach_keyword", False
    if keyword == "lead":
        return "lead_recommendation", "lead_keyword", True
    if keyword == "lookup":
        explicit_domain = _explicit_stats_domain(message)
        if explicit_domain == "delivery":
            # ②c 「把这批邮件发出去」 ("send this batch of emails out"): 「发出去」 is in both `_LOOKUP_KW` and `_SEND_VERBS`,
            # keyword routing judges lookup, and `_explicit_stats_domain()` judges delivery because of 「邮件」 ("email"),
            # with final=True settling it outright — the user gave a send instruction and got a send-volume stats card, and
            # the Router never even got asked. ① not catching it (no time/scheduling word) is correct;
            # the downgrade target was wrong. Hand it back to the Router to decide "ask for a time / draft / schedule"; ②b guarantees
            # nothing is really scheduled without an extractable time — the gate stays, it just no longer answers an instruction as a query.
            if _looks_like_send_command(message):
                return "outreach_draft", "send_command_without_time", False
            return "delivery_lookup", "explicit_delivery_query", True
        if explicit_domain == "recommendation":
            return "recommendation_lookup", "explicit_recommendation_query", True
        return "lookup", "specific_keyword", True
    if keyword in {"faq", "compliance"}:
        return keyword, "specific_keyword", True
    return keyword, "ambiguous_keyword", False


async def _resolve_message_intent(message: str) -> str:
    """The rule Guard catches high-confidence business first; uncertain lead/chat goes to the Router Agent."""
    from agents.sales_agent.flow.intent import llm_classification_enabled, needs_model_classification

    keyword, guard_reason, guard_final = _rule_guard_intent(message)
    if guard_final:
        write_model_call_log(
            request_id=new_model_call_id(), event="intent_guard", status="ok", scene="intent",
            message=message, extra={"intent": keyword, "reason": guard_reason, "final": True},
        )
        return keyword

    agent = _intent_classifier() if (needs_model_classification(keyword) and llm_classification_enabled()) else None

    async def classify(text: str):
        return (await agent.run(text)).output

    intent, source = await resolve_intent(message, keyword, classify if agent is not None else None)
    write_model_call_log(
        request_id=new_model_call_id(), event="intent_router", status="ok", scene="intent",
        message=message, extra={"guard_intent": keyword, "guard_reason": guard_reason, "final_intent": intent, "source": source},
    )
    return intent


def _state_preamble(deps) -> str:
    """Unconditionally splice the profile and this run's filter conditions into the model input every turn.

    Before this, the profile only lived in the DB and the model had to call a tool to read it, so every new session had to
    reconfirm via the "our profile" card (production, 2026-08-23: users with an existing profile were still asked "who are you" in new sessions).
    The summary is spliced deterministically without calling the model, costing only a few dozen tokens.

    `SALES_AGENT_STATE_PREAMBLE=0` turns it off.
    """
    if os.environ.get("SALES_AGENT_STATE_PREAMBLE", "").strip() == "0":
        return ""
    try:
        from agents.sales_agent.runtime.budget import build_state_digest
        digest = build_state_digest(deps.db, deps.user_id, _lead_flow_state(deps), label="账户已知信息")
    except Exception:  # noqa: BLE001 — if unavailable don't splice it; don't let it block the whole turn
        return ""
    return f"{digest}\n\n" if digest else ""


_GATHER_MAX_ROWS = 8


def _is_report_visit_query_text(text: str) -> bool:
    return "报告" in text and any(k in text for k in ("打开", "查看", "访问", "看了", "点", "点击", "阅读", "读", "多少", "几", "哪些", "记录"))


# Unambiguous words of the email domain: their presence is decisive.
# **Bare 「打开」「点开」「阅读」 ("open", "click open", "read") are not here** — 「打开这家公司的官网」 ("open this company's website") and 「点开看看」 ("click to see") would both hit,
# and `_rule_guard_intent` guards the URL-bearing audit entry with `not _is_email_delivery_text()`,
# so a misjudgment blocks the website audit entirely. These three verbs only count with an email object (`_EMAIL_OPEN_RE`);
# metric words like 「打开率」 ("open rate") and 「阅读情况」 ("read status") are unambiguous on their own and listed here.
_EMAIL_DOMAIN_KW = (
    "邮件", "发送", "发出", "发了", "发过", "触达", "开信", "已读", "退信",
    "打开率", "点开率", "阅读率", "阅读情况",
)
# Open verbs + email object: 「打开了几封信」「点开邮件」「读一下这封」 ("how many emails opened", "open the email", "read this one").
_EMAIL_OPEN_OBJECT_RE = r"(?:打开|点开|阅读|已读|读|开).{0,8}(?:信|邮件|封)"
# Subject first, verb last: 「哪些公司点开了」「有几家阅读过」 ("which companies clicked", "how many read it") — these phrasings don't contain 「邮件」 ("email") at all,
# so the object rule can't catch them. The verb must be followed by a final particle (了/过/没/吗/率...), otherwise
# 「打开这家公司的官网」 ("open this company's website") would be matched in reverse by "公司 + 打开" ("company + open").
_EMAIL_OPEN_SUBJECT_RE = r"(?:信|邮件|封|公司|企业|客户|家|谁|哪些|多少|人).{0,8}(?:打开|点开|阅读|已读)(?:了|过|没|吗|嘛|的|率|情况|$|[,,。;;?？!!])"
_EMAIL_OPEN_RE = re.compile(rf"{_EMAIL_OPEN_OBJECT_RE}|{_EMAIL_OPEN_SUBJECT_RE}")


def _is_email_delivery_text(text: str) -> bool:
    if _is_report_visit_query_text(text):
        return True
    if any(k in text for k in _EMAIL_DOMAIN_KW):
        return True
    # Phrasings like 「今天开了几封信」 ("how many opened today") or 「发出去多少封」 ("how many sent") don't necessarily contain the contiguous words "邮件/开信",
    # but verb + 信/邮件/封 ("letter/email/count") is still an email status query. Only used in a query mood, to avoid ordinary chit-chat slipping in.
    return bool(re.search(r"(开|打开|读|阅读|已读|发|发送|寄).{0,8}(信|邮件|封)", text))


def _is_email_open_query_text(text: str) -> bool:
    return any(k in text for k in ("开信", "已读", "打开率", "点开率", "阅读率", "阅读情况")) or bool(_EMAIL_OPEN_RE.search(text))


def _delivery_query_flags(message: str) -> tuple[bool, bool, bool]:
    text = (message or "").strip().lower()
    if not text:
        return False, False, False
    has_delivery = _is_email_delivery_text(text)
    has_question = any(k in text for k in ("多少", "几", "哪些", "有没有", "有多少", "情况", "记录", "状态", "吗", "嘛", "么", "没"))
    wants_analysis = any(k in text for k in ("分析", "效果", "怎么样", "建议", "怎么跟进", "原因", "优化"))
    return has_delivery, has_question, wants_analysis


def _is_delivery_detail_query(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    # 「邮件功能多少钱」 ("how much is the email feature") is FAQ, not a query about a sent email.
    if any(k in text for k in ("多少钱", "价格", "收费", "费用", "报价", "套餐")):
        return False
    # 「发送计划什么时候执行」 ("when will the send plan run") asks about a schedule **not yet sent**, not details of sent emails.
    # If we don't step aside here, 「什么时候/状态」 ("when/status") in `asks_field` would carry it all the way to delivery_lookup,
    # and the scheduled-send check (①) placed later never gets a turn — showing up as a send record answering a scheduling question.
    if _SCHEDULE_NOUN_RE.search(text) and not any(k in text for k in ("已发", "发过", "发出的", "开信", "退信")):
        return False
    asks_field = any(k in text for k in (
        "标题", "主题", "subject", "内容", "正文", "功能", "说了什么", "写了什么", "发的什么",
        "账号", "发件", "发信账号", "发件邮箱", "从哪个邮箱", "哪个邮箱", "收件", "发给谁", "发送时间", "什么时候", "状态",
    ))
    selector, _offset, _count, _label = _delivery_detail_selector(message)
    ordinal = selector != "latest_default"
    delivery = _is_email_delivery_text(text) or any(k in text for k in ("邮箱", "信件", "邮件", "封", "发出的", "已发", "发过"))
    return (delivery or ordinal) and (asks_field or ordinal)


def _delivery_detail_fields(message: str) -> list[str]:
    text = (message or "").strip().lower()
    fields: list[str] = []
    if any(k in text for k in ("标题", "主题", "subject")):
        fields.append("subject")
    if any(k in text for k in ("内容", "正文", "功能", "说了什么", "写了什么", "发的什么")):
        fields.append("content")
    if any(k in text for k in ("账号", "发件", "发信账号", "发件邮箱", "从哪个邮箱", "哪个邮箱")):
        fields.append("sender")
    if any(k in text for k in ("收件", "发给谁", "发给哪个", "发给了谁", "发给了哪个")):
        fields.append("recipient")
    if any(k in text for k in ("发送时间", "什么时候", "几点", "时间")):
        fields.append("sent_at")
    if "状态" in text or "成功" in text or "失败" in text:
        fields.append("status")
    return list(dict.fromkeys(fields)) or ["summary"]


def _delivery_detail_number(token: str) -> int | None:
    token = str(token or "").strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    table = {
        "一": 1,
        "二": 2,
        "两": 2,
        "俩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if token in table:
        return table[token]
    if token.startswith("十") and len(token) == 2 and token[1:] in table:
        return 10 + table[token[1:]]
    if token.endswith("十") and len(token) == 2 and token[:1] in table:
        return table[token[:1]] * 10
    if "十" in token:
        left, right = token.split("十", 1)
        if (not left or left in table) and (not right or right in table):
            return (table.get(left, 1) * 10) + table.get(right, 0)
    return None


def _delivery_detail_selector(message: str) -> tuple[str, int, int, str]:
    text = (message or "").strip().lower()
    number_token = r"(\d+|[一二两俩三四五六七八九十]{1,3})"
    match = re.search(rf"(?:最后|最近|最新)\s*{number_token}\s*封", text)
    if match:
        count = max(1, min(_delivery_detail_number(match.group(1)) or 1, _GATHER_MAX_ROWS))
        if count == 1:
            return "latest", 0, 1, "最近一封"
        return "latest_many", 0, count, f"最近 {count} 封"
    match = re.search(rf"前\s*{number_token}\s*封", text)
    if match:
        count = max(1, min(_delivery_detail_number(match.group(1)) or 1, _GATHER_MAX_ROWS))
        if count == 1:
            return "index", 0, 1, "第 1 封"
        return "first_many", 0, count, f"前 {count} 封"
    if any(k in text for k in ("最后一封", "最后一封信", "最后一封邮件", "最近一封", "最新一封", "上一封", "刚发的", "刚发送的")):
        return "latest", 0, 1, "最近一封"
    if any(k in text for k in ("第一封", "第 1 封", "第1封", "首封")):
        return "index", 0, 1, "第 1 封"
    if any(k in text for k in ("第二封", "第 2 封", "第2封")):
        return "index", 1, 1, "第 2 封"
    if any(k in text for k in ("第三封", "第 3 封", "第3封")):
        return "index", 2, 1, "第 3 封"
    match = re.search(r"第\s*(\d+)\s*封", text)
    if match:
        idx = max(1, int(match.group(1)))
        return "index", idx - 1, 1, f"第 {idx} 封"
    return "latest_default", 0, 1, "最近一封"


def _format_delivery_time(dt: datetime | None) -> str:
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + " (北京时间)"


def _loads_json_object(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _delivery_detail_content(deps, row: SalesAgentEmailDeliveryORM) -> str:
    meta = _loads_json_object(getattr(row, "metadata_json", "{}"))
    content = str(meta.get("content") or "").strip()
    if not content and row.outreach_log_id:
        draft = deps.db.get(SalesAgentOutreachLogORM, row.outreach_log_id)
        if draft is not None:
            content = str(draft.content or "").strip()
    return content or str(row.content_preview or "").strip()


def _delivery_detail_company_name(deps, row: SalesAgentEmailDeliveryORM) -> str:
    if not row.company_id:
        return ""
    company = deps.db.get(SalesAgentCompanyORM, row.company_id)
    return str(company.name or "").strip() if company is not None else ""


def _delivery_sender_label(row: SalesAgentEmailDeliveryORM) -> str:
    email = str(row.from_email or "").strip()
    name = str(row.from_name or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email


def _delivery_recipient_label(deps, row: SalesAgentEmailDeliveryORM) -> str:
    company = _delivery_detail_company_name(deps, row)
    email = str(row.recipient_email or "").strip()
    name = str(row.recipient_name or "").strip()
    who = company or name
    if who and email:
        return f"{who} <{email}>"
    return who or email


def _delivery_detail_line(deps, row: SalesAgentEmailDeliveryORM, field: str) -> str:
    if field == "subject":
        subject = str(row.subject or "").strip()
        return f"邮件标题:{subject}" if subject else "邮件标题:这条发送记录里没有保存标题。"
    if field == "content":
        content = _delivery_detail_content(deps, row)
        return f"邮件内容:\n{content}" if content else "邮件内容:这条发送记录里没有保存可读取的正文,只查得到发送状态。"
    if field == "sender":
        sender = _delivery_sender_label(row)
        return f"发件账号:{sender}" if sender else "发件账号:这条发送记录里没有保存发件账号/发件邮箱。"
    if field == "recipient":
        recipient = _delivery_recipient_label(deps, row)
        return f"收件人:{recipient}" if recipient else "收件人:这条发送记录里没有保存收件人。"
    if field == "sent_at":
        sent_at = _format_delivery_time(row.sent_at or row.created_at)
        return f"发送时间:{sent_at}" if sent_at else "发送时间:这条发送记录里没有保存发送时间。"
    if field == "status":
        status = str(row.status or "").strip() or "未知"
        failed_reason = str(row.failure_reason or "").strip()
        return f"发送状态:{status}" + (f",原因:{failed_reason}" if failed_reason else "")
    return ""


def _delivery_detail_row_reply(deps, row: SalesAgentEmailDeliveryORM, fields: list[str], label: str) -> str:
    recipient = _delivery_recipient_label(deps, row) or f"记录 #{row.id}"
    status = str(row.status or "").strip() or "未知"
    sent_at = _format_delivery_time(row.sent_at or row.created_at)
    prefix = f"{label}是发给 {recipient} 的记录,状态 {status}"
    if sent_at:
        prefix += f",发送时间 {sent_at}"
    prefix += "。"
    lines = [line for line in (_delivery_detail_line(deps, row, field) for field in fields) if line]
    return "\n".join([prefix] + lines)


def _delivery_detail_reply(deps, message: str) -> str:
    admin_scope = str(getattr(deps, "user_role", "user") or "user") in {"admin", "operator"}
    query = deps.db.query(SalesAgentEmailDeliveryORM)
    if not admin_scope:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == deps.user_id)
    days, since, until, _scope_key, scope_label = _delivery_query_window(message)
    delivery_time = func.coalesce(SalesAgentEmailDeliveryORM.sent_at, SalesAgentEmailDeliveryORM.created_at)
    if since is not None:
        query = query.filter(delivery_time >= since)
    elif days and int(days) > 0:
        query = query.filter(delivery_time >= datetime.utcnow() - timedelta(days=int(days)))
    if until is not None:
        query = query.filter(delivery_time < until)

    selector, offset, count, selector_label = _delivery_detail_selector(message)
    scope = scope_label or ("管理范围" if admin_scope else "当前账号")
    total = query.count()
    if total <= 0:
        return f"{scope}没有找到已发送邮件记录,所以不能确认邮件标题、内容、账号或发送状态。"
    if selector == "index" and offset >= total:
        return f"{scope}只找到 {total} 条邮件发送记录,没有{selector_label}。"

    fields = _delivery_detail_fields(message)
    if fields == ["summary"]:
        fields = ["subject", "sender", "recipient", "sent_at", "status"]

    if selector in {"latest_many", "first_many"}:
        if selector == "first_many":
            rows = query.order_by(delivery_time.asc(), SalesAgentEmailDeliveryORM.id.asc()).limit(count).all()
        else:
            rows = query.order_by(delivery_time.desc(), SalesAgentEmailDeliveryORM.id.desc()).limit(count).all()
        if not rows:
            return f"{scope}没有找到对应的邮件发送记录。"
        shown = len(rows)
        prefix = f"{scope}共找到 {total} 条邮件发送记录,下面展示{selector_label}。"
        if shown < count:
            prefix = f"{scope}只找到 {total} 条邮件发送记录,下面展示这 {shown} 条。"
        blocks = [prefix]
        for idx, row in enumerate(rows, start=1):
            blocks.append(_delivery_detail_row_reply(deps, row, fields, f"{selector_label}中的第 {idx} 封邮件"))
        return "\n\n".join(blocks)

    if selector == "index":
        row = query.order_by(delivery_time.asc(), SalesAgentEmailDeliveryORM.id.asc()).offset(offset).limit(1).first()
    else:
        row = query.order_by(delivery_time.desc(), SalesAgentEmailDeliveryORM.id.desc()).first()
    if row is None:
        return f"{scope}没有找到对应的邮件发送记录。"

    prefix = f"{scope}{selector_label}邮件"
    if selector == "latest_default":
        prefix = "你没有指定第几封,我按最近一封邮件查询。" + prefix
    return _delivery_detail_row_reply(deps, row, fields, prefix)


def _append_delivery_detail_reply(deps, message: str, intent: str) -> bool:
    if not _is_delivery_detail_query(message):
        return False
    try:
        reply = _delivery_detail_reply(deps, message)
    except Exception:  # noqa: BLE001 — read-only query failures must also be stated clearly, not degrade into count stats or a generic failure
        logger.warning("Sales Agent delivery detail query failed", exc_info=True)
        deps.db.rollback()
        reply = "我现在暂时没法读取这封邮件的标题、内容或发件账号。这个查询不会重新发送邮件,你可以稍后再试。"
    append_chat_message(deps, "assistant", reply, intent=intent)
    if isinstance(deps.tool_state, dict):
        _remember_last_stats_query(deps, "delivery", _delivery_query_window(message)[3], "detail", "message")
        _save_lead_flow_state(deps)
    return True


def _is_delivery_status_query(message: str, intent: str) -> bool:
    intent = normalize_intent(intent)
    has_delivery, has_question, wants_analysis = _delivery_query_flags(message)
    if wants_analysis:
        return False
    text = (message or "").strip().lower()
    if intent == "delivery_lookup":
        return has_delivery or _is_email_open_query_text(text) or _is_report_visit_query_text(text)
    if intent == "chat" and (_is_email_open_query_text(text) or _is_report_visit_query_text(text)):
        return False
    if has_delivery and has_question and _should_handle_delivery_deterministically(message, intent):
        return True
    return intent == "lookup" and _is_delivery_count_query(message, intent)


def _is_delivery_company_count_query(message: str) -> bool:
    text = (message or "").strip().lower()
    return bool(re.search(r"(发|发送|投递|触达).{0,10}(几|多少|有多少).{0,6}(家|企业|公司)", text) or re.search(r"(几|多少|有多少).{0,6}(家|企业|公司).{0,10}(发|发送|投递|触达)", text))


def _is_delivery_presence_query(message: str) -> bool:
    text = (message or "").strip().lower()
    return bool(any(k in text for k in ("有没有发送", "有没有发", "是否发送", "是否发", "有发送", "有发过")) and any(k in text for k in ("邮件", "发信", "发送", "发过", "发了")))


def _is_delivery_count_query(message: str, intent: str) -> bool:
    """Deterministically catch email count questions; detail queries continue down the lookup/gather chain."""
    has_delivery, has_question, wants_analysis = _delivery_query_flags(message)
    if wants_analysis or not has_delivery or not has_question:
        return False
    text = (message or "").strip().lower()
    if any(k in text for k in ("哪些", "哪几", "列表", "明细", "记录", "状态", "发了吗", "发了没")):
        return False
    return bool(
        re.search(r"(几|多少|有多少|总共|一共|累计).{0,8}(封|邮件|信)", text)
        or re.search(r"(发|发送|寄).{0,8}(几|多少|有多少).{0,8}(封|邮件|信)", text)
        # A common elliptical phrasing in production: 「今天总共发了多少」 ("how many sent in total today") lacks "邮件/封", but the context is send stats.
        or re.search(r"(今天|昨天|前天|最近|过去|这\d*天|总共|一共|累计).{0,12}(发|发送|寄).{0,8}(几|多少|有多少)$", text)
        or _is_delivery_company_count_query(text)
        or _is_delivery_presence_query(text)
    )


def _should_handle_delivery_deterministically(message: str, intent: str) -> bool:
    intent = normalize_intent(intent)
    if intent == "delivery_lookup":
        return True
    if intent != "lookup":
        return True
    return _is_delivery_count_query(message, intent)


def _explicit_stats_domain(message: str) -> str:
    text = (message or "").strip().lower()
    if not text:
        return ""
    if _is_email_delivery_text(text) or any(k in text for k in ("发信", "发邮件", "发送邮件", "投递", "触达")):
        return "delivery"
    if any(k in text for k in ("推荐", "推荐过", "推了", "推出", "找了", "找到")) and any(k in text for k in ("企业", "公司", "客户", "线索", "家")):
        return "recommendation"
    return ""


def _is_delivery_correction(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(k in text for k in ("我说的是", "不是推荐", "不是推荐企业", "说的是发送", "说的是邮件")) and _explicit_stats_domain(text) == "delivery"


def _is_ambiguous_delivery_query(message: str, intent: str) -> bool:
    coarse_intent = coarse_intent_for(intent)
    has_delivery, has_question, wants_analysis = _delivery_query_flags(message)
    text = (message or "").strip().lower()
    if any(k in text for k in ("发送", "发出", "发了", "发过", "发信", "发邮件", "发送邮件", "投递")):
        return False
    return has_delivery and not has_question and not wants_analysis and coarse_intent not in {"lead", "report", "faq"}


def _bj_day_start_utc(days_ago: int = 0) -> datetime:
    """The UTC moment of "midnight Beijing time on a given day". The implementation moved to `time_parse`; this is just the old name.

    Scheduled sending's daily quota must be cut at the same day boundary, and the `service/` layer can't import api.py — rather than
    rewriting the timezone conversion there, this copy was pushed down for both sides to share. Behavior unchanged by a single character.
    """
    return bj_day_start_utc(days_ago)


def _number_text_to_int(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + digits.get(text[1:], 0)
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(text, 0)


def _delivery_query_days(message: str) -> int:
    text = (message or "").strip().lower()
    match = re.search(r"(?:最近|近|过去|这)\s*([0-9一二两三四五六七八九十]+)\s*天", text)
    if not match:
        match = re.search(r"([0-9一二两三四五六七八九十]+)\s*天(?:内|里|之内|总共|一共|累计)?", text)
    if match:
        days = _number_text_to_int(match.group(1))
        return max(1, min(days, 90)) if days else 0
    return 0


def _explicit_bj_day_from_match(year: str | None, month: str, day: str) -> tuple[datetime, str] | None:
    try:
        bj_now = datetime.utcnow() + timedelta(hours=8)
        y = int(year) if year else bj_now.year
        bj_day = datetime(y, int(month), int(day))
        if year is None and bj_day > bj_now + timedelta(days=1):
            bj_day = datetime(y - 1, int(month), int(day))
        return bj_day - timedelta(hours=8), bj_day.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _explicit_bj_days(message: str) -> list[tuple[datetime, str]]:
    text = (message or "").strip().lower()
    days: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    patterns = (
        r"(?:(20\d{2})[-/.年])\s*(\d{1,2})[-/.月]\s*(\d{1,2})(?:日|号)?",
        r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?",
        r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if len(match.groups()) == 3:
                parsed = _explicit_bj_day_from_match(match.group(1), match.group(2), match.group(3))
            else:
                parsed = _explicit_bj_day_from_match(None, match.group(1), match.group(2))
            if parsed and parsed[1] not in seen:
                seen.add(parsed[1])
                days.append(parsed)
    return days


def _delivery_scope_window(scope_key: str) -> tuple[int, datetime | None, datetime | None, str]:
    scope = str(scope_key or "all").strip().lower()
    if scope == "today":
        return 0, _bj_day_start_utc(0), None, "今天"
    if scope == "yesterday":
        return 0, _bj_day_start_utc(1), _bj_day_start_utc(0), "昨天"
    if scope == "day_before_yesterday":
        return 0, _bj_day_start_utc(2), _bj_day_start_utc(1), "前天"
    if scope == "yesterday_today":
        return 0, _bj_day_start_utc(1), None, "昨天和今天"
    if scope == "day_before_yesterday_yesterday":
        return 0, _bj_day_start_utc(2), _bj_day_start_utc(0), "前天和昨天"
    if scope == "day_before_yesterday_today":
        return 0, _bj_day_start_utc(2), None, "前天到今天"
    if scope.startswith("days:"):
        try:
            days = max(1, min(int(scope.split(":", 1)[1]), 90))
        except (TypeError, ValueError):
            days = 0
        return 0, _bj_day_start_utc(days - 1), None, f"最近 {days} 天" if days else ""
    if scope.startswith("day:"):
        label = scope.split(":", 1)[1]
        try:
            bj_day = datetime.strptime(label, "%Y-%m-%d")
        except ValueError:
            return 0, None, None, ""
        return 0, bj_day - timedelta(hours=8), bj_day + timedelta(days=1, hours=-8), label
    if scope.startswith("range:"):
        parts = scope.split(":")
        if len(parts) == 3:
            try:
                start = datetime.strptime(parts[1], "%Y-%m-%d")
                end = datetime.strptime(parts[2], "%Y-%m-%d")
            except ValueError:
                return 0, None, None, ""
            if end < start:
                start, end = end, start
            return 0, start - timedelta(hours=8), end + timedelta(days=1, hours=-8), f"{start:%Y-%m-%d} 到 {end:%Y-%m-%d}"
    return 0, None, None, ""


def _delivery_query_window(message: str) -> tuple[int, datetime | None, datetime | None, str, str]:
    """Email stats time window. Returns days/since/until/scope_key/scope_label."""
    text = (message or "").strip().lower()
    explicit_days = _explicit_bj_days(text)
    if len(explicit_days) >= 2 and any(k in text for k in ("到", "至", "-", "~", "—", "一共", "总共", "累计")):
        labels = [label for _, label in explicit_days[:2]]
        days, since, until, label = _delivery_scope_window(f"range:{labels[0]}:{labels[1]}")
        return days, since, until, f"range:{labels[0]}:{labels[1]}", label
    if explicit_days:
        label = explicit_days[0][1]
        days, since, until, scope_label = _delivery_scope_window(f"day:{label}")
        return days, since, until, f"day:{label}", scope_label
    relatives = []
    if any(k in text for k in ("今天", "今日", "当天")):
        relatives.append(0)
    if any(k in text for k in ("昨天", "昨日")):
        relatives.append(1)
    if any(k in text for k in ("前天", "前日")):
        relatives.append(2)
    relative_set = set(relatives)
    if {0, 1, 2}.issubset(relative_set):
        return 0, _bj_day_start_utc(2), None, "day_before_yesterday_today", "前天到今天"
    if {0, 1}.issubset(relative_set):
        return 0, _bj_day_start_utc(1), None, "yesterday_today", "昨天和今天"
    if {1, 2}.issubset(relative_set):
        return 0, _bj_day_start_utc(2), _bj_day_start_utc(0), "day_before_yesterday_yesterday", "前天和昨天"
    if 2 in relative_set:
        return 0, _bj_day_start_utc(2), _bj_day_start_utc(1), "day_before_yesterday", "前天"
    if 1 in relative_set:
        return 0, _bj_day_start_utc(1), _bj_day_start_utc(0), "yesterday", "昨天"
    if 0 in relative_set:
        return 0, _bj_day_start_utc(0), None, "today", "今天"
    days = _delivery_query_days(message)
    if days:
        scoped_days, since, until, scope_label = _delivery_scope_window(f"days:{days}")
        return scoped_days, since, until, f"days:{days}", scope_label
    return 0, None, None, "all", ""


def _delivery_query_since(message: str) -> datetime | None:
    return _delivery_query_window(message)[1]


def _needs_specific_delivery_day(message: str) -> bool:
    text = (message or "").strip().lower()
    if not any(k in text for k in ("某一天", "某天", "哪一天", "哪天", "那一天", "那天")):
        return False
    if _explicit_bj_days(text):
        return False
    if any(k in text for k in ("今天", "今日", "当天", "昨天", "昨日", "前天", "前日")):
        return False
    return True


def _delivery_followup_scope_from_message(message: str) -> str:
    if _needs_specific_delivery_day(message):
        return "need_specific_day"
    days, _since, _until, scope_key, label = _delivery_query_window(message)
    if days or label or scope_key not in {"all", ""}:
        return scope_key
    return ""


def _last_stats_query(deps) -> dict[str, Any]:
    if not isinstance(getattr(deps, "tool_state", None), dict):
        return {}
    state = deps.tool_state.get("last_stats_query")
    return state if isinstance(state, dict) else {}


def _remember_last_stats_query(deps, domain: str, scope: str, mode: str = "count", kind: str = "") -> None:
    if not isinstance(getattr(deps, "tool_state", None), dict):
        return
    deps.tool_state["last_stats_query"] = {"domain": domain, "scope": scope, "mode": mode, "kind": kind}


def _stats_scope_label(scope_key: str) -> str:
    label = _delivery_scope_window(scope_key)[3]
    return label or "全部历史"


def _is_stats_scope_confirmation_query(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    has_confirm = any(k in text for k in ("是", "对", "是不是", "对不对", "算", "口径", "包含")) and any(k in text for k in ("?", "？", "吗", "么"))
    has_scope = any(k in text for k in ("今天", "昨天", "前天", "这", "最近", "近", "过去", "天", "时间", "发送时间", "创建时间", "统计", "口径"))
    return has_confirm and has_scope


def _append_stats_scope_confirmation_reply(deps, message: str, intent: str) -> bool:
    if not _is_stats_scope_confirmation_query(message):
        return False
    last_stats = _last_stats_query(deps)
    if not last_stats.get("domain"):
        append_chat_message(
            deps,
            "assistant",
            "我还没有上一条可以确认的统计口径。你可以直接说“查今天发了几封邮件”“查最近 3 天发了多少封邮件”或“查今天推荐了哪些企业”。",
            intent=intent,
        )
        return True
    last_scope = str(last_stats.get("scope") or "all")
    asked_scope = _delivery_followup_scope_from_message(message)
    last_label = _stats_scope_label(last_scope)
    if asked_scope and asked_scope != "need_specific_day" and asked_scope != last_scope:
        asked_label = _stats_scope_label(asked_scope)
        reply = f"不是,上一条统计口径是{last_label},不是{asked_label}。如果你要按{asked_label}重新查,可以直接说“查{asked_label}发了多少封邮件”或“查{asked_label}推荐了哪些企业”。"
    elif last_stats.get("domain") == "delivery":
        kind = str(last_stats.get("kind") or "sent")
        kind_label = "报告访问" if kind == "report_visit" else "开信/阅读" if kind == "open" else "发送失败" if kind == "failed" else "邮件发送"
        reply = f"是的,上一条查的是{last_label}的{kind_label}统计。日期按北京时间自然日计算;邮件发送优先按实际发送时间统计,没有实际发送时间时才用记录创建时间兜底。"
    else:
        mode = "名单" if str(last_stats.get("mode") or "count") == "detail" else "数量"
        reply = f"是的,上一条查的是{last_label}的推荐企业{mode},来源是已保存的推荐历史,不会重新推荐一批企业。"
    append_chat_message(deps, "assistant", reply, intent=intent)
    return True


def _is_all_time_correction(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(k in text for k in ("不是今天", "不看今天", "不是当天", "全部", "所有", "总共", "累计", "历史"))


def _delivery_followup_message(kind: str, scope: str = "", mode: str = "detail") -> str:
    if mode == "count":
        if kind == "report_visit":
            target = "报告被访问了几次"
        elif kind == "open":
            target = "有几封邮件被开信/阅读"
        elif kind == "failed":
            target = "失败了几封邮件"
        else:
            target = "发了几封邮件"
    elif kind == "report_visit":
        target = "哪些企业访问过报告"
    elif kind == "open":
        target = "哪些企业开信/阅读了邮件"
    elif kind == "failed":
        target = "哪些邮件发送失败"
    else:
        target = "哪些企业发送过邮件"
    label = _delivery_scope_window(scope)[3]
    if label:
        return f"{label}{target}"
    return target


def _delivery_query_kind(message: str) -> str:
    text = (message or "").strip().lower()
    if _is_report_visit_query_text(text):
        return "report_visit"
    if _is_email_open_query_text(text):
        return "open"
    if any(k in text for k in ("失败", "退信")):
        return "failed"
    return "sent"


# --- Named send queries (「这个邮箱发过邮件吗」 "was this email address sent to", 「某某有限公司发过邮件吗」 "was X Co., Ltd. sent to") ---
#
# These differ from "how many sent today": they have a specific target, and the answer can only come from a **filtered** query.
# In production it used to answer with account-wide aggregates (「管理范围总共尝试发送 950 封邮件」 "950 emails attempted across the managed scope") —
# the number was right, but it didn't answer the question. This only recognizes the target; the actual fetching is left to the model calling tools,
# and after the model answers, `_delivery_target_answer_correction` checks it again against the same DB data.

_DELIVERY_TARGET_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}")
# Only strong company suffixes are recognized. A bare 「公司」 ("company") would also swallow 「哪些公司」「这家公司」 ("which companies", "this company") — those are references, not company names,
# and an ilike on them finds 0 rows, then earnestly answers "never sent".
_DELIVERY_TARGET_COMPANY_RE = re.compile(
    r"([一-龥A-Za-z0-9（）()·&\-]{2,60}?(?:股份有限公司|集团有限公司|有限责任公司|有限公司|集团|工厂|研究院|事务所|医院|学校|银行))"
)
# Verbs/particles/references that often precede company names. The DB uses `ilike '%name%'` substring matching, so cutting one or two extra characters still hits,
# while cutting too little misses (querying the whole 「给信阳市…」 ("to Xinyang City...") string returns 0 rows), so cut aggressively here.
_DELIVERY_TARGET_PREFIX_NOISE = "给向对帮查问看跟是的了过请把在从我你他它这那该家们些哪一下就都还再吧呢啊呀和与咨询确认知道想"
_DELIVERY_TARGET_PRONOUNS = ("我们", "咱们", "这家", "那家", "哪家", "哪些", "这些", "那些", "贵公司", "本公司", "你们")
# If only a legal-form word remains after cutting the prefix (「那家有限公司发过吗」 -> 「有限公司」 "Co., Ltd."), it's generic, not a specific company,
# and an ilike on it would hit every company in the DB, worse than not answering.
_DELIVERY_TARGET_BARE_NAMES = frozenset({
    "股份有限公司", "集团有限公司", "有限责任公司", "有限公司", "公司", "集团", "工厂", "研究院", "事务所", "医院", "学校", "银行",
})
# Phrasings asking "was it sent". Without this layer, "帮我给某某有限公司写封邮件好吗" ("could you write an email to X Co., Ltd.") would also be treated as a status query.
_DELIVERY_TARGET_ASK_TOKENS = (
    "发过", "发没发", "没发", "有没有发", "发了吗", "发了没", "是否发", "发送过", "发送记录", "触达过",
    "发了几", "发了多少", "开信", "打开", "点开", "阅读", "已读", "报告访问", "记录", "状态", "多少", "几封",
)
_DELIVERY_TARGET_ACTION_TOKENS = ("写一封", "写封", "起草", "草稿", "生成邮件", "发一封", "帮我发", "现在发", "立刻发", "马上发")


def _delivery_query_target(message: str) -> tuple[str, str]:
    """Extract the asked-about target from 「xxx@x.cn 这个邮箱发过邮件吗」 or 「某某有限公司发过邮件吗」 ("was this address / X Co., Ltd. sent to").

    Returns (kind, target): kind is "email" / "company"; ("", "") when no target is recognized.
    """
    text = (message or "").strip()
    if not text:
        return "", ""
    email = _DELIVERY_TARGET_EMAIL_RE.search(text)
    if email:
        return "email", email.group(0)
    match = _DELIVERY_TARGET_COMPANY_RE.search(text)
    if not match:
        return "", ""
    name = match.group(1).strip()
    while name and name[0] in _DELIVERY_TARGET_PREFIX_NOISE:
        name = name[1:]
    name = name.strip()
    if len(name) < 4 or name in _DELIVERY_TARGET_BARE_NAMES or name.startswith(_DELIVERY_TARGET_PRONOUNS):
        return "", ""
    return "company", name


def _delivery_target_query(message: str, intent: str = "") -> tuple[str, str]:
    """Whether this sentence is a named send-status query. If so return (kind, target), otherwise ("", "")."""
    text = (message or "").strip().lower()
    if not text:
        return "", ""
    _has_delivery, _has_question, wants_analysis = _delivery_query_flags(message)
    if wants_analysis or any(k in text for k in _DELIVERY_TARGET_ACTION_TOKENS):
        return "", ""
    if not _is_email_delivery_text(text) or not any(k in text for k in _DELIVERY_TARGET_ASK_TOKENS):
        return "", ""
    return _delivery_query_target(message)


def _delivery_target_context(db, kind: str, target: str) -> dict[str, Any]:
    """Whether the target exists in the company DB.

    "Never sent" and "this company / email isn't in the DB at all" are two different conclusions and must be stated separately —
    replying only "no send records" leaves users unable to tell whether to go send, or first import the company/contact.
    When the lookup fails, return known=None so the caller omits this explanation, without dragging down the whole answer.
    """
    like = f"%{(target or '').strip()}%"
    try:
        if kind == "email":
            contact = (
                db.query(SalesAgentCompanyContactORM)
                .filter(SalesAgentCompanyContactORM.email.ilike(like))
                .order_by(SalesAgentCompanyContactORM.id.desc())
                .first()
            )
            if contact is None:
                return {"known": False, "company_name": ""}
            company = db.get(SalesAgentCompanyORM, contact.company_id) if contact.company_id else None
            return {"known": True, "company_name": company.name if company is not None else ""}
        names = [
            str(row[0])
            for row in db.query(SalesAgentCompanyORM.name).filter(SalesAgentCompanyORM.name.ilike(like)).limit(3).all()
        ]
        return {"known": bool(names), "company_name": names[0] if names else "", "names": names}
    except Exception:  # noqa: BLE001 — this is just an explanatory supplement; skip it if the lookup fails
        logger.warning("Sales Agent company DB lookup for the named target failed", exc_info=True)
        # On Postgres a failed statement leaves the transaction aborted; without a rollback every later read-only query fails too.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"known": None, "company_name": ""}


def _delivery_target_scope_note(scope_label: str) -> str:
    return f"(统计范围:{scope_label})" if scope_label else "(已按全部历史记录查)"


_COMPANY_REGION_PREFIX_RE = re.compile(r"^[一-龥]{2,4}?(?:省|自治区|自治州|地区|市|县|区)")
_COMPANY_LEGAL_SUFFIX_RE = re.compile(r"(?:股份有限公司|集团有限公司|有限责任公司|有限公司|集团|公司)$")


def _company_core_name(name: str) -> str:
    """「信阳市嘉欣电子科技有限公司」 -> 「嘉欣电子科技」: the core name with the administrative-region prefix and legal-form suffix removed."""
    core = _COMPANY_LEGAL_SUFFIX_RE.sub("", _COMPANY_REGION_PREFIX_RE.sub("", (name or "").strip())).strip()
    return core if len(core) >= 4 else ""


def _delivery_target_lookup(deps, kind: str, target: str, message: str, limit: int = 20) -> tuple[dict[str, Any], str, str]:
    """Fetch send records by target; returns (data, core name used for fuzzy matching, time range copy).

    When the full name matches nothing, retry once with the core name — the name in the user's mouth often differs a bit from the DB record
    (an extra/missing 「市」 ("city"), with or without 「(深圳)」 ("(Shenzhen)")), and asserting "never sent" because of spelling differences is the worst mistake:
    it looks like a definitive factual answer. If the fallback hits, the answer must say it matched by core name, not pretend the full name matched.
    """
    admin = str(getattr(deps, "user_role", "user") or "user") in {"admin", "operator"}
    days, since, until, _scope_key, scope_label = _delivery_query_window(message)

    def _query(name: str) -> dict[str, Any]:
        return agent_delivery_status(
            deps.db, deps.user_id, company_name=name, admin=admin,
            days=days, limit=limit, since=since, until=until,
        )

    data = _query(target)
    alias = ""
    if kind == "company" and int(data.get("total_matched") or 0) <= 0:
        core = _company_core_name(target)
        if core and core != target:
            fuzzy = _query(core)
            if int(fuzzy.get("total_matched") or 0) > 0:
                data, alias = fuzzy, core
    return data, alias, scope_label


def _delivery_target_next_step(kind: str, target: str, context: dict[str, Any]) -> str:
    """The next step after confirming nothing was sent.

    Replying only "no send records" leaves the user stranded: whether to draft next, add contact details, or ask again with a different name
    is all guesswork. This gives a ready-to-say sentence based on "whether this target is in the DB".
    """
    known = context.get("known") if isinstance(context, dict) else None
    owner = str((context or {}).get("company_name") or "") if isinstance(context, dict) else ""
    # Only one layer of quotes: these sentences are meant for users to copy; nesting like 「给「X」生成一封跟进邮件」 ("generate a follow-up email for 'X'") reads awkwardly.
    if known is True:
        subject = owner or target
        return f"要发的话直接说「给{subject}生成一封跟进邮件」,我会先取它的联系人再起草,缺联系方式会先说明。"
    if known is False:
        if kind == "email":
            return "如果确定发过,换成公司名或只留域名再问一次;要新发就先说「帮我找一下这家企业的联系方式」,把联系人补进企业库再起草。"
        core = _company_core_name(target)
        retry = f"换成核心词「{core}」再问一次也行;" if core else ""
        return f"{retry}要新发就先说「帮我找一下{target}的联系方式」把它补进企业库,或者说「推荐一批同类企业」让我按条件找。"
    return ""


def _delivery_target_reply(
    kind: str, target: str, data: dict[str, Any], scope_label: str, context: dict[str, Any], alias: str = "",
) -> str:
    """Deterministic answer for named queries. Overrides the model when it answers wrong, and also serves as the fallback when fetching fails.

    Non-empty `alias` means this data came from a fuzzy core-name fallback and must be stated in the answer — treating a fuzzy hit
    as a full-name hit pins "another same-named company's send records" on the company the user asked about.
    """
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    deliveries = data.get("deliveries") or []
    sent = int(summary.get("sent") or 0)
    failed = int(summary.get("failed") or 0)
    attempted = int(summary.get("attempted") or (sent + failed))
    opened = int(summary.get("opened") or 0)
    open_count = int(summary.get("open_count") or 0)
    report_visit_count = int(summary.get("report_visit_count") or 0)
    clicked = int(summary.get("clicked") or 0)
    _noun, _verb, cnt_unit, channel_split = _delivery_channel_wording(summary)
    # In Chinese typesetting Latin letters get spaces on both sides, Chinese title marks don't — emails and company names each use their own style.
    subject_head = f"{target} " if kind == "email" else f"「{target}」"
    subject_in = f" {target} " if kind == "email" else f"「{target}」"
    note = _delivery_target_scope_note(scope_label)
    known = context.get("known") if isinstance(context, dict) else None

    if attempted <= 0:
        line = f"没有查到向{subject_in}发送邮件或短信的记录{note}。"
        if kind == "email":
            if known is True:
                owner = str((context or {}).get("company_name") or "")
                line += f"这个邮箱在企业库里{'属于「' + owner + '」' if owner else '有联系人记录'},但还没有向它发出过。"
            elif known is False:
                line += "企业库里也没有这个邮箱的联系人 —— 可能是还没采集/导入,或者和库里登记的写法不一致。"
        else:
            if known is True:
                name = str((context or {}).get("company_name") or "")
                line += f"企业库里有这家企业({'库内登记名:' + name if name and name != target else '名称能对上'}),但还没有向它发出过。"
            elif known is False:
                line += "企业库里也没有匹配到这个名称的企业 —— 可能是名称写法和库里登记的不一致,或者它还没进过推荐/触达名单。"
        # When the conclusion is "never sent", always give a next step, otherwise users don't know whether to draft, add contacts, or rephrase and ask again.
        nxt = _delivery_target_next_step(kind, target, context)
        if nxt:
            line += f"\n{nxt}"
        return line

    if alias:
        # On a fuzzy hit the subject becomes the core name: can't write 「「全名」共有 N 条」 ("'full name' has N records"), that would claim someone else's records for the user.
        subject_head = f"「{alias}」"
        line = f"按全名「{target}」没有查到记录,改按核心词模糊匹配到以下记录(可能包含同名/关联主体,请对一下明细里的企业名):\n"
    else:
        line = ""
    line += (
        f"{subject_head}{note}共有 {attempted} {cnt_unit}发送记录{channel_split}:"
        f"成功 {sent} {cnt_unit},失败 {failed} {cnt_unit};开信/阅读 {opened} 封({open_count} 次),报告访问 {report_visit_count} 次"
    )
    if clicked:
        line += f",短信短链点击 {clicked} 条"
    line += "。"
    rows = deliveries[:5]
    details = []
    for item in rows:
        is_sms = item.get("channel") == "sms"
        who = item.get("recipient_email") or item.get("recipient_phone") or item.get("company_name") or f"记录 #{item.get('delivery_id')}"
        status = item.get("status") or "—"
        when = item.get("sent_at") or item.get("failed_at") or "—"
        if is_sms:
            activity = f"短链点击 {item.get('click_count') or 0} 次" if item.get("clicked") else "未点击短链"
        else:
            activity = f"开信 {item.get('open_count') or 0} 次" if item.get("opened") else "未开信"
        reason = f",失败原因 {item.get('failure_reason')}" if item.get("status") == "failed" and item.get("failure_reason") else ""
        details.append(f"- {who}({'短信' if is_sms else '邮件'}):{status},{activity},发送时间 {when}{reason}")
    if details:
        line += f"\n最近 {len(details)} 条:\n" + "\n".join(details)
    return line


def _delivery_target_answer_correction(deps, message: str, intent: str, output_text: str) -> str:
    """Harness loop for named queries: after the model answers, verify again against the same DB data.

    The model may phrase things however it likes, but "was this email/company sent to" is a factual question with a unique answer.
    When verification fails, don't retry or let the model rewrite; override directly with the deterministic conclusion —
    the same approach as `_verified_delivery_status_reply`. An empty string means no change this turn.
    """
    kind, target = _delivery_target_query(message, intent)
    if not target or not str(output_text or "").strip():
        return ""
    try:
        data, alias, scope_label = _delivery_target_lookup(deps, kind, target, message)
        problems = verify_delivery_target_reply(target, output_text, data)
        if not problems:
            return ""
        logger.warning("Sales Agent named send query verification failed; using the deterministic conclusion: %s", "; ".join(problems))
        return _delivery_target_reply(
            kind, target, data, scope_label, _delivery_target_context(deps.db, kind, target), alias,
        )
    except Exception:  # noqa: BLE001 — if verification itself blows up, it mustn't erase the answer the model already gave
        logger.warning("Sales Agent named send query verification errored; not overriding this turn", exc_info=True)
        deps.db.rollback()
        return ""


def _verified_delivery_status_reply(message: str, data: dict[str, Any], kind: str = "") -> str:
    reply = _delivery_status_reply(message, data, kind=kind)
    feedback = verify_delivery_status_reply(message, reply, data, kind=kind)
    if not feedback:
        return reply
    # Harness feedback loop: don't have the model rewrite; converge once more via the deterministic template on the same data.
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    report_visit_count = int(summary.get("report_visit_count") or 0)
    report_visited_companies = int(summary.get("report_visited_companies") or 0)
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    base_scope = "管理范围" if filters.get("admin") else "当前账号"
    if kind == "report_visit" and report_visit_count > 0 and report_visited_companies <= 0:
        return f"{base_scope}有报告访问 {report_visit_count} 次,但这些访问记录暂时没有关联到企业。"
    return _delivery_status_reply(message, data, kind=kind)


def _delivery_channel_split(summary: dict[str, Any]) -> tuple[int, int]:
    """(email attempts, SMS attempts). `agent_delivery_status` carries `summary.by_channel`."""
    by_channel = summary.get("by_channel") if isinstance(summary.get("by_channel"), dict) else {}
    def attempted(name: str) -> int:
        part = by_channel.get(name)
        return int((part or {}).get("attempted") or 0) if isinstance(part, dict) else 0
    return attempted("email"), attempted("sms")


def _channel_split_preamble(summary: dict[str, Any]) -> str:
    """One sentence on channel distribution for the gather context. Takes no space when both channels are 0."""
    email_n, sms_n = _delivery_channel_split(summary)
    if not email_n and not sms_n:
        return ""
    return f"渠道分布:邮件 {email_n} 封、短信 {sms_n} 条(回答必须写明渠道,不要统称为邮件)。"


def _delivery_channel_wording(summary: dict[str, Any]) -> tuple[str, str, str, str]:
    """(noun, verb, measure word, channel distribution suffix) for the answer.

    When only email was sent, keep saying 「封邮件」 ("emails") — the email-only wording is identical character for character to before SMS was added;
    that's the established answer style and needn't change for a new channel. Once there's SMS, switch to the channel-neutral 「条」 ("items") and state the split,
    otherwise users seeing 「发送 12 封邮件」 ("12 emails sent") would think SMS didn't go out, when it was actually 9 emails + 3 SMS.
    """
    email_n, sms_n = _delivery_channel_split(summary)
    if sms_n <= 0:
        return "封邮件", "发送邮件", "封", ""
    if email_n <= 0:
        return "条短信", "发送短信", "条", ""
    return "条触达", "触达", "条", f"(邮件 {email_n} 封、短信 {sms_n} 条)"


def _delivery_status_reply(message: str, data: dict[str, Any], kind: str = "") -> str:
    text = (message or "").strip().lower()
    summary = data.get("summary") or {}
    deliveries = data.get("deliveries") or []
    filters = data.get("filters") or {}
    days = int(filters.get("days") or 0)
    base_scope = "管理范围" if filters.get("admin") else "当前账号"
    _days, _since, _until, _scope_key, parsed_scope = _delivery_query_window(message)
    scope = parsed_scope or (f"最近 {days} 天" if days else base_scope)
    sent = int(summary.get("sent") or 0)
    failed = int(summary.get("failed") or 0)
    attempted = int(summary.get("attempted") or (sent + failed))
    opened = int(summary.get("opened") or 0)
    report_visited = int(summary.get("report_visited") or 0)
    report_visit_count = int(summary.get("report_visit_count") or 0)
    sent_companies = int(summary.get("sent_companies") or 0)
    attempted_companies = int(summary.get("attempted_companies") or sent_companies)
    opened_companies = int(summary.get("opened_companies") or 0)
    report_visited_companies = int(summary.get("report_visited_companies") or 0)
    clicked = int(summary.get("clicked") or 0)
    click_count = int(summary.get("click_count") or 0)
    noun, verb, cnt_unit, channel_split = _delivery_channel_wording(summary)
    wants_report_visit = kind == "report_visit" or _is_report_visit_query_text(text)
    wants_open = (kind == "open" or _is_email_open_query_text(text)) and not wants_report_visit
    wants_detail = any(k in text for k in ("哪些", "哪几", "列表", "明细", "记录"))
    if wants_report_visit:
        if report_visit_count <= 0 and report_visited_companies <= 0:
            line = f"{scope}没有找到报告访问记录。"
        elif report_visited_companies <= 0:
            line = f"{scope}有报告访问 {report_visit_count} 次,但这些访问记录暂时没有关联到企业。"
        else:
            line = f"{scope}有 {report_visited_companies} 家企业打开/访问过报告,报告访问 {report_visit_count} 次。"
    elif wants_open:
        # SMS has no open pixel; the corresponding behavior metric is short-link clicks — the two can't be added, only reported side by side.
        click_line = f";短信短链被点击 {clicked} 条/{click_count} 次" if clicked or click_count else ""
        if opened <= 0 and opened_companies <= 0:
            line = f"{scope}没有找到开信/阅读记录;报告访问记录也是 0{click_line}。"
        else:
            line = f"{scope}有 {opened_companies} 家企业有开信/阅读记录,对应 {opened} 封邮件;报告被访问 {report_visited_companies} 家/{report_visited} 次{click_line}。"
    elif "企业" in text or "公司" in text or "家" in text:
        if attempted <= 0 and sent_companies <= 0:
            line = f"{scope}没有找到邮件/短信发送记录;失败 {failed} 条。"
        else:
            line = f"{scope}共尝试给 {attempted_companies} 家企业{verb},总计 {attempted} {cnt_unit}{channel_split};其中成功 {sent} {cnt_unit}/成功企业 {sent_companies} 家,失败 {failed} {cnt_unit}。"
    else:
        if attempted <= 0:
            line = f"{scope}没有找到邮件/短信发送记录;成功 0 条,失败 {failed} 条,开信/阅读记录 {opened} 封。"
        else:
            line = f"{scope}总共尝试发送 {attempted} {noun}{channel_split},成功 {sent} {cnt_unit},失败 {failed} {cnt_unit};成功发送涉及 {sent_companies} 家企业,尝试发送涉及 {attempted_companies} 家企业;有开信/阅读记录 {opened} 封。"
    if not wants_detail:
        return line
    if wants_report_visit:
        rows = [item for item in deliveries if int(item.get("report_visit_count") or 0) > 0]
    elif wants_open:
        rows = [item for item in deliveries if item.get("opened")]
    else:
        rows = deliveries
    if (wants_open or wants_report_visit) and not rows:
        return line
    shown = rows[:5]
    # With only one channel, don't label it — the sentence above already says whether it's email or SMS; a tag on every row is noise.
    tag_channel = len({("sms" if r.get("channel") == "sms" else "email") for r in shown}) > 1
    details = []
    for item in shown:
        is_sms = item.get("channel") == "sms"
        company = item.get("company_name") or item.get("recipient_email") or item.get("recipient_phone") or f"记录 #{item.get('delivery_id')}"
        status = item.get("status") or "—"
        if wants_report_visit:
            activity_text = f"报告访问 {item.get('report_visit_count') or 0} 次"
        elif is_sms:
            # SMS has no opens, only short-link clicks; writing 「未开信」 ("not opened") would be read as "sent but nobody looked", a different metric definition.
            activity_text = f"短链点击 {item.get('click_count') or 0} 次" if item.get("clicked") else "未点击短链"
        else:
            activity_text = f"开信 {item.get('open_count') or 0} 次" if item.get("opened") else "未开信"
        sent_at = item.get("sent_at") or "—"
        channel_tag = ("(短信)" if is_sms else "(邮件)") if tag_channel else ""
        details.append(f"- {company}{channel_tag}: {status},{activity_text},发送时间 {sent_at}")
    if details:
        line += f"\n下面仅展示最近 {len(shown)} 条明细:\n" + "\n".join(details)
    return line


def _is_recommendation_stats_query(message: str, intent: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    has_recommendation = any(k in text for k in ("推荐", "推荐过", "推了", "推出", "找了", "找到")) and any(k in text for k in ("企业", "公司", "客户", "线索", "家"))
    has_count = any(k in text for k in ("几", "多少", "有多少", "一共", "总共", "累计"))
    wants_new = any(k in text for k in ("再推荐", "重新推荐", "帮我找", "推荐一些", "推荐几家"))
    wants_detail = any(k in text for k in ("哪些", "哪几", "列表", "明细", "记录"))
    if wants_new or wants_detail and not has_count:
        return False
    return has_recommendation and has_count


def _recommendation_stats_reply(message: str, data: dict[str, Any], scope_label: str) -> str:
    total = int(data.get("total_matched") or 0)
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    with_report = int(summary.get("with_report") or 0)
    with_website = int(summary.get("with_website") or 0)
    scope = scope_label or "当前账号"
    if total <= 0:
        return f"{scope}没有找到已推荐企业记录。"
    return f"{scope}已推荐 {total} 家企业;其中 {with_website} 家有官网,{with_report} 家有关联报告。"


def _is_recommendation_detail_query(message: str, intent: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    has_recommendation = any(k in text for k in ("推荐", "推荐过", "推了", "推出", "找了", "找到")) and any(k in text for k in ("企业", "公司", "客户", "线索", "家"))
    wants_detail = any(k in text for k in ("哪些", "哪几", "列表", "明细", "名单", "具体"))
    wants_count = any(k in text for k in ("几", "多少", "有多少", "一共", "总共", "累计"))
    wants_new = any(k in text for k in ("再推荐", "重新推荐", "帮我找", "推荐一些", "推荐几家"))
    return has_recommendation and wants_detail and not wants_new or bool(has_recommendation and wants_count and wants_detail and not wants_new)


def _recommendation_history_reply(data: dict[str, Any], scope_label: str) -> str:
    total = int(data.get("total_matched") or 0)
    returned = int(data.get("returned") or 0)
    scope = scope_label or "当前账号"
    if total <= 0:
        return f"{scope}没有找到已推荐企业记录。"
    suffix = f",下面用卡片展示最近 {returned} 家。" if returned else "。"
    return f"{scope}已推荐 {total} 家企业{suffix}"


def _append_recommendation_history_card(deps, message: str, intent: str) -> bool:
    explicit_domain = _explicit_stats_domain(message)
    last_stats = _last_stats_query(deps)
    followup_scope = _delivery_followup_scope_from_message(message)
    text = (message or "").strip().lower()
    detail_followup = is_detail_followup(text)
    is_followup = bool(detail_followup and not explicit_domain and last_stats.get("domain") == "recommendation")
    if not is_followup and not _is_recommendation_detail_query(message, intent):
        return False
    if followup_scope == "need_specific_day" or _needs_specific_delivery_day(message):
        append_chat_message(
            deps,
            "assistant",
            "可以按某一天查看推荐企业名单,但你需要告诉我要查哪一天,例如“8月28日推荐了哪些企业”或“2026-08-28 推荐了哪些公司”。",
            intent=intent,
        )
        return True
    if is_followup and last_stats.get("scope"):
        scope_key = str(last_stats.get("scope") or "all")
        days, since, until, scope_label = _delivery_scope_window(scope_key)
    else:
        days, since, until, scope_key, scope_label = _delivery_query_window(message)
    try:
        data = agent_recommended_companies(deps.db, deps.user_id, days=days, since=since, until=until, limit=50)
        data["mode"] = "history_readonly"
        data["readonly"] = True
        data["disable_email_actions"] = True
        data["disable_outreach_actions"] = True
        data["history_scope"] = scope_label or "当前账号"
        reply = _recommendation_history_reply(data, scope_label)
        card = build_guidance_card_from_result("get_recommended_companies", card_payload(data))
        append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    except Exception:  # noqa: BLE001 — recommendation history detail query failures must still give a readable reply
        logger.warning("Sales Agent recommendation history query failed", exc_info=True)
        deps.db.rollback()
        append_chat_message(
            deps,
            "assistant",
            "我现在暂时没法读取推荐企业名单,所以不能确认具体哪些企业。你可以稍后再试;如果当前还没有生成过推荐,这里也会没有记录。",
            intent=intent,
        )
    if isinstance(deps.tool_state, dict):
        deps.tool_state.setdefault("recommendation_query", {}).update({"scope": scope_key})
        _remember_last_stats_query(deps, "recommendation", scope_key, "detail")
        _save_lead_flow_state(deps)
    return True


def _verify_recommendation_stats_reply(reply: str, data: dict[str, Any]) -> list[str]:
    try:
        total = int(data.get("total_matched") or 0)
    except (TypeError, ValueError):
        return ["推荐统计结果结构异常"]
    text = str(reply or "")
    if total > 0 and str(total) not in text:
        return [f"推荐统计回答未包含真实数量 {total}"]
    if total <= 0 and "没有找到" not in text:
        return ["推荐统计为 0 时必须明确没有找到记录"]
    return []


def _append_recommendation_stats_reply(deps, message: str, intent: str) -> bool:
    explicit_domain = _explicit_stats_domain(message)
    last_stats = _last_stats_query(deps)
    followup_scope = _delivery_followup_scope_from_message(message)
    is_followup = bool(followup_scope and not explicit_domain and last_stats.get("domain") == "recommendation")
    if not is_followup and not _is_recommendation_stats_query(message, intent):
        return False
    if followup_scope == "need_specific_day" or _needs_specific_delivery_day(message):
        append_chat_message(
            deps,
            "assistant",
            "可以按某一天统计推荐企业数量,但你需要告诉我要查哪一天,例如“8月28日推荐了几家企业”或“2026-08-28 推荐了多少家”。",
            intent=intent,
        )
        return True
    if is_followup and followup_scope:
        days, since, until, scope_label = _delivery_scope_window(followup_scope)
        scope_key = followup_scope
    else:
        days, since, until, scope_key, scope_label = _delivery_query_window(message)
    try:
        data = agent_recommended_companies(deps.db, deps.user_id, days=days, since=since, until=until, limit=20)
        reply = _recommendation_stats_reply(message, data, scope_label)
        if _verify_recommendation_stats_reply(reply, data):
            reply = _recommendation_stats_reply(message, data, scope_label)
    except Exception:  # noqa: BLE001 — recommendation history read-only query failures must still give a readable reply
        logger.warning("Sales Agent recommendation stats query failed", exc_info=True)
        deps.db.rollback()
        reply = "我现在暂时没法读取推荐企业记录,所以不能确认具体数量。你可以稍后再试;如果当前还没有生成过推荐,这里也会没有记录。"
    append_chat_message(deps, "assistant", reply, intent=intent)
    if isinstance(deps.tool_state, dict):
        deps.tool_state.setdefault("recommendation_query", {}).update({"scope": scope_key})
        _remember_last_stats_query(deps, "recommendation", scope_key, "count")
        _save_lead_flow_state(deps)
    return True


def _append_stats_domain_correction_reply(deps, message: str, intent: str) -> bool:
    """Catch metric-definition corrections like "the recommendation list isn't the send list", instead of falling to the model fallback."""
    last_stats = _last_stats_query(deps)
    correction = resolve_stats_domain_correction(message, last_stats)
    if not correction:
        return False
    last_scope = str(last_stats.get("scope") or "all")
    scope_label = _stats_scope_label(last_scope)
    ctype = str(correction.get("type") or "")
    preferred = str(correction.get("preferred_domain") or "")
    if ctype == "stats_domain_separation":
        if last_stats.get("domain") == "delivery":
            reply = f"对,推荐企业和已触达企业是两套数据。刚才的口径是{scope_label}的邮件/短信发送记录;这些企业是实际发出过触达的企业,不是推荐名单。要看推荐名单,可以说“查{scope_label}推荐了哪些企业”。"
        elif last_stats.get("domain") == "recommendation":
            reply = f"对,推荐企业和已触达企业是两套数据。刚才的口径是{scope_label}的推荐记录;这些企业只是被推荐过,不代表已经发出过邮件或短信。要看发送名单,可以说“查{scope_label}发送给了哪些企业”。"
        else:
            reply = "对,推荐企业和已触达企业是两套数据。你可以分别说“查今天推荐了哪些企业”或“查今天发送给了哪些企业”。"
        append_chat_message(deps, "assistant", reply, intent=intent)
        return True

    if preferred == "recommendation":
        days, since, until, scope_label = _delivery_scope_window(last_scope)
        try:
            data = agent_recommended_companies(deps.db, deps.user_id, days=days, since=since, until=until, limit=50)
            data["mode"] = "history_readonly"
            data["readonly"] = True
            data["disable_email_actions"] = True
            data["disable_outreach_actions"] = True
            data["history_scope"] = scope_label or "当前账号"
            reply = "明白,你要看的是推荐企业,不是已触达企业。" + _recommendation_history_reply(data, scope_label)
            card = build_guidance_card_from_result("get_recommended_companies", card_payload(data))
            append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
        except Exception:  # noqa: BLE001
            logger.warning("Sales Agent recommendation correction query failed", exc_info=True)
            deps.db.rollback()
            append_chat_message(deps, "assistant", "明白,你要看的是推荐企业,不是已触达企业。但我现在暂时没法读取推荐名单,你可以稍后再试。", intent=intent)
        if isinstance(deps.tool_state, dict):
            deps.tool_state.setdefault("recommendation_query", {}).update({"scope": last_scope})
            _remember_last_stats_query(deps, "recommendation", last_scope, "detail")
            _save_lead_flow_state(deps)
        return True

    if preferred == "delivery":
        kind = "sent"
        days, since, until, _scope_label = _delivery_scope_window(last_scope)
        state = deps.tool_state.setdefault("delivery_query", {}) if isinstance(deps.tool_state, dict) else {}
        try:
            data = agent_delivery_status(deps.db, deps.user_id, admin=str(getattr(deps, "user_role", "user") or "user") in {"admin", "operator"}, days=days, limit=20, since=since, until=until, activity=kind)
            reply = "明白,你要看的是已触达企业,不是推荐企业。" + _verified_delivery_status_reply(_delivery_followup_message(kind, last_scope, "detail"), data, kind=kind)
        except Exception:  # noqa: BLE001
            logger.warning("Sales Agent delivery correction query failed", exc_info=True)
            deps.db.rollback()
            reply = "明白,你要看的是已触达企业,不是推荐企业。但我现在暂时没法读取发送记录,你可以稍后再试。"
        append_chat_message(deps, "assistant", reply, intent=intent)
        if isinstance(state, dict):
            state.update({"kind": kind, "scope": last_scope, "mode": "detail"})
            _remember_last_stats_query(deps, "delivery", last_scope, "detail", kind)
            _save_lead_flow_state(deps)
        return True

    append_chat_message(deps, "assistant", "推荐企业和已触达企业是两套数据。你想看推荐名单,还是已发出的邮件/短信名单?", intent=intent)
    return True


def _append_delivery_status_reply(deps, message: str, intent: str) -> bool:
    state = deps.tool_state.setdefault("delivery_query", {}) if isinstance(deps.tool_state, dict) else {}
    admin_scope = str(getattr(deps, "user_role", "user") or "user") in {"admin", "operator"}
    # Named queries about a specific email/company always step aside here: these templates are all account-wide, and catching them means
    # answering "was this email sent to" with 「管理范围总共 950 封」 ("950 in total across the managed scope"). Leave it to the model to call tools with filters,
    # and after it answers, `_delivery_target_answer_correction` verifies with the same DB data as the fallback.
    if _delivery_target_query(message, intent)[1]:
        return False
    is_status_query = _is_delivery_status_query(message, intent)
    if (is_status_query or isinstance(state, dict) and state.get("kind")) and _needs_specific_delivery_day(message):
        append_chat_message(
            deps,
            "assistant",
            "可以按某一天统计邮件发送数量,但你需要告诉我要查哪一天,例如“8月28日发了几封邮件”或“2026-08-28 发了多少封”。",
            intent=intent,
        )
        return True
    followup = resolve_delivery_status_followup(message, state) if not is_status_query else None
    fallback_scope = _delivery_followup_scope_from_message(message)
    last_stats = _last_stats_query(deps)
    if followup and last_stats.get("domain") not in {"", None, "delivery"}:
        followup = None
    if not is_status_query and not followup and fallback_scope and fallback_scope != "need_specific_day" and isinstance(state, dict) and state.get("kind") and last_stats.get("domain") in {"", None, "delivery"}:
        followup = {"kind": str(state.get("kind") or "sent"), "scope": fallback_scope}
    if followup:
        kind = followup["kind"]
        scope_key = followup["scope"]
        mode = str(followup.get("mode") or state.get("mode") or "detail") if isinstance(state, dict) else "detail"
        status = "failed" if kind == "failed" else ""
        days, since, until, _scope_label = _delivery_scope_window(scope_key)
        try:
            data = agent_delivery_status(deps.db, deps.user_id, status=status, admin=admin_scope, days=days, limit=20, since=since, until=until, activity=kind)
            reply = _verified_delivery_status_reply(_delivery_followup_message(kind, scope_key, mode), data, kind=kind)
        except Exception:  # noqa: BLE001 — read-only query failures must not surface as the frontend's 「处理未完成」 ("processing incomplete")
            logger.warning("Sales Agent delivery status followup failed", exc_info=True)
            deps.db.rollback()
            reply = "我现在暂时没法读取邮件/短信发送记录,所以不能确认具体数量。你可以稍后再试;如果是还没配置发件箱或短信通道、还没有真实发出过,这里也会没有记录。"
        append_chat_message(deps, "assistant", reply, intent=intent)
        state.update({"kind": kind, "scope": scope_key, "mode": mode})
        _remember_last_stats_query(deps, "delivery", scope_key, mode, kind)
        _save_lead_flow_state(deps)
        return True
    if _is_ambiguous_delivery_query(message, intent):
        append_chat_message(
            deps,
            "assistant",
            "你是想查邮件开信/阅读记录、报告访问记录,还是想查发送记录? 可以直接说“查今天开信”“查报告访问”或“查今天发送了多少家”。",
            intent=intent,
        )
        return True
    if not _is_delivery_status_query(message, intent):
        return False
    days, since, until, scope_key, _scope_label = _delivery_query_window(message)
    kind = _delivery_query_kind(message)
    status = "failed" if kind == "failed" else ""
    try:
        data = agent_delivery_status(deps.db, deps.user_id, status=status, admin=admin_scope, days=days, limit=20, since=since, until=until, activity=kind)
        reply = _verified_delivery_status_reply(message, data, kind=kind)
    except Exception:  # noqa: BLE001 — harness-style fallback: expected read-only query failures don't interrupt the conversation
        logger.warning("Sales Agent delivery status query failed", exc_info=True)
        deps.db.rollback()
        reply = "我现在暂时没法读取邮件/短信发送记录,所以不能确认具体数量。你可以稍后再试;如果是还没配置发件箱或短信通道、还没有真实发出过,这里也会没有记录。"
    append_chat_message(deps, "assistant", reply, intent=intent)
    if isinstance(state, dict):
        mode = "count" if _is_delivery_count_query(message, intent) else "detail"
        state.update({"kind": kind, "scope": scope_key, "mode": mode})
        _remember_last_stats_query(deps, "delivery", scope_key, mode, kind)
        _save_lead_flow_state(deps)
    return True


def _delivery_target_gather_preamble(deps, kind: str, target: str, message: str) -> str:
    """Prefetch send records **filtered by this target** into the context, stating clearly this is a targeted metric definition and totals must not be substituted."""
    try:
        data, alias, scope_label = _delivery_target_lookup(deps, kind, target, message, limit=_GATHER_MAX_ROWS)
        s = data.get("summary") or {}
        label = "这个邮箱" if kind == "email" else "这家企业"
        lines = []
        if alias:
            lines.append(
                f"【名称兜底】按全名「{target}」一条都没查到,下面这份是按核心词「{alias}」模糊匹配的结果。"
                "回答时必须说明是按核心词模糊匹配到的,并提醒用户核对明细里的企业名,不能直接说成全名命中。"
            )
        lines.append(
            f"【{label}({target})的发送记录 —— 已按它过滤,统计范围:{scope_label or '全部历史'}】"
            f"命中 {data.get('total_matched', 0)} 条;其中已发 {s.get('sent', 0)}、失败 {s.get('failed', 0)}、"
            f"被打开 {s.get('opened', 0)}、报告被访问 {s.get('report_visited', 0)}。"
            + _channel_split_preamble(s)
        )
        for d in (data.get("deliveries") or [])[:_GATHER_MAX_ROWS]:
            is_sms = d.get("channel") == "sms"
            if is_sms:
                activity = f"短链点击 {d.get('click_count') or 0} 次" if d.get("clicked") else "未点击短链"
            else:
                activity = f"已开信 {d.get('open_count') or 0} 次" if d.get("opened") else "未开信"
            who = d.get("recipient_email") or d.get("recipient_phone") or d.get("company_name") or f"#{d.get('delivery_id')}"
            lines.append(f"- {who}({'短信' if is_sms else '邮件'}):{d.get('status')},{activity},发送时间 {d.get('sent_at') or '—'}")
        context = _delivery_target_context(deps.db, kind, target)
        if context.get("known") is True:
            owner = str(context.get("company_name") or "")
            if kind == "email":
                detail = f"(归属企业:{owner})" if owner else ""
                lines.append(f"【企业库对照】库里有这个邮箱的联系人{detail}。")
            else:
                detail = f"(库内登记名:{owner})" if owner and owner != target else ""
                lines.append(f"【企业库对照】库里有这家企业{detail}。")
        elif context.get("known") is False:
            missing = "这个邮箱的联系人" if kind == "email" else "这个名称的企业"
            lines.append(f"【企业库对照】企业库里没有匹配到{missing}。")
        lines.append(
            f"这是一个点名问「{target}」的问题:回答只讲这个对象、必须点到它的名字,"
            "不要拿全账号/管理范围的总数充数。上面就是按它过滤后的真实记录,直接用;"
            "命中 0 条就如实说还没有向它发出过,并结合企业库对照说清是「库里有但没发过」还是「库里根本没有这家/这个邮箱」。"
            "要换时间段或换对象再查,调 get_outreach_delivery_status,把对象填进 company_name"
            "(它同时模糊匹配公司名、收件邮箱和手机号)。"
        )
        if int(data.get("total_matched") or 0) <= 0:
            # "Never sent" can't stop at the conclusion: add a next step the user can act on, so they don't have to guess what to say next.
            nxt = _delivery_target_next_step(kind, target, context)
            if nxt:
                lines.append(f"结论是没发过时,末尾补一句可执行的下一步,例如:{nxt}")
        return "\n".join(lines) + "\n\n"
    except Exception:  # noqa: BLE001 — prefetching is a bonus; if it fails don't splice it
        logger.warning("Sales Agent named send query prefetch failed; not splicing this turn", exc_info=True)
        return ""


def _gather_preamble(deps, intent: str, message: str = "") -> str:
    """Gather — prefetch data by intent into the context, rather than waiting for the model to remember to call a tool.

    In the words of 05-Evaluation §8.4: "don't rely on the model 'remembering' that tool — 2026-08-23 proved that more specific
    old prompt rules suppress new tools". After judging lookup, merely handing the turn to the model and relying on prompt
    rule 11 to beg it to call `get_outreach_delivery_status` often didn't happen, so users asking
    「有哪些开信的公司」 ("which companies opened the email") got "no records yet" or even a profile card.

    The lead half (profile + this run's filter conditions) is already spliced in unconditionally each turn by `_state_preamble`;
    this only adds the lookup half. Both functions splice strings deterministically without calling the model.

    **When the question names a specific email/company, the prefetch must filter by it**: otherwise account-wide aggregates get spliced into the context,
    with a note "answer with this directly, don't call tools again", and the model can only answer "950 sent in total" —
    exactly the source of the production "asked whether an email was sent to, got global stats" bug.

    `SALES_AGENT_GATHER=0` turns it off. Fetch failures always return an empty string — prefetching is a bonus and must not block the whole turn.
    """
    if os.environ.get("SALES_AGENT_GATHER", "").strip() == "0":
        return ""
    target_kind, target = _delivery_target_query(message, intent)
    # Even if a named query is routed as chat (「这家公司开信了吗」 "did this company open the email" is), feed in the filtered records,
    # otherwise the model has no data and can only guess.
    if coarse_intent_for(intent) != "lookup" and not target:
        return ""
    if target:
        return _delivery_target_gather_preamble(deps, target_kind, target, message)
    try:
        from agents.sales_agent.service import agent_delivery_status, agent_recommended_companies

        lines: list[str] = []

        sent = agent_delivery_status(deps.db, deps.user_id, limit=_GATHER_MAX_ROWS)
        s = sent.get("summary") or {}
        lines.append(
            f"【触达发送情况】共 {sent.get('total_matched', 0)} 条记录;"
            f"本次列出的 {sent.get('returned', 0)} 条里已发 {s.get('sent', 0)}、"
            f"失败 {s.get('failed', 0)}、被打开 {s.get('opened', 0)}、报告被访问 {s.get('report_visited', 0)}。"
            # The channel split must go into the context, otherwise the model will only talk in terms of 「封邮件」 ("emails") and omit SMS.
            + _channel_split_preamble(s)
        )
        for d in (sent.get("deliveries") or [])[:_GATHER_MAX_ROWS]:
            is_sms = d.get("channel") == "sms"
            if is_sms:
                opened = f"短链点击 {d.get('click_count') or 0} 次" if d.get("clicked") else "未点击短链"
                extra = ""
            else:
                opened = f"已开信 {d.get('open_count') or 0} 次" if d.get("opened") else "未开信"
                extra = f",报告被访问 {d['report_visit_count']} 次" if d.get("report_visit_count") else ""
            who = d.get("company_name") or d.get("recipient_email") or d.get("recipient_phone") or f"#{d.get('delivery_id')}"
            # Context for the model, so every row is labeled with its channel — it can't infer it elsewhere, and a wrong label costs more than verbosity.
            lines.append(f"- {who}({'短信' if is_sms else '邮件'}):{d.get('status')},{opened}{extra},发送时间 {d.get('sent_at') or '—'}")

        rec = agent_recommended_companies(deps.db, deps.user_id, limit=_GATHER_MAX_ROWS)
        lines.append(f"【推荐过的企业】共 {rec.get('total_matched', 0)} 家,以下是最近 {len(rec.get('items') or [])} 家:")
        for c in (rec.get("items") or [])[:_GATHER_MAX_ROWS]:
            lines.append(
                f"- {c.get('company_name')}({c.get('industry') or '行业未知'} / {c.get('region') or '地区未知'}),"
                f"匹配分 {c.get('match_score')},状态 {c.get('status')},推荐于 {c.get('recommended_at') or '—'}"
            )

        lines.append(
            "以上是刚从库里取好的真实记录,直接用它回答;不要再调 get_outreach_delivery_status "
            "或 get_recommended_companies,更不要改用 recommend_companies 去跑一次新的筛选。"
            "需要按公司名 / 状态 / 天数细查时才调那两个只读工具。"
        )
        return "\n".join(lines) + "\n\n"
    except Exception:  # noqa: BLE001 — if unavailable don't splice it; don't let it block the whole turn
        logger.warning("Sales Agent gather prefetch failed; not splicing this turn", exc_info=True)
        return ""


def _page_preamble(pc: dict | None) -> str:
    if not pc:
        return ""
    actions = [str(a) for a in (pc.get("actions") or [])]
    lines = ["【Sales Agent 页面上下文】当前用户在隐藏 Sales Agent 工作台。"]
    describe = pc.get("describe") or pc.get("page")
    if describe:
        lines.append(f"页面:{describe}。")
    snap = pc.get("snapshot")
    if snap:
        lines.append(f"当前工作台数据:{json.dumps(snap, ensure_ascii=False)}。")
    if actions:
        lines.append(f"前端可用动作:{', '.join(actions)}。如需执行页面动作,先用后端工具完成真实数据更新,不要假装操作。")
    return "\n".join(lines) + "\n"


# SSE response headers. The test host's nginx has proxy_buffering off, but switching gateways or adding a CDN in front would
# buffer the whole stream into a "one-shot response" — making streaming pointless. Set the headers ourselves rather than relying on deployment config.
_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _card_key(tool_name: str, content: object) -> str:
    """Card dedup key. SSE pushes while running and again at the end; both places must recognize the same card."""
    return json.dumps({"tool": tool_name, "content": content}, ensure_ascii=False, sort_keys=True, default=str)


def _card_from_tool_return_part(part) -> tuple[str, dict] | None:
    """tool-return part -> (dedup key, card). Returns None for tools that don't produce cards or non-dict content."""
    if getattr(part, "part_kind", "") != "tool-return":
        return None
    tool_name = getattr(part, "tool_name", "")
    if tool_name not in CARD_TOOLS:
        return None
    # Tools in the recommendation chain split their return via ToolReturn: the model only gets the summary (`content`),
    # and the full data for the card is on `metadata` (see runtime/payload.py). Tools that don't split still read content.
    content = getattr(part, "metadata", None)
    if not isinstance(content, dict):
        content = getattr(part, "content", None)
    if not isinstance(content, dict):
        return None
    return _card_key(tool_name, content), build_guidance_card_from_result(tool_name, content)


def _cards_from_agent_result(result) -> list[dict]:
    cards: list[dict] = []
    seen: set[str] = set()
    messages_for_cards = result.new_messages() if hasattr(result, "new_messages") else result.all_messages()
    for m in messages_for_cards:
        for p in getattr(m, "parts", []):
            keyed = _card_from_tool_return_part(p)
            if keyed is None or keyed[0] in seen:
                continue
            seen.add(keyed[0])
            cards.append(keyed[1])
    return cards


def _fallback_output_from_cards(cards: list[dict]) -> str:
    for card in cards:
        data = card.get("data") if isinstance(card, dict) else None
        if not isinstance(data, dict):
            continue
        if data.get("status") == "need_more_info" or data.get("needs_user_input"):
            question_card = data.get("question_card") if isinstance(data.get("question_card"), dict) else {}
            if question_card.get("id") == "seller_profile":
                return "请先确认我方画像。"
            return "\n".join(str(x) for x in (data.get("message"), data.get("next_question")) if x).strip()
        text = str(data.get("markdown_table") or data.get("message") or "").strip()
        if text:
            return text
    return ""


def _strip_model_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|markdown|md)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    return text


_REPORT_EMAIL_ICON_CHARS = "🔥⚠️🚨🔴📊📌✅🚀🔗⏱️⚔️🏗️❗❌"
_REPORT_EMAIL_FORBIDDEN_SUBJECT_RE = re.compile(
    r"(紧急|严重警告|客户正在流失|正在流失|竞品正在截流|最后机会|抢走|抢占|截流|严重|警告|危险信号|风险警示)",
    flags=re.IGNORECASE,
)


def _strip_report_email_icons(text: str) -> str:
    table = {ord(ch): None for ch in _REPORT_EMAIL_ICON_CHARS}
    table[0xFE0F] = None
    return str(text or "").translate(table).strip()


def _default_outreach_subject(context: dict[str, Any], channel: str) -> str:
    if channel != "email":
        return ""
    company = context.get("company") if isinstance(context.get("company"), dict) else {}
    report = context.get("report_context") if isinstance(context.get("report_context"), dict) else {}
    name = str(company.get("name") or report.get("brand_name") or "").strip()
    if name:
        return f"关于{name}在AI搜索中的表现"
    return "关于企业在AI搜索中的表现"


def _styled_outreach_subject(context: dict[str, Any], style_key: str, subject: str = "") -> str:
    return _default_outreach_subject(context, "email")


def _loose_json_value(value: str) -> str:
    text = str(value or "").strip().strip('"')
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\").strip()


_ALLOWED_EMAIL_HTML_TAGS = {
    "a", "b", "br", "div", "em", "h1", "h2", "h3", "hr", "li", "ol", "p", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul"
}
_ALLOWED_EMAIL_HTML_ATTRS = {"href", "target", "rel", "colspan", "rowspan"}


def _sanitize_email_html(html: str) -> str:
    text = str(html or "").strip()
    if not text:
        return ""
    text = re.sub(r"<\s*(script|style|iframe|object|embed|form|input|button)[^>]*>.*?<\s*/\s*\1\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*/?\s*(script|style|iframe|object|embed|form|input|button)[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+on[a-zA-Z]+\s*=\s*[^\s>]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(href\s*=\s*['\"])\s*javascript:[^'\"]*(['\"])", r"\1#\2", text, flags=re.IGNORECASE)

    def clean_tag(match: re.Match[str]) -> str:
        slash = match.group(1) or ""
        tag = (match.group(2) or "").lower()
        attrs = match.group(3) or ""
        if tag not in _ALLOWED_EMAIL_HTML_TAGS:
            return ""
        if slash:
            return f"</{tag}>"
        kept = []
        for attr_match in re.finditer(r"([a-zA-Z:-]+)\s*=\s*(['\"])(.*?)\2", attrs, flags=re.DOTALL):
            name = attr_match.group(1).lower()
            value = attr_match.group(3).strip()
            if name not in _ALLOWED_EMAIL_HTML_ATTRS:
                continue
            if name == "href" and not re.match(r"^(https?://|mailto:|#)", value, flags=re.IGNORECASE):
                continue
            if name == "style" and re.search(r"(url\s*\(|expression\s*\(|javascript:|behavior\s*:)", value, flags=re.IGNORECASE):
                continue
            value = value.replace('"', "&quot;")
            kept.append(f'{name}="{value}"')
        attr_text = f" {' '.join(kept)}" if kept else ""
        return f"<{tag}{attr_text}>"

    return re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>", clean_tag, text)


def _email_html_from_text(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""

    def linkify(raw: str) -> str:
        parts: list[str] = []
        last = 0
        for match in re.finditer(r"https?://[^\s<>()\"']+", raw):
            url = match.group(0).rstrip("，,。；;)）]")
            suffix = match.group(0)[len(url):]
            parts.append(html_lib.escape(raw[last:match.start()]))
            safe_url = html_lib.escape(url, quote=True)
            parts.append(f'<a href="{safe_url}">{html_lib.escape(url)}</a>')
            parts.append(html_lib.escape(suffix))
            last = match.end()
        parts.append(html_lib.escape(raw[last:]))
        return "".join(parts).replace("\n", "<br>")

    blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
    if not blocks:
        blocks = [line.strip() for line in text.splitlines() if line.strip()]
    return _sanitize_email_html("\n".join(f"<p>{linkify(block)}</p>" for block in blocks))


def _strip_extra_report_reply_cta(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    patterns = (
        r"\n*\s*如需我进一步解读[，,]?\s*直接回复[“\"']?报告[”\"']?即可[。.!！]?\s*$",
        r"\n*\s*(?:如需|如果需要|若需|若需要)[^。！？!?\n]{0,90}(?:回复|回信)[^。！？!?\n]{0,30}[“\"']?报告[”\"']?[^。！？!?\n]{0,40}[。！？!?]?\s*$",
    )
    previous = None
    while previous != text:
        previous = text
        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).rstrip()
    return text.strip()


def _strip_extra_report_reply_cta_html(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    block_re = re.compile(
        r"\s*<(p|li)[^>]*>[\s\S]{0,260}?(?:回复|回信)[\s\S]{0,80}?[“\"']?报告[”\"']?[\s\S]{0,120}?</\1>\s*$",
        flags=re.IGNORECASE,
    )
    previous = None
    while previous != text:
        previous = text
        text = block_re.sub("", text).rstrip()
    return text.strip()


def _jsonish_data(text: str) -> dict[str, Any] | None:
    json_text = text
    if not json_text.startswith("{"):
        start = json_text.find("{")
        end = json_text.rfind("}")
        if start >= 0 and end > start:
            json_text = json_text[start:end + 1]
    try:
        data = json.loads(json_text)
    except Exception:  # noqa: BLE001
        data = None
    return data if isinstance(data, dict) else None


def _jsonish_copy(text: str) -> dict[str, str] | None:
    json_text = text
    data = _jsonish_data(json_text)
    if isinstance(data, dict):
        subject = str(data.get("subject") or "").strip()
        content = str(data.get("content") or "").strip()
        html_content = _sanitize_email_html(str(data.get("html_content") or "").strip())
        if content:
            return {"subject": subject, "content": content, "html_content": html_content}
    if not json_text.startswith("{"):
        start = json_text.find("{")
        end = json_text.rfind("}")
        if start >= 0 and end > start:
            json_text = json_text[start:end + 1]
    subject_match = re.search(r'"subject"\s*:\s*"(?P<subject>.*?)"\s*,\s*"content"\s*:', json_text, flags=re.IGNORECASE | re.DOTALL)
    content_match = re.search(r'"content"\s*:\s*"(?P<content>.*)"\s*}\s*$', json_text, flags=re.IGNORECASE | re.DOTALL)
    if content_match:
        return {
            "subject": _loose_json_value(subject_match.group("subject") if subject_match else ""),
            "content": _loose_json_value(content_match.group("content")),
        }
    return None


def _normalize_copy_variant(value: Any, context: dict[str, Any], channel: str) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    subject = str(value.get("subject") or "").strip()
    content = str(value.get("content") or "").strip()
    html_content = _sanitize_email_html(str(value.get("html_content") or "").strip())
    if not content:
        return None
    return {"subject": subject or _default_outreach_subject(context, channel), "content": content, "html_content": html_content}


def _copy_from_model_output(raw: str, context: dict[str, Any], channel: str) -> dict[str, str]:
    return _copies_from_model_output(raw, context, channel, 1)[0]


def _copies_from_model_output(raw: str, context: dict[str, Any], channel: str, variant_count: int = 1) -> list[dict[str, str]]:
    text = _strip_model_fence(raw)
    if not text:
        raise SalesAgentError("触达邮件正文生成失败,请稍后重试。", status_code=502)
    expected = max(1, min(int(variant_count or 1), 6))
    copies: list[dict[str, str]] = []
    data = _jsonish_data(text)
    if isinstance(data, dict):
        raw_variants = data.get("variants")
        if isinstance(raw_variants, list):
            copies.extend(x for x in (_normalize_copy_variant(v, context, channel) for v in raw_variants) if x)
        else:
            one = _normalize_copy_variant(data, context, channel)
            if one:
                copies.append(one)
    if not copies:
        parsed = _jsonish_copy(text)
        if parsed and parsed.get("content"):
            if not parsed.get("subject"):
                parsed["subject"] = _default_outreach_subject(context, channel)
            copies.append(parsed)

    if not copies:
        lines = [line.strip() for line in text.splitlines()]
        subject = ""
        content = text
        for i, line in enumerate(lines[:4]):
            m = re.match(r"^(?:邮件主题|主题|subject)\s*[：:]\s*(.+)$", line, flags=re.IGNORECASE)
            if m:
                subject = m.group(1).strip().strip('"“”')
                content = "\n".join(lines[i + 1:]).strip()
                break
        if not subject and channel == "email" and len(lines) > 1:
            first = lines[0].strip().strip('"“”')
            if first and len(first) <= 100 and (first.startswith("【") or "审计简报" in first or first.startswith("关于")):
                subject = first
                content = "\n".join(lines[1:]).strip()
        content = re.sub(r"^(?:邮件正文|正文|content)\s*[：:]\s*", "", content, flags=re.IGNORECASE).strip()
        if content:
            copies.append({"subject": subject or _default_outreach_subject(context, channel), "content": content})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in copies:
        key = f"{item.get('subject', '')}\n{item.get('content', '')}".strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= expected:
            break
    if not deduped:
        raise SalesAgentError("触达邮件正文生成失败,请稍后重试。", status_code=502)
    return deduped


def _report_email_style_instruction(style_key: str) -> str:
    key = normalize_report_email_style_key(style_key)
    styles = {
        "leijun_pitch": "综合负责人视角:默认发给公司相关负责人。用自然商务语气说明最值得关注的检测发现,自动选择品牌词、品类词、竞品、官网信号中最有解释力的 3-6 个点。",
        "executive_view": "管理层视角:少讲技术细节,重点说明这件事对新客户发现、AI 搜索入口和同行可见度的商业含义。语气克制,不要制造恐慌。",
        "marketing_brand_view": "市场品牌视角:重点说明品牌在 AI 搜索中的呈现是否准确、品牌词与品类词表现差异、第三方权威信号和品牌实体信号。",
        "sales_growth_view": "销售增长视角:重点说明潜在客户让 AI 推荐供应商时是否能发现该公司、真实竞品是否出现、这是否影响新线索入口。不得写客户流失或订单被抢。",
        "digital_ops_view": "数字化官网视角:重点说明官网结构、语义化内容、结构化数据和可供 AI 引用的信息是否有完善空间。用业务语言解释,不要写成技术审计清单。",
    }
    return styles.get(key, styles["leijun_pitch"])


def _outreach_generation_prompt(context: dict[str, Any], channel: str, tone: str, style_key: str = "leijun_pitch", variant_count: int = 1) -> str:
    compact_context = json.dumps(context, ensure_ascii=False, default=str)
    if len(compact_context) > 14000:
        compact_context = compact_context[:14000] + "..."
    count = max(1, min(int(variant_count or 1), 6))
    output_format = '{"subject":"邮件主题","content":"纯文本正文","html_content":"HTML邮件正文"}' if count == 1 else '{"variants":[{"subject":"版本1主题","content":"版本1纯文本正文","html_content":"版本1HTML邮件正文"},{"subject":"版本2主题","content":"版本2纯文本正文","html_content":"版本2HTML邮件正文"}]}'
    variant_rule = "只输出 1 个版本。" if count == 1 else f"输出 {count} 个差异明显的候选版本,放在 variants 数组中;每个版本都要完整包含主题和正文。"
    return f"""
你是 Sales Agent 的报告邮件生成器。请只输出一个 JSON 对象,不要输出 Markdown 或解释。

输出格式:
{output_format}

收件人视角:
{_report_email_style_instruction(style_key)}
{variant_rule}

硬性规则:
- 你是一名专业的 B2B GEO（Generative Engine Optimization）销售顾问。邮件目标不是立即销售服务,也不是强迫预约会议;有真实报告时通过明确、可验证的 AI 搜索曝光问题引导对方查看完整诊断报告链接,没有真实报告时只做克制的初步沟通,不放报告链接。
- 有已完成真实报告时,邮件应像“发现了一个值得注意的数据异常”,不要像广告、营销话术或高压销售邮件;没有真实报告时,只能写基于公开官网和业务场景的初步沟通,不要声称已经检测出数据异常。第一封邮件不要大篇幅介绍自己的公司。
- subject 由系统统一设置为“关于公司名在AI搜索中的表现”这类中性商务标题;你仍需输出 subject 字段,但不要让诊断切入角度改变标题风格。禁止使用“曝光缺口/紧急/严重警告/客户正在流失/竞品正在截流/最后机会/抢走客户/危险信号”等广告化、诊断化或恐吓式标题。
- 正文控制在 350-600 个中文字,段落简短,适合手机阅读。关键发现和最重要的数据可以用加粗表达。不要使用 Emoji,不要用感叹号制造压力。
- 如果 report_context.has_real_detection=false 或 report_context.public_url 为空,下面关于已检测异常、评分、竞品差距和报告链接的规则全部不适用;只能写待进一步诊断确认的方向。
- 首先判断最值得销售的异常,不要机械罗列所有评分。优先级:1) 品牌词有曝光但品类词没有曝光;2) 竞品已经出现而目标企业没有出现或频率明显较低;3) AI 知道企业但在非品牌问题中不主动推荐;4) 官网或权威信号存在明显不足。
- 如果存在“品牌词高、品类词低”,必须用普通商业语言解释:已经知道该品牌的客户可以通过 AI 找到它;但不知道品牌、正在让 AI 推荐供应商的潜在客户,很难发现它。这应成为整封邮件核心。
- 如果目标企业品类词曝光明显低于竞品,只能表达“在相同客户问题下,部分同行已经进入 AI 推荐结果,而目标企业尚未出现/出现频率较低”。不得写“客户已经被竞品抢走”“订单正在流失”“每一次搜索都在把客户送给竞品”。
- 如果品牌词表现正常但非品牌问题表现较差,核心表达为:问题不是“AI知不知道这家公司”,而是“AI在帮助客户选择供应商时,会不会主动推荐这家公司”。
- 官网和权威信号只能作为原因证据,不是核心卖点。解释时只能使用“可能影响”“存在不足”“信号偏弱”“有完善空间”;不得声称“AI不信任该企业”“AI不敢引用”“AI无法验证企业”,除非报告数据直接证明。
- 只选择 3-6 个最有解释力的数据,优先突出品牌词表现、品类词表现、两者之间的 Gap、竞品表现。不要堆砌十几个评分。
- 必须把技术指标翻译成商业语言。例如不要只写“品类词露出率 0%”,还要解释“当潜在客户不知道贵司名称、直接让 AI 推荐相关服务商时,贵司目前没有进入推荐结果”。不要只写权威分,要解释为“AI 能够找到的第三方权威验证和品牌实体信号相对有限”。
- 有 report_context.public_url 时推荐结构:1) 称呼;2) 说明近期对该公司在主流 AI 搜索平台中的品牌可见度做了一次外部检测,马上给出最值得关注的发现;3) 解释核心异常;4) 列 3-6 项核心数据;5) 最多总结 3 个可能原因;6) 如有有效竞品数据,克制说明竞品差距;7) 一句话总结商业问题;8) 使用完整诊断报告链接作为唯一 CTA。没有 report_context.public_url 时,改写成初步沟通结构:称呼、说明关注到对方官网/业务公开信息、说明可进一步做 AI 搜索可见度诊断、结合推荐理由给出 1-3 个待确认方向,不要写数据异常、检测结论或报告链接。
- CTA 根据报告状态决定。如果 report_context.public_url 存在,正文必须包含该报告链接,并以“完整诊断报告：链接”或“查看完整诊断报告：链接”作为唯一主要 CTA。没有 report_context.public_url 时,正文不得出现报告链接或“完整诊断报告”字样,只保留一个克制的软 CTA,例如“如果方便,可以进一步确认是否值得做一次完整诊断”。不要同时出现添加微信、扫码、预约会议、电话联系、回复“报告”、回复索取报告、回复进一步解读等多个 CTA。
- 如果 report_context.public_url 存在,content 和 html_content 都必须包含这个报告链接;html_content 中的报告链接必须用 <a href="..."> 做成可点击链接。没有 report_context.public_url 时,content 和 html_content 都不能包含报告链接。html_content 只能保留同样正文的富文本结构,不得增加新的 CTA。只使用 p/br/strong/em/h2/h3/ul/ol/li/a/table/tr/td/th 等邮件安全标签,不得包含 script/style/form/iframe/button、事件属性、外部追踪像素或 javascript: 链接。
- 称呼必须自然:要带公司名,但不要加联系人姓名或职位;不要把“总经理/市场负责人”等职位硬拼在公司名后面。优先写“公司名相关负责人您好:”或“公司名团队您好:”。
- 只生成草稿,不要声称已经发送或自动群发。以当前登录用户自己的业务口吻写,不要介绍 Vigilath,不要写“这里是 Vigilath/我们是 Vigilath”。
- 只能使用下方上下文中的线索企业、用户服务画像、本次目标筛选条件、推荐理由、公开证据和真实报告上下文。缺失、不确定、矛盾或无法验证的数据不要使用。不得自行补充或猜测检测结果。
- 优先引用 report_context 中真实存在的评分、等级、维度、关键发现、quick wins、竞品和报告事实;缺数据时不要编造百分比、排名、竞品名或诊断结论。
- 没有已完成且属于该企业官网的真实报告时,不得写具体百分比、排名、竞品名、检测分数、等级、llms.txt 缺失、结构化数据不足等诊断结论;只能写“待审计确认”“可进一步量化”。
- email 正文末尾不要添加 AI 生成标识、草稿审批说明、退订/停止联系说明。
- 非 email 渠道必须包含退订/不再联系/停止联系说明。
- 如果缺少收件邮箱,仍然生成可人工补充收件人的草稿,不要把缺少邮箱写进正文。
channel={channel}
tone={tone}
style_key={normalize_report_email_style_key(style_key)}
variant_count={count}
context={compact_context}
""".strip()


async def _generate_outreach_copies_from_context(context: dict[str, Any], channel: str, tone: str, style_key: str = "leijun_pitch", variant_count: int = 1) -> list[dict[str, str]]:
    try:
        from pydantic_ai import Agent
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,无法生成触达草稿。") from e
    agent = Agent(
        get_sales_agent_model("pro"),
        system_prompt="你是严格的 B2B 报告邮件生成器,只输出 JSON。",
        model_settings=get_sales_agent_model_settings("pro"),
    )
    result = await agent.run(_outreach_generation_prompt(context, channel, tone, style_key, variant_count))
    return _copies_from_model_output(str(getattr(result, "output", "") or ""), context, channel, variant_count)


async def _generate_outreach_copy_from_context(context: dict[str, Any], channel: str, tone: str) -> dict[str, str]:
    return (await _generate_outreach_copies_from_context(context, channel, tone, "leijun_pitch", 1))[0]


def _no_report_intro_prompt(context: dict[str, Any], style_key: str, draft_body: str) -> str:
    """Prompt for the no-report intro letter. **This is a rewrite instruction, not a generation instruction.**

    `draft_body` is the middle section assembled from the brand profile; the facts about brand positioning, service capabilities and target scenarios
    are all in it. Having the model rewrite rather than write from scratch lets the factual boundary be a single hard rule (「只能重组
    草稿里已有的事实」 "only reorganize facts already in the draft"), easier to enforce than listing "no made-up cases, no made-up years, no made-up credentials".

    Doesn't reuse `_outreach_generation_prompt`: its sixty-percent-plus of rules are for the report scenario (scores, category term
    gaps, competitors, report link CTA), all dead weight for an intro letter, and they'd tempt the model to write diagnosis conclusions.

    The output format is "first line `SUBJECT:` + blank line + body" rather than JSON, so it can be sent while being written:
    JSON's body field is escaped, so splitting a half-received stream on `\\n\\n` cuts at escape characters, and until the braces
    close nothing parses at all. With plain text the subject arrives first and the body naturally splits on blank lines
    (see `IntroBodyStream`). The non-streaming path still parses it, with an extra JSON fallback.
    """
    key = normalize_no_report_email_style_key(style_key)
    company = context.get("company") if isinstance(context.get("company"), dict) else {}
    brand = context.get("brand_profile") if isinstance(context.get("brand_profile"), dict) else {}
    material = brand.get("email_material") if isinstance(brand.get("email_material"), dict) else {}
    lead = context.get("lead") if isinstance(context.get("lead"), dict) else {}
    brand_name = str(material.get("brand_name") or "").strip() or "我们"
    positioning = str(material.get("core_message") or material.get("brand_slogan") or material.get("core_service_overview") or "").strip()
    redlines = material.get("content_redlines") or []
    redline_text = "；".join(str(x).strip() for x in redlines if str(x).strip()) if isinstance(redlines, list) else str(redlines or "").strip()
    return f"""
你是一位专业的 B2B 商务邮件撰写专家,擅长把复杂的技术产品用简洁、有说服力的商务语言表达出来。

【发信方】{brand_name}——{positioning or "见下方草稿"}
【收件方】{str(company.get('name') or '').strip() or '目标企业'},所属 {str(company.get('industry') or '').strip() or '行业未知'},{str(lead.get('reason') or '').strip()}
【对方认知】对我们所在领域可能有一定了解但不一定深入,用词不能默认对方是内行

以下是系统按品牌资料拼出的正文中段草稿:
---
{draft_body}
---

请按下面的要求改写这段中段。

{NO_REPORT_EMAIL_STYLES[key]["requirements"]}

事实边界(所有类型通用,违反即作废):
- 只能重组、精简、改写上面草稿里已有的事实。草稿里没写的服务能力、案例、客户名、数据、年限、资质,一律不得新增或推测。
- 不得出现检测结论、评分、等级、百分比、排名、竞品名 —— 这是一封没有任何报告支撑的初次沟通信。
- 不得声称已经了解过对方内部情况或检测过对方官网,只能基于公开业务方向推测,用“如果/可能/通常”这类措辞。
- 不得违反以下内容红线:{redline_text or "(未设置)"}
- 以 {brand_name} 自己的口吻写,不得提及 Vigilath,不得声称已经发送或自动群发。

输出格式(只输出下面这两部分,不要 JSON、不要 Markdown、不要任何解释):
第一行写 `SUBJECT: 邮件标题`,然后空一行,接着直接开始正文中段,段落之间用空行分隔。

SUBJECT 控制在 20 个中文字以内,像同行之间的正常来信。不要书名号、感叹号、Emoji,不要“紧急/最后机会/免费/爆单/颠覆/第一/唯一/最好/领先”这类词(除非品牌资料原文就这么写)。

称呼、联系方式、落款由系统统一拼接,你不要写。body 直接从第一句正文开始,不要“XX公司相关负责人您好”,不要任何电话、微信、邮箱、网址,不要“祝好/此致敬礼”。
不要添加 AI 生成标识、草稿审批说明、退订说明。
""".strip()


# The intro letter rewrite has a tighter timeout than `pro`'s default: a timeout here falls back to the standard intro letter, and the user still gets
# a complete, sendable letter, so better degrade early than make people stare at a spinner for the full 90 seconds.
# The two timeout tiers for intro letter streaming copy the shape of the chat streaming path (`agent_chat_stream`):
#
# - `_INTRO_FIRST_EVENT_TIMEOUT` only covers until **the stream emits its first event**. Note: "event", not "body text":
#   when the model thinks before writing, the thinking phase emits `ThinkingPart` / `ThinkingPartDelta` with not a single body
#   character. Timing to "the first body character" would kill a model that's working normally — the production
#   `[25.9s, 0 块]` came from exactly that. Liveness is about whether the stream moves, not whether body text has appeared.
# - `NO_REPORT_INTRO_MODEL_TIMEOUT` is the wall-clock cap for the whole letter, used as a cap only after the model starts producing text.
#   It used to be 45 seconds and applied to **every chunk** — the model writing a 500-600 character letter is slow anyway, and got
#   cut off midway by its own timer, so users saw "not a single character, straight to degradation". pro's own budget is 90 seconds.
_INTRO_FIRST_EVENT_TIMEOUT = 25.0
NO_REPORT_INTRO_MODEL_TIMEOUT = 90.0

# The non-streaming path (`POST /outreach/drafts/auto`) can't be extended to 90 seconds: it's a single blocking request
# with no frames in between, and nginx's `proxy_read_timeout` defaults to 60 seconds, so waiting 90 would just get it cut by
# the gateway first. Only the streaming path, which stays alive by continuously sending frames, can afford to wait longer.
_INTRO_BLOCKING_MODEL_TIMEOUT = 45.0


_INTRO_SUBJECT_LINE_RE = re.compile(r"^\s*(?:subject|标题)\s*[:：]\s*(.*)$", re.IGNORECASE)

_INTRO_SYSTEM_PROMPT = "你是资深 B2B 商务邮件撰写专家,按要求的纯文本格式输出,不要 JSON、不要 Markdown。"


def _parse_intro_model_output(raw_output: str) -> dict[str, str]:
    """Parse the model output of the intro letter rewrite. Returns {subject, body}, neither having passed the gate yet.

    The normal format is "first line `SUBJECT:` + blank line + body". The JSON branch remains because models have inertia:
    after the format change a certain share still emit JSON, and parsing that as plain text would stuff the whole JSON into the letter as the body.
    """
    raw = _strip_model_fence(str(raw_output or "")).strip()
    if raw[:1] in ("{", "["):
        data = _jsonish_data(raw) or {}
        body = str(data.get("body") or data.get("content") or "").strip()
        if body:
            return {"subject": str(data.get("subject") or "").strip(), "body": body}
    head, sep, rest = raw.partition("\n")
    matched = _INTRO_SUBJECT_LINE_RE.match(head.strip())
    if matched:
        return {"subject": matched.group(1).strip(), "body": rest.strip() if sep else ""}
    # The model didn't even write a subject line. Use the whole thing as the body — stripping and the compliance gate still follow,
    # which is closer to what the user meant by clicking this button than failing and degrading to the assembled letter. The subject falls back to the template subject.
    return {"subject": "", "body": raw}


async def _generate_no_report_intro_body(context: dict[str, Any], style_key: str, draft_body: str) -> dict[str, str]:
    """Have the model rewrite the intro letter's middle section. Returns {subject, body}, neither having passed the gate yet."""
    try:
        from pydantic_ai import Agent
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 pydantic-ai,无法生成触达草稿。") from e
    agent = Agent(
        get_sales_agent_model("pro"),
        system_prompt=_INTRO_SYSTEM_PROMPT,
        model_settings=get_sales_agent_model_settings("pro", timeout=_INTRO_BLOCKING_MODEL_TIMEOUT),
    )
    result = await agent.run(_no_report_intro_prompt(context, style_key, draft_body))
    parsed = _parse_intro_model_output(str(getattr(result, "output", "") or ""))
    if not parsed["body"]:
        raise SalesAgentError("介绍信正文生成失败,请稍后重试。", status_code=502)
    return parsed


def _is_find_customer_entry(text: str) -> bool:
    normalized = (text or "").strip().lower().strip(" ，,。.!！?？~～")
    if normalized in {"找客户", "找企业", "推荐企业", "推荐客户", "获取客户", "获取顾客"}:
        return True
    return any(k in normalized for k in ("找客户", "找企业", "推荐企业", "推荐客户", "获取客户", "获取顾客", "客户名单", "销售线索"))


_CONTINUE_RECOMMENDATION_EXACT = {"继续推荐", "继续找客户", "继续找企业", "再推荐一批", "在推荐一批", "再来一批", "下一批", "换一批", "还有吗", "还有么"}
_CONTINUE_RECOMMENDATION_PATTERNS = (
    re.compile(r"^(继续|再|重新|换|下一批|下批).{0,8}(推荐|找客户|找企业|客户|企业|公司|线索)"),
    re.compile(r"^(还有|再来|再给|再找).{0,8}(一批|一些|一点|类似|客户|企业|公司|线索)"),
)


def _is_continue_recommendation_request(text: str) -> bool:
    normalized = _normalized_action_text(text)
    if normalized in _CONTINUE_RECOMMENDATION_EXACT:
        return True
    return any(p.search(normalized) for p in _CONTINUE_RECOMMENDATION_PATTERNS)


def _normalized_action_text(text: str) -> str:
    return (text or "").strip().lower().strip(" ，,。.!！?？~～")


# 「有官网」 ("has an official website") has two distinct meanings in this product, and must be clarified before recommending:
#   1) a filter condition — "only recommend companies with an official website". The recommendation chain has **no** such filter; the website only adds 5 points to the score
#      (recommend.py::_company_evidences).
#   2) an ideal customer profile — 「有官网但搜索获客一般的企业」 ("companies with a website but mediocre search-driven acquisition"), a preset option in catalog.py,
#      and prompts.py states that a user saying this counts as having provided the ideal customer.
# Without clarification it's silently swallowed as 2: 100 companies recommended, the website column all "-", with not a single hint along the way.
_WEBSITE_MENTION_RE = re.compile(r"(有|带|含|只要|需要|必须有|必须带)\s*(官网|网站|官方网站)")
# When these words appear the user is describing the customer's state (profile), not asking for a filter; don't intercept.
_WEBSITE_PROFILE_HINTS = ("但", "可是", "不过", "搜索获客", "获客一般", "转化", "询盘", "排名", "曝光", "搜不到")


def _is_website_scope_request(message: str, intent: str) -> bool:
    text = (message or "").strip()
    if not text or not _WEBSITE_MENTION_RE.search(text):
        return False
    if any(k in text for k in _WEBSITE_PROFILE_HINTS):
        return False
    # A self-description like 「我们公司有官网」 ("our company has a website") isn't a recommendation request; only ask in a find-customers context.
    if coarse_intent_for(normalize_intent(intent)) == "lead":
        return True
    return bool(_is_find_customer_entry(text) or _is_continue_recommendation_request(text) or resolve_lead_action(text))


def _append_website_scope_clarify(deps, flow: dict[str, object], message: str, intent: str, step: str) -> bool:
    """For 「有官网」 ("has an official website"), first clarify whether it's a filter or a profile. Ask only once per session; after the answer, continue with the original flow this turn.

    Returns True = a clarification card was issued and this turn ends here; False = no clarification needed or already answered, hand over to the following flow.
    """
    if flow.get("website_scope_pending"):
        flow.pop("website_scope_pending", None)
        normalized = _normalized_action_text(message)
        if normalized == _normalized_action_text(WEBSITE_SCOPE_ONLY_OPTION):
            flow["website_scope"] = "only_with_website"
        elif normalized == _normalized_action_text(WEBSITE_SCOPE_PROFILE_OPTION):
            flow["website_scope"] = "typical_customer_profile"
        else:
            # They answered something else (changed their mind, gave conditions directly) — don't pester, and don't ask again next turn.
            flow["website_scope"] = "answered"
        _save_lead_flow_state(deps)
        return False
    if step or flow.get("website_scope") or not _is_website_scope_request(message, intent):
        return False
    flow["website_scope_pending"] = True
    _save_lead_flow_state(deps)
    append_chat_message(
        deps, "assistant",
        "先确认一下「有官网」指的是哪个意思,这两种出来的名单不一样。",
        intent=intent, metadata={"tool_cards": [build_website_scope_clarify_card()]},
    )
    return True


def _is_continue_profile_action(text: str) -> bool:
    normalized = _normalized_action_text(text)
    return normalized in {"继续", "确认", "确认选择", "继续找企业", "继续找客户", "下一步"} or any(k in normalized for k in ("继续", "下一步"))


def _is_update_profile_action(text: str) -> bool:
    normalized = _normalized_action_text(text)
    return normalized in {"更新画像", "更新资料", "重新填写", "修改画像", "修改资料"} or any(k in normalized for k in ("更新画像", "更新资料", "修改画像", "修改资料", "重新填写"))


_URL_RE = re.compile(r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s，,；;。]*)?")


def _entry_guidance_seen(deps) -> bool:
    if deps.session_id is None:
        return False
    return deps.db.query(SalesAgentChatMessageORM.id).filter(
        SalesAgentChatMessageORM.session_id == deps.session_id,
        SalesAgentChatMessageORM.role == "assistant",
        SalesAgentChatMessageORM.metadata_json.contains("sales_agent_entry_action"),
    ).first() is not None


def _assistant_message_seen(deps) -> bool:
    if deps.session_id is None:
        return False
    return deps.db.query(SalesAgentChatMessageORM.id).filter(
        SalesAgentChatMessageORM.session_id == deps.session_id,
        SalesAgentChatMessageORM.role == "assistant",
    ).first() is not None


def _append_entry_guidance(deps, intent: str) -> None:
    append_chat_message(deps, "assistant", SALES_AGENT_GUIDE_REPLY, intent=intent, metadata={"tool_cards": [build_entry_guidance_card()]})
    _record_visible_card_checkpoint(deps, "sales_agent_entry_action")


def _assistant_identity_reply() -> str:
    return SALES_AGENT_GUIDE_REPLY


def _seller_identity_reply(deps) -> str:
    brand_id = _active_seller_profile_id(deps)
    try:
        profile = seller_profile_for_lead_entry(deps.db, deps.user_id, brand_id)
    except Exception:  # noqa: BLE001 — the identity fallback mustn't become a generic error because profile reading failed
        profile = {}
    seller = profile.get("seller_profile") if isinstance(profile.get("seller_profile"), dict) else {}
    name = str(seller.get("name") or "").strip()
    capabilities = [str(item).strip() for item in (seller.get("service_capabilities") or []) if str(item).strip()]
    if name and capabilities:
        return f"我能看到当前 Sales Agent 账号正在使用的品牌是{name},已配置能力包括{'、'.join(capabilities[:5])}。我不知道你的真实个人身份;如果你想确认某个品牌或企业,可以把名字发给我。"
    if name:
        return f"我能看到当前 Sales Agent 账号正在使用的品牌是{name}。我不知道你的真实个人身份;如果你想确认某个品牌或企业,可以把名字发给我。"
    return "我能识别当前登录账号下的 Sales Agent 会话和品牌资料,但不知道你的真实个人身份。你可以补充品牌名或企业名,我再按已有资料帮你确认。"


def _reference_question_reply() -> str:
    return "你这句话里的对象还不够明确。请补充企业名、品牌名,或直接说“查今天发送了哪些企业”“查今天推荐了哪些企业”,我再按对应记录帮你查。"


def _casual_chat_reply() -> str:
    return "我不用吃饭喝水,可以一直帮你处理 Sales Agent 里的事。你可以直接问我邮件发送统计、推荐企业名单、官网检测或跟进邮件。"


def _is_seller_profile_query(message: str) -> bool:
    """Recognize questions like “我的画像是什么” ("what's my profile") that read the current Sales Agent's own profile."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if any(k in compact for k in ("我的画像", "我方画像", "用户画像", "销售画像", "账号画像", "品牌画像", "我的icp画像", "我的企业画像", "企业画像")):
        return any(k in compact for k in ("什么", "是啥", "是啥子", "是什么", "看看", "查看", "查询", "有吗", "有么", "呢", "吗")) or len(compact) <= 10
    return False


def _profile_items_text(values: object, limit: int = 6) -> str:
    items = [str(item).strip() for item in (values if isinstance(values, list) else [values]) if str(item).strip()]
    if not items:
        return "未补充"
    suffix = "等" if len(items) > limit else ""
    return "、".join(items[:limit]) + suffix


def _seller_profile_status_reply(deps) -> str:
    if _brand_is_ambiguous(deps):
        return "你账号下有多个品牌,我还不能确定你要看哪一个品牌的我方画像。请先选择品牌,或直接说品牌名后再问画像。"
    try:
        profile = seller_profile_for_lead_entry(deps.db, deps.user_id, _active_seller_profile_id(deps))
    except Exception:  # noqa: BLE001 — profile queries mustn't fall into a generic error
        logger.exception("Sales Agent seller profile query failed")
        return "我现在暂时没法读取当前我方画像,但不是你的问题。你可以稍后再问,或先到客户工作台检查品牌资料是否已保存。"
    brand_name = str(profile.get("brand_name") or "").strip()
    capabilities = profile.get("service_capabilities") or []
    service_scope = profile.get("service_scope") or []
    company_size = str(profile.get("company_size") or "").strip()
    missing = [str(item).strip() for item in (profile.get("missing") or []) if str(item).strip()]
    has_any = bool(capabilities or service_scope or company_size)
    if not has_any:
        return "当前还没有完整的我方画像。你可以先补充品牌、专业能力、服务范围和企业员工规模,后面推荐企业会更准。"
    lines = ["当前我方画像是："]
    if brand_name:
        lines.append(f"- 品牌：{brand_name}")
    lines.append(f"- 专业能力：{_profile_items_text(capabilities)}")
    lines.append(f"- 服务范围：{_profile_items_text(service_scope)}")
    lines.append(f"- 企业规模：{company_size or '未补充'}")
    if missing:
        lines.append(f"- 待补充：{'、'.join(missing)}")
    lines.append("如果要调整,可以说“更新画像”。")
    return "\n".join(lines)


def _append_seller_profile_status_reply(deps, message: str, intent: str) -> bool:
    if normalize_intent(intent) != "seller_profile_lookup" and not _is_seller_profile_query(message):
        return False
    if _brand_is_ambiguous(deps):
        append_chat_message(deps, "assistant", "你账号下有多个品牌,我还不能确定你要看哪一个品牌的我方画像。请先选择品牌,或直接说品牌名后再问画像。", intent=intent)
        return True
    try:
        profile = seller_profile_for_lead_entry(deps.db, deps.user_id, _active_seller_profile_id(deps))
    except Exception:  # noqa: BLE001 — profile queries mustn't fall into a generic error
        logger.exception("Sales Agent seller profile query failed")
        append_chat_message(deps, "assistant", "我现在暂时没法读取当前我方画像,但不是你的问题。你可以稍后再问,或先到客户工作台检查品牌资料是否已保存。", intent=intent)
        return True
    card = build_seller_profile_snapshot_card(profile)
    append_chat_message(
        deps,
        "assistant",
        "这是当前保存的我方画像。如需调整,可以点击更新画像。",
        intent=intent,
        metadata={"tool_cards": [card]},
    )
    return True


def _append_identity_or_reference_reply(deps, message: str, intent: str) -> bool:
    kind = identity_or_reference_kind(message)
    # Capability intros / anthropomorphic chit-chat go to the model for natural replies first; this only catches identity/reference questions needing clear boundaries and no guessing.
    if kind not in {"seller", "reference"}:
        return False
    if kind == "seller":
        reply = _seller_identity_reply(deps)
    else:
        reply = _reference_question_reply()
    append_chat_message(deps, "assistant", reply, intent=intent)
    return True


def _chat_fallback_reply(message: str, intent: str) -> str:
    kind = identity_or_reference_kind(message)
    if kind == "assistant":
        return _assistant_identity_reply()
    if kind == "seller":
        return "我能识别当前登录账号下的 Sales Agent 会话和品牌资料,但不知道你的真实个人身份。你可以补充品牌名或企业名,我再按已有资料帮你确认。"
    if kind == "reference":
        return _reference_question_reply()
    if kind == "casual" or is_casual_chat(message):
        return "我没有真实的吃饭、喝水或抽烟这些行为,但可以继续陪你聊。你也可以直接告诉我想聊什么。"
    if is_low_value_chat(message):
        return "如果你的问题和销售增长无关,我会尽量帮你解答;如果想继续用 Sales Agent,也可以直接说“查今天发了几封邮件”“看推荐企业”或“生成官网检测报告”。"
    if coarse_intent_for(intent) == "faq":
        return "这个问题我这次没能完整回答。你可以换个更具体的说法再问一次;如果涉及价格、权限或具体操作,我会按 Sales Agent 的实际能力说明。"
    return "我这次没能生成完整回复。你可以换个说法再问一次;如果是 Sales Agent 里的业务,也可以直接说要查邮件、看推荐企业或生成官网检测报告。"


# Length cap for the loose check. People who really want a website audit write short sentences (「帮我看看官网,出个诊断报告」 "check my website and give a diagnosis report", 13 characters);
# anything longer is basically **text assembled by the product itself** — card options submitted joined with 「、」,
# a string of a hundred-plus characters, in which both word groups appearing is pure coincidence.
_DETECTION_LOOSE_MAX_CHARS = 40


def _is_detection_entry(text: str) -> bool:
    """Is this sentence asking for a website audit.

    The loose tier ANDs two word groups 「检测/诊断/报告」 ("audit/diagnose/report") × 「官网/网站/网址/url/链接」 ("website/site/URL/url/link"), ignoring distance.
    Production failure on 2026-08-27: in the find-customers flow the user clicked a few card options, and what was submitted was

        "...existing GEO providers both optimize and issue **reports** (报告) on their own results — both athlete
         and referee, ..., companies with an **official website** (官网) but mediocre search-driven acquisition"

    「出具报告」 ("issue reports") is brand pain-point copy, 「有官网但搜索获客一般的企业」 is an "ideal customer" option;
    two completely unrelated things half a paragraph apart got ANDed together and judged as "wants a website audit", hijacking the whole find-customers
    flow into the diagnosis flow. **The triggering text was assembled by the product itself**, not typed by the user — meaning
    selecting any option containing 「报告」 ("report") together with one containing 「官网」 ("website") would trigger it.

    So the loose tier gets a length gate: anything over `_DETECTION_LOOSE_MAX_CHARS` is rejected. The string above is
    120-plus characters and doesn't pass; normal phrasings like 「帮我检测一下官网」 ("audit my website") still hit.
    The exact-phrase tier isn't affected by length — it's a whole-string equality anyway.
    """
    normalized = (text or "").strip().lower().strip(" ，,。.!！?？~～")
    if normalized in {"先检测官网", "检测官网", "官网检测", "诊断官网", "官网诊断", "生成诊断报告", "生成报告"}:
        return True
    if len(normalized) > _DETECTION_LOOSE_MAX_CHARS:
        return False
    return any(k in normalized for k in ("检测", "诊断", "报告")) and any(k in normalized for k in ("官网", "网站", "网址", "url", "链接"))


def _seller_profile_id_for_lead_report(db: Session, user_id: int, lead_id: int) -> int | None:
    """The lead report button is gated by the brand the lead belongs to; it can't pass just because any brand on the account can be audited."""
    lead = db.query(SalesAgentLeadRecommendationORM).filter(
        SalesAgentLeadRecommendationORM.id == int(lead_id),
        SalesAgentLeadRecommendationORM.user_id == user_id,
    ).first()
    if lead is None:
        raise SalesAgentError("线索不存在或不属于当前用户。", status_code=404)
    if not lead.campaign_id:
        return None
    campaign = db.query(SalesAgentRecommendationCampaignORM).filter(
        SalesAgentRecommendationCampaignORM.id == int(lead.campaign_id),
        SalesAgentRecommendationCampaignORM.user_id == user_id,
    ).first()
    return campaign.seller_profile_id if campaign is not None else None


def _is_detection_escape(message: str, intent: str) -> bool:
    """Exit of the audit flow.

    This flow used to **have no exit**: once in detection_url / detection_keywords,
    every message was swallowed by one of the two branches and `return True`ed, so the find-customers branch below was never reached,
    and the only place in the file that cleared `detection_flow` was inside `if detected_url:` —
    there was no way out except handing over a URL.

    Production on 2026-08-27: a user misjudged into the audit flow couldn't get out even by typing 「推荐企业?」 ("recommend companies?"). For that sentence
    `_is_detection_entry` is False — **it never reached the entry check; the state intercepted it** —
    so tightening the entry alone isn't enough; people stuck in existing sessions stay stuck.

    Same criterion as the URL-bearing branch (see `intent not in {"lead","lookup","faq"}` below):
    explicitly finding customers / checking history / asking questions means they're not asking for an audit.
    """
    return _is_find_customer_entry(message) or coarse_intent_for(intent) in {"lead", "lookup", "faq"}


def _url_from_text(text: str) -> str:
    match = _URL_RE.search(text or "")
    if not match:
        return ""
    url = match.group(0).strip().rstrip("，,；;。")
    return url if re.match(r"^https?://", url, flags=re.IGNORECASE) else f"https://{url}"


def _brand_from_url(url: str) -> str:
    host = re.sub(r"^https?://", "", url or "", flags=re.IGNORECASE).split("/", 1)[0].split(":", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    first = host.split(".", 1)[0].strip()
    return first or host or url


def _clean_detection_terms(text: str) -> list[str]:
    raw = re.sub(r"(?:品牌词|品牌|行业关键词|行业词|行业|关键词)\s*[：:]", " ", text or "", flags=re.IGNORECASE)
    parts = [part.strip(" \t\r\n，,；;。'\"“”") for part in re.split(r"[、,，;；/\n\r]+", raw)]
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or _URL_RE.fullmatch(part):
            continue
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(part[:40])
        if len(values) >= 5:
            break
    return values


def _detection_terms_from_text(text: str, default_brand: str) -> tuple[list[str], list[str]]:
    raw = (text or "").strip()
    brand_match = re.search(r"(?:品牌词|品牌)\s*[：:]\s*(?P<value>.*?)(?:[；;。\n]|$)", raw, flags=re.IGNORECASE)
    industry_match = re.search(r"(?:行业关键词|行业词|行业)\s*[：:]\s*(?P<value>.*?)(?:[；;。\n]|$)", raw, flags=re.IGNORECASE)
    brand_terms = _clean_detection_terms(brand_match.group("value")) if brand_match else []
    if default_brand and default_brand not in brand_terms:
        brand_terms.insert(0, default_brand)
    industry_source = industry_match.group("value") if industry_match else re.sub(r"(?:品牌词|品牌)\s*[：:]\s*.*?(?:[；;。\n]|$)", " ", raw, flags=re.IGNORECASE)
    industry_terms = _clean_detection_terms(industry_source)
    brand_keys = {item.casefold() for item in brand_terms}
    industry_terms = [item for item in industry_terms if item.casefold() not in brand_keys]
    return industry_terms[:5], brand_terms[:5]


def _detection_flow_state(deps) -> dict[str, object]:
    return deps.tool_state.setdefault("detection_flow", {}) if isinstance(deps.tool_state, dict) else {}


def _append_detection_guidance(deps, intent: str, reply: str, question: str, card_id: str, field: str, options: list[dict[str, str]] | None = None) -> None:
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": reply,
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": card_id,
            "field": field,
            "title": question,
            "helper_text": "补齐这些信息后才会开始生成诊断报告;未补齐前不会生成失败报告卡片。",
            "type": "text_suggestions",
            "options": options or [],
            "allow_text_fallback": True,
        },
        "missing_items": [field],
        "leads": [],
        "count": 0,
    }
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [build_guidance_card_from_result("recommend_companies", data)]})


def _append_detection_url_guidance(deps, intent: str) -> None:
    flow = _detection_flow_state(deps)
    flow.clear()
    flow["step"] = "detection_url"
    _save_lead_flow_state(deps)
    _append_detection_guidance(
        deps,
        intent,
        "可以,先把要检测的官网发我。",
        "官网地址：请先输入要检测的官网 URL。",
        "sales_agent_detection_url",
        "官网 URL",
    )


def _append_detection_keywords_guidance(deps, intent: str, url: str, reply: str = "已记录官网。还需要补充品牌词和行业关键词,补齐后我再开始生成诊断报告。") -> None:
    flow = _detection_flow_state(deps)
    flow["step"] = "detection_keywords"
    flow["url"] = url
    flow["brand_name"] = str(flow.get("brand_name") or _brand_from_url(url))
    _save_lead_flow_state(deps)
    brand = str(flow.get("brand_name") or "品牌名")
    _append_detection_guidance(
        deps,
        intent,
        reply,
        f"品牌词/行业关键词：请补充 {brand} 的品牌词和行业关键词。比如“品牌词:{brand};行业关键词:网络安全、SaaS”。",
        "sales_agent_detection_keywords",
        "品牌词和行业关键词",
        options=[
            {"label": "AI-GEO、SEO", "value": "AI-GEO、SEO"},
            {"label": "网络安全、SaaS", "value": "网络安全、SaaS"},
            {"label": "数字营销、企业服务", "value": "数字营销、企业服务"},
        ],
    )


async def _append_detection_report_started(deps, intent: str, message: str) -> None:
    flow = _detection_flow_state(deps)
    url = str(flow.get("url") or "")
    brand = str(flow.get("brand_name") or _brand_from_url(url))
    industry_terms, brand_terms = _detection_terms_from_text(message, brand)
    if not industry_terms:
        _append_detection_keywords_guidance(deps, intent, url, "还需要补充行业关键词,补齐后我再开始生成诊断报告。")
        return
    try:
        guard_check_user(deps.db, deps.user_id, "generate_detection_report", _active_seller_profile_id(deps))
    except SalesAgentGuardDenied as e:
        append_chat_message(deps, "assistant", str(e), intent=intent)
        return
    result = await start_detection_report(
        deps.db,
        deps.user_id,
        url=url,
        brand_name=brand,
        industry_terms=industry_terms,
        brand_terms=brand_terms,
        competitors=[],
    )
    flow["step"] = "ready"
    flow["industry_terms"] = industry_terms
    flow["brand_terms"] = brand_terms
    _save_lead_flow_state(deps)
    card = build_guidance_card_from_result("generate_detection_report", result)
    reply = result.get("hint") or "诊断报告已开始生成。"
    if result.get("status") == "failed":
        reply = str(result.get("error") or "诊断报告发起失败,请稍后重试。")
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})


def _target_size_from_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw in {"不限规模", "不限制规模", "规模不限"}:
        return "不限规模"
    values: list[str] = []
    for part in _list_from_text(raw):
        if part in {"不限规模", "不限制规模", "规模不限"}:
            return "不限规模"
        bucket = _normalize_company_size_bucket(part) or (part if part in COMPANY_SIZE_BUCKET_LABELS else "")
        if bucket and bucket not in values:
            values.append(bucket)
    return "、".join(values)


def _seller_team_from_text(text: str) -> str:
    raw = (text or "").strip()
    return _normalize_company_size_bucket(raw) or (raw if raw else "")


def _seller_team_bucket_from_text(text: str) -> str:
    raw = (text or "").strip()
    return _normalize_company_size_bucket(raw) or (raw if raw in COMPANY_SIZE_BUCKET_LABELS else "")


def _dedupe_text_parts(parts: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return values


def _list_from_text(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        parsed = None
    if isinstance(parsed, list):
        parts = [str(item).strip() for item in parsed]
    else:
        # Don't split on `/`: card options contain original text with slashes (「官网建设/优化」 "website building/optimization", 「销售/交付团队」 "sales/delivery team"),
        # and splitting would make two items ("官网建设" + "优化"; that's what a production profile looked like on 2026-08-23).
        parts = [part.strip() for part in re.split(r"[、,，;；\n]+", raw)]
    return _dedupe_text_parts(parts)


def _line_list_from_text(text: str) -> list[str]:
    """One long-text option per line. Commas/enumeration commas/semicolons inside a sentence are content, not boundaries."""
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        parsed = None
    if isinstance(parsed, list):
        return _dedupe_text_parts([str(item).strip() for item in parsed])
    return _dedupe_text_parts([part.strip() for part in re.split(r"[\n\r]+", raw)])


def _typical_customers_from_text(text: str) -> list[str]:
    return _line_list_from_text(text)


def _empty_profile_catalog(catalog: dict[str, list[dict]] | None) -> dict[str, list[dict]]:
    """An empty profile card doesn't show system-preset capabilities/customer types; only private items the user added are kept."""
    scoped = {key: [dict(o) for o in (value or [])] for key, value in (catalog or {}).items()}
    for kind in (CAPABILITY, TYPICAL_CUSTOMER):
        scoped[kind] = [o for o in scoped.get(kind, []) if str(o.get("source") or "system") != "system"]
    return scoped


def _profile_suggestion_context(deps, flow: dict[str, object], profile: dict[str, object]) -> dict[str, object]:
    return {
        "business_hint": _flow_list(flow, "business_hint"),
        "brand_name": profile.get("brand_name") or "",
        "service_capabilities": profile.get("service_capabilities") or _flow_list(flow, "seller_capabilities"),
        "service_scope": profile.get("service_scope") or _flow_list(flow, "service_scope"),
        "target_industries": _flow_list(flow, "target_industries"),
        "target_regions": _flow_list(flow, "target_regions"),
        "target_company_size": flow.get("target_company_size") or "",
    }


def _cached_profile_suggestions(flow: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    cache = flow.get("profile_suggestions") if isinstance(flow.get("profile_suggestions"), dict) else None
    hint_key = "|".join(_flow_list(flow, "business_hint"))
    if not cache or str(cache.get("business_hint_key") or "") != hint_key:
        return {"service_capabilities": [], "service_scope": [], "typical_customers": []}
    return {
        "service_capabilities": [dict(o) for o in (cache.get("service_capabilities") or []) if isinstance(o, dict)],
        "service_scope": [dict(o) for o in (cache.get("service_scope") or []) if isinstance(o, dict)],
        "typical_customers": [dict(o) for o in (cache.get("typical_customers") or []) if isinstance(o, dict)],
    }


async def _profile_suggestions_for_flow(
    deps,
    flow: dict[str, object],
    profile: dict[str, object],
    *,
    required: str = "",
) -> dict[str, list[dict[str, str]]]:
    cached = _cached_profile_suggestions(flow)
    if any(cached.values()) and (not required or cached.get(required)):
        return cached
    hint_key = "|".join(_flow_list(flow, "business_hint"))
    suggestions = await suggest_profile_options(_profile_suggestion_context(deps, flow, profile))
    flow["profile_suggestions"] = {"business_hint_key": hint_key, **suggestions}
    _save_lead_flow_state(deps)
    return suggestions


def _seller_context_values(flow: dict[str, object], profile: dict[str, object]) -> list[str]:
    return _merge_keep_order(
        profile.get("service_capabilities") if isinstance(profile, dict) else [],
        profile.get("service_scope") if isinstance(profile, dict) else [],
        _flow_list(flow, "seller_capabilities"),
        _flow_list(flow, "service_scope"),
    )


def _lead_profile_has_value(flow: dict[str, object], profile: dict[str, object], flow_key: str, profile_key: str) -> bool:
    return bool(_flow_list(flow, flow_key) or (profile.get(profile_key) if isinstance(profile, dict) else None))


def _orphan_profile_ready_for_summary(flow: dict[str, object], profile: dict[str, object]) -> bool:
    """A profile without uploaded material must have all four items explicit to be treated as our own profile ready for direct confirmation."""
    return bool(
        _lead_profile_has_value(flow, profile, "seller_capabilities", "service_capabilities")
        and _lead_profile_has_value(flow, profile, "service_scope", "service_scope")
        and _lead_profile_has_value(flow, profile, "typical_customers", "typical_customers")
        and str(flow.get("seller_team") or profile.get("company_size") or "").strip()
    )


def _needs_business_hint_first(flow: dict[str, object], profile: dict[str, object]) -> bool:
    """When no usable profile exists, first ask for the business direction, then let AI expand profile candidates by industry/service."""
    if _flow_list(flow, "business_hint"):
        return False
    if profile.get("has_profile"):
        return not _seller_context_values(flow, profile)
    # A profile not linked to uploaded/topic material is summarized for direct confirmation only when all four items are explicit enough;
    # otherwise first use the business direction to let AI expand capabilities, service directions and ideal customers, so new accounts don't see a half-empty history card.
    return not _orphan_profile_ready_for_summary(flow, profile)


def _ai_capability_options(flow: dict[str, object]) -> list[dict[str, str]]:
    return _cached_profile_suggestions(flow).get("service_capabilities", [])


def _ai_service_scope_options(flow: dict[str, object]) -> list[dict[str, str]]:
    return _cached_profile_suggestions(flow).get("service_scope", [])


def _ai_typical_customer_options(flow: dict[str, object]) -> list[dict[str, str]]:
    return _filter_typical_customer_options(_cached_profile_suggestions(flow).get("typical_customers", []))


def _target_industry_from_text(text: str) -> str:
    raw = re.sub(r"(?:推荐)?\s*\d+\s*家(?:企业|公司)?", "", (text or "").strip()).strip(" 、,，;；/-")
    if raw in CUSTOM_INDUSTRY_TOKENS:
        return "__custom__"
    if raw in NO_LIMIT_INDUSTRY_TOKENS:
        return "不限行业"
    return raw


def _target_industries_from_text(text: str) -> list[str]:
    raw = re.sub(r"(?:推荐)?\s*\d+\s*家(?:企业|公司)?", "", (text or "").strip()).strip(" 、,，;；/-")
    values: list[str] = []
    for part in _list_from_text(raw):
        item = part.strip(" 、,，;；-")
        if not item or item in CUSTOM_INDUSTRY_TOKENS or item in FLOW_ACTION_TOKENS:
            continue
        if item in NO_LIMIT_INDUSTRY_TOKENS:
            return ["不限行业"]
        if item not in values:
            values.append(item)
    return values


def _target_regions_from_text(text: str) -> list[str]:
    raw = re.sub(r"(?:推荐)?\s*\d+\s*家(?:企业|公司)?", "", (text or "").strip()).strip(" 、,，;；/-")
    if not raw:
        return []
    parts = _list_from_text(raw)
    regions: list[str] = []
    for part in parts:
        item = part.strip(" 、,，;；-")
        if not item or item in FLOW_ACTION_TOKENS:
            continue
        if item in {"不限地区", "不限制地区", "地区不限", "全国", "全国范围"}:
            # 「全国」 ("nationwide") is no longer shown as a region card option; typed by hand it's still accepted as “不限地区” ("any region").
            # 「不限地区」 ("any region") is mutually exclusive: when it appears together with specific regions, the backend keeps only "any region".
            return ["不限地区"]
        elif item == "一线城市":
            regions.append("一线城市")
        else:
            regions.append(item)
    return _clean_list(regions, 30)


def _recommendation_limit_from_text(text: object) -> int:
    raw = str(text or "").strip()
    match = re.search(r"(\d+)\s*家", raw) or re.search(r"(?:推荐|数量|几家)\D{0,6}(\d+)", raw)
    if not match and re.fullmatch(r"\d+", raw):
        match = re.search(r"\d+", raw)
    if not match:
        return 0
    try:
        n = int(match.group(1) if match.lastindex else match.group(0))
    except Exception:  # noqa: BLE001
        return 0
    return max(1, min(DEFAULT_RECOMMENDATIONS, n))


def _lead_flow_state(deps) -> dict[str, object]:
    return deps.tool_state.setdefault("lead_flow", {}) if isinstance(deps.tool_state, dict) else {}


def _save_lead_flow_state(deps) -> None:
    if deps.session_id is None:
        return
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if row is None or row.user_id != deps.user_id:
        return
    try:
        context = json.loads(row.context_json or "{}") if row.context_json else {}
    except Exception:  # noqa: BLE001
        context = {}
    if not isinstance(context, dict):
        context = {}
    context["tool_state"] = deps.tool_state or {}
    row.context_json = _dumps(context)
    deps.db.commit()


def _flow_echo_card(card: dict[str, Any], flow: dict[str, object]) -> dict[str, Any]:
    """Newly issued cards must also carry this turn's selected values; session replay fills them in, but the first SSE render doesn't get session_bundle."""
    data = card.get("data") if isinstance(card, dict) else None
    if not isinstance(data, dict):
        return card
    data, changed = _apply_lead_flow_selected_values_to_guidance(data, flow)
    if not changed:
        return card
    return build_guidance_card_from_result(str(card.get("tool") or "recommend_companies"), data)


def _attach_active_seller_profile_id(card: dict[str, Any], deps) -> dict[str, Any]:
    data = card.get("data") if isinstance(card.get("data"), dict) else None
    question_card = data.get("question_card") if isinstance(data, dict) and isinstance(data.get("question_card"), dict) else None
    if question_card is None:
        return card
    seller_profile_id = _active_seller_profile_id(deps)
    if seller_profile_id is None:
        return card
    question_card["seller_profile_id"] = seller_profile_id
    return card


def _build_flow_missing_card(deps, flow: dict[str, object], missing: list[str], question: str, **kwargs) -> dict[str, Any]:
    return _attach_active_seller_profile_id(_flow_echo_card(build_missing_info_guidance_card(missing, question=question, **kwargs), flow), deps)


def _append_campaign_industry_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    question = "目标行业：这次想找哪个行业的客户?"
    reply = "已记录目标企业规模。继续确认目标行业。"
    echo_flow = dict(flow)
    if not _flow_list(echo_flow, "target_industries"):
        latest_industries = _latest_target_industries_for_echo(deps)
        if latest_industries:
            echo_flow["target_industries"] = latest_industries
    card = _build_flow_missing_card(deps, echo_flow, ["本次想找的目标客户行业"], question, catalog_options=_catalog_options(deps))
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "target_industries", echo_flow)


def _append_custom_capability_guidance(deps, intent: str) -> None:
    """The user clicked 「自定义能力」 ("custom capability") in the capability card — that's not a capability, it means "I'll write my own".

    Without a follow-up, the profile would end up with "you are a company providing custom capabilities" (verbatim from production, 2026-08-23).
    Same approach as custom industry.
    """
    flow = _lead_flow_state(deps)
    flow["step"] = "custom_capability"
    _save_lead_flow_state(deps)
    question = "自定义能力:请直接输入你们的这项专业能力。"
    card = _build_flow_missing_card(deps, flow, ["我方专业能力"], question, catalog_options=_catalog_options(deps))
    append_chat_message(deps, "assistant", "好,请补充一项具体能力。", intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "custom_capability", flow)


def _append_custom_industry_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "custom_industry"
    _save_lead_flow_state(deps)
    question = "自定义行业：请直接输入这次想找的目标客户行业。"
    reply = "可以,请补充一个具体目标行业。"
    card = _build_flow_missing_card(deps, flow, ["本次想找的目标客户行业"], question, catalog_options=_catalog_options(deps))
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "custom_industry", flow)


def _append_campaign_region_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "target_regions"
    echo_flow = dict(flow)
    if not _flow_list(echo_flow, "target_regions"):
        latest_regions = _latest_target_regions_for_echo(deps)
        if latest_regions:
            echo_flow["target_regions"] = latest_regions
    _save_lead_flow_state(deps)
    question = "目标地区：这次想找哪个地区的客户?"
    reply = "已记录目标行业。继续确认目标地区。"
    card = _build_flow_missing_card(deps, echo_flow, ["本次想找的目标地区"], question, catalog_options=_catalog_options(deps))
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "target_regions", echo_flow)


def _append_lead_target_size_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "target_company_size"
    echo_flow = dict(flow)
    if not str(echo_flow.get("target_company_size") or "").strip():
        latest_size = _latest_target_company_size_for_echo(deps)
        if latest_size:
            echo_flow["target_company_size"] = latest_size
    _save_lead_flow_state(deps)
    question = "目标企业规模：这次想优先找什么规模的客户企业?"
    reply = "已记录适合客户类型,继续确认这次推荐的目标客户规模。"
    card = _build_flow_missing_card(deps, echo_flow, ["目标客户企业规模"], question, catalog_options=_catalog_options(deps))
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "target_company_size", echo_flow)


def _brand_is_ambiguous(deps) -> bool:
    """In a multi-brand account, "which brand to use this time" hasn't been decided yet.

    In this case `_active_seller_profile_id` returns None, and the three downstream fallbacks on None are **all**
    `_latest_seller_profile(db, user_id)` = `order_by(id.desc()).first()`, i.e. the most recently
    created brand. So no matter which one the user wants, reads and writes all go to the same one
    (production, 2026-08-27: both brands' update-profile cards had identical content, all GEO-TAG's).

    Guessing the wrong brand isn't on the scale of "a few missing options":
      - `_append_seller_profile_summary_card` prefills flow from it, and the router skips slots it sees as filled
      - `_persist_lead_seller_profile_snapshot` **overwrites the whole row**, i.e. A's answers
        wipe out what B filled in /admin
    If undecided, don't guess — hand over to the brand selection card (the router already issues it first when `_brand_required` is true).
    """
    return _brand_required(deps) and _active_seller_profile_id(deps) is None


def _brand_profile_options(profile: dict[str, object], field: str, kind: str = CAPABILITY) -> list[dict[str, object]]:
    """Turn one item saved by the brand itself into card options (deduped by value, keeping DB order).

    `profile` comes from `seller_profile_for_lead_entry`, i.e. the row of **this one brand** in sales_agent_seller_profiles.
    Multi-brand accounts are distinguished by it: two brands, two rows, so the cards look different.

    desc follows `has_profile`: for brands that claimed brand material, this row is a mirror synced from
    `ai_telemetry_topics.profile_json` — calling it "the profile you saved"
    would send users to /admin looking for a form that's now read-only. Orphan brands really can only be filled by hand in /admin,
    so the copy stays.
    """
    desc = "来自你上传的品牌资料。" if profile.get("has_profile") else "来自你保存的我方画像。"
    seller_profile_id = int(profile.get("brand_id") or 0) or None
    options: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in profile.get(field) or []:
        text = str(item).strip()
        key = text.casefold()
        if not text or "待补充" in text or key in seen:
            continue
        seen.add(key)
        options.append({
            "label": text,
            "value": text,
            "desc": desc,
            "source": "custom",
            "kind": kind,
            "seller_profile_id": seller_profile_id,
            "editable": True,
        })
    return options


def _is_industry_unlimited_label(value: object) -> bool:
    text = str(value or "").strip()
    return text in {"不限行业", "不限制行业", "行业不限", "全行业", "不限"}


def _filter_typical_customer_options(options: list[dict[str, object]]) -> list[dict[str, object]]:
    """The ideal-customer card only shows customer types; target industry terms that leaked into the slot from history are filtered out."""
    return [
        option for option in options
        if not _is_industry_unlimited_label(option.get("value") or option.get("label"))
    ]


def _brand_typical_customer_options(profile: dict[str, object]) -> list[dict[str, object]]:
    """Turn the brand's own saved 「适合客户」 ("ideal customers") into card options."""
    return _filter_typical_customer_options(_brand_profile_options(profile, "typical_customers", TYPICAL_CUSTOMER))


def _append_business_hint_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "business_hint"
    _save_lead_flow_state(deps)
    question = "业务方向：你们主要提供什么服务或产品?"
    card = _build_flow_missing_card(
        deps,
        flow,
        ["业务方向或服务产品"],
        question,
        catalog_options=_catalog_options(deps),
    )
    append_chat_message(
        deps,
        "assistant",
        "先确认你们主要提供什么服务或产品,我再基于这个生成可勾选的画像候选。",
        intent=intent,
        metadata={"tool_cards": [card]},
    )
    _record_visible_card_checkpoint(deps, "business_hint", flow)


def _append_lead_typical_customers_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "typical_customers"
    _save_lead_flow_state(deps)
    profile: dict[str, object] = {} if _brand_is_ambiguous(deps) else seller_profile_for_lead_entry(
        deps.db, deps.user_id, _active_seller_profile_id(deps)
    )
    brand_options = _brand_typical_customer_options(profile)
    catalog = _catalog_options(deps)
    question_options: list[dict[str, str]] | None = None
    if brand_options:
        brand_options = _filter_typical_customer_options(brand_options)
        # When brand/AI-generated ideal-customer candidates exist, show only those; system defaults are only a fallback,
        # and must not be mixed into non-GEO scenarios like finance/tax or legal to mislead users.
        catalog[TYPICAL_CUSTOMER] = brand_options
        if not _flow_list(flow, "typical_customers"):
            flow["typical_customers"] = [
                str(option.get("value") or option.get("label") or "").strip()
                for option in brand_options
                if str(option.get("value") or option.get("label") or "").strip()
            ]
            _save_lead_flow_state(deps)
    else:
        # For empty profiles, the fixed GEO/SEO customer fallback is no longer shown; when AI fails or hasn't run, only the custom input entry is given.
        catalog = _empty_profile_catalog(catalog)
        catalog[TYPICAL_CUSTOMER] = _ai_typical_customer_options(flow) + list(catalog.get(TYPICAL_CUSTOMER) or [])
    question = "适合客户：你最适合服务哪类客户?"
    reply = "已记录团队情况,请确认这次更适合服务的客户类型。"
    card = _build_flow_missing_card(
        deps,
        flow,
        ["典型客户或适合服务的客户类型"],
        question,
        question_options=question_options,
        catalog_options=catalog,
    )
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "typical_customers", flow)


def _append_lead_seller_team_size_guidance(deps, intent: str) -> None:
    _append_lead_seller_capabilities_guidance(deps, intent)


def _append_lead_service_scope_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    flow["step"] = "service_scope"
    _save_lead_flow_state(deps)
    profile = _seller_profile_context(deps)
    brand_options = _brand_profile_options(profile, "service_scope")
    question_options = brand_options or _ai_service_scope_options(flow)
    card = _build_flow_missing_card(
        deps,
        flow,
        ["你准备推广/销售的服务范围"],
        "服务/产品方向：这次你主要想推广什么服务或产品?",
        question_options=question_options,
        catalog_options=_catalog_options(deps),
    )
    append_chat_message(deps, "assistant", "已记录专业能力,请确认这次要推广的服务方向。", intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "service_scope", flow)


def _seller_profile_context(deps) -> dict[str, object]:
    return {} if _brand_is_ambiguous(deps) else seller_profile_for_lead_entry(
        deps.db, deps.user_id, _active_seller_profile_id(deps)
    )


def _append_lead_seller_capabilities_guidance(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    profile = _seller_profile_context(deps)
    if _needs_business_hint_first(flow, profile) and not _brand_is_ambiguous(deps):
        _append_business_hint_guidance(deps, intent)
        return
    flow["step"] = "seller_profile"
    _save_lead_flow_state(deps)
    # This card used to read only the option catalog, not a single field of sales_agent_seller_profiles.
    # The catalog is mostly system-preset rows (both ownership columns NULL), and brand-private rows had only one
    # source, "custom values typed by the user in chat" — content filled in /admin never entered the catalog.
    # So when a multi-brand account clicked 「更新画像」 ("update profile"), A and B got the same identical preset list,
    # and the items the brand filled itself (e.g. GEO-TAG's "AI engine visibility baseline measurement") weren't on the card at all,
    # so they had to be retyped (2026-08-27 user: "the database should be the source of truth", "shouldn't two brands have two profiles?").
    # Now it works the same way as the 「适合客户」 ("ideal customers") card: the DB copy first, then custom items and system presets.
    # Don't read the DB when the brand is undecided: it would only return the content of the brand with the largest id (see _brand_is_ambiguous),
    # and showing another business line's capabilities on the card is worse than giving only system presets.
    catalog = _catalog_options(deps)
    brand_options = _brand_profile_options(profile, "service_capabilities")
    if brand_options:
        # When the brand's own copy exists, system presets are **hidden**. The 7 presets (AI-GEO optimization / SEO tech team ...,
        # seeded by migration c2e8a5d31f94, with both user_id and seller_profile_id NULL) are generic words shared platform-wide,
        # a distraction for brands with a clear positioning: GEO-TAG only does performance measurement, yet the card showed
        # 「SEO技术团队」 ("SEO tech team"), and ticking it writes into service_capabilities_json — the profile body, later
        # recommendation and email metric definitions all change with it (2026-08-27 user: "everything still shows up").
        # Brand-private items (source=custom, typed by the user in chat) stay; they belong to this brand anyway.
        # To add generic capabilities use the card's built-in `allow_custom_option` input box (set by guidance, unaffected here);
        # it's no longer the trailing 「自定义能力」 ("custom capability") button occupying an option slot.
        private = [o for o in (catalog.get(CAPABILITY) or []) if str(o.get("source") or "system") != "system"]
        # Duplicates are deduped by value in _normalize_question_options; no need to filter again here.
        catalog[CAPABILITY] = brand_options + private
    else:
        # Empty profiles don't show hard-coded capability fallbacks. AI candidates first; when AI fails only the user's private items + custom entry remain.
        catalog = _empty_profile_catalog(catalog)
        catalog[CAPABILITY] = _ai_capability_options(flow) + list(catalog.get(CAPABILITY) or [])
    question = "我方画像"
    reply = "先确认我方画像,后面推荐客户会更准。"
    card = _build_flow_missing_card(
        deps,
        flow,
        ["我方画像"],
        question,
        catalog_options=catalog,
        # Size also comes from the DB: otherwise the card defaults to the literal 「11-100 名员工」 ("11-100 employees"),
        # and once the user clicks continue, the 101-500 saved in /admin gets overwritten.
        company_size_default=str(profile.get("company_size") or ""),
    )
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "seller_profile", flow)


def _merge_keep_order(*lists: list[Any] | None) -> list[str]:
    """Concatenate in order, dedupe by value (casefold), keeping first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for items in lists:
        for item in items or []:
            text = str(item).strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def _persist_profile_updates_to_topic(
    deps,
    brand_id: int | None,
    capabilities: list[str] | None,
    typical_customers: list[str] | None = None,
    company_size: str = "",
) -> bool:
    """For brands that claimed brand material -> write this turn's additions back to the material itself, then refresh the mirror.

    The return value means "this brand is managed by material", not "how many were written": even if nothing new was added (everything the user ticked
    is already in the material) it returns True, because the mirror row still shouldn't be touched by the chat side's full overwrite.
    """
    row = _latest_seller_profile(deps.db, deps.user_id, brand_id)
    topic = topic_for_brand(deps.db, row)
    if topic is None:
        return False
    changed = write_agent_updates_to_topic(
        deps.db,
        topic,
        capabilities=capabilities,
        typical_customers=typical_customers,
        company_size=company_size,
        actor_id=deps.user_id,
    )
    if changed:
        # Only refresh the mirror when the material changed; otherwise it's already up to date.
        sync_brand_from_topic(deps.db, topic)
    deps.db.commit()
    return True


def _persist_lead_seller_profile_snapshot(
    deps,
    capabilities: list[str] | None = None,
    company_size: str = "",
    typical_customers: list[str] | None = None,
    service_scope: list[str] | None = None,
) -> None:
    """Persist this turn's collected profile to DB so the next new session shows the profile card directly instead of asking again.

    Capabilities, team size and 「适合客户」 ("ideal customers") are all written back incrementally. List fields only grow, so one
    chat selection doesn't wash out existing content in /admin; team size is only filled when the material is empty.

    The source of truth is `ai_telemetry_topics.profile_json` (brand material uploaded by the customer), so brands that claimed material
    get **two-way write-back**: capabilities are appended to the material's 「服务核心特点」 ("core service features"), ideal customers to
    「服务覆盖场景 / 客户类型」 ("service scenarios / customer types"), then the mirror row is refreshed. Orphan brands (no claimed material) have nowhere to write back,
    so they keep writing only the mirror row — their only home.
    """
    if _brand_is_ambiguous(deps):
        # Whole-row overwrite + guessed brand = this turn's answers wipe out another brand's copy in /admin.
        # Better not persist this turn: once the brand is chosen the router comes back here, and nothing is lost.
        logger.warning("Skip seller profile snapshot: brand not selected yet (user_id=%s)", deps.user_id)
        return
    try:
        brand_id = _active_seller_profile_id(deps)
        if _persist_profile_updates_to_topic(deps, brand_id, capabilities, typical_customers, company_size):
            # Material-side write succeeded -> the mirror row has already been refreshed from material by the syncer; another full overwrite would only
            # cover the freshly synced content with old values.
            return
        existing = seller_profile_for_lead_entry(deps.db, deps.user_id, brand_id)
        seller = existing.get("seller_profile") if isinstance(existing.get("seller_profile"), dict) else {}
        # Write to the row of **the brand chosen this time**. It used to "read the latest row and write it back",
        # so brands A and B overwrote each other when edited in turn.
        seller_profile_id = brand_id
        try:
            seller_profile_id = seller_profile_id or int(seller.get("id") or 0) or None
        except Exception:  # noqa: BLE001
            seller_profile_id = brand_id
        legacy_profile_id = None
        try:
            legacy_profile_id = int(seller.get("legacy_profile_id") or 0) or None
        except Exception:  # noqa: BLE001
            legacy_profile_id = None
        # The DB copy is the **base**, and this turn's answers are appended (deduped by value).
        # It used to be `capabilities or existing...` — any new answer replaced the whole column. But the card's
        # `selected_values` only come from this session's lead_flow (guidance._selected_values_from_question_card_flow),
        # so none are ticked when a new session opens the card: the user sees 5 items, casually ticks 1, and in /admin those 5
        # become just 1. Unticked ≠ delete, just not mentioned this turn. So the catalog only grew while
        # the profile row kept only the last turn — the two drifted further apart, which is where "few in the DB, extra ones on the card" came from
        # (2026-08-27 user: "the ones added in chat should also show up here").
        # Read `seller` rather than `existing`: the latter's service_capabilities falls back to
        # service_scope when empty (seller_profile_for_lead_entry), and merging it in would copy the service scope as capabilities.
        # Deleting capabilities still happens only in /admin — the chat path only adds.
        merged_capabilities = _merge_keep_order(seller.get("service_capabilities"), capabilities)
        # Service directions and capabilities are saved separately: service scope selected in chat is only appended to service_scope_json,
        # and must no longer be copied into service_capabilities_json as a fallback.
        merged_service_scope = _merge_keep_order(existing.get("service_scope"), service_scope)
        merged_typical = _merge_keep_order(existing.get("typical_customers"), typical_customers)
        merged_size = str(company_size or existing.get("company_size") or "").strip()
        if not merged_capabilities and not merged_service_scope and not merged_size and not merged_typical:
            return
        create_or_update_seller_profile(
            deps.db,
            deps.user_id,
            seller_profile_id=seller_profile_id,
            legacy_profile_id=legacy_profile_id,
            service_scope=merged_service_scope,
            service_capabilities=merged_capabilities,
            company_size=merged_size,
            typical_customers=merged_typical,
            default_buyer_roles=seller.get("default_buyer_roles") or None,
            default_exclude_rules=seller.get("default_exclude_rules") or None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Persist lead seller profile snapshot failed")


def _append_seller_profile_summary_card(deps, intent: str) -> bool:
    if _brand_is_ambiguous(deps):
        # Returns False = this card can't be issued; hand back to the router; with `_brand_required` true, the next step is the brand selection card.
        # Forcing it would show a card titled A with B's content, prefilling flow from B,
        # and the router would skip the filled slot — the user would never get a chance to correct it.
        return False
    profile = seller_profile_for_lead_entry(deps.db, deps.user_id, _active_seller_profile_id(deps))
    has_profile_values = bool(profile.get("service_capabilities") or profile.get("service_scope") or profile.get("company_size"))
    if not profile.get("ready") and not has_profile_values:
        return False
    # Including the brand name in the card title only matters for multi-brand accounts; for single-brand it's dropped and the title stays 「我方画像」 ("our profile") —
    # existing accounts see exactly the same thing as before.
    if not _brand_required(deps):
        profile = {**profile, "brand_name": ""}
    flow = _lead_flow_state(deps)
    flow["step"] = "seller_profile_summary"
    flow["seller_capabilities"] = profile.get("service_capabilities") or []
    flow["service_scope"] = profile.get("service_scope") or []
    flow["seller_team"] = profile.get("company_size") or ""
    flow["seller_team_raw"] = profile.get("company_size") or ""
    # 「适合客户」 ("ideal customers") is **deliberately not prefilled**. It's different from the items above: capabilities/size are the brand's own attributes,
    # rarely changing even once a year; "what kind of customers to find this time" is a per-task filter, on par with target industry, region and
    # company size — those three are stored on the recommendation task row, created fresh every run, never prefilled.
    # Prefilling it here would make the router skip the filled slot, and the card would never appear again
    # (2026-08-26 user: "ideal customers should be chosen again every time, right?").
    #
    # This didn't change after brand material took over the profile. The material's `target_scenarios` are already
    # placed first among that card's options by `_append_lead_typical_customers_guidance` — the user just ticks them,
    # without retyping; but "which ones to tick" is still decided each turn. What's saved is typing, not choosing.
    _save_lead_flow_state(deps)
    card = build_seller_profile_summary_card(profile)
    reply = "已根据已有内容组装我方画像,请确认继续还是更新画像。" if not profile.get("ready") else "已读取到你历史保存的我方画像,请确认是继续还是更新画像。"
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    _record_visible_card_checkpoint(deps, "seller_profile_summary", flow)
    return True


def _flow_list(flow: dict[str, object], key: str) -> list[str]:
    value = flow.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ensure_recommendation_profile_usage_for_campaign(deps, flow: dict[str, object], campaign_id: int, intent: str) -> None:
    """Write the recommendation conditions confirmed in this turn's chat into that brand's recommendation profile, and bind it to the recommendation task.

    The recommendation profile is the context of this recommendation/email, not the brand profile; brand_profile isn't written here,
    sales_agent_seller_profiles isn't updated; only recommendation_profile_versions/usages are touched.

    Upsert every turn, **without reusing the profile_key pinned in flow**: restored history sessions or old checkpoints
    bring in the previous turn's key, and recording usage with it directly would lose a change like "the user just changed the industry from healthcare to manufacturing",
    so outreach emails would get old conditions. Upsert overwrites the conditions with the latest each turn, so this mismatch can't happen.
    """
    if not campaign_id or not _lead_flow_ready_for_recommendation_profile_save(flow):
        return
    try:
        seller_profile_id = _active_seller_profile_id(deps)
        brand_name = ""
        try:
            profile = seller_profile_for_lead_entry(deps.db, deps.user_id, seller_profile_id)
            brand_name = str(profile.get("brand_name") or "").strip()
        except Exception:  # noqa: BLE001
            brand_name = ""
        row = upsert_recommendation_profile_from_flow(
            deps.db,
            deps.user_id,
            flow,
            seller_profile_id=seller_profile_id,
            brand_name_snapshot=brand_name,
            source="chat_auto",
            commit=False,
        )
        profile_key = row.profile_key
        profile_version_id = int(row.id)
        flow["recommendation_profile_key"] = profile_key
        flow["recommendation_profile_version_id"] = profile_version_id
        flow["recommendation_profile_saved_key"] = profile_key
        record_recommendation_profile_usage(
            deps.db,
            deps.user_id,
            profile_key,
            profile_version_id,
            session_id=deps.session_id,
            campaign_id=campaign_id,
            commit=False,
        )
        deps.db.commit()
        _save_lead_flow_state(deps)
    except Exception as e:  # noqa: BLE001
        deps.db.rollback()
        logger.exception("Save Sales Agent recommendation profile snapshot failed")
        write_model_call_log(
            request_id=new_model_call_id(), event="auto_save_recommendation_profile", status="failed", scene="lead",
            user_id=deps.user_id, session_public_id=deps.session_public_id, intent=intent,
            message=str(flow), error=e,
        )


def _append_lead_recommendations(deps, intent: str) -> None:
    flow = _lead_flow_state(deps)
    requested_limit = _recommendation_limit_from_text(flow.get("requested_limit")) or DEFAULT_RECOMMENDATIONS
    service_context = _clean_list([*_flow_list(flow, "seller_capabilities"), *_flow_list(flow, "service_scope")], 30)
    try:
        guard_check_user(deps.db, deps.user_id, "recommend_companies")
        result = recommend_companies_workflow(
            deps.db,
            deps.user_id,
            limit=requested_limit,
            service_scope=service_context,
            industries=_flow_list(flow, "target_industries"),
            regions=_flow_list(flow, "target_regions"),
            company_size=str(flow.get("seller_team") or ""),
            typical_customers=_flow_list(flow, "typical_customers"),
            target_company_size=str(flow.get("target_company_size") or ""),
            seller_profile_id=_active_seller_profile_id(deps),
            session_id=deps.session_id,
        )
        campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
        campaign_id = int(campaign.get("id") or 0) if isinstance(campaign, dict) else 0
        if campaign_id:
            flow["last_recommendation_campaign_id"] = campaign_id
        if campaign_id:
            _ensure_recommendation_profile_usage_for_campaign(deps, flow, campaign_id, intent)
    except SalesAgentGuardDenied as e:
        append_chat_message(deps, "assistant", str(e), intent=intent)
        return
    except SalesAgentError as e:
        deps.db.rollback()
        append_chat_message(deps, "assistant", _public_error_message(e), intent=intent)
        return
    except Exception as e:  # noqa: BLE001
        deps.db.rollback()
        logger.exception("Sales Agent deterministic recommendation failed")
        write_model_call_log(
            request_id=new_model_call_id(), event="deterministic_recommendation", status="failed", scene="lead",
            user_id=deps.user_id, session_public_id=deps.session_public_id, intent=intent,
            message=str(flow), error=e,
        )
        append_chat_message(deps, "assistant", "这次推荐企业生成失败了,请稍后重试;如果仍失败,可以先把推荐数量改成 20 家再试。", intent=intent)
        return
    card = build_guidance_card_from_result("recommend_companies", card_payload(result))
    cards = [card]
    if result.get("needs_user_input") or result.get("status") == "need_more_info":
        reply = _fallback_output_from_cards([card]) or "还需要补充一个信息,才能继续推荐企业。"
    else:
        count = int(result.get("actual_count") or result.get("count") or 0)
        reply = "" if count else "暂时没有找到足够企业;可以调整行业、地区或规模后再试。"
        # The recommendation result card has landed; `step=ready` is only an internal transitional state and can't keep being treated as a pending card.
        # Otherwise the next “继续推荐一批” ("recommend another batch") would be blocked by the ready cursor, fall to the model path and possibly time out.
        flow["step"] = ""
        _save_lead_flow_state(deps)
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": cards})


def _append_continue_recommendations(deps, message: str, intent: str) -> bool:
    requested_limit = _recommendation_limit_from_text(message) or 0
    try:
        guard_check_user(deps.db, deps.user_id, "recommend_companies")
        result = continue_recommend_companies_workflow(
            deps.db,
            deps.user_id,
            limit=requested_limit or None,
            seller_profile_id=_active_seller_profile_id(deps),
            session_id=deps.session_id,
        )
    except SalesAgentGuardDenied as e:
        append_chat_message(deps, "assistant", str(e), intent=intent)
        return True
    except SalesAgentError as e:
        deps.db.rollback()
        append_chat_message(deps, "assistant", _public_error_message(e), intent=intent)
        return True
    except Exception as e:  # noqa: BLE001
        deps.db.rollback()
        logger.exception("Sales Agent deterministic continue recommendation failed")
        write_model_call_log(
            request_id=new_model_call_id(), event="deterministic_continue_recommendation", status="failed", scene="lead",
            user_id=deps.user_id, session_public_id=deps.session_public_id, intent=intent,
            message=message, error=e,
        )
        append_chat_message(deps, "assistant", "这次继续推荐没有稳定完成,请稍后重试;如果还没有上一轮推荐,可以直接说目标行业和地区重新开始。", intent=intent)
        return True
    card = build_guidance_card_from_result("recommend_companies", card_payload(result))
    if result.get("needs_user_input") or result.get("status") == "need_more_info":
        reply = _fallback_output_from_cards([card]) or "还没有找到上一轮推荐条件。请先告诉我目标行业和地区,我再开始推荐。"
    else:
        count = int(result.get("actual_count") or result.get("count") or 0)
        reply = "已按上一轮筛选条件继续推荐,并排除了已推荐/已发送企业。" if count else "按上一轮条件暂时没有找到新的企业;可以调整行业、地区或规模后再试。"
    append_chat_message(deps, "assistant", reply, intent=intent, metadata={"tool_cards": [card]})
    return True


def _append_lead_guidance_fallback(deps, intent: str) -> None:
    """Keep lead-entry A2UI usable when the model/runtime fails before returning cards."""
    _append_lead_seller_capabilities_guidance(deps, intent)


def _last_assistant_reply(deps) -> tuple[str, dict]:
    """Get the assistant message (text + tool_cards) just persisted by the deterministic flow."""
    row = (
        deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.desc())
        .first()
    )
    if row is None:
        return "", {}
    try:
        meta = json.loads(row.metadata_json or "{}")
    except (TypeError, ValueError):
        meta = {}
    return row.content or "", meta if isinstance(meta, dict) else {}


async def _deterministic_reply_stream(deps):
    """The deterministic flow has no increments to stream; emit the result in SSE form all at once, with the same closing events as the model path."""
    text, meta = _last_assistant_reply(deps)
    if text:
        yield f"data: {json.dumps({'delta': text}, ensure_ascii=False)}\n\n"
    cards = meta.get("tool_cards")
    if cards:
        # The key is `cards`: that's what the client reads (`obj.cards` in `salesAgentApi.ts`).
        # While it was sent as `tool_cards`, all SSE cards were silently dropped by the client.
        yield f"data: {json.dumps({'cards': cards}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"


# 「自定义能力」 ("custom capability") in the capability card means "I'll write my own"; on a hit, ask a follow-up instead of storing it as a capability
CUSTOM_CAPABILITY_TOKENS = {"自定义能力", "自定义", "其它能力", "其他能力"}
CUSTOM_INDUSTRY_TOKENS = {"自定义行业", "自定义", "其它行业", "其他行业"}
NO_LIMIT_INDUSTRY_TOKENS = {"不限行业", "不限制行业", "行业不限"}
NO_LIMIT_REGION_TOKENS = {"不限地区", "不限制地区", "地区不限", "全国", "全国范围"}
# 「新增品牌」 ("add brand") isn't a brand name. The card no longer offers this option (brands are created only in /sales-agent/admin),
# but users may still type these four characters — then treat it as "not recognized" and ask again,
# never match it as a brand name, let alone create a business line out of thin air.
BRAND_NEW_TOKENS = {BRAND_NEW_TOKEN, "新增品牌", "新建品牌", "添加品牌", "+ 新增品牌"}


def _active_seller_profile_id(deps) -> int | None:
    """Which brand to use this time (= one row of sales_agent_seller_profiles).

    Chosen in the session takes priority -> the only active brand -> None. None only happens with
    "multiple brands and none chosen yet", and the upper layer issues the brand selection card accordingly; single-brand accounts always take the second path,
    equivalent to the pre-change "take the latest row".
    """
    try:
        return resolve_active_brand_id(deps.db, deps.user_id, _lead_flow_state(deps))
    except Exception:  # noqa: BLE001
        return None


def _brand_required(deps) -> bool:
    """Whether to ask for the brand first. Only counts with ≥2 active brands — existing accounts always have 1 after migration."""
    try:
        return active_brand_count(deps.db, deps.user_id) >= 2
    except Exception:  # noqa: BLE001
        return False


def _is_new_brand_action(message: str) -> bool:
    """This sentence is clicking 「新增品牌」 ("add brand"), not naming a brand."""
    parts = _list_from_text(message)
    return any(part.strip() in BRAND_NEW_TOKENS for part in parts)


def _brand_slot(message: str, brands: list[dict] | None = None) -> dict:
    """Recognize which brand was chosen by brand name. Pure function; the brand list is injected by the caller.

    Only two forms are recognized: "the whole sentence is the brand name" and "exactly one brand name appears in the sentence".
    **Multiple hits count as not recognized** — a wrong brand makes the whole recommendation round and the emails sent later all wrong,
    so better ask again. Typing 「新增品牌」 ("add brand") also returns empty, so the selection card is issued again as-is:
    brands are not created in chat, only in /sales-agent/admin.
    """
    text = str(message or "").strip()
    if not text or _is_new_brand_action(text):
        return {}
    folded = text.casefold()
    exact: list[int] = []
    loose: list[int] = []
    for brand in brands or []:
        name = str(brand.get("name") or "").strip()
        brand_id = int(brand.get("id") or 0)
        if not name or not brand_id:
            continue
        if name.casefold() == folded:
            exact.append(brand_id)
        elif name.casefold() in folded:
            loose.append(brand_id)
    if len(exact) == 1:
        return {"brand_id": exact[0]}
    if not exact and len(loose) == 1:
        return {"brand_id": loose[0]}
    return {}


def _append_brand_picker_guidance(deps, intent: str) -> bool:
    """Issue the brand selection card. With only one brand don't ask; return False directly to let the flow continue."""
    brands = list_brands(deps.db, deps.user_id)
    if len(brands) < 2:
        return False
    flow = _lead_flow_state(deps)
    flow["step"] = BRAND
    _save_lead_flow_state(deps)
    card = build_brand_picker_card(brands)
    append_chat_message(
        deps, "assistant",
        "你名下有多个品牌,先确认这次要推哪一个。",
        intent=intent, metadata={"tool_cards": [card]},
    )
    _record_visible_card_checkpoint(deps, BRAND, flow)
    return True


def _brands_for_picker(deps, state: LeadState) -> list[dict]:
    """Candidate list for the brand selection card. Single-brand accounts don't query the DB — that branch is never reached."""
    if not state.brand_required:
        return []
    try:
        return list_brands(deps.db, deps.user_id)
    except Exception:  # noqa: BLE001
        return []


def _append_brand_profile_entry(deps) -> bool:
    """The first card after the brand is decided: give this brand's profile confirmation card first; without a profile, ask for capabilities directly."""
    if not _append_seller_profile_summary_card(deps, "lead"):
        _append_lead_seller_capabilities_guidance(deps, "lead")
    return True


async def _append_recommendation_profile_or_brand_entry(deps, intent: str = "lead") -> bool:
    """Find-customers entry: only confirm our own profile; no longer package this run's filter conditions as a “推荐画像” ("recommendation profile")."""
    flow = _lead_flow_state(deps)
    profile = _seller_profile_context(deps)
    if _needs_business_hint_first(flow, profile):
        _append_business_hint_guidance(deps, intent)
        return True
    if _flow_list(flow, "business_hint"):
        await _profile_suggestions_for_flow(deps, flow, profile)
    return _append_brand_profile_entry(deps)


RECOMMENDATION_PROFILE_STEP = "recommendation_profile"
RECOMMENDATION_PROFILE_SAVE_STEP = "recommendation_profile_save"
_RECOMMENDATION_PROFILE_VALUE_PREFIX = "rec_profile:"
_BRAND_RECOMMENDATION_PROFILE_VALUE = "__brand_profile__"
_NEW_RECOMMENDATION_PROFILE_VALUE = "__fill_new_recommendation_profile__"
_SAVE_RECOMMENDATION_PROFILE_VALUE = "保存为本次推荐画像"
_SKIP_RECOMMENDATION_PROFILE_SAVE_VALUE = "本次不保存"


def _lead_flow_has_recommendation_profile_context(flow: dict[str, object]) -> bool:
    return bool(
        _flow_list(flow, "seller_capabilities")
        or _flow_list(flow, "business_hint")
        or _flow_list(flow, "target_industries")
        or _flow_list(flow, "target_regions")
        or str(flow.get("target_company_size") or "").strip()
    )


def _lead_flow_ready_for_recommendation_profile_save(flow: dict[str, object]) -> bool:
    return bool(
        (_flow_list(flow, "seller_capabilities") or _flow_list(flow, "business_hint"))
        and _flow_list(flow, "target_industries")
        and _flow_list(flow, "target_regions")
        and str(flow.get("target_company_size") or "").strip()
    )


def _recommendation_profile_label(profile: dict[str, Any]) -> str:
    name = str(profile.get("name") or "").strip() or "未命名推荐画像"
    return f"本次推荐画像:{name}"


def _recommendation_profile_desc(profile: dict[str, Any]) -> str:
    summary = str(profile.get("summary") or "").strip()
    if summary:
        return summary
    parts: list[str] = []
    industries = "、".join(_clean_list(profile.get("target_industries"), 3))
    regions = "、".join(_clean_list(profile.get("target_regions"), 3))
    size = str(profile.get("target_company_size") or "").strip()
    if industries:
        parts.append(f"行业:{industries}")
    if regions:
        parts.append(f"地区:{regions}")
    if size:
        parts.append(f"规模:{size}")
    return "；".join(parts) or "聊天中保存的找客户配置。"


def _brand_recommendation_profile_source(deps) -> dict[str, Any] | None:
    if _brand_is_ambiguous(deps):
        return None
    try:
        profile = seller_profile_for_lead_entry(deps.db, deps.user_id, _active_seller_profile_id(deps))
    except Exception:  # noqa: BLE001
        return None
    has_profile_values = bool(profile.get("service_capabilities") or profile.get("service_scope") or profile.get("company_size") or profile.get("typical_customers"))
    if not has_profile_values:
        return None
    brand_name = str(profile.get("brand_name") or "").strip()
    capabilities = "、".join(_clean_list(profile.get("service_capabilities"), 3))
    service_scope = "、".join(_clean_list(profile.get("service_scope"), 3))
    size = str(profile.get("company_size") or "").strip()
    desc_parts = []
    if capabilities:
        desc_parts.append(f"能力:{capabilities}")
    if service_scope:
        desc_parts.append(f"服务方向:{service_scope}")
    if size:
        desc_parts.append(size)
    return {
        "kind": "brand_profile",
        "label": f"品牌画像:{brand_name}" if brand_name else "品牌画像",
        "value": _BRAND_RECOMMENDATION_PROFILE_VALUE,
        "desc": "；".join(desc_parts) or "来自主题/品牌资料,选择只影响本次推荐流程。",
        "seller_profile_id": int(profile.get("brand_id") or 0) or None,
    }


def _recommendation_profile_sources(deps) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    brand_source = _brand_recommendation_profile_source(deps)
    if brand_source is not None:
        sources.append(brand_source)
    try:
        profiles = list_active_recommendation_profiles(
            deps.db,
            deps.user_id,
            _active_seller_profile_id(deps),
            include_unbound=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("List Sales Agent recommendation profiles failed")
        profiles = []
    for profile in profiles:
        key = str(profile.get("profile_key") or "").strip()
        version_id = int(profile.get("id") or 0)
        if not key or not version_id:
            continue
        sources.append({
            "kind": "recommendation_profile",
            "label": _recommendation_profile_label(profile),
            "value": f"{_RECOMMENDATION_PROFILE_VALUE_PREFIX}{key}:{version_id}",
            "desc": _recommendation_profile_desc(profile),
            "profile": profile,
        })
    return sources


def _build_recommendation_profile_picker_card(sources: list[dict[str, Any]], *, light_confirm: bool = False) -> dict[str, Any]:
    question = "这次按哪套画像找客户?" if not light_confirm else "要使用已保存的推荐画像吗?"
    helper = "选择只影响本次推荐,不会修改品牌资料。"
    options = [
        {
            "label": str(source.get("label") or ""),
            "value": str(source.get("value") or ""),
            "desc": str(source.get("desc") or ""),
        }
        for source in sources
        if str(source.get("label") or "").strip() and str(source.get("value") or "").strip()
    ]
    if light_confirm:
        options.append({"label": "重新填写", "value": _NEW_RECOMMENDATION_PROFILE_VALUE, "desc": "不用已保存画像,重新从业务方向开始补充。"})
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "你有多份可用于推荐的画像,先确认这次按哪份找客户。" if not light_confirm else "已找到一份聊天保存的推荐画像,可以直接使用,也可以重新填写。",
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": "sales_agent_recommendation_profile_picker",
            "field": "recommendation_profile_key",
            "title": "选择推荐画像" if not light_confirm else "使用已保存推荐画像",
            "helper_text": helper,
            "type": "single_select",
            "layout": "vertical",
            "options": options,
            "allow_text_fallback": True,
            "can_go_back": False,
        },
        "missing_items": ["本次推荐画像"],
        "recommendation_profiles": [source.get("profile") for source in sources if isinstance(source.get("profile"), dict)],
        "leads": [],
        "count": 0,
    }
    return build_guidance_card_from_result("recommend_companies", data)


def _append_recommendation_profile_picker_guidance(deps, intent: str) -> bool:
    flow = _lead_flow_state(deps)
    if str(flow.get("recommendation_profile_key") or "").strip() or str(flow.get("recommendation_profile_source") or "").strip():
        return False
    # When the user has manually given filter context this turn, don't overwrite this turn's input with the historical profile.
    if _lead_flow_has_recommendation_profile_context(flow):
        return False
    sources = _recommendation_profile_sources(deps)
    if len(sources) >= 2:
        flow["step"] = RECOMMENDATION_PROFILE_STEP
        _save_lead_flow_state(deps)
        card = _build_recommendation_profile_picker_card(sources)
        append_chat_message(deps, "assistant", "你现在有多份可用于推荐的画像,先确认这次按哪份找客户。", intent=intent, metadata={"tool_cards": [card]})
        _record_visible_card_checkpoint(deps, RECOMMENDATION_PROFILE_STEP, flow)
        return True
    if len(sources) == 1 and sources[0].get("kind") == "recommendation_profile":
        flow["step"] = RECOMMENDATION_PROFILE_STEP
        _save_lead_flow_state(deps)
        card = _build_recommendation_profile_picker_card(sources, light_confirm=True)
        append_chat_message(deps, "assistant", "已找到一份聊天保存的推荐画像,你可以直接使用,也可以重新填写。", intent=intent, metadata={"tool_cards": [card]})
        _record_visible_card_checkpoint(deps, RECOMMENDATION_PROFILE_STEP, flow)
        return True
    return False


def _parse_recommendation_profile_choice(message: str) -> str:
    parts = _list_from_text(message)
    return str(parts[0] if parts else message or "").strip()


def _is_recommendation_profile_choice_answer(message: str) -> bool:
    choice = _parse_recommendation_profile_choice(message)
    return bool(
        choice == _BRAND_RECOMMENDATION_PROFILE_VALUE
        or choice == _NEW_RECOMMENDATION_PROFILE_VALUE
        or choice.startswith(_RECOMMENDATION_PROFILE_VALUE_PREFIX)
        or choice.startswith("品牌画像:")
        or choice.startswith("本次推荐画像:")
    )


def _is_save_recommendation_profile_action(message: str) -> bool:
    normalized = _normalized_action_text(message)
    return normalized in {"保存为本次推荐画像", "保存本次推荐画像", "保存推荐画像", "保存画像"} or "保存为本次推荐画像" in normalized


def _is_skip_recommendation_profile_save_action(message: str) -> bool:
    normalized = _normalized_action_text(message)
    return normalized in {"本次不保存", "不保存", "不用保存", "直接推荐", "跳过保存"}


def _build_recommendation_profile_save_card(flow: dict[str, object]) -> dict[str, Any]:
    summary_parts: list[str] = []
    industries = "、".join(_flow_list(flow, "target_industries")[:3])
    regions = "、".join(_flow_list(flow, "target_regions")[:3])
    size = str(flow.get("target_company_size") or "").strip()
    if industries:
        summary_parts.append(f"行业:{industries}")
    if regions:
        summary_parts.append(f"地区:{regions}")
    if size:
        summary_parts.append(f"规模:{size}")
    question = "要把这次已确认的推荐条件保存下来吗?"
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "本次推荐已经生成。你可以把这套找客户配置保存为推荐画像,下次继续使用。",
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": "sales_agent_recommendation_profile_save",
            "field": "recommendation_profile_save_action",
            "title": "保存本次推荐画像",
            "helper_text": "保存的是推荐画像,不是品牌画像;不会修改主题资料。" + (f" 本次条件:{'；'.join(summary_parts)}" if summary_parts else ""),
            "type": "single_select",
            "layout": "vertical",
            "options": [
                {"label": "保存为本次推荐画像", "value": _SAVE_RECOMMENDATION_PROFILE_VALUE, "desc": "下次找客户时可选择并回显行业、地区和规模。"},
                {"label": "本次不保存", "value": _SKIP_RECOMMENDATION_PROFILE_SAVE_VALUE, "desc": "只保留本轮推荐结果,不新增推荐画像。"},
            ],
            "allow_text_fallback": False,
            "can_go_back": False,
        },
        "missing_items": ["是否保存推荐画像"],
        "leads": [],
        "count": 0,
    }
    return build_guidance_card_from_result("recommend_companies", data)


def _save_current_recommendation_profile(deps, intent: str, name: str = "") -> bool:
    flow = _lead_flow_state(deps)
    if not _lead_flow_ready_for_recommendation_profile_save(flow):
        append_chat_message(deps, "assistant", "请先确认服务能力/业务方向、目标行业、目标地区和目标企业规模,再保存为本次推荐画像。", intent=intent)
        return True
    seller_profile_id = _active_seller_profile_id(deps)
    brand_name = ""
    try:
        profile = seller_profile_for_lead_entry(deps.db, deps.user_id, seller_profile_id)
        brand_name = str(profile.get("brand_name") or "").strip()
    except Exception:  # noqa: BLE001
        brand_name = ""
    try:
        row = create_recommendation_profile_from_flow(
            deps.db,
            deps.user_id,
            flow,
            name=name,
            seller_profile_id=seller_profile_id,
            brand_name_snapshot=brand_name,
            commit=False,
        )
        campaign_id = int(flow.get("last_recommendation_campaign_id") or 0)
        if campaign_id:
            record_recommendation_profile_usage(
                deps.db,
                deps.user_id,
                row.profile_key,
                row.id,
                session_id=deps.session_id,
                campaign_id=campaign_id,
                commit=False,
            )
        deps.db.commit()
        deps.db.refresh(row)
    except SalesAgentError as e:
        deps.db.rollback()
        append_chat_message(deps, "assistant", _public_error_message(e), intent=intent)
        return True
    except Exception as e:  # noqa: BLE001
        deps.db.rollback()
        logger.exception("Save Sales Agent recommendation profile failed")
        write_model_call_log(
            request_id=new_model_call_id(), event="save_recommendation_profile", status="failed", scene="lead",
            user_id=deps.user_id, session_public_id=deps.session_public_id, intent=intent,
            message=str(flow), error=e,
        )
        append_chat_message(deps, "assistant", "本次推荐画像保存失败了,但不会影响刚才的推荐结果。请稍后再点保存,或重新确认目标行业、地区和规模后再保存。", intent=intent)
        return True
    flow["recommendation_profile_key"] = row.profile_key
    flow["recommendation_profile_version_id"] = row.id
    flow["recommendation_profile_saved_key"] = row.profile_key
    flow["step"] = ""
    _save_lead_flow_state(deps)
    saved = serialize_recommendation_profile_version(row) or {}
    append_chat_message(
        deps,
        "assistant",
        f"已保存为本次推荐画像“{saved.get('name') or row.name}”。这不是品牌画像,不会修改主题资料;下次找客户时可以选择它,目标行业、目标地区和目标企业规模会回显。",
        intent=intent,
    )
    return True


def _handle_recommendation_profile_choice(deps, message: str, intent: str) -> bool:
    choice = _parse_recommendation_profile_choice(message)
    sources = _recommendation_profile_sources(deps)
    if choice.startswith("品牌画像:"):
        choice = _BRAND_RECOMMENDATION_PROFILE_VALUE
    elif choice.startswith("本次推荐画像:"):
        matched = next((source for source in sources if source.get("label") == choice), None)
        if matched:
            choice = str(matched.get("value") or choice)
    flow = _lead_flow_state(deps)
    if choice == _NEW_RECOMMENDATION_PROFILE_VALUE:
        for key in ("recommendation_profile_key", "recommendation_profile_version_id", "recommendation_profile_source", "recommendation_profile_saved_key"):
            flow.pop(key, None)
        for key in ("seller_capabilities", "service_scope", "seller_team", "seller_team_raw", "typical_customers", "target_company_size", "target_industries", "target_regions", "business_hint", "profile_suggestions"):
            flow.pop(key, None)
        flow["step"] = ""
        _save_lead_flow_state(deps)
        _append_business_hint_guidance(deps, intent)
        return True
    if choice == _BRAND_RECOMMENDATION_PROFILE_VALUE:
        flow["recommendation_profile_source"] = "brand_profile"
        flow["step"] = ""
        _save_lead_flow_state(deps)
        return _append_brand_profile_entry(deps)
    if choice.startswith(_RECOMMENDATION_PROFILE_VALUE_PREFIX):
        append_chat_message(
            deps,
            "assistant",
            "现在推荐企业不再使用“推荐画像”选择卡。请直接确认我方画像,再选择本次目标行业、地区和企业规模。",
            intent=intent,
        )
        flow["step"] = ""
        _save_lead_flow_state(deps)
        return True
    append_chat_message(deps, "assistant", "现在推荐企业不再使用“推荐画像”选择卡。请重新发起推荐企业,我会从我方画像开始确认。", intent=intent)
    flow["step"] = ""
    _save_lead_flow_state(deps)
    return True


def _handle_recommendation_profile_save_choice(deps, message: str, intent: str) -> bool:
    if _is_save_recommendation_profile_action(message) or _is_skip_recommendation_profile_save_action(message):
        # Compatibility with already-issued historical cards. "Profile" now only means “我方画像” ("our profile"): capabilities, service scope,
        # company size and ideal customers, which are saved automatically in the earlier profile confirmation step. Target industry, region and
        # target size are only this run's company filter conditions; they go with the campaign record and are no longer saved separately as a profile.
        flow = _lead_flow_state(deps)
        flow["step"] = ""
        _save_lead_flow_state(deps)
        append_chat_message(
            deps,
            "assistant",
            "画像已在你确认专业能力、服务范围、企业规模和适合客户时自动保存;目标行业、地区和目标规模属于本次推荐企业条件,已随推荐记录保存,不用单独保存画像。",
            intent=intent,
        )
        return True
    return False


def _catalog_options(deps) -> dict[str, list[dict]]:
    """Five kinds of card options. guidance.py doesn't touch the db; they're loaded here and injected."""
    return load_card_options(deps.db, deps.user_id, _active_seller_profile_id(deps))


def _slot_catalog(deps) -> SlotCatalog:
    """Extraction word list. It used to be a module constant evaluated at import time, so custom items never got in —
    a user entering "orthodontics" via 「自定义行业」 ("custom industry") was never recognized when mentioning it again."""
    return build_slot_catalog(deps.db, deps.user_id, _active_seller_profile_id(deps))


def _target_list_echo_values(raw: object) -> list[str]:
    """campaign/ICP pass JSON text, recommendation profile snapshots already pass a list; accept both."""
    if isinstance(raw, (list, tuple)):
        return _clean_list(list(raw), 30)
    try:
        values = json.loads(str(raw or "[]"))
    except Exception:  # noqa: BLE001
        values = []
    return _clean_list(values, 30)


def _target_industry_echo_values(raw: object) -> list[str]:
    """Old recommendations/ICP may have stored the “自定义行业” ("custom industry") action word; don't add it back to cards when echoing."""
    return [item for item in _target_list_echo_values(raw) if item not in CUSTOM_INDUSTRY_TOKENS]


def _latest_campaign_for_echo(deps):
    seller_profile_id = _active_seller_profile_id(deps)
    try:
        q = deps.db.query(SalesAgentRecommendationCampaignORM).filter(
            SalesAgentRecommendationCampaignORM.user_id == deps.user_id,
        )
        if seller_profile_id is not None:
            q = q.filter(SalesAgentRecommendationCampaignORM.seller_profile_id == seller_profile_id)
        return q.order_by(SalesAgentRecommendationCampaignORM.id.desc()).first()
    except Exception:  # noqa: BLE001
        return None


def _latest_icp_for_echo(deps):
    try:
        return _latest_account_icp(deps.db, deps.user_id, seller_profile_id=_active_seller_profile_id(deps))
    except Exception:  # noqa: BLE001
        return None


def _latest_recommendation_profile_for_echo(deps) -> dict[str, object] | None:
    """The current brand's active recommendation profile; when the three condition cards echo values it ranks before the latest campaign.

    The profile stores "what conditions this brand currently uses to find customers", closer to user intent than the latest campaign —
    a campaign is just one round's execution snapshot. The brand metric definition is exactly the same as at upsert time (the same
    `_brand_ref_for_seller`), so "written to A but echoed from B" can't happen.
    """
    try:
        ref_type, ref_key = _brand_ref_for_seller(
            _active_seller_profile_id(deps),
            RECOMMENDATION_PROFILE_BRAND_REF_NONE,
            "",
        )
        rows = list_active_recommendation_profiles(
            deps.db,
            deps.user_id,
            brand_ref_type=ref_type,
            brand_ref_key=ref_key,
            limit=1,
        )
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None


def _latest_target_company_size_for_echo(deps) -> str:
    """Default selection for a new round of the target company size card: current session first, otherwise echo the recommendation profile/latest recommendation/ICP."""
    flow = _lead_flow_state(deps)
    current = str(flow.get("target_company_size") or "").strip()
    if current:
        return current
    profile = _latest_recommendation_profile_for_echo(deps)
    if profile is not None and str(profile.get("target_company_size") or "").strip():
        return str(profile.get("target_company_size") or "").strip()
    campaign = _latest_campaign_for_echo(deps)
    if campaign is not None and str(campaign.target_company_size or "").strip():
        return str(campaign.target_company_size or "").strip()
    icp = _latest_icp_for_echo(deps)
    return str(getattr(icp, "target_company_size", "") or "").strip() if icp is not None else ""


def _latest_target_industries_for_echo(deps) -> list[str]:
    """Default selection for a new round of the target industry card: current session first, otherwise echo the recommendation profile/latest recommendation/ICP."""
    flow = _lead_flow_state(deps)
    current = [item for item in _flow_list(flow, "target_industries") if item not in CUSTOM_INDUSTRY_TOKENS]
    if current:
        return current
    profile = _latest_recommendation_profile_for_echo(deps)
    if profile is not None:
        values = _target_industry_echo_values(profile.get("target_industries"))
        if values:
            return values
    campaign = _latest_campaign_for_echo(deps)
    if campaign is not None:
        values = _target_industry_echo_values(campaign.target_industries_json)
        if values:
            return values
    icp = _latest_icp_for_echo(deps)
    return _target_industry_echo_values(icp.target_industries_json) if icp is not None else []


def _latest_target_regions_for_echo(deps) -> list[str]:
    """Default selection for a new round of the target region card: current session first, otherwise echo the recommendation profile/latest recommendation/ICP."""
    flow = _lead_flow_state(deps)
    current = _flow_list(flow, "target_regions")
    if current:
        return current
    profile = _latest_recommendation_profile_for_echo(deps)
    if profile is not None:
        values = _target_list_echo_values(profile.get("target_regions"))
        if values:
            return values
    campaign = _latest_campaign_for_echo(deps)
    if campaign is not None:
        values = _target_list_echo_values(campaign.target_regions_json)
        if values:
            return values
    icp = _latest_icp_for_echo(deps)
    return _target_list_echo_values(icp.target_regions_json) if icp is not None else []


def _looks_like_lead_filter_request(deps, message: str) -> bool:
    """With no pending card, "filter conditions + N companies" should also count as a new customer search, not be caught by lookup.

    Users often reply directly 「工业制造、一线城市、北京、上海、100家」 ("industrial manufacturing, tier-1 cities, Beijing, Shanghai, 100 companies"), which isn't a history lookup;
    but without an action word like 「推荐/找客户」 ("recommend/find customers"), the intent classifier conservatively falls to lookup.
    This is tightened to recognized target filter conditions + an explicit count, so history lookups like 「推荐过哪些公司」 ("which companies were recommended") don't trigger it.
    """
    slots = extract_slots(message, _slot_catalog(deps))
    if not slots.get("requested_limit"):
        return False
    return any(slots.get(slot) for slot in ("target_industries", "target_regions", "target_company_size"))


# Slot name -> which guide card to issue. The router only computes "what's next"; presentation stays here.
_STEP_CARDS = {
    BRAND: _append_brand_picker_guidance,
    RECOMMENDATION_PROFILE_STEP: _append_recommendation_profile_picker_guidance,
    "business_hint": _append_business_hint_guidance,
    "seller_capabilities": _append_lead_seller_capabilities_guidance,
    "service_scope": _append_lead_service_scope_guidance,
    "typical_customers": _append_lead_typical_customers_guidance,
    "target_company_size": _append_lead_target_size_guidance,
    "target_industries": _append_campaign_industry_guidance,
    "target_regions": _append_campaign_region_guidance,
    CUSTOM_INDUSTRY: _append_custom_industry_guidance,
    "seller_profile_summary": _append_seller_profile_summary_card,
}

_BACK_STEP_TOKENS = {"返回上一步", "上一步", "返回", "退回上一步"}
_UNDO_TOKENS = {"撤销", "undo", "Undo", "UNDO"}
_STEP_LABELS = {
    "sales_agent_entry_action": "选择操作",
    BRAND: "品牌选择",
    RECOMMENDATION_PROFILE_STEP: "推荐画像选择",
    RECOMMENDATION_PROFILE_SAVE_STEP: "推荐画像保存",
    "business_hint": "业务方向",
    "seller_profile": "我方画像",
    "seller_capabilities": "我方画像",
    "service_scope": "服务方向",
    "custom_capability": "自定义能力",
    "seller_profile_summary": "我方画像确认",
    "typical_customers": "适合客户",
    "target_company_size": "目标企业规模",
    "target_industries": "目标行业",
    CUSTOM_INDUSTRY: "自定义行业",
    "target_regions": "目标地区",
}
_STEP_BACK_TARGET = {
    "business_hint": BRAND,
    "seller_profile": BRAND,
    "seller_capabilities": BRAND,
    "service_scope": "seller_capabilities",
    "custom_capability": "seller_capabilities",
    "typical_customers": "service_scope",
    "target_company_size": "typical_customers",
    "target_industries": "target_company_size",
    CUSTOM_INDUSTRY: "target_industries",
    "target_regions": "target_industries",
}


def _is_back_step_action(message: str) -> bool:
    return str(message or "").strip() in _BACK_STEP_TOKENS


def _is_undo_action(message: str) -> bool:
    return str(message or "").strip() in _UNDO_TOKENS


def _record_current_step_checkpoint(deps, flow: dict[str, object], step: str) -> None:
    record_lead_flow_checkpoint(deps, flow, step, _STEP_LABELS.get(step, step))


def _record_visible_card_checkpoint(deps, step: str, flow: dict[str, object] | None = None) -> None:
    checkpoint_flow = dict(flow or {})
    if step != "sales_agent_entry_action" and step and not checkpoint_flow.get("step"):
        checkpoint_flow["step"] = step
    record_lead_flow_checkpoint(deps, checkpoint_flow, step, _STEP_LABELS.get(step, step))


def _restore_undo_and_append_card(deps, intent: str = "lead", silent_noop: bool = False, checkpoint_id: int | None = None) -> dict[str, Any]:
    restored = restore_lead_flow_checkpoint(deps, checkpoint_id)
    if not restored.get("restored"):
        if not silent_noop:
            append_chat_message(deps, "assistant", str(restored.get("message") or "当前没有可撤销步骤。"), intent=intent)
        return restored
    # The assistant card at the checkpoint watermark is kept in the messages; after undo it becomes the latest editable card again.
    # Don't append a duplicate card, otherwise there'd be an extra item below the card the user selected.
    if int(restored.get("message_watermark_id") or 0) > 0:
        return restored
    step = str(restored.get("step") or "")
    if not _append_step_card(deps, step, intent):
        append_chat_message(deps, "assistant", "已撤销到这一步,可以继续编辑。", intent=intent)
    return restored


def _handle_undo_action(deps, intent: str = "lead") -> bool:
    _restore_undo_and_append_card(deps, intent)
    return True


def _handle_back_or_undo_action(deps, flow: dict[str, object], step: str, intent: str) -> bool:
    restored = _restore_undo_and_append_card(deps, intent, silent_noop=True)
    if restored.get("restored"):
        return True
    # The old "go back a step" without a checkpoint still falls back by static step, for compatibility with historical tests and old sessions.
    return _handle_back_step_action(deps, flow, step, intent)


def _step_slot(step: str) -> str:
    if step in {"seller_profile", "custom_capability"}:
        return "seller_capabilities"
    return "seller_capabilities" if step == "business_hint" else step


def _clear_flow_after_step(flow: dict[str, object], step: str, *, include_current: bool = False) -> None:
    if step == BRAND:
        flow.pop("brand_id", None)
        slots_to_clear = list(SLOT_ORDER)
    elif step == "business_hint":
        slots_to_clear = ["seller_capabilities", "service_scope", "typical_customers"]
        flow.pop("profile_suggestions", None)
    else:
        slot = _step_slot(step)
        try:
            index = SLOT_ORDER.index(slot)
        except ValueError:
            slots_to_clear = []
        else:
            # When returning to a step, echo the values chosen last time at that step, so by default only later slots are cleared.
            # To really reset the current step, pass include_current=True explicitly.
            start = index if include_current else index + 1
            slots_to_clear = list(SLOT_ORDER[start:])
    for slot in slots_to_clear:
        flow.pop(slot, None)
    if "seller_capabilities" in slots_to_clear:
        flow.pop("service_scope", None)
        flow.pop("seller_team", None)
        flow.pop("seller_team_raw", None)
    flow.pop("requested_limit", None)


def _append_step_card(deps, step: str, intent: str) -> bool:
    card = _STEP_CARDS.get(step)
    if card is None:
        return False
    return bool(card(deps, intent)) if step == BRAND else (card(deps, intent) or True)


def _handle_back_step_action(deps, flow: dict[str, object], step: str, intent: str) -> bool:
    target = _STEP_BACK_TARGET.get(step)
    if step in {"seller_profile", "seller_capabilities"} and _flow_list(flow, "business_hint"):
        target = "business_hint"
    if target == BRAND and not _brand_required(deps):
        target = ""
    if not target and step == "seller_profile_summary" and _brand_required(deps):
        target = BRAND
    if not target:
        append_chat_message(deps, "assistant", "已经是当前流程的第一步。", intent=intent)
        return True
    _clear_flow_after_step(flow, target)
    flow["step"] = target
    _save_lead_flow_state(deps)
    if not _append_step_card(deps, target, intent):
        append_chat_message(deps, "assistant", "已经是当前流程的第一步。", intent=intent)
    return True


# Model-issued question cards -> which step of the deterministic flow.
#
# When the model calls `recommend_companies` and gets need_more_info, it issues a question_card, which doesn't write
# `flow["step"]`; so the next answer has "no step to match" in the deterministic flow, hits the read-only gate, is treated as a new question
# and handed back to the model (production, 2026-08-26: 「企业服务」 ("business services") and 「全国、20家」 ("nationwide, 20 companies") were both judged chat and went to the model,
# and the last round fed in a 510k-character tool return, exhausting the 60s wall clock).
# Card id / field already share names with slots, so just restore by them; no need for the model to judge again.
_QUESTION_CARD_STEPS = {
    "sales_agent_brand_picker": BRAND,
    "sales_agent_recommendation_profile_picker": RECOMMENDATION_PROFILE_STEP,
    "sales_agent_recommendation_profile_save": RECOMMENDATION_PROFILE_SAVE_STEP,
    "seller_business_hint": "business_hint",
    "seller_profile": "seller_capabilities",
    "seller_service_scope": "service_scope",
    "seller_custom_capability": "custom_capability",
    "seller_typical_customers": "typical_customers",
    "campaign_target_company_size": "target_company_size",
    "campaign_target_industries": "target_industries",
    "campaign_target_industries_custom": CUSTOM_INDUSTRY,
    "campaign_target_regions": "target_regions",
}
_QUESTION_CARD_FIELD_STEPS = {
    "brand_id": BRAND,
    "recommendation_profile_key": RECOMMENDATION_PROFILE_STEP,
    "recommendation_profile_save_action": RECOMMENDATION_PROFILE_SAVE_STEP,
    "business_hint": "business_hint",
    "seller_capabilities": "seller_capabilities",
    "service_scope": "service_scope",
    "company_size": "seller_capabilities",
    "typical_customers": "typical_customers",
    "target_company_size": "target_company_size",
    "target_industries": "target_industries",
    "target_regions": "target_regions",
}


def _pending_question_step(deps, message: str = "") -> str:
    """The flow step corresponding to the latest pending card.

    Usually the previous assistant message suffices; but when the user clicks rapidly, by the time the second request arrives the previous assistant message
    may not have been replaced with the new card yet, while flow["step"] has already advanced. Then the old card must be filtered out by the answer content:
    「不限地区、100家」 ("any region, 100 companies") is obviously not a target industry answer and must not be eaten by the old industry card.
    """
    _, meta = _last_assistant_reply(deps)
    cards = meta.get("tool_cards") if isinstance(meta, dict) else None
    if not isinstance(cards, list):
        return ""
    for card in reversed(cards):
        data = card.get("data") if isinstance(card, dict) else None
        if not isinstance(data, dict):
            continue
        if not (data.get("needs_user_input") or data.get("status") == "need_more_info"):
            continue
        question_card = data.get("question_card")
        if not isinstance(question_card, dict):
            continue
        step = _QUESTION_CARD_STEPS.get(str(question_card.get("id") or "")) \
            or _QUESTION_CARD_FIELD_STEPS.get(str(question_card.get("field") or ""))
        if step and _message_can_answer_pending_step(step, message):
            return step
    return ""


def _message_can_answer_pending_step(step: str, message: str = "") -> bool:
    if not message:
        return True
    text = str(message or "").strip()
    if not text:
        return True
    if verify_slot_answer(step, text):
        return False
    if step in {"target_industries", CUSTOM_INDUSTRY}:
        parts = _list_from_text(re.sub(r"(?:推荐)?\s*\d+\s*家(?:企业|公司)?", "", text).strip(" 、,，;；/-"))
        if any(part in NO_LIMIT_REGION_TOKENS for part in parts):
            return False
    return True


def _implicit_step_from_flow_answer(deps, flow: dict[str, object], message: str) -> str:
    """When the pending card is lost, restore the current step from the standard card answer itself.

    In production, occasionally the frontend card already shows “已提交:11-100 名员工” ("submitted: 11-100 employees"), but the backend didn't get
    `flow["step"]` / the latest question_card metadata this turn, so the size bucket was treated as an ordinary chat/unclear sentence.
    Only explicit card option shapes are accepted here, not questions, to avoid forcing ordinary chat into the flow.
    """
    if verify_slot_answer("implicit", message):
        return ""
    if _recommendation_limit_from_text(message):
        return ""
    if _seller_team_bucket_from_text(message) and _flow_list(flow, "seller_capabilities") and not str(flow.get("seller_team") or "").strip():
        return "seller_capabilities"
    if _line_list_from_text(message) and _flow_list(flow, "seller_capabilities") and not _flow_list(flow, "service_scope"):
        return "service_scope"
    if _typical_customers_from_text(message) and (_flow_list(flow, "seller_capabilities") or _flow_list(flow, "business_hint")) and not _flow_list(flow, "typical_customers"):
        return "typical_customers"
    if _target_size_from_text(message) and (_flow_list(flow, "typical_customers") or _flow_list(flow, "seller_capabilities")) and not str(flow.get("target_company_size") or "").strip():
        return "target_company_size"
    if _target_industries_from_text(message) and str(flow.get("target_company_size") or "").strip() and not _flow_list(flow, "target_industries"):
        return CUSTOM_INDUSTRY if str(flow.get("custom_industry_pending") or "").strip() else "target_industries"
    if _target_regions_from_text(message) and _flow_list(flow, "target_industries") and not _flow_list(flow, "target_regions"):
        return "target_regions"
    return ""


def _slot_from_step(step: str, message: str, brands: list[dict] | None = None) -> dict:
    """Which slot the current step should recognize. Free input the word list can't recognize (custom industries, regions not in the options) relies on it as fallback.

    `brands` is injected by the caller (this function doesn't touch the db); only the brand step uses it.
    """
    if step == BRAND:
        return _brand_slot(message, brands)
    if step == "business_hint":
        hints = [part for part in _list_from_text(message) if part.strip() not in FLOW_ACTION_TOKENS]
        return {"business_hint": hints} if hints else {}
    if step in {"seller_profile", "seller_capabilities", "custom_capability"}:
        parts = _list_from_text(message)
        team = next((_seller_team_bucket_from_text(part) for part in parts if _seller_team_bucket_from_text(part)), "")
        found: dict = {}
        # 「自定义能力」 ("custom capability") is the entry for "I'll write my own", not a capability; don't store it in the profile.
        # No length trimming here: parsed capabilities are written back two-way to the brand material's 「服务核心特点」 ("core service features"),
        # and what goes into the material must be the user's original words. Control length by trimming on the display side.
        capabilities = _clean_profile_option_list(
            [part for part in parts if not _seller_team_bucket_from_text(part) and part.strip() not in CUSTOM_CAPABILITY_TOKENS and part.strip() not in FLOW_ACTION_TOKENS],
            30,
        )
        if capabilities:
            found["seller_capabilities"] = capabilities
        if team:
            found["seller_team"] = team
        return found
    if step == "service_scope":
        scopes = _clean_profile_option_list(
            [part for part in _list_from_text(message) if part.strip() not in FLOW_ACTION_TOKENS],
            20,
        )
        return {"service_scope": scopes} if scopes else {}
    if step in {"seller_team", "seller_team_size"}:
        team = _seller_team_from_text(message)
        return {"seller_team": team} if team else {}
    if step == "typical_customers":
        customers = [c for c in _typical_customers_from_text(message) if c.strip() not in FLOW_ACTION_TOKENS]
        return {"typical_customers": customers} if customers else {}
    if step == "target_company_size":
        size = _target_size_from_text(message)
        return {"target_company_size": size} if size else {}
    if step in {"target_industries", CUSTOM_INDUSTRY}:
        found = _limit_slot(message)
        industries = _target_industries_from_text(message)
        if industries:
            found["target_industries"] = industries
        return found
    if step == "target_regions":
        found = _limit_slot(message)
        regions = _target_regions_from_text(message)
        if regions:
            found["target_regions"] = regions
        return found
    return {}


def _limit_slot(message: str) -> dict:
    limit = _recommendation_limit_from_text(message)
    return {"requested_limit": limit} if limit else {}


_CUSTOM_OPTION_SLOTS = (
    ("seller_capabilities", CAPABILITY),
    ("typical_customers", TYPICAL_CUSTOMER),
    ("target_industries", INDUSTRY),
    ("target_regions", REGION),
)


def _persist_custom_options(deps, flow: dict, changed: list[str]) -> None:
    """Add user-written values to the option catalog — next time the card opens, it's among the options.

    This is where S-33 / S-34 land: custom capabilities used to go only into that profile row's
    `service_capabilities_json`, a value on that profile, **never becoming a selectable option next time**.

    Only items not among system presets are accepted; the check is done in `add_custom_option`, not repeated here.
    Must be called **after** the profile is persisted — on the first turn the profile row doesn't exist yet and seller_profile_id isn't available.
    """
    slots = [(slot, kind) for slot, kind in _CUSTOM_OPTION_SLOTS if slot in changed]
    if not slots:
        return
    brand_id = _active_seller_profile_id(deps)
    if brand_id is None:
        return
    for slot, kind in slots:
        add_custom_options(deps.db, kind, deps.user_id, brand_id, _flow_list(flow, slot))


async def _advance_lead_flow(deps, flow: dict, state: LeadState, changed: list[str]) -> bool:
    """Persist slots -> compute next step -> issue card. Returns whether this turn was handled."""
    state.to_flow(flow)
    changed_slots = set(changed)
    if {"seller_capabilities", "service_scope", "seller_team", "typical_customers"} & changed_slots:
        _persist_lead_seller_profile_snapshot(
            deps,
            capabilities=_flow_list(flow, "seller_capabilities") if "seller_capabilities" in changed_slots else None,
            company_size=str(flow.get("seller_team") or "") if "seller_team" in changed_slots else "",
            typical_customers=_flow_list(flow, "typical_customers") if "typical_customers" in changed_slots else None,
            service_scope=_flow_list(flow, "service_scope") if "service_scope" in changed_slots else None,
        )
    _persist_custom_options(deps, flow, changed)
    step = next_step(state)
    profile = _seller_profile_context(deps)
    if step == "seller_capabilities":
        if _needs_business_hint_first(flow, profile):
            step = "business_hint"
        elif _flow_list(flow, "business_hint"):
            cached = _cached_profile_suggestions(flow)
            if not any(cached.values()):
                flow["profile_suggestions"] = {"business_hint_key": "|".join(_flow_list(flow, "business_hint")), **cached}
    elif step == "typical_customers" and not _brand_typical_customer_options(profile):
        # Half-empty profile: capabilities/service scope exist but 「适合客户」 ("ideal customers") is empty; use the existing profile context to have AI generate customer type candidates.
        # This only adds card options; it writes neither the brand profile nor the topic profile.
        await _profile_suggestions_for_flow(deps, flow, profile, required="typical_customers")
    flow["step"] = step
    _save_lead_flow_state(deps)
    if step == READY:
        mapped_industries = await map_target_industries_to_available(deps.db, _flow_list(flow, "target_industries"))
        if mapped_industries:
            state.target_industries = mapped_industries
            flow["target_industries"] = mapped_industries
            _save_lead_flow_state(deps)
        checkpoint_flow = dict(flow)
        checkpoint_flow["step"] = "target_regions"
        _record_current_step_checkpoint(deps, checkpoint_flow, "target_regions")
        _append_lead_recommendations(deps, "lead")
        return True
    card = _STEP_CARDS.get(step)
    if card is None:
        return False
    card(deps, "lead")
    return True


def _append_send_schedule_batch_picker(deps, message: str, intent: str) -> bool:
    """「给这批企业下午三点发邮件」 ("email this batch of companies at 3pm") -> issue a batch selection card. Returns True if handled.

    **If either precondition fails, return False and leave it to the model**:

    - the time wasn't parsed into an absolute moment (`when.ok`) — vague times need a follow-up question, and `when_followup_text`'s
      four sentences are split by cause more finely than here; rewriting them would only be worse;
    - the current session has no batches at all — then 「这批」 ("this batch") most likely refers to some named companies, and popping an empty dialog
      would push the user into an interface where nothing can be selected.

    The card only has the time, batch numbers and company counts, **no company names**: the list is in the post-scheduling card and the right-side panel,
    and at the batch selection step the user only needs to judge "which batch, how many companies".
    """
    when = parse_when_text(message)
    if not when.ok or when.utc_at is None:
        return False
    batches = session_recommendation_batches(deps.db, deps.user_id, deps.session_id)
    rows = batches.get("batches") or []
    if not rows:
        return False

    # Only one rule remains on time: it can't be earlier than now. Say it clearly here instead of erroring after the user clicks confirm —
    # the frontend uses this sentence to disable 「确定」 ("OK"), consistent with the `<= now` check in `arm_send_schedule`.
    warning = ""
    if when.utc_at <= datetime.utcnow():
        warning = f"{describe_utc(when.utc_at)}已经过去了,挑一个以后的时间。"

    total_leads = sum(int(x.get("lead_count") or 0) for x in rows)
    payload = {
        "status": "need_user_choice",
        "needs_user_input": True,
        "when_text": describe_utc(when.utc_at),
        # **A bare Beijing-time string** (`2026-09-20T15:00`), not UTC. The frontend puts it as-is into
        # `<input type="datetime-local">` and sends it back as-is after editing; `_schedule_time_from_body`'s
        # convention for bare strings is to read them as Beijing time — sending a UTC string out here would be off by 8 hours after a round trip,
        # and not conspicuously so.
        "scheduled_at": to_local(when.utc_at).strftime("%Y-%m-%dT%H:%M"),
        # Also a bare Beijing-time string; the frontend puts it as-is into `<input min=...>` so the browser blocks past times first.
        "min_scheduled_at": to_local(datetime.utcnow()).strftime("%Y-%m-%dT%H:%M"),
        "matched_text": when.matched,
        "session_id": deps.session_public_id or "",
        "batches": rows,
        "default_campaign_id": batches.get("default_campaign_id") or 0,
        "total_batches": len(rows),
        "total_leads": total_leads,
        "time_warning": warning,
    }

    shown_when = describe_utc(when.utc_at)
    matched = (when.matched or "").strip()
    # The full time must be echoed back: 「下午3点**之前**」 ("**before** 3pm") gets parsed as 15:00, and 「三点」 ("three o'clock") is read as pm —
    # both heuristics rely on this echo as the safety net (first row of the design 4.7 boundary table).
    head = f"「{matched}」我按 {shown_when} 来排。" if matched else f"我按 {shown_when} 来排。"
    if len(rows) > 1:
        body = f"这个会话推荐过 {len(rows)} 批、一共 {total_leads} 家,选一下发哪一批(也可以全发)。"
    else:
        body = f"要发的是刚推荐的这 {total_leads} 家。"
    tail = warning or "点确定之前一封都不会发出去;确定之后到点逐封发,中途还能改时间或整个取消。"
    append_chat_message(
        deps, "assistant", f"{head}{body}{tail}",
        intent=intent,
        metadata={"tool_cards": [build_guidance_card_from_result("send_schedule_batch_picker", payload)]},
    )
    return True


async def _run_deterministic_flow(deps, message: str, intent: str) -> bool:
    """Deterministic flow: audit guidance / profile card / four filter cards / entry guidance.

    Returns True if this turn was handled (message already persisted), and callers wrap up in their own transport form; returns False to hand over to the model.
    Both conversation entries share it — previously only `/chat/a2ui` ran this, while `/chat` (SSE) went straight to the model,
    so the same product had two entries with two behaviors.
    """
    intent = normalize_intent(intent)
    coarse_intent = coarse_intent_for(intent)
    flow = _lead_flow_state(deps)
    step = str(flow["step"] or "") if "step" in flow else ""
    if _is_undo_action(message):
        return _handle_undo_action(deps, "lead")
    if _is_seller_profile_query(message):
        return _append_seller_profile_status_reply(deps, message, intent)

    # What the user actually sees is the card in the previous assistant message; the step in context_json is only a server-side cursor.
    # Restore the latest pending card before all read-only query / FAQ / stats branches, so card answers like 「11-100 名员工」 ("11-100 employees")
    # aren't hijacked early after the router judges them unclear/lookup/faq.
    pending_step = _pending_question_step(deps, message)
    if pending_step and pending_step != step:
        step = pending_step
        flow["step"] = step
        _save_lead_flow_state(deps)
    elif not step:
        # The previous turn's card was issued by the model and didn't write step; this sentence is still answering that card.
        step = pending_step
    if not step:
        implicit_step = _implicit_step_from_flow_answer(deps, flow, message)
        if implicit_step:
            step = implicit_step
            flow["step"] = step
            _save_lead_flow_state(deps)

    if not step and _append_seller_profile_status_reply(deps, message, intent):
        return True

    # 「推荐一批有官网的」 ("recommend a batch with websites") could be a filter request or an ideal-customer profile — silently treating it as the profile would recommend companies without websites.
    if _append_website_scope_clarify(deps, flow, message, intent, step):
        return True

    if intent == "compliance":
        try:
            record_compliance_event(deps.db, deps.user_id, "sales_agent_chat", "medium", "blocked", "用户请求不支持或存在合规风险", {"message": message})
        except Exception:  # noqa: BLE001
            deps.db.rollback()
        # 「把这批群发一下」 ("mass-send this batch") hits the same compliance red line, but it's not the same as 「帮我爬手机号」 ("scrape phone numbers for me"):
        # what the user wants to do is itself allowed; only the wording tripped a word. Blocking without guidance dead-ends people on a word they don't know
        # how to get around — so this branch must **offer a usable phrasing** (the 「群发」 ("mass send") row of the design 4.7 boundary table).
        if _is_mass_send_request(message):
            append_chat_message(
                deps, "assistant",
                "群发我不能做 —— 一次性把同一封信推给一批人,既过不了合规,也很容易被收件方判成垃圾邮件。"
                "但你要的效果可以换个做法:我帮你排一个到点逐封发的计划,每封单独生成、单独过合规检查,"
                "默认每封间隔 30 秒。你说个时间就行,比如“下午三点发”,我会先把能发的名单和跳过的原因列给你确认。",
                intent=intent,
            )
            return True
        append_chat_message(deps, "assistant", "这个动作我不能帮你执行。Sales Agent 不支持伪造报告、绕过平台限制、抓取私人联系方式、规避退订或批量群发;如果你要做合规获客,可以改成“推荐一批符合条件的企业”或“生成合规跟进邮件草稿”。", intent=intent)
        return True

    if intent == "unclear" and not step:
        append_chat_message(deps, "assistant", "这句话还不够明确。你是想查已发送邮件、查看历史推荐,还是重新推荐一批企业? 可以直接说“查今天发了几封邮件”“看昨天推荐过哪些企业”或“推荐 50 家工业制造企业”。", intent=intent)
        return True

    if coarse_intent == "faq" and not step:
        return False

    if not step and (should_use_guide_reply(message) or identity_or_reference_kind(message)) and not _assistant_message_seen(deps):
        _append_entry_guidance(deps, intent)
        return True
    if not step and identity_or_reference_kind(message):
        return _append_identity_or_reference_reply(deps, message, intent)

    # ③ Scheduled sending. **Must come before the delivery / recommendation query branches below**:
    # 「下午三点把这批邮件发一下」 ("send this batch of emails at 3pm") contains 「邮件」 ("email"), and `_explicit_stats_domain()` would judge it
    # "delivery", so an already-correctly-judged intent gets hijacked a second time in the deterministic flow; the user waits for scheduling
    # and receives a send-volume stats card. **Judging the intent right doesn't mean the flow goes right; these are two independent decisions.**
    #
    # When the time is clear and the session really has recommended batches, issue a 「发给哪一批?」 ("send to which batch?") selection card directly,
    # **without calling the model**: which batch 「这批」 ("this batch") refers to is a database question (campaign_id), not a language question,
    # and the model can't get batch ids (the allowlist in `runtime/payload.py` trims leads/campaign_id);
    # letting it guess means picking lead_ids from a flat page of recommendations — inevitably wrong when two batches mix.
    #
    # Other cases still go to the model: vague times need a follow-up, and with no batches in the session the user is most likely talking about some
    # named company; neither is solved by choosing a batch. Query intents (lookup) are never touched.
    if intent == "send_schedule_create" and _append_send_schedule_batch_picker(deps, message, intent):
        return True
    if intent in {"send_schedule_create", "send_schedule_lookup"}:
        return False

    if _append_stats_domain_correction_reply(deps, message, intent):
        return True
    if _append_delivery_detail_reply(deps, message, intent):
        return True
    if _append_stats_scope_confirmation_reply(deps, message, intent):
        return True
    if _append_recommendation_history_card(deps, message, intent):
        return True
    if _append_recommendation_stats_reply(deps, message, intent):
        return True
    if _is_delivery_count_query(message, intent) and _append_delivery_status_reply(deps, message, intent):
        return True
    followup_scope = _delivery_followup_scope_from_message(message)
    if followup_scope and _append_delivery_status_reply(deps, message, intent):
        return True
    if intent == "delivery_lookup" and _append_delivery_status_reply(deps, message, intent):
        return True
    if intent == "recommendation_lookup" and _append_recommendation_history_card(deps, message, intent):
        return True
    if intent == "recommendation_lookup" and _append_recommendation_stats_reply(deps, message, intent):
        return True

    if not step:
        explicit_domain = _explicit_stats_domain(message)
        if intent == "delivery_lookup":
            explicit_domain = "delivery"
        elif intent == "recommendation_lookup":
            explicit_domain = "recommendation"
        followup_scope = _delivery_followup_scope_from_message(message)
        # Ordinary chat like "who are you / have you eaten" must not inherit the previous turn's "which companies were recommended yesterday" stats context.
        # Otherwise whenever the last turn had last_stats_query = recommendation details, a "who are you" would be treated as "who are those companies" and push the history card.
        if not explicit_domain and identity_or_reference_kind(message):
            return _append_identity_or_reference_reply(deps, message, intent)
        if _append_stats_domain_correction_reply(deps, message, intent):
            return True
        if _append_delivery_detail_reply(deps, message, intent):
            return True
        if _append_stats_scope_confirmation_reply(deps, message, intent):
            return True
        if explicit_domain == "delivery" and _append_delivery_status_reply(deps, message, intent):
            return True
        if _is_delivery_correction(message):
            append_chat_message(deps, "assistant", "明白,你要查的是邮件发送记录,不是推荐企业。你可以直接说“昨天发了几封邮件”或“查看昨天发送了多少家”,我会按真实发送记录统计。", intent=intent)
            _remember_last_stats_query(deps, "delivery", "all", "count", "sent")
            _save_lead_flow_state(deps)
            return True
        if not explicit_domain and _append_delivery_status_reply(deps, message, intent):
            return True
        if explicit_domain == "recommendation" and _append_recommendation_history_card(deps, message, intent):
            return True
        if explicit_domain == "recommendation" and _append_recommendation_stats_reply(deps, message, intent):
            return True
        if not explicit_domain and _append_recommendation_history_card(deps, message, intent):
            return True
        if not explicit_domain and _append_recommendation_stats_reply(deps, message, intent):
            return True
        if _is_delivery_count_query(message, intent) and _append_delivery_status_reply(deps, message, intent):
            return True
        if followup_scope and _append_delivery_status_reply(deps, message, intent):
            return True
        if coarse_intent != "lookup" and _append_delivery_status_reply(deps, message, intent):
            return True
        if _append_identity_or_reference_reply(deps, message, intent):
            return True

    # This only catches the fallback of "not on a profile step, yet suddenly a save-profile sentence". When really on the history save card it must pass through
    # to `_handle_recommendation_profile_save_choice` below — only it clears step.
    # It used to exclude only RECOMMENDATION_PROFILE_STEP ("recommendation_profile") and missed the one-word-different
    # RECOMMENDATION_PROFILE_SAVE_STEP ("recommendation_profile_save"), so after clicking that history card the user got an explanation
    # but step stayed forever on recommendation_profile_save, stuck in an obsolete step with no way out.
    if _is_save_recommendation_profile_action(message) and step not in (RECOMMENDATION_PROFILE_STEP, RECOMMENDATION_PROFILE_SAVE_STEP):
        append_chat_message(
            deps,
            "assistant",
            "画像会在你确认专业能力、服务范围、企业规模和适合客户时自动保存;目标行业、地区和目标规模属于本次推荐企业条件,已随推荐记录保存,不用单独保存画像。",
            intent=intent,
        )
        return True

    if _is_update_profile_action(message):
        _record_current_step_checkpoint(deps, flow, step or "seller_profile_summary")
        # 「更新画像」 ("update profile") is a global action and must not be intercepted by stale audit flow state. Otherwise when the user jumps from the website audit card
        # to edit the profile, the next capability description would still be swallowed by detection_url / detection_keywords.
        _detection_flow_state(deps).clear()
        flow.pop("seller_capabilities", None)
        flow.pop("service_scope", None)
        flow.pop("seller_team", None)
        flow.pop("seller_team_raw", None)
        flow["step"] = ""
        _save_lead_flow_state(deps)
        state_for_brand = LeadState.from_flow(flow)
        state_for_brand.brand_required = _brand_required(deps)
        if state_for_brand.brand_required and not state_for_brand.brand_id and _append_brand_picker_guidance(deps, "lead"):
            return True
        _append_lead_seller_capabilities_guidance(deps, "lead")
        return True

    detection_flow = _detection_flow_state(deps)
    detection_step = str(detection_flow.get("step") or "")
    detected_url = _url_from_text(message)
    # The exit must be checked **before** the two state branches: each unconditionally does `return True`,
    # so placing it after means it's never reached. After clearing don't return; continue down the find-customers path.
    if detection_step and not detected_url and _is_detection_escape(message, intent):
        detection_flow.clear()
        _save_lead_flow_state(deps)
        detection_step = ""
    if detection_step == "detection_url":
        if detected_url:
            _append_detection_keywords_guidance(deps, "report", detected_url)
        else:
            _append_detection_url_guidance(deps, "report")
        return True
    if detection_step == "detection_keywords":
        if detected_url:
            detection_flow["url"] = detected_url
            detection_flow["brand_name"] = _brand_from_url(detected_url)
        keyword_problems = verify_detection_keywords_answer(message)
        if keyword_problems:
            write_model_call_log(
                request_id=new_model_call_id(), event="detection_keywords_rejected", status="skipped", scene="report",
                user_id=deps.user_id, session_public_id=deps.session_public_id, message=message,
                extra={"reasons": keyword_problems},
            )
            _append_detection_keywords_guidance(deps, "report", str(detection_flow.get("url") or ""), "请补充品牌词和行业关键词,不要直接提交卡片动作文字。")
            return True
        await _append_detection_report_started(deps, "report", message)
        return True
    # A URL doesn't mean an audit request: 「www.abc.com 我给他发过邮件吗?」 ("did I email www.abc.com?") is a history lookup and shouldn't be hijacked by the audit card.
    # Only block the two explicit query intents lookup / faq — keep chat, since pasting just a URL is the main audit entry.
    if not step and ((detected_url and coarse_intent not in {"lead", "lookup", "faq"}) or _is_detection_entry(message) or resolve_detection_action(message)):
        if detected_url:
            industry_terms, brand_terms = _detection_terms_from_text(message, _brand_from_url(detected_url))
            detection_flow.clear()
            detection_flow["url"] = detected_url
            detection_flow["brand_name"] = brand_terms[0] if brand_terms else _brand_from_url(detected_url)
            if industry_terms:
                _save_lead_flow_state(deps)
                await _append_detection_report_started(deps, "report", message)
            else:
                _append_detection_keywords_guidance(deps, "report", detected_url)
        else:
            _append_detection_url_guidance(deps, "report")
        return True

    flow = _lead_flow_state(deps)
    if step == RECOMMENDATION_PROFILE_STEP:
        if _handle_recommendation_profile_choice(deps, message, intent):
            return True

    if step == RECOMMENDATION_PROFILE_SAVE_STEP:
        if _handle_recommendation_profile_save_choice(deps, message, intent):
            return True

    if step == "seller_profile_summary":
        if _is_back_step_action(message):
            return _handle_back_or_undo_action(deps, flow, step, "lead")
        if _is_continue_profile_action(message):
            _record_current_step_checkpoint(deps, flow, "seller_profile_summary")
            # 「继续」 ("Continue") = accepting the /admin content on the profile card, not "ask me each item again".
            # This used to hard-code a jump to _append_lead_typical_customers_guidance, bypassing the router —
            # a leftover of the old step chain from **before** 5c533cd4 switched to slot routing. Consequence: the admin page clearly had
            # 「适合服务的客户类型」 ("customer types suited to serve") filled in, and the profile card had just prefilled it into flow, yet after clicking continue the user was
            # stopped to confirm it again; and if they casually changed the ticks, it would write back and overwrite the admin page's copy.
            # Now next_step computes it: filled slots are skipped, and only what's really missing on the /admin side is asked.
            continue_state = LeadState.from_flow(flow)
            continue_state.brand_required = _brand_required(deps)
            if await _advance_lead_flow(deps, flow, continue_state, []):
                return True
        if _is_find_customer_entry(message):
            if _append_seller_profile_summary_card(deps, "lead"):
                return True

    state = LeadState.from_flow(flow)
    # The router doesn't query the DB; "whether to ask for the brand first" is injected here. Always False for single-brand accounts, so the whole branch is skipped.
    state.brand_required = _brand_required(deps)

    if _is_continue_recommendation_request(message) and (not step or step == READY):
        if step == READY:
            flow["step"] = ""
            _save_lead_flow_state(deps)
            step = ""
        return _append_continue_recommendations(deps, message, intent)

    orphan_action = resolve_orphan_flow_action(message, step=step, detection_step=detection_step)
    if orphan_action:
        append_chat_message(
            deps,
            "assistant",
            "当前没有正在等待确认的卡片。你可以重新选择要做的事。",
            intent=intent,
            metadata={"tool_cards": [build_entry_guidance_card()]},
        )
        return True

    if resolve_lead_action(message) and not step and not intent_is_read_only(intent):
        if state.brand_required and not state.brand_id and _append_brand_picker_guidance(deps, "lead"):
            return True
        return await _append_recommendation_profile_or_brand_entry(deps, "lead")

    if step and _is_back_step_action(message):
        return _handle_back_or_undo_action(deps, flow, step, "lead")

    # Read-only intents may not **start** a write path. With no flow in progress, this sentence is initiating something —
    # queries / questions / chit-chat all go to the model (with Gather-prefetched data), no profile card, no recommendation run.
    # The criterion uses `intent_is_read_only` instead of a hard-coded set here: read-only-ness is part of the intent result
    # (flow/intent.py::READ_ONLY_INTENTS), and writing it in two places leads to misses — the line below's
    # `{"lookup","faq"}` missed chat, and chat is exactly the fallback bucket with the largest volume.
    # Exception one: a sentence while already in a flow (step non-empty) is answering a card; ignore the intent and let verify_slot_answer judge.
    lead_filter_request = _looks_like_lead_filter_request(deps, message)
    if intent_is_read_only(intent) and not step and not lead_filter_request:
        return False

    # History lookups / FAQ aren't answering a card; hand them to the model. The deterministic flow used to ignore intent,
    # so even when route_intent judged correctly it didn't help — questions were still eaten by step as answers.
    # Chat is **deliberately** excluded here: the raw option text sent by clicking a card (「11-100 名员工」 "11-100 employees") is judged chat,
    # and using the read-only set would block card answers too, so the flow could never proceed.
    # Exception: filter conditions + a count like 「工业制造、一线城市、北京、上海、100家」 mean finding a new batch.
    if coarse_intent in {"lookup", "faq"} and not step and not lead_filter_request:
        return False

    # Questions and action button text aren't "answers to the current question" and may not enter slots
    answer_problems = verify_slot_answer(step, message)
    if answer_problems:
        write_model_call_log(
            request_id=new_model_call_id(), event="slot_answer_rejected", status="skipped", scene="lead",
            user_id=deps.user_id, session_public_id=deps.session_public_id, message=message,
            extra={"step": step, "reasons": answer_problems},
        )
        if _normalized_action_text(message) in {"确认", "确认选择", "下一步", "跳过"} and _append_step_card(deps, step, "lead"):
            return True
        return False

    # Judged as lead means finding a new batch of customers via the deterministic four cards; no need to rely on the word list matching "找客户" ("find customers") verbatim.
    # When only the word list was used, an opener like 「帮我查下有哪些企业需要GEO服务的？」 ("find which companies need GEO services") couldn't get in, and the whole conversation
    # could only have the model issue cards, with step always empty (production, 2026-08-26).
    if not step and (coarse_intent == "lead" or _is_find_customer_entry(message) or lead_filter_request):
        # The first entry sentence still issues the profile confirmation card first; target conditions in this sentence are extracted into slots, and after confirmation jump straight to the card still missing
        state.apply(extract_slots(message, _slot_catalog(deps)))
        state.to_flow(flow)
        _save_lead_flow_state(deps)
        # Multi-brand: before the profile card, first ask "which one this time" — capabilities/ideal customers are attributes of **a particular brand**,
        # and issuing the profile card with the brand undecided takes an arbitrary brand's profile. For single-brand this returns False directly.
        if state.brand_required and not state.brand_id and _append_brand_picker_guidance(deps, "lead"):
            return True
        return await _append_recommendation_profile_or_brand_entry(deps, "lead")

    # While answering a specific card, parse only the slot for that card. Don't run a global extract_slots first:
    # entering 「工业制造、企业服务」 ("industrial manufacturing, business services") at the 「目标行业」 ("target industry") step, the global word list would also pick up historical region values like 「全国」 ("nationwide");
    # conversely, entering 「全国、100家」 ("nationwide, 100 companies") at the 「目标地区」 ("target region") step must not put 「全国」 into target_industries.
    # Extracting multiple slots from one sentence is allowed only with no pending card, for entry sentences like 「找北京教育培训客户 20 家」 ("find 20 Beijing education/training customers").
    step_updates = _slot_from_step(step, message, _brands_for_picker(deps, state)) if step else {}
    updates = step_updates if step else extract_slots(message, _slot_catalog(deps))

    if step == BRAND and "brand_id" in updates:
        _record_current_step_checkpoint(deps, flow, step)
        # After choosing a brand don't go through `_advance_lead_flow`: the next card should be **this brand's** profile confirmation card,
        # while next_step would only compute "seller_capabilities still missing" and skip the confirmation card.
        state.brand_id = int(updates["brand_id"])
        state.to_flow(flow)
        flow["step"] = ""
        _save_lead_flow_state(deps)
        return await _append_recommendation_profile_or_brand_entry(deps, "lead")

    if step == BRAND and _is_new_brand_action(message):
        # Typed 「新增品牌」 ("add brand"). **No brand creation in chat** — brands are created only in /sales-agent/admin,
        # and mixing it into this step means one slip creates an extra empty business line. Issue the selection card again as-is.
        # Only this sentence is blocked: other unrecognized input proceeds as usual; don't turn the brand step into a dead end.
        if _append_brand_picker_guidance(deps, "lead"):
            return True

    if step == "business_hint" and updates.get("business_hint"):
        _record_current_step_checkpoint(deps, flow, step)
        flow["business_hint"] = _clean_list(updates.get("business_hint"), 10)
        flow.pop("profile_suggestions", None)
        _save_lead_flow_state(deps)
        await _profile_suggestions_for_flow(deps, flow, _seller_profile_context(deps))
        _append_lead_seller_capabilities_guidance(deps, "lead")
        return True

    if step in {"seller_profile", "seller_capabilities"} and "seller_capabilities" not in updates \
            and any(part.strip() in CUSTOM_CAPABILITY_TOKENS for part in _list_from_text(message)):
        _record_current_step_checkpoint(deps, flow, step)
        _append_custom_capability_guidance(deps, "lead")
        return True

    if step in {"target_industries", CUSTOM_INDUSTRY} and "target_industries" not in updates \
            and _target_industry_from_text(message) == "__custom__":
        _record_current_step_checkpoint(deps, flow, step)
        state.custom_industry_pending = True
        return await _advance_lead_flow(deps, flow, state, ["custom_industry_pending"])

    changed = state.apply(updates)
    if changed:
        checkpoint_flow = dict(flow)
        state.to_flow(checkpoint_flow)
        checkpoint_flow["step"] = step
        _record_current_step_checkpoint(deps, checkpoint_flow, step)
        return await _advance_lead_flow(deps, flow, state, changed)
    if updates and step:
        # After undo the card echoes the original values; the user directly confirming the original values should also proceed.
        checkpoint_flow = dict(flow)
        state.to_flow(checkpoint_flow)
        checkpoint_flow["step"] = step
        _record_current_step_checkpoint(deps, checkpoint_flow, step)
        state.to_flow(flow)
        return await _advance_lead_flow(deps, flow, state, [])

    if _is_find_customer_entry(message):
        if state.brand_required and not state.brand_id and _append_brand_picker_guidance(deps, "lead"):
            return True
        return await _append_recommendation_profile_or_brand_entry(deps, "lead")

    return False


@router.post("/chat/a2ui")
async def chat_a2ui(
    body: ChatIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sales Agent A2UI/JSON conversation entry: the page's main path, not SSE."""
    try:
        deps = resolve_account(
            current_user,
            db,
            session_id=body.session_id,
            new_session=body.new_session,
            page_context=body.page_context,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    history = load_message_history(deps)
    intent = await _resolve_message_intent(body.message)
    append_chat_message(deps, "user", body.message, intent=intent)

    if await _run_deterministic_flow(deps, body.message, intent):
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)

    model_call_id = new_model_call_id()
    model_started_at = now_monotonic()
    model_cfg_fields: dict[str, object] = {}
    scene = "lead" if coarse_intent_for(intent) == "lead" else "plain_chat"
    try:
        cfg = resolve_sales_agent_model_config("chat")
        model_cfg_fields = model_config_fields(cfg)
        agent = build_sales_agent(scene)
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent A2UI build failed")
        write_model_call_log(
            request_id=model_call_id,
            event="model_build",
            status="failed",
            scene="a2ui",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            duration=duration_ms(model_started_at),
            model_config=model_cfg_fields,
            error=e,
            extra={"agent_scene": scene},
        )
        if scene == "lead":
            _append_lead_guidance_fallback(deps, intent)
        else:
            append_chat_message(deps, "assistant", _chat_fallback_reply(body.message, intent), intent=intent)
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)

    run_started_at = now_monotonic()
    write_model_call_log(
        request_id=model_call_id,
        event="model_call",
        status="started",
        scene="a2ui",
        user_id=current_user.id,
        session_public_id=deps.session_public_id,
        intent=intent,
        message=body.message,
        model_config=model_cfg_fields,
        extra={"history_loaded": history is not None, "json_endpoint": True},
    )
    try:
        result = await run_guarded(
            agent.run(_page_preamble(body.page_context) + _state_preamble(deps)
                      + _gather_preamble(deps, intent, body.message) + body.message, deps=deps,
                      message_history=history, usage_limits=usage_limits_for(scene)),
            scene=scene,
        )
        cards = _cards_from_agent_result(result)
        output_text = _fallback_output_from_cards(cards) or str(getattr(result, "output", "") or "").strip()
        # For named send-status queries, verify against DB data before persisting; on mismatch override with the deterministic conclusion.
        output_text = _delivery_target_answer_correction(deps, body.message, intent, output_text) or output_text
        save_message_history(deps, result.all_messages_json())
        metadata = {"tool_cards": cards} if cards else None
        append_chat_message(deps, "assistant", output_text, intent=intent, metadata=metadata)
        write_model_call_log(
            request_id=model_call_id,
            event="model_call",
            status="ok",
            scene="a2ui",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            duration=duration_ms(run_started_at),
            model_config=model_cfg_fields,
            extra={"cards_count": len(cards), "output_len": len(output_text), "json_endpoint": True},
            usage=(result.usage() if hasattr(result, "usage") else None),
        )
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)
    except (SalesAgentTimeout, UsageLimitExceeded) as e:
        # Timeout/over limit: reply with a readable sentence and persist it, no 500 — the user should see "it stopped this time",
        # not a spinner until the gateway times out or a red error. Ordinary chit-chat uses the chat fallback; don't surface a generic failure.
        logger.warning("Sales Agent A2UI wrap-up: %s", e)
        write_model_call_log(
            request_id=model_call_id, event="model_call", status="failed", scene="a2ui",
            user_id=current_user.id, session_public_id=deps.session_public_id,
            intent=intent, message=body.message, duration=duration_ms(run_started_at),
            model_config=model_cfg_fields, error=e,
            extra={"reason": "timeout" if isinstance(e, SalesAgentTimeout) else "usage_limit"},
        )
        msg = public_message(e) if scene == "lead" else _chat_fallback_reply(body.message, intent)
        append_chat_message(deps, "assistant", msg, intent=intent)
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)
    except SalesAgentGuardDenied as e:
        logger.warning("Sales Agent A2UI gate blocked: %s", e)
        write_model_call_log(
            request_id=model_call_id, event="model_call", status="failed", scene="a2ui",
            user_id=current_user.id, session_public_id=deps.session_public_id,
            intent=intent, message=body.message, duration=duration_ms(run_started_at),
            model_config=model_cfg_fields, error=e, extra={"reason": "guard_denied"},
        )
        append_chat_message(deps, "assistant", str(e), intent=intent)
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)
    except SalesAgentError as e:
        logger.warning("Sales Agent A2UI handled error: %s", e)
        write_model_call_log(
            request_id=model_call_id,
            event="model_call",
            status="failed",
            scene="a2ui",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            duration=duration_ms(run_started_at),
            model_config=model_cfg_fields,
            error=e,
        )
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent A2UI chat failed")
        write_model_call_log(
            request_id=model_call_id,
            event="model_call",
            status="failed",
            scene="a2ui",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            duration=duration_ms(run_started_at),
            model_config=model_cfg_fields,
            error=e,
        )
        if coarse_intent_for(intent) == "lead":
            _append_lead_guidance_fallback(deps, intent)
        else:
            append_chat_message(deps, "assistant", _chat_fallback_reply(body.message, intent), intent=intent)
        return session_bundle(db, current_user.id, session_id=deps.session_public_id)


@router.post("/chat")
async def chat(
    body: ChatIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sales Agent conversation entry (SSE)."""
    try:
        deps = resolve_account(
            current_user,
            db,
            session_id=body.session_id,
            new_session=body.new_session,
            page_context=body.page_context,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    history = load_message_history(deps)
    intent = await _resolve_message_intent(body.message)
    append_chat_message(deps, "user", body.message, intent=intent)

    # Shares the same deterministic flow as `/chat/a2ui` — this used to have only a quick_reply shortcut,
    # so profile cards, filter cards and audit guidance were all bypassed straight to the model; the same sentence gave two results at two entries.
    if await _run_deterministic_flow(deps, body.message, intent):
        return StreamingResponse(_deterministic_reply_stream(deps), media_type="text/event-stream", headers=_SSE_HEADERS)

    model_call_id = new_model_call_id()
    model_started_at = now_monotonic()
    model_cfg_fields: dict[str, object] = {}
    scene = "plain_chat" if coarse_intent_for(intent) != "lead" else "lead"
    try:
        cfg = resolve_sales_agent_model_config("chat")
        model_cfg_fields = model_config_fields(cfg)
        agent = build_sales_agent(scene)
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent build failed")
        write_model_call_log(
            request_id=model_call_id,
            event="model_build",
            status="failed",
            scene="chat",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            duration=duration_ms(model_started_at),
            model_config=model_cfg_fields,
            error=e,
            extra={"agent_scene": scene},
        )
        if scene == "lead":
            _append_lead_guidance_fallback(deps, intent)
        else:
            append_chat_message(deps, "assistant", _chat_fallback_reply(body.message, intent), intent=intent)
        return StreamingResponse(_deterministic_reply_stream(deps), media_type="text/event-stream", headers=_SSE_HEADERS)
    write_model_call_log(
        request_id=model_call_id,
        event="model_build",
        status="ok",
        scene="chat",
        user_id=current_user.id,
        session_public_id=deps.session_public_id,
        intent=intent,
        message=body.message,
        duration=duration_ms(model_started_at),
        model_config=model_cfg_fields,
    )
    run_message = (_page_preamble(body.page_context) + _state_preamble(deps)
                   + _gather_preamble(deps, intent, body.message) + body.message)

    async def event_stream():
        run_started_at = now_monotonic()
        write_model_call_log(
            request_id=model_call_id,
            event="model_call",
            status="started",
            scene="chat",
            user_id=current_user.id,
            session_public_id=deps.session_public_id,
            intent=intent,
            message=body.message,
            model_config=model_cfg_fields,
            extra={"history_loaded": history is not None},
        )
        try:
            from pydantic_ai import Agent
            from pydantic_ai.messages import (
                FunctionToolResultEvent, PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta,
            )

            yield f"data: {json.dumps({'stage': '请求 Sales Agent 服务'}, ensure_ascii=False)}\n\n"
            # SSE must keep pushing increments, so it can't wait_for the whole thing; check the deadline between nodes instead.
            # (prod is Python 3.10, without asyncio.timeout.)
            deadline = now_monotonic() + timeout_for("chat")
            streamed_card_keys: set[str] = set()
            async with agent.iter(run_message, deps=deps, message_history=history,
                                  usage_limits=usage_limits_for("chat")) as run:
                async for node in run:
                    if now_monotonic() > deadline:
                        msg = public_message(SalesAgentTimeout('chat 超时')) if coarse_intent_for(intent) == "lead" else _chat_fallback_reply(body.message, intent)
                        try:
                            append_chat_message(deps, "assistant", msg, intent=intent)
                        except Exception:  # noqa: BLE001
                            pass
                        yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"
                        return
                    if Agent.is_model_request_node(node):
                        yield f"data: {json.dumps({'stage': '生成回复内容'}, ensure_ascii=False)}\n\n"
                        first_delta_timeout = _model_request_timeout("chat") if scene == "plain_chat" else 0
                        first_delta_deadline = now_monotonic() + first_delta_timeout if first_delta_timeout else 0
                        saw_text_delta = False
                        req_cm = node.stream(run.ctx)
                        try:
                            if first_delta_deadline:
                                remaining = max(first_delta_deadline - now_monotonic(), 0.001)
                                req_stream = await asyncio.wait_for(req_cm.__aenter__(), timeout=remaining)
                            else:
                                req_stream = await req_cm.__aenter__()
                        except asyncio.TimeoutError as e:
                            msg = _chat_fallback_reply(body.message, intent)
                            try:
                                append_chat_message(deps, "assistant", msg, intent=intent)
                            except Exception:  # noqa: BLE001
                                pass
                            write_model_call_log(
                                request_id=model_call_id,
                                event="model_call",
                                status="failed",
                                scene="chat",
                                user_id=current_user.id,
                                session_public_id=deps.session_public_id,
                                intent=intent,
                                message=body.message,
                                duration=duration_ms(run_started_at),
                                model_config=model_cfg_fields,
                                error=e,
                                extra={"reason": "plain_chat_first_delta_timeout", "timeout_seconds": first_delta_timeout},
                            )
                            yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"
                            return
                        else:
                            try:
                                req_iter = req_stream.__aiter__()
                                while True:
                                    try:
                                        if first_delta_deadline and not saw_text_delta:
                                            remaining = first_delta_deadline - now_monotonic()
                                            if remaining <= 0:
                                                raise asyncio.TimeoutError()
                                            ev = await asyncio.wait_for(req_iter.__anext__(), timeout=remaining)
                                        else:
                                            ev = await req_iter.__anext__()
                                    except StopAsyncIteration:
                                        break
                                    except asyncio.TimeoutError as e:
                                        msg = _chat_fallback_reply(body.message, intent)
                                        try:
                                            append_chat_message(deps, "assistant", msg, intent=intent)
                                        except Exception:  # noqa: BLE001
                                            pass
                                        write_model_call_log(
                                            request_id=model_call_id,
                                            event="model_call",
                                            status="failed",
                                            scene="chat",
                                            user_id=current_user.id,
                                            session_public_id=deps.session_public_id,
                                            intent=intent,
                                            message=body.message,
                                            duration=duration_ms(run_started_at),
                                            model_config=model_cfg_fields,
                                            error=e,
                                            extra={"reason": "plain_chat_first_delta_timeout", "timeout_seconds": first_delta_timeout},
                                        )
                                        yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
                                        yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"
                                        return
                                    delta = None
                                    if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                                        delta = ev.part.content
                                    elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                                        delta = ev.delta.content_delta
                                    if delta:
                                        saw_text_delta = True
                                        yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                            finally:
                                await req_cm.__aexit__(None, None, None)
                        continue
                    if Agent.is_call_tools_node(node):
                        # Push the card as soon as the tool returns, without waiting for the turn to finish — when recommending 100 companies the model still needs several seconds
                        # to finish its sentence, while the list is already in hand.
                        yield f"data: {json.dumps({'stage': '整理销售信息'}, ensure_ascii=False)}\n\n"
                        async with node.stream(run.ctx) as tool_stream:
                            async for ev in tool_stream:
                                if not isinstance(ev, FunctionToolResultEvent):
                                    continue
                                keyed = _card_from_tool_return_part(getattr(ev, "part", None))
                                if keyed is None or keyed[0] in streamed_card_keys:
                                    continue
                                streamed_card_keys.add(keyed[0])
                                yield f"data: {json.dumps({'cards': [keyed[1]]}, ensure_ascii=False)}\n\n"
                        continue
                    yield f"data: {json.dumps({'stage': '整理销售信息'}, ensure_ascii=False)}\n\n"
                result = run.result

            cards = _cards_from_agent_result(result)
            # One more pass at the end: fill in anything missed while streaming (nodes not covered by tool events); already-pushed ones aren't resent
            pending_cards = [c for c in cards if _card_key(c.get("tool", ""), c.get("data")) not in streamed_card_keys]
            if pending_cards:
                yield f"data: {json.dumps({'cards': pending_cards}, ensure_ascii=False)}\n\n"
            # Persisted copy uses the same metric definition as `/chat/a2ui`: prefer card copy when a card can serve as fallback, otherwise the model output
            output_text = _fallback_output_from_cards(cards) or str(getattr(result, "output", "") or "").strip()
            # For named send-status queries, verify against DB data before persisting. The stream already went out and can't be changed,
            # but the frontend's onDone runs `loadBundle` and treats the DB copy as authoritative (SalesAgentWorkbench.tsx),
            # so what's persisted is the corrected conclusion, and that's what the user finally sees.
            corrected = _delivery_target_answer_correction(deps, body.message, intent, output_text)
            if corrected:
                correction_delta = "\n\n(按库里的发送记录更正)\n" + corrected
                output_text = corrected
                yield f"data: {json.dumps({'delta': correction_delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'stage': '保存会话记录'}, ensure_ascii=False)}\n\n"
            try:
                save_message_history(deps, result.all_messages_json())
                metadata = {"tool_cards": cards} if cards else None
                append_chat_message(deps, "assistant", output_text, intent=intent, metadata=metadata)
            except Exception:  # noqa: BLE001
                pass
            write_model_call_log(
                request_id=model_call_id,
                event="model_call",
                status="ok",
                scene="chat",
                user_id=current_user.id,
                session_public_id=deps.session_public_id,
                intent=intent,
                message=body.message,
                duration=duration_ms(run_started_at),
                model_config=model_cfg_fields,
                extra={"cards_count": len(cards), "output_len": len(str(getattr(result, "output", "") or ""))},
                # RunUsage already aggregates the whole turn's tool round trips — the model usage page computes tokens and call counts from it
                usage=(result.usage() if hasattr(result, "usage") else None),
            )
            yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"
        except SalesAgentError as e:
            logger.warning("Sales Agent handled error: %s", e)
            write_model_call_log(
                request_id=model_call_id,
                event="model_call",
                status="failed",
                scene="chat",
                user_id=current_user.id,
                session_public_id=deps.session_public_id,
                intent=intent,
                message=body.message,
                duration=duration_ms(run_started_at),
                model_config=model_cfg_fields,
                error=e,
            )
            msg = _public_error_message(e, _chat_fallback_reply(body.message, intent))
            try:
                append_chat_message(deps, "assistant", msg, intent=intent)
            except Exception:  # noqa: BLE001
                pass
            yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.exception("Sales Agent stream failed")
            write_model_call_log(
                request_id=model_call_id,
                event="model_call",
                status="failed",
                scene="chat",
                user_id=current_user.id,
                session_public_id=deps.session_public_id,
                intent=intent,
                message=body.message,
                duration=duration_ms(run_started_at),
                model_config=model_cfg_fields,
                error=e,
            )
            msg = _chat_fallback_reply(body.message, intent)
            try:
                append_chat_message(deps, "assistant", msg, intent=intent)
            except Exception:  # noqa: BLE001
                pass
            yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True, 'session_id': deps.session_public_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/reset")
async def reset(
    session_id: str | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        deps = resolve_account(current_user, db, session_id=session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    reset_conversation(deps)
    return {"ok": True}


@router.get("/events")
async def agent_events(
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Long-task progress stream (SSE framing, read with fetch + ReadableStream, not EventSource).

    For design and choices see docs/08-独立模块/SalesAgent/03-系统设计/产品体验与工程优化.md §8b.
    The server queries the DB every SALES_AGENT_EVENTS_INTERVAL seconds and **pushes only on changes**;
    with no running tasks it sends heartbeats, and after staying idle up to the idle limit it wraps up, letting the client reconnect instead of hanging.

    Only report generation is wired up for now — the product's only truly long task (8–20 minutes). Batch reports naturally reuse the same stream.
    """
    interval = max(1.0, float(os.environ.get("SALES_AGENT_EVENTS_INTERVAL", "3")))
    max_seconds = max(60.0, float(os.environ.get("SALES_AGENT_EVENTS_MAX_SECONDS", "1800")))
    idle_seconds = max(30.0, float(os.environ.get("SALES_AGENT_EVENTS_IDLE_SECONDS", "180")))
    user_id = current_user.id

    async def stream():
        started = now_monotonic()
        idle_since = started
        last_sent: dict[int, str] = {}
        # Send the first frame immediately: both the proxy and the client rely on it to confirm the connection is alive
        yield f"data: {json.dumps({'type': 'ready'}, ensure_ascii=False)}\n\n"
        while True:
            if await request.is_disconnected() or now_monotonic() - started > max_seconds:
                break
            try:
                items = running_report_progress(db, user_id)
            except Exception:  # noqa: BLE001 — a failed DB query shouldn't break the stream; retry next round
                logger.exception("Sales Agent progress query failed")
                items = []

            pushed = False
            for item in items:
                fingerprint = f"{item['status']}|{item['done']}/{item['total']}|{item['current_step']}"
                if last_sent.get(item["report_id"]) == fingerprint:
                    continue
                last_sent[item["report_id"]] = fingerprint
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                pushed = True

            running = any(x["status"] == "generating" for x in items)
            if pushed or running:
                idle_since = now_monotonic()
            elif now_monotonic() - idle_since > idle_seconds:
                yield f"data: {json.dumps({'type': 'idle'}, ensure_ascii=False)}\n\n"
                break
            if not pushed:
                yield ": keepalive\n\n"   # Comment frame: prevents proxies from cutting the connection on idle timeout
            await asyncio.sleep(interval)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",   # nginx already has proxy_buffering off; this adds another layer of safety
    })


@router.post("/reports/generate")
async def generate_report(
    body: GenerateReportIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        guard_check_user(db, current_user.id, "generate_detection_report")
        return await start_detection_report(
            db,
            current_user.id,
            url=body.url,
            brand_name=body.brand_name,
            industry_terms=body.industry_terms,
            brand_terms=body.brand_terms,
            competitors=body.competitors,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/leads/{lead_id}/report/generate")
async def generate_lead_report(
    lead_id: int,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        # Batch report generation goes through here, so even with the button hidden on the frontend, the backend must block it once.
        # Only the report capability is checked here, not `guard_check_user`: the latter records a quota use, and when this endpoint
        # hits an existing report it's a **pure read** (reused=True); charging quota for every read would be wrong.
        seller_profile_id = _seller_profile_id_for_lead_report(db, current_user.id, lead_id)
        denial = report_capability_denial(db, current_user.id, seller_profile_id)
        if denial:
            raise SalesAgentError(denial, status_code=403)
        return await generate_lead_report_share(db, current_user.id, lead_id, public_base_url=_request_public_base_url(request))
    except SalesAgentError as e:
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent lead report generation failed")
        raise HTTPException(status_code=503, detail=_SAFE_SERVICE_ERROR) from e


@router.get("/public/reports/{public_token}")
async def public_report_share(
    public_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        delivery_id = None
        raw_delivery_id = str(request.query_params.get("sad") or "").strip()
        if raw_delivery_id.isdigit():
            delivery_id = int(raw_delivery_id)
        return get_public_report_share(
            db,
            public_token,
            public_base_url=_request_public_base_url(request),
            delivery_id=delivery_id,
            user_agent=request.headers.get("user-agent", ""),
            ip_hash=_request_ip_hash(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent public report read failed")
        raise HTTPException(status_code=503, detail=_SAFE_SERVICE_ERROR) from e


@router.get("/email/open/{tracking_token}.png")
async def email_open_pixel(
    tracking_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        track_sales_agent_email_open(db, tracking_token, user_agent=request.headers.get("user-agent", ""), ip_hash=_request_ip_hash(request))
    except Exception:  # noqa: BLE001
        logger.exception("Sales Agent email open tracking failed")
    return Response(content=_TRANSPARENT_PIXEL, media_type="image/png", headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/reports/{source_report_table}/{source_report_id}/summary")
async def report_summary(
    source_report_table: str,
    source_report_id: str,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return get_report_summary(db, current_user.id, source_report_table, source_report_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/reports/latest/summary")
async def latest_report_summary(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return get_report_summary(db, current_user.id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/customer-profiles")
async def customer_profile(
    body: CustomerProfileIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return create_customer_profile(
            db,
            current_user.id,
            source_report_table=body.source_report_table,
            source_report_id=body.source_report_id,
            service_scope=body.service_scope,
            service_capabilities=body.service_capabilities,
            industries=body.industries,
            regions=body.regions,
            company_size=body.company_size,
            buyer_roles=body.buyer_roles,
            exclude_rules=body.exclude_rules,
            typical_customers=body.typical_customers,
            manual_profile=body.manual_profile,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/recommendation-profiles")
async def recommendation_profiles(
    seller_profile_id: int | None = Query(default=None),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return {"profiles": list_active_recommendation_profiles(db, current_user.id, seller_profile_id)}
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/recommendation-profiles")
async def create_recommendation_profile(
    body: SalesAgentRecommendationProfileIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = create_recommendation_profile_from_flow(
            db,
            current_user.id,
            body.flow,
            name=body.name,
            seller_profile_id=body.seller_profile_id,
            brand_ref_type=body.brand_ref_type,
            brand_ref_key=body.brand_ref_key,
            brand_name_snapshot=body.brand_name_snapshot,
            brand_context=body.brand_context,
            source=body.source,
        )
        return serialize_recommendation_profile_version(row)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/recommendation-profiles/{profile_key}/rename")
async def rename_recommendation_profile_endpoint(
    profile_key: str,
    body: SalesAgentRecommendationProfileRenameIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = rename_recommendation_profile(db, current_user.id, profile_key, body.name)
        return serialize_recommendation_profile_version(row)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/recommendation-profiles/{profile_key}/archive")
async def archive_recommendation_profile_endpoint(
    profile_key: str,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = archive_recommendation_profile(db, current_user.id, profile_key)
        return serialize_recommendation_profile_version(row)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/recommendation-profiles/{profile_key}/usage")
async def record_recommendation_profile_usage_endpoint(
    profile_key: str,
    body: SalesAgentRecommendationProfileUsageIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = get_recommendation_profile_version(db, current_user.id, profile_key, body.profile_version_id)
        if row is None:
            raise SalesAgentError("推荐画像不存在或已归档。", status_code=404)
        usage = record_recommendation_profile_usage(
            db,
            current_user.id,
            row.profile_key,
            row.id,
            session_id=None,
            campaign_id=body.campaign_id,
        )
        return {"ok": True, "id": usage.id, "profile_key": usage.profile_key, "profile_version_id": usage.profile_version_id}
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/companies/discover")
async def companies_discover(
    body: DiscoverCompaniesIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        guard_check_user(db, current_user.id, "discover_companies")
        return discover_companies(
            db,
            current_user.id,
            profile_id=body.profile_id,
            limit=body.limit,
            seed_companies=[x.model_dump() for x in body.seed_companies] if body.seed_companies else None,
            campaign_id=body.campaign_id,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/companies/rank")
async def companies_rank(
    body: RankCompaniesIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return rank_companies(
            db,
            current_user.id,
            profile_id=body.profile_id,
            source_report_table=body.source_report_table,
            source_report_id=body.source_report_id,
            limit=body.limit,
            campaign_id=body.campaign_id,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/companies/recommend")
async def companies_recommend(
    body: RecommendCompaniesIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        guard_check_user(db, current_user.id, "recommend_companies")
        industries = await map_target_industries_to_available(db, body.industries)
        return recommend_companies_workflow(
            db,
            current_user.id,
            profile_id=body.profile_id,
            limit=body.limit,
            service_scope=body.service_scope,
            industries=industries or body.industries,
            regions=body.regions,
            company_size=body.company_size,
            typical_customers=body.typical_customers,
            seed_companies=[x.model_dump() for x in body.seed_companies],
            source_report_table=body.source_report_table,
            source_report_id=body.source_report_id,
            seller_profile_id=body.seller_profile_id,
            account_icp_id=body.account_icp_id,
            campaign_id=body.campaign_id,
            target_company_size=body.target_company_size,
            service_scope_override=body.service_scope_override,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/outreach/drafts")
async def outreach_draft(
    body: OutreachDraftIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        guard_check_user(db, current_user.id, "generate_outreach_copy")
        return generate_outreach_draft(
            db,
            current_user.id,
            lead_id=body.lead_id,
            channel=body.channel,
            contact_id=body.contact_id,
            tone=body.tone,
            subject=body.subject,
            content=body.content,
            public_base_url=_request_public_base_url(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@dataclass(frozen=True)
class _OutreachCopyShaping:
    """Parameters for the chain of gates a piece of copy must pass between model output and persisting to DB.

    Extracted into an object not to save a few lines but to avoid drift: report link completion, contact info fallback, HTML allowlist and
    metadata — if the POST and SSE endpoints each wrote their own copy, changing one would inevitably miss the other.
    """

    ch: str
    tone: str
    style_key: str
    fingerprint: str
    can_reference_report: bool
    public_url: str
    is_intro_email: bool
    # template = fully assembled; llm = model rewrites the middle section; template_fallback = degraded after model failure;
    # empty string = the report email path, which has no concept of "letter type".
    intro_mode: str
    contact_id: int | None
    public_base_url: str


def _shape_and_persist_outreach_copy(
    db: Session,
    user_id: int,
    lead_id: int,
    context: dict[str, Any],
    structured: Any,
    copy: dict[str, str],
    idx: int,
    shaping: _OutreachCopyShaping,
) -> dict[str, Any]:
    """Pass a piece of model copy through the gates, persist it, and return `generate_outreach_draft`'s result.

    When the return value has `blocked=True`, the caller must return it to the frontend as-is — that's the last compliance gate before persisting,
    and a model call sits between fetching the context and here, so the lead may have just unsubscribed.
    """
    ch, can_reference_report, public_url = shaping.ch, shaping.can_reference_report, shaping.public_url
    if ch == "email" and can_reference_report:
        copy["subject"] = _styled_outreach_subject(context, shaping.style_key, copy.get("subject", ""))
        copy["content"] = _strip_extra_report_reply_cta(copy.get("content", ""))
    elif ch == "email":
        copy["subject"] = str(copy.get("subject") or "").strip()
        copy["content"] = str(copy.get("content") or "").strip()
    if ch == "email" and public_url and public_url not in copy.get("content", ""):
        copy["content"] = f"{copy.get('content', '').rstrip()}\n\n完整诊断报告：{public_url}"
    # If contact info is filled in the material, it must be in the signature. On the report path the body is written by the model, and the prompt only says
    # 「只能用资料里列出的」 ("only use what's listed in the material") — an allowlist, not a requirement, so the model omitting a signature isn't a violation.
    # The no-report path already assembles the contact info, so the values are there and this is a no-op.
    email_material = (context.get("brand_profile") or {}).get("email_material") or {}
    if ch == "email":
        copy["content"] = _ensure_contact_block(copy.get("content", ""), email_material)
    html_content = _sanitize_email_html(copy.get("html_content", "")) if ch == "email" else ""
    if ch == "email" and can_reference_report:
        html_content = _strip_extra_report_reply_cta_html(html_content)
    if ch == "email" and not html_content:
        html_content = _email_html_from_text(copy.get("content", ""))
    if ch == "email" and public_url and public_url not in html_content:
        safe_url = html_lib.escape(public_url, quote=True)
        html_content = _sanitize_email_html(
            f'{html_content.rstrip()}\n<p><strong>完整诊断报告：</strong><a href="{safe_url}">{safe_url}</a></p>'
        )
    if ch == "email" and html_content:
        # If plain text was completed but rich text wasn't, the two versions mismatch — and recipients see the rich-text one.
        html_content = _sanitize_email_html(_ensure_contact_block_html(html_content, email_material))
    metadata = report_email_metadata(shaping.style_key, idx, shaping.fingerprint, shaping.fingerprint, cached=False)
    metadata["contact_id"] = shaping.contact_id
    if shaping.is_intro_email:
        metadata.update({
            "kind": "no_report_default_email",
            # report_email_metadata() normalizes key to the report enum set, so intro letter keys
            # fall back to leijun_pitch — the four letter types share one frontend cache slot, so it must be overwritten back.
            "style_key": shaping.style_key,
            "style_label": NO_REPORT_EMAIL_STYLES[shaping.style_key]["label"],
            "prompt_version": NO_REPORT_EMAIL_PROMPT_VERSION,
            # The frontend uses this to decide whether to show 「重新生成」 ("Regenerate").
            "intro_mode": shaping.intro_mode,
            "template_mode": "no_report_default_pitch",
            # The compliance gate's Vigilath exemption only recognizes this flag, and it's computed only from the material —
            # the model can't influence it, and Vigilath appearing in other customers' letters is still blocked.
            "sender_is_vigilath": _brand_is_vigilath(email_material),
        })
    if ch == "email" and html_content:
        metadata["content_format"] = "html"
        metadata["html_content"] = html_content
    result = generate_outreach_draft(
        db,
        user_id,
        lead_id=lead_id,
        channel=ch,
        contact_id=shaping.contact_id,
        tone=shaping.tone,
        subject=copy["subject"],
        content=copy["content"],
        public_base_url=shaping.public_base_url,
        metadata=metadata,
        structured=structured,
    )
    if not isinstance(result, dict):
        return {"blocked": False}
    if result.get("blocked"):
        return result
    if ch == "email" and not can_reference_report:
        lead_result = dict(result.get("lead") or {}) if isinstance(result.get("lead"), dict) else {}
        lead_result["public_report_url"] = ""
        lead_result["share_token"] = ""
        lead_result["report_share"] = {}
        result["lead"] = lead_result
        result["report_share"] = {}
        result["public_report_url"] = ""
    return result


async def produce_outreach_draft(
    db: Session,
    user_id: int,
    lead_id: int,
    *,
    channel: str = "email",
    contact_id: int | None = None,
    tone: str = "professional",
    style_key: str = "leijun_pitch",
    variant_count: int = 1,
    force_regenerate: bool = False,
    public_base_url: str = "",
) -> dict[str, Any]:
    """The **only** implementation for generating follow-up email drafts. Non-HTTP callers (scheduled sending) take the same path.

    Moved verbatim out of the `/outreach/drafts/auto` function body, without changing a single condition — its branches
    "is there a real audit report -> report email or no-report intro letter -> template or model-rewritten intro letter ->
    reuse the cache on a fingerprint hit"; if scheduled sending wrote its own copy, bugs like "scheduled letters differ from manually
    sent ones" would never be noticed by anyone: each letter looks right on its own.
    """
    ch = (channel or "email").strip()
    variant_count = max(1, min(int(variant_count or 1), 6))
    if variant_count < 1:
        variant_count = 1
    # The second return value is passed as-is to generate_outreach_draft below —
    # without it the whole profile/campaign/report set would be re-queried, and inside the per-variant loop at that.
    context, structured_context = get_outreach_context_with_structured(
        db,
        user_id,
        lead_id=lead_id,
        channel=ch,
        contact_id=contact_id,
        public_base_url=public_base_url,
    )
    if context.get("blocked"):
        return {
            "blocked": True,
            "decision": context.get("decision"),
            "reasons": context.get("reasons") or [],
            "policy": "llm_generated_draft_only_human_approval_required",
            "hint": "已阻断,不会生成报告邮件。",
        }
    # Only id + name remain, for the fingerprint — the brand-level style/tone fallback was removed along with /sales-agent/admin.
    brand_settings = outreach_brand_settings(context)
    raw_style_key = (style_key or "").strip()
    tone = (tone or "").strip()
    report_context = context.get("report_context") if isinstance(context.get("report_context"), dict) else {}
    can_reference_report = bool(report_context.get("has_real_detection"))
    # The no-report intro letter has its own "letter type" keys, a different space from the report email's "recipient perspective".
    # With the wrong normalize, the three intro letter types would all fall back to leijun_pitch — same fingerprint,
    # so switching types would directly hit the previously generated draft.
    is_intro_email = ch == "email" and not can_reference_report
    style_key = normalize_no_report_email_style_key(raw_style_key) if is_intro_email else normalize_report_email_style_key(raw_style_key)
    style_options = no_report_email_style_options() if is_intro_email else report_email_style_options()
    if is_intro_email:
        # Generate only the selected one at a time. Intro letter differences come from the type, not from multiple candidates of the same type.
        variant_count = 1
    public_url = str(report_context.get("public_url") or context.get("public_report_url") or "").strip() if can_reference_report else ""
    if ch == "email" and not can_reference_report:
        report_context = {**report_context, "has_real_detection": False, "public_url": ""}
        lead_context = dict(context.get("lead") or {}) if isinstance(context.get("lead"), dict) else {}
        lead_context["public_report_url"] = ""
        lead_context["share_token"] = ""
        lead_context["report_share"] = {}
        context["lead"] = lead_context
        context["report_context"] = report_context
        context["report_share"] = {}
        context["public_report_url"] = ""

    fingerprint = report_email_fingerprint(
        lead_id,
        ch,
        style_key,
        report_context,
        public_url,
        brand=brand_settings,
        tone=tone,
        no_report=is_intro_email,
    )
    cache_key = fingerprint
    draft_kind = "report_email" if can_reference_report else "no_report_default_email"
    if not force_regenerate:
        cached_rows = cached_report_email_drafts(db, lead_id, ch, cache_key, limit=variant_count, draft_kind=draft_kind)
        if cached_rows:
            variants = []
            for row in cached_rows:
                draft = serialize_draft(row)
                meta = dict(draft.get("metadata") or {})
                meta["cached"] = True
                draft["metadata"] = meta
                variants.append(draft)
            return {
                "blocked": False,
                "cached": True,
                "draft": variants[0],
                "variants": variants,
                "lead": context.get("lead"),
                "company": context.get("company"),
                "contact": context.get("contact"),
                "report_share": context.get("report_share"),
                "public_report_url": public_url,
                "style_key": style_key,
                "style_options": style_options,
                "report_fingerprint": fingerprint,
                "policy": "report_email_cached_draft_only_human_approval_required" if can_reference_report else "no_report_email_cached_draft_only_human_approval_required",
            }

    guard_check_user(db, user_id, "generate_outreach_copy")
    intro_mode = ""   # The report email path has no concept of "letter type"; leave empty
    if is_intro_email:
        # No-report intro letter: salutation, contact info and signature are always assembled by code; the middle section depends on the letter type.
        # 「标准介绍」 ("standard intro") is fully assembled without the model (customers filled the material carefully; assembled text is more accurate than what the model invents);
        # other types hand this assembled middle section to the model to rewrite — the facts are already there, leaving the model no room to fabricate.
        # Empty material -> NoBrandProfileError -> prompt to fill in the material; don't send a hollow letter.
        base_paragraphs, base_html_parts = build_no_report_default_body_paragraphs(context)
        paragraphs, html_parts = base_paragraphs, base_html_parts
        intro_subject = ""
        intro_mode = "template"
        if no_report_email_style_is_llm(style_key):
            intro_mode = "llm"
            try:
                generated = await _generate_no_report_intro_body(context, style_key, "\n\n".join(base_paragraphs))
                rewritten = split_intro_body_paragraphs(strip_intro_body_affixes(generated["body"], context))
                if rewritten:
                    # The rich-text version is converted by the backend from plain text; don't ask the model for HTML.
                    paragraphs, html_parts = rewritten, None
                    intro_subject = sanitize_intro_subject(generated["subject"], context)
            except Exception:  # noqa: BLE001
                # However the model fails at this step, the user must still get a letter — the header and footer are assembled anyway,
                # and falling back to the assembled middle section gives a complete, sendable 「标准介绍」 ("standard intro").
                logger.warning("no-report intro rewrite failed, falling back to template body", exc_info=True)
                intro_mode = "template_fallback"
        copies = [{
            "subject": intro_subject or default_intro_subject(context),
            **assemble_intro_email(context, paragraphs, html_parts),
        }]
    else:
        copies = await _generate_outreach_copies_from_context(context, ch, tone, style_key, variant_count)
    variants = []
    lead_payload = context.get("lead")
    company_payload = context.get("company")
    contact_payload = context.get("contact")
    report_share_payload = context.get("report_share")
    shaping = _OutreachCopyShaping(
        ch=ch,
        tone=tone,
        style_key=style_key,
        fingerprint=fingerprint,
        can_reference_report=can_reference_report,
        public_url=public_url,
        is_intro_email=is_intro_email,
        intro_mode=intro_mode,
        contact_id=contact_id,
        public_base_url=public_base_url,
    )
    for idx, copy in enumerate(copies, start=1):
        result = _shape_and_persist_outreach_copy(
            db, user_id, lead_id, context, structured_context, copy, idx, shaping
        )
        if result.get("blocked"):
            return result
        variants.append(result["draft"])
        lead_payload = result.get("lead") or lead_payload
        company_payload = result.get("company") or company_payload
        contact_payload = result.get("contact") or contact_payload
        report_share_payload = result.get("report_share") or report_share_payload
    return {
        "blocked": False,
        "cached": False,
        "draft": variants[0] if variants else None,
        "variants": variants,
        "lead": lead_payload,
        "company": company_payload,
        "contact": contact_payload,
        "report_share": report_share_payload,
        "public_report_url": public_url,
        "style_key": style_key,
        "style_options": style_options,
        "report_fingerprint": fingerprint,
        "policy": "report_email_draft_only_human_approval_required" if can_reference_report else "no_report_email_draft_without_report_link_human_approval_required",
    }


async def generate_scheduled_outreach_draft(user_id: int, lead_id: int) -> int:
    """Draft generation entry for scheduled sending; returns `outreach_log_id`.

    `service/send_schedule.py`'s default generator delegates here (it can't import api.py directly — the layering
    is one-way, so that side keeps an injectable callable whose default implementation lazily imports this function).

    Opens its own session: the caller is the scheduler process's sending thread, which has no request-level session, and draft generation
    takes tens of seconds, so holding someone else's connection makes no sense. `public_base_url` is passed empty —
    `_sales_agent_public_base_url()` falls back to `SALES_AGENT_PUBLIC_BASE_URL` / `FRONTEND_URL`,
    since scheduled sending has no Request to ask anyway.
    """
    db = SessionLocal()
    try:
        result = await produce_outreach_draft(db, int(user_id), int(lead_id))
        if result.get("blocked"):
            # A compliance block must carry back **exactly which rule**, written into this company's skip_reason —
            # the word 「已阻断」 ("blocked") is useless to users; they need to know which company, why, and what to do.
            #
            # Not marked public: messages with interpolation are always sanitized via the word list (see `test_nothing_marked_public_
            # interpolates_anything`). `reasons` comes from `_outreach_guard`, made of our own
            # copy plus DB fields like lead status / consent_status, and goes out as-is after sanitizing.
            reasons = [str(x) for x in (result.get("reasons") or []) if str(x).strip()]
            raise SalesAgentError(
                f"这家企业的跟进邮件没通过合规检查:{';'.join(reasons)}" if reasons
                else "这家企业的跟进邮件没通过合规检查,没有生成。"
            )
        draft = result.get("draft") if isinstance(result.get("draft"), dict) else None
        draft_id = int((draft or {}).get("id") or 0)
        if draft_id <= 0:
            raise SalesAgentError("这家企业的跟进邮件生成后没拿到草稿编号,没有发。", public=True)
        return draft_id
    finally:
        db.close()


@router.post("/outreach/drafts/auto")
async def outreach_auto_draft(
    body: OutreachAutoDraftIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Button-triggered report email generation: no chat bubble, only candidates generated and handed to the right-side drawer for confirmation."""
    try:
        return await produce_outreach_draft(
            db,
            current_user.id,
            body.lead_id,
            channel=body.channel,
            contact_id=body.contact_id,
            tone=body.tone,
            style_key=body.style_key,
            variant_count=body.variant_count,
            force_regenerate=body.force_regenerate,
            public_base_url=_request_public_base_url(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent auto outreach draft failed")
        raise HTTPException(status_code=503, detail=_SAFE_SERVICE_ERROR) from e



def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/outreach/drafts/auto/stream")
async def outreach_auto_draft_stream(
    body: OutreachAutoDraftIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streaming version of the intro letter: header and footer are still assembled fixed by code; only the middle section is sent as it's written (SSE framing, read with fetch + ReadableStream).

    **Only the three model-rewritten letter types are accepted** (场景切入 / 行业观察 / 需求确认: scenario entry / industry observation / needs confirmation); everything else gets 400,
    sending the frontend to `/outreach/drafts/auto`:

    - 「标准介绍」 ("standard intro") is pure assembly with zero tokens, nothing to stream; streaming would just add a round trip;
    - report emails **can't** stream. Their compliance gate has **violation-if-missing** rules like "must contain the report link",
      and the link is usually in the last paragraph — gating by prefix would judge every unfinished letter a violation.
      The rules applicable on the intro letter path are all "violation-if-present" substring matches, so prefix gating is equivalent (see
      the notes on `IntroBodyStream`).

    The streamed paragraphs are only a locally assembled preview; what's persisted is still the complete letter: the `done` frame's payload matches
    `/outreach/drafts/auto` field by field, and the frontend must treat it as authoritative when wrapping up.
    """
    try:
        ch = (body.channel or "email").strip()
        public_base_url = _request_public_base_url(request)
        context, structured_context = get_outreach_context_with_structured(
            db,
            current_user.id,
            lead_id=body.lead_id,
            channel=ch,
            contact_id=body.contact_id,
            public_base_url=public_base_url,
        )
        blocked_payload = None
        if context.get("blocked"):
            blocked_payload = {
                "blocked": True,
                "decision": context.get("decision"),
                "reasons": context.get("reasons") or [],
                "policy": "llm_generated_draft_only_human_approval_required",
                "hint": "已阻断,不会生成介绍信。",
            }
        report_context = context.get("report_context") if isinstance(context.get("report_context"), dict) else {}
        can_reference_report = bool(report_context.get("has_real_detection"))
        style_key = normalize_no_report_email_style_key((body.style_key or "").strip())
        if blocked_payload is None and (ch != "email" or can_reference_report or not no_report_email_style_is_llm(style_key)):
            raise SalesAgentError(
                "这封邮件不支持流式生成:标准介绍是固定拼接、报告邮件要整封过完合规闸才能给,请改用 /outreach/drafts/auto。",
                status_code=400,
            )
        tone = (body.tone or "").strip()
        style_options = no_report_email_style_options()
        brand_settings = outreach_brand_settings(context)

        if blocked_payload is not None:
            async def blocked_stream():
                yield _sse_frame({"type": "done", "result": blocked_payload})
            return StreamingResponse(blocked_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

        # Intro letters never have a report to cite — clear those report fields in the context so neither the model nor the frontend sees them.
        report_context = {**report_context, "has_real_detection": False, "public_url": ""}
        lead_context = dict(context.get("lead") or {}) if isinstance(context.get("lead"), dict) else {}
        lead_context.update({"public_report_url": "", "share_token": "", "report_share": {}})
        context["lead"] = lead_context
        context["report_context"] = report_context
        context["report_share"] = {}
        context["public_report_url"] = ""

        fingerprint = report_email_fingerprint(
            body.lead_id, ch, style_key, report_context, "",
            brand=brand_settings, tone=tone, no_report=True,
        )
        cached_payload = None
        if not body.force_regenerate:
            cached_rows = cached_report_email_drafts(db, body.lead_id, ch, fingerprint, limit=1, draft_kind="no_report_default_email")
            if cached_rows:
                variants = []
                for row in cached_rows:
                    draft = serialize_draft(row)
                    meta = dict(draft.get("metadata") or {})
                    meta["cached"] = True
                    draft["metadata"] = meta
                    variants.append(draft)
                cached_payload = {
                    "blocked": False,
                    "cached": True,
                    "draft": variants[0],
                    "variants": variants,
                    "lead": context.get("lead"),
                    "company": context.get("company"),
                    "contact": context.get("contact"),
                    "report_share": context.get("report_share"),
                    "public_report_url": "",
                    "style_key": style_key,
                    "style_options": style_options,
                    "report_fingerprint": fingerprint,
                    "policy": "no_report_email_cached_draft_only_human_approval_required",
                }
        if cached_payload is not None:
            # On a cache hit there's nothing to stream, but still reply via SSE — the frontend has only one way to read.
            async def cached_stream():
                yield _sse_frame({"type": "done", "result": cached_payload})
            return StreamingResponse(cached_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

        guard_check_user(db, current_user.id, "generate_outreach_copy")
        # Empty material -> NoBrandProfileError -> prompt to fill in the material; don't send a hollow letter. This must happen before opening the stream;
        # once the stream is open, raising can only go into an error frame, and the HTTP status is already 200.
        base_paragraphs, base_html_parts = build_no_report_default_body_paragraphs(context)
    except SalesAgentError as e:
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent intro letter streaming generation failed in the preparation phase")
        raise HTTPException(status_code=503, detail=_SAFE_SERVICE_ERROR) from e

    email_material = (context.get("brand_profile") or {}).get("email_material") or {}
    gate_metadata = {"sender_is_vigilath": _brand_is_vigilath(email_material)}
    shaping = _OutreachCopyShaping(
        ch=ch,
        tone=tone,
        style_key=style_key,
        fingerprint=fingerprint,
        can_reference_report=False,
        public_url="",
        is_intro_email=True,
        intro_mode="llm",
        contact_id=body.contact_id,
        public_base_url=public_base_url,
    )
    user_id = current_user.id

    async def stream():
        # Header and footer don't depend on the model; send them as soon as the connection is established — users don't stare at a blank waiting for the model's first character.
        shell = assemble_intro_email(context, [])["content"].split("\n\n")
        yield _sse_frame({
            "type": "head",
            "greeting": shell[0],
            "tail": shell[1:],
            # Named `fallback_subject` rather than `subject`: it's the template subject used as a **fallback** when the model writes no subject,
            # not this letter's subject. When it was called `subject`, the frontend displayed it on connect,
            # and the model's subject arrived seconds later — users saw "an old subject flash and then jump".
            "fallback_subject": default_intro_subject(context),
            "style_key": style_key,
            "style_label": NO_REPORT_EMAIL_STYLES[style_key]["label"],
        })

        buffer = IntroBodyStream(context, gate_metadata)
        intro_mode = "template_fallback"
        index = 0
        started_at = now_monotonic()
        deadline = started_at + NO_REPORT_INTRO_MODEL_TIMEOUT
        # How many body chunks were received. Reported along with degradation — 「0 块」 ("0 chunks") and "chunks but unfinished" are two completely
        # different failures (gateway not responding / model too slow), indistinguishable by exception type alone.
        delta_count = 0
        # How many events the stream emitted in total and how many characters the thinking phase emitted. The two numbers separate failures:
        # 0 events = the gateway really didn't respond; thinking without body = the model was thinking and got cut off by the timer.
        event_count = 0
        thinking_chars = 0
        connect_seconds = -1.0
        prompt_chars = 0
        try:
            from pydantic_ai import Agent
            from pydantic_ai.messages import (
                PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta, ThinkingPart, ThinkingPartDelta,
            )

            agent = Agent(
                get_sales_agent_model("pro"),
                system_prompt=_INTRO_SYSTEM_PROMPT,
                model_settings=get_sales_agent_model_settings("pro", timeout=NO_REPORT_INTRO_MODEL_TIMEOUT),
            )
            prompt = _no_report_intro_prompt(context, style_key, "\n\n".join(base_paragraphs))
            prompt_chars = len(prompt) + len(_INTRO_SYSTEM_PROMPT)
            # Use `agent.iter()` + `node.stream()`, not `agent.run_stream()`: the chat streaming path in the same file
            # (`agent_chat_stream`) is written this way, the only combination verified with this provider + production's
            # Python 3.10. `run_stream` works locally on 3.12
            # but raises every time in production — rather than fixing two styles separately, keep just one.
            async with agent.iter(prompt) as run:
                async for node in run:
                    if not Agent.is_model_request_node(node):
                        continue
                    req_cm = node.stream(run.ctx)
                    # `__aenter__` waits for the gateway's response headers. Without wrapping it in `wait_for`, when the gateway hangs here
                    # the wall clock below never gets a chance to run — the chat streaming path is wrapped the same way.
                    first_event_deadline = now_monotonic() + _INTRO_FIRST_EVENT_TIMEOUT
                    connect_at = now_monotonic()
                    req_stream = await asyncio.wait_for(
                        req_cm.__aenter__(), timeout=max(first_event_deadline - now_monotonic(), 0.001)
                    )
                    # How long connecting (waiting for response headers) took. This number separates "gateway didn't respond" from "responded but
                    # emits no text" — the fixes are completely different, indistinguishable by exception type alone.
                    connect_seconds = now_monotonic() - connect_at
                    try:
                        req_iter = req_stream.__aiter__()
                        while True:
                            # The timeout in the model settings becomes a **per-chunk** read timeout when streaming: the model emitting one character every 40 seconds
                            # still doesn't time out. The whole-letter cap can only be enforced by this wall clock.
                            remaining = deadline - now_monotonic()
                            if remaining <= 0:
                                raise asyncio.TimeoutError(f"介绍信流式生成超过墙钟上限({event_count} 事件 / {delta_count} 块正文)")
                            # Use the short tier while the stream hasn't emitted any event: degrade early if there's truly no response. After it has emitted,
                            # only the total wall clock remains — whether the model is thinking or writing, it shouldn't be cut off by a per-chunk timer.
                            if not event_count:
                                remaining = min(remaining, max(first_event_deadline - now_monotonic(), 0.001))
                            try:
                                ev = await asyncio.wait_for(req_iter.__anext__(), timeout=remaining)
                            except StopAsyncIteration:
                                break
                            event_count += 1
                            delta = ""
                            if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                                delta = ev.part.content
                            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                                delta = ev.delta.content_delta
                            elif isinstance(ev, PartStartEvent) and isinstance(ev.part, ThinkingPart):
                                # Thinking content goes neither into the letter nor to the frontend — it isn't body text, can't pass the gate and
                                # shouldn't be gated. Only its length is recorded, to separate failures.
                                thinking_chars += len(ev.part.content or "")
                            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, ThinkingPartDelta):
                                thinking_chars += len(ev.delta.content_delta or "")
                            if not delta:
                                continue
                            delta_count += 1
                            update = buffer.push(delta)
                            if update.subject:
                                yield _sse_frame({"type": "subject", "subject": update.subject})
                            # Character-by-character preview. A frame may hold just one character; the frontend appends them in order to the current paragraph.
                            for text in update.chunks:
                                yield _sse_frame({"type": "chunk", "text": text})
                            for text in update.paragraphs:
                                index += 1
                                yield _sse_frame({"type": "paragraph", "index": index, "text": text})
                            if update.violations:
                                break
                    finally:
                        await req_cm.__aexit__(None, None, None)
                    if buffer.violations:
                        break
                if not buffer.violations:
                    update = buffer.finish()
                    if update.subject:
                        yield _sse_frame({"type": "subject", "subject": update.subject})
                    for text in update.paragraphs:
                        index += 1
                        yield _sse_frame({"type": "paragraph", "index": index, "text": text})

            if buffer.json_mode:
                # The model didn't follow the plain-text format and emitted JSON as before. The streaming path was wasted (no paragraph frames were sent),
                # but the letter must still be delivered: parse it, run the same stripping and gates, then send it as ordinary paragraphs.
                parsed = _parse_intro_model_output(buffer.raw)
                buffer = intro_stream_from_text(context, parsed["subject"], parsed["body"], gate_metadata)
                if not buffer.violations:
                    yield _sse_frame({"type": "subject", "subject": buffer.subject})
                    for text in buffer.paragraphs:
                        index += 1
                        yield _sse_frame({"type": "paragraph", "index": index, "text": text})

            if buffer.violations:
                logger.warning("Intro letter streaming content failed the compliance gate; degrading to standard intro: %s", "；".join(buffer.violations))
                paragraphs, html_parts, subject = base_paragraphs, base_html_parts, ""
                yield _sse_frame({"type": "reset", "reason": "这次写出来的内容没通过合规检查,已换成标准介绍。"})
            elif buffer.paragraphs:
                # The rich-text version is converted by the backend from plain text; don't ask the model for HTML.
                paragraphs, html_parts, subject = buffer.paragraphs, None, buffer.subject
                intro_mode = "llm"
            else:
                paragraphs, html_parts, subject = base_paragraphs, base_html_parts, ""
                yield _sse_frame({"type": "reset", "reason": "这次没写出可用的正文,已换成标准介绍。"})
        except Exception as e:  # noqa: BLE001
            # However the model fails at this step, the user must still get a letter — the header and footer are assembled anyway,
            # and falling back to the assembled middle section gives a complete, sendable 「标准介绍」 ("standard intro").
            elapsed = now_monotonic() - started_at
            # The resolved model name and endpoint host. `pro` and `chat` both point to the same model by default, but each
            # reads a different set of environment variables — what production actually resolves to can only be reported by itself.
            # Only the model name and host are taken; api_key goes neither into logs nor to the frontend.
            try:
                cfg = resolve_sales_agent_model_config("pro")
                model_hint = f"{cfg.model_name}@{urlparse(cfg.base_url or '').netloc or '?'}"
            except Exception:  # noqa: BLE001
                model_hint = "?"
            connect_hint = f"建连 {connect_seconds:.1f}s" if connect_seconds >= 0 else "没连上"
            logger.warning(
                "Intro letter streaming generation failed; degrading to the assembled middle section: %s (%.1fs, %d events / %d body chunks / thinking %d chars, %s, model=%s)",
                type(e).__name__, elapsed, event_count, delta_count, thinking_chars,
                connect_hint, model_hint, exc_info=True,
            )
            paragraphs, html_parts, subject = base_paragraphs, base_html_parts, ""
            # `detail` is only for troubleshooting; the frontend prints it to the console and doesn't render it to users. Without it the frontend only sees
            # 「生成中断了」 ("generation interrupted"), and without server logs you'd never know where it broke — the last investigation got stuck right here.
            # Elapsed time and chunk count separate failures: 0 chunks = the gateway didn't respond at all; chunks but unfinished = the model was too slow;
            # chunks all crammed into the last moment = the gateway isn't really streaming, so the typewriter effect can't work and the strategy must change.
            yield _sse_frame({
                "type": "reset",
                "reason": "这次生成中断了,已换成标准介绍。",
                "detail": (
                    f"{type(e).__name__}: {e} "
                    f"[{elapsed:.1f}s, {event_count} 事件 / {delta_count} 块正文 / "
                    f"思考 {thinking_chars} 字, {connect_hint}, "
                    f"prompt {prompt_chars} 字, model={model_hint}]"
                )[:300],
            })

        # The body ends here. Persisting still takes a moment; this frame lets the frontend show the letter footer first —
        # otherwise while the model is just starting to write, "contact info + regards" sits there, looking like the letter is already finished.
        yield _sse_frame({"type": "body_done"})
        copy = {
            "subject": subject or default_intro_subject(context),
            **assemble_intro_email(context, paragraphs, html_parts),
        }
        try:
            result = _shape_and_persist_outreach_copy(
                db, user_id, body.lead_id, context, structured_context, copy, 1,
                dataclass_replace(shaping, intro_mode=intro_mode),
            )
        except SalesAgentError as e:
            yield _sse_frame({"type": "error", "message": _public_error_message(e)})
            return
        except Exception:  # noqa: BLE001
            logger.exception("Sales Agent failed to persist the streamed intro letter draft")
            yield _sse_frame({"type": "error", "message": _SAFE_SERVICE_ERROR})
            return
        if result.get("blocked"):
            yield _sse_frame({"type": "done", "result": result})
            return
        yield _sse_frame({"type": "done", "result": {
            "blocked": False,
            "cached": False,
            "draft": result.get("draft"),
            "variants": [result.get("draft")],
            "lead": result.get("lead") or context.get("lead"),
            "company": result.get("company") or context.get("company"),
            "contact": result.get("contact") or context.get("contact"),
            "report_share": result.get("report_share") or {},
            "public_report_url": "",
            "style_key": style_key,
            "style_options": style_options,
            "report_fingerprint": fingerprint,
            "policy": "no_report_email_draft_without_report_link_human_approval_required",
        }})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/outreach/drafts/{draft_id}/confirm")
async def outreach_confirm(
    draft_id: int,
    body: OutreachConfirmIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return confirm_outreach_send(
            db,
            current_user.id,
            draft_id=draft_id,
            confirmed=body.confirmed,
            actor=f"user:{current_user.id}",
            note=body.note,
            public_base_url=_request_public_base_url(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


# ---------------------------------------------------------------- Scheduled sending (design 4.4)
#
# **These six endpoints are all written as synchronous `def`.** Everything beneath them is synchronous SQLAlchemy; writing them
# as `async def` runs blocking IO on the event loop thread — a slow query stalls not just this request but
# every request of the whole agent process (the 1.4 issue). Written as `def`, FastAPI throws them into the
# anyio thread pool, where blocking stays confined to the thread. The `async def`s above are history, not a template.
#
# Auth matches `POST /outreach/drafts/{draft_id}/confirm`: `get_current_user` gets the user,
# and ownership is checked again in the service layer's `_owned_schedule` (fetching someone else's plan returns 404, not 403 —
# not confirming whether the id exists).


def _schedule_time_from_body(when_text: str, scheduled_at: str, *, verb: str = "发") -> datetime:
    """Time in the request body -> naive UTC. One of two fields; if both are empty, ask a follow-up.

    `when_text` goes through exactly the same parsing as chat (`parse_when_text`), and the follow-up wording is the same copy
    (`when_followup_text`) — saying 「晚点发」 ("send later") on the panel and in chat must get the same follow-up question,
    otherwise switching entries would switch temperaments.

    `scheduled_at` is a concrete moment from the picker: timezone-aware values are converted by timezone, **naive values are read as Beijing time**.
    It can't be read as UTC — 10:00 shown in the picker is ten in the morning to the user; read as UTC it would become
    6pm Beijing time, and not conspicuously off.
    """
    text = str(when_text or "").strip()
    raw = str(scheduled_at or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as e:
            raise SalesAgentError(
                f"发送时间「{raw}」读不出来,要的是 2026-09-14T10:00 这样的格式。",
                status_code=400,
            ) from e
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            parsed = to_utc(parsed)
        return parsed.replace(second=0, microsecond=0)
    when = parse_when_text(text)
    if when.needs_followup:
        raise SalesAgentError(when_followup_text(when, verb=verb), status_code=400)
    return when.utc_at


@router.post("/outreach/schedules")
def create_send_schedule(
    body: SendScheduleCreateIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a plan and return the **full preview list** (how many companies can be sent + per-item reasons for those that can't). Status stays at "pending confirmation".

    Not a single email goes out at this step; authorization happens at `/confirm`.
    """
    try:
        scheduled_at = _schedule_time_from_body(body.when_text, body.scheduled_at)
        session_row = _find_user_session(db, current_user.id, body.session_id)
        if body.confirm and scheduled_at <= datetime.utcnow():
            # The one-click scheduling path (batch dialog) can't create first and reject later: `arm_send_schedule` hard-rejects
            # past times, and confirm=True here means arm right after creation — letting it proceed would
            # leave a draft in the DB that can never be armed, while the user sees "clicked OK, then an error".
            # Reject first: the DB stays clean and the message clearly says what to change.
            #
            # Not marked `public=True`: that flag is only for **pure literals** (`test_error_message_guard`
            # pins it on the source shape). This sentence has interpolation, so it takes the same path as the rejection in `arm_send_schedule` —
            # the word-list gate lets it through because it contains no secret/model-type words.
            raise SalesAgentError(
                f"{describe_utc(scheduled_at)}已经过去了,挑一个以后的时间。",
                status_code=400,
            )
        result = plan_send_schedule(
            db,
            current_user.id,
            _schedule_lead_ids_from_body(db, current_user.id, body),
            scheduled_at,
            throttle_seconds=body.throttle_seconds,
            daily_cap=body.daily_cap,
            session_id=session_row.id if session_row is not None else None,
            campaign_id=body.campaign_id or (body.campaign_ids[0] if len(body.campaign_ids) == 1 else None),
            user_text=body.when_text or body.scheduled_at,
        )
        if body.confirm:
            # The dialog path: the user already saw the full time and company count in the dialog; the authorization point is that click.
            # Splitting it into two clicks wouldn't make authorization clearer, it would only make nothing happen after 「确定」 ("OK").
            schedule_id = int((result.get("schedule") or {}).get("id") or 0)
            result["armed"] = arm_send_schedule(db, current_user.id, schedule_id, actor="user")
            result["schedule"] = result["armed"].get("schedule") or result["schedule"]
            result["hint"] = result["armed"].get("hint") or result.get("hint") or ""
    except PermissionError as e:
        # The session id isn't a valid UUID / doesn't belong to this account. Falling into the generic exception would give a 500 stack trace,
        # while this is actually an expected rejection.
        raise HTTPException(status_code=403, detail=str(e)) from e
    except SalesAgentError as e:
        raise _http_error(e) from e
    # Same metric definition as `schedule_outreach_send`: arm hard-rejects past times (①b of §4.1),
    # but by then the user has already confirmed once. Warn in the preview first, rather than telling them after confirmation that it can't be sent.
    if scheduled_at <= datetime.utcnow():
        result["time_warning"] = (
            f"{describe_utc(scheduled_at)}已经过去了,确认后也发不出去,换一个以后的时间吧。"
        )
    return result


def _schedule_lead_ids_from_body(db: Session, user_id: int, body: SendScheduleCreateIn) -> list[int]:
    """Explicit lead_ids take priority; otherwise resolve by batch.

    Returns an empty list when both are empty — `plan_send_schedule` gives the "no companies selected" sentence,
    so it isn't written again here.
    """
    explicit = [int(x) for x in (body.lead_ids or [])]
    if explicit:
        return explicit
    return resolve_batch_lead_ids(db, user_id, [int(x) for x in (body.campaign_ids or [])])


@router.post("/outreach/schedules/preview")
def preview_send_schedule(
    body: SendSchedulePreviewIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trial computation: with these batches and this time, **how many companies can be sent, how many can't**. Writes nothing to the DB.

    The batch dialog relies on it to report counts before the user clicks 「确定」 ("OK"). The algorithm uses
    `evaluate_send_candidates` — the same function as `plan_send_schedule`,
    so the 8 companies in the dialog can't disagree with the 8 actually scheduled.
    """
    try:
        scheduled_at = _schedule_time_from_body(body.when_text, body.scheduled_at)
        lead_ids = [int(x) for x in (body.lead_ids or [])] or resolve_batch_lead_ids(
            db, current_user.id, [int(x) for x in (body.campaign_ids or [])]
        )
        included, skipped = evaluate_send_candidates(db, current_user.id, lead_ids)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except SalesAgentError as e:
        raise _http_error(e) from e
    warning = ""
    if scheduled_at <= datetime.utcnow():
        warning = f"{describe_utc(scheduled_at)}已经过去了,挑一个以后的时间。"
    return {
        "total": len(lead_ids),
        "sendable_count": len(included),
        "blocked_count": len(skipped),
        "when_text": describe_utc(scheduled_at),
        "time_warning": warning,
    }


@router.get("/outreach/recommendation-batches")
def list_recommendation_batches(
    session_id: str = "",
    limit: int = Query(10, ge=1, le=50),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Which batches the current session has recommended. The option source for the 「发给哪一批?」 ("send to which batch?") dialog."""
    try:
        session_row = _find_user_session(db, current_user.id, session_id or None)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return session_recommendation_batches(
        db, current_user.id, session_row.id if session_row is not None else None, limit=limit
    )


@router.get("/outreach/schedules")
def list_schedules(
    status: str = "",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Plan list — this is what feeds the persistent entry of 4.5.

    A user who created a plan today and closed the page must be able to find "my pending plans" tomorrow: something authorized
    and about to really send external email can't be visible only at the moment it was created.
    """
    try:
        return list_send_schedules(db, current_user.id, status=status, limit=limit, offset=offset)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/outreach/schedules/{schedule_id}")
def get_schedule_detail(
    schedule_id: int,
    item_limit: int = Query(200, ge=1, le=500),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A single plan + item details. **This endpoint is required**: only it can feed the detail panel and 4.6's 10-second polling;
    the list endpoint can't carry 100 rows of detail.
    """
    try:
        return get_send_schedule_detail(db, current_user.id, schedule_id, item_limit=item_limit)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/outreach/schedules/{schedule_id}/confirm")
def confirm_send_schedule(
    schedule_id: int,
    body: SendScheduleConfirmIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Authorize (pending confirmation -> scheduled). **This is the compliance authorization point**; it can't be skipped or done implicitly by plan creation.

    `confirmed=false` isn't "do nothing"; it's the user deciding not to send after reviewing the list — treat it as cancellation,
    otherwise a draft plan would hang in the list forever.
    """
    try:
        if not body.confirmed:
            return cancel_send_schedule(db, current_user.id, schedule_id, reason="用户在确认环节取消")
        return arm_send_schedule(db, current_user.id, schedule_id, actor=f"user:{current_user.id}")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/outreach/schedules/{schedule_id}")
def patch_send_schedule(
    schedule_id: int,
    body: SendSchedulePatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change time / drop items.

    Drop first, then change the time: the time-change receipt says "send to these N companies", and in the reverse order N would be the pre-drop number.
    If neither is given, return an error — a 200 that changed nothing would make the frontend think the change succeeded.
    """
    try:
        drop_ids = [int(x) for x in (body.drop_item_ids or [])]
        has_time = bool(str(body.when_text or "").strip() or str(body.scheduled_at or "").strip())
        if not drop_ids and not has_time:
            raise SalesAgentError("没有要改的东西:要么给新的发送时间,要么给要剔除的清单项。", status_code=400)
        result: dict = {}
        if drop_ids:
            result = drop_send_schedule_items(db, current_user.id, schedule_id, drop_ids)
        if has_time:
            scheduled_at = _schedule_time_from_body(body.when_text, body.scheduled_at, verb="改到")
            moved = reschedule_send_schedule(db, current_user.id, schedule_id, scheduled_at)
            hint = "；".join(x for x in (result.get("hint"), moved.get("hint")) if x)
            result = {**result, **moved, "hint": hint}
        return result
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/outreach/schedules/{schedule_id}")
def delete_send_schedule(
    schedule_id: int,
    reason: str = "",
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel. **Only stops what hasn't gone out; what's already sent can't be recalled** — the receipt states both numbers truthfully."""
    try:
        return cancel_send_schedule(db, current_user.id, schedule_id, reason=reason or "用户在计划面板取消")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/email/settings")
async def email_settings(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return get_sales_agent_email_settings(db, current_user.id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.put("/email/settings")
async def update_email_settings(
    body: SalesAgentEmailSettingsIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_sales_agent_email_settings(db, current_user.id, body.model_dump(), scope="user", actor=f"user:{current_user.id}")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/email/accounts")
async def email_accounts(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sales_agent_email_accounts(db, current_user.id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/email/accounts")
async def create_email_account(
    body: SalesAgentEmailAccountIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return create_sales_agent_email_account(db, current_user.id, body.model_dump(), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/email/accounts/{account_id}")
async def update_email_account(
    account_id: int,
    body: SalesAgentEmailAccountPatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_sales_agent_email_account(db, current_user.id, account_id, body.model_dump(exclude_unset=True), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/email/accounts/{account_id}")
async def disable_email_account(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return disable_sales_agent_email_account(db, current_user.id, account_id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/email/accounts/{account_id}/test")
async def test_email_account(
    account_id: int,
    body: SalesAgentEmailTestIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return test_sales_agent_email_account(
            db,
            current_user.id,
            account_id,
            to_email=body.to_email,
            subject=body.subject,
            content=body.content,
            scope="user",
            public_base_url=_request_public_base_url(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/email/deliveries")
async def email_deliveries(
    status: str = "",
    message_type: str = "",
    q: str = "",
    read: str = "",
    company_id: int | None = None,
    lead_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sales_agent_email_deliveries(db, current_user.id, status=status, message_type=message_type, q=q, read=read, limit=limit, offset=offset, company_id=company_id, lead_id=lead_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/email/stats")
async def email_stats(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return sales_agent_email_stats(db, current_user.id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/email/settings")
async def admin_email_settings(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return get_sales_agent_email_settings(db, current_user.id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.put("/admin/email/settings")
async def admin_update_email_settings(
    body: SalesAgentEmailSettingsIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return update_sales_agent_email_settings(db, current_user.id, body.model_dump(), scope="global", actor=f"admin:{current_user.id}")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/email/accounts")
async def admin_email_accounts(
    scope: str = "all",
    user_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return list_sales_agent_email_accounts(db, current_user.id, scope=scope, admin=True, target_user_id=user_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/admin/email/accounts")
async def admin_create_email_account(
    body: SalesAgentEmailAccountIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return create_sales_agent_email_account(db, current_user.id, body.model_dump(), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/admin/email/accounts/{account_id}")
async def admin_update_email_account(
    account_id: int,
    body: SalesAgentEmailAccountPatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return update_sales_agent_email_account(db, current_user.id, account_id, body.model_dump(exclude_unset=True), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/admin/email/accounts/{account_id}")
async def admin_disable_email_account(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return disable_sales_agent_email_account(db, current_user.id, account_id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/admin/email/accounts/{account_id}/test")
async def admin_test_email_account(
    account_id: int,
    body: SalesAgentEmailTestIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return test_sales_agent_email_account(
            db,
            current_user.id,
            account_id,
            to_email=body.to_email,
            subject=body.subject,
            content=body.content,
            scope="global",
            public_base_url=_request_public_base_url(request),
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/email/deliveries")
async def admin_email_deliveries(
    status: str = "",
    message_type: str = "",
    q: str = "",
    read: str = "",
    user_id: int | None = None,
    company_id: int | None = None,
    lead_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return list_sales_agent_email_deliveries(db, current_user.id, admin=True, status=status, message_type=message_type, q=q, read=read, limit=limit, offset=offset, target_user_id=user_id, company_id=company_id, lead_id=lead_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/email/stats")
async def admin_email_stats(
    user_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return sales_agent_email_stats(db, current_user.id, admin=True, target_user_id=user_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


# ---------------------------------------------------------------------------
# SMS channel configuration. Paths map one-to-one to /email/* above so the frontend can copy the admin page;
# the extra one is /sms/accounts/{id}/templates — CN channels can only send registered templates,
# and templates hang under a channel, not globally.
# ---------------------------------------------------------------------------


@router.get("/sms/settings")
async def sms_settings(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return get_sales_agent_sms_settings(db, current_user.id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.put("/sms/settings")
async def update_sms_settings(
    body: SalesAgentSmsSettingsIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_sales_agent_sms_settings(db, current_user.id, body.model_dump(), scope="user", actor=f"user:{current_user.id}")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sms/accounts")
async def sms_accounts(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sales_agent_sms_accounts(db, current_user.id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/sms/accounts")
async def create_sms_account(
    body: SalesAgentSmsAccountIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return create_sales_agent_sms_account(db, current_user.id, body.model_dump(), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/sms/accounts/{account_id}")
async def update_sms_account(
    account_id: int,
    body: SalesAgentSmsAccountPatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_sales_agent_sms_account(db, current_user.id, account_id, body.model_dump(exclude_unset=True), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/sms/accounts/{account_id}")
async def disable_sms_account(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return disable_sales_agent_sms_account(db, current_user.id, account_id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sms/accounts/{account_id}/templates")
async def sms_templates(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sales_agent_sms_templates(db, current_user.id, account_id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/sms/accounts/{account_id}/templates")
async def create_sms_template(
    account_id: int,
    body: SalesAgentSmsTemplateIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return create_sales_agent_sms_template(db, current_user.id, account_id, body.model_dump(), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/sms/templates/{template_id}")
async def update_sms_template(
    template_id: int,
    body: SalesAgentSmsTemplatePatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_sales_agent_sms_template(db, current_user.id, template_id, body.model_dump(exclude_unset=True), scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/sms/templates/{template_id}")
async def disable_sms_template(
    template_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return disable_sales_agent_sms_template(db, current_user.id, template_id, scope="user")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/sms/settings")
async def admin_sms_settings(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return get_sales_agent_sms_settings(db, current_user.id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.put("/admin/sms/settings")
async def admin_update_sms_settings(
    body: SalesAgentSmsSettingsIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return update_sales_agent_sms_settings(db, current_user.id, body.model_dump(), scope="global", actor=f"admin:{current_user.id}")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/sms/accounts")
async def admin_sms_accounts(
    scope: str = "all",
    user_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return list_sales_agent_sms_accounts(db, current_user.id, scope=scope, admin=True, target_user_id=user_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/admin/sms/accounts")
async def admin_create_sms_account(
    body: SalesAgentSmsAccountIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return create_sales_agent_sms_account(db, current_user.id, body.model_dump(), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/admin/sms/accounts/{account_id}")
async def admin_update_sms_account(
    account_id: int,
    body: SalesAgentSmsAccountPatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return update_sales_agent_sms_account(db, current_user.id, account_id, body.model_dump(exclude_unset=True), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/admin/sms/accounts/{account_id}")
async def admin_disable_sms_account(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return disable_sales_agent_sms_account(db, current_user.id, account_id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/sms/accounts/{account_id}/templates")
async def admin_sms_templates(
    account_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return list_sales_agent_sms_templates(db, current_user.id, account_id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/admin/sms/accounts/{account_id}/templates")
async def admin_create_sms_template(
    account_id: int,
    body: SalesAgentSmsTemplateIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return create_sales_agent_sms_template(db, current_user.id, account_id, body.model_dump(), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/admin/sms/templates/{template_id}")
async def admin_update_sms_template(
    template_id: int,
    body: SalesAgentSmsTemplatePatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return update_sales_agent_sms_template(db, current_user.id, template_id, body.model_dump(exclude_unset=True), scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/admin/sms/templates/{template_id}")
async def admin_disable_sms_template(
    template_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return disable_sales_agent_sms_template(db, current_user.id, template_id, scope="global")
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/sms/accounts/{account_id}/test")
async def test_sms_account(
    account_id: int,
    body: SalesAgentSmsTestIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return test_sales_agent_sms_account(
            db,
            current_user.id,
            account_id,
            to_phone=body.to_phone,
            content=body.content,
            template_code=body.template_code,
            template_params=body.template_params,
            scope="user",
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sms/deliveries")
async def sms_deliveries(
    status: str = "",
    message_type: str = "",
    q: str = "",
    company_id: int | None = None,
    lead_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sales_agent_sms_deliveries(db, current_user.id, status=status, message_type=message_type, q=q, limit=limit, offset=offset, company_id=company_id, lead_id=lead_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sms/stats")
async def sms_stats(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return sales_agent_sms_stats(db, current_user.id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/admin/sms/accounts/{account_id}/test")
async def admin_test_sms_account(
    account_id: int,
    body: SalesAgentSmsTestIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return test_sales_agent_sms_account(
            db,
            current_user.id,
            account_id,
            to_phone=body.to_phone,
            content=body.content,
            template_code=body.template_code,
            template_params=body.template_params,
            scope="global",
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/sms/deliveries")
async def admin_sms_deliveries(
    status: str = "",
    message_type: str = "",
    q: str = "",
    user_id: int | None = None,
    company_id: int | None = None,
    lead_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return list_sales_agent_sms_deliveries(db, current_user.id, admin=True, status=status, message_type=message_type, q=q, limit=limit, offset=offset, target_user_id=user_id, company_id=company_id, lead_id=lead_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/admin/sms/stats")
async def admin_sms_stats(
    user_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_staff_user(current_user)
    try:
        return sales_agent_sms_stats(db, current_user.id, admin=True, target_user_id=user_id)
    except SalesAgentError as e:
        raise _http_error(e) from e


# ---------------------------------------------------------------------------
# The two login-free SMS endpoints. Carrier callbacks and short links on users' phones can't carry a JWT; identity relies respectively on
# the signature string in the URL and a one-time short-link token. They're placed here rather than under /public/* to sit with
# /email/open/{token}.png above — both are entries "called back from the outside world".
# ---------------------------------------------------------------------------


@router.post("/sms/callback/{provider}/{token}")
async def sms_callback(
    provider: str,
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delivery receipts / inbound SMS. Tencent Cloud sends a JSON array, Twilio sends form-encoded; split by content-type."""
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body: Any = await request.json()
        else:
            body = dict(await request.form())
    except Exception:  # noqa: BLE001 — even if the body can't be parsed, give a readable reason, not a 500
        raise HTTPException(status_code=400, detail="短信回调请求体无法解析,请确认平台配置的是 JSON 或表单格式。")
    try:
        return apply_sales_agent_sms_callback(db, provider, token, headers=dict(request.headers), body=body)
    except SalesAgentError as e:
        raise _http_error(e) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("Sales Agent sms callback failed")
        db.rollback()
        raise HTTPException(status_code=503, detail=_SAFE_SERVICE_ERROR) from e


@router.get("/sms/r/{short_link_token}")
async def sms_short_link(short_link_token: str, db: Session = Depends(get_db)):
    """Short link in an SMS: count a click, then 302 to the public report page.

    When no record is found, redirect to the home page instead of a 404 — the short link was sent to the user's phone, and after the delivery record is cleared
    opening it should be "nothing happens", not an error page.
    """
    try:
        result = track_sms_short_link(db, short_link_token)
    except Exception:  # noqa: BLE001 — a tracking failure must never block the redirect
        logger.exception("Sales Agent sms short link tracking failed")
        db.rollback()
        result = {"found": False, "url": ""}
    target = str(result.get("url") or "") or _sales_agent_public_base_url()
    return RedirectResponse(url=target, status_code=302)


@router.post("/leads/{lead_id}/status")
async def lead_status(
    lead_id: int,
    body: LeadStatusIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_lead_status(
            db,
            current_user.id,
            lead_id=lead_id,
            status=body.status,
            reason=body.reason,
            note=body.note,
            next_followup_at=body.next_followup_at,
            actor=body.actor or f"user:{current_user.id}",
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(15, ge=1, le=15),
    offset: int = Query(0, ge=0),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_sessions_page(db, current_user.id, limit=limit, offset=offset)
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sessions/current")
async def current_session(
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return session_bundle(db, current_user.id, public_base_url=_request_public_base_url(request))
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/sessions/undo")
async def undo_session(
    body: SalesAgentUndoIn,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        if not body.confirmed:
            raise HTTPException(status_code=400, detail="撤销前需要确认")
        deps = resolve_account(current_user, db, session_id=body.session_id)
        restored = _restore_undo_and_append_card(deps, "lead", checkpoint_id=body.checkpoint_id)
        bundle = session_bundle(db, current_user.id, session_id=deps.session_public_id, public_base_url=_request_public_base_url(request))
        bundle["undo"] = restored
        return bundle
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return session_bundle(db, current_user.id, session_id=session_id, public_base_url=_request_public_base_url(request))
    except SalesAgentError as e:
        raise _http_error(e) from e


# ---------------------------------------------------------------------------
# Option catalog — used by the hidden admin page /sales-agent/admin
#
# Cards already carry their own options; these are for the admin page to read and write, not on the conversation path.
# All three only accept current_user.id: someone guessing the URL can only see their own account's rows.
# ---------------------------------------------------------------------------

@router.get("/option-catalog")
async def get_option_catalog(
    kind: str = "",
    seller_profile_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return list_option_catalog(db, current_user.id, seller_profile_id=seller_profile_id, kind=kind)
    except SalesAgentError as e:
        raise _http_error(e) from e


def _sync_added_option_value(db: Session, user_id: int, kind: str, seller_profile_id: int | None, value: str) -> bool:
    if kind not in {CAPABILITY, TYPICAL_CUSTOMER} or seller_profile_id is None:
        return False
    row = _latest_seller_profile(db, user_id, seller_profile_id)
    if row is None:
        return False
    topic = topic_for_brand(db, row)
    if topic is not None:
        if kind == CAPABILITY:
            changed = write_agent_updates_to_topic(db, topic, capabilities=[value], actor_id=user_id)
        else:
            profile = brand_profile_payload(db, user_id, seller_profile_id).get("profile") or {}
            current = [str(item).strip() for item in profile.get("target_scenarios") or [] if str(item).strip()] if isinstance(profile, dict) else []
            merged = _merge_keep_order(current, [value])[:20]
            if merged == current:
                changed = False
            else:
                from geo.models.ai_telemetry import merge_profile_json

                profile["target_scenarios"] = merged
                topic.profile_json = merge_profile_json(topic, profile)
                changed = True
        if changed:
            sync_brand_from_topic(db, topic)
        db.commit()
        return True
    existing = seller_profile_for_lead_entry(db, user_id, seller_profile_id)
    seller = existing.get("seller_profile") if isinstance(existing.get("seller_profile"), dict) else {}
    create_or_update_seller_profile(
        db,
        user_id,
        seller_profile_id=seller_profile_id,
        legacy_profile_id=int(seller.get("legacy_profile_id") or 0) or None,
        service_scope=[str(item).strip() for item in (existing.get("service_scope") or []) if str(item).strip()],
        service_capabilities=_merge_keep_order(seller.get("service_capabilities"), [value] if kind == CAPABILITY else []),
        company_size=str(existing.get("company_size") or "").strip(),
        typical_customers=_merge_keep_order(existing.get("typical_customers"), [value] if kind == TYPICAL_CUSTOMER else []),
        default_buyer_roles=seller.get("default_buyer_roles") or None,
        default_exclude_rules=seller.get("default_exclude_rules") or None,
    )
    return True


def _sync_deleted_option_value(db: Session, user_id: int, kind: str, seller_profile_id: int | None, value: str) -> bool:
    if kind not in {CAPABILITY, TYPICAL_CUSTOMER} or seller_profile_id is None:
        return False
    row = _latest_seller_profile(db, user_id, seller_profile_id)
    return remove_agent_profile_option(db, row, kind, value, actor_id=user_id, commit=True)


@router.delete("/option-catalog/by-value")
async def archive_option_catalog_entry_by_value(
    kind: str = Query(...),
    seller_profile_id: int | None = None,
    value: str = Query(...),
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        archived = archive_custom_option_value(db, current_user.id, kind, seller_profile_id, value)
        synced = _sync_deleted_option_value(db, current_user.id, kind, seller_profile_id, value)
        return {"kind": kind, "value": value, "seller_profile_id": seller_profile_id, "archived": archived, "synced": synced}
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.post("/option-catalog")
async def add_option_catalog_entry(
    body: SalesAgentOptionIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        entry = create_custom_option(
            db,
            current_user.id,
            body.kind,
            body.seller_profile_id,
            body.value,
            label=body.label,
            description=body.description,
        )
        _sync_added_option_value(db, current_user.id, body.kind, body.seller_profile_id, body.value)
        return entry
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.patch("/option-catalog/{option_id}")
async def update_option_catalog_entry(
    option_id: int,
    body: SalesAgentOptionPatchIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return update_custom_option(
            db,
            current_user.id,
            option_id,
            label=body.label,
            description=body.description,
        )
    except SalesAgentError as e:
        raise _http_error(e) from e


@router.delete("/option-catalog/{option_id}")
async def archive_option_catalog_entry(
    option_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        archived = archive_custom_option(db, current_user.id, option_id)
        _sync_deleted_option_value(
            db,
            current_user.id,
            str(archived.get("kind") or ""),
            int(archived.get("seller_profile_id") or 0) or None,
            str(archived.get("value") or ""),
        )
        return archived
    except SalesAgentError as e:
        raise _http_error(e) from e


# ---------------------------------------------------------------------------
# Brand (= one business line) CRUD was removed on 2026-08-27 together with /sales-agent/admin.
#
# Brands are now mirrors of brand material (ai_telemetry_topics.profile_json),
# created/refreshed by `sync_brand_from_topic()` whenever the customer saves material, and soft-deleted by `archive_brand_for_topic()`
# when material is deleted. The frontend no longer has create/update/delete entries, so these four routes have no callers.
# The brand selection card on the conversation path uses `list_brands()`, a direct service-layer call, not via HTTP.
# ---------------------------------------------------------------------------
