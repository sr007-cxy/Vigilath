"""Audit scope tiers (full / lite) and self-healing of zombie reports.

lite: lead reports run only the four site-side items and skip the heavy engine queue stage -- requirements in
docs/08-独立模块/SalesAgent/02-需求分析/找客户推荐与邮件流程.md §7.3.

Zombie self-healing: background work runs in a daemon thread and is killed whenever the backend restarts, leaving status stuck at
generating. On 2026-08-23 a restart interrupted 5 reports; at the next restart 20 minutes later they had
only been idle 21 minutes (under the 25-minute threshold) and weren't swept, and there was no restart after that -- "diagnosing" hung on the workbench
for 17 hours. Sweeping at startup inevitably misses the batch the restart itself just created.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_report_scope.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentLeadRecommendationORM
from agents.sales_agent.service import running_report_progress
from agents.sales_agent.service.reports import LEAD_REPORT_SCOPE
from geo.database import Base
from geo.models.full_diagnosis import FullDiagnosisReportORM
from geo.services.full_diagnosis_generator import (
    DEFAULT_SCOPE,
    HEAVY_MODES,
    NOT_SELECTED,
    SCOPE_MODES,
    STALE_THRESHOLD,
    SUB_MODES,
    modes_for_scope,
)

USER = 1


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[FullDiagnosisReportORM.__table__,
                                             SalesAgentLeadRecommendationORM.__table__])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


# ─────────────── Tiers ───────────────


def test_lite_is_exactly_the_four_site_side_checks():
    assert modes_for_scope("lite") == ["base", "aeo", "crawlTest", "authority"]


def test_lite_touches_no_heavy_engine_mode():
    """The whole point of lite: not a single item enters the heavy engine stage -- that stage is the sole source of waiting and hangs."""
    assert not set(modes_for_scope("lite")) & set(HEAVY_MODES)


def test_full_is_still_all_eight():
    assert modes_for_scope("full") == SUB_MODES


def test_unknown_and_empty_scope_fall_back_to_full():
    """Unknown tiers should run more rather than silently run less -- running less sends an incomplete report to the customer as complete."""
    for bad in ("", "  ", "LITEE", "unknown", None):
        assert modes_for_scope(bad) == SUB_MODES


def test_scope_name_is_case_insensitive():
    assert modes_for_scope("LITE") == modes_for_scope("lite")


def test_default_scope_is_full():
    """The three existing callers that don't pass scope must behave exactly the same."""
    assert DEFAULT_SCOPE == "full" and SCOPE_MODES[DEFAULT_SCOPE] == SUB_MODES


def test_lead_report_uses_lite():
    assert LEAD_REPORT_SCOPE == "lite"


# ─────────────── Zombie self-healing ───────────────


def _report(db, rid, *, status="generating", idle_minutes=0.0, scope="full"):
    steps = [{"mode": m, "status": "pending"} for m in modes_for_scope(scope)]
    db.add(FullDiagnosisReportORM(
        id=rid, user_id=USER, status=status, scope=scope,
        url="https://x.com", brand_name="X",
        progress_json=json.dumps({"stage": "checks", "done": 0,
                                  "total": len(steps), "steps": steps}),
        created_at=datetime.utcnow() - timedelta(hours=2),
        updated_at=datetime.utcnow() - timedelta(minutes=idle_minutes),
    ))
    db.commit()


def test_stale_generating_row_is_healed_to_failed(db):
    _report(db, 1, idle_minutes=STALE_THRESHOLD.total_seconds() / 60 + 1)
    [item] = running_report_progress(db, USER)
    assert item["status"] == "failed"
    assert db.get(FullDiagnosisReportORM, 1).status == "failed"


def test_live_generating_row_is_left_alone(db):
    """Heartbeat is every 60s; a row just touched is still running and must not be killed by mistake."""
    _report(db, 1, idle_minutes=1)
    [item] = running_report_progress(db, USER)
    assert item["status"] == "generating"
    assert db.get(FullDiagnosisReportORM, 1).status == "generating"


def test_every_zombie_in_the_batch_is_healed_not_just_the_first(db):
    """One restart interrupts a batch at once (5 that time). Writing it as any(generator) short-circuits on the first one."""
    stale = STALE_THRESHOLD.total_seconds() / 60 + 1
    for rid in (1, 2, 3, 4, 5):
        _report(db, rid, idle_minutes=stale)
    running_report_progress(db, USER)
    assert [db.get(FullDiagnosisReportORM, r).status for r in (1, 2, 3, 4, 5)] == ["failed"] * 5


def test_healed_row_carries_a_user_facing_reason(db):
    _report(db, 1, idle_minutes=STALE_THRESHOLD.total_seconds() / 60 + 1)
    running_report_progress(db, USER)
    assert "重新生成" in (db.get(FullDiagnosisReportORM, 1).error or "")


def test_not_selected_is_its_own_status(db):
    """"Not audited this time" must not be lumped with "audit failed"/"skipped"; the frontend shows them separately."""
    assert NOT_SELECTED not in ("failed", "skipped", "ok", "pending", "running")
