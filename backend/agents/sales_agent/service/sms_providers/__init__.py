"""Sales Agent SMS platform adapters.

Living **outside** `service/` is deliberate: `service/` is a linear layering table pinned by
`tests/sales_agent/test_service_layering.py`, where every `.py` must be registered in
LAYERS; adapters are naturally a package (base + one file per provider + registry), and putting them in
would degrade the layering table into a "directory listing". This package only does transport, touching neither ORM nor db: credentials are
decrypted by `service/sms_send.py` and passed in as `SmsChannelCredentials`, so this package does not import
`agents.sales_agent.service` and never loops around with the facade.
"""
from agents.sales_agent.service.sms_providers.base import (
    SmsCallbackEvent,
    SmsChannelCredentials,
    SmsProvider,
    SmsProviderError,
    SmsSendResult,
    STOP_KEYWORDS,
    is_opt_out_reply,
    sms_segments,
)
from agents.sales_agent.service.sms_providers.registry import (
    SMS_PROVIDER_CLASSES,
    provider_for_key,
    provider_supports_callback,
)

__all__ = [
    "SMS_PROVIDER_CLASSES",
    "SmsCallbackEvent",
    "SmsChannelCredentials",
    "SmsProvider",
    "SmsProviderError",
    "SmsSendResult",
    "STOP_KEYWORDS",
    "is_opt_out_reply",
    "provider_for_key",
    "provider_supports_callback",
    "sms_segments",
]
