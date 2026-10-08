"""Aliyun SMS (dysmsapi SendSms, 2017-05-25).

Uses the old RPC-style signature: sort parameters, build the canonical query string, sign with HMAC-SHA1.
Completely different from Tencent Cloud TC3; do not copy one from the other. Also does not pull in the aliyun SDK.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from agents.sales_agent.service.sms_providers.base import (
    SmsCallbackEvent,
    SmsChannelCredentials,
    SmsProvider,
    SmsProviderError,
    SmsSendResult,
)

HOST = "https://dysmsapi.aliyuncs.com"
VERSION = "2017-05-25"
ACTION = "SendSms"


def _percent_encode(value: str) -> str:
    """Encoding required by Aliyun: standard RFC3986, but `+`->`%20`, `*`->`%2A`, `%7E`->`~`."""
    return quote(str(value), safe="").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def _signature(params: dict[str, str], access_secret: str) -> str:
    canonical = "&".join(
        f"{_percent_encode(k)}={_percent_encode(params[k])}" for k in sorted(params)
    )
    string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical)}"
    digest = hmac.new((access_secret + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


class AliyunSmsProvider(SmsProvider):
    key = "aliyun"
    label = "阿里云短信"
    content_mode = "template"
    # Aliyun delivery receipts go through the MNS message queue, not HTTP push: there is no callback request to parse.
    supports_callback = False

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
            raise SmsProviderError("阿里云短信通道缺少 AccessKeyId / AccessKeySecret,请到短信管理页补全。", "missing_credentials")
        if not credentials.sign_name:
            raise SmsProviderError("阿里云短信通道缺少已报备的签名(SignName)。", "missing_sign_name")
        if not template_code:
            raise SmsProviderError("阿里云只能下发已报备模板,当前草稿没有指定模板号。", "missing_template")

        # Aliyun template variables are named {name}, not Tencent's positional {1}. The slot definition's name is the key;
        # slots without a name fall back to "1"/"2"..., matching templates written with {1}.
        params_map = {str(i + 1): str(v) for i, v in enumerate(template_params or [])}
        named = credentials.provider_config.get("param_names")
        if isinstance(named, list) and len(named) >= len(params_map):
            params_map = {str(named[i]): str(v) for i, v in enumerate(template_params or [])}

        query = {
            "AccessKeyId": credentials.api_key_id,
            "Action": ACTION,
            "Format": "JSON",
            "PhoneNumbers": phone,
            "RegionId": credentials.config("region", "cn-hangzhou"),
            "SignName": credentials.sign_name,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "TemplateCode": template_code,
            "TemplateParam": json.dumps(params_map, ensure_ascii=False, separators=(",", ":")),
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": VERSION,
        }
        query["Signature"] = _signature(query, credentials.api_secret)
        url = credentials.api_base_url.rstrip("/") or HOST
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.get(url, params=query)
            body = resp.json()
        except httpx.HTTPError as e:
            raise SmsProviderError(f"连接阿里云短信接口失败:{e}", "network_error") from e
        except ValueError as e:
            raise SmsProviderError("阿里云短信接口返回了无法解析的内容,请稍后重试。", "bad_response") from e

        code = str(body.get("Code") or "")
        if code != "OK":
            raise SmsProviderError(f"阿里云拒绝下发:{body.get('Message') or code}", code)
        return SmsSendResult(
            provider_message_id=str(body.get("BizId") or ""),
            segments=1,
            raw={"request_id": body.get("RequestId"), "biz_id": body.get("BizId")},
        )

    def parse_callback(
        self,
        credentials: SmsChannelCredentials,
        headers: dict[str, str],
        body: Any,
    ) -> list[SmsCallbackEvent]:
        """Aliyun receipts must be pulled from the MNS queue; there is no HTTP push to parse.

        Return an empty list rather than raising: the callback endpoint returns 200 accordingly, so anyone manually hitting this URL
        does not see a 5xx that looks like a config error. Actually receiving receipts would require a separate MNS pull job.
        """
        return []
