"""provider key -> adapter.

Platforms not yet integrated **raise immediately with a clear message**, no silent downgrade to another channel:
quietly sending through another channel means the signature and caller number are not what the user thinks.
"""
from __future__ import annotations

from agents.sales_agent.service.sms_providers.aliyun import AliyunSmsProvider
from agents.sales_agent.service.sms_providers.base import SmsProvider, SmsProviderError
from agents.sales_agent.service.sms_providers.tencent import TencentCloudSmsProvider
from agents.sales_agent.service.sms_providers.twilio import TwilioSmsProvider

SMS_PROVIDER_CLASSES: dict[str, type[SmsProvider]] = {
    TencentCloudSmsProvider.key: TencentCloudSmsProvider,
    AliyunSmsProvider.key: AliyunSmsProvider,
    TwilioSmsProvider.key: TwilioSmsProvider,
}

# Platforms the config layer allows saving but the sending path does not implement yet. Listed separately so errors can name them,
# rather than a vague "unsupported provider".
_KNOWN_NOT_IMPLEMENTED = {
    "aws_sns": "AWS SNS",
    "custom_http": "自定义 HTTP 网关",
}


def provider_for_key(provider: str) -> SmsProvider:
    key = str(provider or "").strip().lower()
    cls = SMS_PROVIDER_CLASSES.get(key)
    if cls is not None:
        return cls()
    label = _KNOWN_NOT_IMPLEMENTED.get(key)
    if label:
        raise SmsProviderError(
            f"{label} 通道尚未接入真实发送,请改用腾讯云 / 阿里云(国内)或 Twilio(国际)。",
            "provider_not_implemented",
        )
    raise SmsProviderError(
        f"未知的短信服务商「{provider}」,请到短信管理页重新选择通道类型。",
        "provider_unknown",
    )


def provider_supports_callback(provider: str) -> bool:
    cls = SMS_PROVIDER_CLASSES.get(str(provider or "").strip().lower())
    return bool(cls and cls.supports_callback)
