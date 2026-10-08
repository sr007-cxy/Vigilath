"""Post-paid batch run settlement -- one charge per closed Beijing day.

Two metric definitions must be pinned:
  1. **Today isn't settled** (still being written to; settling would miss the second half of the day)
  2. **One charge per day, idempotent** (the settler may catch up 7 days; hitting the same key must not charge twice)

⚠️ Since 2026-09-09 production defaults to **prepaid** (`GEO_BATCH_PREPAY=1`,
geo/services/batch_prepay.py), in which case `settle_closed_days` runs empty -- the two are mutually exclusive;
enabling both charges the same cell twice. So this file turns prepay off throughout: it tests the behavior in
the "fallback to post-paid" world. The prepay self-check itself is in
`scripts/verify_batch_prepay.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.ai_telemetry import (
    AiTelemetryResponseORM, AiTelemetryRunORM, AiTelemetryTopicORM,
)
from geo.models.balance import BalanceLedgerORM, BalancePriceORM, UserBalanceORM
from geo.models.user import UserORM
from geo.services import balance_service, batch_billing


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, BalanceLedgerORM.__table__,
        BalancePriceORM.__table__, AiTelemetryTopicORM.__table__,
        AiTelemetryRunORM.__table__, AiTelemetryResponseORM.__table__,
    ])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _setup(db, *, role="user", balance=100000):
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role=role))
    db.add(AiTelemetryTopicORM(id=1, user_id=1, name="t", queries_json="[]",
                               engines_json="[]"))
    db.add(AiTelemetryRunORM(id=1, topic_id=1, status="success"))
    db.add(BalancePriceORM(key="batch_cell", price_cents=2, enabled=1,
                           updated_at=datetime.utcnow()))
    db.commit()
    balance_service.get_or_create_account(db, 1)
    balance_service.credit(db, user_id=1, kind="recharge", amount_cents=balance,
                           idem_key="topup")
    db.commit()


def _cells(db, n: int, *, days_ago: int):
    at = datetime.utcnow() - timedelta(days=days_ago)
    for i in range(n):
        db.add(AiTelemetryResponseORM(run_id=1, topic_id=1, engine="qwen",
                                      query=f"q{i}", answer="a", created_at=at))
    db.commit()


@pytest.fixture(autouse=True)
def enforce(monkeypatch):
    monkeypatch.setenv("GEO_BATCH_CHARGE_MODE", "enforce")
    monkeypatch.setenv("GEO_TOPIC_CHARGE_ENABLED", "1")
    monkeypatch.setenv("GEO_BATCH_PREPAY", "0")   # see module docstring: the two are mutually exclusive


def test_closed_day_is_charged_once(db):
    _setup(db)
    _cells(db, 10, days_ago=1)
    before = balance_service.get_balance(db, 1)

    stat = batch_billing.settle_closed_days(db)
    assert stat["cells"] == 10
    assert balance_service.get_balance(db, 1) == before - 20      # 10 cells x 2 cents

    batch_billing.settle_closed_days(db)                          # catch-up run
    assert balance_service.get_balance(db, 1) == before - 20, "补跑不能重扣"


def test_today_is_not_settled(db):
    """The current day is still being written -- settling would miss the second half of the day."""
    _setup(db)
    _cells(db, 10, days_ago=0)
    before = balance_service.get_balance(db, 1)
    stat = batch_billing.settle_closed_days(db)
    assert stat["cells"] == 0
    assert balance_service.get_balance(db, 1) == before


def test_shadow_mode_only_counts(db, monkeypatch):
    """At launch, shadow-run for a week and cross-check estimates against llm_call_log before switching to enforce."""
    monkeypatch.setenv("GEO_BATCH_CHARGE_MODE", "shadow")
    _setup(db)
    _cells(db, 10, days_ago=1)
    before = balance_service.get_balance(db, 1)
    stat = batch_billing.settle_closed_days(db)
    assert stat["cells"] == 10 and stat["charged_cents"] == 0
    assert balance_service.get_balance(db, 1) == before


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_topics_are_skipped(db, role):
    _setup(db, role=role)
    _cells(db, 10, days_ago=1)
    before = balance_service.get_balance(db, 1)
    stat = batch_billing.settle_closed_days(db)
    assert stat["skipped"] == 1
    assert balance_service.get_balance(db, 1) == before


def test_insufficient_is_recorded_not_rolled_back(db):
    """A failed post-paid charge can't roll back batches already run -- record it; the telemetry-side gate blocks the next round."""
    _setup(db, balance=0)
    _cells(db, 10000, days_ago=1)          # 200 CNY, far above the balance
    stat = batch_billing.settle_closed_days(db)
    assert stat["insufficient"] == 1
    assert stat["charged_cents"] == 0


# ── Billing start date: switching to enforce doesn't retroactively charge the shadow period ────────────────────────────────

def _bj_days_ago(n: int) -> str:
    """Beijing day n days ago. Fabricated created_at is UTC; the definition must match the settler."""
    from datetime import timezone
    bj = timezone(timedelta(hours=8))
    return (datetime.now(bj) - timedelta(days=n)).strftime("%Y-%m-%d")


def test_days_before_start_date_are_not_charged(db, monkeypatch):
    """Cells before the start date aren't charged a cent -- the only guarantee of no back-charging on the day enforce is switched on."""
    _setup(db)
    _cells(db, 10, days_ago=3)                       # before the start date
    _cells(db, 5, days_ago=1)                        # after the start date
    monkeypatch.setenv("GEO_BATCH_BILLING_START_DATE", _bj_days_ago(2))
    before = balance_service.get_balance(db, 1)

    stat = batch_billing.settle_closed_days(db)

    assert stat["before_start"] == 1, "3 天前那一天应被起始日挡下"
    assert stat["cells"] == 5, "只结起始日当天及之后"
    assert balance_service.get_balance(db, 1) == before - 10      # 5 cells x 2 cents


def test_start_date_beats_lookback(db, monkeypatch):
    """However large lookback is, it can't reach before the start date -- that's the point of the safety valve."""
    _setup(db)
    _cells(db, 100, days_ago=6)
    monkeypatch.setenv("GEO_BATCH_SETTLE_LOOKBACK_DAYS", "30")
    monkeypatch.setenv("GEO_BATCH_BILLING_START_DATE", _bj_days_ago(2))
    before = balance_service.get_balance(db, 1)

    stat = batch_billing.settle_closed_days(db)

    assert stat["cells"] == 0
    assert balance_service.get_balance(db, 1) == before, "一分都不该动"


def test_no_start_date_keeps_old_behaviour(db, monkeypatch):
    """No start date set = behavior byte-identical to before this gate was added; existing deployments unchanged."""
    _setup(db)
    _cells(db, 10, days_ago=1)
    monkeypatch.delenv("GEO_BATCH_BILLING_START_DATE", raising=False)
    before = balance_service.get_balance(db, 1)

    stat = batch_billing.settle_closed_days(db)

    assert stat["before_start"] == 0
    assert stat["start_date"] is None
    assert balance_service.get_balance(db, 1) == before - 20


def test_garbage_start_date_is_ignored_not_fatal(db, monkeypatch):
    """A malformed value must not crash the settler -- better fall back to "no start date" than not settle the batch at all."""
    _setup(db)
    _cells(db, 10, days_ago=1)
    monkeypatch.setenv("GEO_BATCH_BILLING_START_DATE", "2026/08/25")
    before = balance_service.get_balance(db, 1)

    stat = batch_billing.settle_closed_days(db)

    assert stat["start_date"] is None
    assert balance_service.get_balance(db, 1) == before - 20


def test_failed_and_retried_cells_are_not_billed(db):
    """2026-09-16: charge only for delivered cells. Failed rows aren't charged; multiple retry rows for the same cell count as one cell."""
    at = datetime.utcnow() - timedelta(days=1)
    for i in range(10):
        db.add(AiTelemetryResponseORM(run_id=1, topic_id=1, engine="qwen",
                                      query=f"q{i}", answer="a", created_at=at))
    for i in range(7):                                     # 7 cells each failing 3 times -> 0 cells
        for _ in range(3):
            db.add(AiTelemetryResponseORM(run_id=1, topic_id=1, engine="doubao",
                                          query=f"q{i}", answer="", error="empty_answer",
                                          created_at=at))
    db.add(AiTelemetryResponseORM(run_id=1, topic_id=1, engine="qwen",      # q0 succeeds once more -> still 1 cell
                                  query="q0", answer="a", created_at=at))
    db.commit()
    stat = batch_billing.settle_closed_days(db)
    assert stat["cells"] == 10
