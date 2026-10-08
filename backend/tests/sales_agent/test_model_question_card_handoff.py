"""Handoff between model-emitted question cards and the deterministic flow.

2026-08-26 production: the entry sentence 「帮我查下有哪些企业需要GEO服务的？」 ("find which companies need GEO services") was keyword-classified as profile,
the deterministic find-customers flow never started, and `flow["step"]` stayed empty; all three filter cards were question_cards returned by the model calling
`recommend_companies`. So every subsequent answer ("enterprise services",
"nationwide, 20 companies") was classified as chat, hit the read-only gate and went back to the model -- the last turn fed a 510K-character tool result
into context, the chat scenario's 60s wall clock ran out, and the user saw "this is taking longer than usual".

This locks in: while a card is pending, that sentence is the answer, handled by the deterministic flow; but real questions still go back to the model.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_model_question_card_handoff.py -q
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.api import _pending_question_step, _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import SalesAgentChatMessageORM, SalesAgentChatSessionORM
from geo.database import Base
from geo.models.user import UserORM

USER = 1000001


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="handoff@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="handoff-1", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id="handoff-1")
    try:
        yield d
    finally:
        db.close()


def _model_card(deps, card_id: str, field: str, title: str) -> None:
    """Reproduce that model turn: it emitted a question card but didn't write flow["step"]."""
    card = {
        "tool": "recommend_companies",
        "data": {
            "status": "need_more_info",
            "needs_user_input": True,
            "next_question": title,
            "question_card": {"id": card_id, "field": field, "title": title, "type": "single_select"},
        },
    }
    deps.db.add(SalesAgentChatMessageORM(
        session_id=deps.session_id, role="assistant",
        content=title, metadata_json=json.dumps({"tool_cards": [card]}, ensure_ascii=False),
    ))
    deps.db.commit()


def _run(deps, message: str, intent: str) -> bool:
    return asyncio.run(_run_deterministic_flow(deps, message, intent))


def _flow(deps) -> dict:
    return deps.tool_state.get("lead_flow") or {}


# --- Card id / field -> step ---

@pytest.mark.parametrize("card_id,field,step", [
    ("campaign_target_regions", "target_regions", "target_regions"),
    ("campaign_target_industries", "target_industries", "target_industries"),
    ("campaign_target_company_size", "target_company_size", "target_company_size"),
    ("seller_typical_customers", "typical_customers", "typical_customers"),
    ("seller_business_hint", "business_hint", "business_hint"),
    ("seller_profile", "seller_capabilities", "seller_capabilities"),
    ("seller_service_scope", "service_scope", "service_scope"),
    # Unrecognized ids fall back to field
    ("sales_agent_guidance", "target_regions", "target_regions"),
])
def test_pending_card_maps_to_step(deps, card_id, field, step):
    _model_card(deps, card_id, field, "这次想找哪个?")
    assert _pending_question_step(deps) == step


def test_entry_action_card_is_not_a_slot_question(deps):
    """The two action buttons on the entry aren't questions and must not be treated as a step's card."""
    _model_card(deps, "sales_agent_entry_action", "entry_action", "你想先做哪一步?")
    assert _pending_question_step(deps) == ""


def test_no_card_means_no_step(deps):
    assert _pending_question_step(deps) == ""


# --- Reproducing that production turn ---

def test_answer_to_a_model_card_is_handled_deterministically(deps):
    """Region card answers go through the deterministic flow even if classified as chat; 「全国」 ("nationwide") is accepted as any region, no longer a card option."""
    _model_card(deps, "campaign_target_regions", "target_regions", "目标地区：这次想找哪个地区的客户?")

    handled = _run(deps, "北京、上海、全国、20家", "chat")

    assert handled is True, "又被只读闸交回模型了"
    assert _flow(deps).get("target_regions") == ["不限地区"]
    assert _flow(deps).get("requested_limit") == 20


def test_stale_industry_card_does_not_swallow_no_limit_region_answer(deps, monkeypatch):
    """With rapid clicks the old industry card may still be the latest assistant message; an any-region answer must not be swallowed by it and then re-prompt the region card."""
    import agents.sales_agent.api as sales_api

    monkeypatch.setattr(sales_api, "guard_check_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(sales_api, "recommend_companies_workflow", lambda *args, **kwargs: {"status": "ok", "actual_count": 0, "count": 0, "leads": [], "requested_limit": kwargs.get("limit") or 100})
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["AI-GEO优化"],
        "service_scope": ["AI 可见性检测"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["有官网但搜索获客一般的企业"],
        "target_company_size": "11-100 名员工",
        "target_industries": ["企业服务"],
        "step": "target_regions",
    })
    _model_card(deps, "campaign_target_industries", "target_industries", "目标行业：这次想找哪个行业的客户?")

    handled = _run(deps, "不限地区、100家", "chat")

    assert handled is True
    assert _flow(deps).get("target_regions") == ["不限地区"]
    assert _flow(deps).get("target_industries") == ["企业服务"]
    assert _flow(deps).get("requested_limit") == 100
    assert _flow(deps).get("step") == ""



def test_custom_region_answer_finishes_the_flow(deps, monkeypatch):
    """After free-text input "Jiangsu/Zhejiang/Shanghai, 10 companies" on the region card, proceed to recommendation; don't push another region card."""
    import agents.sales_agent.api as sales_api

    def fake_recommend(*args, **kwargs):
        return {"status": "ok", "actual_count": 0, "count": 0, "leads": [], "requested_limit": kwargs.get("limit") or 10}

    monkeypatch.setattr(sales_api, "guard_check_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(sales_api, "recommend_companies_workflow", fake_recommend)
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["AI-GEO优化"],
        "service_scope": ["AI 可见性检测"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["有官网但搜索获客一般的企业"],
        "target_company_size": "11-100 名员工",
        "target_industries": ["企业服务"],
        "step": "target_regions",
    })

    handled = _run(deps, "江浙沪、10家", "chat")

    assert handled is True
    assert _flow(deps).get("target_regions") == ["江浙沪"]
    assert _flow(deps).get("requested_limit") == 10
    assert _flow(deps).get("step") == ""



def test_real_question_while_a_card_is_pending_still_goes_to_the_right_read_path(deps):
    """A pending card doesn't mean the next sentence must be the answer; Q&A goes to the model, fact lookups take the deterministic query."""
    _model_card(deps, "campaign_target_regions", "target_regions", "目标地区：这次想找哪个地区的客户?")
    assert _run(deps, "你们怎么收费", "faq") is False
    assert _run(deps, "哪些企业已经发过邮件", "chat") is True
    assert _flow(deps).get("target_regions") is None



def test_recommendation_history_query_still_returns_result_card_while_question_card_is_pending(deps):
    """Recommendation history is itself a card: a real query must return the result card, not be written into target regions by the pending question card."""
    _model_card(deps, "campaign_target_regions", "target_regions", "目标地区：这次想找哪个地区的客户?")

    handled = _run(deps, "推荐过哪些企业？", "recommendation_lookup")

    assert handled is True
    assert _flow(deps).get("target_regions") is None


def test_without_a_pending_card_read_only_gate_is_unchanged(deps):
    """With no pending card, behavior is identical to before the change: read-only intents go to the model."""
    assert _run(deps, "全国、20家", "chat") is False


def test_company_size_button_text_is_handled_even_when_pending_card_metadata_is_missing(deps):
    """Production screenshot regression: after the size card is submitted, standard size buttons must not fall to "this isn't clear enough"."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["法律服务"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["需要合同审查的企业"],
    })

    handled = _run(deps, "11-100 名员工", "unclear")

    assert handled is True
    assert _flow(deps).get("target_company_size") == "11-100 名员工"
    assert _flow(deps).get("step") == "target_industries"


def test_target_industry_button_text_is_handled_even_when_pending_card_metadata_is_missing(deps):
    """Same fallback for the target industry card: standard industry answers must not go back to the model because step was lost."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["法律服务"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["需要合同审查的企业"],
        "target_company_size": "11-100 名员工",
    })

    handled = _run(deps, "法律服务", "unclear")

    assert handled is True
    assert _flow(deps).get("target_industries") == ["法律服务"]
    assert _flow(deps).get("step") == "target_regions"


def test_target_region_button_text_is_handled_even_when_pending_card_metadata_is_missing(deps, monkeypatch):
    """Same fallback for the target region card: standard region answers must complete the flow, not fall to plain chat."""
    import agents.sales_agent.api as sales_api

    monkeypatch.setattr(sales_api, "guard_check_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(sales_api, "recommend_companies_workflow", lambda *args, **kwargs: {"status": "ok", "actual_count": 0, "count": 0, "leads": [], "requested_limit": kwargs.get("limit") or 100})
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["法律服务"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["需要合同审查的企业"],
        "target_company_size": "11-100 名员工",
        "target_industries": ["法律服务"],
    })

    handled = _run(deps, "上海", "unclear")

    assert handled is True
    assert _flow(deps).get("target_regions") == ["上海"]
    # Once the last slot is filled, recommendation runs immediately and step is cleared afterwards. step is now derived from slots by `next_step(state)`,
    # no "ready" intermediate state left hanging in the session.
    assert _flow(deps).get("step") == ""


def test_typical_customer_button_text_is_handled_even_when_pending_card_metadata_is_missing(deps):
    """Same fallback for the suitable-customers card: common customer type answers must not go back to the model because step was lost."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["法律服务"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
    })

    handled = _run(deps, "需要合同审查的企业", "unclear")

    assert handled is True
    assert _flow(deps).get("typical_customers") == ["需要合同审查的企业"]
    assert _flow(deps).get("target_industries") is None
    assert _flow(deps).get("step") == "target_company_size"


def test_typical_customer_pending_card_still_handles_long_text_answer(deps):
    """With suitable-customers card metadata present, long-text answers are still handled as suitable customers."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["法律服务"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
    })
    _model_card(deps, "seller_typical_customers", "typical_customers", "适合客户：你最适合服务哪类客户?")

    handled = _run(deps, "需要合同审查的企业", "unclear")

    assert handled is True
    assert _flow(deps).get("typical_customers") == ["需要合同审查的企业"]
    assert _flow(deps).get("step") == "target_company_size"


# --- The entry sentence itself should take the deterministic flow ---

def test_lead_entry_opens_the_card_flow_without_the_model(deps):
    """Classified as lead, show the profile card directly, without waiting for the model to emit a card and hand off.

    The production sentence 「帮我查下有哪些企业需要GEO服务的？」 used to hit geo in the word list and be classified as profile,
    so every card in the conversation came from the model.
    """
    from agents.sales_agent.agent import route_intent

    message = "帮我查下有哪些企业需要GEO服务的？"
    assert route_intent(message) == "lead"
    assert _run(deps, message, "lead") is True
    row = deps.db.query(SalesAgentChatMessageORM).filter(
        SalesAgentChatMessageORM.role == "assistant").order_by(SalesAgentChatMessageORM.id.desc()).first()
    assert "seller_profile" in (row.metadata_json or ""), "该出我方画像卡"


def test_lookup_entry_uses_readonly_history_not_lead_flow(deps):
    """Querying history isn't finding a new batch -- read-only history may be a fallback, but must not be dragged into the write path/recommendation flow."""
    assert _run(deps, "之前推荐过哪些公司", "lookup") is True
    row = deps.db.query(SalesAgentChatMessageORM).filter(
        SalesAgentChatMessageORM.role == "assistant").order_by(SalesAgentChatMessageORM.id.desc()).first()
    assert row is not None
    assert "没有找到已推荐企业记录" in row.content
    assert "seller_profile" not in (row.metadata_json or "")
