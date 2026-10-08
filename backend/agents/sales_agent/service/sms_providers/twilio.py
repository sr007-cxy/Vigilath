"""Twilio Programmable Messaging (international free-copy channel).

Basic Auth + form-encoded POST, the simplest of the three.
Callbacks are form-encoded rather than JSON: the endpoint must parse by content-type, not treat everything as JSON.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from agents.sales_agent.service.sms_providers.base import (
    SmsCallbackEvent,
    SmsChannelCredentials,
    SmsProvider,
    SmsProviderError,
    SmsSendResult,
    is_opt_out_reply,
)

API_ROOT = "https://api.twilio.com/2010-04-01"

# Twilio terminal states. `undelivered` is an explicit carrier bounce, handled as failure like `failed`.
_DELIVERED_STATUSES = {"delivered"}
_FAILED_STATUSES = {"failed", "undelivered"}

# Unsubscribe detection is in base.STOP_KEYWORDS: Twilio itself also intercepts the STOP family and stops delivery,
# but it does not tell our DB, so we must write back consent_status ourselves.


class TwilioSmsProvider(SmsProvider):
    key = "twilio"
    label = "Twilio"
    content_mode = "freeform"
    supports_callback = True

    def send(
        self,
        credentials: SmsChannelCredentials,
        phone: str,
        *,
        text: str = "",
        template_code: str = "",
        template_params: list[str] | None = None,
    ) -> SmsSendResult:
        if not credentials.api_key_id or not credentials.api_secret:
            raise SmsProviderError("Twilio 通道缺少 Account SID / Auth Token,请到短信管理页补全。", "missing_credentials")
        messaging_service_sid = credentials.config("messaging_service_sid")
        if not credentials.from_number and not messaging_service_sid:
            raise SmsProviderError("Twilio 通道缺少主叫号码(或 Messaging Service SID)。", "missing_from_number")
        content = str(text or "").strip()
        if not content:
            raise SmsProviderError("短信正文为空,不能发送。", "empty_body")

        data = {"To": phone, "Body": content}
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = credentials.from_number
        status_callback = credentials.config("status_callback_url")
        if status_callback:
            data["StatusCallback"] = status_callback

        root = credentials.api_base_url.rstrip("/") or API_ROOT
        url = f"{root}/Accounts/{credentials.api_key_id}/Messages.json"
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.post(url, data=data, auth=(credentials.api_key_id, credentials.api_secret))
            body = resp.json()
        except httpx.HTTPError as e:
            raise SmsProviderError(f"连接 Twilio 接口失败:{e}", "network_error") from e
        except ValueError as e:
            raise SmsProviderError("Twilio 返回了无法解析的内容,请稍后重试。", "bad_response") from e

        if resp.status_code >= 400 or body.get("error_code"):
            code = str(body.get("code") or body.get("error_code") or resp.status_code)
            raise SmsProviderError(f"Twilio 拒绝下发:{body.get('message') or body.get('error_message') or code}", code)
        try:
            segments = max(1, int(body.get("num_segments") or 1))
        except (TypeError, ValueError):
            segments = 1
        return SmsSendResult(
            provider_message_id=str(body.get("sid") or ""),
            segments=segments,
            raw={"sid": body.get("sid"), "status": body.get("status"), "price": body.get("price")},
        )

    def parse_callback(
        self,
        credentials: SmsChannelCredentials,
        headers: dict[str, str],
        body: Any,
    ) -> list[SmsCallbackEvent]:
        """One callback carries only one message (unlike Tencent Cloud's array).

        StatusCallback carries `MessageStatus`; inbound SMS (Inbound webhook) carries `Body` + `From`.
        """
        if not isinstance(body, dict):
            return []
        sid = str(body.get("MessageSid") or body.get("SmsSid") or "")
        status = str(body.get("MessageStatus") or body.get("SmsStatus") or "").lower()
        now = datetime.utcnow()
        if status in _DELIVERED_STATUSES:
            return [SmsCallbackEvent(event_type="delivered", provider_message_id=sid,
                                     phone=str(body.get("To") or ""), occurred_at=now, raw=body)]
        if status in _FAILED_STATUSES:
            return [SmsCallbackEvent(
                event_type="failed", provider_message_id=sid, phone=str(body.get("To") or ""),
                failure_code=str(body.get("ErrorCode") or ""),
                failure_reason=str(body.get("ErrorMessage") or "Twilio 回执为投递失败"),
                occurred_at=now, raw=body,
            )]
        reply = str(body.get("Body") or "").strip()
        if reply:
            return [SmsCallbackEvent(
                event_type="opted_out" if is_opt_out_reply(reply) else "replied",
                provider_message_id=sid, phone=str(body.get("From") or ""),
                reply_text=reply, occurred_at=now, raw=body,
            )]
        # Intermediate states like sending/queued/sent carry no information; ignore them so the delivery status is not reverted.
        return []
