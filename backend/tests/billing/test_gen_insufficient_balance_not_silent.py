"""Insufficient balance must not be silent -- drafts must never stay "generating" forever.

Production case on 2026-08-27: ops repeatedly clicked "regenerate" on the plan page, and the endpoint returned 200 every time,
but the background task hit the balance gate, wrote one WARNING line and returned. As a result:
  - the frontend got 200 and assumed dispatch succeeded
  - the draft row was pre-set to generating by the endpoint, and nobody ever changed it back
  - generation_error was empty, so the UI showed no reason at all
5 zombie rows piled up in the DB, the oldest stuck for nearly a month (topics 9 / 10 / 20).

This pins two things:
  1. On insufficient balance `insufficient_balance_msg` must give a sentence **you can top up from**
     (how much is missing, how much there is now), not just "insufficient balance"
  2. `_fail_generating_docs` must move generating rows back to pending_review and record the error cause
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.ai_telemetry import TopicGeneratedDocORM
from geo.services import content_generator as cg


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[TopicGeneratedDocORM.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


class _Topic:
    id = 7


def _doc(db, doc_id, *, status, item_id=None, err=None):
    db.add(TopicGeneratedDocORM(
        id=doc_id, topic_id=7, status=status, plan_item_id=item_id,
        source_query_text="q", generation_error=err,
    ))
    db.commit()


def test_message_says_how_much_is_missing(monkeypatch):
    """The copy must contain "how much is needed" and "how much is available", otherwise ops won't know how much to top up."""
    monkeypatch.setattr(cg.topic_billing, "price", lambda db, key: 300)
    monkeypatch.setattr(cg.topic_billing, "shortfall", lambda db, t, need: (need, 120))
    msg = cg.insufficient_balance_msg(None, _Topic(), 2)
    assert msg is not None
    assert "600" in msg and "120" in msg, msg


def test_enough_balance_returns_none(monkeypatch):
    monkeypatch.setattr(cg.topic_billing, "price", lambda db, key: 300)
    monkeypatch.setattr(cg.topic_billing, "shortfall", lambda db, t, need: None)
    assert cg.insufficient_balance_msg(None, _Topic(), 1) is None


def test_generating_rows_are_released_with_reason(db):
    """After hitting the gate: generating goes back to pending_review with a non-empty error cause -- the core of this bug."""
    _doc(db, 1, status="generating", item_id="a")
    _doc(db, 2, status="generating", item_id="b")
    _doc(db, 3, status="pending_review", item_id="c")   # must not be touched

    cg._fail_generating_docs(db, 7, "余额不足:本次需 300 分,当前余额 0 分。")

    d1 = db.get(TopicGeneratedDocORM, 1)
    d3 = db.get(TopicGeneratedDocORM, 3)
    assert d1.status == "pending_review"
    assert d1.generation_error and "余额不足" in d1.generation_error
    assert d3.generation_error is None          # it was never generating; don't hit it by mistake


def test_only_this_batch_is_released(db):
    """With plan_item_ids only these rows are touched; don't bounce other batches along with them."""
    _doc(db, 1, status="generating", item_id="a")
    _doc(db, 2, status="generating", item_id="other-batch")

    cg._fail_generating_docs(db, 7, "余额不足", plan_item_ids=["a"])

    assert db.get(TopicGeneratedDocORM, 1).status == "pending_review"
    assert db.get(TopicGeneratedDocORM, 2).status == "generating"


def test_soft_deleted_rows_are_left_alone(db):
    """Soft-deleted drafts don't participate -- they shouldn't appear in any UI again."""
    from datetime import datetime
    _doc(db, 1, status="generating", item_id="a")
    d = db.get(TopicGeneratedDocORM, 1)
    d.deleted_at = datetime.utcnow()
    db.commit()

    cg._fail_generating_docs(db, 7, "余额不足")
    assert db.get(TopicGeneratedDocORM, 1).status == "generating"
