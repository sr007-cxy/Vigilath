"""Skips for insufficient balance must leave a trace -- otherwise "updates stopped" is invisible on the page and in logs.

Actual case: the only account with Feishu push configured ran out of balance on 8/30, and every cron round after that turned back
at the billing pre-check. That return happened before the run log was created, so:
  - not a single record in sentiment_run_logs;
  - the account page still showed the "success" from 8/30.
It was broken for ten days with no hint anywhere in the system.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.sentiment import SentimentAccountORM, SentimentRunLogORM
from geo.services import sentiment_pipeline as pipe


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        SentimentAccountORM.__table__, SentimentRunLogORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_insufficient_balance_leaves_a_trace(db, monkeypatch):
    db.add(SentimentAccountORM(
        id=1, user_id=7, target="Acme Cloud", ticker="ACME", active=True,
        last_run_status="success", last_run_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    ))
    db.commit()

    monkeypatch.setattr(pipe, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(pipe, "_can_afford_one_run", lambda *a, **k: False)
    monkeypatch.setattr(pipe, "balance_service_get_daily_fee", lambda *a, **k: 500)

    r = pipe.run_pipeline_for_account(1, trigger="cron")
    assert r == {"status": "skipped", "reason": "insufficient_balance"}

    logs = db.query(SentimentRunLogORM).all()
    assert len(logs) == 1, "跳过也要写一条 run log,否则排查时什么都看不到"
    assert logs[0].status == "skipped"
    assert "余额不足" in (logs[0].error or "")
    assert "5.00 元" in (logs[0].error or ""), "提示里要带上真实单价"

    acc = db.get(SentimentAccountORM, 1)
    assert "余额不足" in (acc.last_run_error or "")
    # Frontend RunStatus only accepts pending/running/success/failed -- no new values
    assert acc.last_run_status == "success"
