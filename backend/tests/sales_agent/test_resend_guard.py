"""Resend guardrail.

Before this, `sent` was also a confirmable state, and the frontend turned the button into "send again", so the same email
could be really sent to the same recipient unlimited times -- hurting sender domain reputation and easily getting blocked.
The panel didn't show how many times it was sent either; the user only saw a button.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_resend_guard.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentEmailDeliveryORM, SalesAgentOutreachLogORM
from agents.sales_agent.service.common import SalesAgentError
from agents.sales_agent.service.outreach import RESEND_COOLDOWN_SECONDS, RESEND_MAX_TIMES, _guard_resend
from geo.database import Base

USER = 1


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentOutreachLogORM.__table__, SalesAgentEmailDeliveryORM.__table__])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _draft(db, status="sent"):
    row = SalesAgentOutreachLogORM(id=1, lead_id=1, channel="email",
                                   subject="s", content="c", send_status=status)
    db.add(row)
    db.commit()
    return row


def _sent(db, minutes_ago=0, n=1):
    now = datetime.utcnow() - timedelta(minutes=minutes_ago)
    for i in range(n):
        db.add(SalesAgentEmailDeliveryORM(user_id=USER, outreach_log_id=1, status="sent",
                                          sent_at=now, created_at=now, tracking_token=f"t{minutes_ago}-{i}"))
    db.commit()


def test_first_send_passes(db):
    """Drafts never sent are unaffected."""
    _guard_resend(db, _draft(db, status="draft"))


def test_resend_within_cooldown_is_blocked(db):
    draft = _draft(db)
    _sent(db, minutes_ago=1)
    with pytest.raises(SalesAgentError) as e:
        _guard_resend(db, draft)
    assert "分钟" in str(e.value)


def test_resend_after_cooldown_passes(db):
    draft = _draft(db)
    _sent(db, minutes_ago=int(RESEND_COOLDOWN_SECONDS / 60) + 1)
    _guard_resend(db, draft)


def test_max_times_blocks_even_after_cooldown(db):
    """Even after cooldown, sending can't be unlimited."""
    draft = _draft(db)
    _sent(db, minutes_ago=600, n=RESEND_MAX_TIMES)
    with pytest.raises(SalesAgentError) as e:
        _guard_resend(db, draft)
    assert "不能再发" in str(e.value)


def test_failed_deliveries_do_not_count(db):
    """Failures don't count toward the total -- otherwise a few failed sends would block sending forever."""
    draft = _draft(db)
    db.add(SalesAgentEmailDeliveryORM(user_id=USER, outreach_log_id=1, status="failed",
                                      created_at=datetime.utcnow(), tracking_token="f1"))
    db.commit()
    _guard_resend(db, draft)


def test_error_message_is_actionable(db):
    """A refusal must give a next step, not just say no."""
    draft = _draft(db)
    _sent(db, minutes_ago=600, n=RESEND_MAX_TIMES)
    with pytest.raises(SalesAgentError) as e:
        _guard_resend(db, draft)
    assert "重新生成草稿" in str(e.value)
