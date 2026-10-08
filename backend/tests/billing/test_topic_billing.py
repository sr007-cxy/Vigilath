"""Ownership decisions for topic-level charges -- who pays, and when nobody does.

Batch runs / drafts / images / compliance / re-tests use a **deliberately different** metric definition from paid media placement:
placement charges the operator (whoever clicks orders), these five charge the topic owner (the cost grows on the topic; cron / background threads /
agent / ops console can all trigger it, and charging "whoever clicked" would make the same cost drift between people depending on the trigger path).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.user import UserORM
from geo.services import balance_service, topic_billing


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__,
        BalanceLedgerORM.__table__, BalancePriceORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _user(db, uid: int, role: str = "user"):
    db.add(UserORM(id=uid, email=f"u{uid}@x.z", password_hash="x", role=role))
    db.commit()
    return uid


def _topic(uid):
    return SimpleNamespace(id=99, user_id=uid)


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("GEO_TOPIC_CHARGE_ENABLED", "1")


def test_payer_is_the_topic_owner(db):
    _user(db, 7)
    assert topic_billing.payer_id(db, _topic(7)) == 7


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_owned_topics_are_free(db, role):
    """Internal, demo and cross-check topics all live under ops accounts -- charging them is charging ourselves."""
    _user(db, 8, role)
    assert topic_billing.payer_id(db, _topic(8)) is None
    assert topic_billing.charge(db, _topic(8), kind="batch", amount_cents=500,
                                idem_key="k1") is None


def test_orphan_topic_charges_nobody(db):
    """The owner has been deleted -- can't charge some other still-existing account out of thin air."""
    assert topic_billing.payer_id(db, _topic(404)) is None
    assert topic_billing.payer_id(db, SimpleNamespace(id=1, user_id=None)) is None


def test_charge_moves_money_and_is_idempotent(db):
    uid = _user(db, 9)
    balance_service.get_or_create_account(db, uid)      # creating the account grants the sign-up bonus
    balance_service.credit(db, user_id=uid, kind="recharge", amount_cents=10000,
                           idem_key="topup-1")
    db.commit()
    before = balance_service.get_balance(db, uid)

    r1 = topic_billing.charge(db, _topic(uid), kind="batch", amount_cents=300,
                              idem_key="batch:1:2026-08-25")
    db.commit()
    assert r1.status == balance_service.OK
    assert balance_service.get_balance(db, uid) == before - 300

    r2 = topic_billing.charge(db, _topic(uid), kind="batch", amount_cents=300,
                              idem_key="batch:1:2026-08-25")
    db.commit()
    assert r2.status == balance_service.DUPLICATE
    assert balance_service.get_balance(db, uid) == before - 300, "同一把幂等键不能扣两次"


def test_zero_price_is_a_no_op(db):
    """Setting prices to 0 is this change's gradual rollout technique: hook points go live fully, then prices are turned on one by one."""
    uid = _user(db, 10)
    balance_service.get_or_create_account(db, uid)
    db.commit()
    n_before = db.query(BalanceLedgerORM).count()      # the account creation bonus row is already there
    assert topic_billing.charge(db, _topic(uid), kind="batch",
                                amount_cents=0, idem_key="z") is None
    assert db.query(BalanceLedgerORM).count() == n_before, "0 元不该写流水"


def test_switch_off_stops_everything(db, monkeypatch):
    monkeypatch.setenv("GEO_TOPIC_CHARGE_ENABLED", "0")
    uid = _user(db, 11)
    assert topic_billing.charge(db, _topic(uid), kind="batch",
                                amount_cents=500, idem_key="x") is None
    assert topic_billing.can_afford(db, _topic(uid), 999999) is True


def test_can_afford_never_blocks_free_topics(db):
    """Topics that aren't charged (ops / master gate off / price 0) always pass the pre-check -- don't let it block them."""
    _user(db, 12, "admin")
    assert topic_billing.can_afford(db, _topic(12), 999999) is True
