"""Paid placement limits -- the only "worst case" box after removing second confirmation.

The criterion must be **net spend**: money charged then refunded doesn't count as spent, otherwise two failed order clicks would eat up
the user's daily allowance for nothing.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import (
    DIRECTION_CREDIT, DIRECTION_DEBIT, KIND_PUBLISH_MEDIA, KIND_REFUND,
    BalanceLedgerORM,
)
from geo.services import balance_service, publish_limits


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[BalanceLedgerORM.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _ledger(db, *, direction, kind, cents, ref_id, day=None):
    db.add(BalanceLedgerORM(
        user_id=1, created_at=datetime.utcnow(),
        bj_date=day or balance_service._bj_date(),
        direction=direction, kind=kind, amount_cents=cents,
        balance_after_cents=0, ref_type="publish_task", ref_id=str(ref_id),
        idem_key=f"{kind}:{ref_id}:{direction}",
    ))
    db.commit()


def test_single_order_over_cap_is_refused(db, monkeypatch):
    monkeypatch.setenv("PAID_PUBLISH_MAX_ORDER_CENTS", "20000")
    out = publish_limits.check_order(db, 1, 20001)
    assert out and out["code"] == "ORDER_LIMIT_EXCEEDED"
    assert publish_limits.check_order(db, 1, 20000) is None


def test_zero_means_unlimited(db, monkeypatch):
    monkeypatch.setenv("PAID_PUBLISH_MAX_ORDER_CENTS", "0")
    monkeypatch.setenv("PAID_PUBLISH_DAILY_CENTS", "0")
    assert publish_limits.check_order(db, 1, 99_999_99) is None


def test_daily_cap_counts_today_only(db, monkeypatch):
    monkeypatch.setenv("PAID_PUBLISH_MAX_ORDER_CENTS", "0")
    monkeypatch.setenv("PAID_PUBLISH_DAILY_CENTS", "10000")
    _ledger(db, direction=DIRECTION_DEBIT, kind=KIND_PUBLISH_MEDIA,
            cents=9000, ref_id=1)
    _ledger(db, direction=DIRECTION_DEBIT, kind=KIND_PUBLISH_MEDIA,
            cents=50000, ref_id=2, day="2000-01-01")     # a different day, must not be counted
    out = publish_limits.check_order(db, 1, 2000)
    assert out and out["code"] == "DAILY_LIMIT_EXCEEDED"
    assert out["spent_today_cents"] == 9000
    assert publish_limits.check_order(db, 1, 1000) is None


def test_refunded_orders_do_not_eat_the_daily_cap(db, monkeypatch):
    """Charged then refunded = never spent. That's why this function exists."""
    monkeypatch.setenv("PAID_PUBLISH_MAX_ORDER_CENTS", "0")
    monkeypatch.setenv("PAID_PUBLISH_DAILY_CENTS", "10000")
    _ledger(db, direction=DIRECTION_DEBIT, kind=KIND_PUBLISH_MEDIA,
            cents=9000, ref_id=1)
    _ledger(db, direction=DIRECTION_CREDIT, kind=KIND_REFUND,
            cents=9000, ref_id=1)
    assert balance_service.spent_today_cents(db, 1, kind=KIND_PUBLISH_MEDIA) == 0
    assert publish_limits.check_order(db, 1, 9000) is None


def test_refund_of_another_kind_is_not_subtracted(db, monkeypatch):
    """Refunds of other categories can't offset the daily media placement allowance."""
    monkeypatch.setenv("PAID_PUBLISH_MAX_ORDER_CENTS", "0")
    monkeypatch.setenv("PAID_PUBLISH_DAILY_CENTS", "10000")
    _ledger(db, direction=DIRECTION_DEBIT, kind=KIND_PUBLISH_MEDIA,
            cents=9000, ref_id=1)
    db.add(BalanceLedgerORM(
        user_id=1, created_at=datetime.utcnow(), bj_date=balance_service._bj_date(),
        direction=DIRECTION_CREDIT, kind=KIND_REFUND, amount_cents=9000,
        balance_after_cents=0, ref_type="retest_probe", ref_id="1",
        idem_key="other-refund",
    ))
    db.commit()
    assert balance_service.spent_today_cents(db, 1, kind=KIND_PUBLISH_MEDIA) == 9000


def test_batch_cap(db, monkeypatch):
    monkeypatch.setenv("PAID_PUBLISH_MAX_BATCH_CENTS", "100000")
    assert publish_limits.check_batch(db, 1, 100000, 5) is None
    out = publish_limits.check_batch(db, 1, 100001, 6)
    assert out and out["code"] == "BATCH_LIMIT_EXCEEDED"
