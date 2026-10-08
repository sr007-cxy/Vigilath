"""Whether an email may reference a report must consider both the real report and the current brand profile's report capability.

2026-08-27 production: under the same account, a lead/company matched a historical audit report, but the current sending brand's profile
had no "audit/diagnose/report" capability. The old logic only looked at `report_context.has_real_detection`, so it still took
the report email branch and wrote the body from report content; the user saw "this brand of mine doesn't do report monitoring, why is the email
based on report content".

Correct rule: report email = real report exists AND report belongs to this company AND current brand `report_capable`.
When the brand profile lacks report capability, even if a report matches in the DB, only the no-report intro email is allowed.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_outreach_report_capability_gate.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentReportShareORM,
    SalesAgentSellerProfileORM,
)
from agents.sales_agent.service.outreach import _lead_report_context_for_outreach
from geo.database import Base
from geo.models.full_diagnosis import FullDiagnosisReportORM
from geo.models.user import UserORM

USER = 3400001


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="outreach-report@test.local", password_hash="x", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _brand(db, *, report_capable: bool) -> SalesAgentSellerProfileORM:
    row = SalesAgentSellerProfileORM(
        user_id=USER,
        name="GEO-TAG" if report_capable else "内容顾问品牌",
        brand_key="geo_tag" if report_capable else "content_advisor",
        report_capable=report_capable,
        service_capabilities_json=json.dumps(["AI 可见性检测"] if report_capable else ["品牌内容策略"], ensure_ascii=False),
        service_scope_json=json.dumps(["GEO 检测"] if report_capable else ["内容咨询"], ensure_ascii=False),
        company_size="1-10 名员工",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _lead_with_real_report(db, brand: SalesAgentSellerProfileORM):
    company = SalesAgentCompanyORM(
        name="星迈科技",
        website="https://example.com",
        normalized_domain="example.com",
        canonical_name="星迈科技",
        data_status="verified",
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=brand.id,
        status="completed",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    report = FullDiagnosisReportORM(
        id=88001,
        user_id=USER,
        status="ready",
        scope="lite",
        url="https://example.com",
        brand_name="星迈科技",
        scores_json=json.dumps({"total": 72, "grade": "B", "dimensions": []}, ensure_ascii=False),
        narrative_json=json.dumps({"executive_summary": "官网 AI 可见性有提升空间"}, ensure_ascii=False),
        subresults_json="{}",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    lead = SalesAgentLeadRecommendationORM(
        user_id=USER,
        company_id=company.id,
        campaign_id=campaign.id,
        source_report_table="full_diagnosis_reports",
        source_report_id=str(report.id),
        report_snapshot="{}",
        score=90,
        rank=1,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return company, lead, report


def test_real_report_is_not_enough_when_the_current_brand_cannot_sell_reports(db):
    """The old logic returned True here and also created a public report share along the way."""
    brand = _brand(db, report_capable=False)
    company, lead, _report = _lead_with_real_report(db, brand)

    report, share, can_reference = _lead_report_context_for_outreach(
        db,
        USER,
        lead,
        company,
        seller_profile_id=brand.id,
        commit_share=True,
        public_base_url="https://www.vigilath.cn",
    )

    assert report["has_real_detection"] is True, "真实报告确实存在,问题不在报告匹配"
    assert can_reference is False, "当前品牌画像没有报告能力,邮件不许走报告分支"
    assert share == {}, "不允许引用报告时,不该顺手创建公开报告链接"
    assert db.query(SalesAgentReportShareORM).count() == 0


def test_existing_share_does_not_leak_when_brand_loses_report_capability(db):
    """Existing shares must not bypass the brand capability gate either.

    The old code first got the active share, then on commit refresh filled share_payload back in;
    seeing public_url, the API layer might still treat it as a report email.
    """
    brand = _brand(db, report_capable=False)
    company, lead, report_row = _lead_with_real_report(db, brand)
    db.add(SalesAgentReportShareORM(
        user_id=USER,
        lead_id=lead.id,
        company_id=company.id,
        source_report_table="full_diagnosis_reports",
        source_report_id=str(report_row.id),
        status="active",
        public_token="old-share-token",
    ))
    db.commit()

    _report, share, can_reference = _lead_report_context_for_outreach(
        db,
        USER,
        lead,
        company,
        seller_profile_id=brand.id,
        commit_share=True,
        public_base_url="https://www.vigilath.cn",
    )

    assert can_reference is False
    assert share == {}
    assert db.query(SalesAgentReportShareORM).count() == 1, "存量链接不用删,但本次上下文不能带出去"


def test_report_email_is_allowed_only_when_the_current_brand_is_report_capable(db):
    brand = _brand(db, report_capable=True)
    company, lead, _report = _lead_with_real_report(db, brand)

    _report, share, can_reference = _lead_report_context_for_outreach(
        db,
        USER,
        lead,
        company,
        seller_profile_id=brand.id,
        commit_share=True,
        public_base_url="https://www.vigilath.cn",
    )

    assert can_reference is True
    assert share.get("public_url")
    assert db.query(SalesAgentReportShareORM).count() == 1
