"""Sales Agent service layer: SMS sending: number normalization, country-code routing, dispatch, persistence, short links and rate limiting.

Aligned step by step with `email_send.py`'s state machine (create row -> sending_enabled -> recipient -> channel ->
rate limit -> semaphore -> dispatch -> sent/failed, with an event per step); the differences are only SMS's own three things:

* **Two content modes**: CN channels can only send registered templates + variable slots; only international channels can send free copy.
* **Country-code routing**: before picking a channel, normalize the number to E.164 and extract the country, then hand it to
  `sms_config._effective_sms_config` to filter candidates by `region_scope`.
* **Rate limiting**: SMS is billed per segment and complaints cost far more than with email, so there is a daily cap and a same-number cooldown.

`_message_type_for_draft` / `_report_share_for_draft` / `_delivery_content_preview`
are reused directly from `email_send`: they are **draft-level** and channel-agnostic; rewriting them would hit
`test_service_layering.py`'s "same-named symbols must not be defined twice".
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, object_session

from agents.sales_agent.constants import BLOCKED_CONTACT_CONSENT
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentSmsAccountORM,
    SalesAgentSmsDeliveryORM,
    SalesAgentSmsTemplateORM,
)
from agents.sales_agent.service.sms_providers import (
    SmsChannelCredentials,
    SmsProviderError,
    provider_for_key,
    sms_segments,
)
from geo.models.user import UserORM

from agents.sales_agent.service.common import SalesAgentError, _dumps, _iso, _loads, session_has_table
from agents.sales_agent.service.entities import _record_sms_event, serialize_company
from agents.sales_agent.service.reports import _sales_agent_public_base_url, _sales_agent_report_public_url
from agents.sales_agent.service.sms_config import (
    SALES_AGENT_SMS_STATUSES,
    _decrypt_sms_secret,
    _effective_sms_config,
    _get_sms_account_for_scope,
    serialize_sales_agent_sms_account,
    usable_sms_template,
)
from agents.sales_agent.service.email_send import (
    _bj,
    _delivery_content_preview,
    _message_type_for_draft,
    _report_share_for_draft,
)

SALES_AGENT_SMS_SEND_CONCURRENCY = 10
_SMS_SEND_SEMAPHORE = threading.BoundedSemaphore(SALES_AGENT_SMS_SEND_CONCURRENCY)

# Country codes of length 1 and 2 in ITU-T E.164. All the rest are 3 digits; this rule is far more reliable than maintaining
# a country table of two hundred-plus rows, and extracting the country code only requires knowing "how many digits to cut".
_ONE_DIGIT_CODES = {"1", "7"}
_TWO_DIGIT_CODES = {
    "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44", "45",
    "46", "47", "48", "49", "51", "52", "53", "54", "55", "56", "57", "58", "60", "61",
    "62", "63", "64", "65", "66", "81", "82", "84", "86", "90", "91", "92", "93", "94",
    "95", "98",
}

# Dialing codes of common markets -> ISO 3166-1 alpha-2. Only covers ones actually used; for unlisted countries
# `recipient_country` stores "+<dialing code>": routing only needs to decide "is it CN", and storing it as-is beats guessing.
_DIAL_TO_ISO = {
    "1": "US", "7": "RU", "20": "EG", "27": "ZA", "30": "GR", "31": "NL", "32": "BE",
    "33": "FR", "34": "ES", "36": "HU", "39": "IT", "40": "RO", "41": "CH", "43": "AT",
    "44": "GB", "45": "DK", "46": "SE", "47": "NO", "48": "PL", "49": "DE", "51": "PE",
    "52": "MX", "54": "AR", "55": "BR", "56": "CL", "57": "CO", "58": "VE", "60": "MY",
    "61": "AU", "62": "ID", "63": "PH", "64": "NZ", "65": "SG", "66": "TH", "81": "JP",
    "82": "KR", "84": "VN", "86": "CN", "90": "TR", "91": "IN", "92": "PK", "98": "IR",
    "351": "PT", "353": "IE", "358": "FI", "380": "UA", "420": "CZ", "852": "HK",
    "853": "MO", "886": "TW", "966": "SA", "971": "AE", "972": "IL", "234": "NG",
}

_SMS_UNSUBSCRIBE_HINT = "回复TD退订"


def normalize_msisdn(raw: str, default_region: str = "CN") -> tuple[str, str, bool]:
    """Normalize a number written in any form to `(E.164, country, is mobile)`.

    The country is ISO alpha-2 (or "+<dialing code>" if not found); `is_mobile` is only reliable for CN numbers:
    overseas ranges have no universal prefix rule for mobile vs landline, so they are treated as sendable, with platform receipts as the fallback when they fail.
    """
    text = str(raw or "").strip()
    if not text:
        return "", "", False
    plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return "", "", False
    if digits.startswith("00"):
        digits, plus = digits[2:], True
    if not plus:
        # No country code: 11 digits starting with 1[3-9] are treated as CN mobile and get +86; others use the default region,
        # and if that cannot be completed the number is invalid: no guessing, a wrong guess means messaging a stranger in another country.
        region = str(default_region or "CN").upper()
        if region == "CN":
            if len(digits) == 13 and digits.startswith("86"):
                plus = True  # 86 written without + / 00; treat as already having a country code
            elif re.fullmatch(r"1[3-9]\d{9}", digits):
                return f"+86{digits}", "CN", True
            else:
                return "", "CN", False
        else:
            return "", region, False

    code = ""
    if digits[:1] in _ONE_DIGIT_CODES:
        code = digits[:1]
    elif digits[:2] in _TWO_DIGIT_CODES:
        code = digits[:2]
    elif len(digits) > 3:
        code = digits[:3]
    if not code:
        return "", "", False
    national = digits[len(code):]
    if not national:
        return "", _DIAL_TO_ISO.get(code, f"+{code}"), False
    country = _DIAL_TO_ISO.get(code, f"+{code}")
    if country == "CN":
        # Only 11 digits starting with 1[3-9] are mobile numbers; 400/800/95/landlines are all judged unable to receive SMS.
        return f"+{code}{national}", country, bool(re.fullmatch(r"1[3-9]\d{9}", national))
    return f"+{code}{national}", country, True


def _resolve_sms_recipient(
    db: Session,
    lead: SalesAgentLeadRecommendationORM,
    contact_id: int | None = None,
) -> tuple[SalesAgentCompanyContactORM | None, str, str, str]:
    """Pick a contact that can receive SMS, returning `(contact, E.164, country, name)`.

    The difference from email is an extra `is_mobile` filter: a sizable share of stored numbers are 400/landline
    (and `contact_extraction.py` even weights them rather high); sending to them only wastes money on failures.
    """
    def _pick(row: SalesAgentCompanyContactORM) -> tuple[str, str, bool]:
        return normalize_msisdn(row.phone)

    if contact_id:
        contact = (
            db.query(SalesAgentCompanyContactORM)
            .filter(SalesAgentCompanyContactORM.id == int(contact_id),
                    SalesAgentCompanyContactORM.company_id == lead.company_id)
            .first()
        )
        if contact is not None and contact.consent_status not in BLOCKED_CONTACT_CONSENT:
            phone, country, is_mobile = _pick(contact)
            if phone and is_mobile:
                return contact, phone, country, contact.contact_name or contact.position or ""
    rows = (
        db.query(SalesAgentCompanyContactORM)
        .filter(SalesAgentCompanyContactORM.company_id == lead.company_id,
                SalesAgentCompanyContactORM.phone != "")
        .order_by(SalesAgentCompanyContactORM.is_reachable.desc(), SalesAgentCompanyContactORM.id.desc())
        .limit(20)
        .all()
    )
    for row in rows:
        if row.consent_status in BLOCKED_CONTACT_CONSENT:
            continue
        phone, country, is_mobile = _pick(row)
        if phone and is_mobile:
            return row, phone, country, row.contact_name or row.position or ""
    return None, "", "", ""


def sms_recipient_country_for_lead(db: Session, lead: SalesAgentLeadRecommendationORM, contact_id: int | None = None) -> str:
    """Know which country we are sending to before picking a channel: the routing basis; if unavailable return an empty string to use all candidates."""
    _, _, country, _ = _resolve_sms_recipient(db, lead, contact_id)
    return country


def _sms_short_link_url(token: str, public_base_url: str = "") -> str:
    if not token:
        return ""
    base = (public_base_url or _sales_agent_public_base_url()).rstrip("/")
    return f"{base}/api/sales-agent/sms/r/{token}"


def _apply_sms_short_link(body: str, public_url: str, short_url: str) -> str:
    """Replace long report links in the body with short links.

    SMS is billed per 70 characters, and one public report link can eat most of a segment; short links save money and are also the SMS version of
    "open tracking": SMS has no pixel like email, so clicks are the only observable engagement signal.
    """
    if not public_url or not short_url:
        return body
    return body.replace(public_url, short_url)


def _sms_credentials(account: SalesAgentSmsAccountORM) -> SmsChannelCredentials:
    return SmsChannelCredentials(
        provider=account.provider,
        account_id=int(account.id),
        display_name=account.display_name or "",
        sign_name=account.sign_name or "",
        sdk_app_id=account.sdk_app_id or "",
        api_key_id=_decrypt_sms_secret(account.api_key_id_encrypted),
        api_secret=_decrypt_sms_secret(account.api_secret_encrypted),
        api_base_url=account.api_base_url or "",
        from_number=account.from_number or "",
        provider_config=_loads(account.provider_config_json, {}),
    )


def _sms_rate_limit_reason(db: Session, user_id: int, phone: str, cfg: dict[str, Any]) -> str:
    """Return the reason for refusing to send; return an empty string if sending is allowed.

    Both gates only count **actually sent** messages (sent/delivered), not failed: a platform rejection
    disturbed nobody and should not consume the user's quota.
    """
    sent_statuses = ("sent", "delivered")
    cooldown_days = int(cfg.get("per_contact_cooldown_days") or 0)
    if cooldown_days > 0 and phone:
        since = datetime.utcnow() - timedelta(days=cooldown_days)
        recent = (
            db.query(SalesAgentSmsDeliveryORM)
            .filter(SalesAgentSmsDeliveryORM.user_id == user_id,
                    SalesAgentSmsDeliveryORM.recipient_phone == phone,
                    SalesAgentSmsDeliveryORM.status.in_(sent_statuses),
                    SalesAgentSmsDeliveryORM.sent_at.isnot(None),
                    SalesAgentSmsDeliveryORM.sent_at >= since)
            .order_by(SalesAgentSmsDeliveryORM.sent_at.desc())
            .first()
        )
        if recent is not None:
            next_at = recent.sent_at + timedelta(days=cooldown_days)
            return (
                f"该号码在 {cooldown_days} 天冷却期内已发送过短信"
                f"(上次 {recent.sent_at.strftime('%Y-%m-%d %H:%M')} UTC),"
                f"{next_at.strftime('%Y-%m-%d %H:%M')} UTC 之后才能再次发送。"
                "如需调整,请到短信管理页改「同号码冷却天数」。"
            )
    daily_cap = int(cfg.get("daily_send_cap") or 0)
    if daily_cap > 0:
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        used = (
            db.query(func.count(SalesAgentSmsDeliveryORM.id))
            .filter(SalesAgentSmsDeliveryORM.user_id == user_id,
                    SalesAgentSmsDeliveryORM.status.in_(sent_statuses),
                    SalesAgentSmsDeliveryORM.sent_at.isnot(None),
                    SalesAgentSmsDeliveryORM.sent_at >= day_start)
            .scalar() or 0
        )
        if int(used) >= daily_cap:
            return (
                f"今日短信发送量已达上限 {daily_cap} 条(已发 {int(used)} 条)。"
                "如需提高,请到短信管理页改「每日发送上限」。"
            )
    return ""


def _template_params_from_draft(meta: dict[str, Any], template: SalesAgentSmsTemplateORM | None) -> tuple[list[str], str]:
    """Get template variables, returning `(params, missing reason)`.

    Slots are positional per `{1}{2}`, and the platform rejects the whole message if one is missing, so better to report clearly which one is missing here
    than to fill in an empty string and send: a customer receiving 「尊敬的 ,您好」 ("Dear , hello") is worse than receiving nothing.
    """
    raw = meta.get("sms_template_params")
    slots = _loads(template.param_slots_json, []) if template is not None else []
    if isinstance(raw, dict):
        values = [str(raw.get(str(slot.get("index") or i + 1)) or raw.get(str(slot.get("name") or "")) or "")
                  for i, slot in enumerate(slots)] if slots else []
    elif isinstance(raw, list):
        values = [str(v) for v in raw]
    else:
        values = []
    if slots and len(values) < len(slots):
        missing = [str(slot.get("name") or slot.get("index") or (i + 1)) for i, slot in enumerate(slots) if i >= len(values) or not values[i]]
        if missing:
            return values, f"模板 {template.template_code} 还缺变量:{'、'.join(missing)}"
    empty = [str(slots[i].get("name") if i < len(slots) else i + 1) for i, v in enumerate(values) if not str(v).strip()]
    if empty:
        return values, f"模板变量 {'、'.join(empty)} 为空,平台会直接拒绝下发"
    return values, ""


def _render_template_preview(template: SalesAgentSmsTemplateORM | None, params: list[str]) -> str:
    """Fill `{1}{2}` with actual text, only for `content_preview` and frontend echo.

    What is actually dispatched is the template code + variables, not this text; do not use it to call the platform API.
    """
    if template is None:
        return ""
    body = str(template.body or "")
    for i, value in enumerate(params, start=1):
        body = body.replace("{" + str(i) + "}", str(value))
    return body


def send_sales_agent_sms_delivery(
    db: Session,
    user_id: int,
    draft: SalesAgentOutreachLogORM,
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    public_base_url: str = "",
) -> SalesAgentSmsDeliveryORM:
    if draft.channel != "sms":
        raise SalesAgentError("当前草稿不是短信渠道,不能按短信发送。", status_code=400)
    meta = _loads(getattr(draft, "metadata_json", "{}"), {})
    try:
        metadata_contact_id = int(meta.get("contact_id") or 0) or None
    except Exception:  # noqa: BLE001
        metadata_contact_id = None
    contact, phone, country, recipient_name = _resolve_sms_recipient(db, lead, metadata_contact_id)
    cfg = _effective_sms_config(db, user_id, country)
    account: SalesAgentSmsAccountORM | None = cfg.get("account")
    share = _report_share_for_draft(db, user_id, draft, lead)
    body = str(draft.content or "").strip()

    delivery = SalesAgentSmsDeliveryORM(
        user_id=user_id,
        lead_id=lead.id,
        company_id=company.id,
        contact_id=contact.id if contact else None,
        outreach_log_id=draft.id,
        sms_account_id=account.id if account else None,
        report_share_id=share.id if share else None,
        message_type=_message_type_for_draft(draft),
        provider=account.provider if account else "",
        sign_name=account.sign_name if account else "",
        recipient_phone=phone,
        recipient_country=country,
        recipient_name=recipient_name,
        content_preview=_delivery_content_preview(body),
        segments=sms_segments(body),
        status="sending",
        metadata_json=_dumps({
            "account_source": cfg.get("account_source"),
            "sender_selection_policy": cfg.get("sender_selection_policy"),
            "receipt_enabled": bool(cfg.get("receipt_enabled")),
            "content_mode": account.content_mode if account else "",
            "draft_kind": meta.get("kind"),
            "content": body,
        }),
    )
    db.add(delivery)
    db.flush()

    if not cfg.get("sending_enabled"):
        delivery.status = "disabled"
        delivery.failure_reason = "；".join(cfg.get("disabled_reasons") or ["短信发送已关闭"])
        _record_sms_event(db, delivery, user_id, "send_disabled", {"reasons": cfg.get("disabled_reasons") or []})
        return delivery
    if not phone:
        delivery.status = "no_recipient"
        delivery.failure_reason = "目标企业没有可发短信的手机号(只有座机/400 号码或号码格式无法识别)"
        _record_sms_event(db, delivery, user_id, "no_recipient", {"company_id": company.id})
        return delivery
    if account is None:
        delivery.status = "disabled"
        delivery.failure_reason = "；".join(cfg.get("disabled_reasons") or ["未配置可用短信通道"])
        _record_sms_event(db, delivery, user_id, "send_disabled", {"reasons": [delivery.failure_reason]})
        return delivery

    limit_reason = _sms_rate_limit_reason(db, user_id, phone, cfg)
    if limit_reason:
        delivery.status = "rejected"
        delivery.failure_code = "rate_limited"
        delivery.failure_reason = limit_reason
        delivery.failed_at = datetime.utcnow()
        _record_sms_event(db, delivery, user_id, "rejected", {"reason": limit_reason, "phone": phone})
        return delivery

    template: SalesAgentSmsTemplateORM | None = None
    template_params: list[str] = []
    text_for_send = ""
    if account.content_mode == "template":
        template_code = str(meta.get("sms_template_code") or "").strip()
        if template_code:
            template = (
                db.query(SalesAgentSmsTemplateORM)
                .filter(SalesAgentSmsTemplateORM.account_id == int(account.id),
                        SalesAgentSmsTemplateORM.template_code == template_code)
                .first()
            )
        if template is None:
            template = usable_sms_template(db, account, purpose=_message_type_for_draft(draft) if _message_type_for_draft(draft) in {"followup", "test"} else "outreach")
        if template is None:
            reason = (
                f"通道「{account.display_name or account.provider}」是国内模板通道,只能下发已报备且审核通过的模板,"
                "当前草稿没有指定模板。请到短信管理页给该通道添加一条 approved 状态的模板,再重新发送。"
            )
            delivery.status = "rejected"
            delivery.failure_code = "missing_template"
            delivery.failure_reason = reason
            delivery.failed_at = datetime.utcnow()
            _record_sms_event(db, delivery, user_id, "rejected", {"reason": reason, "account_id": account.id})
            return delivery
        template_params, missing = _template_params_from_draft(meta, template)
        if missing:
            delivery.status = "rejected"
            delivery.failure_code = "missing_template_params"
            delivery.failure_reason = f"{missing}。请在草稿里补齐模板变量后再发送。"
            delivery.failed_at = datetime.utcnow()
            _record_sms_event(db, delivery, user_id, "rejected", {"reason": delivery.failure_reason, "template_code": template.template_code})
            return delivery
        delivery.template_code = template.template_code
        delivery.template_params_json = _dumps(template_params)
        text_for_send = _render_template_preview(template, template_params)
    else:
        if not body:
            delivery.status = "rejected"
            delivery.failure_code = "empty_body"
            delivery.failure_reason = "短信正文为空,不能发送。"
            delivery.failed_at = datetime.utcnow()
            _record_sms_event(db, delivery, user_id, "rejected", {"reason": delivery.failure_reason})
            return delivery
        text_for_send = body
        if share is not None:
            public_url = _sales_agent_report_public_url(share.public_token, public_base_url)
            short_url = _sms_short_link_url(delivery.short_link_token, public_base_url)
            text_for_send = _apply_sms_short_link(text_for_send, public_url, short_url)

    delivery.content_preview = _delivery_content_preview(text_for_send)
    delivery.segments = sms_segments(text_for_send)
    now = datetime.utcnow()
    delivery.send_attempted_at = now
    _record_sms_event(db, delivery, user_id, "send_attempt", {"provider": account.provider, "segments": delivery.segments})

    acquired = _SMS_SEND_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        delivery.status = "failed"
        delivery.failure_code = "queue_busy"
        delivery.failure_reason = f"短信发送队列忙碌,当前最多同时发送 {SALES_AGENT_SMS_SEND_CONCURRENCY} 条,请稍后重试。"
        delivery.failed_at = datetime.utcnow()
        _record_sms_event(db, delivery, user_id, "failed", {"reason": delivery.failure_reason, "provider": account.provider})
        return delivery
    try:
        try:
            provider = provider_for_key(account.provider)
            result = provider.send(
                _sms_credentials(account),
                phone,
                text=text_for_send,
                template_code=delivery.template_code or "",
                template_params=template_params,
            )
        except SmsProviderError as e:
            delivery.status = "failed"
            delivery.failure_code = e.code[:120]
            delivery.failure_reason = str(e)
            delivery.failed_at = datetime.utcnow()
            _record_sms_event(db, delivery, user_id, "failed", {"reason": str(e), "code": e.code, "provider": account.provider})
            return delivery
        delivery.status = "sent"
        delivery.sent_at = datetime.utcnow()
        delivery.provider_message_id = str(result.provider_message_id or "")[:255]
        if result.segments:
            delivery.segments = int(result.segments)
        _record_sms_event(db, delivery, user_id, "sent", {
            "provider": account.provider,
            "provider_message_id": delivery.provider_message_id,
            "segments": delivery.segments,
        })
        return delivery
    finally:
        _SMS_SEND_SEMAPHORE.release()


def test_sales_agent_sms_account(
    db: Session,
    user_id: int,
    account_id: int,
    to_phone: str = "",
    content: str = "",
    template_code: str = "",
    template_params: list[str] | None = None,
    scope: str = "user",
) -> dict[str, Any]:
    """Send a test SMS on a channel and write the result back to `last_test_*`.

    Template channels require a template code: if missing, say where to choose one; no "just pick any template and send" fallback,
    since that would test whether another template can send, not the one the user wants to test.
    """
    account = _get_sms_account_for_scope(db, account_id, scope, user_id)
    phone, country, is_mobile = normalize_msisdn(to_phone)
    if not phone:
        raise SalesAgentError("请填写有效的测试手机号(建议写成 +8613800138000 这样的 E.164 格式)。")
    if not is_mobile:
        raise SalesAgentError("测试号码不是手机号(400/800/座机收不了短信),请换一个手机号。")

    resolved_template: SalesAgentSmsTemplateORM | None = None
    params = [str(p) for p in (template_params or [])]
    if account.content_mode == "template":
        code = str(template_code or "").strip()
        if not code:
            raise SalesAgentError("该通道是国内模板通道,请先选择一条已报备的模板再测试。")
        resolved_template = (
            db.query(SalesAgentSmsTemplateORM)
            .filter(SalesAgentSmsTemplateORM.account_id == int(account.id),
                    SalesAgentSmsTemplateORM.template_code == code)
            .first()
        )
        if resolved_template is None:
            raise SalesAgentError(f"该通道下没有模板号为 {code} 的模板,请到模板列表核对。")
        slots = _loads(resolved_template.param_slots_json, [])
        if len(params) < len(slots):
            raise SalesAgentError(f"该模板需要 {len(slots)} 个变量,当前只填了 {len(params)} 个。")
        text = _render_template_preview(resolved_template, params)
    else:
        text = str(content or "").strip() or f"这是一条 Sales Agent 短信通道测试。收到说明该通道可以正常发送。{_SMS_UNSUBSCRIBE_HINT}"

    delivery = SalesAgentSmsDeliveryORM(
        user_id=user_id,
        sms_account_id=account.id,
        message_type="test",
        provider=account.provider,
        sign_name=account.sign_name or "",
        template_code=resolved_template.template_code if resolved_template else "",
        template_params_json=_dumps(params) if params else "[]",
        recipient_phone=phone,
        recipient_country=country,
        content_preview=_delivery_content_preview(text),
        segments=sms_segments(text),
        status="sending",
        metadata_json=_dumps({"scope": scope, "content": text, "content_mode": account.content_mode}),
    )
    db.add(delivery)
    db.flush()
    delivery.send_attempted_at = datetime.utcnow()
    _record_sms_event(db, delivery, user_id, "send_attempt", {"message_type": "test", "provider": account.provider})
    try:
        provider = provider_for_key(account.provider)
        result = provider.send(
            _sms_credentials(account), phone,
            text=text, template_code=delivery.template_code or "", template_params=params,
        )
    except SmsProviderError as e:
        delivery.status = "failed"
        delivery.failure_code = e.code[:120]
        delivery.failure_reason = str(e)
        delivery.failed_at = datetime.utcnow()
        account.last_tested_at = datetime.utcnow()
        account.last_test_status = "failed"
        account.last_test_error = str(e)[:500]
        _record_sms_event(db, delivery, user_id, "failed", {"message_type": "test", "reason": str(e), "code": e.code})
        db.commit()
        return {"ok": False, "error": str(e), "delivery": serialize_sales_agent_sms_delivery(delivery),
                "account": serialize_sales_agent_sms_account(account)}
    delivery.status = "sent"
    delivery.sent_at = datetime.utcnow()
    delivery.provider_message_id = str(result.provider_message_id or "")[:255]
    if result.segments:
        delivery.segments = int(result.segments)
    account.last_tested_at = datetime.utcnow()
    account.last_test_status = "ok"
    account.last_test_error = ""
    _record_sms_event(db, delivery, user_id, "sent", {"message_type": "test", "provider": account.provider})
    db.commit()
    return {"ok": True, "delivery": serialize_sales_agent_sms_delivery(delivery),
            "account": serialize_sales_agent_sms_account(account)}


def serialize_sales_agent_sms_delivery(row: SalesAgentSmsDeliveryORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    metadata = _loads(row.metadata_json, {})
    content = str(metadata.get("content") or "").strip()
    db = object_session(row)
    if not content and db is not None and row.outreach_log_id:
        draft = db.get(SalesAgentOutreachLogORM, row.outreach_log_id)
        if draft is not None:
            content = str(draft.content or "").strip()
    payload = {
        "id": row.id,
        "channel": "sms",
        "user_id": row.user_id,
        "lead_id": row.lead_id,
        "company_id": row.company_id,
        "contact_id": row.contact_id,
        "outreach_log_id": row.outreach_log_id,
        "sms_account_id": row.sms_account_id,
        "report_share_id": row.report_share_id,
        "message_type": row.message_type,
        "provider": row.provider,
        "provider_message_id": row.provider_message_id,
        "sign_name": row.sign_name,
        "template_code": row.template_code,
        "template_params": _loads(row.template_params_json, []),
        "recipient_phone": row.recipient_phone,
        "recipient_country": row.recipient_country,
        "recipient_name": row.recipient_name,
        "content_preview": row.content_preview,
        "content": content or row.content_preview,
        "segments": row.segments,
        "status": row.status,
        "failure_code": row.failure_code,
        "failure_reason": row.failure_reason,
        "send_attempted_at": _iso(row.send_attempted_at),
        "sent_at": _iso(row.sent_at),
        "delivered_at": _iso(row.delivered_at),
        "failed_at": _iso(row.failed_at),
        "first_click_at": _iso(row.first_click_at),
        "last_click_at": _iso(row.last_click_at),
        "click_count": row.click_count,
        "reply_count": row.reply_count,
        "opted_out_at": _iso(row.opted_out_at),
        "metadata": {k: v for k, v in metadata.items() if k != "content"},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if db is not None:
        payload["send_count"] = (
            db.query(SalesAgentSmsDeliveryORM)
            .filter(SalesAgentSmsDeliveryORM.outreach_log_id == row.outreach_log_id,
                    SalesAgentSmsDeliveryORM.status.in_(("sent", "delivered")))
            .count()
        ) if row.outreach_log_id else (1 if row.status in ("sent", "delivered") else 0)
        if row.company_id:
            company = db.get(SalesAgentCompanyORM, row.company_id)
            if company is not None:
                payload["company"] = serialize_company(company)
                payload["company_name"] = company.name
        user = db.get(UserORM, row.user_id) if row.user_id else None
        if user is not None:
            payload["user_email"] = user.email
    return payload


def _latest_sms_delivery_for_draft(db: Session, draft_id: int) -> SalesAgentSmsDeliveryORM | None:
    return (
        db.query(SalesAgentSmsDeliveryORM)
        .filter(SalesAgentSmsDeliveryORM.outreach_log_id == int(draft_id))
        .order_by(SalesAgentSmsDeliveryORM.id.desc())
        .first()
    )


def list_sales_agent_sms_deliveries(
    db: Session,
    user_id: int,
    admin: bool = False,
    status: str = "",
    message_type: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    target_user_id: int | None = None,
    company_id: int | None = None,
    lead_id: int | None = None,
) -> dict[str, Any]:
    query = db.query(SalesAgentSmsDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == user_id)
    if company_id:
        query = query.filter(SalesAgentSmsDeliveryORM.company_id == int(company_id))
    if lead_id:
        query = query.filter(SalesAgentSmsDeliveryORM.lead_id == int(lead_id))
    if status:
        query = query.filter(SalesAgentSmsDeliveryORM.status == status)
    if message_type:
        query = query.filter(SalesAgentSmsDeliveryORM.message_type == message_type)
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(SalesAgentCompanyORM, SalesAgentSmsDeliveryORM.company_id == SalesAgentCompanyORM.id).filter(
            or_(SalesAgentSmsDeliveryORM.recipient_phone.ilike(like),
                SalesAgentSmsDeliveryORM.content_preview.ilike(like),
                SalesAgentCompanyORM.name.ilike(like))
        )
    total = query.count()
    rows = (
        query.order_by(SalesAgentSmsDeliveryORM.id.desc())
        .offset(max(0, int(offset or 0)))
        .limit(max(1, min(int(limit or 50), 100)))
        .all()
    )
    return {"deliveries": [serialize_sales_agent_sms_delivery(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _sms_tables_ready(db: Session) -> bool:
    """Whether the SMS table exists.

    Some unit tests only create the email delivery table (see `tests/sales_agent/test_delivery_status.py`);
    after the stats metric definition was merged, blindly joining the SMS table would blow those tests up with "table does not exist",
    and SMS is not what they verify at all.
    """
    return session_has_table(db, SalesAgentSmsDeliveryORM.__tablename__)


def agent_sms_delivery_status(
    db: Session,
    user_id: int,
    company_name: str = "",
    lead_id: int | None = None,
    status: str = "",
    days: int = 0,
    limit: int = 20,
    since: datetime | None = None,
    until: datetime | None = None,
    admin: bool = False,
    target_user_id: int | None = None,
    activity: str = "",
) -> dict[str, Any]:
    """**SMS** delivery status for the Agent to read. Fields align with the email version; the difference is no "opens", only clicks.

    The external entry point is `delivery_stats.agent_delivery_status`, which merges both channels.
    """
    empty = {"total_matched": 0, "returned": 0, "summary": {
        "sent": 0, "failed": 0, "attempted": 0, "clicked": 0, "delivered": 0,
        "click_count": 0, "segments": 0,
        "sent_companies": 0, "attempted_companies": 0, "clicked_companies": 0,
    }, "deliveries": []}
    if not _sms_tables_ready(db):
        return empty

    query = db.query(SalesAgentSmsDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == user_id)
    if lead_id:
        query = query.filter(SalesAgentSmsDeliveryORM.lead_id == int(lead_id))
    if status:
        query = query.filter(SalesAgentSmsDeliveryORM.status == status)
    delivery_time = func.coalesce(SalesAgentSmsDeliveryORM.sent_at, SalesAgentSmsDeliveryORM.created_at)
    if since is not None:
        query = query.filter(delivery_time >= since)
    elif days and int(days) > 0:
        query = query.filter(delivery_time >= datetime.utcnow() - timedelta(days=int(days)))
    if until is not None:
        query = query.filter(delivery_time < until)
    name = (company_name or "").strip()
    if name:
        like = f"%{name}%"
        query = query.outerjoin(SalesAgentCompanyORM, SalesAgentSmsDeliveryORM.company_id == SalesAgentCompanyORM.id).filter(
            or_(SalesAgentCompanyORM.name.ilike(like), SalesAgentSmsDeliveryORM.recipient_phone.ilike(like))
        )

    total = query.count()

    def distinct_company_count(q) -> int:
        return q.with_entities(SalesAgentSmsDeliveryORM.company_id).filter(SalesAgentSmsDeliveryORM.company_id.isnot(None)).distinct().count()

    # For SMS both sent and delivered count as "sent out": delivered is the upgraded state after receipt confirmation,
    # and counting only sent would make channels with receipts look like they sent less.
    sent_query = query.filter(SalesAgentSmsDeliveryORM.status.in_(("sent", "delivered")))
    delivered_query = query.filter(SalesAgentSmsDeliveryORM.status == "delivered")
    failed_query = query.filter(SalesAgentSmsDeliveryORM.status.in_(("failed", "rejected")))
    attempted_query = query.filter(SalesAgentSmsDeliveryORM.status.in_(("sent", "delivered", "failed", "rejected")))
    clicked_query = query.filter(SalesAgentSmsDeliveryORM.click_count > 0)
    click_total = int(query.with_entities(func.coalesce(func.sum(SalesAgentSmsDeliveryORM.click_count), 0)).scalar() or 0)
    segments_total = int(query.with_entities(func.coalesce(func.sum(SalesAgentSmsDeliveryORM.segments), 0)).scalar() or 0)
    row_query = (
        clicked_query if activity in {"click", "report_visit", "open"}
        else failed_query if activity == "failed"
        else sent_query if activity == "sent"
        else query
    )
    rows = row_query.order_by(SalesAgentSmsDeliveryORM.id.desc()).limit(max(1, min(int(limit or 20), 50))).all()

    items = []
    for row in rows:
        company = db.get(SalesAgentCompanyORM, row.company_id) if row.company_id else None
        items.append({
            "channel": "sms",
            "delivery_id": row.id,
            "lead_id": row.lead_id,
            "company_name": company.name if company is not None else "",
            "recipient_phone": row.recipient_phone,
            "recipient_country": row.recipient_country,
            "status": row.status,
            "segments": row.segments,
            "sent_at": _bj(row.sent_at),
            "delivered_at": _bj(row.delivered_at),
            "failed_at": _bj(row.failed_at),
            "failure_reason": row.failure_reason or "",
            "clicked": int(row.click_count or 0) > 0,
            "click_count": row.click_count,
            "first_click_at": _bj(row.first_click_at),
            "reply_count": row.reply_count,
            "opted_out_at": _bj(row.opted_out_at),
        })

    return {
        "total_matched": total,
        "returned": len(items),
        "summary": {
            "sent": sent_query.count(),
            "delivered": delivered_query.count(),
            "failed": failed_query.count(),
            "attempted": attempted_query.count(),
            "clicked": clicked_query.count(),
            "click_count": click_total,
            "segments": segments_total,
            "sent_companies": distinct_company_count(sent_query),
            "attempted_companies": distinct_company_count(attempted_query),
            "clicked_companies": distinct_company_count(clicked_query),
        },
        "deliveries": items,
    }


def sales_agent_sms_stats(db: Session, user_id: int, admin: bool = False, target_user_id: int | None = None) -> dict[str, Any]:
    if not _sms_tables_ready(db):
        return {"total": 0, "sent": 0, "delivered": 0, "failed": 0, "rejected": 0, "disabled": 0,
                "no_recipient": 0, "sending": 0, "click_count": 0, "reply_count": 0,
                "opted_out": 0, "segments": 0}
    query = db.query(SalesAgentSmsDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentSmsDeliveryORM.user_id == user_id)
    total = query.count()
    counts = {status: query.filter(SalesAgentSmsDeliveryORM.status == status).count() for status in SALES_AGENT_SMS_STATUSES}
    sums = query.with_entities(
        func.coalesce(func.sum(SalesAgentSmsDeliveryORM.click_count), 0),
        func.coalesce(func.sum(SalesAgentSmsDeliveryORM.reply_count), 0),
        func.coalesce(func.sum(SalesAgentSmsDeliveryORM.segments), 0),
    ).first()
    return {
        "total": total,
        "sent": counts.get("sent", 0),
        "delivered": counts.get("delivered", 0),
        "failed": counts.get("failed", 0),
        "rejected": counts.get("rejected", 0),
        "disabled": counts.get("disabled", 0),
        "no_recipient": counts.get("no_recipient", 0),
        "sending": counts.get("sending", 0),
        "click_count": int(sums[0] or 0) if sums else 0,
        "reply_count": int(sums[1] or 0) if sums else 0,
        "opted_out": query.filter(SalesAgentSmsDeliveryORM.opted_out_at.isnot(None)).count(),
        "segments": int(sums[2] or 0) if sums else 0,
    }


def track_sales_agent_sms_click(db: Session, short_link_token: str) -> SalesAgentSmsDeliveryORM | None:
    """Short link opened: the SMS version of "open tracking". Returns the delivery row; the endpoint decides where to redirect."""
    token = str(short_link_token or "").strip()
    if not token:
        return None
    row = (
        db.query(SalesAgentSmsDeliveryORM)
        .filter(SalesAgentSmsDeliveryORM.short_link_token == token)
        .first()
    )
    if row is None:
        return None
    now = datetime.utcnow()
    if row.first_click_at is None:
        row.first_click_at = now
    row.last_click_at = now
    row.click_count = int(row.click_count or 0) + 1
    _record_sms_event(db, row, row.user_id, "clicked", {"click_count": row.click_count})
    return row
