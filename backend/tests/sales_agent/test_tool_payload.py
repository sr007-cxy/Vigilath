"""Two views of the tool return body -- full data for cards, summary for the model.

2026-08-26 production: the entire return of `recommend_companies` was fed back into the model context verbatim; recommending 20 companies
was 510K characters (`ranking` a full copy of `leads`, `discovery` echoing 130 seed companies,
`campaign.seed_companies` echoing them again), exhausting the chat scenario's 60s wall clock. The tool itself only took 0.4s.

Assertions here use a really-run recommendation result, not a hand-crafted fake dict -- a fake dict can't catch regressions like "the workflow added
another big field".

Run:
    venv/bin/python -m pytest tests/sales_agent/test_tool_payload.py -q
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.runtime.payload import card_payload, model_view
from agents.sales_agent.seed_companies import sales_agent_seed_companies
from agents.sales_agent.service.companies import import_sales_agent_seed_companies
from agents.sales_agent.service.recommend import recommend_companies_workflow
from geo.database import Base
from geo.models.user import UserORM

USER = 1000001


def _size(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str))


@pytest.fixture(scope="module")
def result():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="payload@test.local", password_hash="x", is_active=True))
    db.commit()
    return recommend_companies_workflow(
        db, USER, limit=20,
        service_scope=["AI-GEO优化"],
        industries=["企业服务"],
        regions=["全国"],
        company_size="11-100 名员工",
        typical_customers=["国内企业"],
        seed_companies=sales_agent_seed_companies(),
    )


def test_the_raw_result_is_the_thing_we_must_not_send(result):
    """Regression baseline: the raw return really is hundreds of thousands of characters. If it ever shrinks, this trimming should be re-evaluated."""
    assert result["actual_count"] == 20
    assert _size(result) > 200_000


# --- The model's view ---

def test_model_view_is_small(result):
    assert _size(model_view(result)) < 20_000


def test_model_view_keeps_what_the_reply_needs(result):
    view = model_view(result)
    assert view["status"] == "ok"
    assert view["actual_count"] == 20
    assert view["requested_limit"] == 20
    # Filter match rates must be kept: candidates from the seed pool aren't filtered by industry/region, and the model must be able to say so truthfully
    assert "industry_match_count" in view and "requested_industries" in view


def test_model_view_drops_the_bulk(result):
    view = model_view(result)
    for key in ("leads", "ranking", "discovery", "campaign", "evidence_summary",
                "seller_profile", "account_icp", "profile", "recommendation_table"):
        assert key not in view, f"{key} 又回灌给模型了"


def test_model_view_drops_the_table(result):
    # The three table columns are a strict subset of the card; sending them back only makes the model paste the table into the body, duplicating the card.
    assert "markdown_table" not in model_view(result)
    assert card_payload(result)["markdown_table"], "卡片那份要留着,确定性兜底回复在用"


def test_model_view_carries_the_question_when_more_info_is_needed():
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "可以。请先确认这次推荐范围:",
        "next_question": "目标地区：这次想找哪个地区的客户?",
        "missing_items": ["本次想找的目标地区"],
        "question_card": {"id": "campaign_target_regions", "field": "target_regions",
                          "title": "目标地区", "options": [{"label": "北京", "value": "北京"}] * 18},
        "leads": [],
    }
    view = model_view(data)
    assert view["next_question"].startswith("目标地区")
    assert view["question_card"] == {"id": "campaign_target_regions", "field": "target_regions", "title": "目标地区"}
    assert "options" not in view["question_card"]


# --- The card's view ---

def test_card_payload_keeps_every_company(result):
    card = card_payload(result)
    assert len(card["leads"]) == 20
    assert card["leads"][0]["company"]["name"]
    assert len(card["recommendation_table"]) == 20
    assert card["markdown_table"]
    assert card["status"] == "ok"


def test_card_payload_drops_only_the_duplicates(result):
    card = card_payload(result)
    assert "ranking" not in card, "ranking 与顶层 leads 逐字重复"
    assert "companies" not in card["discovery"], "discovery 回显的是同一批种子"
    assert "seed_companies" not in card["campaign"], "种子池的第三份副本"
    assert _size(card) < _size(result) / 4


def test_card_payload_still_renders_without_ranking(result):
    """When frontend `recommendationData` can't read `data.ranking.leads` it falls back to the top level, which must be complete."""
    card = card_payload(result)
    for key in ("leads", "count", "actual_count", "requested_limit", "shortfall", "status", "mode"):
        assert key in card


# --- End to end: one real agent run, what the model sees vs what the card gets ---

def test_agent_run_sends_the_summary_and_still_renders_the_card():
    """Run one real Pydantic AI run: the model receives a summary tool-return, while the card still gets the full data.

    This is the seam's gatekeeper -- change how `ToolReturn` is written, or make `_cards_from_agent_result` read only
    `content`, and each side looks "right", but wired together the cards are empty or the context blows up again.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from agents.sales_agent.api import _cards_from_agent_result
    from agents.sales_agent.deps import SalesAgentDeps
    from agents.sales_agent.tools import recommend_companies

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="run@test.local", password_hash="x", is_active=True))
    db.commit()
    # Default recommendations now read from sales_agent_companies; the test DB also imports the candidate pool via the production path,
    # and can no longer rely on the built-in list fallback that once existed in the tool layer.
    import_sales_agent_seed_companies(db, user_id=None)
    deps = SalesAgentDeps(account_id=USER, user_id=USER, db=db)

    def steps(messages, info):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("recommend_companies", {
                "limit": 20, "service_scope": ["AI-GEO优化"], "industries": ["企业服务"],
                "regions": ["北京"], "company_size": "11-100 名员工", "typical_customers": ["国内企业"],
            })])
        return ModelResponse(parts=[TextPart("已按当前筛选条件推荐 20 家企业。")])

    agent = Agent(FunctionModel(steps), deps_type=SalesAgentDeps, tools=[recommend_companies])
    result = agent.run_sync("推荐 20 家", deps=deps)

    returns = [p for m in result.all_messages() for p in getattr(m, "parts", [])
               if getattr(p, "part_kind", "") == "tool-return"]
    assert len(returns) == 1
    assert _size(returns[0].content) < 20_000, "模型又收到全量返回体了"
    assert "leads" not in returns[0].content

    cards = _cards_from_agent_result(result)
    assert len(cards) == 1
    assert len(cards[0]["data"]["leads"]) == 20, "卡片没拿到完整名单"

    # Filters confirmed by the tool are written back to lead_flow so the deterministic flow recognizes the next answer;
    # service_scope in the model's tool args is service/product direction and must not be written as capabilities.
    assert deps.tool_state["lead_flow"]["service_scope"] == ["AI-GEO优化"]
    assert "seller_capabilities" not in deps.tool_state["lead_flow"]
    assert deps.tool_state["lead_flow"]["target_regions"] == ["北京"]
    assert deps.tool_state["lead_flow"]["target_industries"] == ["企业服务"]
    db.close()


def test_history_does_not_carry_the_card_payload():
    """Full card data doesn't go into messages_json -- the model can't see it, yet its bulk would crowd out the real conversation."""
    from agents.sales_agent.methods import _strip_tool_metadata

    raw = json.dumps([{"parts": [
        {"part_kind": "tool-return", "tool_name": "recommend_companies",
         "content": {"status": "ok"}, "metadata": {"leads": [{"company": {"name": "某某科技"}}]}},
        {"part_kind": "text", "content": "已按当前筛选条件推荐 20 家企业。"},
    ]}], ensure_ascii=False)

    stripped = json.loads(_strip_tool_metadata(raw))
    assert stripped[0]["parts"][0]["metadata"] is None
    assert stripped[0]["parts"][0]["content"] == {"status": "ok"}, "模型看的那份不许动"
    assert stripped[0]["parts"][1]["content"].startswith("已按")


def test_history_without_tool_metadata_is_untouched():
    """History without metadata is returned byte-for-byte as-is, behaving the same as before the change."""
    from agents.sales_agent.methods import _strip_tool_metadata

    raw = json.dumps([{"parts": [{"part_kind": "text", "content": "你好"}]}], ensure_ascii=False)
    assert _strip_tool_metadata(raw) is raw
    assert _strip_tool_metadata("not json") == "not json"
