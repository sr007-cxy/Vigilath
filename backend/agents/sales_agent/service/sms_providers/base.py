"""Common protocol for SMS platform adapters.

This layer only knows "credentials + recipient number + text or template params", not ORM or Session.
Reason: credentials are stored encrypted and decryption goes through `service/channel_common.py`; if adapters consumed ORM rows directly,
they would depend back on the service package, whose facade imports the sending layer: a full circle into a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# GSM-03.38 basic character set (including escape characters). Text within this set is billed as 7bit; otherwise the whole message is UCS-2.
_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXTENDED = set("^{}\\[~]|€")

# A user replying with any of these counts as unsubscribing. It lives in base rather than one adapter because this is the compliance baseline:
# the Provisions on the Administration of Short Message Services require stopping on unsubscribe, and CN channels (Tencent Cloud/Aliyun) are stricter than international ones;
# recognizing STOP only on Twilio while a CN 「退订」 ("unsubscribe") reply is recorded as an ordinary reply would leak on the most critical side.
STOP_KEYWORDS = {
    "stop", "stopall", "unsubscribe", "cancel", "end", "quit", "td", "n",
    "退订", "拒收", "退订回复", "不再接收",
}


def is_opt_out_reply(text: str) -> bool:
    """Whether inbound content is an unsubscribe.

    Only "the whole message is an unsubscribe word" counts, no substring matching: 「我不想退订」 ("I don't want to unsubscribe") and "stop by tomorrow"
    contain the keyword but are precisely not unsubscribes; a false positive would permanently blacklist a customer still in talks.
    """
    keyword = str(text or "").strip().strip(".!。!,,、 \t\r\n").lower()
    return bool(keyword) and keyword in STOP_KEYWORDS


class SmsProviderError(Exception):
    """A failure returned by the platform, or credentials/params not meeting platform requirements.

    Deliberately does not reuse `SalesAgentError`: it lives in `service/common.py`, and importing it would drag in the whole
    service facade. `sms_send.py` catches this exception and converts it to `SalesAgentError`.
    `code` stores the platform's raw error code (e.g. Tencent Cloud `LimitExceeded.PhoneNumberDailyLimit`),
    persisted to `delivery.failure_code`, so debugging can go by code rather than a single Chinese sentence.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = str(code or "")


@dataclass
class SmsChannelCredentials:
    """A decrypted channel credential. Assembled by `sms_send.py` from the ORM row."""

    provider: str
    account_id: int = 0
    display_name: str = ""
    sign_name: str = ""
    sdk_app_id: str = ""
    api_key_id: str = ""
    api_secret: str = ""
    api_base_url: str = ""
    from_number: str = ""
    provider_config: dict[str, Any] = field(default_factory=dict)

    def config(self, key: str, default: str = "") -> str:
        return str(self.provider_config.get(key) or default).strip()


@dataclass
class SmsSendResult:
    """Receipt after the platform accepts the message. Accepted != delivered; delivery waits for `parse_callback`."""

    provider_message_id: str = ""
    segments: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmsCallbackEvent:
    """A normalized platform callback.

    `event_type` is one of `delivered` / `failed` / `replied` / `opted_out`, the same values as
    `sales_agent_sms_events.event_type`.
    """

    event_type: str
    provider_message_id: str = ""
    phone: str = ""
    failure_code: str = ""
    failure_reason: str = ""
    reply_text: str = ""
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def sms_segments(text: str) -> int:
    """Estimate segment count per carrier billing rules.

    Pure GSM-7: 160 characters for a single segment, 153 per segment when concatenated (room for the UDH header).
    Any non-GSM-7 character such as Chinese/emoji: the whole message becomes UCS-2, 70 for a single segment, 67 per segment when concatenated.
    This is an estimate: the real count comes from the platform receipt and is overwritten with the platform's value after acceptance.
    """
    body = str(text or "")
    if not body:
        return 0
    units = 0
    ucs2 = False
    for ch in body:
        if ch in _GSM7_BASIC:
            units += 1
        elif ch in _GSM7_EXTENDED:
            units += 2  # Escape characters take two 7bit units
        else:
            ucs2 = True
            break
    if ucs2:
        length = len(body)
        return 1 if length <= 70 else -(-length // 67)
    return 1 if units <= 160 else -(-units // 153)


class SmsProvider:
    """Adapter protocol. Each provider implements `send`; those that can push receipts also implement `parse_callback`."""

    key = ""
    label = ""
    content_mode = "freeform"
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
        raise NotImplementedError

    def parse_callback(
        self,
        credentials: SmsChannelCredentials,
        headers: dict[str, str],
        body: Any,
    ) -> list[SmsCallbackEvent]:
        """If the platform does not support HTTP push, return an empty list instead of raising: the callback endpoint returns 200 accordingly."""
        return []
