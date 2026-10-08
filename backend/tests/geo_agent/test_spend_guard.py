"""Guardrails for spending money in chat -- the only "worst case" box after removing second confirmation."""
from __future__ import annotations

import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.geo import spend
from geo.database import Base
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.user import UserORM
from geo.services import balance_service


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__,
        BalanceLedgerORM.__table__, BalancePriceORM.__table__,
    ])
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role="user"))
    db.commit()
    balance_service.get_or_create_account(db, 1)
    balance_service.credit(db, user_id=1, kind="recharge", amount_cents=100000,
                           idem_key="topup")
    db.commit()
    yield SimpleNamespace(db=db, user_id=1, account_id=1, channel="web",
                          caps=["read", "write"], tid="", tenant_id=None,
                          policy_key="", role="user", scope="self")
    db.close()


@pytest.fixture(autouse=True)
def _no_policy(monkeypatch):
    monkeypatch.setattr("agents.geo.methods.usage_guardrail_check", lambda d, a: None)
    spend._recent.clear()


def _plan(cents=1000, ref="1"):
    return spend.SpendPlan(tool="pay_and_publish", amount_cents=cents, ref_id=ref)


def _spent(db, cents, key, via="agent"):
    balance_service.charge(db, user_id=1, kind="publish_media", amount_cents=cents,
                           idem_key=key, ref_type="publish_task", ref_id=key,
                           note={"via": via})
    db.commit()


def test_normal_order_passes(deps):
    assert spend.precheck(deps, _plan(1000)) is None


def test_single_cap(deps, monkeypatch):
    monkeypatch.setenv("AGENT_SPEND_MAX_SINGLE_CENTS", "5000")
    out = spend.precheck(deps, _plan(5001))
    assert out and out["code"] == "SPEND_LIMIT_SINGLE"
    assert "网页" in out["note"], "拦下来必须给出路,不能只说不行"


def test_daily_cap_counts_only_agent_spending(deps, monkeypatch):
    """The daily gate only counts money spent **in chat** -- cards swiped by users on the web shouldn't use up the chat allowance."""
    monkeypatch.setenv("AGENT_SPEND_MAX_SINGLE_CENTS", "0")
    monkeypatch.setenv("AGENT_SPEND_MAX_DAILY_CENTS", "3000")
    _spent(deps.db, 5000, "web-order", via="web")      # spent on the web, doesn't count
    assert spend.precheck(deps, _plan(2000)) is None
    _spent(deps.db, 2500, "agent-order", via="agent")  # spent in chat, counts
    out = spend.precheck(deps, _plan(1000))
    assert out and out["code"] == "SPEND_LIMIT_DAILY"


def test_daily_count_cap(deps, monkeypatch):
    monkeypatch.setenv("AGENT_SPEND_MAX_SINGLE_CENTS", "0")
    monkeypatch.setenv("AGENT_SPEND_MAX_DAILY_CENTS", "0")
    monkeypatch.setenv("AGENT_SPEND_MAX_DAILY_COUNT", "2")
    _spent(deps.db, 100, "a1")
    _spent(deps.db, 100, "a2")
    out = spend.precheck(deps, _plan(100))
    assert out and out["code"] == "SPEND_LIMIT_COUNT"


def test_insufficient_balance_points_at_topup(deps, monkeypatch):
    # Turn off all caps to verify the balance check alone
    monkeypatch.setenv("AGENT_SPEND_MAX_SINGLE_CENTS", "0")
    monkeypatch.setenv("AGENT_SPEND_MAX_DAILY_CENTS", "0")
    monkeypatch.setenv("AGENT_SPEND_MAX_DAILY_COUNT", "0")
    out = spend.precheck(deps, _plan(999_999))
    assert out and out["code"] == "INSUFFICIENT_BALANCE"
    assert out["topup"]["web"] == "/account/balance"


def test_external_channel_blocked_before_anything_else(deps):
    deps.channel = "im"
    out = spend.precheck(deps, _plan(100))
    assert out and out["capability"] == "publish_paid"


def test_cooldown_returns_last_result_instead_of_reordering(deps):
    """Model retries / user double clicks / SSE reconnects all "run the same sentence twice", and orders are irreversible."""
    plan = _plan(1000, ref="7,8")
    assert spend.cooled_down(plan) is None
    spend.remember(plan, {"已下单": 2})
    again = spend.cooled_down(plan)
    assert again is not None and again["已下单"] == 2
    assert "没有重复下单" in again["note"]


def test_idem_key_has_no_turn_id(deps):
    """The idempotency key **excludes turn_id** -- including it would mean "charge once per model retry".

    This is deliberately the opposite of confirm.py: that one relies on turn_id to guarantee "there must really be a new user message",
    while here the same order must always hit the same key.
    """
    plan = _plan(1000, ref="7")
    k1 = spend.idem_key(deps, plan)
    deps.turn_id = "another-turn"
    assert spend.idem_key(deps, plan) == k1
    assert "turn" not in k1
    body = inspect.getsource(spend.idem_key)
    body = body.replace(spend.idem_key.__doc__ or "", "")     # mentions in comments are explanations and don't count
    assert "turn_id" not in body


def test_dry_run_never_touches_the_wallet(deps, monkeypatch):
    monkeypatch.setenv("AGENT_SPEND_DRY_RUN", "1")
    called = []
    monkeypatch.setattr(balance_service, "charge",
                        lambda *a, **k: called.append(1))
    assert spend.dry_run() is True
    assert not called
