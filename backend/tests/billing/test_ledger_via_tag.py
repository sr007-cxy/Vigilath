"""`note_json.via` must actually be written by the **production path**.

This test exists because of a failure mode that already happened: the agent's daily amount / daily count gates
counted by `via='agent'`, while nowhere in the repo ever wrote that tag -- the gates always counted 0 and never blocked
anything, and their unit tests stayed green because they hand-wrote `note={"via": "agent"}` themselves.
(Similar precedent: commit 4f717dd0 "count source is empty, the gate is inert" was reverted entirely.)

So this **doesn't hand-write note**; it sets up the context and goes through the real charge() to see whether it stamps the tag itself.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.services import balance_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserBalanceORM.__table__, BalanceLedgerORM.__table__, BalancePriceORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    balance_service.get_or_create_account(s, 1)
    balance_service.credit(s, user_id=1, kind="recharge", amount_cents=100000,
                           idem_key="topup")
    s.commit()
    yield s
    s.close()


def _note(db, idem: str) -> dict:
    row = db.query(BalanceLedgerORM).filter(
        BalanceLedgerORM.idem_key == idem).one()
    return json.loads(row.note_json or "{}")


def test_charge_stamps_via_from_context(db):
    with balance_service.via("agent"):
        balance_service.charge(db, user_id=1, kind="publish_media", amount_cents=100,
                               idem_key="k1", note={"media": "搜狐网"})
    db.commit()
    n = _note(db, "k1")
    assert n["via"] == "agent"
    assert n["media"] == "搜狐网", "盖章不能把原有 note 冲掉"


def test_no_context_no_stamp(db):
    """Money spent on the web shouldn't be counted as "spent in chat"."""
    balance_service.charge(db, user_id=1, kind="publish_media", amount_cents=100,
                           idem_key="k2", note={"media": "网易"})
    db.commit()
    assert "via" not in _note(db, "k2")


def test_explicit_via_wins(db):
    with balance_service.via("agent"):
        balance_service.charge(db, user_id=1, kind="batch", amount_cents=100,
                               idem_key="k3", note={"via": "cron"})
    db.commit()
    assert _note(db, "k3")["via"] == "cron"


def test_context_does_not_leak(db):
    with balance_service.via("agent"):
        pass
    balance_service.charge(db, user_id=1, kind="batch", amount_cents=100,
                           idem_key="k4", note={"x": 1})
    db.commit()
    assert "via" not in _note(db, "k4")


def test_credit_is_stamped_too(db):
    """Refunds must carry the tag too, otherwise daily **net** spend misses refunds and eats up the allowance for nothing."""
    with balance_service.via("agent"):
        balance_service.credit(db, user_id=1, kind="refund", amount_cents=100,
                               idem_key="k5", note={"reason": "publish_failed"})
    db.commit()
    assert _note(db, "k5")["via"] == "agent"


def test_daily_counter_sees_context_stamped_rows(db):
    """Gate (4)'s real criterion: without hand-writing note, just setting the context, spent_today_cents should count it."""
    with balance_service.via("agent"):
        balance_service.charge(db, user_id=1, kind="publish_media", amount_cents=4200,
                               idem_key="k6", ref_type="publish_task", ref_id="6")
    db.commit()
    assert balance_service.spent_today_cents(db, 1, via="agent") == 4200
    assert balance_service.spent_today_cents(db, 1, via="nobody") == 0


def test_context_survives_spawn_thread(db):
    """Draft generation charges in a background thread -- llm_usage.spawn_thread carries a contextvar snapshot in,
    taken at the moment of spawn, so the call site must wrap the spawn itself in via()."""
    from geo.services.llm_usage import spawn_thread

    seen = {}

    def _worker():
        seen["via"] = balance_service.current_via()

    with balance_service.via("agent"):
        th = spawn_thread(_worker)
        th.start()
    th.join(timeout=5)
    assert seen.get("via") == "agent"
