"""Buying membership = activate tier + equal amount of credits. The three payment gateways share `membership_billing`.

Product metric definition: buying the 39.9 CNY tier also adds +39.9 to the credit balance (1 credit = 1 CNY).
Two edges prone to accidents:
  1. **Currency** -- `memberships.price` stores USD, CN and global sites share one DB, and credits are counted in CNY cents.
     Crediting $9.99 directly as ¥9.99 gives away 7x for free.
  2. **Duplicate callbacks** -- WeChat re-pushes, Stripe webhooks collide with frontend polling; duplicate credits are real money.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base, settings
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.payment import PaymentSessionORM
from geo.models.user import UserORM
from geo.services import balance_service, membership_billing


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, BalanceLedgerORM.__table__,
        BalancePriceORM.__table__, PaymentSessionORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_real_upgrade(monkeypatch):
    """`upgrade_membership` brings its own SessionLocal and would hit the real DB -- tests only record calls."""
    calls = []
    monkeypatch.setattr(membership_billing.membership_service, "upgrade_membership",
                        lambda uid, mid: calls.append((uid, mid)))
    return calls


def _row(db, *, amount_cents=3990, currency="cny", membership_id=2):
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role="user"))
    row = PaymentSessionORM(
        id=7, stripe_session_id="ORDER123", user_id=1, membership_id=membership_id,
        purpose="membership", amount_cents=amount_cents, currency=currency,
        status="paid", created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    balance_service.get_or_create_account(db, 1)
    drain = balance_service.get_balance(db, 1)       # drain the sign-up bonus so assertions are clean
    if drain:
        balance_service.charge(db, user_id=1, kind="batch", amount_cents=drain,
                               idem_key="drain-grant")
    db.commit()
    return row


def test_cny_order_grants_equal_points(db, no_real_upgrade):
    row = _row(db, amount_cents=3990, currency="cny")

    membership_billing.fulfill_paid_membership(db, row, provider="wechat",
                                               order_ref="ORDER123")

    assert balance_service.get_balance(db, 1) == 3990       # 39.9 CNY -> 39.9 credits
    assert no_real_upgrade == [(1, 2)]


def test_usd_order_is_converted_not_taken_at_face_value(db):
    """$9.99 must not count as ¥9.99 -- convert to cents with USD_TO_CNY_RATE."""
    row = _row(db, amount_cents=999, currency="usd")

    membership_billing.fulfill_paid_membership(db, row, provider="stripe",
                                               order_ref="ORDER123")

    assert balance_service.get_balance(db, 1) == round(999 * settings.USD_TO_CNY_RATE)


def test_usdc_follows_usd(db):
    """MoltsPay writes the currency as usdc, 1:1 with USD."""
    row = _row(db, amount_cents=999, currency="usdc")

    membership_billing.fulfill_paid_membership(db, row, provider="moltspay",
                                               order_ref="ORDER123")

    assert balance_service.get_balance(db, 1) == round(999 * settings.USD_TO_CNY_RATE)


def test_replayed_callback_credits_once(db):
    """WeChat re-push / webhook colliding with polling -- the idempotency key pins the order number, credited only once."""
    row = _row(db, amount_cents=3990, currency="cny")

    first = membership_billing.fulfill_paid_membership(db, row, provider="wechat",
                                                       order_ref="ORDER123")
    second = membership_billing.fulfill_paid_membership(db, row, provider="wechat",
                                                        order_ref="ORDER123")

    assert first.status == balance_service.OK
    assert second.status == balance_service.DUPLICATE
    assert balance_service.get_balance(db, 1) == 3990
    assert db.query(BalanceLedgerORM).filter(
        BalanceLedgerORM.idem_key == "memb:ORDER123").count() == 1


def test_topup_and_membership_of_same_order_do_not_collide(db):
    """Top-up orders use `wxpay:`, membership orders use `memb:` -- one order number can't count as both a top-up and a membership."""
    row = _row(db, amount_cents=3990, currency="cny")
    balance_service.credit(db, user_id=1, kind="recharge", amount_cents=3990,
                           idem_key="wxpay:ORDER123")
    db.commit()

    membership_billing.fulfill_paid_membership(db, row, provider="wechat",
                                               order_ref="ORDER123")

    assert balance_service.get_balance(db, 1) == 7980


def test_unknown_currency_grants_no_points_but_still_upgrades(db, no_real_upgrade):
    """Unrecognized currencies: better to give less than give wrong -- tier still activated, credits untouched."""
    row = _row(db, amount_cents=999, currency="jpy")

    assert membership_billing.fulfill_paid_membership(
        db, row, provider="stripe", order_ref="ORDER123") is None
    assert balance_service.get_balance(db, 1) == 0
    assert no_real_upgrade == [(1, 2)]


def test_free_tier_order_grants_nothing(db, no_real_upgrade):
    row = _row(db, amount_cents=0, currency="cny")

    assert membership_billing.fulfill_paid_membership(
        db, row, provider="wechat", order_ref="ORDER123") is None
    assert balance_service.get_balance(db, 1) == 0
    assert no_real_upgrade == [(1, 2)]
