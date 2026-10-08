"""SMS send chain -- number normalization, channel routing, two content modes, and every refusal must say why.

What's pinned here isn't "it can send", but "when it can't, does the record clearly state why". SMS is billed per message and
complaints suspend the signature, so silent failures cost far more than with email: the user only sees `failed`, not knowing whether it's no number,
no template, or a cooldown hit, so they keep resending and get the signature suspended.

The provider is stubbed throughout -- really calling Tencent Cloud/Twilio costs money, and this layer verifies the state machine, not the signing algorithm.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_sms_send.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentSmsAccountORM,
    SalesAgentSmsDeliveryORM,
    SalesAgentSmsEventORM,
    SalesAgentSmsSettingORM,
    SalesAgentSmsTemplateORM,
)
from agents.sales_agent.service import (
    encrypt_channel_secret,
    normalize_msisdn,
    send_sales_agent_sms_delivery,
    sms_recipient_country_for_lead,
)
from agents.sales_agent.service.sms_providers import SmsProviderError, SmsSendResult
from geo.database import Base
from geo.models.user import UserORM

USER = 1


class _StubProvider:
    """Fake adapter recording call arguments. When `error` is non-empty, it returns a platform rejection."""

    def __init__(self, error: SmsProviderError | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def send(self, credentials, phone, *, text="", template_code="", template_params=None):
        self.calls.append({
            "provider": credentials.provider,
            "sign_name": credentials.sign_name,
            "api_key_id": credentials.api_key_id,
            "phone": phone,
            "text": text,
            "template_code": template_code,
            "template_params": list(template_params or []),
        })
        if self.error is not None:
            raise self.error
        return SmsSendResult(provider_message_id="pm-1", segments=2, raw={"ok": True})


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="sms@test.local", password_hash="x", is_active=True))
    company = SalesAgentCompanyORM(name="示例清洁科技")
    s.add(company)
    s.flush()
    s.add(SalesAgentCompanyContactORM(
        company_id=company.id, contact_name="王经理", phone="13800138000",
        consent_status="consented", is_reachable=True,
    ))
    lead = SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=80, status="New")
    s.add(lead)
    s.flush()
    s.commit()
    s.company = company
    s.lead = lead
    try:
        yield s
    finally:
        s.close()


def _account(s, **kw) -> SalesAgentSmsAccountORM:
    defaults = dict(
        scope="user", user_id=USER, display_name="测试通道", enabled=True, is_default=True,
        provider="tencent_cloud", content_mode="template", region_scope="cn", sign_name="示例清洁",
        sdk_app_id="1400000000",
        api_key_id_encrypted=encrypt_channel_secret("AKID-test"),
        api_secret_encrypted=encrypt_channel_secret("SK-test"),
    )
    defaults.update(kw)
    row = SalesAgentSmsAccountORM(**defaults)
    s.add(row)
    s.flush()
    return row


def _draft(s, content: str = "你好,这是一条测试短信。", metadata: dict | None = None) -> SalesAgentOutreachLogORM:
    row = SalesAgentOutreachLogORM(
        lead_id=s.lead.id, channel="sms", content=content, send_status="draft",
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
    )
    s.add(row)
    s.flush()
    return row


def _send(s, draft, monkeypatch, provider: _StubProvider | None = None) -> tuple[SalesAgentSmsDeliveryORM, _StubProvider]:
    from agents.sales_agent.service import sms_send as mod

    stub = provider or _StubProvider()
    monkeypatch.setattr(mod, "provider_for_key", lambda key: stub)
    delivery = send_sales_agent_sms_delivery(s, USER, draft, s.lead, s.company)
    s.commit()
    return delivery, stub


def _event_types(s, delivery) -> list[str]:
    return [
        e.event_type
        for e in s.query(SalesAgentSmsEventORM)
        .filter(SalesAgentSmsEventORM.delivery_id == delivery.id)
        .order_by(SalesAgentSmsEventORM.id.asc())
        .all()
    ]


# --- Number normalization ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("13800138000", ("+8613800138000", "CN", True)),
    ("+86 138 0013 8000", ("+8613800138000", "CN", True)),
    ("008613800138000", ("+8613800138000", "CN", True)),
    ("8613800138000", ("+8613800138000", "CN", True)),
    ("+1 415 555 2671", ("+14155552671", "US", True)),
])
def test_normalize_msisdn_accepts_common_writings(raw, expected):
    assert normalize_msisdn(raw) == expected


@pytest.mark.parametrize("raw", ["4008201234", "010-62345678", "95588", "", "abc"])
def test_normalize_msisdn_rejects_non_mobile(raw):
    """400/800/landline/short numbers can't receive SMS in China and must be judged unsendable, not billed for nothing."""
    phone, _country, is_mobile = normalize_msisdn(raw)
    assert not (phone and is_mobile)


# --- Recipients --------------------------------------------------------------------

def test_no_mobile_contact_is_no_recipient_with_specific_reason(db, monkeypatch):
    db.query(SalesAgentCompanyContactORM).update({"phone": "4008201234"})
    _account(db)
    db.commit()

    delivery, stub = _send(db, _draft(db), monkeypatch)

    assert delivery.status == "no_recipient"
    assert "座机/400" in delivery.failure_reason
    assert stub.calls == []
    assert "no_recipient" in _event_types(db, delivery)


def test_recipient_country_drives_channel_routing(db):
    assert sms_recipient_country_for_lead(db, db.lead) == "CN"


def test_intl_only_channel_cannot_serve_a_cn_number(db, monkeypatch):
    """Channel configured but its sendable scope doesn't cover it -- the error should say "can't send to this country", not "not configured"."""
    _account(db, region_scope="intl", provider="twilio", content_mode="freeform")
    db.commit()

    delivery, stub = _send(db, _draft(db), monkeypatch)

    assert delivery.status == "disabled"
    assert "CN" in delivery.failure_reason and "可发范围" in delivery.failure_reason
    assert stub.calls == []


# --- Switches ----------------------------------------------------------------------

def test_sending_disabled_records_reason_instead_of_sending(db, monkeypatch):
    _account(db)
    db.add(SalesAgentSmsSettingORM(scope="user", user_id=USER, sending_enabled=False))
    db.commit()

    delivery, stub = _send(db, _draft(db), monkeypatch)

    assert delivery.status == "disabled"
    assert "关闭" in delivery.failure_reason
    assert stub.calls == []
    assert "send_disabled" in _event_types(db, delivery)


def test_no_account_at_all_says_so(db, monkeypatch):
    db.commit()
    delivery, stub = _send(db, _draft(db), monkeypatch)

    assert delivery.status == "disabled"
    assert "未配置可用短信通道" in delivery.failure_reason
    assert stub.calls == []


# --- Free-text channel --------------------------------------------------------------

def test_freeform_channel_sends_draft_content(db, monkeypatch):
    _account(db, provider="twilio", content_mode="freeform", region_scope="all", from_number="+15005550006")
    db.commit()

    delivery, stub = _send(db, _draft(db, content="Hi there, quick question about your AI visibility."), monkeypatch)

    assert delivery.status == "sent"
    assert delivery.provider_message_id == "pm-1"
    assert delivery.segments == 2  # the platform receipt's segment count overrides the local estimate
    assert delivery.recipient_phone == "+8613800138000"
    assert stub.calls[0]["text"].startswith("Hi there")
    assert stub.calls[0]["template_code"] == ""
    assert _event_types(db, delivery) == ["send_attempt", "sent"]


def test_freeform_empty_body_is_rejected_not_sent_blank(db, monkeypatch):
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.commit()

    delivery, stub = _send(db, _draft(db, content="   "), monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "empty_body"
    assert stub.calls == []


def test_provider_credentials_are_decrypted_before_send(db, monkeypatch):
    """Credentials are stored encrypted and must be decrypted before sending -- otherwise the platform receives ciphertext."""
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.commit()

    _delivery, stub = _send(db, _draft(db, content="hello"), monkeypatch)

    assert stub.calls[0]["api_key_id"] == "AKID-test"


# --- Template channel ------------------------------------------------------------------

def _approved_template(s, account, **kw) -> SalesAgentSmsTemplateORM:
    defaults = dict(
        account_id=account.id, template_code="123456", name="拓客", body="您好{1},您的官网 AI 可见度评分为{2}分。",
        param_slots_json=json.dumps([{"index": 1, "name": "称呼"}, {"index": 2, "name": "评分"}], ensure_ascii=False),
        purpose="outreach", approved_status="approved", enabled=True,
    )
    defaults.update(kw)
    row = SalesAgentSmsTemplateORM(**defaults)
    s.add(row)
    s.flush()
    return row


def test_template_channel_sends_code_and_params_not_freeform_text(db, monkeypatch):
    """Domestic channels send a template id + variables; not a single character of LLM-written body goes out."""
    account = _account(db)
    _approved_template(db, account)
    draft = _draft(db, content="这段自由文案不该被发出去", metadata={
        "sms_content_mode": "template", "sms_template_code": "123456", "sms_template_params": ["王经理", "62"],
    })
    db.commit()

    delivery, stub = _send(db, draft, monkeypatch)

    assert delivery.status == "sent"
    assert delivery.template_code == "123456"
    assert json.loads(delivery.template_params_json) == ["王经理", "62"]
    assert stub.calls[0]["template_params"] == ["王经理", "62"]
    assert "这段自由文案" not in stub.calls[0]["text"]
    assert delivery.content_preview.startswith("您好王经理")


def test_template_channel_without_any_template_is_rejected_with_next_step(db, monkeypatch):
    _account(db)
    db.commit()

    delivery, stub = _send(db, _draft(db), monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "missing_template"
    assert "报备" in delivery.failure_reason and "短信管理页" in delivery.failure_reason
    assert stub.calls == []


def test_template_channel_missing_params_is_rejected_naming_the_slot(db, monkeypatch):
    """A missing variable must be named, not filled with an empty string and sent -- 「尊敬的 ,您好」 ("Dear ,hello") is worse than not sending."""
    account = _account(db)
    _approved_template(db, account)
    draft = _draft(db, metadata={"sms_template_code": "123456", "sms_template_params": ["王经理"]})
    db.commit()

    delivery, stub = _send(db, draft, monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "missing_template_params"
    assert "评分" in delivery.failure_reason
    assert stub.calls == []


def test_template_channel_empty_param_is_rejected(db, monkeypatch):
    account = _account(db)
    _approved_template(db, account)
    draft = _draft(db, metadata={"sms_template_code": "123456", "sms_template_params": ["王经理", "  "]})
    db.commit()

    delivery, stub = _send(db, draft, monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "missing_template_params"
    assert stub.calls == []


# --- Rate limiting ----------------------------------------------------------------------

def test_per_contact_cooldown_rejects_with_next_available_time(db, monkeypatch):
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.add(SalesAgentSmsSettingORM(scope="user", user_id=USER, sending_enabled=True, per_contact_cooldown_days=7))
    db.add(SalesAgentSmsDeliveryORM(
        user_id=USER, company_id=db.company.id, recipient_phone="+8613800138000",
        status="sent", sent_at=datetime.utcnow() - timedelta(days=1),
    ))
    db.commit()

    delivery, stub = _send(db, _draft(db, content="再发一条"), monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "rate_limited"
    assert "冷却期" in delivery.failure_reason and "之后才能再次发送" in delivery.failure_reason
    assert stub.calls == []
    assert "rejected" in _event_types(db, delivery)


def test_cooldown_does_not_count_failed_attempts(db, monkeypatch):
    """A platform-rejected attempt disturbed nobody and shouldn't consume the user's cooldown quota."""
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.add(SalesAgentSmsSettingORM(scope="user", user_id=USER, sending_enabled=True, per_contact_cooldown_days=7))
    db.add(SalesAgentSmsDeliveryORM(
        user_id=USER, company_id=db.company.id, recipient_phone="+8613800138000",
        status="failed", failed_at=datetime.utcnow() - timedelta(days=1),
    ))
    db.commit()

    delivery, _stub = _send(db, _draft(db, content="再发一条"), monkeypatch)

    assert delivery.status == "sent"


def test_daily_cap_rejects_with_used_and_limit(db, monkeypatch):
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.add(SalesAgentSmsSettingORM(scope="user", user_id=USER, sending_enabled=True, daily_send_cap=2))
    now = datetime.utcnow()
    db.add_all([
        SalesAgentSmsDeliveryORM(user_id=USER, company_id=db.company.id, recipient_phone="+8613900139000",
                                 status="sent", sent_at=now),
        SalesAgentSmsDeliveryORM(user_id=USER, company_id=db.company.id, recipient_phone="+8613700137000",
                                 status="delivered", sent_at=now),
    ])
    db.commit()

    delivery, stub = _send(db, _draft(db, content="第三条"), monkeypatch)

    assert delivery.status == "rejected" and delivery.failure_code == "rate_limited"
    assert "上限 2 条" in delivery.failure_reason and "已发 2 条" in delivery.failure_reason
    assert stub.calls == []


# --- Platform failures ------------------------------------------------------------------

def test_provider_error_keeps_platform_code_for_reconciliation(db, monkeypatch):
    """Platform error codes are persisted as-is -- with only a Chinese sentence, you can't reconcile by code in the platform console."""
    _account(db, provider="twilio", content_mode="freeform", region_scope="all")
    db.commit()
    stub = _StubProvider(error=SmsProviderError("模板未审核通过", "FailedOperation.TemplateIncorrectOrUnapproved"))

    delivery, _ = _send(db, _draft(db, content="hello"), monkeypatch, provider=stub)

    assert delivery.status == "failed"
    assert delivery.failure_code == "FailedOperation.TemplateIncorrectOrUnapproved"
    assert delivery.failure_reason == "模板未审核通过"
    assert delivery.failed_at is not None
    assert _event_types(db, delivery) == ["send_attempt", "failed"]


def test_email_draft_cannot_be_sent_as_sms(db):
    draft = SalesAgentOutreachLogORM(lead_id=db.lead.id, channel="email", content="x")
    db.add(draft)
    db.flush()

    from agents.sales_agent.service import SalesAgentError

    with pytest.raises(SalesAgentError):
        send_sales_agent_sms_delivery(db, USER, draft, db.lead, db.company)
