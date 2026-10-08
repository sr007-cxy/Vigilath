"""Query "which companies were recommended".

Unlike recommend_companies, this **only reads history and doesn't re-run filtering** -- re-running when the user asks about "that last batch"
is slow, may give different results, and wastes a quota.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_recommended_lookup.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import SalesAgentCompanyORM, SalesAgentLeadRecommendationORM
from agents.sales_agent.service import agent_recommended_companies
from geo.database import Base

USER, OTHER = 1, 2


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SalesAgentCompanyORM.__table__, SalesAgentLeadRecommendationORM.__table__])
    s = sessionmaker(bind=engine)()
    s.add_all([
        SalesAgentCompanyORM(id=1, name="示例清洁科技", industry="工业制造", region="长三角", website="https://cleantech.example", employee_size="101-500 名员工"),
        SalesAgentCompanyORM(id=2, name="Acme Cloud", industry="企业服务", region="北京", website=""),
        SalesAgentCompanyORM(id=3, name="旧客户", industry="工业制造", region="长三角", website="https://old.com"),
    ])
    now = datetime.utcnow()
    s.add_all([
        SalesAgentLeadRecommendationORM(id=11, user_id=USER, company_id=1, score=86, status="Diagnosed",
                                        source_report_id="123", created_at=now),
        SalesAgentLeadRecommendationORM(id=12, user_id=USER, company_id=2, score=64, status="New", created_at=now),
        SalesAgentLeadRecommendationORM(id=13, user_id=USER, company_id=3, score=50, status="New",
                                        created_at=now - timedelta(days=40)),
        SalesAgentLeadRecommendationORM(id=14, user_id=OTHER, company_id=1, score=90, status="New", created_at=now),
    ])
    s.commit()
    try:
        yield s
    finally:
        s.close()


def test_lists_history(db):
    out = agent_recommended_companies(db, USER)
    assert out["total_matched"] == 3
    assert out["summary"]["with_report"] == 1


def test_only_current_user(db):
    assert all(x["id"] != 14 for x in agent_recommended_companies(db, USER)["leads"])


def test_filter_by_company_name(db):
    out = agent_recommended_companies(db, USER, company_name="示例清洁")
    assert out["total_matched"] == 1 and out["leads"][0]["match_score"] == 86


def test_filter_by_industry_and_region(db):
    assert agent_recommended_companies(db, USER, industry="工业制造")["total_matched"] == 2
    assert agent_recommended_companies(db, USER, region="北京")["total_matched"] == 1


def test_filter_by_status(db):
    assert agent_recommended_companies(db, USER, status="Diagnosed")["total_matched"] == 1


def test_filter_by_days(db):
    """The one from 40 days ago must not appear in "last 7 days"."""
    assert agent_recommended_companies(db, USER, days=7)["total_matched"] == 2


def test_filter_by_since_until_exact_window(db):
    """Recommendation history also supports deterministic time windows, so the Agent can answer "how many were recommended yesterday"."""
    now = datetime.utcnow()
    since = now - timedelta(days=1)
    until = now + timedelta(days=1)

    out = agent_recommended_companies(db, USER, since=since, until=until)

    assert out["total_matched"] == 2
    assert out["filters"]["since"]
    assert out["filters"]["until"]


def test_report_flag_surfaces(db):
    row = next(x for x in agent_recommended_companies(db, USER)["companies"] if x["lead_id"] == 11)
    assert row["has_report"] and row["status"] == "Diagnosed"


def test_payload_has_no_evidence_blob(db):
    """The copy shown to the model has trimmed fields: no report snapshot."""
    row = agent_recommended_companies(db, USER)["companies"][0]
    assert "report_snapshot" not in row and "reason" not in row


def test_leads_are_card_shaped(db):
    """`leads` has the same shape as recommend_companies, and the frontend card with pagination/search/details consumes it directly --
    otherwise the model can only hand-copy a table in the body, and listing 100 companies only gets 6 rows (2026-08-23 production)."""
    out = agent_recommended_companies(db, USER)
    lead = out["leads"][0]
    assert set(lead["company"]) >= {"name", "industry", "region", "website"}
    assert lead["company"]["name"]
    assert "match_score" in lead and "report_status" in lead
    # Count fields needed for card rendering
    assert out["actual_count"] == len(out["leads"]) and out["requested_limit"] >= 1


def test_limit_capped(db):
    assert agent_recommended_companies(db, USER, limit=999)["returned"] <= 50


def test_empty_is_explicit(db):
    out = agent_recommended_companies(db, USER, company_name="查无此公司")
    assert out["total_matched"] == 0 and out["leads"] == [] and out["companies"] == []
