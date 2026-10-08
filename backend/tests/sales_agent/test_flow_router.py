"""Slots and routing for the find-customers flow -- pure functions: no server, no model calls, no DB.

Replaces the chain of `if flow.get("step") == ...` in `api.py`. The old structure could only advance step by step:
giving all filters in one sentence still asked each one in turn, and at step four you couldn't change the region. This pins the new behavior.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_flow_router.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.constants import TARGET_INDUSTRY_OPTIONS, TARGET_REGION_OPTIONS
from agents.sales_agent.flow import LeadState, SlotCatalog, extract_slots, next_step
from agents.sales_agent.flow.router import CUSTOM_INDUSTRY, READY, should_rerun

CATALOG = SlotCatalog(
    industries=TARGET_INDUSTRY_OPTIONS,
    regions=TARGET_REGION_OPTIONS,
    company_sizes=("1-10 名员工", "11-100 名员工", "101-500 名员工", "不限规模"),
)


def _full() -> LeadState:
    return LeadState(
        seller_capabilities=["GEO 检测"],
        service_scope=["AI 可见性检测"],
        typical_customers=["有官网的企业"],
        target_company_size="101-500 名员工",
        target_industries=["医疗健康"],
        target_regions=["上海"],
    )


# --- Routing ---

def test_empty_state_asks_capabilities_first():
    assert next_step(LeadState()) == "seller_capabilities"


def test_asks_in_slot_order():
    state = LeadState(seller_capabilities=["GEO 检测"])
    assert next_step(state) == "service_scope"
    state.service_scope = ["AI 可见性检测"]
    assert next_step(state) == "typical_customers"
    state.typical_customers = ["有官网的企业"]
    assert next_step(state) == "target_company_size"
    state.target_company_size = "101-500 名员工"
    assert next_step(state) == "target_industries"
    state.target_industries = ["医疗健康"]
    assert next_step(state) == "target_regions"
    state.target_regions = ["上海"]
    assert next_step(state) == READY


def test_prefilled_target_slots_are_skipped():
    """All target filters in one sentence -- the three cards in between should not be asked again."""
    state = LeadState(target_company_size="101-500 名员工", target_industries=["工业制造"], target_regions=["长三角"])
    assert next_step(state) == "seller_capabilities"
    state.seller_capabilities = ["GEO 检测"]
    assert next_step(state) == "service_scope"
    state.service_scope = ["AI 可见性检测"]
    state.typical_customers = ["有官网的企业"]
    assert next_step(state) == READY


def test_custom_industry_pending_routes_to_custom_card():
    state = _full()
    state.target_industries = []
    state.custom_industry_pending = True
    assert next_step(state) == CUSTOM_INDUSTRY


def test_custom_industry_pending_cleared_once_filled():
    state = _full()
    state.custom_industry_pending = True
    assert next_step(state) == READY


def test_next_step_is_pure():
    state = _full()
    before = (list(state.target_industries), state.target_company_size)
    next_step(state)
    assert (list(state.target_industries), state.target_company_size) == before


# --- Slot changes ---

def test_changing_one_slot_keeps_the_rest():
    """Wanting to change region at the end touches only region. The old step chain couldn't do this."""
    state = _full()
    changed = state.apply({"target_regions": ["华东"]})
    assert changed == ["target_regions"]
    assert state.target_regions == ["华东"]
    assert state.target_industries == ["医疗健康"] and state.target_company_size == "101-500 名员工"
    assert should_rerun(state, changed)


def test_unchanged_value_is_not_reported_as_changed():
    state = _full()
    assert state.apply({"target_regions": ["上海"]}) == []


def test_empty_updates_never_wipe_filled_slots():
    state = _full()
    assert state.apply({"target_industries": [], "target_company_size": ""}) == []
    assert state.target_industries == ["医疗健康"]


def test_rerun_only_when_conditions_complete():
    state = LeadState(target_regions=["上海"])
    assert not should_rerun(state, ["target_regions"])


def test_seller_slot_change_does_not_trigger_rerun():
    state = _full()
    assert not should_rerun(state, ["seller_capabilities"])


# --- Conversion to/from the old structure ---

def test_round_trip_with_legacy_flow_dict():
    flow = {
        "step": "target_regions",
        "seller_capabilities": ["GEO 检测"],
        "service_scope": ["AI 可见性检测"],
        "seller_team": "11-100 名员工",
        "typical_customers": ["有官网的企业"],
        "target_company_size": "101-500 名员工",
        "target_industries": ["医疗健康"],
        "requested_limit": 50,
    }
    state = LeadState.from_flow(flow)
    assert state.service_scope == ["AI 可见性检测"]
    assert state.target_industries == ["医疗健康"] and state.requested_limit == 50
    assert not state.custom_industry_pending
    state.apply({"target_regions": ["上海"]})
    out = dict(flow)
    state.to_flow(out)
    assert out["target_regions"] == ["上海"]
    assert out["seller_team"] == "11-100 名员工"


def test_from_flow_reads_custom_industry_step():
    assert LeadState.from_flow({"step": "custom_industry"}).custom_industry_pending


def test_to_flow_does_not_clear_existing_keys():
    flow = {"seller_capabilities": ["旧能力"]}
    LeadState().to_flow(flow)
    assert flow["seller_capabilities"] == ["旧能力"]


@pytest.mark.parametrize("junk", [None, "", 0, {}])
def test_from_flow_tolerates_junk_values(junk):
    state = LeadState.from_flow({"target_industries": junk, "requested_limit": junk or 0})
    assert state.target_industries == [] and state.requested_limit == 0


# --- Slot extraction ---

def test_one_sentence_fills_every_target_slot():
    found = extract_slots("帮我找 50 家北京和上海的工业制造企业,101-500 名员工", CATALOG)
    assert found["target_industries"] == ["工业制造"]
    assert found["target_regions"] == ["北京", "上海"]
    assert found["target_company_size"] == "101-500 名员工"
    assert found["requested_limit"] == 50


def test_longer_option_wins():
    """「口腔医疗」 ("dental care") must not be grabbed by 「医疗健康」 ("healthcare"), nor fill both."""
    assert extract_slots("找口腔医疗的客户", CATALOG)["target_industries"] == ["口腔医疗"]


def test_no_limit_slots_are_exclusive():
    found = extract_slots("不限行业,不限地区,再看看北京和上海", CATALOG)
    assert found["target_industries"] == ["不限行业"]
    assert found["target_regions"] == ["不限地区"]


def test_unknown_words_fill_nothing():
    assert extract_slots("随便聊聊", CATALOG) == {}


def test_card_click_text_is_recognized():
    assert extract_slots("医疗健康", CATALOG) == {"target_industries": ["医疗健康"]}


@pytest.mark.parametrize("bad", ["", None, "   "])
def test_blank_message_extracts_nothing(bad):
    assert extract_slots(bad, CATALOG) == {}


def test_limit_needs_the_unit():
    """The number in 「101-500 名员工」 ("101-500 employees") must not be taken as the recommendation count."""
    assert "requested_limit" not in extract_slots("101-500 名员工", CATALOG)


# --- "Custom capability" is not a capability ---

def test_custom_capability_tokens_are_not_capabilities():
    """「自定义能力」 ("custom capability") on the capability card is the "I'll write my own" entry. Stored in the profile, it leaves
    「您是一家提供自定义能力的公司」 ("you are a company providing custom capability") -- verbatim from production on 2026-08-23."""
    import pathlib
    api = (pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent" / "api.py").read_text(encoding="utf-8")
    assert 'CUSTOM_CAPABILITY_TOKENS = {"自定义能力"' in api
    assert "part.strip() not in CUSTOM_CAPABILITY_TOKENS" in api
    assert "_append_custom_capability_guidance(deps, \"lead\")" in api


# --- Cards only accept "an answer to the current question" ---

def _api_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent" / "api.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("text", [
    "都发过哪些企业?", "我都有找到哪些企业？", "之前推荐过哪些公司",
    "怎么收费", "能不能改一下", "有没有别的选项",
])
def test_questions_are_not_taken_as_answers(text):
    """The deterministic flow looks only at step, not content, so a question gets taken as the answer to the current card.

    2026-08-23 production: the user asked 「都发过哪些企业?」 ("which companies have I sent to?"), and the profile gained a capability named
    「都发过哪些企业?」, persisted to DB.
    """
    from agents.sales_agent.runtime.verify import verify_slot_answer
    assert verify_slot_answer("seller_capabilities", text)


@pytest.mark.parametrize("text", ["继续", "更新画像", "更新企业信息", "找客户", "先检测官网", "跳过"])
def test_action_buttons_are_not_answers(text):
    """Button text is "what to do", not an answer -- written into the profile it becomes 「更适合优先服务找客户」 ("better suited to serving find customers")."""
    from agents.sales_agent.runtime.verify import verify_slot_answer
    assert verify_slot_answer("seller_capabilities", text)


@pytest.mark.parametrize("text", ["AI-GEO优化", "11-100 名员工", "长三角", "跨境独立站代运营", "医疗健康"])
def test_real_answers_still_pass(text):
    from agents.sales_agent.runtime.verify import verify_slot_answer
    assert verify_slot_answer("seller_capabilities", text) == []


def test_no_step_means_nothing_to_verify():
    """Skip this check when no card is awaiting an answer -- otherwise normal natural-language input gets wrongly blocked."""
    from agents.sales_agent.runtime.verify import verify_slot_answer
    assert verify_slot_answer("", "都发过哪些企业?") == []


def test_lookup_and_faq_skip_the_card_flow():
    """With no card pending, faq doesn't enter the card flow; within lookup, only email count questions are caught by the deterministic guard.

    Since 2026-09-01 (38c13200) this early return has `and not step`: while a card is pending it can't return early,
    or messages of the "question card lost its step" kind get grabbed by the faq/unclear branches and the handoff breaks. With a card, let
    the card flow try first and move on if it can't answer -- `verify_slot_answer` catches "this is not this card's answer".
    """
    src = _api_src()
    assert 'if coarse_intent == "faq" and not step:\n        return False' in src
    assert 'if _is_delivery_count_query(message, intent) and _append_delivery_status_reply(deps, message, intent):' in src


def test_faq_with_a_pending_card_is_never_written_into_the_slot():
    """The other half of the above: with a card pending, faq enters the card flow but **must not** be stored as this card's answer.

    After loosening the early-return condition, only verify blocks this, so it's actually run here rather than reading source.
    """
    import tests.sales_agent.test_flow_end_to_end as e2e

    for step in ("seller_capabilities", "typical_customers", "target_industries", "target_regions"):
        gen = e2e.deps.__wrapped__()
        deps = next(gen)
        try:
            e2e._at_step(deps, step)
            e2e._run(deps, "你们这个推荐是怎么算出来的?", "faq")
            assert (deps.tool_state.get("lead_flow") or {}).get(step) is None, \
                f"step={step} 时 faq 问句被当成本卡答案写进去了"
        finally:
            gen.close()


def test_slot_write_goes_through_the_verify_layer():
    """Input-understanding validation sits in the same layer as tool-output validation (runtime/verify.py);
    failures must be logged, not silently skipped."""
    src = _api_src()
    assert "verify_slot_answer(step, message)" in src
    assert 'event="slot_answer_rejected"' in src


# --- Option text containing a slash ---

@pytest.mark.parametrize("text,expected", [
    ("官网建设/优化", ["官网建设/优化"]),
    ("销售/交付团队", ["销售/交付团队"]),
    ("AI-GEO优化、官网建设/优化、内容营销能力", ["AI-GEO优化", "官网建设/优化", "内容营销能力"]),
])
def test_slash_in_option_labels_is_not_a_separator(text, expected):
    """Card options contain text with slashes; splitting on `/` turns one into two --
    the 2026-08-23 production profile stored two entries, 「官网建设」 ("website building") + 「优化」 ("optimization")."""
    from agents.sales_agent.api import _list_from_text
    assert _list_from_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("a、b", ["a", "b"]),
    ("a,b，c", ["a", "b", "c"]),
    ("a;b；c", ["a", "b", "c"]),
])
def test_other_separators_still_split(text, expected):
    from agents.sales_agent.api import _list_from_text
    assert _list_from_text(text) == expected


def test_typical_customers_keep_sentence_punctuation_and_split_lines():
    from agents.sales_agent.api import _typical_customers_from_text

    text = "稿件是否被AI引擎引用、引了谁、带来的访客是否转化、转化金额，均缺乏可对账的第三方数据\n品牌方可申请体验全链路GEO效果计量能力"
    assert _typical_customers_from_text(text) == [
        "稿件是否被AI引擎引用、引了谁、带来的访客是否转化、转化金额，均缺乏可对账的第三方数据",
        "品牌方可申请体验全链路GEO效果计量能力",
    ]
