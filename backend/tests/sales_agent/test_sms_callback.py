"""SMS receipt / inbound SMS callback -- status write-back and unsubscribe handling.

This endpoint requires no login (carriers don't log in with a token), and both kinds of errors are costly:
- Silent 200: the carrier side always shows "callback OK" while nothing is persisted here, so "delivered" in stats is all guesswork;
- Missed unsubscribe: the user replied TD and keeps receiving, which means complaints and a suspended signature.

So this pins, case by case, "every anomaly has an explicit status code + explicit Chinese message", and "unsubscribe writes all three places".

Run:
    venv/bin/python -m pytest tests/sales_agent/test_sms_callback.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentComplianceLogORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentReportShareORM,
    SalesAgentSmsAccountORM,
    SalesAgentSmsDeliveryORM,
    SalesAgentSmsEventORM,
)
from agents.sales_agent.service import (
    SalesAgentError,
    apply_sales_agent_sms_callback,
    channel_callback_token,
    serialize_sales_agent_sms_account,
    track_sms_short_link,
    verify_channel_callback_token,
)
from agents.sales_agent.service.sms_config import SMS_CALLBACK_TOKEN_KIND
from geo.database import Base
from geo.models.user import UserORM

USER = 1
PHONE = "+8613800138000"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="cb@test.local", password_hash="x", is_active=True))
    company = SalesAgentCompanyORM(name="示例清洁科技")
    s.add(company)
    s.flush()
    contact = SalesAgentCompanyContactORM(
        company_id=company.id, contact_name="王经理", phone="13800138000",
        consent_status="consented", is_reachable=True,
    )
    s.add(contact)
    lead = SalesAgentLeadRecommendationORM(user_id=USER, company_id=company.id, score=80, status="Contacted")
    s.add(lead)
    account = SalesAgentSmsAccountORM(
        scope="user", user_id=USER, display_name="腾讯云通道", provider="tencent_cloud",
        content_mode="template", region_scope="cn", sign_name="示例清洁",
    )
    s.add(account)
    s.flush()
    delivery = SalesAgentSmsDeliveryORM(
        user_id=USER, lead_id=lead.id, company_id=company.id, contact_id=contact.id,
        sms_account_id=account.id, provider="tencent_cloud", provider_message_id="sid-1",
        recipient_phone=PHONE, recipient_country="CN", status="sent", sent_at=datetime.utcnow(),
    )
    s.add(delivery)
    s.commit()
    s.company, s.contact, s.lead, s.account, s.delivery = company, contact, lead, account, delivery
    try:
        yield s
    finally:
        s.close()


def _token(account_id: int) -> str:
    return channel_callback_token(SMS_CALLBACK_TOKEN_KIND, account_id)


def _report(db, provider="tencent_cloud", status="SUCCESS", **extra):
    row = {"sid": "sid-1", "mobile": "13800138000", "nationcode": "86", "report_status": status}
    row.update(extra)
    return apply_sales_agent_sms_callback(db, provider, _token(db.account.id), body=[row])


def _event_types(db, delivery_id: int) -> list[str]:
    return [
        e.event_type
        for e in db.query(SalesAgentSmsEventORM)
        .filter(SalesAgentSmsEventORM.delivery_id == delivery_id)
        .order_by(SalesAgentSmsEventORM.id.asc())
        .all()
    ]


# --- token ---------------------------------------------------------------------

def test_token_round_trips_without_being_stored():
    """The token is derived from SECRET_KEY and not persisted -- `sanitize_channel_provider_config`
    strips any key containing token as a secret, so storing it would store nothing."""
    token = channel_callback_token(SMS_CALLBACK_TOKEN_KIND, 42)
    assert token.startswith("42.")
    assert verify_channel_callback_token(SMS_CALLBACK_TOKEN_KIND, token) == 42


@pytest.mark.parametrize("bad", ["", "42", "42.", "42.deadbeef", "abc.def", "43.{sig}"])
def test_tampered_token_is_rejected(bad):
    token = channel_callback_token(SMS_CALLBACK_TOKEN_KIND, 42)
    probe = bad.replace("{sig}", token.split(".", 1)[1])
    assert verify_channel_callback_token(SMS_CALLBACK_TOKEN_KIND, probe) == 0


def test_token_is_scoped_to_its_kind():
    """A token signed for the same account ID on another channel must not be usable for the SMS callback."""
    other = channel_callback_token("sales-agent-email-callback", 42)
    assert verify_channel_callback_token(SMS_CALLBACK_TOKEN_KIND, other) == 0


def test_callback_url_is_exposed_on_the_account(db):
    payload = serialize_sales_agent_sms_account(db.account)
    assert payload["supports_callback"] is True
    assert payload["callback_url"].endswith(f"/sms/callback/tencent_cloud/{_token(db.account.id)}")


# --- Rejection paths: each must say something, never a silent 200 -----------------------------------

def test_bad_token_is_403_not_silent_200(db):
    with pytest.raises(SalesAgentError) as e:
        apply_sales_agent_sms_callback(db, "tencent_cloud", "999.bogus", body=[])
    assert e.value.status_code == 403
    assert "校验串" in str(e.value)


def test_deleted_account_is_404(db):
    token = _token(db.account.id)
    db.delete(db.delivery)
    db.delete(db.account)
    db.commit()
    with pytest.raises(SalesAgentError) as e:
        apply_sales_agent_sms_callback(db, "tencent_cloud", token, body=[])
    assert e.value.status_code == 404


def test_provider_mismatch_names_both_sides(db):
    """Switched platforms but didn't update the callback URL in the console -- the error must name both sides."""
    with pytest.raises(SalesAgentError) as e:
        apply_sales_agent_sms_callback(db, "twilio", _token(db.account.id), body=[])
    assert e.value.status_code == 400
    assert "twilio" in str(e.value) and "tencent_cloud" in str(e.value)


def test_provider_without_http_callback_says_so(db):
    """Alibaba Cloud uses MNS queues, not HTTP push -- configuring this URL will never receive data; say so right away."""
    db.account.provider = "aliyun"
    db.commit()
    with pytest.raises(SalesAgentError) as e:
        apply_sales_agent_sms_callback(db, "aliyun", _token(db.account.id), body=[])
    assert e.value.status_code == 400
    assert "MNS" in str(e.value)


def test_unknown_provider_error_is_translated_not_500(db):
    """The provider layer raises SmsProviderError; untranslated it would surface to the carrier as a 500."""
    db.account.provider = "aws_sns"
    db.commit()
    with pytest.raises(SalesAgentError) as e:
        apply_sales_agent_sms_callback(db, "aws_sns", _token(db.account.id), body=[])
    assert e.value.status_code == 400


# --- Status receipts ------------------------------------------------------------------

def test_success_report_marks_delivered(db):
    out = _report(db)
    assert out["received"] == 1 and out["matched"] == 1
    db.refresh(db.delivery)
    assert db.delivery.status == "delivered" and db.delivery.delivered_at is not None
    assert "delivered" in _event_types(db, db.delivery.id)


def test_fail_report_keeps_operator_code(db):
    _report(db, status="FAIL", errmsg="MOBILE_BLACKLIST", description="号码在黑名单中")
    db.refresh(db.delivery)
    assert db.delivery.status == "failed"
    assert db.delivery.failure_code == "MOBILE_BLACKLIST"
    assert db.delivery.failure_reason == "号码在黑名单中"


def test_late_delivered_report_does_not_undo_a_failure(db):
    """Receipts arrive out of order. A row already failed must not be flipped back to delivered by a late SUCCESS."""
    _report(db, status="FAIL", errmsg="X")
    _report(db, status="SUCCESS")
    db.refresh(db.delivery)
    assert db.delivery.status == "failed"


def test_late_failed_report_does_not_undo_a_delivery(db):
    _report(db, status="SUCCESS")
    _report(db, status="FAIL", errmsg="X")
    db.refresh(db.delivery)
    assert db.delivery.status == "delivered"


def test_report_for_another_account_is_not_matched(db):
    """The same number may have been sent to via two channels -- cross-account matching would attribute the receipt to someone else."""
    other = SalesAgentSmsAccountORM(scope="user", user_id=USER, display_name="另一条", provider="tencent_cloud")
    db.add(other)
    db.commit()
    out = apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(other.id),
        body=[{"sid": "sid-1", "mobile": "13800138000", "nationcode": "86", "report_status": "SUCCESS"}],
    )
    assert out["received"] == 1 and out["matched"] == 0
    db.refresh(db.delivery)
    assert db.delivery.status == "sent"


def test_unmatched_message_id_is_not_an_error(db):
    """The same channel also sends non-module SMS like verification codes; failing to match is normal."""
    db.delivery.recipient_phone = "+8613900139000"
    db.commit()
    out = apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"sid": "unknown-sid", "mobile": "13777777777", "nationcode": "86", "report_status": "SUCCESS"}],
    )
    assert out["received"] == 1 and out["matched"] == 0


# --- Inbound SMS and unsubscribe ------------------------------------------------------------

def test_plain_reply_is_counted_but_does_not_unsubscribe(db):
    apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"mobile": "13800138000", "nationcode": "86", "text": "请发个报价"}],
    )
    db.refresh(db.delivery)
    db.refresh(db.contact)
    assert db.delivery.reply_count == 1
    assert db.delivery.opted_out_at is None
    assert db.contact.consent_status == "consented"


@pytest.mark.parametrize("text", ["TD", "td", " 退订 ", "STOP", "N"])
def test_cn_opt_out_writes_back_to_contact_lead_and_compliance(db, text):
    """Unsubscribe must write all three places. Writing only contact leaves the company looking "reachable" in the lead list,
    and the next recommendation round includes it again -- the user replies TD and gets a second message, which is where complaints come from."""
    apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"mobile": "13800138000", "nationcode": "86", "text": text}],
    )
    db.refresh(db.delivery)
    db.refresh(db.contact)
    db.refresh(db.lead)
    assert db.delivery.opted_out_at is not None
    assert db.contact.consent_status == "unsubscribed"
    assert db.lead.status == "Unsubscribed"
    log = db.query(SalesAgentComplianceLogORM).filter(SalesAgentComplianceLogORM.action == "sms_opt_out").one()
    assert log.decision == "blocked" and log.risk_level == "high"


def test_a_sentence_containing_a_stop_word_is_not_an_opt_out(db):
    """「我不想退订」 ("I don't want to unsubscribe") contains the keyword but means the opposite -- a misjudgment would permanently block a customer still in talks."""
    apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"mobile": "13800138000", "nationcode": "86", "text": "我不想退订,继续发吧"}],
    )
    db.refresh(db.contact)
    assert db.contact.consent_status == "consented"


def test_twilio_form_encoded_stop_opts_out(db):
    """Twilio callbacks are form-encoded single items, a different shape from Tencent Cloud's JSON arrays."""
    db.account.provider = "twilio"
    db.delivery.provider = "twilio"
    db.commit()
    apply_sales_agent_sms_callback(
        db, "twilio", _token(db.account.id),
        body={"MessageSid": "sid-1", "From": PHONE, "Body": "STOP"},
    )
    db.refresh(db.contact)
    assert db.contact.consent_status == "unsubscribed"


def test_opt_out_matches_contact_by_phone_when_delivery_has_none(db):
    """Inbound SMS only carries the number, and DB number formats aren't uniform (some +86, some bare) -- normalize both sides before comparing."""
    db.delivery.contact_id = None
    db.commit()
    apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"mobile": "13800138000", "nationcode": "86", "text": "TD"}],
    )
    db.refresh(db.contact)
    assert db.contact.consent_status == "unsubscribed"


def test_already_blacklisted_contact_is_not_downgraded(db):
    db.contact.consent_status = "blacklisted"
    db.commit()
    apply_sales_agent_sms_callback(
        db, "tencent_cloud", _token(db.account.id),
        body=[{"mobile": "13800138000", "nationcode": "86", "text": "TD"}],
    )
    db.refresh(db.contact)
    assert db.contact.consent_status == "blacklisted"


# --- Short links ----------------------------------------------------------------------

def test_short_link_counts_the_click_and_returns_the_report_url(db):
    share = SalesAgentReportShareORM(user_id=USER, lead_id=db.lead.id, company_id=db.company.id,
                                     expires_at=datetime.utcnow() + timedelta(days=30))
    db.add(share)
    db.flush()
    db.delivery.report_share_id = share.id
    db.commit()

    out = track_sms_short_link(db, db.delivery.short_link_token)

    assert out["found"] is True
    assert share.public_token in out["url"]
    db.refresh(db.delivery)
    assert db.delivery.click_count == 1
    assert db.delivery.first_click_at is not None


def test_unknown_short_link_is_empty_not_an_error(db):
    """Short links are on the user's phone; opening one after the delivery record is deleted should be "nothing happens", not an error page."""
    assert track_sms_short_link(db, "no-such-token") == {"found": False, "url": ""}
