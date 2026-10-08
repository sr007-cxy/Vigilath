"""The receipt written into the session when a schedule finishes (A7, design §4.6).

The drawer only shows schedule-state numbers; **execution results always go through the conversation** -- so this message is the only place users can see
"scheduled 12, actually sent 11, which one is missing and why". Phase one has no push (§4.6
"polling, not push"); users only see it next time they come in, which makes it all the more necessary that it carries a complete conclusion.

Guards four things:

1. When scheduled and actually sent counts differ, **name each missing company and why**;
2. Every skipped item carries a reason; never a dangling "1 company skipped";
3. **Broken sender mailbox is reported separately** -- that's for the user to fix their own config, entirely different from "recipient rejected";
4. A failed receipt write **must not swallow the send result**: the mail is already out, the receipt just went unseen.

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_send_schedule_receipt.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyORM,
    SalesAgentEmailAccountORM,
    SalesAgentSendScheduleItemORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.service.send_schedule import (
    agent_send_schedule_status,
    build_schedule_receipt,
    write_schedule_receipt,
)
from geo.database import Base

USER = 1


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    yield maker
    engine.dispose()


@pytest.fixture()
def db(factory):
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _session_row(db) -> SalesAgentChatSessionORM:
    row = SalesAgentChatSessionORM(
        id=7, user_id=USER, public_id="sess-7", title="定时发送",
        updated_at=datetime.utcnow() - timedelta(days=3),
    )
    db.add(row)
    db.commit()
    return row


def _schedule(db, *, status: str = "running", session_id: int | None = 7, account_id: int | None = None):
    row = SalesAgentSendScheduleORM(
        id=1, user_id=USER, channel="email", status=status,
        scheduled_at=datetime(2026, 9, 20, 7, 0),   # 15:00 Beijing time
        created_by_session_id=session_id, email_account_id=account_id,
    )
    db.add(row)
    db.commit()
    return row


def _item(db, item_id: int, company: str, status: str, *, reason: str = "", attempts: list | None = None):
    db.add(SalesAgentCompanyORM(id=item_id, name=company, source="manual", data_status="active"))
    db.add(SalesAgentSendScheduleItemORM(
        id=item_id, schedule_id=1, company_id=item_id, status=status, skip_reason=reason,
        attempted_json=json.dumps(attempts or [], ensure_ascii=False),
    ))
    db.commit()


def test_receipt_names_every_company_that_did_not_get_the_email(db):
    """"Scheduled 3, actually sent 1": the two missing ones must be named + given reasons.

    One email missing without the user knowing which is worse than never scheduling -- they'll plan follow-ups
    as if "sent to 3".
    """
    _session_row(db)
    schedule = _schedule(db, status="done")
    _item(db, 1, "甲科技", "sent")
    _item(db, 2, "乙智能", "skipped", reason="这家在排期后退订了")
    _item(db, 3, "丙网络", "failed", reason="对方邮箱不存在(试过:no@x.com)")

    text = build_schedule_receipt(db, schedule)
    assert "已完成" in text and "发出 1 封" in text
    assert "乙智能:这家在排期后退订了" in text
    assert "丙网络:对方邮箱不存在" in text
    # A dangling "1 company skipped" appearing on its own is the failing wording
    assert "没发出去的:" in text


def test_sender_outage_is_its_own_message(db):
    """A broken sender mailbox must point to config and give a next step, not just "3 companies failed".

    Sender-side faults and recipient rejections call for entirely different actions: one is fixing your own auth code, the other is
    no point retrying this company. Mixed into one sentence, the user just blames those three companies' mailboxes.
    """
    _session_row(db)
    db.add(SalesAgentEmailAccountORM(
        id=5, scope="user", user_id=USER, provider_type="smtp", enabled=True,
        is_default=True, from_email="me@example.com", smtp_host="smtp.example.com",
    ))
    db.commit()
    schedule = _schedule(db, status="done", account_id=5)
    for i, name in enumerate(["甲科技", "乙智能", "丙网络"], start=1):
        _item(db, i, name, "failed", reason="发件箱认证失败",
              attempts=[{"email": f"a{i}@x.com", "result": "failed", "side": "sender", "reason": "发件箱认证失败"}])

    text = build_schedule_receipt(db, schedule)
    assert "me@example.com" in text and "3 家都没发" in text
    assert "授权码" in text
    # Under this wording, don't list companies one by one -- the problem isn't with them
    assert "甲科技" not in text


def test_one_success_means_the_sender_is_fine(db):
    """If even one email succeeded, the problem isn't the mailbox; use the general wording and go company by company."""
    _session_row(db)
    schedule = _schedule(db, status="done", account_id=None)
    _item(db, 1, "甲科技", "sent")
    _item(db, 2, "乙智能", "failed", reason="连接发件服务器超时",
          attempts=[{"email": "b@x.com", "result": "failed", "side": "sender", "reason": "连接发件服务器超时"}])

    text = build_schedule_receipt(db, schedule)
    assert "发件箱" not in text.split("没发出去的:")[0]
    assert "乙智能:连接发件服务器超时" in text


def test_cancelled_rest_is_counted_and_explained(db):
    """Cancelled mid-send: the remaining ones not reached must clearly say "none sent", not just leave a number."""
    _session_row(db)
    schedule = _schedule(db, status="cancelled")
    _item(db, 1, "甲科技", "sent")
    _item(db, 2, "乙智能", "cancelled")
    _item(db, 3, "丙网络", "cancelled")

    text = build_schedule_receipt(db, schedule)
    assert "已取消" in text and "2 家没有发" in text and "一封都没发" in text


def test_receipt_lands_in_the_session_that_created_the_plan(factory, db):
    _session_row(db)
    _schedule(db, status="done")
    _item(db, 1, "甲科技", "sent")

    text = write_schedule_receipt(factory, 1)
    assert text
    row = db.query(SalesAgentChatMessageORM).one()
    assert row.session_id == 7 and row.role == "assistant" and row.content == text
    assert json.loads(row.metadata_json)["schedule_id"] == 1
    # The session list is ordered by updated_at; if not bumped to the top, this receipt sits in the seventh session, as good as unwritten
    session = db.get(SalesAgentChatSessionORM, 7)
    assert (datetime.utcnow() - session.updated_at) < timedelta(minutes=5)


def test_status_tool_gives_the_model_reasons_not_just_counts(db):
    """If the model only has "1 skipped", it can only parrot "1 company skipped" -- the forbidden wording."""
    schedule = _schedule(db, status="done")
    _item(db, 1, "甲科技", "sent")
    _item(db, 2, "乙智能", "skipped", reason="这家在排期后退订了")

    row = agent_send_schedule_status(db, USER, schedule.id)["schedules"][0]
    assert row["已发"] == 1 and row["跳过"] == 1
    assert row["没发出去的"] == [{"企业": "乙智能", "原因": "这家在排期后退订了"}]


def test_status_tool_answers_how_much_longer(db):
    """"Sending" isn't an answer -- the user is asking how much longer to wait."""
    schedule = _schedule(db, status="running")
    schedule.throttle_seconds = 30
    db.commit()
    _item(db, 1, "甲科技", "sent")
    for i, name in enumerate(["乙", "丙", "丁", "戊"], start=2):
        _item(db, i, name, "pending")

    row = agent_send_schedule_status(db, USER, schedule.id)["schedules"][0]
    assert "预计还要" in row and "剩下的 4 家" in row["预计还要"]


def test_status_tool_has_nothing_to_estimate_when_the_batch_is_done(db):
    """Once finished, don't report "estimated remaining" -- that makes the user think mail is still unsent."""
    schedule = _schedule(db, status="done")
    _item(db, 1, "甲科技", "sent")
    assert "预计还要" not in agent_send_schedule_status(db, USER, schedule.id)["schedules"][0]


def test_no_session_means_no_message_and_no_crash(factory, db):
    """Schedules can be created directly from the panel (without a session). Then there's nowhere to write, but it must never crash."""
    _schedule(db, status="done", session_id=None)
    _item(db, 1, "甲科技", "sent")

    assert write_schedule_receipt(factory, 1) == ""
    assert db.query(SalesAgentChatMessageORM).count() == 0
