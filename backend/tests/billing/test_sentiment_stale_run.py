"""Accounts stuck at running must be able to unstick themselves -- otherwise briefs stop updating forever.

Batch runs use daemon threads (see the comment at the top of sentiment_pipeline); a release restart / OOM / docker stop
makes them vanish on the spot without the state machine writing failed, so the account stays at last_run_status='running'.
After that every entry gets blocked by the re-entry guard: cron skips, the save-side dedupe doesn't queue, even the /run-now
"escape hatch" returns skipped -- while the frontend shows everything normal.

The old fallback cleanup_zombies had two holes:
  1. it only ran inside APScheduler, and GEO_SCHEDULER_LEADER wasn't set on either the test host or CN production;
  2. the criterion was `last_run_at < cutoff`, and last_run_at records "last finished",
     which _mark_running never writes -- an account crashing on its first round has last_run_at NULL,
     `NULL < cutoff` is always NULL, so **the kind most likely to get stuck was exactly the kind never cleaned up**.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.sentiment import SentimentAccountORM, SentimentRunLogORM
from geo.services import sentiment_pipeline as pipe
from geo.services import sentiment_scheduler as sched


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        SentimentAccountORM.__table__, SentimentRunLogORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _acc(db, aid=1, *, last_run_at=None):
    db.add(SentimentAccountORM(
        id=aid, user_id=1, target=f"t{aid}", ticker=f"T{aid}",
        active=True, last_run_status="running", last_run_at=last_run_at,
        created_at=datetime.utcnow(),
    ))
    db.commit()


def _log(db, aid=1, *, minutes_ago, status="running"):
    row = SentimentRunLogORM(
        account_id=aid, trigger="cron", status=status,
        started_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(row)
    db.commit()
    return row


def test_fresh_run_is_not_a_zombie(db):
    """A round that just started must not be grabbed as a zombie -- that would run two rounds in parallel."""
    _acc(db)
    _log(db, minutes_ago=5)
    assert pipe.zombie_started_at(db, 1) is None


def test_timed_out_run_is_a_zombie(db):
    _acc(db)
    _log(db, minutes_ago=pipe.ZOMBIE_TIMEOUT_MIN + 1)
    assert pipe.zombie_started_at(db, 1) is not None


def test_running_without_any_log_is_a_zombie(db):
    """The status field says running but there's no running log -- the state machine broke halfway.

    There's no evidence anyone is really running; the account shouldn't be locked forever.
    """
    _acc(db)
    assert pipe.zombie_started_at(db, 1) is not None


def test_cleanup_catches_account_that_never_finished_a_run(db, monkeypatch):
    """Regression: zombies with last_run_at NULL (never succeeded) must be cleanable.

    The old criterion `last_run_at < cutoff` is always NULL for NULL in SQL, so this kind always slipped through.
    """
    _acc(db, last_run_at=None)
    _log(db, minutes_ago=pipe.ZOMBIE_TIMEOUT_MIN + 1)

    monkeypatch.setattr(sched, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    sched.cleanup_zombies()

    assert db.get(SentimentAccountORM, 1).last_run_status == "failed"


def test_takeover_closes_the_dead_run_log(db):
    """On takeover the old log must be closed out as failed, otherwise the next round picks it again and the list forever shows an unfinished record."""
    _acc(db)
    dead = _log(db, minutes_ago=pipe.ZOMBIE_TIMEOUT_MIN + 1)

    pipe._fail_stale_runs(db, 1)

    db.refresh(dead)
    assert dead.status == "failed"
    assert dead.ended_at is not None
    assert pipe.zombie_started_at(db, 1) is not None  # no running logs left -> can still take over
