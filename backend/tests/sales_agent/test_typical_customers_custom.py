"""Suitable-customers card: in-card item addition + persist to DB + echo back.

Requirements in docs/08-独立模块/SalesAgent/02-需求分析/找客户推荐与邮件流程.md §4.1.1 / §4.1.2.

The two original holes: the card had only 4 hardcoded checkboxes and no add entry; and what was filled in only went into the session's
context_json, while `sales_agent_seller_profiles.typical_customers_json` stayed empty,
lost on switching sessions (the only persistence entry fired only on capability/size changes, and passed the old DB values at that).

Run:
    venv/bin/python -m pytest tests/sales_agent/test_typical_customers_custom.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.service import (
    _apply_lead_flow_selected_values_to_guidance,
    _merge_custom_options,
    build_missing_info_guidance_card,
    CUSTOM_OPTION_MAX,
)
from agents.sales_agent.constants import TARGET_INDUSTRY_OPTIONS, TARGET_REGION_OPTIONS


def _card() -> dict:
    card = build_missing_info_guidance_card(
        ["典型客户或适合服务的客户类型"], question="适合客户：你最适合服务哪类客户?",
    )
    return card["data"]["question_card"] if "data" in card else card["question_card"]


# ─────────────── Card declaration ───────────────


def test_card_allows_adding_options_in_place():
    q = _card()
    assert q["id"] == "seller_typical_customers"
    assert q["allow_custom_option"] is True
    assert q["custom_option_max"] == CUSTOM_OPTION_MAX


def test_card_still_has_the_preset_options():
    """Adding a custom entry doesn't mean removing the presets."""
    assert len(_card()["options"]) >= 4


def test_typical_customer_option_keeps_phrase_boundary():
    """Suitable customers are complete phrases: punctuation is sentence content and must not be washed into fragments or spaces."""
    card = build_missing_info_guidance_card(
        ["典型客户或适合服务的客户类型"],
        question="适合客户：你最适合服务哪类客户?",
        question_options=[
            {"label": "有官网但搜索获客效果一般的企业", "desc": "已有官网,但转化一般。"},
            {"label": "官网建设/优化需求明确的企业", "desc": "斜杠是短语内容,不能拆。"},
            {"label": "线上获客一般、想提升 AI 曝光的企业", "desc": "顿号是句子内容,不能清理。"},
        ],
    )
    q = card["data"]["question_card"] if "data" in card else card["question_card"]
    values = [o["value"] for o in q["options"]]
    assert "有官网但搜索获客效果一般的企业" in values
    assert "官网建设/优化需求明确的企业" in values
    assert "线上获客一般、想提升 AI 曝光的企业" in values


def test_other_cards_do_not_get_the_custom_entry():
    """Only enabled for suitable customers -- a card like target company size shouldn't sprout an add-item box."""
    card = build_missing_info_guidance_card(["目标客户企业规模"], question="目标企业规模")
    q = card["data"]["question_card"] if "data" in card else card["question_card"]
    assert q["type"] == "checkbox"
    assert not q.get("allow_custom_option")


# ─────────────── Echo: custom items must be added back to options ───────────────


def test_custom_values_are_merged_back_into_options():
    """Without adding to options, custom items only live in selected_values with no clickable item on the card -- showing up as "gone next time it opens"."""
    options = [{"label": "有官网但搜索获客一般的企业", "value": "有官网但搜索获客一般的企业"}]
    merged = _merge_custom_options(options, ["有官网但搜索获客一般的企业", "跨境独立站代运营"])
    assert [o["value"] for o in merged] == ["有官网但搜索获客一般的企业", "跨境独立站代运营"]
    assert merged[-1]["source"] == "custom"


def test_merge_is_case_insensitive_and_keeps_order():
    merged = _merge_custom_options([{"label": "GEO", "value": "GEO"}], ["geo", "医疗内容合规"])
    assert [o["value"] for o in merged] == ["GEO", "医疗内容合规"]


def test_merge_tolerates_missing_options():
    assert [o["value"] for o in _merge_custom_options(None, ["自建渠道"])] == ["自建渠道"]


def test_guidance_echo_puts_custom_values_on_the_card():
    data = {"question_card": _card()}
    flow = {"typical_customers": ["跨境独立站代运营", "医疗行业内容合规"]}
    updated, changed = _apply_lead_flow_selected_values_to_guidance(data, flow)
    assert changed
    q = updated["question_card"]
    values = [o["value"] for o in q["options"]]
    for custom in flow["typical_customers"]:
        assert custom in values, "自定义项没补进 options,卡上会看不见"
        assert custom in q["selected_values"], "自定义项没保持选中"


def test_no_limit_options_are_first_on_target_cards():
    industry_card = build_missing_info_guidance_card(["本次想找的目标客户行业"], question="目标行业")["data"]["question_card"]
    region_card = build_missing_info_guidance_card(["本次想找的目标地区"], question="目标地区")["data"]["question_card"]
    assert TARGET_INDUSTRY_OPTIONS[0] == "不限行业"
    assert TARGET_REGION_OPTIONS[0] == "不限地区"
    assert industry_card["options"][0]["value"] == "不限行业"
    assert region_card["options"][0]["value"] == "不限地区"
    assert all(option["value"] != "全国" for option in region_card["options"])


def test_target_cards_echo_flow_values():
    """Target size/industry/region cards must have selected_values on first SSE render too, not rely on session replay to fill them."""
    flow = {
        "target_company_size": "11-100 名员工、101-500 名员工",
        "target_industries": ["工业制造", "企业服务"],
        "target_regions": ["北京", "上海"],
        "requested_limit": 20,
    }
    cases = [
        ("目标客户企业规模", "目标企业规模", "target_company_size", ["11-100 名员工", "101-500 名员工"], []),
        ("本次想找的目标客户行业", "目标行业", "target_industries", ["工业制造", "企业服务"], []),
        ("本次想找的目标地区", "目标地区", "target_regions", ["北京", "上海"], ["20家"]),
    ]
    for missing, question, field, expected, secondary in cases:
        card = build_missing_info_guidance_card([missing], question=question)
        updated, changed = _apply_lead_flow_selected_values_to_guidance(card["data"], flow)
        assert changed
        q = updated["question_card"]
        assert q["field"] == field
        assert q["selected_values"] == expected
        if secondary:
            assert q["secondary_selected_values"] == secondary
