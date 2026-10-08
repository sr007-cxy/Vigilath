"""Proactive notifications -- a citation hit is the product's only aha moment, and the only thing that can sustain renewals.

The `agent_notifications` table and read endpoint existed long ago, but **nothing ever wrote to it**
(`methods.deliver_notification` was a NotImplementedError). So "the article you published three weeks ago
was cited by DeepSeek today" **could never happen** before this change.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.geo import notify
from geo.database import Base
from geo.models.agent import AgentNotificationORM


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[AgentNotificationORM.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _doc(did=1, title="老楼加装电梯怎么选"):
    return SimpleNamespace(id=did, title=title, source_query_text="")


def _topic(uid=1):
    return SimpleNamespace(id=1, user_id=uid)


def test_cited_notification_says_what_who_and_when(db):
    """All three are required: which article, cited by whom, and while answering what question.

    A subject-less notification like "your article was cited" leaves users not knowing what to do.
    """
    notify.cited_by_engine(db, _doc(), _topic(), ["DeepSeek"], query="加装电梯哪家好")
    db.commit()
    row = db.query(AgentNotificationORM).one()
    assert "老楼加装电梯怎么选" in row.body
    assert "DeepSeek" in row.body
    assert "加装电梯哪家好" in row.body


def test_same_event_same_day_is_deduped(db):
    """The same article cited by three engines shouldn't push three times, nor should two re-test rounds in a day push twice."""
    assert notify.cited_by_engine(db, _doc(), _topic(), ["DeepSeek"]) is not None
    db.commit()
    assert notify.cited_by_engine(db, _doc(), _topic(), ["豆包"]) is None
    db.commit()
    assert db.query(AgentNotificationORM).count() == 1


def test_different_docs_are_not_deduped(db):
    notify.cited_by_engine(db, _doc(1), _topic(), ["DeepSeek"])
    notify.cited_by_engine(db, _doc(2, "另一篇"), _topic(), ["DeepSeek"])
    db.commit()
    assert db.query(AgentNotificationORM).count() == 2


def test_no_engines_no_notification(db):
    assert notify.cited_by_engine(db, _doc(), _topic(), []) is None


def test_quiet_hours_still_persist(db, monkeypatch):
    """Do-not-disturb means "not delivered immediately", not "discarded" -- users must see it when they open the chat the next day."""
    monkeypatch.setattr(notify, "in_quiet_hours", lambda now=None: True)
    assert notify.cited_by_engine(db, _doc(), _topic(), ["DeepSeek"]) is not None
    db.commit()
    assert db.query(AgentNotificationORM).count() == 1


def test_refund_notification_tells_the_amount(db):
    """Saying only "placement failed" makes users think the money is gone; it must say how much was refunded."""
    task = SimpleNamespace(id=7, media_name="搜狐网")
    notify.refunded(db, task, _topic(), cents=4200, reason_text="媒体拒稿")
    db.commit()
    row = db.query(AgentNotificationORM).one()
    assert "42.00" in row.body and "媒体拒稿" in row.body


def test_zero_refund_is_not_announced(db):
    task = SimpleNamespace(id=7, media_name="搜狐网")
    assert notify.refunded(db, task, _topic(), cents=0, reason_text="x") is None


def test_switch_off(db, monkeypatch):
    monkeypatch.setenv("AGENT_NOTIFY_ENABLED", "0")
    assert notify.cited_by_engine(db, _doc(), _topic(), ["DeepSeek"]) is None


@pytest.mark.parametrize("hours,hour,expected", [
    ("22-8", 23, True), ("22-8", 3, True), ("22-8", 12, False),
    ("1-6", 3, True), ("1-6", 12, False),
    ("0-0", 3, False),          # same start and end = no do-not-disturb
])
def test_quiet_window_wraps_midnight(monkeypatch, hours, hour, expected):
    monkeypatch.setenv("AGENT_NOTIFY_QUIET_HOURS", hours)
    now = datetime(2026, 8, 26, hour, tzinfo=notify._BJ)
    assert notify.in_quiet_hours(now) is expected
