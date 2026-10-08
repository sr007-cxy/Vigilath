"""Context within one request is queried once, but the compliance gate re-runs every time.

`/outreach/drafts/auto` is shaped as "fetch context -> call model -> persist to DB". The persist step
(`generate_outreach_draft`) used to re-query the whole profile, campaign and report set, and **inside** the loop over
variant count -- multi-variant report emails re-queried several times, times 10 for a batch of 10 concurrent. Pure reads, identical whoever runs them,
so the endpoint passes its copy straight down.

But reuse has a boundary that must hold: a model call (a dozen-plus seconds) sits between fetching context and persisting. The lead may
have just unsubscribed in between, so lead / contact / `_outreach_guard` are **not reused** and must be re-fetched and re-checked every time.
Only the pure-read re-queries are saved.

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_outreach_context_reuse.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.models import (
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)
from agents.sales_agent.service.outreach import (
    generate_outreach_draft,
    get_outreach_context,
    get_outreach_context_with_structured,
)
from geo.database import Base
from geo.models.user import UserORM

USER = 1000002
BODY = "我这边是星启网络,把官网做成能带单的官网。\n\n如果贵司这边有相关需求,欢迎直接回复本邮件。"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(UserORM(id=USER, email="reuse@test.local", password_hash="x", is_active=True))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def lead(db):
    seller = SalesAgentSellerProfileORM(
        user_id=USER,
        name="星启网络",
        brand_key="brand-reuse",
        service_capabilities_json=json.dumps(["官网建设"], ensure_ascii=False),
        service_scope_json=json.dumps(["官网建设"], ensure_ascii=False),
        typical_customers_json=json.dumps(["制造业企业"], ensure_ascii=False),
        company_size="11-100 名员工",
    )
    company = SalesAgentCompanyORM(name="星迈科技", industry="制造业", region="上海", source="manual", data_status="active")
    db.add_all([seller, company])
    db.flush()
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["制造业"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        target_company_size="11-100 名员工",
        status="completed",
    )
    db.add_all([campaign, SalesAgentCompanyContactORM(company_id=company.id, email="sales@xingmai.example.com", is_reachable=True)])
    db.flush()
    row = SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=campaign.id, company_id=company.id, rank=1, status="Recommended")
    db.add(row)
    db.commit()
    return row


def _count_selects(db, run) -> int:
    engine = db.get_bind()
    seen = [0]

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return seen[0]


def _draft(db, lead_id, structured=None) -> dict:
    return generate_outreach_draft(
        db, USER, lead_id, channel="email",
        subject="关于贵司的AI搜索可见性", content=BODY,
        structured=structured,
    )


# --- The half that's saved ---

def test_passing_the_structured_context_skips_the_requery(db, lead):
    _, structured = get_outreach_context_with_structured(db, USER, lead.id, channel="email")
    reused = _count_selects(db, lambda: _draft(db, lead.id, structured))
    fresh = _count_selects(db, lambda: _draft(db, lead.id))
    assert reused < fresh, "传了上下文还在重查,这次改动等于没做"


def test_reused_and_fresh_drafts_are_identical(db, lead):
    """Saving queries must not produce a second kind of result -- drafts from both paths must be identical verbatim."""
    _, structured = get_outreach_context_with_structured(db, USER, lead.id, channel="email")
    reused = _draft(db, lead.id, structured)["draft"]
    fresh = _draft(db, lead.id)["draft"]
    for field in ("subject", "content", "channel", "send_status"):
        assert reused[field] == fresh[field], field


def test_get_outreach_context_still_returns_just_the_payload(db, lead):
    """The old signature is public surface still used by the Agent tool path in tools.py; it must not change shape because of an extra return value."""
    payload = get_outreach_context(db, USER, lead.id, channel="email")
    assert isinstance(payload, dict)
    assert payload["company"]["name"] == "星迈科技"
    assert payload == get_outreach_context_with_structured(db, USER, lead.id, channel="email")[0]


# --- The half that's not saved: the gate ---

def test_guard_still_blocks_when_the_lead_unsubscribes_mid_generation(db, lead):
    """This is the reuse boundary. Context fetched, the model writes for a dozen-plus seconds, the lead unsubscribes meanwhile -- it must be blocked.

    If the compliance gate were reused too, it would persist the email based on a judgment from a dozen-plus seconds ago.
    """
    _, structured = get_outreach_context_with_structured(db, USER, lead.id, channel="email")
    lead.status = "Unsubscribed"
    db.commit()

    result = _draft(db, lead.id, structured)

    assert result["blocked"] is True
    assert "Unsubscribed" in "；".join(result["reasons"])
