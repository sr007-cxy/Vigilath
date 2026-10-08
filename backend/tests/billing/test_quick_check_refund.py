"""Whether a quick test task refunds quota after it finishes -- the criterion is "did anything answer", not "is status failed".

Separate from test_anon_quota_refund.py: that one tests the refund itself, this one tests **when to refund**.
One case pinned per outcome:
  - batch run center unreachable    -> not a single engine asked -> refund
  - finished but every engine errored -> status=done, answered=0 -> also refund
  - at least one engine answered     -> money was really spent -> no refund (refunding would open a free endpoint-abuse hole)

    venv/bin/python -m pytest tests/billing/test_quick_check_refund.py -q   # cwd = backend/
"""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from geo.api import quick_check as qc
from geo.database import Base
from geo.models.anon_usage import AnonQuickTaskORM, AnonUsageORM

DEV, IP = "dev_qc", "203.0.113.9"


@pytest.fixture
def env(monkeypatch):
    """In-memory DB + one running task row. Returns (Session, refunds record list)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[AnonUsageORM.__table__, AnonQuickTaskORM.__table__])
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(qc, "SessionLocal", Session)
    monkeypatch.setattr(qc, "_build_queries", lambda b, t: ["某品类推荐"])
    # The batch run center address and engine list are imported inside _run_task at call time; patch the source module's attributes
    from geo.services import full_diagnosis_competitive as fdc

    monkeypatch.setattr(fdc, "_engines", lambda: ["deepseek", "doubao", "yuanbao"])

    refunds: list = []
    real_refund = qc.anon_quota.refund

    def spy(db, feature, device_id, ip):
        refunds.append((feature, device_id, ip))
        return real_refund(db, feature, device_id, ip)

    monkeypatch.setattr(qc.anon_quota, "refund", spy)

    s = Session()
    s.add(AnonQuickTaskORM(task_id="t1", feature="mention", brand="某品牌",
                           device_id=DEV, ip=IP, status="running"))
    qc.anon_quota.consume(s, "mention", DEV, IP, note="某品牌")   # deducted at start
    s.commit()
    s.close()
    return Session, refunds


def _row(Session):
    s = Session()
    try:
        r = s.query(AnonQuickTaskORM).filter_by(task_id="t1").one()
        return r.status, json.loads(r.result_json or "{}")
    finally:
        s.close()


def _left(Session) -> int:
    s = Session()
    try:
        st = qc.anon_quota.check(s, "mention", DEV, IP)
        return st["device_limit"] - st["device_used"]
    finally:
        s.close()


def test_telemetry_unreachable_refunds(env, monkeypatch):
    Session, refunds = env
    from geo.services import telemetry_client

    def boom(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(telemetry_client, "post_run_now", boom)
    qc._run_task("t1", "某品牌", ["某品类"])

    status, stored = _row(Session)
    assert status == "failed"
    assert stored["quota_refunded"] is True
    assert refunds and _left(Session) == 2, "一个引擎都没问到,额度必须回来"


def test_all_engines_errored_refunds_even_though_done(env, monkeypatch):
    """Status is done but every engine errored -- the user got nothing, so refund as well.

    This one is the easiest to miss: an implementation that only checks status == 'failed' lets it through.
    """
    Session, refunds = env
    from geo.services import telemetry_client

    monkeypatch.setattr(telemetry_client, "post_run_now",
                        lambda *a, **kw: [{"engine": "doubao", "query": "q",
                                           "error": "engine busy"}])
    qc._run_task("t1", "某品牌", ["某品类"])

    status, stored = _row(Session)
    assert status == "done"
    assert stored["answered"] == 0
    assert stored["quota_refunded"] is True
    assert _left(Session) == 2


def test_one_real_answer_keeps_the_charge(env, monkeypatch):
    """An engine really answered = money really spent. Refunding would make it a free unlimited "retry on failure" channel."""
    Session, refunds = env
    from geo.services import telemetry_client

    monkeypatch.setattr(telemetry_client, "post_run_now",
                        lambda *a, **kw: [
                            {"engine": "doubao", "query": "q", "error": "busy"},
                            {"engine": "deepseek", "query": "q",
                             "answer": "推荐 A、B、某品牌", "citations": []},
                        ])
    qc._run_task("t1", "某品牌", ["某品类"])

    status, stored = _row(Session)
    assert status == "done"
    assert stored["answered"] == 1
    assert stored["quota_refunded"] is False
    assert refunds == [], "有应答就不该走退还"
    assert _left(Session) == 1
