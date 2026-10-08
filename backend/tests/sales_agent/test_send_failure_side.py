"""Recipient-side / sender-side classification of send failures (scheduled send A1, design 4.1.1).

Falling back to "the next mailbox at the same company" only makes sense for **recipient-side** failures. When the sender side is broken, switching recipients
is useless, and doing so anyway burns through every mailbox at the company in one config error -- so the point here isn't
"classify precisely", it's "when unsure, it must land on the sender side".

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_send_failure_side.py -q
"""
from __future__ import annotations

import smtplib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
)
from agents.sales_agent.service.common import (
    FAILURE_SIDE_RECIPIENT,
    FAILURE_SIDE_SENDER,
    SalesAgentError,
)
from agents.sales_agent.service.email_send import (
    _delivery_recipient_candidates,
    _resend_body_blames_recipient,
    _resolve_delivery_recipient,
    _smtp_failure,
    delivery_failure_side,
)
from geo.database import Base


def test_default_is_sender_side():
    """Errors not explicitly marked count as sender side -- a wrong default silently burns addresses."""
    assert SalesAgentError("随便什么错").failure_side == FAILURE_SIDE_SENDER


def test_unknown_value_falls_back_to_sender():
    assert SalesAgentError("x", failure_side="whatever").failure_side == FAILURE_SIDE_SENDER


@pytest.mark.parametrize("exc", [
    smtplib.SMTPAuthenticationError(535, b"auth failed"),
    smtplib.SMTPSenderRefused(553, b"sender rejected", "me@example.com"),
    smtplib.SMTPConnectError(421, b"cannot connect"),
    smtplib.SMTPServerDisconnected("closed"),
    smtplib.SMTPHeloError(501, b"bad helo"),
    TimeoutError("timed out"),
    smtplib.SMTPException("something else"),
    ValueError("完全不认识的异常"),
])
def test_sender_side_smtp_errors(exc):
    """Wrong auth code, can't connect, timeout, unrecognized -- all sender side, no fallback."""
    assert _smtp_failure(exc).failure_side == FAILURE_SIDE_SENDER, type(exc).__name__


def test_recipients_refused_is_recipient_side():
    """Only a rejected RCPT TO means "this address doesn't work", where trying the next mailbox makes sense."""
    exc = smtplib.SMTPRecipientsRefused({"nobody@example.com": (550, b"no such user")})
    assert _smtp_failure(exc).failure_side == FAILURE_SIDE_RECIPIENT


def test_failure_messages_never_carry_server_text():
    """Messages must be literals: with raw server text, the exit word list would squash the whole sentence into "please retry later"."""
    leak = "SUPER-SECRET-TOKEN-abc123"
    for exc in (
        smtplib.SMTPAuthenticationError(535, leak.encode()),
        smtplib.SMTPRecipientsRefused({leak: (550, leak.encode())}),
        smtplib.SMTPConnectError(421, leak.encode()),
    ):
        assert leak not in str(_smtp_failure(exc)), type(exc).__name__


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.mark.parametrize("status,body,expected", [
    (422, {"message": "Invalid `to` field. Please use the correct email address."}, True),
    (400, {"message": "recipient address is not valid"}, True),
    (422, {"message": "The from address is not verified."}, False),
    (401, {"message": "Invalid `to` field"}, False),   # 401 is always a Key problem; check the status code first
    (403, {"message": "recipient"}, False),
    (500, {"message": "recipient"}, False),
    (422, {"message": ""}, False),
    (422, "not a dict", False),
    (422, ValueError("响应体不是 JSON"), False),
])
def test_resend_recipient_detection(status, body, expected):
    assert _resend_body_blames_recipient(_FakeResponse(status, body)) is expected


class _FakeDelivery:
    def __init__(self, metadata_json):
        self.metadata_json = metadata_json


@pytest.mark.parametrize("metadata,expected", [
    ('{"failure_side": "recipient"}', FAILURE_SIDE_RECIPIENT),
    ('{"failure_side": "sender"}', FAILURE_SIDE_SENDER),
    ('{"failure_side": "typo"}', FAILURE_SIDE_SENDER),
    ('{}', FAILURE_SIDE_SENDER),
    ('不是 JSON', FAILURE_SIDE_SENDER),
])
def test_delivery_failure_side_readback(metadata, expected):
    assert delivery_failure_side(_FakeDelivery(metadata)) == expected


def test_delivery_failure_side_of_none_is_sender():
    """Even less reason to fall back when there's no delivery record at all."""
    assert delivery_failure_side(None) == FAILURE_SIDE_SENDER


# ---------------------------------------------------------------- fallback candidates


def _db_with_contacts(contacts):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(SalesAgentCompanyORM(id=1, name="星迈科技", source="manual", data_status="active"))
    db.add(SalesAgentLeadRecommendationORM(id=1, user_id=1, company_id=1))
    for kwargs in contacts:
        db.add(SalesAgentCompanyContactORM(company_id=1, **kwargs))
    db.commit()
    return db, db.get(SalesAgentLeadRecommendationORM, 1)


def test_candidates_prefer_reachable_and_cap_at_limit():
    db, lead = _db_with_contacts([
        {"id": 1, "email": "cold@example.com", "consent_status": "unknown", "is_reachable": False},
        {"id": 2, "email": "warm@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 3, "email": "third@example.com", "consent_status": "unknown", "is_reachable": False},
    ])
    try:
        got = _delivery_recipient_candidates(db, lead, limit=2)
        assert [email for _, email, _ in got] == ["warm@example.com", "third@example.com"]
    finally:
        db.close()


def test_candidates_skip_blocked_and_invalid():
    db, lead = _db_with_contacts([
        {"id": 1, "email": "no@example.com", "consent_status": "unsubscribed", "is_reachable": True},
        {"id": 2, "email": "不是邮箱", "consent_status": "unknown", "is_reachable": True},
        {"id": 3, "email": "ok@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    try:
        assert [e for _, e, _ in _delivery_recipient_candidates(db, lead)] == ["ok@example.com"]
    finally:
        db.close()


def test_candidates_dedupe_same_address():
    """The same info@ under two contacts is common -- fallback must not hit the same address again."""
    db, lead = _db_with_contacts([
        {"id": 1, "email": "info@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 2, "email": "INFO@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 3, "email": "other@example.com", "consent_status": "unknown", "is_reachable": False},
    ])
    try:
        # Ordered by is_reachable DESC, id DESC, so id=2 comes first; dedup compares lowercase, and for
        # the same address in different case only the first one's original text is kept.
        assert [e for _, e, _ in _delivery_recipient_candidates(db, lead)] == ["INFO@example.com", "other@example.com"]
    finally:
        db.close()


def test_preferred_contact_goes_first_but_still_filtered():
    db, lead = _db_with_contacts([
        {"id": 1, "email": "picked@example.com", "consent_status": "unknown", "is_reachable": False},
        {"id": 2, "email": "warm@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    try:
        got = _delivery_recipient_candidates(db, lead, preferred_contact_id=1)
        assert [e for _, e, _ in got] == ["picked@example.com", "warm@example.com"]
        # A specified contact that is blocked must not be let through just because "the user specified it"
        db.get(SalesAgentCompanyContactORM, 1).consent_status = "blacklisted"
        db.commit()
        got = _delivery_recipient_candidates(db, lead, preferred_contact_id=1)
        assert [e for _, e, _ in got] == ["warm@example.com"]
    finally:
        db.close()


def test_first_candidate_matches_the_manual_send_path():
    """The fallback's first choice must be the same one manual send picks -- if the two ordering rules diverge you get
    the inexplicable "panel sends to A, schedule sends to B"."""
    db, lead = _db_with_contacts([
        {"id": 1, "email": "cold@example.com", "consent_status": "unknown", "is_reachable": False},
        {"id": 2, "email": "warm@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    try:
        _, manual_email, _ = _resolve_delivery_recipient(db, lead)
        assert _delivery_recipient_candidates(db, lead)[0][1] == manual_email
    finally:
        db.close()


def test_no_usable_contact_returns_empty():
    db, lead = _db_with_contacts([
        {"id": 1, "email": "no@example.com", "consent_status": "do_not_contact", "is_reachable": True},
    ])
    try:
        assert _delivery_recipient_candidates(db, lead) == []
        assert _delivery_recipient_candidates(db, lead, limit=0) == []
    finally:
        db.close()
