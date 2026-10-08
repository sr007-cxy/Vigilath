"""Paid placement refunds -- all three "value not delivered" cases must be refunded.

Before the change there was only one refund path: order failing on the spot. The other three never refunded users:

  1. Order result unknown, later reconciliation finds no order was created -> task back to planned and can be published again;
     without a refund the same article is charged twice
  2. Media rejection -> the article won't go out
  3. Link dead after publishing, appeal refund succeeded -> **the platform refunds our supplier account; the user gets nothing back**
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicDocPublishTaskORM,
)
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.user import UserORM
from geo.services import balance_service, publish_refund


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, BalanceLedgerORM.__table__,
        BalancePriceORM.__table__, AiTelemetryTopicORM.__table__,
        TopicDocPublishTaskORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    s.add(UserORM(id=1, email="u@x.z", password_hash="x", role="user"))
    s.add(AiTelemetryTopicORM(id=1, user_id=1, name="t", queries_json="[]",
                              engines_json="[]"))
    s.commit()
    balance_service.get_or_create_account(s, 1)
    balance_service.credit(s, user_id=1, kind="recharge", amount_cents=50000,
                           idem_key="topup")
    s.commit()
    yield s
    s.close()


def _task(db, tid=1):
    t = TopicDocPublishTaskORM(id=tid, topic_id=1, doc_id=1, platform="软文匠",
                               media_name="搜狐网", channel_type="external_media",
                               publish_mode="paid_media", status="publishing")
    db.add(t)
    db.commit()
    return t


def _charge(db, task, cents=4200):
    balance_service.charge(db, user_id=1, kind="publish_media", amount_cents=cents,
                           idem_key=f"rwjpub:{task.id}", topic_id=1,
                           ref_type="publish_task", ref_id=str(task.id))
    db.commit()


@pytest.mark.parametrize("reason", ["order_not_found", "rejected", "appeal",
                                    "publish_failed"])
def test_every_reason_refunds_in_full(db, reason):
    task = _task(db)
    before = balance_service.get_balance(db, 1)
    _charge(db, task)
    assert balance_service.get_balance(db, 1) == before - 4200

    got = publish_refund.refund_task(db, task, reason)
    db.commit()
    assert got == 4200
    assert balance_service.get_balance(db, 1) == before


def test_same_reason_refunds_only_once(db):
    task = _task(db)
    _charge(db, task)
    after_charge = balance_service.get_balance(db, 1)
    publish_refund.refund_task(db, task, "rejected")
    db.commit()
    refunded = balance_service.get_balance(db, 1)
    # Refund again: same idempotency key, money unchanged
    publish_refund.refund_task(db, task, "rejected")
    db.commit()
    assert balance_service.get_balance(db, 1) == refunded > after_charge


def test_second_reason_cannot_double_refund(db):
    """First refunded as no-order, then an appeal later succeeds -- the net is already 0; no second refund possible."""
    task = _task(db)
    _charge(db, task)
    publish_refund.refund_task(db, task, "order_not_found")
    db.commit()
    bal = balance_service.get_balance(db, 1)
    assert publish_refund.refund_task(db, task, "appeal") is None
    db.commit()
    assert balance_service.get_balance(db, 1) == bal


def test_nothing_charged_nothing_refunded(db):
    """With the charging gate closed / ops publishing on behalf there was never a charge; can't hand out money from nothing."""
    task = _task(db)
    before = balance_service.get_balance(db, 1)
    assert publish_refund.refund_task(db, task, "rejected") is None
    db.commit()
    assert balance_service.get_balance(db, 1) == before


def test_refund_goes_back_to_whoever_paid(db):
    """Refund to **whoever was originally charged**, looked up from the ledger, not guessing the topic owner.

    If the topic owner changed later, or ops publishing on behalf charged someone else, refunding the owner refunds the wrong person.
    """
    db.add(UserORM(id=2, email="v@x.z", password_hash="x", role="user"))
    db.commit()
    balance_service.get_or_create_account(db, 2)
    balance_service.credit(db, user_id=2, kind="recharge", amount_cents=10000,
                           idem_key="topup2")
    db.commit()
    task = _task(db, tid=5)
    balance_service.charge(db, user_id=2, kind="publish_media", amount_cents=3000,
                           idem_key=f"rwjpub:{task.id}", ref_type="publish_task",
                           ref_id=str(task.id))
    db.commit()
    b1 = balance_service.get_balance(db, 1)
    b2 = balance_service.get_balance(db, 2)

    publish_refund.refund_task(db, task, "rejected")
    db.commit()
    assert balance_service.get_balance(db, 2) == b2 + 3000
    assert balance_service.get_balance(db, 1) == b1, "退错人了"


def test_charged_cents_is_net(db):
    task = _task(db)
    _charge(db, task)
    assert publish_refund.charged_cents(db, task.id) == 4200
    publish_refund.refund_task(db, task, "rejected")
    db.commit()
    assert publish_refund.charged_cents(db, task.id) == 0
