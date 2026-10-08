"""End-to-end assertions for the deterministic flow -- really runs `_run_deterministic_flow`, really writes sqlite.

The cases above are unit-level or structural gates: `verify_slot_answer` judges correctly, the call line in `api.py` is still there.
They can't catch "both pieces correct, still wrong when wired together" -- the 2026-08-23 production profile pollution was exactly that:
route_intent classified 「都发过哪些企业?」 ("which companies have I sent to?") as lookup, but the flow layer ignored intent and wrote it to the DB as an answer.

So this runs from "the user sent a message" to "what the DB ends up looking like".

Run:
    venv/bin/python -m pytest tests/sales_agent/test_flow_end_to_end.py -q
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.api import _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentRecommendationProfileUsageORM,
    SalesAgentRecommendationProfileVersionORM,
    SalesAgentSellerProfileORM,
)
from geo.database import Base
from geo.models.user import UserORM

USER = 1000001


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="e2e@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="e2e-1", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id="e2e-1")
    try:
        yield d
    finally:
        db.close()


def _at_step(deps, step: str) -> None:
    """Park the session on a card -- reproducing the user having opened the profile card but not answered."""
    deps.tool_state["lead_flow"] = {"step": step}
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    row.context_json = json.dumps({"lead_flow": {"step": step}}, ensure_ascii=False)
    deps.db.commit()


def _at_summary(deps, **slots) -> None:
    """Park on the "our profile" confirmation card, with slots prefilled from the /admin table.

    Reproduces `_append_seller_profile_summary_card`: when it emits the card it writes capabilities, service scope and
    size from the brand row into lead_flow -- so these values are already on hand when "continue" is clicked. **"Suitable customers" is not among them**;
    it's re-chosen every turn and the profile card deliberately doesn't prefill it (see the comment in that function).
    """
    flow = {"step": "seller_profile_summary", **slots}
    deps.tool_state["lead_flow"] = flow
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    row.context_json = json.dumps({"lead_flow": flow}, ensure_ascii=False)
    deps.db.commit()


def _set_flow(deps, flow: dict) -> None:
    deps.tool_state["lead_flow"] = dict(flow)
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    row.context_json = json.dumps({"lead_flow": flow}, ensure_ascii=False)
    deps.db.commit()


def _seller_profile(deps, **fields) -> SalesAgentSellerProfileORM:
    """Build a brand profile row -- reproducing what the user filled in on /admin."""
    row = SalesAgentSellerProfileORM(
        user_id=USER,
        name="默认品牌",
        brand_key="brand-e2e",
        service_capabilities_json=json.dumps(fields.get("service_capabilities") or [], ensure_ascii=False),
        service_scope_json=json.dumps(fields.get("service_scope") or [], ensure_ascii=False),
        typical_customers_json=json.dumps(fields.get("typical_customers") or [], ensure_ascii=False),
        company_size=fields.get("company_size") or "",
    )
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def _delivery(deps, *, subject="关于测试公司在AI搜索中的表现", content="邮件正文内容", sent_at=None, status="sent", from_email="sender@example.com", from_name="发件人") -> SalesAgentEmailDeliveryORM:
    suffix = str(abs(hash((subject, content, sent_at))) % 100000)
    company = SalesAgentCompanyORM(name=f"测试公司{suffix}", canonical_name=f"测试公司{suffix}", normalized_domain=f"example{suffix}.com")
    deps.db.add(company)
    deps.db.flush()
    draft = SalesAgentOutreachLogORM(lead_id=1, channel="email", subject=subject, content=content, send_status="sent")
    deps.db.add(draft)
    deps.db.flush()
    row = SalesAgentEmailDeliveryORM(
        user_id=USER,
        company_id=company.id,
        outreach_log_id=draft.id,
        from_email=from_email,
        from_name=from_name,
        recipient_email="sales@example.com",
        subject=subject,
        content_preview=content[:600],
        status=status,
        sent_at=sent_at or datetime.utcnow(),
        metadata_json=json.dumps({"content": content}, ensure_ascii=False),
    )
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def _step(deps) -> str:
    return str((deps.tool_state.get("lead_flow") or {}).get("step") or "")


def _flow_slot(deps, key: str) -> list[str]:
    """Read slot values from this turn's session -- things re-chosen each turn live only here, not in the brand row."""
    return list((deps.tool_state.get("lead_flow") or {}).get(key) or [])


def _capabilities(deps) -> list[str]:
    row = (
        deps.db.query(SalesAgentSellerProfileORM)
        .filter(SalesAgentSellerProfileORM.user_id == USER)
        .order_by(SalesAgentSellerProfileORM.id.desc())
        .first()
    )
    return json.loads(row.service_capabilities_json) if row else []


def _typical_customers(deps) -> list[str]:
    row = (
        deps.db.query(SalesAgentSellerProfileORM)
        .filter(SalesAgentSellerProfileORM.user_id == USER)
        .order_by(SalesAgentSellerProfileORM.id.desc())
        .first()
    )
    return json.loads(row.typical_customers_json) if row else []


def _service_scope(deps) -> list[str]:
    row = (
        deps.db.query(SalesAgentSellerProfileORM)
        .filter(SalesAgentSellerProfileORM.user_id == USER)
        .order_by(SalesAgentSellerProfileORM.id.desc())
        .first()
    )
    return json.loads(row.service_scope_json) if row else []


def _replies(deps) -> list[str]:
    return [
        m.content for m in deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant").all()
    ]



def _last_metadata(deps) -> str:
    row = (
        deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.desc()).first()
    )
    return row.metadata_json if row else ""


def _last_question_card(deps) -> dict:
    metadata = json.loads(_last_metadata(deps) or "{}")
    cards = metadata.get("tool_cards") or []
    return ((cards[-1].get("data") or {}).get("question_card") or {}) if cards else {}


def _all_metadata(deps) -> list[str]:
    return [
        m.metadata_json for m in deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant").all()
    ]


def _run(deps, message: str, intent: str) -> bool:
    # Same as test_intent_resolution: don't use get_event_loop(); on Python 3.12, once
    # asyncio.run has been called elsewhere it raises "There is no current event loop"
    return asyncio.run(_run_deterministic_flow(deps, message, intent))


# --- Full reproduction of that production incident ---


def test_back_to_target_size_keeps_previous_selection_for_echo(deps):
    """When clicking "back" to target size, the previously chosen size must stay in flow for question_card to echo."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "seller_capabilities": ["AI-GEO优化"],
        "service_scope": ["AI 可见性检测"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["有官网但搜索获客一般的企业"],
        "target_company_size": "101-500 名员工",
        "target_industries": ["企业服务"],
        "step": "target_industries",
    })

    handled = _run(deps, "返回上一步", "lead")

    assert handled is True
    assert _step(deps) == "target_company_size"
    assert (deps.tool_state.get("lead_flow") or {}).get("target_company_size") == "101-500 名员工"
    assert '"selected_values": ["101-500 名员工"]' in _last_metadata(deps)


def test_new_session_target_size_card_echoes_latest_campaign_selection(deps):
    """Harness regression: in a new session the target company size card also echoes defaults from the latest recommendation filters."""
    deps.db.add(SalesAgentRecommendationCampaignORM(
        user_id=USER,
        session_id=None,
        target_company_size="101-500 名员工、11-100 名员工",
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
    ))
    deps.db.commit()

    deps.tool_state["lead_flow"] = {"typical_customers": ["有官网但搜索获客一般的企业"]}
    from agents.sales_agent.api import _append_lead_target_size_guidance
    _append_lead_target_size_guidance(deps, "lead")

    metadata = json.loads(_last_metadata(deps))
    card = metadata["tool_cards"][0]["data"]["question_card"]
    assert card["selected_values"] == ["101-500 名员工", "11-100 名员工"]


def test_new_session_target_region_card_echoes_latest_campaign_selection(deps):
    """Harness regression: in a new session the target region card also echoes defaults from the latest recommendation filters."""
    deps.db.add(SalesAgentRecommendationCampaignORM(
        user_id=USER,
        session_id=None,
        target_company_size="101-500 名员工",
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["北京", "上海"], ensure_ascii=False),
    ))
    deps.db.commit()

    deps.tool_state["lead_flow"] = {"target_industries": ["企业服务"]}
    from agents.sales_agent.api import _append_campaign_region_guidance
    _append_campaign_region_guidance(deps, "lead")

    metadata = json.loads(_last_metadata(deps))
    card = metadata["tool_cards"][0]["data"]["question_card"]
    assert card["selected_values"] == ["北京", "上海"]


def test_current_session_selection_wins_over_latest_campaign_echo(deps):
    """Harness regression: historical defaults only fill empty cards; they must not override values the user picked this turn."""
    deps.db.add(SalesAgentRecommendationCampaignORM(
        user_id=USER,
        session_id=None,
        target_company_size="101-500 名员工",
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["北京"], ensure_ascii=False),
    ))
    deps.db.commit()

    deps.tool_state["lead_flow"] = {"target_company_size": "501-1000 名员工"}
    from agents.sales_agent.api import _append_lead_target_size_guidance
    _append_lead_target_size_guidance(deps, "lead")

    metadata = json.loads(_last_metadata(deps))
    card = metadata["tool_cards"][0]["data"]["question_card"]
    assert card["selected_values"] == ["501-1000 名员工"]


def test_question_at_a_pending_card_does_not_pollute_profile(deps):
    """The user asked something else before answering the profile card -- it must not be written into the profile, nor pretend to be answered.

    Production wording: 「我们具备**都发过哪些企业?**等专业能力」 ("we have expertise in **which companies have I sent to?**").
    """
    _at_step(deps, "seller_capabilities")
    handled = _run(deps, "都发过哪些企业?", "lookup")

    assert handled is False, "应该交给模型去查发送记录,而不是当成对画像卡的回答"
    assert "都发过哪些企业?" not in _capabilities(deps)
    assert _capabilities(deps) == []
    assert _replies(deps) == [], "既然没处理,就不该往会话里塞回复"


def test_action_button_text_does_not_become_a_capability(deps):
    """「找客户」 ("find customers") is button text; written into the profile it becomes 「更适合优先服务找客户」."""
    _at_step(deps, "seller_capabilities")
    assert _run(deps, "找客户", "lead") is False
    assert _capabilities(deps) == []


def test_real_answer_at_the_same_step_still_works(deps):
    """Blocking questions must not also block normal answers."""
    _at_step(deps, "seller_capabilities")
    handled = _run(deps, "AI-GEO优化、SEO技术团队", "lead")

    assert handled is True
    assert _capabilities(deps) == ["AI-GEO优化", "SEO技术团队"]
    assert _replies(deps), "处理了就该有回复"


def test_incomplete_orphan_profile_asks_business_hint_before_summary(deps):
    """A half-empty profile from a new/unlinked account can't be confirmed as a historical profile; ask the business direction first for AI to expand."""
    _seller_profile(deps, service_capabilities=["财务能力"], company_size="1001+ 名员工")

    assert _run(deps, "推荐企业", "lead_recommendation") is True

    card = _last_question_card(deps)
    assert card["id"] == "seller_business_hint"
    assert _step(deps) == "business_hint"
    assert "sales_agent_seller_profile_summary" not in _last_metadata(deps)


def test_complete_orphan_profile_can_show_summary(deps):
    """With no uploaded materials but all four profile fields complete, show our profile confirmation card directly."""
    _seller_profile(
        deps,
        service_capabilities=["财务能力"],
        service_scope=["财税咨询"],
        typical_customers=["需要财务规范化的企业"],
        company_size="1001+ 名员工",
    )

    assert _run(deps, "推荐企业", "lead_recommendation") is True

    assert "sales_agent_seller_profile_summary" in _last_metadata(deps)
    assert _step(deps) == "seller_profile_summary"


def test_business_hint_generates_ai_profile_candidates(deps, monkeypatch):
    """Business direction is context only; after the user answers, show AI-generated profile candidates."""
    import agents.sales_agent.api as sales_api

    async def fake_suggest(context):
        assert context["business_hint"] == ["法律服务"]
        return {
            "service_capabilities": [
                {"label": "合同审查支持", "value": "合同审查支持", "source": "ai"},
                {"label": "企业合规咨询", "value": "企业合规咨询", "source": "ai"},
                {"label": "劳动用工风险处理", "value": "劳动用工风险处理", "source": "ai"},
            ],
            "service_scope": [
                {"label": "法律顾问服务", "value": "法律顾问服务", "source": "ai"},
                {"label": "合同管理服务", "value": "合同管理服务", "source": "ai"},
                {"label": "劳动用工合规", "value": "劳动用工合规", "source": "ai"},
            ],
            "typical_customers": [
                {"label": "需要合规支持的企业", "value": "需要合规支持的企业", "source": "ai"},
                {"label": "有合同管理需求的企业", "value": "有合同管理需求的企业", "source": "ai"},
                {"label": "关注劳动用工风险的企业", "value": "关注劳动用工风险的企业", "source": "ai"},
            ],
        }

    monkeypatch.setattr(sales_api, "suggest_profile_options", fake_suggest)
    _at_step(deps, "business_hint")

    handled = _run(deps, "法律服务", "lead")

    assert handled is True
    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert card["id"] == "seller_profile"
    assert "合同审查支持" in values
    assert "企业合规咨询" in values
    assert "法律顾问服务" not in values
    assert "合同管理服务" not in values
    assert "AI-GEO优化" not in values
    assert _capabilities(deps) == []


def test_service_scope_ai_candidates_go_to_service_scope_card_only(deps):
    """AI-generated service directions may only go into the service direction card, not mixed into capabilities."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "step": "service_scope",
        "seller_capabilities": ["合同审查支持"],
        "seller_team": "11-100 名员工",
        "profile_suggestions": {
            "business_hint_key": "法律服务",
            "service_capabilities": [
                {"label": "合同审查支持", "value": "合同审查支持", "source": "ai"},
            ],
            "service_scope": [
                {"label": "法律顾问服务", "value": "法律顾问服务", "source": "ai"},
                {"label": "合同管理服务", "value": "合同管理服务", "source": "ai"},
                {"label": "劳动用工合规", "value": "劳动用工合规", "source": "ai"},
            ],
            "typical_customers": [],
        },
        "business_hint": ["法律服务"],
    })
    from agents.sales_agent.api import _append_lead_service_scope_guidance

    _append_lead_service_scope_guidance(deps, "lead")

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert card["id"] == "seller_service_scope"
    assert card["field"] == "service_scope"
    assert "法律顾问服务" in values
    assert "合同审查支持" not in values


def test_service_scope_answer_writes_scope_not_capabilities(deps):
    """Service direction card answers write only service_scope_json and must not pollute service_capabilities_json."""
    _at_step(deps, "service_scope")
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow["seller_capabilities"] = ["合同审查支持"]
    flow["seller_team"] = "11-100 名员工"

    assert _run(deps, "法律顾问服务、合同管理服务", "lead") is True

    assert _capabilities(deps) == []
    assert _service_scope(deps) == ["法律顾问服务", "合同管理服务"]
    assert _flow_slot(deps, "service_scope") == ["法律顾问服务", "合同管理服务"]


def test_empty_profile_ai_failure_still_shows_custom_input_card(deps, monkeypatch):
    """When AI fails, don't fall back to hardcoded capabilities; the card still allows manual additions."""
    import agents.sales_agent.api as sales_api

    async def fake_suggest(_context):
        return {"service_capabilities": [], "service_scope": [], "typical_customers": []}

    monkeypatch.setattr(sales_api, "suggest_profile_options", fake_suggest)
    _at_step(deps, "business_hint")

    assert _run(deps, "财税咨询", "lead") is True
    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert card["id"] == "seller_profile"
    assert card.get("allow_custom_option") is True
    assert "AI-GEO优化" not in values
    assert "SEO技术团队" not in values
    assert "处理未完成" not in _replies(deps)[-1]


def test_saved_typical_customers_replace_fixed_fallback(deps):
    """With an existing suitable-customer profile, show only profile candidates, without fixed GEO/SEO customer types."""
    _seller_profile(deps, typical_customers=["需要财务规范化的企业", "多门店连锁经营企业"])
    from agents.sales_agent.api import _append_lead_typical_customers_guidance

    _append_lead_typical_customers_guidance(deps, "lead")

    values = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]
    assert "需要财务规范化的企业" in values
    assert "多门店连锁经营企业" in values
    assert "有官网但搜索获客一般的企业" not in values
    assert "想提升 AI 曝光的品牌方" not in values



def test_ai_typical_customers_replace_fixed_fallback(deps):
    """With empty suitable customers, use the already-generated AI candidates, without fixed GEO/SEO customer types."""
    flow = deps.tool_state.setdefault("lead_flow", {})
    flow.update({
        "business_hint": ["法律服务"],
        "profile_suggestions": {
            "business_hint_key": "法律服务",
            "service_capabilities": [],
            "service_scope": [],
            "typical_customers": [
                {"label": "需要合规支持的企业", "value": "需要合规支持的企业", "source": "ai"},
                {"label": "有合同管理需求的企业", "value": "有合同管理需求的企业", "source": "ai"},
                {"label": "关注劳动用工风险的企业", "value": "关注劳动用工风险的企业", "source": "ai"},
            ],
        },
    })
    from agents.sales_agent.api import _append_lead_typical_customers_guidance

    _append_lead_typical_customers_guidance(deps, "lead")

    values = [str(o.get("value") or "") for o in _last_question_card(deps).get("options") or []]
    assert "需要合规支持的企业" in values
    assert "有官网但搜索获客一般的企业" not in values
    assert "想提升 AI 曝光的品牌方" not in values


def test_continue_generates_typical_customer_candidates_from_existing_profile(deps, monkeypatch):
    """Half-empty profile: with capabilities but empty suitable customers, continuing must still use AI-generated customer type candidates."""
    import agents.sales_agent.api as sales_api

    async def fake_suggest(context):
        assert context["business_hint"] == []
        assert context["service_capabilities"] == ["财务能力"]
        return {
            "service_capabilities": [],
            "service_scope": [],
            "typical_customers": [
                {"label": "需要财务规范化的企业", "value": "需要财务规范化的企业", "source": "ai"},
                {"label": "有预算管理需求的企业", "value": "有预算管理需求的企业", "source": "ai"},
                {"label": "需要经营报表支持的企业", "value": "需要经营报表支持的企业", "source": "ai"},
            ],
        }

    monkeypatch.setattr(sales_api, "suggest_profile_options", fake_suggest)
    _seller_profile(deps, service_capabilities=["财务能力"], service_scope=["财务咨询"], company_size="1001+ 名员工")
    # Service scope must be prefilled too: it's the slot before "suitable customers" in SLOT_ORDER; if empty,
    # clicking "continue" pops seller_service_scope first and never reaches the suitable-customer candidates tested here.
    _at_summary(deps, seller_capabilities=["财务能力"], service_scope=["财务咨询"], seller_team="1001+ 名员工")

    assert _run(deps, "继续", "lead") is True

    card = _last_question_card(deps)
    values = [str(o.get("value") or "") for o in card.get("options") or []]
    assert card["id"] == "seller_typical_customers"
    assert "需要财务规范化的企业" in values
    assert "有预算管理需求的企业" in values
    assert "有官网但搜索获客一般的企业" not in values
    assert _typical_customers(deps) == []


def test_lookup_intent_uses_history_harness_at_any_step(deps):
    """Not just the profile card -- while any card is pending, a recommendation-history query must not be taken as its answer."""
    for step in ("typical_customers", "target_company_size", "target_industries", "target_regions"):
        _at_step(deps, step)
        assert _run(deps, "之前推荐过哪些公司", "lookup") is True, f"step={step} 时没有走历史查询兜底"


def test_free_text_capability_still_accepted(deps):
    """Free-text capabilities (not in the options) must be accepted -- don't make validation a whitelist."""
    _at_step(deps, "seller_capabilities")
    assert _run(deps, "跨境独立站代运营", "lead") is True
    assert _capabilities(deps) == ["跨境独立站代运营"]


# --- First-turn greeting/small talk gets the entry card; later small talk in the session goes to plain chat ---

@pytest.mark.parametrize("greeting", ["hello/", "hello?", "Hello!", "你好~", "hi", "在吗。"])
def test_first_greeting_gets_the_same_entry_card(deps, greeting):
    """A greeting as the first message of a new session gets the entry card, so the user knows they can find customers/audit a website."""
    handled = _run(deps, greeting, "chat")
    assert handled is True, f"{greeting!r} 第一轮应该给入口卡"
    replies = _replies(deps)
    assert replies, "该给引导回复"
    assert "sales_agent_entry_action" in _last_metadata(deps), "该带入口按钮"


def test_repeat_greeting_uses_plain_chat_after_entry_card(deps):
    """The entry card only guides the first turn; greeting again mid-chat goes to plain_chat, without pushing the card repeatedly."""
    _run(deps, "你好", "chat")
    assert _run(deps, "hello?", "chat") is False
    metas = _all_metadata(deps)
    assert sum("sales_agent_entry_action" in m for m in metas) == 1


@pytest.mark.parametrize("message", ["你知道我是谁吗？", "这个是谁？"])
def test_identity_and_reference_boundaries_do_not_fall_to_generic_error(deps, message):
    """Identity/reference questions we can't guess must get a deterministic boundary explanation, never the frontend "processing incomplete"."""
    handled = _run(deps, message, "chat")
    assert handled is True
    reply = _replies(deps)[-1]
    assert reply
    assert "处理未完成" not in reply
    assert "Sales Agent 本次处理失败" not in reply


@pytest.mark.parametrize("message", ["我的画像是什么", "用户画像是啥", "我方画像"])
def test_seller_profile_query_reads_profile_without_model_fallback(deps, message):
    """Asking about our/the user's profile is a business query; read the saved profile directly, don't fall to the plain chat fallback."""
    _seller_profile(
        deps,
        service_capabilities=["AI-GEO优化", "SEO技术团队"],
        company_size="11-100 名员工",
    )

    handled = _run(deps, message, "chat")

    assert handled is True
    reply = _replies(deps)[-1]
    assert "我方画像" in reply
    assert "没有稳定生成回复" not in reply

    # Profile content now goes through a read-only profile card instead of the body text -- assertions must check the card,
    # otherwise "the profile was read" isn't tested at all.
    card = (json.loads(_last_metadata(deps) or "{}").get("tool_cards") or [])[-1]
    assert card["tool"] == "sales_agent_seller_profile_snapshot"
    data = card["data"]
    assert data["readonly"] is True
    assert "AI-GEO优化" in data["service_capabilities"]
    assert data["company_size"] == "11-100 名员工"
    # The read-only card only offers "update profile"; no "continue" that makes the user think they entered the recommendation flow.
    assert [a["label"] for a in data["actions"]] == ["更新画像"]


def test_chat_fallback_allows_continued_conversation():
    """When the plain chat model is unstable, the fallback should still feel conversational, not just ask for business parameters."""
    from agents.sales_agent.api import _chat_fallback_reply

    reply = _chat_fallback_reply("随便聊聊", "chat")

    assert "如果你的问题和销售增长无关" in reply
    assert "我会尽量帮你解答" in reply
    assert "查今天发了几封邮件" in reply
    assert "可以的" not in reply
    assert "帮我写跟进邮件" not in reply


@pytest.mark.parametrize("message", ["你是什么工具呢？", "你有什么能力呢？", "你吃饭了吗？", "你喝水了嘛？"])
def test_first_non_business_chat_gets_entry_card_without_opening_lead_flow(deps, message):
    """The first casual chat/capability question gets the entry card, but must not start the find-customers profile flow."""
    assert _run(deps, message, "chat") is True
    assert "sales_agent_entry_action" in _last_metadata(deps)
    assert _capabilities(deps) == []


def test_later_non_business_chat_can_go_to_model_without_opening_cards(deps):
    """After the entry card, casual chat goes to plain_chat; model failures are caught by the chat fallback."""
    _run(deps, "你好", "chat")
    assert _run(deps, "你吃饭了吗？", "chat") is False
    assert len(_replies(deps)) == 1


@pytest.mark.parametrize("message", ["你是谁？", "你吃饭了吗？", "你喝水了吗？"])
def test_non_business_chat_does_not_inherit_recommendation_history_context(deps, message):
    """After querying the recommendation list last turn, the next casual message must not be taken as "who are those companies" and push the history card again."""
    from agents.sales_agent.api import _remember_last_stats_query

    _run(deps, "你好", "chat")
    _remember_last_stats_query(deps, "recommendation", "yesterday", "detail")

    assert _run(deps, message, "chat") is False
    assert len(_replies(deps)) == 1


# --- Suitable customers: saved into the profile directly on submit ---
#
# Submitting the "suitable customers" card is the user's confirmed customer type and must be written back to the profile; later email context reads it
# from brand materials / the mirrored profile. The write-back is still append-only and must not overwrite existing /admin content.

def test_typical_customers_submit_saves_to_the_brand_profile(deps):
    """The suitable-customer answer stays in this turn's lead_flow and is also written to the profile for later emails."""
    _at_step(deps, "typical_customers")
    handled = _run(deps, "跨境独立站代运营、医疗行业内容合规", "lead")

    assert handled
    assert _flow_slot(deps, "typical_customers") == ["跨境独立站代运营、医疗行业内容合规"]
    assert _typical_customers(deps) == ["跨境独立站代运营、医疗行业内容合规"]


def test_custom_items_survive_in_the_flow_without_any_capability_answer(deps):
    """The in-card custom item is the only new value this turn -- don't drop the whole turn because "capabilities/size unchanged"."""
    _at_step(deps, "typical_customers")
    _run(deps, "只做工业制造业客户", "lead")

    assert "只做工业制造业客户" in _flow_slot(deps, "typical_customers")


# --- 2026-08-25 production: 「有哪些开信的公司?」 ("which companies opened the email?") returned our profile card ---

def test_open_query_uses_delivery_lookup_without_opening_cards(deps):
    """A question about email opens should go to the deterministic email query, never pop the find-customers profile card."""
    handled = _run(deps, "有哪些开信的公司？", "delivery_lookup")

    assert handled is True
    assert _replies(deps)
    assert "开信" in _replies(deps)[-1] or "阅读" in _replies(deps)[-1]
    assert _capabilities(deps) == []


def test_plain_chat_read_only_question_opens_no_card(deps):
    """The plain chat fallback must still not open the profile card because of generic nouns like "company/enterprise"."""
    assert _run(deps, "这几家企业怎么样", "chat") is False
    assert _replies(deps) == []
    assert _capabilities(deps) == []


def test_read_only_intent_blocks_the_profile_card(deps):
    """If the classifier says read-only, read-only wins -- even if the sentence looks like finding customers, no profile card.

    The classifier's rule is "when unsure pick lookup -- a query is cheap, a run is expensive" (flow/intent.py).
    So this decision must really block the write path; otherwise the separate word list in `_is_find_customer_entry`
    still pops the card -- the original gate hardcoded only {lookup, faq}, missing chat, the largest fallback bucket.
    """
    assert _run(deps, "推荐企业", "chat") is False
    assert _replies(deps) == []


def test_empty_profile_lead_entry_opens_business_hint_card(deps):
    """An empty profile entering the find-customers flow asks business direction first, not a hardcoded capability fallback."""
    assert _run(deps, "推荐 50 家工业制造企业", "lead_recommendation") is True
    assert _replies(deps)
    assert "tool_cards" in _last_metadata(deps)
    card = _last_question_card(deps)
    assert card["id"] == "seller_business_hint"
    assert card["field"] == "business_hint"


def test_first_greeting_gets_entry_card_then_later_greeting_uses_plain_chat(deps):
    """First greeting gets the entry card; later greetings/small talk go to plain_chat without popping the card again."""
    assert _run(deps, "你好", "chat") is True
    assert _replies(deps) != []

    assert _run(deps, "在吗", "chat") is False
    assert _run(deps, "你吃饭了吗", "chat") is False
    assert len(_replies(deps)) == 1
    assert _capabilities(deps) == []


# --- Next-batch recommendations: "continue" reuses the previous filters without reopening filter cards ---


def test_continue_recommendation_uses_latest_campaign_without_reasking_cards(deps, monkeypatch):
    """When the user says "continue recommending", run the next batch on the previous campaign without re-asking industry/region."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": False, "missing_config": ["TENCENT_SEARCH_API_KEY"]})
    # Service scope must be on the brand row: next-batch computes "what's missing" from the brand profile; if this slot is empty it's
    # judged "new conversation, business unknown" and pops the service direction card instead of continuing the batch.
    seller = _seller_profile(deps, service_capabilities=["AI-GEO优化"], service_scope=["AI 可见性检测"], company_size="11-100 名员工")
    previous = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        requested_limit=1,
        limit=1,
        status="completed",
    )
    deps.db.add(previous)
    deps.db.flush()
    deps.db.add_all([
        SalesAgentCompanyORM(id=1, name="上一批企业", industry="企业服务", region="上海", source="manual", data_status="active"),
        SalesAgentCompanyORM(id=2, name="续批企业", industry="企业服务", region="上海", source="manual", data_status="active"),
        SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=previous.id, company_id=1, rank=1, status="Recommended"),
    ])
    deps.db.commit()

    assert _run(deps, "在推荐一批", "lead_recommendation") is True

    assert "已按上一轮筛选条件继续推荐" in _replies(deps)[-1]
    assert "recommend_companies" in _last_metadata(deps)
    assert _step(deps) == ""
    new_campaign = deps.db.query(SalesAgentRecommendationCampaignORM).order_by(SalesAgentRecommendationCampaignORM.id.desc()).first()
    assert new_campaign.id != previous.id
    names = [row.name for row in deps.db.query(SalesAgentCompanyORM).join(SalesAgentLeadRecommendationORM, SalesAgentLeadRecommendationORM.company_id == SalesAgentCompanyORM.id).filter(SalesAgentLeadRecommendationORM.campaign_id == new_campaign.id).all()]
    assert names == ["续批企业"]


def test_continue_recommendation_after_ready_step_uses_next_batch(deps, monkeypatch):
    """Right after a recommendation, step may still be ready; "continue recommending" must still run the next batch, not fall into the model path."""
    import agents.sales_agent.service.recommend as recommend_service

    monkeypatch.setattr(recommend_service, "search_capability", lambda: {"search_available": False, "missing_config": ["TENCENT_SEARCH_API_KEY"]})
    # Service scope must be on the brand row: next-batch computes "what's missing" from the brand profile; if this slot is empty it's
    # judged "new conversation, business unknown" and pops the service direction card instead of continuing the batch.
    seller = _seller_profile(deps, service_capabilities=["AI-GEO优化"], service_scope=["AI 可见性检测"], company_size="11-100 名员工")
    previous = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        requested_limit=1,
        limit=1,
        status="completed",
    )
    deps.db.add(previous)
    deps.db.flush()
    deps.db.add_all([
        SalesAgentCompanyORM(id=1, name="上一批企业", industry="企业服务", region="上海", source="manual", data_status="active"),
        SalesAgentCompanyORM(id=2, name="续批企业", industry="企业服务", region="上海", source="manual", data_status="active"),
        SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=previous.id, company_id=1, rank=1, status="Recommended"),
    ])
    _set_flow(deps, {
        "step": "ready",
        "seller_capabilities": ["AI-GEO优化"],
        "service_scope": ["AI 可见性检测"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["有官网但搜索获客一般的企业"],
        "target_company_size": "11-100 名员工",
        "target_industries": ["企业服务"],
        "target_regions": ["上海"],
        "brand_id": seller.id,
    })
    deps.db.commit()

    assert _run(deps, "继续推荐一批", "lead_recommendation") is True

    assert "已按上一轮筛选条件继续推荐" in _replies(deps)[-1]
    assert "recommend_companies" in _last_metadata(deps)
    assert _step(deps) == ""
    new_campaign = deps.db.query(SalesAgentRecommendationCampaignORM).order_by(SalesAgentRecommendationCampaignORM.id.desc()).first()
    assert new_campaign.id != previous.id
    names = [row.name for row in deps.db.query(SalesAgentCompanyORM).join(SalesAgentLeadRecommendationORM, SalesAgentLeadRecommendationORM.company_id == SalesAgentCompanyORM.id).filter(SalesAgentLeadRecommendationORM.campaign_id == new_campaign.id).all()]
    assert names == ["续批企业"]


def test_continue_recommendation_without_previous_campaign_guides_user(deps):
    """Without a previous recommendation, explicitly say filters are missing, not a generic error."""
    assert _run(deps, "继续推荐", "lead_recommendation") is True
    assert "还没有找到上一轮推荐条件" in _replies(deps)[-1]
    assert "recommend_companies" in _last_metadata(deps)


def test_profile_continue_action_is_not_continue_batch_while_step_pending(deps):
    """"Continue" inside a card flow still confirms the current card; next-batch logic must not hijack it."""
    _at_summary(
        deps,
        seller_capabilities=["AI-GEO优化"],
        seller_team="11-100 名员工",
        typical_customers=["有官网但搜索获客一般的企业"],
        target_company_size="11-100 名员工",
        target_industries=["企业服务"],
    )

    assert _run(deps, "继续", "lead") is True
    assert "还没有找到上一轮推荐条件" not in _replies(deps)[-1]


# --- Profile card "continue": skip what's already in the profile, still confirm suitable customers every turn ---
#
# Capabilities, service scope and team size are our long-term attributes; if filled on /admin, don't ask again.
# "Which customers you suit" is still confirmed every turn, since whom to target this turn is the user's choice; but after submit it is
# incrementally saved into the profile for the next card options and later email context.

def test_continue_skips_capabilities_but_still_asks_who_to_target(deps):
    """Capabilities filled on /admin -- "continue" skips them, but "suitable customers" is still asked."""
    _at_summary(deps, seller_capabilities=["AI-GEO优化"], service_scope=["AI 可见性检测"], seller_team="11-100 名员工")
    assert _run(deps, "继续", "lead") is True

    assert _step(deps) == "typical_customers", "适合客户被跳过了,它应该每轮都问"
    assert "适合客户" in _last_metadata(deps), "适合客户那张卡没出"


def test_continue_asks_who_to_target_even_when_admin_filled_it(deps):
    """Even if suitable customers is filled on /admin, still ask -- whom to target this turn is for the user to confirm.

    This guards that `_append_seller_profile_summary_card` no longer prefills typical_customers:
    once prefilled, the router sees the slot filled and skips it, so this question would never appear.
    """
    _seller_profile(deps, typical_customers=["口腔诊所"])
    _at_summary(deps, seller_capabilities=["AI-GEO优化"], service_scope=["AI 可见性检测"], seller_team="11-100 名员工")
    assert _run(deps, "继续", "lead") is True

    assert _step(deps) == "typical_customers"


def test_choosing_who_to_target_appends_to_the_brand_profile(deps):
    """This turn's "suitable customers" are appended to the profile, but must not overwrite existing /admin content."""
    row = _seller_profile(deps, typical_customers=["口腔诊所"])
    _at_summary(deps, seller_capabilities=["AI-GEO优化"], service_scope=["AI 可见性检测"], seller_team="11-100 名员工")
    _run(deps, "继续", "lead")
    _run(deps, "连锁美容机构", "lead")

    deps.db.refresh(row)
    assert json.loads(row.typical_customers_json) == ["口腔诊所", "连锁美容机构"]


def test_continue_does_not_rewrite_the_admin_profile(deps):
    """Clicking "continue" is only a confirmation, not a resubmission -- not a single character may be written to the brand row.

    Via `_advance_lead_flow` changed is empty, so the profile snapshot step must not fire; if it did,
    what was filled on /admin would be overwritten by the session's (possibly incomplete) copy.
    """
    _at_summary(deps, seller_capabilities=["AI-GEO优化"], typical_customers=["口腔诊所"])
    _run(deps, "继续", "lead")

    assert deps.db.query(SalesAgentSellerProfileORM).count() == 0, "凭空写出了一行画像"


def test_lookup_with_a_url_is_not_hijacked_by_the_detection_card(deps):
    """「www.abc.com 我给他发过邮件吗?」 ("did I email www.abc.com?") is a history query; a URL in it must not let the audit card hijack it."""
    assert _run(deps, "www.abc.com 我给他发过邮件吗?", "delivery_lookup") is True
    assert _replies(deps)
    assert "发送" in _replies(deps)[-1] or "邮件" in _replies(deps)[-1]


def test_latest_email_subject_query_reads_saved_subject(deps):
    """Asking for the last email's subject must not fall back to a "how many sent today" count."""
    _delivery(deps, subject="关于测试公司在AI搜索中的表现", content="这是一封报告跟进邮件。")

    assert _run(deps, "发出的邮箱标题是什么", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "邮件标题:关于测试公司在AI搜索中的表现" in reply
    assert "成功发送" not in reply
    assert "涉及" not in reply


def test_latest_email_content_query_reads_saved_content(deps):
    _delivery(deps, subject="跟进", content="您好,这是完整邮件正文。")

    assert _run(deps, "今天最后一封信的内容", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "邮件内容" in reply
    assert "您好,这是完整邮件正文。" in reply


def test_latest_email_sender_query_reads_saved_account(deps):
    _delivery(deps, subject="跟进", content="正文", from_email="bd@example.com", from_name="BD")

    assert _run(deps, "发送的邮件的账号是什么", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "发件账号:BD <bd@example.com>" in reply
    assert "成功发送" not in reply


def test_second_email_subject_query_uses_ordinal(deps):
    _delivery(deps, subject="第一封标题", content="第一封正文", sent_at=datetime.utcnow() - timedelta(hours=3))
    _delivery(deps, subject="第二封标题", content="第二封正文", sent_at=datetime.utcnow() - timedelta(hours=2))
    _delivery(deps, subject="第三封标题", content="第三封正文", sent_at=datetime.utcnow() - timedelta(hours=1))

    assert _run(deps, "今天第二封邮件的标题", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "第 2 封" in reply
    assert "第二封标题" in reply
    assert "第一封标题" not in reply
    assert "第三封标题" not in reply


def test_latest_email_detail_without_record_has_clear_fallback(deps):
    assert _run(deps, "今天最后一封信的内容", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "没有找到已发送邮件记录" in reply
    assert "不能确认邮件标题、内容、账号或发送状态" in reply
    assert "处理未完成" not in reply
    assert "成功发送" not in reply


def test_latest_email_query_uses_latest_delivery(deps):
    _delivery(deps, subject="旧标题", content="旧正文", sent_at=datetime.utcnow() - timedelta(hours=2))
    _delivery(deps, subject="新标题", content="新正文", sent_at=datetime.utcnow() - timedelta(minutes=1))

    assert _run(deps, "今天最后一封信的标题是什么", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "新标题" in reply
    assert "旧标题" not in reply


def test_latest_three_email_subject_query_lists_recent_rows(deps):
    _delivery(deps, subject="第一封标题", content="第一封正文", sent_at=datetime.utcnow() - timedelta(hours=4))
    _delivery(deps, subject="第二封标题", content="第二封正文", sent_at=datetime.utcnow() - timedelta(hours=3))
    _delivery(deps, subject="第三封标题", content="第三封正文", sent_at=datetime.utcnow() - timedelta(hours=2))
    _delivery(deps, subject="第四封标题", content="第四封正文", sent_at=datetime.utcnow() - timedelta(hours=1))

    assert _run(deps, "今天最后三封邮件的标题", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "最近 3 封" in reply
    assert "第四封标题" in reply
    assert "第三封标题" in reply
    assert "第二封标题" in reply
    assert "第一封标题" not in reply
    assert "成功发送" not in reply


def test_recent_three_email_sender_query_lists_accounts(deps):
    _delivery(deps, subject="邮件一", content="正文一", sent_at=datetime.utcnow() - timedelta(hours=3), from_email="one@example.com", from_name="一号")
    _delivery(deps, subject="邮件二", content="正文二", sent_at=datetime.utcnow() - timedelta(hours=2), from_email="two@example.com", from_name="二号")
    _delivery(deps, subject="邮件三", content="正文三", sent_at=datetime.utcnow() - timedelta(hours=1), from_email="three@example.com", from_name="三号")

    assert _run(deps, "最近 3 封邮件的账号是什么", "delivery_lookup") is True

    reply = _replies(deps)[-1]
    assert "发件账号:三号 <three@example.com>" in reply
    assert "发件账号:二号 <two@example.com>" in reply
    assert "发件账号:一号 <one@example.com>" in reply
    assert "成功发送" not in reply


def test_bare_url_still_opens_the_detection_card(deps):
    """Tightening must not block the main audit entry (pasting just a URL)."""
    assert _run(deps, "www.abc.com", "chat") is True
    assert _replies(deps) != []


# --- Legacy recommendation profiles: historical data compatibility only; the new recommend-companies flow no longer uses/creates them ---


def _recommendation_profile(deps, **fields) -> SalesAgentRecommendationProfileVersionORM:
    row = SalesAgentRecommendationProfileVersionORM(
        profile_key=fields.get("profile_key") or "rec-e2e-1",
        user_id=USER,
        seller_profile_id=fields.get("seller_profile_id"),
        brand_ref_type=fields.get("brand_ref_type") or "none",
        brand_ref_key=fields.get("brand_ref_key") or "",
        brand_name_snapshot=fields.get("brand_name_snapshot") or "",
        brand_context_json=json.dumps(fields.get("brand_context") or {}, ensure_ascii=False),
        version=fields.get("version") or 1,
        event_type=fields.get("event_type") or "created",
        name=fields.get("name") or "法律服务推荐画像",
        source="ai_assisted",
        service_capabilities_json=json.dumps(fields.get("service_capabilities") or ["法律咨询"], ensure_ascii=False),
        service_scope_json=json.dumps(fields.get("service_scope") or ["法律服务"], ensure_ascii=False),
        typical_customers_json=json.dumps(fields.get("typical_customers") or ["需要合同审查的企业"], ensure_ascii=False),
        target_industries_json=json.dumps(fields.get("target_industries") or ["法律服务"], ensure_ascii=False),
        target_regions_json=json.dumps(fields.get("target_regions") or ["上海"], ensure_ascii=False),
        target_company_size=fields.get("target_company_size") or "11-100 名员工",
        summary=fields.get("summary") or "能力:法律咨询；行业:法律服务；地区:上海；规模:11-100 名员工",
    )
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def test_submitted_typical_customers_feed_outreach_brand_profile(deps):
    """Suitable customers are written to the profile on submit; the no-report email context reads exactly this brand profile."""
    from agents.sales_agent.service.outreach import get_outreach_context

    _at_step(deps, "typical_customers")
    assert _run(deps, "连锁美容机构", "lead") is True
    seller = deps.db.query(SalesAgentSellerProfileORM).order_by(SalesAgentSellerProfileORM.id.desc()).first()
    company = SalesAgentCompanyORM(name="美业科技", industry="企业服务", region="上海", source="manual", data_status="active")
    deps.db.add(company)
    deps.db.flush()
    contact = SalesAgentCompanyContactORM(company_id=company.id, email="sales@beauty.example.com", is_reachable=True)
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["企业服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        target_company_size="11-100 名员工",
        status="completed",
    )
    deps.db.add_all([contact, campaign])
    deps.db.flush()
    lead = SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=campaign.id, company_id=company.id, rank=1, status="Recommended")
    deps.db.add(lead)
    deps.db.commit()

    context = get_outreach_context(deps.db, USER, lead.id, channel="email")

    assert context["brand_profile"]["typical_customers"] == ["连锁美容机构"]
    assert context["brand_profile"]["email_material"]["target_scenarios"] == ["连锁美容机构"]


def test_brand_profile_still_drives_outreach_without_recommendation_profile(deps):
    """Original logic must not be lost: without a bound recommendation profile, email context still takes sender info from the brand profile."""
    from agents.sales_agent.service.outreach import get_outreach_context

    seller = _seller_profile(deps, service_capabilities=["品牌官网建设"], company_size="11-100 名员工")
    company = SalesAgentCompanyORM(name="星迈科技", industry="制造业", region="上海", source="manual", data_status="active")
    deps.db.add(company)
    deps.db.flush()
    contact = SalesAgentCompanyContactORM(company_id=company.id, email="sales@xingmai.example.com", is_reachable=True)
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["制造业"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        target_company_size="11-100 名员工",
        service_scope_override_json=json.dumps(["官网建设"], ensure_ascii=False),
        status="completed",
    )
    deps.db.add_all([contact, campaign])
    deps.db.flush()
    lead = SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=campaign.id, company_id=company.id, rank=1, status="Recommended")
    deps.db.add(lead)
    deps.db.commit()

    context = get_outreach_context(deps.db, USER, lead.id, channel="email")

    assert context["recommendation_profile"] is None
    assert context["brand_profile"]["brand_id"] == seller.id
    assert context["brand_profile"]["service_capabilities"] == ["品牌官网建设"]
    assert context["profile_context"]["service"] == "官网建设"
    assert context["profile_context"]["industry"] == "制造业"


def test_outreach_context_includes_recommendation_profile_for_bound_campaign(deps):
    """When the recommendation task is bound to this run's recommendation profile, email context carries both brand and recommendation profiles."""
    from agents.sales_agent.service.outreach import get_outreach_context

    seller = _seller_profile(deps, service_capabilities=["品牌资料里的长期能力"], company_size="11-100 名员工")
    rec_profile = _recommendation_profile(
        deps,
        seller_profile_id=seller.id,
        brand_ref_type="seller_profile",
        brand_ref_key=f"seller_profile:{seller.id}",
        brand_name_snapshot=seller.name,
        service_capabilities=["合同审查", "企业法律顾问"],
        service_scope=["法律服务"],
        typical_customers=["需要常年法务支持的企业"],
        target_industries=["法律服务"],
        target_regions=["上海"],
        target_company_size="11-100 名员工",
    )
    company = SalesAgentCompanyORM(name="法务科技", industry="法律服务", region="上海", source="manual", data_status="active")
    deps.db.add(company)
    deps.db.flush()
    contact = SalesAgentCompanyContactORM(company_id=company.id, email="sales@lawtech.example.com", is_reachable=True)
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["法律服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        target_company_size="11-100 名员工",
        service_scope_override_json=json.dumps(["法律服务"], ensure_ascii=False),
        status="completed",
    )
    deps.db.add_all([contact, campaign])
    deps.db.flush()
    lead = SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=campaign.id, company_id=company.id, rank=1, status="Recommended")
    usage = SalesAgentRecommendationProfileUsageORM(
        profile_key=rec_profile.profile_key,
        profile_version_id=rec_profile.id,
        user_id=USER,
        session_id=deps.session_id,
        campaign_id=campaign.id,
    )
    deps.db.add_all([lead, usage])
    deps.db.commit()

    context = get_outreach_context(deps.db, USER, lead.id, channel="email")

    assert context["brand_profile"]["brand_id"] == seller.id
    assert context["brand_profile"]["service_capabilities"] == ["品牌资料里的长期能力"]
    assert context["recommendation_profile"]["profile_key"] == rec_profile.profile_key
    assert context["recommendation_profile"]["service_capabilities"] == ["合同审查", "企业法律顾问"]
    assert context["recommendation_profile"]["typical_customers"] == ["需要常年法务支持的企业"]
    assert context["recommendation_profile"]["target_industries"] == ["法律服务"]
    assert context["recommendation_profile"]["target_regions"] == ["上海"]
    assert context["recommendation_profile"]["target_company_size"] == "11-100 名员工"
    assert any("recommendation_profile 不为空" in rule for rule in context["copy_rules"])


def test_saved_recommendation_profile_does_not_open_picker_on_lead_entry(deps):
    """The recommend-companies entry only confirms our profile; historical recommendation profiles are no longer inserted into this turn's flow."""
    # All four filled = a brand configured on /admin, so the entry shows the profile confirmation card directly; one missing gets intercepted to ask business direction first.
    seller = _seller_profile(
        deps, service_capabilities=["品牌资料里的长期能力"], service_scope=["长期法务支持"],
        typical_customers=["需要常年法务支持的企业"], company_size="11-100 名员工",
    )
    _recommendation_profile(deps, seller_profile_id=seller.id, target_industries=["法律服务"], target_regions=["上海"], target_company_size="11-100 名员工")

    assert _run(deps, "找客户", "lead_recommendation") is True

    metadata = _last_metadata(deps)
    assert "sales_agent_recommendation_profile_picker" not in metadata
    assert "sales_agent_seller_profile_summary" in metadata
    assert _step(deps) == "seller_profile_summary"


def test_recommendation_result_does_not_show_save_profile_card(deps, monkeypatch):
    """After recommendation, show only the recommended-companies card, no extra "save this recommendation profile" question card."""
    import agents.sales_agent.api as api_module

    def fake_recommend_companies_workflow(db, user_id, **kwargs):
        campaign = SalesAgentRecommendationCampaignORM(
            user_id=user_id,
            seller_profile_id=kwargs.get("seller_profile_id"),
            session_id=kwargs.get("session_id"),
            target_industries_json=json.dumps(kwargs.get("industries") or [], ensure_ascii=False),
            target_regions_json=json.dumps(kwargs.get("regions") or [], ensure_ascii=False),
            target_company_size=kwargs.get("target_company_size") or "",
            actual_count=1,
            status="completed",
        )
        db.add(campaign)
        db.flush()
        return {"status": "ok", "count": 1, "actual_count": 1, "campaign": {"id": campaign.id}}

    monkeypatch.setattr(api_module, "recommend_companies_workflow", fake_recommend_companies_workflow)
    _set_flow(deps, {
        "seller_capabilities": ["合同审查"],
        "service_scope": ["法律顾问服务"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["需要合同审查的企业"],
        "target_company_size": "11-100 名员工",
        "target_industries": ["法律服务"],
        "target_regions": ["上海"],
        "step": "target_regions",
    })

    assert _run(deps, "上海", "lead") is True

    metadata = _last_metadata(deps)
    assert "recommend_companies" in metadata
    assert "sales_agent_recommendation_profile_save" not in metadata
    # step is cleared after the recommendation run: it's derived from slots by `next_step(state)`, no lingering "ready" intermediate state.
    assert _step(deps) == ""


def test_legacy_recommendation_profile_choice_does_not_recommend_or_record_usage(deps, monkeypatch):
    """Clicking the legacy recommendation profile card only exits the old step; it is no longer used as this turn's filters."""
    import agents.sales_agent.api as api_module

    seller = _seller_profile(deps, service_capabilities=["品牌资料里的长期能力"], company_size="11-100 名员工")
    rec_profile = _recommendation_profile(
        deps,
        seller_profile_id=seller.id,
        service_capabilities=["合同审查"],
        service_scope=["法律服务"],
        typical_customers=["需要常年法务支持的企业"],
        target_industries=["法律服务"],
        target_regions=["上海"],
        target_company_size="11-100 名员工",
    )
    calls: list[dict] = []

    def fake_recommend_companies_workflow(db, user_id, **kwargs):
        calls.append(kwargs)
        return {"status": "ok", "count": 1, "actual_count": 1, "campaign": {"id": 123}}

    monkeypatch.setattr(api_module, "recommend_companies_workflow", fake_recommend_companies_workflow)
    _set_flow(deps, {"step": "recommendation_profile"})

    assert _run(deps, f"rec_profile:{rec_profile.profile_key}:{rec_profile.id}", "lead") is True

    flow = deps.tool_state["lead_flow"]
    assert flow.get("step") == ""
    assert "seller_capabilities" not in flow
    assert "target_industries" not in flow
    assert calls == []
    assert deps.db.query(SalesAgentRecommendationProfileUsageORM).count() == 0
    assert "不再使用“推荐画像”选择卡" in _replies(deps)[-1]


def test_legacy_save_recommendation_profile_card_has_no_write_side_effect(deps):
    """The legacy "save this recommendation profile" card only exits the old step; no more INSERT of recommendation profile/usage."""
    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    campaign = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(["法律服务"], ensure_ascii=False),
        target_regions_json=json.dumps(["上海"], ensure_ascii=False),
        target_company_size="11-100 名员工",
        status="completed",
    )
    deps.db.add(campaign)
    deps.db.commit()
    _set_flow(deps, {
        "step": "recommendation_profile_save",
        "seller_capabilities": ["合同审查"],
        "service_scope": ["法律服务"],
        "typical_customers": ["需要常年法务支持的企业"],
        "target_industries": ["法律服务"],
        "target_regions": ["上海"],
        "target_company_size": "11-100 名员工",
        "last_recommendation_campaign_id": campaign.id,
    })

    assert _run(deps, "保存为本次推荐画像", "lead") is True

    deps.db.refresh(seller)
    assert json.loads(seller.service_capabilities_json) == ["品牌原能力"]
    assert deps.db.query(SalesAgentRecommendationProfileVersionORM).filter_by(user_id=USER).count() == 0
    assert deps.db.query(SalesAgentRecommendationProfileUsageORM).filter_by(campaign_id=campaign.id).count() == 0
    assert deps.tool_state["lead_flow"].get("step") == ""
    assert "不用单独保存画像" in _replies(deps)[-1]


@pytest.mark.parametrize("message", [
    # matched by `_is_save_recommendation_profile_action`
    "保存为本次推荐画像", "保存本次推荐画像", "保存推荐画像", "保存画像",
    # matched by `_is_skip_recommendation_profile_save_action`
    "本次不保存", "不保存", "不用保存", "直接推荐", "跳过保存",
])
def test_every_legacy_save_card_answer_leaves_the_dead_step(deps, message):
    """Every wording on this legacy card must exit, not just the one above.

    The one above only runs "save as this recommendation profile", while the real bug was "one entry works,
    another is stuck" -- the save fallback's exclusion list missed RECOMMENDATION_PROFILE_SAVE_STEP,
    so users clicking save were grabbed by the fallback first and step stayed on this deprecated step forever. Nine wordings spread across
    two matcher functions, any of which could be intercepted by some early-return branch again, so the whole row is pinned together.
    """
    _set_flow(deps, {
        "step": "recommendation_profile_save",
        "seller_capabilities": ["合同审查"],
        "service_scope": ["法律服务"],
        "typical_customers": ["需要常年法务支持的企业"],
        "target_industries": ["法律服务"],
        "target_regions": ["上海"],
        "target_company_size": "11-100 名员工",
    })

    assert _run(deps, message, "lead") is True

    assert deps.tool_state["lead_flow"].get("step") == "", f"{message!r} 之后还卡在废弃步骤上"
    assert "不用单独保存画像" in _replies(deps)[-1], f"{message!r} 被别的分支截胡了"


# --- Recommendation profile: one per brand, updated in place ---


_REC_FLOW = {
    "seller_capabilities": ["合同审查"],
    "service_scope": ["法律服务"],
    "typical_customers": ["需要常年法务支持的企业"],
    "target_industries": ["法律服务"],
    "target_regions": ["上海"],
    "target_company_size": "11-100 名员工",
}


def _rec_flow(**overrides) -> dict:
    return {**_REC_FLOW, **overrides}


def _campaign(deps, seller, *, industries, regions, size="11-100 名员工") -> SalesAgentRecommendationCampaignORM:
    row = SalesAgentRecommendationCampaignORM(
        user_id=USER,
        seller_profile_id=seller.id,
        target_industries_json=json.dumps(industries, ensure_ascii=False),
        target_regions_json=json.dumps(regions, ensure_ascii=False),
        target_company_size=size,
        status="completed",
    )
    deps.db.add(row)
    deps.db.commit()
    deps.db.refresh(row)
    return row


def _versions(deps) -> list[SalesAgentRecommendationProfileVersionORM]:
    return (
        deps.db.query(SalesAgentRecommendationProfileVersionORM)
        .filter_by(user_id=USER)
        .order_by(SalesAgentRecommendationProfileVersionORM.id)
        .all()
    )


def _upsert(deps, seller, flow) -> SalesAgentRecommendationProfileVersionORM:
    from agents.sales_agent.service.recommendation_profiles import upsert_recommendation_profile_from_flow

    return upsert_recommendation_profile_from_flow(
        deps.db,
        USER,
        flow,
        seller_profile_id=seller.id,
        brand_name_snapshot=seller.name,
        source="chat_auto",
    )


def test_upsert_recommendation_profile_keeps_one_row_for_the_same_brand(deps):
    """Two runs with the same filters: the profile table has one row, not two."""
    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")

    first = _upsert(deps, seller, _rec_flow())
    second = _upsert(deps, seller, _rec_flow())

    rows = _versions(deps)
    assert len(rows) == 1
    assert first.id == second.id
    assert rows[0].version == 1


def test_upsert_recommendation_profile_overwrites_conditions_in_place(deps):
    """Changed filters don't create a new row either -- a profile is state, not a log."""
    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")

    _upsert(deps, seller, _rec_flow(target_industries=["制造业"], target_regions=["上海"]))
    _upsert(deps, seller, _rec_flow(target_industries=["医疗健康"], target_regions=["北京"]))

    rows = _versions(deps)
    assert len(rows) == 1
    assert json.loads(rows[0].target_industries_json) == ["医疗健康"]
    assert json.loads(rows[0].target_regions_json) == ["北京"]
    assert rows[0].version == 1


def test_upsert_recommendation_profile_never_touches_the_brand_profile(deps):
    """The recommendation profile is updated in place and must not touch the brand profile -- the two layers are one-way."""
    seller = _seller_profile(
        deps,
        service_capabilities=["品牌原能力"],
        service_scope=["品牌原服务"],
        typical_customers=["品牌原客户"],
        company_size="11-100 名员工",
    )

    _upsert(deps, seller, _rec_flow(seller_capabilities=["推荐画像的能力"]))

    deps.db.refresh(seller)
    assert json.loads(seller.service_capabilities_json) == ["品牌原能力"]
    assert json.loads(seller.service_scope_json) == ["品牌原服务"]
    assert json.loads(seller.typical_customers_json) == ["品牌原客户"]


def test_auto_path_writes_one_profile_and_one_usage_per_recommendation(deps):
    """Two runs on the automatic path: 1 profile row, 2 usage rows -- usage is the only campaign->profile index and can't be omitted."""
    from agents.sales_agent.api import _ensure_recommendation_profile_usage_for_campaign

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    first_campaign = _campaign(deps, seller, industries=["制造业"], regions=["上海"])
    second_campaign = _campaign(deps, seller, industries=["医疗健康"], regions=["北京"])

    flow_one = _rec_flow(target_industries=["制造业"], target_regions=["上海"])
    _set_flow(deps, flow_one)
    _ensure_recommendation_profile_usage_for_campaign(deps, flow_one, first_campaign.id, "lead")

    flow_two = _rec_flow(target_industries=["医疗健康"], target_regions=["北京"])
    _set_flow(deps, flow_two)
    _ensure_recommendation_profile_usage_for_campaign(deps, flow_two, second_campaign.id, "lead")

    rows = _versions(deps)
    assert len(rows) == 1
    assert json.loads(rows[0].target_industries_json) == ["医疗健康"]
    usages = deps.db.query(SalesAgentRecommendationProfileUsageORM).filter_by(user_id=USER).all()
    assert len(usages) == 2
    assert {u.campaign_id for u in usages} == {first_campaign.id, second_campaign.id}


def test_resending_an_old_campaign_still_uses_that_rounds_conditions(deps):
    """The most important one: after the profile is overwritten by a later run, resending old emails still uses that run's campaign snapshot filters."""
    from agents.sales_agent.api import _ensure_recommendation_profile_usage_for_campaign
    from agents.sales_agent.service.outreach import get_outreach_context

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    old_campaign = _campaign(deps, seller, industries=["制造业"], regions=["上海"])
    company = SalesAgentCompanyORM(name="星迈科技", industry="制造业", region="上海", source="manual", data_status="active")
    deps.db.add(company)
    deps.db.flush()
    deps.db.add(SalesAgentCompanyContactORM(company_id=company.id, email="sales@xingmai.example.com", is_reachable=True))
    lead = SalesAgentLeadRecommendationORM(user_id=USER, campaign_id=old_campaign.id, company_id=company.id, rank=1, status="Recommended")
    deps.db.add(lead)
    deps.db.commit()

    flow_one = _rec_flow(target_industries=["制造业"], target_regions=["上海"])
    _set_flow(deps, flow_one)
    _ensure_recommendation_profile_usage_for_campaign(deps, flow_one, old_campaign.id, "lead")

    # The second run changes filters to healthcare; the profile is overwritten in place
    new_campaign = _campaign(deps, seller, industries=["医疗健康"], regions=["北京"])
    flow_two = _rec_flow(target_industries=["医疗健康"], target_regions=["北京"])
    _set_flow(deps, flow_two)
    _ensure_recommendation_profile_usage_for_campaign(deps, flow_two, new_campaign.id, "lead")

    context = get_outreach_context(deps.db, USER, lead.id, channel="email")

    assert json.loads(old_campaign.target_industries_json) == ["制造业"]
    assert context["profile_context"]["industry"] == "制造业"
    assert context["campaign"]["target_industries"] == ["制造业"]
    assert context["campaign"]["target_regions"] == ["上海"]


def test_auto_path_ignores_a_stale_profile_key_pinned_in_the_flow(deps):
    """Restoring a historical session pins the previous run's key into flow; if filters changed, the new filters win."""
    from agents.sales_agent.api import _ensure_recommendation_profile_usage_for_campaign

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    campaign = _campaign(deps, seller, industries=["制造业"], regions=["上海"])
    stale = _upsert(deps, seller, _rec_flow(target_industries=["医疗健康"], target_regions=["北京"]))

    flow = _rec_flow(
        target_industries=["制造业"],
        target_regions=["上海"],
        recommendation_profile_key=stale.profile_key,
        recommendation_profile_version_id=stale.id,
    )
    _set_flow(deps, flow)
    _ensure_recommendation_profile_usage_for_campaign(deps, flow, campaign.id, "lead")

    rows = _versions(deps)
    assert len(rows) == 1
    assert json.loads(rows[0].target_industries_json) == ["制造业"]
    usage = deps.db.query(SalesAgentRecommendationProfileUsageORM).filter_by(campaign_id=campaign.id).one()
    assert usage.profile_version_id == rows[0].id


def test_auto_path_does_not_overwrite_a_user_renamed_profile(deps):
    """After the user renames it, the automatic path only updates filters, not the name."""
    from agents.sales_agent.service.recommendation_profiles import rename_recommendation_profile

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    created = _upsert(deps, seller, _rec_flow())
    rename_recommendation_profile(deps.db, USER, created.profile_key, "我自己起的名字")

    _upsert(deps, seller, _rec_flow(target_industries=["医疗健康"]))

    rows = _versions(deps)
    assert len(rows) == 1
    assert rows[0].name == "我自己起的名字"
    assert json.loads(rows[0].target_industries_json) == ["医疗健康"]


def test_default_profile_name_follows_the_brand_not_the_conditions(deps):
    """The name must not contain filters -- filters get overwritten, and a name left in place would no longer match."""
    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")

    row = _upsert(deps, seller, _rec_flow(target_industries=["制造业"]))

    assert row.name == f"{seller.name}的推荐条件"
    assert "制造业" not in row.name


def test_archive_hides_the_profile_without_adding_a_row(deps):
    """Archiving sets a flag in place; no more appending archived versions."""
    from agents.sales_agent.service.recommendation_profiles import (
        archive_recommendation_profile,
        list_active_recommendation_profiles,
    )

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    created = _upsert(deps, seller, _rec_flow())

    archive_recommendation_profile(deps.db, USER, created.profile_key)

    rows = _versions(deps)
    assert len(rows) == 1
    assert rows[0].event_type == "archived"
    assert list_active_recommendation_profiles(deps.db, USER, seller.id) == []


def test_archived_profile_is_not_revived_by_the_next_recommendation(deps):
    """Recommending after archiving creates a new one; the archived row must not be picked back up for reuse."""
    from agents.sales_agent.service.recommendation_profiles import archive_recommendation_profile

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    created = _upsert(deps, seller, _rec_flow())
    archive_recommendation_profile(deps.db, USER, created.profile_key)

    revived = _upsert(deps, seller, _rec_flow(target_industries=["医疗健康"]))

    assert revived.id != created.id
    assert revived.event_type == "created"
    rows = _versions(deps)
    assert len(rows) == 2


def test_condition_cards_echo_the_recommendation_profile_over_the_latest_campaign(deps):
    """The three filter cards echo the recommendation profile first -- it's closer to the user's current intent than any single campaign."""
    from agents.sales_agent.api import (
        _latest_target_company_size_for_echo,
        _latest_target_industries_for_echo,
        _latest_target_regions_for_echo,
    )

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    _campaign(deps, seller, industries=["制造业"], regions=["上海"], size="1-10 名员工")
    _upsert(deps, seller, _rec_flow(target_industries=["医疗健康"], target_regions=["北京"], target_company_size="101-500 名员工"))
    _set_flow(deps, {"step": "target_industries"})

    assert _latest_target_industries_for_echo(deps) == ["医疗健康"]
    assert _latest_target_regions_for_echo(deps) == ["北京"]
    assert _latest_target_company_size_for_echo(deps) == "101-500 名员工"


def test_condition_cards_fall_back_to_the_campaign_without_a_profile(deps):
    """Without a recommendation profile, the campaign / ICP fallback must be preserved as-is."""
    from agents.sales_agent.api import (
        _latest_target_company_size_for_echo,
        _latest_target_industries_for_echo,
        _latest_target_regions_for_echo,
    )

    seller = _seller_profile(deps, service_capabilities=["品牌原能力"], company_size="11-100 名员工")
    _campaign(deps, seller, industries=["制造业"], regions=["上海"], size="1-10 名员工")
    _set_flow(deps, {"step": "target_industries"})

    assert _latest_target_industries_for_echo(deps) == ["制造业"]
    assert _latest_target_regions_for_echo(deps) == ["上海"]
    assert _latest_target_company_size_for_echo(deps) == "1-10 名员工"
