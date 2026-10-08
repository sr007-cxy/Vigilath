"""Tencent Cloud SMS (SendSms, 2021-01-11).

The signature is modeled on the TC3-HMAC-SHA256 implementation in `geo/services/sms_service.py`, turned into a parameterized version:
that file's credentials are global settings and it is tied to verification-code rate limiting and SmsCodeORM, none of which we want here.
Likewise does not pull in tencentcloud-sdk-python (tens of MB, for a single API).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
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

HOST = "sms.tencentcloudapi.com"
SERVICE = "sms"
VERSION = "2021-01-11"
ACTION = "SendSms"
DEFAULT_REGION = "ap-guangzhou"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _authorization(secret_id: str, secret_key: str, payload: str, timestamp: int) -> str:
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{HOST}\nx-tc-action:{ACTION.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers, signed_headers,
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    ])
    scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, SERVICE)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _callback_time(value: Any) -> datetime | None:
    """Tencent Cloud receipt time is a 10-digit second-level Unix timestamp (the `user_receive_time` field is a string date)."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.utcfromtimestamp(ts) if ts > 0 else None


class TencentCloudSmsProvider(SmsProvider):
    key = "tencent_cloud"
    label = "腾讯云短信"
    content_mode = "template"
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
            raise SmsProviderError("腾讯云短信通道缺少 SecretId / SecretKey,请到短信管理页补全。", "missing_credentials")
        if not credentials.sign_name:
            raise SmsProviderError("腾讯云短信通道缺少已报备的签名(SignName)。", "missing_sign_name")
        if not credentials.sdk_app_id:
            raise SmsProviderError("腾讯云短信通道缺少短信应用 SmsSdkAppId。", "missing_sdk_app_id")
        if not template_code:
            raise SmsProviderError("腾讯云只能下发已报备模板,当前草稿没有指定模板号。", "missing_template")

        region = credentials.config("region", DEFAULT_REGION)
        payload = json.dumps({
            "PhoneNumberSet": [phone],
            "SmsSdkAppId": credentials.sdk_app_id,
            "SignName": credentials.sign_name,
            "TemplateId": template_code,
            "TemplateParamSet": [str(p) for p in (template_params or [])],
        }, separators=(",", ":"), ensure_ascii=False)
        timestamp = int(time.time())
        headers = {
            "Authorization": _authorization(credentials.api_key_id, credentials.api_secret, payload, timestamp),
            "Content-Type": "application/json; charset=utf-8",
            "Host": HOST,
            "X-TC-Action": ACTION,
            "X-TC-Version": VERSION,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": region,
        }
        url = credentials.api_base_url.rstrip("/") or f"https://{HOST}"
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.post(url, content=payload.encode("utf-8"), headers=headers)
            body = resp.json().get("Response", {})
        except httpx.HTTPError as e:
            raise SmsProviderError(f"连接腾讯云短信接口失败:{e}", "network_error") from e
        except ValueError as e:
            raise SmsProviderError("腾讯云短信接口返回了无法解析的内容,请稍后重试。", "bad_response") from e

        error = body.get("Error") if isinstance(body, dict) else None
        if error:
            raise SmsProviderError(
                f"腾讯云拒绝下发:{error.get('Message') or error}", str(error.get("Code") or "")
            )
        statuses = body.get("SendStatusSet") or []
        if not statuses:
            raise SmsProviderError("腾讯云未返回下发结果,无法确认是否发出,请到控制台核对后重试。", "empty_status")
        status = statuses[0]
        if str(status.get("Code")) != "Ok":
            raise SmsProviderError(
                f"腾讯云下发失败:{status.get('Message') or status.get('Code')}", str(status.get("Code") or "")
            )
        # Tencent Cloud bills per 70 characters (Chinese); Fee is the actual billed segment count.
        try:
            segments = max(1, int(status.get("Fee") or 1))
        except (TypeError, ValueError):
            segments = 1
        return SmsSendResult(
            provider_message_id=str(status.get("SerialNo") or ""),
            segments=segments,
            raw={"request_id": body.get("RequestId"), "status": status},
        )

    def parse_callback(
        self,
        credentials: SmsChannelCredentials,
        headers: dict[str, str],
        body: Any,
    ) -> list[SmsCallbackEvent]:
        """Delivery receipts and inbound SMS are both JSON arrays, distinguished by fields:

        Delivery receipts have `report_status` (SUCCESS/FAIL); inbound SMS has `text` (the user's reply).
        Both carry `sid`, but the inbound sid is not the same as the outbound SerialNo, so inbound events are attributed by number.
        """
        rows = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
        events: list[SmsCallbackEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            phone = str(row.get("mobile") or "")
            nation = str(row.get("nationcode") or "")
            if phone and nation and not phone.startswith("+"):
                phone = f"+{nation}{phone}"
            if "report_status" in row:
                ok = str(row.get("report_status") or "").upper() == "SUCCESS"
                events.append(SmsCallbackEvent(
                    event_type="delivered" if ok else "failed",
                    provider_message_id=str(row.get("sid") or ""),
                    phone=phone,
                    failure_code="" if ok else str(row.get("errmsg") or ""),
                    failure_reason="" if ok else str(row.get("description") or row.get("errmsg") or "运营商回执为失败"),
                    occurred_at=_callback_time(row.get("time")),
                    raw=row,
                ))
            elif row.get("text") is not None:
                # Unsubscribe words on CN channels (TD / 退订) arrive exactly as inbound SMS; missing them is a violation.
                reply_text = str(row.get("text") or "")
                events.append(SmsCallbackEvent(
                    event_type="opted_out" if is_opt_out_reply(reply_text) else "replied",
                    provider_message_id="",
                    phone=phone,
                    reply_text=reply_text,
                    occurred_at=_callback_time(row.get("time")),
                    raw=row,
                ))
        return events
