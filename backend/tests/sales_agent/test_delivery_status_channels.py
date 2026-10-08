"""Cross-channel send stats -- the metric definition after merging email + SMS.

Split into its own file instead of `test_delivery_status.py`: that file's fixture deliberately builds only
the email delivery table (verifying "missing SMS table must not crash"), while this needs full-table sqlite; mixing the two fixtures
in one file would make it unclear which test verifies which case.

The one thing to pin: once an SMS is sent, asking in chat "which companies did I send to today" must show it, labeled with its channel.
Before the merge `agent_delivery_status` only queried the email table, and the model would confidently say "no records sent to X".

Run:
    venv/bin/python -m pytest tests/sales_agent/test_delivery_status_channels.py -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.agent import route_intent
from agents.sales_agent.api import _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.methods import append_chat_message
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentSmsDeliveryORM,
)
from agents.sales_agent.service import agent_delivery_status, sales_agent_delivery_stats
from geo.database import Base
from geo.models.user import UserORM

USER = 1


@pytest.fixture()
def db():
    """Companies: the email company only got email, the SMS company only got SMS, the dual-channel company got both."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=USER, email="both@test.local", password_hash="x", is_active=True))
    mail_only = SalesAgentCompanyORM(name="只发邮件科技")
    sms_only = SalesAgentCompanyORM(name="只发短信科技")
    both = SalesAgentCompanyORM(name="两样都发科技")
    s.add_all([mail_only, sms_only, both])
    s.flush()
    now = datetime.utcnow()
    s.add_all([
        SalesAgentEmailDeliveryORM(user_id=USER, company_id=mail_only.id, recipient_email="a@mail.example",
                                   subject="AI 搜索可见度", status="sent", sent_at=now, created_at=now,
                                   first_opened_at=now, open_count=2),
        SalesAgentEmailDeliveryORM(user_id=USER, company_id=both.id, recipient_email="b@both.example",
                                   subject="竞品对比", status="failed", failure_reason="SMTP 550", created_at=now),
    ])
    s.add_all([
        SalesAgentSmsDeliveryORM(user_id=USER, company_id=sms_only.id, recipient_phone="+8613800138000",
                                 provider="tencent_cloud", status="delivered", sent_at=now, delivered_at=now,
                                 created_at=now, segments=1, click_count=3, first_click_at=now),
        SalesAgentSmsDeliveryORM(user_id=USER, company_id=both.id, recipient_phone="+8613900139000",
                                 provider="twilio", status="sent", sent_at=now, created_at=now, segments=2),
    ])
    s.commit()
    s.mail_only, s.sms_only, s.both = mail_only, sms_only, both
    try:
        yield s
    finally:
        s.close()


def _channels(out) -> list[str]:
    return [row["channel"] for row in out["deliveries"]]


# --- Merge ----------------------------------------------------------------------

def test_default_query_returns_both_channels(db):
    """Core regression: sent SMS must be queryable, not just the two email rows."""
    out = agent_delivery_status(db, USER)
    assert out["total_matched"] == 4
    assert set(_channels(out)) == {"email", "sms"}
    assert out["channels"] == ["email", "sms"]


def test_every_row_is_tagged_with_its_channel(db):
    """Without a channel tag, the model reports SMS as email."""
    out = agent_delivery_status(db, USER)
    assert all(row.get("channel") in ("email", "sms") for row in out["deliveries"])


def test_sms_only_company_is_findable_by_name(db):
    out = agent_delivery_status(db, USER, company_name="只发短信")
    assert out["total_matched"] == 1
    assert _channels(out) == ["sms"]


def test_channel_filter_narrows_to_one_side(db):
    email_only = agent_delivery_status(db, USER, channel="email")
    sms_only = agent_delivery_status(db, USER, channel="sms")
    assert email_only["total_matched"] == 2 and email_only["channels"] == ["email"]
    assert sms_only["total_matched"] == 2 and sms_only["channels"] == ["sms"]
    assert set(_channels(email_only)) == {"email"}
    assert set(_channels(sms_only)) == {"sms"}


def test_unknown_channel_falls_back_to_both_not_empty(db):
    """A misspelled channel name must not silently return empty -- that would look like "nothing was ever sent"."""
    out = agent_delivery_status(db, USER, channel="carrier-pigeon")
    assert out["total_matched"] == 4
    assert out["filters"]["channel"] == ""


def test_status_filter_applies_across_channels(db):
    out = agent_delivery_status(db, USER, status="sent")
    assert {row["channel"] for row in out["deliveries"]} == {"email", "sms"}


# --- summary -------------------------------------------------------------------

def test_summary_sums_shared_keys_across_channels(db):
    s = agent_delivery_status(db, USER)["summary"]
    assert s["attempted"] == 4
    assert s["sent"] == 3   # 1 email + 2 SMS (delivered also counts as sent)
    assert s["failed"] == 1


def test_summary_keeps_channel_private_metrics_separate(db):
    """Open (email) and click (SMS) mean different things; don't merge them into one "engagement count" -- the sum is meaningless."""
    s = agent_delivery_status(db, USER)["summary"]
    assert s["open_count"] == 2 and s["opened"] == 1
    assert s["click_count"] == 3 and s["clicked"] == 1
    assert s["segments"] == 3
    assert "opened" not in s["by_channel"]["sms"]
    assert "clicked" not in s["by_channel"]["email"]


def test_summary_by_channel_gives_the_split_for_the_reply(db):
    by_channel = agent_delivery_status(db, USER)["summary"]["by_channel"]
    assert by_channel["email"]["attempted"] == 2
    assert by_channel["sms"]["attempted"] == 2


def test_note_tells_the_model_to_state_the_channel_split(db):
    note = agent_delivery_status(db, USER)["note"]
    assert "真实发出" in note and "渠道分布" in note


# --- Ordering and truncation ----------------------------------------------------

def test_merged_rows_are_sorted_newest_first(db):
    """Each channel is ordered by id desc; without re-sorting after merge you get a false "all SMS after email" order."""
    old = datetime.utcnow() - timedelta(days=5)
    db.add(SalesAgentSmsDeliveryORM(user_id=USER, company_id=db.sms_only.id, recipient_phone="+8613700137000",
                                    provider="twilio", status="sent", sent_at=old, created_at=old))
    db.commit()
    sent_at = [row["sent_at"] for row in agent_delivery_status(db, USER)["deliveries"] if row.get("sent_at")]
    assert sent_at == sorted(sent_at, reverse=True)


def test_limit_caps_the_merged_list_not_each_channel(db):
    out = agent_delivery_status(db, USER, limit=3)
    assert out["returned"] == 3
    assert out["total_matched"] == 4  # details are truncated, the total is reported as-is


# --- Panel stats ----------------------------------------------------------------

def test_panel_stats_merge_totals_and_keep_the_split(db):
    out = sales_agent_delivery_stats(db, USER)
    assert out["total"] == 4
    assert out["by_channel"]["email"]["total"] == 2
    assert out["by_channel"]["sms"]["total"] == 2


def test_panel_stats_channel_filter(db):
    out = sales_agent_delivery_stats(db, USER, channel="sms")
    assert out["total"] == 2 and set(out["by_channel"]) == {"sms"}


# --- Chat metric definition ------------------------------------------------------

def _ask(s, session, deps, message: str) -> str:
    append_chat_message(deps, "user", message, intent=route_intent(message))
    assert asyncio.run(_run_deterministic_flow(deps, message, route_intent(message))) is True
    row = (
        s.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == session.id, SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.desc())
        .first()
    )
    assert row is not None
    return row.content


def test_chat_reply_states_the_channel_split_when_both_were_used(db):
    """When both channels were used, reporting only one total makes the user think SMS was never sent."""
    session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000101")
    db.add(session)
    db.commit()
    deps = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id,
                          session_public_id=session.public_id)

    reply = _ask(db, session, deps, "今天发了多少")

    assert "4" in reply
    assert "邮件" in reply and "短信" in reply
    assert "处理未完成" not in reply


def test_chat_company_detail_lists_both_channels(db):
    session = SalesAgentChatSessionORM(user_id=USER, public_id="00000000-0000-4000-8000-000000000102")
    db.add(session)
    db.commit()
    deps = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id,
                          session_public_id=session.public_id)

    _ask(db, session, deps, "今天发了多少")
    reply = _ask(db, session, deps, "哪些企业呢")

    assert "只发短信科技" in reply  # before the merge this company would not appear at all
    assert "只发邮件科技" in reply
    assert "(短信)" in reply and "(邮件)" in reply
