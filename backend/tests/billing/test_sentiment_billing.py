"""Per-run sentiment monitoring charges -- one charge per day, charged only on success, insufficient credits only skip and never disable.

Product metric definition: sentiment monitoring has no monthly plan; it's charged per run. **Daily monitoring uses `sentiment_daily` (5 CNY/day),
not the quick-check tier `sentiment_once` (10 CNY/run)** -- charging the quick-check price daily would be 300 CNY per brand per month,
more than the most expensive tier.

cron runs hourly, so the idempotency key must pin "account x Beijing day" -- charging per call would bill
daily users 24 times over.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.sentiment import SentimentAccountORM
from geo.models.user import UserORM
from geo.services import balance_service
from geo.services import sentiment_pipeline as sp


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, BalanceLedgerORM.__table__,
        BalancePriceORM.__table__, SentimentAccountORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _acc(db, *, role="user", balance=100000, price=500):
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role=role))
    # Daily monitoring reads sentiment_daily; the quick-check tier has its own price; don't mix them
    db.add(BalancePriceORM(key="sentiment_daily", price_cents=price, enabled=1,
                           updated_at=datetime.utcnow()))
    db.commit()
    balance_service.get_or_create_account(db, 1)      # creating the account grants the sign-up bonus
    # Drain the bonus first so the balance parameter decides -- otherwise the "balance 0" case still has ¥10
    grant = balance_service.get_balance(db, 1)
    if grant:
        balance_service.charge(db, user_id=1, kind="batch", amount_cents=grant,
                               idem_key="drain-grant")
    if balance:
        balance_service.credit(db, user_id=1, kind="recharge", amount_cents=balance,
                               idem_key="topup")
    acc = SentimentAccountORM(id=1, user_id=1, target="威吉力", ticker="VGL",
                              intent="brand", active=True)
    db.add(acc)
    db.commit()
    return acc


def test_one_charge_per_day_not_per_run(db):
    """cron runs hourly -- charging per call would bill daily users 24 times over."""
    acc = _acc(db)
    before = balance_service.get_balance(db, 1)
    for _ in range(5):
        sp._charge_one_run(db, acc)
    assert balance_service.get_balance(db, 1) == before - 500


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_accounts_are_free(db, role):
    acc = _acc(db, role=role)
    before = balance_service.get_balance(db, 1)
    sp._charge_one_run(db, acc)
    assert balance_service.get_balance(db, 1) == before


def test_precheck_blocks_when_broke(db):
    acc = _acc(db, balance=0)
    assert sp._can_afford_one_run(db, acc) is False


def test_precheck_passes_when_already_charged_today(db):
    """Already charged today -- this round is free and shouldn't be skipped because the balance ran out."""
    acc = _acc(db, balance=600)
    sp._charge_one_run(db, acc)               # charge 500, 100 left
    assert sp._charged_today(db, acc) is True
    # Drain the balance; it should still be allowed (the idempotency key makes this round free)
    balance_service.charge(db, user_id=1, kind="batch",
                           amount_cents=balance_service.get_balance(db, 1),
                           idem_key="drain")
    db.commit()
    assert balance_service.get_balance(db, 1) == 0
    assert sp._can_afford_one_run(db, acc) is True


def test_zero_price_is_free_and_unblocking(db):
    acc = _acc(db, balance=0, price=0)
    assert sp._can_afford_one_run(db, acc) is True
    before = balance_service.get_balance(db, 1)
    sp._charge_one_run(db, acc)
    assert balance_service.get_balance(db, 1) == before


def test_legacy_idem_key_is_honoured_on_the_switch_day(db):
    """On the price-split day: accounts already charged under the old key `sentonce:` must not be charged again under the new key `sentdaily:`."""
    acc = _acc(db, balance=10000)
    day = balance_service._bj_date()
    balance_service.charge(db, user_id=1, kind="sentiment_once", amount_cents=500,
                           idem_key=f"sentonce:{acc.id}:{day}")
    db.commit()
    before = balance_service.get_balance(db, 1)

    sp._charge_one_run(db, acc)

    assert balance_service.get_balance(db, 1) == before
    assert sp._charged_today(db, acc) is True
