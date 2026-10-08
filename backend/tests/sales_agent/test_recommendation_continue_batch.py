"""Next-batch recommendations: reuse the previous campaign's filters, excluding already recommended/sent companies."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.discovery import CompanyDiscoveryCandidate, CompanyDiscoveryResult
from agents.sales_agent.models import (
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)
from agents.sales_agent.service.recommend import continue_recommend_companies_workflow
from geo.database import Base


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _campaign(db, user_id: int = 1) -> SalesAgentRecommendationCampaignORM:
    seller = SalesAgentSellerProfileORM(
        user_id=user_id,
        name="默认品牌",
        brand_key="default-brand",
        service_scope_json='["AI-GEO优化"]',
        service_capabilities_json='["AI-GEO优化"]',
        company_size="11-100 名员工",
    )
    db.add(seller)
    db.flush()
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=user_id,
        seller_profile_id=seller.id,
        target_industries_json='["企业服务"]',
        target_regions_json='["上海"]',
        target_company_size="11-100 名员工",
        requested_limit=2,
        limit=2,
        status="completed",
    )
    db.add(campaign)
    db.flush()
    return campaign


def test_continue_recommendation_excludes_previous_recommendations_and_sent_email(monkeypatch):
    """When the user says "continue recommending", give a new batch, not the previous companies again."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": False, "missing_config": ["TENCENT_SEARCH_API_KEY"]})
    db = _db()
    try:
        previous = _campaign(db)
        db.add_all([
            SalesAgentCompanyORM(id=1, name="上一批企业一", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=2, name="上一批企业二", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=3, name="已发送企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=4, name="新企业一", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentCompanyORM(id=5, name="新企业二", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentLeadRecommendationORM(user_id=1, campaign_id=previous.id, company_id=1, rank=1, status="Recommended"),
            SalesAgentLeadRecommendationORM(user_id=1, campaign_id=previous.id, company_id=2, rank=2, status="Recommended"),
            SalesAgentEmailDeliveryORM(user_id=1, company_id=3, recipient_email="sent@example.com", status="sent"),
        ])
        db.commit()

        result = continue_recommend_companies_workflow(db, 1, limit=2)

        names = [lead["company"]["name"] for lead in result["leads"]]
        assert names == ["新企业二", "新企业一"]
        assert result["actual_count"] == 2
        assert result["shortfall"] == 0
        assert result["continued_from_campaign_id"] == previous.id
        assert result["campaign"]["id"] != previous.id
    finally:
        db.close()


def test_continue_recommendation_expands_shortfall_with_public_discovery(monkeypatch):
    """Next batches also inherit public-web discovery to fill candidates when the DB is short."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": True, "missing_config": []})
    monkeypatch.setattr(
        recommend_service,
        "discover_public_companies",
        lambda _profile, limit: CompanyDiscoveryResult(
            candidates=[
                CompanyDiscoveryCandidate(
                    name=f"续批公网企业{i}",
                    website=f"https://next{i}.example.com",
                    industry="企业服务",
                    region="上海",
                    source="public_company_site",
                    source_url=f"https://next{i}.example.com",
                )
                for i in range(1, int(limit) + 1)
            ],
            search_available=True,
            status="ok",
        ),
    )
    db = _db()
    try:
        previous = _campaign(db)
        db.add_all([
            SalesAgentCompanyORM(id=1, name="上一批企业", industry="企业服务", region="上海", source="manual", data_status="active"),
            SalesAgentLeadRecommendationORM(user_id=1, campaign_id=previous.id, company_id=1, rank=1, status="Recommended"),
        ])
        db.commit()

        result = continue_recommend_companies_workflow(db, 1, limit=3)

        names = [lead["company"]["name"] for lead in result["leads"]]
        assert result["actual_count"] == 3
        assert result["shortfall"] == 0
        assert names == ["续批公网企业1", "续批公网企业2", "续批公网企业3"]
        assert result["continued_from_campaign_id"] == previous.id
    finally:
        db.close()


def test_continue_recommendation_without_previous_campaign_returns_guidance():
    """Without previous filters, explicitly ask the user to add them; don't raise a generic error."""
    db = _db()
    try:
        result = continue_recommend_companies_workflow(db, 1, limit=5)
        assert result["status"] == "need_more_info"
        assert result["needs_user_input"] is True
        assert "还没有找到上一轮推荐条件" in result["message"]
        assert result["requested_limit"] == 5
    finally:
        db.close()
