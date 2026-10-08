"""Credit pricing system revamp -- unit price table, daily cap, full diagnosis charged by usage.

Corresponds to `docs/02-需求分析/计费与支付/需求文档-积分计价体系改版.md`. Each of the three has an edge that's easy to trip on:

1. **Unit prices**: env fallbacks must match the requirements table. The table (`balance_prices`) is authoritative, but when a row is missing
   it falls back to these defaults -- wrong defaults = the whole site charges wrong prices.
2. **Daily cap**: a capacity guardrail, not a tier entitlement, so even "unlimited monthly" tiers must be stopped by it.
3. **Full diagnosis**: uses run-now preview and doesn't write `ai_telemetry_responses`, so the daily settler can't see it;
   it must be charged on the spot by "words x engines"; if the charge fails, **skip the heavy engine sections instead of failing the whole report**.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.database import Base
from geo.models.balance import (
    PRICE_BATCH_CELL, PRICE_COMPLIANCE_SCAN, PRICE_CONTENT_DOC,
    PRICE_CONTENT_IMAGE, PRICE_RETEST_PROBE, PRICE_SENTIMENT_DAILY,
    PRICE_SENTIMENT_ONCE, PRICE_SOLUTION, BalanceLedgerORM, BalancePriceORM, UserBalanceORM,
)
from geo.models.membership import (
    Membership, UserCheckUsageDailyORM, UserCheckUsageORM,
)
from geo.models.user import UserORM
from geo.services import balance_service, quota_service
from geo.utils.error_handler import AppException


# ── Unit prices ────────────────────────────────────────────────────────────────
def test_env_fallback_prices_match_the_spec():
    """The table in section 2 of the requirements doc. To change prices change balance_prices; changing this changes the site-wide fallback."""
    expected = {
        PRICE_BATCH_CELL: 4,            # one word x one engine
        PRICE_CONTENT_DOC: 300,         # per draft
        PRICE_CONTENT_IMAGE: 50,
        PRICE_COMPLIANCE_SCAN: 50,
        PRICE_RETEST_PROBE: 20,
        PRICE_SENTIMENT_ONCE: 1000,     # one quick check
        PRICE_SENTIMENT_DAILY: 500,     # one day of daily monitoring
        PRICE_SOLUTION: 1000,           # one GEO strategy optimization solution generation (10 CNY)
    }
    actual = {k: v[1] for k, v in balance_service._PRICE_ENV.items()}
    assert actual == expected


def test_full_diagnosis_costs_800_points_at_spec_scale():
    """40 words x 5 engines x 4 = 800 credits, matching the requirements table."""
    assert 40 * 5 * balance_service._PRICE_ENV[PRICE_BATCH_CELL][1] == 800


# ── Daily cap ──────────────────────────────────────────────────────────────
@pytest.fixture()
def quota_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserCheckUsageORM.__table__, UserCheckUsageDailyORM.__table__,
    ])
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(quota_service, "SessionLocal", Session)
    return Session


def _tier(quota: int) -> Membership:
    return Membership(
        id=1, slug="t", name="t", price=0, period="/月", description="",
        features=[], not_included=[], monthly_check_quota=quota,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )


def test_daily_cap_stops_unlimited_tiers_too(quota_db, monkeypatch):
    """Unlimited monthly != can drain the engine pool in one day. growth's "unlimited" is really <= 10/day."""
    monkeypatch.setenv("GEO_DAILY_CHECK_CAP", "10")
    unlimited = _tier(0)
    for _ in range(10):
        quota_service.check_and_increment_quota(1, unlimited)
    with pytest.raises(AppException) as e:
        quota_service.check_and_increment_quota(1, unlimited)
    assert e.value.status_code == 429


def test_monthly_quota_still_bites_first(quota_db, monkeypatch):
    monkeypatch.setenv("GEO_DAILY_CHECK_CAP", "10")
    pro = _tier(3)
    for _ in range(3):
        quota_service.check_and_increment_quota(1, pro)
    with pytest.raises(AppException) as e:
        quota_service.check_and_increment_quota(1, pro)
    assert "Monthly" in str(e.value.message)


def test_both_counters_advance_together(quota_db, monkeypatch):
    """Incrementing only one causes "daily rolled but monthly didn't" bookkeeping errors."""
    monkeypatch.setenv("GEO_DAILY_CHECK_CAP", "10")
    quota_service.check_and_increment_quota(1, _tier(0))
    quota, used, _rem, _ym, daily = quota_service.get_usage_full(1, _tier(0))
    assert used == 1 and daily["used"] == 1 and daily["cap"] == 10


def test_cap_zero_means_unlimited(quota_db, monkeypatch):
    """Emergency gate opening: with GEO_DAILY_CHECK_CAP=0 the daily cap doesn't apply."""
    monkeypatch.setenv("GEO_DAILY_CHECK_CAP", "0")
    for _ in range(25):
        quota_service.check_and_increment_quota(1, _tier(0))
    _q, _u, _r, _ym, daily = quota_service.get_usage_full(1, _tier(0))
    assert daily["remaining"] == -1


# ── Full diagnosis charged by usage ──────────────────────────────────────────────────────
@pytest.fixture()
def diag_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        UserORM.__table__, UserBalanceORM.__table__, BalanceLedgerORM.__table__,
        BalancePriceORM.__table__,
    ])
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role="user"))
    db.add(UserORM(id=2, email="ops@x.z", password_hash="x", role="operator"))
    db.add(BalancePriceORM(key="batch_cell", price_cents=4, enabled=1,
                           updated_at=datetime.utcnow()))
    db.commit()
    balance_service.get_or_create_account(db, 1)
    balance_service.credit(db, user_id=1, kind="recharge", amount_cents=100000,
                           idem_key="topup")
    db.commit()
    db.close()

    import geo.services.full_diagnosis_generator as gen
    monkeypatch.setattr(gen, "SessionLocal", Session, raising=False)
    monkeypatch.setattr("geo.database.SessionLocal", Session)
    monkeypatch.setattr("geo.services.full_diagnosis_competitive._engines",
                        lambda: ["deepseek", "doubao", "qwen", "wenxin", "yuanbao"])
    return Session


def _charge(report_id: int, user_id: int, n: int) -> str:
    from geo.services.full_diagnosis_generator import _charge_full_diagnosis
    return _charge_full_diagnosis(report_id, user_id, query_count=n)


def _balance(Session, user_id: int) -> int:
    db = Session()
    try:
        return balance_service.get_balance(db, user_id)
    finally:
        db.close()


def test_charges_queries_times_engines(diag_db):
    before = _balance(diag_db, 1)
    assert _charge(11, 1, 15) == ""
    # 15 words x 5 engines x 4 = 300 credits
    assert _balance(diag_db, 1) == before - 300


def test_retry_of_the_same_report_charges_once(diag_db):
    before = _balance(diag_db, 1)
    _charge(12, 1, 15)
    _charge(12, 1, 15)
    assert _balance(diag_db, 1) == before - 300


def test_staff_runs_free(diag_db):
    """Ops accounts aren't charged a single cell -- internal/demo reports all live there."""
    assert _charge(13, 2, 40) == ""
    db = diag_db()
    try:
        assert db.query(BalanceLedgerORM).filter(
            BalanceLedgerORM.user_id == 2).count() == 0
    finally:
        db.close()


def test_broke_user_gets_a_reason_not_an_exception(diag_db):
    """A failed charge **doesn't raise**: the site-side sections already finished and are worth keeping for the user."""
    db = diag_db()
    try:
        balance_service.charge(db, user_id=1, kind="batch",
                               amount_cents=balance_service.get_balance(db, 1),
                               idem_key="drain")
        db.commit()
    finally:
        db.close()
    reason = _charge(14, 1, 40)
    assert "积分不足" in reason and "800" in reason
