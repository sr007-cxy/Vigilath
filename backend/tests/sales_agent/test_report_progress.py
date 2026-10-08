"""Data plane of the long-task progress stream.

Report generation takes 8-20 minutes, and the UI used to show only an unchanging "diagnosis in progress" from start to finish;
knowing where it was required clicking refresh. Design in
docs/08-独立模块/SalesAgent/03-系统设计/产品体验与工程优化.md §8b.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_report_progress.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentLeadRecommendationORM
from agents.sales_agent.service import REPORT_STEP_LABELS, running_report_progress
from geo.database import Base
from geo.models.full_diagnosis import FullDiagnosisReportORM

USER, OTHER = 1, 2


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


def _report(db, rid, user=USER, status="generating", done=3, total=8, minutes_ago=6, updated_ago=0):
    steps = [{"mode": m, "status": "ok" if i < done else "pending"}
             for i, m in enumerate(["base", "aeo", "crawlTest", "authority",
                                    "visibility", "entity", "competitiveIntel", "sentiment"])]
    db.add(FullDiagnosisReportORM(
        id=rid, user_id=user, status=status, url="https://x.com", brand_name="X",
        progress_json=json.dumps({"stage": "checks", "done": done, "total": total, "steps": steps}),
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
        updated_at=datetime.utcnow() - timedelta(seconds=updated_ago),
    ))
    db.commit()


def test_running_report_is_reported(db):
    _report(db, 1)
    [item] = running_report_progress(db, USER)
    assert item["report_id"] == 1 and item["status"] == "generating"
    assert (item["done"], item["total"]) == (3, 8)


def test_current_step_is_human_readable(db):
    """progress_json stores mode (authority); showing it directly exposes internal names to users."""
    _report(db, 1, done=3)
    [item] = running_report_progress(db, USER)
    assert item["current_step"] == REPORT_STEP_LABELS["authority"]
    assert item["current_step"] not in ("authority", "")


def test_elapsed_comes_from_created_at(db):
    _report(db, 1, minutes_ago=6)
    [item] = running_report_progress(db, USER)
    assert 5 * 60 <= item["elapsed_seconds"] <= 7 * 60


def test_lead_ids_are_attached(db):
    """The frontend renders by lead row; without lead_id it can't tell which row this progress updates."""
    _report(db, 7)
    db.add(SalesAgentLeadRecommendationORM(id=11, user_id=USER, company_id=1, source_report_id="7"))
    db.add(SalesAgentLeadRecommendationORM(id=12, user_id=USER, company_id=2, source_report_id="7"))
    db.commit()
    [item] = running_report_progress(db, USER)
    assert sorted(item["lead_ids"]) == [11, 12]


def test_other_users_reports_excluded(db):
    _report(db, 1, user=OTHER)
    assert running_report_progress(db, USER) == []


def test_just_finished_report_still_reported(db):
    """Finished ones must be pushed once too, or the client's last view is forever stuck at 7/8."""
    _report(db, 1, status="ready", done=8, updated_ago=10)
    [item] = running_report_progress(db, USER)
    assert item["status"] == "ready" and item["done"] == 8


def test_long_finished_report_dropped(db):
    """Ones finished long ago are no longer pushed, so each turn doesn't refresh a pile of history."""
    _report(db, 1, status="ready", done=8, updated_ago=9999)
    assert running_report_progress(db, USER) == []


def test_missing_progress_does_not_crash(db):
    """When progress_json isn't written yet (row just created), it must still emit one entry, not raise."""
    db.add(FullDiagnosisReportORM(id=1, user_id=USER, status="generating", url="https://x.com",
                                  progress_json="{}", created_at=datetime.utcnow(),
                                  updated_at=datetime.utcnow()))
    db.commit()
    [item] = running_report_progress(db, USER)
    assert (item["done"], item["total"]) == (0, 0) and item["current_step"] == ""
