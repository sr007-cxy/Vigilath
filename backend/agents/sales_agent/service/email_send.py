"""Sales Agent service layer: email sending: delivery records, SMTP/Resend sending, open and report-visit tracking."""
from __future__ import annotations

import html
import re
import smtplib
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
import requests
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, object_session
from agents.sales_agent.constants import BLOCKED_CONTACT_CONSENT, MAX_RECIPIENT_FALLBACK
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailAccountORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentReportShareORM,
)
from geo.models.user import UserORM

from agents.sales_agent.service.common import (
    FAILURE_SIDE_RECIPIENT,
    FAILURE_SIDE_SENDER,
    SalesAgentError,
    _dumps,
    _is_email,
    _iso,
    _loads,
    _safe_email,
    session_has_table,
)
from agents.sales_agent.service.entities import _event_metadata, _record_email_event, serialize_company
from agents.sales_agent.service.suppression import (
    SUPPRESSION_REASON_BOUNCED_HARD,
    add_email_suppression,
    is_email_suppressed,
    suppression_entry,
    suppression_skip_reason,
)
from agents.sales_agent.service.reports import _active_report_share_for_lead, _sales_agent_public_base_url, _sales_agent_report_public_url
from agents.sales_agent.service.email_config import (
    SALES_AGENT_EMAIL_STATUSES,
    _decrypt_email_secret,
    _effective_email_config,
    _get_email_account_for_scope,
    serialize_sales_agent_email_account,
)


SALES_AGENT_EMAIL_SEND_CONCURRENCY = 10
_EMAIL_SEND_SEMAPHORE = threading.BoundedSemaphore(SALES_AGENT_EMAIL_SEND_CONCURRENCY)


def _sales_agent_email_tracking_pixel(tracking_pixel_url: str = "") -> str:
    if not tracking_pixel_url:
        return ""
    safe_pixel = html.escape(tracking_pixel_url, quote=True)
    # Some email clients skip remote images with display:none, so opening the email does not trigger read tracking.
    # Keep it 1x1 + transparent but without hiding display, to raise the odds that clients like QQ/163 actually load the pixel.
    return f'<img src="{safe_pixel}" width="1" height="1" alt="" style="width:1px;height:1px;opacity:0;border:0;outline:none;text-decoration:none;" />'


def _sales_agent_email_html(content: str, tracking_pixel_url: str = "") -> str:
    safe = html.escape(str(content or "").strip())

    def linkify(match: re.Match[str]) -> str:
        url = match.group(0)
        href = url.rstrip("。），,.;；")
        tail = url[len(href):]
        safe_href = html.escape(href, quote=True)
        label = html.escape(href)
        return f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer" style="color:#2563eb;text-decoration:underline;word-break:break-all;">{label}</a>{tail}'

    safe = re.sub(r"https?://[^\s<]+", linkify, safe).replace("\n", "<br />")
    pixel = _sales_agent_email_tracking_pixel(tracking_pixel_url)
    return (
        '<div style="font-family:Arial,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
        'font-size:14px;line-height:1.8;color:#111827;white-space:normal;">'
        f"{safe}{pixel}"
        "</div>"
    )


def _sales_agent_email_html_from_safe_html(content: str, tracking_pixel_url: str = "") -> str:
    body = str(content or "").strip()
    body = re.sub(r"<\s*(script|style|iframe|object|embed|form|input|button)[^>]*>.*?<\s*/\s*\1\s*>", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<\s*/?\s*(script|style|iframe|object|embed|form|input|button)[^>]*>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"(href\s*=\s*['\"])\s*javascript:[^'\"]*(['\"])", r"\1#\2", body, flags=re.IGNORECASE)
    pixel = _sales_agent_email_tracking_pixel(tracking_pixel_url)
    return (
        '<div style="font-family:Arial,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
        'font-size:14px;line-height:1.8;color:#111827;white-space:normal;">'
        f"{body}{pixel}"
        "</div>"
    )


def _sales_agent_email_open_url(tracking_token: str, public_base_url: str = "") -> str:
    path = f"/api/sales-agent/email/open/{tracking_token}.png"
    base = _sales_agent_public_base_url(public_base_url)
    return f"{base}{path}" if base else path


def _append_delivery_query(url: str, delivery_id: int) -> str:
    text = str(url or "").strip()
    if not text or not delivery_id:
        return text
    if re.search(r"([?&])sad=", text):
        return text
    sep = "&" if "?" in text else "?"
    return f"{text}{sep}sad={int(delivery_id)}"


def _message_type_for_draft(draft: SalesAgentOutreachLogORM) -> str:
    meta = _loads(getattr(draft, "metadata_json", "{}"), {})
    kind = str(meta.get("kind") or meta.get("message_type") or "").strip()
    if kind == "report_email":
        return "report_email"
    if kind == "no_report_default_email":
        return "general_outreach"
    if kind in {"general_outreach", "follow_up", "test"}:
        return kind
    return "general_outreach"


def _report_share_for_draft(db: Session, user_id: int, draft: SalesAgentOutreachLogORM, lead: SalesAgentLeadRecommendationORM) -> SalesAgentReportShareORM | None:
    meta = _loads(getattr(draft, "metadata_json", "{}"), {})
    raw_id = meta.get("report_share_id") or meta.get("share_id")
    if raw_id:
        row = db.get(SalesAgentReportShareORM, int(raw_id))
        if row is not None and row.user_id == user_id and row.lead_id == lead.id:
            return row
    return _active_report_share_for_lead(db, user_id, lead.id)


def _delivery_content_preview(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "").strip())
    return text[:600]


def _resolve_delivery_recipient(db: Session, lead: SalesAgentLeadRecommendationORM, contact_id: int | None = None) -> tuple[SalesAgentCompanyContactORM | None, str, str]:
    if contact_id:
        contact = db.query(SalesAgentCompanyContactORM).filter(SalesAgentCompanyContactORM.id == int(contact_id), SalesAgentCompanyContactORM.company_id == lead.company_id).first()
        if contact is not None and _is_email(contact.email) and contact.consent_status not in BLOCKED_CONTACT_CONSENT:
            return contact, contact.email.strip(), contact.contact_name or contact.position or ""
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
        # Explicit user requirement: a company contact can be emailed as long as it has a valid email; no longer blocked for empty/non-whitelisted source.
        if _is_email(row.email):
            return row, row.email.strip(), row.contact_name or row.position or ""
    return None, "", ""


def _delivery_recipient_candidates(
    db: Session,
    lead: SalesAgentLeadRecommendationORM,
    limit: int = MAX_RECIPIENT_FALLBACK,
    preferred_contact_id: int | None = None,
    user_id: int | None = None,
) -> list[tuple[SalesAgentCompanyContactORM, str, str]]:
    """This company's recipient candidates sorted by priority, at most `limit`.

    Scheduled sending uses it for "if the first choice is rejected, fall through to the next mailbox" (4.1.1). Sorting and filtering match
    `_resolve_delivery_recipient` exactly (`is_reachable` first, `BLOCKED_CONTACT_CONSENT`
    skipped, `_is_email` validated), **but that function is not changed at all**: the manual send path uses it,
    and its contract is "give me one recipient", not "give me a list".

    Each email address appears only once: companies commonly attach the same info@ to two contacts,
    and without dedup "fall through" would hit the same wall with the same address, wasting a chance.

    When `user_id` is given, also check the suppression list (§3.3). The fall-through backup address must pass the gate too,
    or "first choice bounced -> fall through to the second" would fish out exactly an address that already unsubscribed.
    """
    if limit <= 0:
        return []
    rows: list[SalesAgentCompanyContactORM] = []
    if preferred_contact_id:
        first = (
            db.query(SalesAgentCompanyContactORM)
            .filter(
                SalesAgentCompanyContactORM.id == int(preferred_contact_id),
                SalesAgentCompanyContactORM.company_id == lead.company_id,
            )
            .first()
        )
        if first is not None:
            rows.append(first)
    rows.extend(
        db.query(SalesAgentCompanyContactORM)
        .filter(SalesAgentCompanyContactORM.company_id == lead.company_id, SalesAgentCompanyContactORM.email != "")
        .order_by(SalesAgentCompanyContactORM.is_reachable.desc(), SalesAgentCompanyContactORM.id.desc())
        .limit(20)
        .all()
    )
    out: list[tuple[SalesAgentCompanyContactORM, str, str]] = []
    seen_emails: set[str] = set()
    seen_ids: set[int] = set()
    for row in rows:
        if row.id in seen_ids:
            continue
        seen_ids.add(row.id)
        if row.consent_status in BLOCKED_CONTACT_CONSENT:
            continue
        if not _is_email(row.email):
            continue
        email = row.email.strip()
        key = email.lower()
        if key in seen_emails:
            continue
        seen_emails.add(key)
        if user_id is not None and is_email_suppressed(db, user_id, email):
            continue
        out.append((row, email, row.contact_name or row.position or ""))
        if len(out) >= limit:
            break
    return out


def _mark_delivery_failed(
    db: Session,
    delivery: SalesAgentEmailDeliveryORM,
    user_id: int,
    reason: str,
    failure_side: str,
    permanent: bool = False,
) -> SalesAgentEmailDeliveryORM:
    """Write the failed state, keeping "which side is at fault" and "whether permanent" in metadata.

    Scheduled sending's fall-through decision reads exactly these two fields (4.1.1). We leave a trace instead of letting the caller catch the exception because
    on the executor path sending and deciding are separated by a `confirm_outreach_send`, and the exception has long been swallowed into a delivery record.
    No column of the existing delivery table changes; the classification lives in `metadata_json`.

    **Recipient side + permanent = hard bounce; the address goes straight into the suppression list** (one of the two phase-1 data sources in §3.3).
    It lives here rather than in the executor because manual sending also hits hard bounces, and the suppression list is a global list
    keyed by email address; both send paths must feed it, or bad addresses from manual sends would be hit again in scheduled batches.
    """
    side = failure_side if failure_side in (FAILURE_SIDE_SENDER, FAILURE_SIDE_RECIPIENT) else FAILURE_SIDE_SENDER
    permanent = bool(permanent) and side == FAILURE_SIDE_RECIPIENT
    delivery.status = "failed"
    delivery.failure_reason = reason
    delivery.failed_at = datetime.utcnow()
    meta = _loads(delivery.metadata_json, {})
    meta["failure_side"] = side
    meta["failure_permanent"] = permanent
    delivery.metadata_json = _dumps(meta)
    _record_email_event(
        db, delivery, user_id, "failed",
        {"reason": reason, "provider": delivery.provider_type, "failure_side": side, "permanent": permanent},
    )
    if permanent and delivery.recipient_email:
        # A broken suppression list must not roll back this email's failure record too: the failed state itself must be persisted to DB.
        try:
            add_email_suppression(
                db, user_id, delivery.recipient_email,
                reason=SUPPRESSION_REASON_BOUNCED_HARD,
                source=f"hard_bounce:delivery:{delivery.id or 0}",
            )
        except Exception:  # noqa: BLE001
            pass
    return delivery


def delivery_failure_side(row: SalesAgentEmailDeliveryORM | None) -> str:
    """Read back the classification written above. If unreadable, treat as sender side: when unsure, do not fall through."""
    if row is None:
        return FAILURE_SIDE_SENDER
    side = str(_loads(row.metadata_json, {}).get("failure_side") or "")
    return side if side in (FAILURE_SIDE_SENDER, FAILURE_SIDE_RECIPIENT) else FAILURE_SIDE_SENDER


def delivery_failure_is_permanent(row: SalesAgentEmailDeliveryORM | None) -> bool:
    """Whether this failure is a permanent rejection. If unreadable, treat as transient: when unsure, do not blacklist."""
    if row is None:
        return False
    return bool(_loads(row.metadata_json, {}).get("failure_permanent")) and delivery_failure_side(row) == FAILURE_SIDE_RECIPIENT


def serialize_sales_agent_email_delivery(row: SalesAgentEmailDeliveryORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    metadata = _loads(row.metadata_json, {})
    content = str(metadata.get("content") or "").strip()
    db = object_session(row)
    if not content and db is not None and row.outreach_log_id:
        draft = db.get(SalesAgentOutreachLogORM, row.outreach_log_id)
        if draft is not None:
            content = str(draft.content or "").strip()
    metadata_payload = {k: v for k, v in metadata.items() if k != "content"}
    payload = {
        "id": row.id,
        "user_id": row.user_id,
        "lead_id": row.lead_id,
        "company_id": row.company_id,
        "contact_id": row.contact_id,
        "outreach_log_id": row.outreach_log_id,
        "email_account_id": row.email_account_id,
        "report_share_id": row.report_share_id,
        "message_type": row.message_type,
        "provider_type": row.provider_type,
        "provider_message_id": row.provider_message_id,
        "from_email": row.from_email,
        "from_name": row.from_name,
        "reply_to_email": row.reply_to_email,
        "recipient_email": row.recipient_email,
        "recipient_name": row.recipient_name,
        "subject": row.subject,
        "content_preview": row.content_preview,
        "content": content or row.content_preview,
        "status": row.status,
        "failure_reason": row.failure_reason,
        "send_attempted_at": _iso(row.send_attempted_at),
        "sent_at": _iso(row.sent_at),
        "failed_at": _iso(row.failed_at),
        "first_opened_at": _iso(row.first_opened_at),
        "last_opened_at": _iso(row.last_opened_at),
        "open_count": row.open_count,
        "first_report_visit_at": _iso(row.first_report_visit_at),
        "last_report_visit_at": _iso(row.last_report_visit_at),
        "report_visit_count": row.report_visit_count,
        "read_at": _iso(row.read_at),
        "metadata": metadata_payload,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    db = object_session(row)
    if db is not None:
        # How many times this draft was actually sent: the panel echoes it and the backend enforces the resend cap with it
        # (outreach.py::_guard_resend). Without the echo the user only sees "send again" and learns of the rejection on click.
        payload["send_count"] = (
            db.query(SalesAgentEmailDeliveryORM)
            .filter(SalesAgentEmailDeliveryORM.outreach_log_id == row.outreach_log_id,
                    SalesAgentEmailDeliveryORM.status == "sent")
            .count()
        ) if row.outreach_log_id else (1 if row.status == "sent" else 0)
        if row.company_id:
            company = db.get(SalesAgentCompanyORM, row.company_id)
            if company is not None:
                payload["company"] = serialize_company(company)
                payload["company_name"] = company.name
        has_users_table = session_has_table(db, UserORM.__tablename__)
        user = db.get(UserORM, row.user_id) if row.user_id and has_users_table else None
        if user is not None:
            payload["user_email"] = user.email
    return payload


def _latest_email_delivery_for_draft(db: Session, draft_id: int) -> SalesAgentEmailDeliveryORM | None:
    return (
        db.query(SalesAgentEmailDeliveryORM)
        .filter(SalesAgentEmailDeliveryORM.outreach_log_id == int(draft_id))
        .order_by(SalesAgentEmailDeliveryORM.id.desc())
        .first()
    )


def _provider_label(row: SalesAgentEmailAccountORM) -> str:
    return row.api_provider if row.provider_type == "api" else "smtp"


def _format_from(row: SalesAgentEmailAccountORM) -> str:
    return formataddr((row.from_name, row.from_email)) if row.from_name else row.from_email


def _smtp_refusal_is_permanent(exc: smtplib.SMTPRecipientsRefused) -> bool:
    """Whether this rejection is permanent (5xx) or transient (4xx).

    Only permanent rejections write the address to the suppression list: "mailbox full" or "remote server busy" are 4xx
    and resolve in a couple of days; blacklisting them permanently cuts off a valid customer on the user's behalf.

    `recipients` is `{address: (status code, server text)}`. **Only the status code is read, never the text**:
    the text is vendor content, and touching it forces the non-public path. When unsure, treat as transient (no blacklisting).
    """
    codes: list[int] = []
    for value in (getattr(exc, "recipients", None) or {}).values():
        try:
            codes.append(int(value[0]))
        except (TypeError, ValueError, IndexError):
            return False
    return bool(codes) and all(500 <= code < 600 for code in codes)


def _smtp_failure(exc: Exception) -> SalesAgentError:
    """Split smtplib exceptions into recipient side / sender side, with an actionable message.

    Only the recipient side may fall through to the next mailbox of the same company (scheduled sending 4.1.1). Anything indistinguishable
    falls to the sender side: changing recipients does nothing for a broken sender mailbox, and misjudging would let one config error
    burn through all of this company's mailboxes.

    Every message is a literal with no content from `exc`, so it can be marked public and shown to users directly;
    including the server text would force the non-public path, where the outbound word list would scrub it anyway.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        # The remote server rejected at RCPT TO: address does not exist / refused by recipient; trying another mailbox makes sense.
        return SalesAgentError(
            "收件地址被对方邮件服务器拒收,请确认该邮箱是否有效。",
            status_code=502, public=True, failure_side=FAILURE_SIDE_RECIPIENT,
            permanent=_smtp_refusal_is_permanent(exc),
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SalesAgentError(
            "SMTP 登录失败,请检查发件箱的账号和授权码/密码是否正确。",
            status_code=503, public=True,
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        # MAIL FROM rejected: sender address does not match the login account, or the domain is not authorized. Changing recipients is useless; sender side.
        return SalesAgentError(
            "发件地址被 SMTP 服务器拒绝,请确认发件邮箱与登录账号一致、且发信域名已授权。",
            status_code=503, public=True,
        )
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, smtplib.SMTPHeloError, TimeoutError)):
        return SalesAgentError(
            "连接 SMTP 服务器失败,请检查服务器地址、端口和加密方式(SSL / STARTTLS)。",
            status_code=503, public=True,
        )
    return SalesAgentError("SMTP 邮件发送失败,请检查服务器、端口、账号、授权码和发件人域名。", status_code=503)


def _send_smtp_message(row: SalesAgentEmailAccountORM, recipient_email: str, subject: str, text: str, html_body: str) -> str:
    username = row.smtp_username or row.from_email
    password = _decrypt_email_secret(row.smtp_secret_encrypted)
    if not username or not password:
        raise SalesAgentError("SMTP 发件箱缺少用户名或授权码/密码。", status_code=400)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _format_from(row)
    msg["To"] = recipient_email
    if row.reply_to_email:
        msg["Reply-To"] = row.reply_to_email
    msg.set_content(text, subtype="plain", charset="utf-8")
    msg.add_alternative(html_body, subtype="html")
    try:
        if row.smtp_security == "ssl":
            with smtplib.SMTP_SSL(row.smtp_host, int(row.smtp_port or 465), timeout=10) as server:
                server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(row.smtp_host, int(row.smtp_port or 587), timeout=10) as server:
                if row.smtp_security == "starttls":
                    server.starttls()
                server.login(username, password)
                server.send_message(msg)
    except SalesAgentError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _smtp_failure(e) from e
    return ""


_RESEND_RECIPIENT_MARKERS = ("recipient", "`to`", "to field", "to address")


def _resend_body_blames_recipient(resp: "requests.Response") -> bool:
    """Whether a Resend 4xx means the recipient address is wrong, or our Key / domain is wrong.

    Only asked for 400/422 (401/403/5xx are always sender side). The criterion is the response body's
    `message`, but **it is only used for a boolean check; not a single character goes into the raised message**: the outbound word list
    exists precisely to keep vendor text out of user-visible copy. If unreadable, treat as sender side.
    """
    if resp.status_code not in (400, 422):
        return False
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(body, dict):
        return False
    message = str(body.get("message") or body.get("error") or "").lower()
    return any(marker in message for marker in _RESEND_RECIPIENT_MARKERS)


def _send_resend_message(row: SalesAgentEmailAccountORM, recipient_email: str, subject: str, text: str, html_body: str) -> str:
    api_key = _decrypt_email_secret(row.api_key_encrypted)
    if not api_key:
        # public: this must reach the user verbatim. Unmarked, the outbound word list would treat it as a key leak and turn it into
        # "please retry later", while what is missing is a Key they could configure in two minutes, so they would keep retrying.
        raise SalesAgentError("Resend 发件箱缺少 API Key。", status_code=400, public=True)
    url = row.api_base_url or "https://api.resend.com/emails"
    payload: dict[str, Any] = {"from": _format_from(row), "to": [recipient_email], "subject": subject, "html": html_body, "text": text}
    if row.reply_to_email:
        payload["reply_to"] = row.reply_to_email
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as e:
        raise SalesAgentError("Resend 邮件发送请求失败,请稍后重试。", status_code=503) from e
    if resp.status_code >= 400:
        # Same as above: the whole sentence is hard-coded; not a single character comes from the Resend response body.
        if _resend_body_blames_recipient(resp):
            raise SalesAgentError(
                "Resend 拒绝了这个收件地址,请确认该邮箱是否有效。",
                status_code=502, public=True, failure_side=FAILURE_SIDE_RECIPIENT,
                # Resend only names the recipient address on 400/422, meaning "this address itself is invalid/rejected",
                # not "try again later"; same level as SMTP 5xx, so it may go into the suppression list.
                permanent=True,
            )
        if resp.status_code in (401, 403):
            raise SalesAgentError("Resend API Key 无效或无权发送,请检查 Key 与发件域名的授权。", status_code=503, public=True)
        raise SalesAgentError("Resend 邮件发送失败,请检查 API Key、发件域名和收件地址。", status_code=503, public=True)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}
    return str(body.get("id") or "") if isinstance(body, dict) else ""


def _send_email_account_message(row: SalesAgentEmailAccountORM, recipient_email: str, subject: str, text: str, html_body: str) -> str:
    if row.provider_type == "smtp":
        return _send_smtp_message(row, recipient_email, subject, text, html_body)
    if row.provider_type == "api" and row.api_provider == "resend":
        return _send_resend_message(row, recipient_email, subject, text, html_body)
    raise SalesAgentError("当前邮件服务商暂未接入真实发送,请先选择 SMTP 或 Resend。", status_code=400)


def send_sales_agent_email_delivery(
    db: Session,
    user_id: int,
    draft: SalesAgentOutreachLogORM,
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    public_base_url: str = "",
) -> SalesAgentEmailDeliveryORM:
    if draft.channel != "email":
        raise SalesAgentError("当前草稿不是邮件渠道,不能直接发送。", status_code=400)
    subject = str(draft.subject or "").strip() or "Sales Agent 邮件"
    body = str(draft.content or "").strip()
    if not body:
        raise SalesAgentError("邮件正文为空,不能发送。", status_code=400)
    meta = _loads(getattr(draft, "metadata_json", "{}"), {})
    try:
        metadata_contact_id = int(meta.get("contact_id") or 0) or None
    except Exception:  # noqa: BLE001
        metadata_contact_id = None
    contact, recipient_email, recipient_name = _resolve_delivery_recipient(db, lead, metadata_contact_id)
    cfg = _effective_email_config(db, user_id)
    account: SalesAgentEmailAccountORM | None = cfg.get("account")
    share = _report_share_for_draft(db, user_id, draft, lead)
    delivery = SalesAgentEmailDeliveryORM(
        user_id=user_id,
        lead_id=lead.id,
        company_id=company.id,
        contact_id=contact.id if contact else None,
        outreach_log_id=draft.id,
        email_account_id=account.id if account else None,
        report_share_id=share.id if share else None,
        message_type=_message_type_for_draft(draft),
        provider_type=_provider_label(account) if account else "",
        from_email=account.from_email if account else "",
        from_name=account.from_name if account else "",
        reply_to_email=account.reply_to_email if account else "",
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        subject=subject[:255],
        content_preview=_delivery_content_preview(body),
        status="sending",
        metadata_json=_dumps({
            "account_source": cfg.get("account_source"),
            "sender_selection_policy": cfg.get("sender_selection_policy"),
            "tracking_enabled": bool(cfg.get("tracking_enabled")),
            "content_format": meta.get("content_format"),
            "html_content": meta.get("html_content"),
            "draft_kind": meta.get("kind"),
            "template_mode": meta.get("template_mode"),
            "content": body,
        }),
    )
    db.add(delivery)
    db.flush()
    if not cfg.get("sending_enabled"):
        delivery.status = "disabled"
        delivery.failure_reason = "；".join(cfg.get("disabled_reasons") or ["邮件发送已关闭"])
        _record_email_event(db, delivery, user_id, "send_disabled", {"reasons": cfg.get("disabled_reasons") or []})
        return delivery
    if not recipient_email:
        delivery.status = "no_recipient"
        delivery.failure_reason = "目标企业缺少可用公开收件邮箱"
        _record_email_event(db, delivery, user_id, "no_recipient", {"company_id": company.id})
        return delivery
    # The last gate of the suppression list, shared by manual and scheduled sending (§3.3 "check once right before each send").
    # On the scheduled path the executor marks this company skipped earlier and does not even send a draft, so reaching here
    # means a manual send, or the recipient unsubscribed after the plan was created; both must be blocked.
    #
    # Reuse `disabled` instead of adding a `suppressed` status: status values are spread across six frontend places
    # (filters, stat cards, label mapping, two TS interfaces); adding one is a cross-stack change, and
    # the meaning "we deliberately chose not to send" is already covered by `disabled`. The sentence actually shown to users is in
    # `failure_reason` and the event, with nothing lost.
    suppressed = suppression_entry(db, user_id, recipient_email)
    if suppressed is not None:
        delivery.status = "disabled"
        delivery.failure_reason = suppression_skip_reason(suppressed.reason)
        _record_email_event(
            db, delivery, user_id, "suppressed",
            {"company_id": company.id, "reason": suppressed.reason, "source": suppressed.source},
        )
        return delivery
    if account is None:
        delivery.status = "disabled"
        delivery.failure_reason = "未配置可用发件箱"
        _record_email_event(db, delivery, user_id, "send_disabled", {"reasons": [delivery.failure_reason]})
        return delivery

    body_for_send = body
    html_content = str(meta.get("html_content") or "").strip() if str(meta.get("content_format") or "").lower() == "html" else ""
    if share is not None:
        public_url = _sales_agent_report_public_url(share.public_token, public_base_url)
        tracked_url = _append_delivery_query(public_url, delivery.id)
        if public_url and tracked_url != public_url:
            body_for_send = body_for_send.replace(public_url, tracked_url)
            if html_content:
                safe_public_url = html.escape(public_url, quote=True)
                safe_tracked_url = html.escape(tracked_url, quote=True)
                html_content = html_content.replace(public_url, tracked_url).replace(safe_public_url, safe_tracked_url)
    pixel_url = _sales_agent_email_open_url(delivery.tracking_token, public_base_url) if cfg.get("tracking_enabled") else ""
    if html_content:
        html_body = _sales_agent_email_html_from_safe_html(html_content, pixel_url)
    else:
        html_body = _sales_agent_email_html(body_for_send, pixel_url)
    now = datetime.utcnow()
    delivery.send_attempted_at = now
    _record_email_event(db, delivery, user_id, "send_attempt", {"provider": delivery.provider_type})
    acquired = _EMAIL_SEND_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        # A busy queue is a capacity issue on our side, not a recipient address issue: resending to another mailbox is pointless.
        return _mark_delivery_failed(
            db, delivery, user_id,
            f"邮件发送队列忙碌,当前最多同时发送 {SALES_AGENT_EMAIL_SEND_CONCURRENCY} 封,请稍后重试。",
            FAILURE_SIDE_SENDER,
        )
    try:
        try:
            provider_message_id = _send_email_account_message(account, recipient_email, subject, body_for_send, html_body)
        except SalesAgentError as e:
            return _mark_delivery_failed(
                db, delivery, user_id, str(e),
                getattr(e, "failure_side", FAILURE_SIDE_SENDER),
                permanent=bool(getattr(e, "permanent", False)),
            )
        delivery.status = "sent"
        delivery.sent_at = datetime.utcnow()
        delivery.provider_message_id = provider_message_id[:255]
        _record_email_event(db, delivery, user_id, "sent", {"provider": delivery.provider_type, "provider_message_id": delivery.provider_message_id})
        return delivery
    finally:
        _EMAIL_SEND_SEMAPHORE.release()


def test_sales_agent_email_account(
    db: Session,
    user_id: int,
    account_id: int,
    to_email: str = "",
    subject: str = "",
    content: str = "",
    scope: str = "user",
    public_base_url: str = "",
) -> dict[str, Any]:
    account = _get_email_account_for_scope(db, account_id, scope, user_id)
    recipient = _safe_email(to_email)
    if not recipient:
        user = db.get(UserORM, user_id)
        recipient = user.email if user is not None else ""
    if not _is_email(recipient):
        raise SalesAgentError("请填写有效的测试收件邮箱。")
    subject_text = str(subject or "Sales Agent 邮箱测试").strip()[:255]
    body = str(content or "这是一封 Sales Agent 邮箱配置测试邮件。收到这封邮件说明当前发件箱可以正常发送。").strip()
    delivery = SalesAgentEmailDeliveryORM(
        user_id=user_id,
        email_account_id=account.id,
        message_type="test",
        provider_type=_provider_label(account),
        from_email=account.from_email,
        from_name=account.from_name,
        reply_to_email=account.reply_to_email,
        recipient_email=recipient,
        subject=subject_text,
        content_preview=_delivery_content_preview(body),
        status="sending",
        metadata_json=_dumps({"scope": scope, "content": body}),
    )
    db.add(delivery)
    db.flush()
    pixel_url = _sales_agent_email_open_url(delivery.tracking_token, public_base_url)
    delivery.send_attempted_at = datetime.utcnow()
    _record_email_event(db, delivery, user_id, "send_attempt", {"message_type": "test", "provider": delivery.provider_type})
    try:
        delivery.provider_message_id = _send_email_account_message(account, recipient, subject_text, body, _sales_agent_email_html(body, pixel_url))[:255]
    except SalesAgentError as e:
        delivery.status = "failed"
        delivery.failure_reason = str(e)
        delivery.failed_at = datetime.utcnow()
        account.last_tested_at = datetime.utcnow()
        account.last_test_status = "failed"
        account.last_test_error = str(e)
        _record_email_event(db, delivery, user_id, "failed", {"message_type": "test", "reason": str(e)})
        db.commit()
        return {"ok": False, "delivery": serialize_sales_agent_email_delivery(delivery), "account": serialize_sales_agent_email_account(account), "error": str(e)}
    delivery.status = "sent"
    delivery.sent_at = datetime.utcnow()
    account.last_tested_at = datetime.utcnow()
    account.last_test_status = "sent"
    account.last_test_error = ""
    _record_email_event(db, delivery, user_id, "sent", {"message_type": "test", "provider_message_id": delivery.provider_message_id})
    db.commit()
    db.refresh(delivery)
    db.refresh(account)
    return {"ok": True, "delivery": serialize_sales_agent_email_delivery(delivery), "account": serialize_sales_agent_email_account(account)}


def list_sales_agent_email_deliveries(
    db: Session,
    user_id: int,
    admin: bool = False,
    status: str = "",
    message_type: str = "",
    q: str = "",
    read: str = "",
    limit: int = 50,
    offset: int = 0,
    target_user_id: int | None = None,
    company_id: int | None = None,
    lead_id: int | None = None,
) -> dict[str, Any]:
    query = db.query(SalesAgentEmailDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == user_id)
    if company_id:
        query = query.filter(SalesAgentEmailDeliveryORM.company_id == int(company_id))
    if lead_id:
        query = query.filter(SalesAgentEmailDeliveryORM.lead_id == int(lead_id))
    if status:
        query = query.filter(SalesAgentEmailDeliveryORM.status == status)
    if message_type:
        query = query.filter(SalesAgentEmailDeliveryORM.message_type == message_type)
    if read == "read":
        query = query.filter(SalesAgentEmailDeliveryORM.read_at.isnot(None))
    elif read == "unread":
        query = query.filter(SalesAgentEmailDeliveryORM.read_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        query = query.outerjoin(SalesAgentCompanyORM, SalesAgentEmailDeliveryORM.company_id == SalesAgentCompanyORM.id).filter(
            or_(SalesAgentEmailDeliveryORM.recipient_email.ilike(like), SalesAgentEmailDeliveryORM.subject.ilike(like), SalesAgentCompanyORM.name.ilike(like))
        )
    total = query.count()
    rows = query.order_by(SalesAgentEmailDeliveryORM.id.desc()).offset(max(0, int(offset or 0))).limit(max(1, min(int(limit or 50), 100))).all()
    return {"deliveries": [serialize_sales_agent_email_delivery(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _bj(dt) -> str:
    """Times shown to the model are always Beijing time and labeled as such.

    The DB stores utcnow; returning a bare UTC string makes the model guess the timezone: on 2026-08-23 in production the same tool
    used two different times in two answers (converted to 14:47 in the morning, later reported 08:06 directly).
    """
    if not dt:
        return ""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + " (北京时间)"


def agent_email_delivery_status(
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
    """**Email** delivery status for the Agent to read: real delivery status + opens/report visits. Read-only: does not send or change state.

    Unlike `list_sales_agent_email_deliveries`, this is **for the model**: fields trimmed to the few that can be stated directly,
    no body, no company object, to avoid stuffing it all into context.

    The external entry point is `delivery_stats.agent_delivery_status`, which merges SMS in as well.
    A chat question like "which companies did we send to today" should not be answered with email only.
    """
    query = db.query(SalesAgentEmailDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == user_id)
    if lead_id:
        query = query.filter(SalesAgentEmailDeliveryORM.lead_id == int(lead_id))
    if status:
        query = query.filter(SalesAgentEmailDeliveryORM.status == status)
    delivery_time = func.coalesce(SalesAgentEmailDeliveryORM.sent_at, SalesAgentEmailDeliveryORM.created_at)
    if since is not None:
        query = query.filter(delivery_time >= since)
    elif days and int(days) > 0:
        query = query.filter(delivery_time >= datetime.utcnow() - timedelta(days=int(days)))
    if until is not None:
        query = query.filter(delivery_time < until)
    name = (company_name or "").strip()
    if name:
        like = f"%{name}%"
        query = query.outerjoin(SalesAgentCompanyORM, SalesAgentEmailDeliveryORM.company_id == SalesAgentCompanyORM.id).filter(
            or_(SalesAgentCompanyORM.name.ilike(like), SalesAgentEmailDeliveryORM.recipient_email.ilike(like))
        )

    total = query.count()

    def distinct_company_count(q) -> int:
        return q.with_entities(SalesAgentEmailDeliveryORM.company_id).filter(SalesAgentEmailDeliveryORM.company_id.isnot(None)).distinct().count()

    sent_query = query.filter(SalesAgentEmailDeliveryORM.status == "sent")
    failed_query = query.filter(SalesAgentEmailDeliveryORM.status == "failed")
    attempted_query = query.filter(SalesAgentEmailDeliveryORM.status.in_(["sent", "failed"]))
    opened_query = query.filter(or_(SalesAgentEmailDeliveryORM.first_opened_at.isnot(None), SalesAgentEmailDeliveryORM.read_at.isnot(None), SalesAgentEmailDeliveryORM.open_count > 0))
    report_visited_query = query.filter(SalesAgentEmailDeliveryORM.report_visit_count > 0)
    open_count_total = int(query.with_entities(func.coalesce(func.sum(SalesAgentEmailDeliveryORM.open_count), 0)).scalar() or 0)
    report_visit_count_total = int(query.with_entities(func.coalesce(func.sum(SalesAgentEmailDeliveryORM.report_visit_count), 0)).scalar() or 0)
    row_query = (
        report_visited_query if activity == "report_visit"
        else opened_query if activity == "open"
        else failed_query if activity == "failed"
        else sent_query if activity == "sent"
        else query
    )
    rows = row_query.order_by(SalesAgentEmailDeliveryORM.id.desc()).limit(max(1, min(int(limit or 20), 50))).all()

    items = []
    for row in rows:
        company = db.get(SalesAgentCompanyORM, row.company_id) if row.company_id else None
        items.append({
            "delivery_id": row.id,
            "lead_id": row.lead_id,
            "company_name": company.name if company is not None else "",
            "recipient_email": row.recipient_email,
            "subject": row.subject,
            "status": row.status,
            "sent_at": _bj(row.sent_at),
            "failed_at": _bj(row.failed_at),
            "failure_reason": row.failure_reason or "",
            "opened": bool(row.first_opened_at or row.read_at or int(row.open_count or 0) > 0),
            "open_count": row.open_count,
            "first_opened_at": _bj(row.first_opened_at),
            "report_visit_count": row.report_visit_count,
            "first_report_visit_at": _bj(row.first_report_visit_at),
            "last_report_visit_at": _bj(row.last_report_visit_at),
            "read_at": _bj(row.read_at),
        })

    return {
        "total_matched": total,
        "returned": len(items),
        "summary": {
            "sent": sent_query.count(),
            "failed": failed_query.count(),
            "attempted": attempted_query.count(),
            "opened": opened_query.count(),
            "report_visited": report_visited_query.count(),
            "open_count": open_count_total,
            "report_visit_count": report_visit_count_total,
            "sent_companies": distinct_company_count(sent_query),
            "attempted_companies": distinct_company_count(attempted_query),
            "opened_companies": distinct_company_count(opened_query),
            "report_visited_companies": distinct_company_count(report_visited_query),
        },
        "deliveries": items,
        "filters": {"company_name": name, "lead_id": lead_id or 0, "status": status, "days": int(days or 0), "since": _bj(since) if since else "", "until": _bj(until) if until else "", "admin": bool(admin), "target_user_id": int(target_user_id or 0)},
        "note": "平台在用户逐封确认后用其配置的发件箱真实发出;这里是真实投递记录,不是草稿。",
    }


def sales_agent_email_stats(db: Session, user_id: int, admin: bool = False, target_user_id: int | None = None) -> dict[str, Any]:
    query = db.query(SalesAgentEmailDeliveryORM)
    if admin and target_user_id:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == int(target_user_id))
    elif not admin:
        query = query.filter(SalesAgentEmailDeliveryORM.user_id == user_id)
    total = query.count()
    counts = {status: query.filter(SalesAgentEmailDeliveryORM.status == status).count() for status in SALES_AGENT_EMAIL_STATUSES}
    read_count = query.filter(SalesAgentEmailDeliveryORM.read_at.isnot(None)).count()
    sums = query.with_entities(
        func.coalesce(func.sum(SalesAgentEmailDeliveryORM.open_count), 0),
        func.coalesce(func.sum(SalesAgentEmailDeliveryORM.report_visit_count), 0),
    ).first()
    return {
        "total": total,
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "disabled": counts.get("disabled", 0),
        "no_recipient": counts.get("no_recipient", 0),
        "sending": counts.get("sending", 0),
        "read": read_count,
        "open_count": int(sums[0] or 0) if sums else 0,
        "report_visit_count": int(sums[1] or 0) if sums else 0,
    }


def track_sales_agent_email_open(db: Session, tracking_token: str, user_agent: str = "", ip_hash: str = "") -> bool:
    token = str(tracking_token or "").strip()
    if not token:
        return False
    delivery = db.query(SalesAgentEmailDeliveryORM).filter(SalesAgentEmailDeliveryORM.tracking_token == token).first()
    if delivery is None:
        return False
    now = datetime.utcnow()
    if delivery.first_opened_at is None:
        delivery.first_opened_at = now
    delivery.last_opened_at = now
    delivery.open_count = int(delivery.open_count or 0) + 1
    if delivery.read_at is None:
        delivery.read_at = now
    _record_email_event(db, delivery, delivery.user_id, "open_pixel", _event_metadata(user_agent, ip_hash))
    db.commit()
    return True
