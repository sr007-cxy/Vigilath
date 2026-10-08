"""Daily monitoring hour -- `run_hour` only moves the time, not the money.

The billing metric definition is "one charge per account per Beijing day" (see test_sentiment_billing), so whichever hour
this column picks doesn't affect the amount. This only checks the scheduler's selection rules:
  - NULL: keep the old run-every-round behavior; old accounts' behavior must not change
  - value set: selected only at that Beijing-time hour
  - disabled accounts: never selected
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.sentiment import SentimentAccountORM
from geo.services import sentiment_scheduler as sched


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentimentAccountORM.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _acc(db, aid, *, run_hour=None, active=True):
    db.add(SentimentAccountORM(
        id=aid, user_id=1, target=f"t{aid}", ticker=f"T{aid}",
        run_hour=run_hour, active=active, created_at=datetime.utcnow(),
    ))
    db.commit()


def test_null_run_hour_always_eligible(db, monkeypatch):
    _acc(db, 1, run_hour=None)
    for hour in (0, 9, 23):
        monkeypatch.setattr(sched, "_bj_hour", lambda h=hour: h)
        assert sched._list_eligible_accounts(db) == [1]


def test_run_hour_only_fires_on_its_own_hour(db, monkeypatch):
    _acc(db, 1, run_hour=9)
    monkeypatch.setattr(sched, "_bj_hour", lambda: 8)
    assert sched._list_eligible_accounts(db) == []
    monkeypatch.setattr(sched, "_bj_hour", lambda: 9)
    assert sched._list_eligible_accounts(db) == [1]


def test_inactive_never_fires(db, monkeypatch):
    _acc(db, 1, run_hour=9, active=False)
    monkeypatch.setattr(sched, "_bj_hour", lambda: 9)
    assert sched._list_eligible_accounts(db) == []


def test_bj_hour_is_utc_plus_8_not_machine_local():
    """The server is UTC; local time can't be used as Beijing time -- an 8-hour offset means running in the middle of the night."""
    from datetime import timedelta

    assert sched._bj_hour() == (datetime.utcnow() + timedelta(hours=8)).hour
