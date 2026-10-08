"""Company email send policy: allow generating/sending whenever the contact has a valid email, and rank such contacts first in recommendations.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_email_contact_policy.py -q
"""
from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.discovery import CompanyDiscoveryCandidate, CompanyDiscoveryResult
from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
)
from agents.sales_agent.service.companies import load_company_candidates_from_db
from agents.sales_agent.service.industry_mapping import map_target_industries_to_available
from agents.sales_agent.service.recommend import recommend_companies_workflow
from agents.sales_agent.service.email_send import _resolve_delivery_recipient
from agents.sales_agent.service.outreach import _outreach_guard, _usable_email_contact_for_lead
from geo.database import Base


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_outreach_uses_email_even_when_contact_source_is_unknown():
    """User requirement: having an email is enough to generate mail; stop reporting "unknown origin" because source is not whitelisted."""
    db = _db()
    try:
        company = SalesAgentCompanyORM(id=1, name="星迈科技", source="manual", data_status="active")
        lead = SalesAgentLeadRecommendationORM(id=1, user_id=1, company_id=1)
        contact = SalesAgentCompanyContactORM(
            company_id=1,
            email="sales@example.com",
            source="unknown_external_sheet",
            consent_status="unknown",
            is_reachable=False,
        )
        db.add_all([company, lead, contact])
        db.commit()

        assert _usable_email_contact_for_lead(db, lead).email == "sales@example.com"
        decision, reasons = _outreach_guard(db, 1, lead, contact, "email")
        assert decision == "needs_approval"
        assert "联系方式来源不明或不在授权范围" not in reasons
        resolved_contact, recipient_email, _ = _resolve_delivery_recipient(db, lead)
        assert resolved_contact.id == contact.id
        assert recipient_email == "sales@example.com"
    finally:
        db.close()


def test_default_company_pool_prefers_companies_with_email():
    """When recommending companies, among equal candidates those with a valid email rank ahead of those without."""
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="无邮箱企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="有邮箱企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyContactORM(company_id=2, email="hello@example.com", source="", consent_status="unknown", is_reachable=False),
        ])
        db.commit()

        rows = load_company_candidates_from_db(db, industries=["企业服务"], regions=["上海"], limit=2)

        assert [row["name"] for row in rows[:2]] == ["有邮箱企业", "无邮箱企业"]
        assert rows[0]["email"] == "hello@example.com"
    finally:
        db.close()


def test_default_company_pool_matches_legal_service_industry_from_db():
    """The production company DB industry field is 「法律服务」 ("legal services"): when that filter is selected, prefer legal-services companies from the DB."""
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="通用咨询公司", industry="企业服务", region="上海", source="manual", data_status="active"),
            # The production company DB may only have basic fields; an empty source must not be blocked at the candidate-pool entry.
            SalesAgentCompanyORM(id=2, name="安合商务服务", industry="法律服务", region="安徽", website="https://anhe.example.com", source="", data_status="active"),
            SalesAgentCompanyORM(id=3, name="法衡服务中心", industry="法律服务", region="安徽", website="https://faheng.example.com", source="", data_status="active"),
        ])
        db.commit()

        rows = load_company_candidates_from_db(db, industries=["法律服务"], regions=["安徽"], limit=2)

        assert [row["name"] for row in rows[:2]] == ["法衡服务中心", "安合商务服务"]
    finally:
        db.close()


def test_ai_industry_mapping_selects_real_db_labels(monkeypatch):
    """Industry understanding first lets the AI pick among DB tags, e.g. 「律师」 ("lawyer") maps to the DB's 「法律服务」 ("legal services")."""
    from agents.sales_agent.service import industry_mapping

    class _Output:
        matched_industries = ["法律服务"]

    class _Result:
        output = _Output()

        def usage(self):
            return {}

    class _Agent:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, prompt):
            assert "用户输入的目标行业: 律师" in prompt
            assert "法律服务" in prompt
            assert "企业服务" in prompt
            return _Result()

    monkeypatch.setattr(industry_mapping, "Agent", _Agent)
    monkeypatch.setattr(industry_mapping, "get_sales_agent_model", lambda scene="chat": object())
    monkeypatch.setattr(industry_mapping, "get_sales_agent_model_settings", lambda scene="chat": {})
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="通用咨询公司", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="安合商务服务", industry="法律服务", region="安徽", website="https://anhe.example.com", source="", data_status="active"),
        ])
        db.commit()

        mapped = asyncio.run(map_target_industries_to_available(db, ["律师"]))

        assert mapped == ["法律服务"]
    finally:
        db.close()


def test_default_company_pool_understands_lawyer_as_legal_service():
    """When AI is unavailable, the 「律师」 ("lawyer") fallback match must still hit the DB's 「法律服务」 ("legal services") industry tag."""
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="通用咨询公司", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="安合商务服务", industry="法律服务", region="安徽", website="https://anhe.example.com", source="", data_status="active"),
            SalesAgentCompanyORM(id=3, name="法衡服务中心", industry="法律服务", region="安徽", website="https://faheng.example.com", source="", data_status="active"),
        ])
        db.commit()

        rows = load_company_candidates_from_db(db, industries=["律师"], regions=["安徽"], limit=2)

        assert [row["name"] for row in rows[:2]] == ["法衡服务中心", "安合商务服务"]
    finally:
        db.close()


def test_unlimited_industry_and_region_scan_whole_company_pool():
    """「不限行业/不限地区」 ("any industry/any region") is not a search term but no filter; take candidates directly from the all-industry pool."""
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="上海企服", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="安徽法务", industry="法律服务", region="安徽", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=3, name="停用企业", industry="法律服务", region="安徽", source="manual", data_status="archived"),
        ])
        db.commit()

        rows = load_company_candidates_from_db(db, industries=["不限行业"], regions=["不限地区"], limit=2)

        names = {row["name"] for row in rows}
        assert {"上海企服", "安徽法务"} <= names
        assert "停用企业" not in names
    finally:
        db.close()


def test_recommendation_excludes_companies_with_sent_or_failed_email(monkeypatch):
    """Companies already sent to, successfully or not, are not recommended again as new recommendations."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": False, "missing_config": ["TENCENT_SEARCH_API_KEY"]})
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="已发送企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="未发送企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=3, name="发送失败企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyContactORM(company_id=1, email="sent@example.com", source="", consent_status="unknown", is_reachable=True),
            SalesAgentCompanyContactORM(company_id=2, email="fresh@example.com", source="", consent_status="unknown", is_reachable=True),
            SalesAgentCompanyContactORM(company_id=3, email="failed@example.com", source="", consent_status="unknown", is_reachable=True),
            SalesAgentEmailDeliveryORM(user_id=1, company_id=1, recipient_email="sent@example.com", status="sent"),
            SalesAgentEmailDeliveryORM(user_id=1, company_id=3, recipient_email="failed@example.com", status="failed"),
        ])
        db.commit()

        result = recommend_companies_workflow(
            db,
            1,
            limit=3,
            service_scope=["AI-GEO优化"],
            industries=["企业服务"],
            regions=["上海"],
            company_size="11-100 名员工",
            typical_customers=["国内企业"],
        )

        names = [lead["company"]["name"] for lead in result["leads"]]
        assert "已发送企业" not in names
        assert "发送失败企业" not in names
        assert names == ["未发送企业"]
    finally:
        db.close()


def test_recommendation_expands_db_pool_shortfall_with_public_discovery(monkeypatch):
    """When the DB candidate pool is short, actually expand to public web search; don't just return 10/100 + a gap of 90."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": True, "missing_config": []})
    monkeypatch.setattr(
        recommend_service,
        "discover_public_companies",
        lambda _profile, limit: CompanyDiscoveryResult(
            candidates=[
                CompanyDiscoveryCandidate(
                    name=f"公网补充企业{i}",
                    website=f"https://public{i}.example.com",
                    industry="企业服务",
                    region="上海",
                    source="public_company_site",
                    source_url=f"https://public{i}.example.com",
                )
                for i in range(1, int(limit) + 1)
            ],
            search_available=True,
            status="ok",
        ),
    )
    db = _db()
    try:
        db.add_all([
            SalesAgentCompanyORM(id=1, name="库内企业一", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="库内企业二", industry="企业服务", region="上海", source="manual", data_status="active"),
        ])
        db.commit()

        result = recommend_companies_workflow(
            db,
            1,
            limit=5,
            service_scope=["AI-GEO优化"],
            industries=["企业服务"],
            regions=["上海"],
            company_size="11-100 名员工",
            typical_customers=["国内企业"],
        )

        names = [lead["company"]["name"] for lead in result["leads"]]
        assert result["requested_limit"] == 5
        assert result["actual_count"] == 5
        assert result["shortfall"] == 0
        assert "库内企业一" in names and "库内企业二" in names
        assert len([name for name in names if name.startswith("公网补充企业")]) == 3
    finally:
        db.close()
