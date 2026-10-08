"""Pre-action gate.

Before this, `usage_guardrail_check` was an empty function -- any account could start unlimited real diagnoses
(8-20 minutes each, consuming audit quota and LLM spend). This pins three verdicts and the counting metric definition.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_guard.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentComplianceLogORM
from agents.sales_agent.runtime.guard import (
    SalesAgentGuardDenied,
    bj_day_range_utc,
    check_user,
    daily_limit_for,
    disabled_actions,
)
from geo.database import Base
from geo.models.user import UserORM

USER_ID = 1


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[UserORM.__table__, SalesAgentComplianceLogORM.__table__])
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER_ID, email="a@b.c", password_hash="x", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _allowed_rows(db, action="recommend_companies"):
    return (
        db.query(SalesAgentComplianceLogORM)
        .filter(SalesAgentComplianceLogORM.action == action, SalesAgentComplianceLogORM.decision == "allowed")
        .count()
    )


def test_unlisted_action_passes_without_counting(db):
    """Read tools don't go through the gate and shouldn't flood the compliance log with rows."""
    check_user(db, USER_ID, "get_current_customer_profile")
    assert db.query(SalesAgentComplianceLogORM).count() == 0


def test_allowed_action_is_counted(db):
    check_user(db, USER_ID, "recommend_companies")
    check_user(db, USER_ID, "recommend_companies")
    assert _allowed_rows(db) == 2


def test_daily_limit_blocks_and_logs(db, monkeypatch):
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "2")
    check_user(db, USER_ID, "recommend_companies")
    check_user(db, USER_ID, "recommend_companies")
    with pytest.raises(SalesAgentGuardDenied) as e:
        check_user(db, USER_ID, "recommend_companies")

    assert e.value.status_code == 403
    blocked = db.query(SalesAgentComplianceLogORM).filter(SalesAgentComplianceLogORM.decision == "blocked").one()
    assert "2/2" in blocked.reason


def test_denial_message_hides_internal_counts(db, monkeypatch):
    """Messages to users must not reveal field names, counts or action names."""
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "1")
    check_user(db, USER_ID, "recommend_companies")
    with pytest.raises(SalesAgentGuardDenied) as e:
        check_user(db, USER_ID, "recommend_companies")
    text = str(e.value)
    assert "recommend_companies" not in text and "limit" not in text and "1/1" not in text


def test_yesterdays_usage_does_not_count(db, monkeypatch):
    """Quota resets on the Beijing day boundary; exhausted yesterday must be allowed today."""
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "1")
    start, _ = bj_day_range_utc()
    db.add(SalesAgentComplianceLogORM(user_id=USER_ID, action="recommend_companies", decision="allowed",
                                      created_at=start - timedelta(minutes=1)))
    db.commit()
    check_user(db, USER_ID, "recommend_companies")


def test_other_users_usage_does_not_count(db, monkeypatch):
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "1")
    db.add(SalesAgentComplianceLogORM(user_id=USER_ID + 99, action="recommend_companies", decision="allowed",
                                      created_at=datetime.utcnow()))
    db.commit()
    check_user(db, USER_ID, "recommend_companies")


def test_disabled_action_blocked(db, monkeypatch):
    monkeypatch.setenv("SALES_AGENT_DISABLE_ACTIONS", "discover_companies, recommend_companies")
    assert disabled_actions() == {"discover_companies", "recommend_companies"}
    with pytest.raises(SalesAgentGuardDenied):
        check_user(db, USER_ID, "recommend_companies")
    check_user(db, USER_ID, "generate_outreach_copy")  # disabling one doesn't affect other actions


def test_inactive_account_blocked(db):
    db.query(UserORM).filter(UserORM.id == USER_ID).update({"is_active": False})
    db.commit()
    with pytest.raises(SalesAgentGuardDenied):
        check_user(db, USER_ID, "recommend_companies")


def test_zero_limit_means_unlimited(db, monkeypatch):
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "0")
    assert daily_limit_for("recommend_companies") == 0
    for _ in range(50):
        check_user(db, USER_ID, "recommend_companies")
    assert _allowed_rows(db) == 0  # unlimited also writes no counter rows


def test_bad_env_falls_back_to_default(db, monkeypatch):
    monkeypatch.setenv("SALES_AGENT_DAILY_LIMIT_RECOMMEND_COMPANIES", "abc")
    assert daily_limit_for("recommend_companies") == 30


def test_missing_db_or_user_is_noop():
    check_user(None, USER_ID, "recommend_companies")
    check_user(object(), None, "recommend_companies")
