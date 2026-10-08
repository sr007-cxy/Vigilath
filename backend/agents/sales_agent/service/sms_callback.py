"""Sales Agent service layer: persisting SMS delivery receipt / inbound SMS callbacks.

SMS has no open pixel like email; `sent` only means "accepted by the platform", and whether it actually reached the phone requires the carrier receipt.
Without receipts every record stays at `sent` forever, and "delivered" in the stats is a guess.

Inbound SMS matters even more: a user replying TD / STOP is unsubscribing, and the Provisions on the Administration of Short Message Services require stopping immediately.
So unsubscribe handling here is not a nice-to-have but the compliance baseline: on receipt, write contact/lead on the spot, no queue, no async.

The endpoint requires no login (carriers do not log in with a token), so identity relies on the
`channel_callback_token` signature in the URL: holding a valid signature tells which channel account this callback belongs to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agents.sales_agent.constants import BLOCKED_CONTACT_CONSENT
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentReportShareORM,
    SalesAgentSmsAccountORM,
    SalesAgentSmsDeliveryORM,
)
from agents.sales_agent.service.sms_providers import SmsCallbackEvent, SmsProviderError, provider_for_key
from agents.sales_agent.service.common import SalesAgentError, record_compliance_event
from agents.sales_agent.service.channel_common import verify_channel_callback_token
from agents.sales_agent.service.entities import _record_sms_event
from agents.sales_agent.service.reports import _sales_agent_report_public_url
from agents.sales_agent.service.sms_config import SMS_CALLBACK_TOKEN_KIND
from agents.sales_agent.service.sms_send import (
    _sms_credentials,
    _sms_tables_ready,
    normalize_msisdn,
    track_sales_agent_sms_click,
)

# Do not change status back on these events: a row already delivered should not be knocked back by a late sent receipt.
_TERMINAL_STATUSES = ("delivered", "failed", "rejected")


def resolve_sms_callback_account(db: Session, provider: str, token: str) -> SalesAgentSmsAccountORM:
    """Find the channel account by provider + token in the URL. If not found, raise an explicit error rather than a silent 200.

    Silent success is the most common pitfall for such endpoints: the carrier side keeps showing "callback OK" while not a single one is persisted here.
    """
    if not _sms_tables_ready(db):
        raise SalesAgentError("短信通道表尚未创建,回调无处落库。请先在部署机执行 alembic upgrade。", status_code=503)
    account_id = verify_channel_callback_token(SMS_CALLBACK_TOKEN_KIND, token)
    if not account_id:
        raise SalesAgentError("回调地址里的校验串不正确,请到短信通道管理页复制最新的回调地址。", status_code=403)
    account = db.get(SalesAgentSmsAccountORM, account_id)
    if account is None:
        raise SalesAgentError("回调地址对应的短信通道已被删除,请到管理页重新配置回调地址。", status_code=404)
    if (account.provider or "") != str(provider or "").strip():
        raise SalesAgentError(
            f"回调地址里的平台是 {provider},但该通道配置的是 {account.provider};"
            "多半是换了平台之后没更新控制台里的回调地址。",
            status_code=400,
        )
    return account


def _delivery_for_event(db: Session, account: SalesAgentSmsAccountORM, event: SmsCallbackEvent) -> SalesAgentSmsDeliveryORM | None:
    """First match exactly by platform message ID; inbound SMS has no corresponding outbound ID, so fall back to the latest one by number.

    Only searches within this channel account: the same number may have been sent by two channels, and cross-account matching would attribute the receipt to someone else.
    """
    query = db.query(SalesAgentSmsDeliveryORM).filter(SalesAgentSmsDeliveryORM.sms_account_id == account.id)
    msg_id = (event.provider_message_id or "").strip()
    if msg_id:
        row = query.filter(SalesAgentSmsDeliveryORM.provider_message_id == msg_id).first()
        if row is not None:
            return row
    phone, _country, _mobile = normalize_msisdn(event.phone)
    if not phone:
        return None
    return (
        query.filter(SalesAgentSmsDeliveryORM.recipient_phone == phone)
        .order_by(SalesAgentSmsDeliveryORM.id.desc())
        .first()
    )


def _mark_opted_out(db: Session, delivery: SalesAgentSmsDeliveryORM, event: SmsCallbackEvent) -> dict[str, Any]:
    """Unsubscribe write-back: set the contact to unsubscribed, the lead to Unsubscribed, and record a compliance event.

    All three must be written. Writing only the contact leaves that company looking "reachable" in the lead list,
    and the next recommendation round includes it again; a user who replied TD getting a second message is how complaints and signature suspensions happen.
    """
    now = event.occurred_at or datetime.utcnow()
    touched: dict[str, Any] = {"contact_id": 0, "lead_id": 0}
    delivery.opted_out_at = delivery.opted_out_at or now

    contact = db.get(SalesAgentCompanyContactORM, delivery.contact_id) if delivery.contact_id else None
    if contact is None and delivery.company_id:
        # When inbound SMS carries only a number and no outbound contact_id, look up by number: unsubscribing applies per person,
        # and stored number formats are inconsistent (some +86, some bare), so both sides are normalized to E.164 before comparing.
        phone, _c, _m = normalize_msisdn(event.phone or delivery.recipient_phone)
        if phone:
            candidates = (
                db.query(SalesAgentCompanyContactORM)
                .filter(SalesAgentCompanyContactORM.company_id == delivery.company_id)
                .all()
            )
            contact = next((c for c in candidates if normalize_msisdn(c.phone or "")[0] == phone), None)
    if contact is not None and contact.consent_status not in BLOCKED_CONTACT_CONSENT:
        contact.consent_status = "unsubscribed"
        touched["contact_id"] = contact.id

    lead = db.get(SalesAgentLeadRecommendationORM, delivery.lead_id) if delivery.lead_id else None
    if lead is not None and lead.status not in {"Unsubscribed", "Blacklisted"}:
        lead.status = "Unsubscribed"
        touched["lead_id"] = lead.id

    record_compliance_event(
        db,
        delivery.user_id,
        "sms_opt_out",
        "high",
        "blocked",
        f"收到短信退订:{(event.reply_text or 'TD')[:100]}",
        {"delivery_id": delivery.id, "phone": delivery.recipient_phone, **touched},
    )
    return touched


def _apply_event(db: Session, account: SalesAgentSmsAccountORM, event: SmsCallbackEvent) -> dict[str, Any]:
    delivery = _delivery_for_event(db, account, event)
    if delivery is None:
        # No match is not an error: SMS not from this module (e.g. verification codes sent over the same channel) ends up here.
        return {"event_type": event.event_type, "matched": False}
    now = event.occurred_at or datetime.utcnow()
    result: dict[str, Any] = {"event_type": event.event_type, "matched": True, "delivery_id": delivery.id}

    if event.event_type == "delivered":
        if delivery.status not in ("failed", "rejected"):
            delivery.status = "delivered"
            delivery.delivered_at = delivery.delivered_at or now
        _record_sms_event(db, delivery, delivery.user_id, "delivered", {"provider_message_id": event.provider_message_id})
    elif event.event_type == "failed":
        # failed arriving after delivered means receipts are out of order; the first terminal state wins, no flip-flopping.
        if delivery.status not in _TERMINAL_STATUSES:
            delivery.status = "failed"
            delivery.failed_at = delivery.failed_at or now
            delivery.failure_code = event.failure_code or delivery.failure_code
            delivery.failure_reason = event.failure_reason or delivery.failure_reason or "运营商回执为投递失败"
        _record_sms_event(db, delivery, delivery.user_id, "failed", {
            "failure_code": event.failure_code, "failure_reason": event.failure_reason, "source": "callback",
        })
    elif event.event_type == "replied":
        delivery.reply_count = int(delivery.reply_count or 0) + 1
        _record_sms_event(db, delivery, delivery.user_id, "replied", {"text": (event.reply_text or "")[:200]})
    elif event.event_type == "opted_out":
        delivery.reply_count = int(delivery.reply_count or 0) + 1
        result.update(_mark_opted_out(db, delivery, event))
        _record_sms_event(db, delivery, delivery.user_id, "opted_out", {"text": (event.reply_text or "")[:200]})
    else:
        return {"event_type": event.event_type, "matched": False, "ignored": True}
    return result


def apply_sales_agent_sms_callback(
    db: Session,
    provider: str,
    token: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> dict[str, Any]:
    """Callback entry point. Returns processing details to help debug "the carrier says it called back but nothing changed here"."""
    account = resolve_sms_callback_account(db, provider, token)
    try:
        impl = provider_for_key(account.provider)
    except SmsProviderError as e:
        # The provider layer does not know SalesAgentError; translate once at the boundary so a 500 does not bubble up to the carrier.
        raise SalesAgentError(str(e), status_code=400) from e
    if not impl.supports_callback:
        raise SalesAgentError(
            f"{impl.label}不通过 HTTP 回调推送回执(阿里云走 MNS 队列),这个地址不会有数据。"
            "请按该平台的文档接收回执,或改用支持回调的通道。",
            status_code=400,
        )
    events = impl.parse_callback(_sms_credentials(account), dict(headers or {}), body)
    applied = [_apply_event(db, account, event) for event in events]
    db.commit()
    return {
        "provider": account.provider,
        "sms_account_id": account.id,
        "received": len(events),
        "matched": sum(1 for r in applied if r.get("matched")),
        "results": applied,
    }


def track_sms_short_link(db: Session, short_link_token: str) -> dict[str, Any]:
    """Landing info for the short-link redirect: the report's public URL + click counted.

    No matching record is neither an error nor a 500: return an empty url and let the endpoint redirect to the home page. Short links go to users' phones,
    and opening one after the delivery record is deleted should be "nothing happened", not an error page.
    """
    if not _sms_tables_ready(db):
        return {"found": False, "url": ""}
    delivery = track_sales_agent_sms_click(db, short_link_token)
    if delivery is None:
        return {"found": False, "url": ""}
    share = db.get(SalesAgentReportShareORM, delivery.report_share_id) if delivery.report_share_id else None
    db.commit()
    return {
        "found": True,
        "delivery_id": delivery.id,
        "url": _sales_agent_report_public_url(share.public_token) if share is not None else "",
    }
