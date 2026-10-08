"""Suppression list + automatic listing on hard bounce (scheduled send A2, design §3.3 / §5.1).

This guards two lines:

1. **Listing must be precise** -- only "recipient side + permanent (5xx)" gets blocked. 4xx means "mailbox full" or "remote server
   busy", which clears up in a couple of days; a wrong block cuts off a valid customer for the user;
2. **Checks must be complete** -- manual send, scheduled send, and fallback candidates all must pass this table. Miss any one,
   and an unsubscribed address can be emailed again, which is the entire reason this table exists.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_email_suppression.py -q
"""
from __future__ import annotations

import json
import smtplib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentEmailSuppressionORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
)
from agents.sales_agent.service.common import (
    FAILURE_SIDE_RECIPIENT,
    FAILURE_SIDE_SENDER,
    SalesAgentError,
)
from agents.sales_agent.service.email_send import (
    _delivery_recipient_candidates,
    _mark_delivery_failed,
    _smtp_failure,
    _smtp_refusal_is_permanent,
    delivery_failure_is_permanent,
)
from agents.sales_agent.service.suppression import (
    REMOVABLE_SUPPRESSION_REASONS,
    SUPPRESSION_REASON_BOUNCED_HARD,
    SUPPRESSION_REASON_MANUAL,
    SUPPRESSION_REASON_UNSUBSCRIBED,
    add_email_suppression,
    is_email_suppressed,
    list_email_suppressions,
    normalize_suppression_email,
    remove_email_suppression,
    serialize_email_suppression,
    suppressed_emails,
    suppression_entry,
    suppression_skip_reason,
)
from geo.database import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------- permanent / temporary


@pytest.mark.parametrize("recipients,expected", [
    ({"a@example.com": (550, b"no such user")}, True),
    ({"a@example.com": (551, b"user not local")}, True),
    ({"a@example.com": (452, b"mailbox full")}, False),          # mailbox full, clears up in a couple of days
    ({"a@example.com": (421, b"try again later")}, False),
    ({"a@example.com": (550, b"x"), "b@example.com": (450, b"y")}, False),  # if any one is temporary, treat the whole as temporary
    ({"a@example.com": (550, b"x"), "b@example.com": (553, b"y")}, True),
    ({}, False),                                                  # no codes at all = undeterminable
    ({"a@example.com": ("五百五", b"x")}, False),                  # non-numeric code = undeterminable
    ({"a@example.com": None}, False),
    ({"a@example.com": ()}, False),
])
def test_refusal_permanence_reads_only_the_status_code(recipients, expected):
    exc = smtplib.SMTPRecipientsRefused(recipients)
    assert _smtp_refusal_is_permanent(exc) is expected


def test_unsure_means_temporary():
    """Must not block when the `recipients` attribute is missing entirely."""
    assert _smtp_refusal_is_permanent(smtplib.SMTPException("没有 recipients")) is False


def test_hard_refusal_is_recipient_side_and_permanent():
    err = _smtp_failure(smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no such user")}))
    assert err.failure_side == FAILURE_SIDE_RECIPIENT
    assert err.permanent is True


def test_soft_refusal_is_recipient_side_but_not_permanent():
    """Fall back (trying another mailbox makes sense), but don't block (the address itself is fine)."""
    err = _smtp_failure(smtplib.SMTPRecipientsRefused({"a@example.com": (452, b"mailbox full")}))
    assert err.failure_side == FAILURE_SIDE_RECIPIENT
    assert err.permanent is False


def test_sender_side_errors_are_never_permanent():
    """A wrong auth code is a sender-side problem unrelated to recipient addresses -- none may enter the list."""
    for exc in (
        smtplib.SMTPAuthenticationError(535, b"auth failed"),
        smtplib.SMTPSenderRefused(553, b"sender rejected", "me@example.com"),
        smtplib.SMTPConnectError(421, b"cannot connect"),
        ValueError("认不出来"),
    ):
        assert _smtp_failure(exc).permanent is False, type(exc).__name__


def test_default_is_not_permanent():
    assert SalesAgentError("随便什么错").permanent is False


# ------------------------------------------------------- failure persisted to DB -> auto-listed


def _delivery(db, email="target@example.com"):
    row = SalesAgentEmailDeliveryORM(
        user_id=1, lead_id=1, company_id=1, recipient_email=email,
        status="pending", provider_type="smtp",
    )
    db.add(row)
    db.flush()
    return row


def test_hard_bounce_lands_in_the_suppression_list(db):
    row = _delivery(db)
    _mark_delivery_failed(db, row, 1, "地址不存在", FAILURE_SIDE_RECIPIENT, permanent=True)
    assert row.status == "failed"
    assert delivery_failure_is_permanent(row) is True
    assert is_email_suppressed(db, 1, "target@example.com") is True
    entry = suppression_entry(db, 1, "target@example.com")
    assert entry.reason == SUPPRESSION_REASON_BOUNCED_HARD
    assert entry.source == f"hard_bounce:delivery:{row.id}"


def test_soft_bounce_does_not_blacklist(db):
    row = _delivery(db)
    _mark_delivery_failed(db, row, 1, "对方邮箱已满", FAILURE_SIDE_RECIPIENT, permanent=False)
    assert delivery_failure_is_permanent(row) is False
    assert is_email_suppressed(db, 1, "target@example.com") is False


def test_sender_side_permanent_is_downgraded(db):
    """Sender side marked permanent doesn't count either -- a mailbox problem shouldn't be blamed on the recipient address."""
    row = _delivery(db)
    _mark_delivery_failed(db, row, 1, "授权码错误", FAILURE_SIDE_SENDER, permanent=True)
    assert json.loads(row.metadata_json)["failure_permanent"] is False
    assert delivery_failure_is_permanent(row) is False
    assert is_email_suppressed(db, 1, "target@example.com") is False


def test_suppression_list_is_per_user(db):
    row = _delivery(db)
    _mark_delivery_failed(db, row, 1, "地址不存在", FAILURE_SIDE_RECIPIENT, permanent=True)
    assert is_email_suppressed(db, 2, "target@example.com") is False


def test_failure_record_survives_a_broken_suppression_write(db, monkeypatch):
    """If writing the suppression list fails, this email's failed state must still be persisted to DB -- otherwise a side-write failure
    swallows the fact that "this email failed" too, and the user sees an email stuck in pending forever."""
    import agents.sales_agent.service.email_send as mod

    def _boom(*a, **kw):
        raise RuntimeError("抑制表炸了")

    monkeypatch.setattr(mod, "add_email_suppression", _boom)
    row = _delivery(db)
    _mark_delivery_failed(db, row, 1, "地址不存在", FAILURE_SIDE_RECIPIENT, permanent=True)
    assert row.status == "failed"
    assert row.failure_reason == "地址不存在"


@pytest.mark.parametrize("metadata,expected", [
    ('{"failure_side": "recipient", "failure_permanent": true}', True),
    ('{"failure_side": "recipient", "failure_permanent": false}', False),
    ('{"failure_side": "sender", "failure_permanent": true}', False),   # wrong side doesn't count
    ('{"failure_permanent": true}', False),                             # no side given = sender side
    ('{}', False),
    ('不是 JSON', False),
])
def test_permanent_readback(metadata, expected):
    row = SalesAgentEmailDeliveryORM(metadata_json=metadata)
    assert delivery_failure_is_permanent(row) is expected


def test_permanent_readback_of_none_is_false():
    assert delivery_failure_is_permanent(None) is False


# ---------------------------------------------------------------- list CRUD


def test_normalize_lowercases_and_trims():
    assert normalize_suppression_email("  Info@Example.COM \n") == "info@example.com"
    assert normalize_suppression_email(None) == ""
    assert len(normalize_suppression_email("a" * 300 + "@example.com")) == 255


def test_lookup_is_case_insensitive(db):
    add_email_suppression(db, 1, "Info@Example.com")
    assert is_email_suppressed(db, 1, "INFO@example.COM") is True
    assert is_email_suppressed(db, 1, " info@example.com ") is True


def test_add_is_idempotent_and_keeps_the_first_reason(db):
    """The reason of the first block is the real one; overwrite it and you can never tell whether they unsubscribed or the address doesn't exist."""
    first = add_email_suppression(db, 1, "a@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    again = add_email_suppression(db, 1, "A@example.com", reason=SUPPRESSION_REASON_BOUNCED_HARD)
    assert again.id == first.id
    assert again.reason == SUPPRESSION_REASON_UNSUBSCRIBED
    assert db.query(SalesAgentEmailSuppressionORM).count() == 1


def test_unknown_reason_falls_back_to_manual(db):
    assert add_email_suppression(db, 1, "a@example.com", reason="随便写的").reason == SUPPRESSION_REASON_MANUAL


def test_add_rejects_a_non_email(db):
    for bad in ("", "不是邮箱", "a@", "@example.com"):
        with pytest.raises(SalesAgentError):
            add_email_suppression(db, 1, bad)


def test_only_manual_entries_can_be_removed(db):
    add_email_suppression(db, 1, "manual@example.com", reason=SUPPRESSION_REASON_MANUAL)
    add_email_suppression(db, 1, "bounced@example.com", reason=SUPPRESSION_REASON_BOUNCED_HARD)
    assert remove_email_suppression(db, 1, "MANUAL@example.com") is True
    assert is_email_suppressed(db, 1, "manual@example.com") is False
    # Unsubscribes / hard bounces must not be restorable in one click -- that's the opposite of why this table exists.
    with pytest.raises(SalesAgentError):
        remove_email_suppression(db, 1, "bounced@example.com")
    assert is_email_suppressed(db, 1, "bounced@example.com") is True
    assert REMOVABLE_SUPPRESSION_REASONS == (SUPPRESSION_REASON_MANUAL,)


def test_remove_a_missing_address_is_false_not_an_error(db):
    assert remove_email_suppression(db, 1, "nobody@example.com") is False


def test_batch_lookup_returns_only_the_hits(db):
    add_email_suppression(db, 1, "a@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    add_email_suppression(db, 1, "b@example.com", reason=SUPPRESSION_REASON_BOUNCED_HARD)
    got = suppressed_emails(db, 1, ["A@example.com", "c@example.com", "", "b@example.com"])
    assert got == {
        "a@example.com": SUPPRESSION_REASON_UNSUBSCRIBED,
        "b@example.com": SUPPRESSION_REASON_BOUNCED_HARD,
    }
    assert suppressed_emails(db, 1, []) == {}
    assert suppressed_emails(db, 2, ["a@example.com"]) == {}


def test_skip_reason_is_always_specific():
    """Sales Agent never emits generic errors -- an unrecognized reason must still get a readable sentence."""
    for reason in ("unsubscribed", "bounced_hard", "complained", "manual", "没见过的原因", ""):
        text = suppression_skip_reason(reason)
        assert text and "失败" not in text and "不支持" not in text


def test_list_and_serialize(db):
    add_email_suppression(db, 1, "a@example.com", reason=SUPPRESSION_REASON_MANUAL, source="人工")
    add_email_suppression(db, 1, "b@example.com", reason=SUPPRESSION_REASON_BOUNCED_HARD)
    add_email_suppression(db, 2, "other@example.com")
    page = list_email_suppressions(db, 1)
    assert page["total"] == 2
    assert [s["email"] for s in page["suppressions"]] == ["b@example.com", "a@example.com"]
    manual = next(s for s in page["suppressions"] if s["email"] == "a@example.com")
    assert manual["removable"] is True and manual["source"] == "人工"
    assert next(s for s in page["suppressions"] if s["email"] == "b@example.com")["removable"] is False
    assert all(s["reason_text"] for s in page["suppressions"])
    assert list_email_suppressions(db, 1, limit=1, offset=1)["suppressions"][0]["email"] == "a@example.com"
    assert serialize_email_suppression(None) is None


def test_missing_table_degrades_to_empty_instead_of_crashing():
    """In environments where the migration hasn't run, the whole send path must not crash (see the degrade direction in suppression.py comments)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentCompanyORM.__table__])
    db = sessionmaker(bind=engine)()
    try:
        assert is_email_suppressed(db, 1, "a@example.com") is False
        assert suppressed_emails(db, 1, ["a@example.com"]) == {}
        assert add_email_suppression(db, 1, "a@example.com") is None
        assert list_email_suppressions(db, 1)["total"] == 0
    finally:
        db.close()


# ------------------------------------------------------------ fallback candidates pass the gate


def _company_with_contacts(db, contacts):
    db.add(SalesAgentCompanyORM(id=1, name="星迈科技", source="manual", data_status="active"))
    db.add(SalesAgentLeadRecommendationORM(id=1, user_id=1, company_id=1))
    for kwargs in contacts:
        db.add(SalesAgentCompanyContactORM(company_id=1, **kwargs))
    db.commit()
    return db.get(SalesAgentLeadRecommendationORM, 1)


def test_candidates_skip_suppressed_addresses(db):
    """First choice bounces -> fall back to the second, which happens to be an unsubscribed address: exactly the scenario to block here."""
    lead = _company_with_contacts(db, [
        {"id": 1, "email": "first@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 2, "email": "SECOND@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    add_email_suppression(db, 1, "second@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    got = [e for _, e, _ in _delivery_recipient_candidates(db, lead, user_id=1)]
    assert got == ["first@example.com"]


def test_candidates_without_user_id_keep_the_old_behaviour(db):
    """Callers that don't pass user_id (the manual send path) behave exactly as before."""
    lead = _company_with_contacts(db, [
        {"id": 1, "email": "a@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    add_email_suppression(db, 1, "a@example.com")
    assert [e for _, e, _ in _delivery_recipient_candidates(db, lead)] == ["a@example.com"]
    assert _delivery_recipient_candidates(db, lead, user_id=1) == []


def test_suppressed_candidate_does_not_eat_a_slot(db):
    """A skipped one must not consume a limit slot, or fallback chances get silently eaten."""
    lead = _company_with_contacts(db, [
        {"id": 1, "email": "a@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 2, "email": "b@example.com", "consent_status": "unknown", "is_reachable": True},
        {"id": 3, "email": "c@example.com", "consent_status": "unknown", "is_reachable": True},
    ])
    add_email_suppression(db, 1, "c@example.com")
    got = [e for _, e, _ in _delivery_recipient_candidates(db, lead, limit=2, user_id=1)]
    assert got == ["b@example.com", "a@example.com"]


# ------------------------------------------------------------ gate right before sending


def _send_fixture(db, monkeypatch, email="target@example.com"):
    """Switch the send path to the state "config works, but no usable sender mailbox".

    The suppression check runs before `account is None`, so on a hit `failure_reason` is the list's wording,
    not "no usable sender mailbox configured" -- the difference between the two proves which gate blocked it, without
    stubbing real SMTP.
    """
    import agents.sales_agent.service.email_send as mod

    monkeypatch.setattr(mod, "_effective_email_config", lambda *a, **kw: {
        "sending_enabled": True, "account": None, "account_source": "test",
        "sender_selection_policy": "default_only", "tracking_enabled": False,
        "disabled_reasons": [],
    })
    monkeypatch.setattr(mod, "_report_share_for_draft", lambda *a, **kw: None)
    company = SalesAgentCompanyORM(id=1, name="星迈科技", source="manual", data_status="active")
    lead = SalesAgentLeadRecommendationORM(id=1, user_id=1, company_id=1)
    draft = SalesAgentOutreachLogORM(id=1, lead_id=1, channel="email", subject="你好", content="正文")
    db.add_all([company, lead, draft, SalesAgentCompanyContactORM(
        id=1, company_id=1, email=email, consent_status="unknown", is_reachable=True,
    )])
    db.commit()
    return draft, lead, company


def test_send_is_blocked_at_the_last_moment(db, monkeypatch):
    """Hours pass between planning and actually sending, and someone may unsubscribe meanwhile -- so check again right before each send."""
    from agents.sales_agent.service.email_send import send_sales_agent_email_delivery

    draft, lead, company = _send_fixture(db, monkeypatch)
    add_email_suppression(db, 1, "target@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    delivery = send_sales_agent_email_delivery(db, 1, draft, lead, company)
    # Reuse disabled instead of adding a new status value (rationale in the comments in email_send.py);
    # the sentence the user actually sees is in failure_reason, fully intact.
    assert delivery.status == "disabled"
    assert delivery.failure_reason == suppression_skip_reason(SUPPRESSION_REASON_UNSUBSCRIBED)
    assert "退订" in delivery.failure_reason


def test_send_proceeds_when_not_suppressed(db, monkeypatch):
    """No hit should continue onward -- reaching the "no sender mailbox" step here shows the gate didn't misfire."""
    from agents.sales_agent.service.email_send import send_sales_agent_email_delivery

    draft, lead, company = _send_fixture(db, monkeypatch)
    delivery = send_sales_agent_email_delivery(db, 1, draft, lead, company)
    assert delivery.failure_reason == "未配置可用发件箱"


def test_send_gate_is_case_insensitive(db, monkeypatch):
    """The list stores lowercase, while the contacts table may have Target@ -- a case mismatch would make the gate useless."""
    from agents.sales_agent.service.email_send import send_sales_agent_email_delivery

    draft, lead, company = _send_fixture(db, monkeypatch, email="Target@Example.com")
    add_email_suppression(db, 1, "target@example.com", reason=SUPPRESSION_REASON_BOUNCED_HARD)
    assert send_sales_agent_email_delivery(db, 1, draft, lead, company).failure_reason == (
        suppression_skip_reason(SUPPRESSION_REASON_BOUNCED_HARD)
    )
