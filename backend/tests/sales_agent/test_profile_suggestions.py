from __future__ import annotations

from agents.sales_agent.service.profile_suggestions import (
    ProfileSuggestionItem,
    ProfileSuggestions,
    normalize_profile_suggestions,
)


def test_normalize_profile_suggestions_splits_desc_marker_from_label():
    output = ProfileSuggestions(
        typical_customers=[
            ProfileSuggestionItem(label="财务服务客户1：中小企业；desc:需要日常记账、报税与财务支持的企业"),
        ],
    )

    suggestions = normalize_profile_suggestions(output)

    assert suggestions["typical_customers"] == [
        {
            "label": "中小企业",
            "value": "中小企业",
            "source": "ai",
            "desc": "需要日常记账、报税与财务支持的企业",
        }
    ]


def test_normalize_profile_suggestions_strips_product_self_description_from_typical_customers():
    output = ProfileSuggestions(
        typical_customers=[
            ProfileSuggestionItem(label="房地产开发商、楼盘项目或房产经纪公司，有线上获客与线索跟进需求。小明是一款智能聊天机器人。"),
            ProfileSuggestionItem(label="家装、家居建材类企业或门店，小明是一个智能获客助手"),
        ],
    )

    suggestions = normalize_profile_suggestions(output)

    assert suggestions["typical_customers"] == [
        {"label": "房地产开发商、楼盘项目或房产经纪公司，有线上获客与线索跟进需求", "value": "房地产开发商、楼盘项目或房产经纪公司，有线上获客与线索跟进需求", "source": "ai"},
        {"label": "家装、家居建材类企业或门店", "value": "家装、家居建材类企业或门店", "source": "ai"},
    ]


def test_normalize_profile_suggestions_strips_product_desc_without_cutting_the_option():
    """Only strip the trailing product self-description; candidates are shown as-is however long.

    This **must not** truncate by character count: a cut-off 「…精准触达能力,可」 is a half sentence the user would
    tick into the profile, flowing all the way into the profile card and outreach email body. Length is constrained by the prompt, not by chopping afterwards.
    """
    output = ProfileSuggestions(
        service_capabilities=[
            ProfileSuggestionItem(label="房地产家居行业营销获客服务能力，覆盖楼盘、中介、家装等场景。小明是一款智能聊天机器人。"),
        ],
        service_scope=[
            ProfileSuggestionItem(label="金融保险咨询与意向筛选，支持贷款、理财、保险等方向。小明是一款智能聊天机器人。"),
        ],
    )

    suggestions = normalize_profile_suggestions(output)

    capability = "房地产家居行业营销获客服务能力，覆盖楼盘、中介、家装等场景"
    scope = "金融保险咨询与意向筛选，支持贷款、理财、保险等方向"
    assert suggestions["service_capabilities"] == [
        {"label": capability, "value": capability, "source": "ai"},
    ]
    assert suggestions["service_scope"] == [
        {"label": scope, "value": scope, "source": "ai"},
    ]


def test_normalize_profile_suggestions_keeps_clean_structured_fields():
    output = ProfileSuggestions(
        service_capabilities=[
            ProfileSuggestionItem(label="日常记账支持", desc="适合需要规范账务处理的客户。"),
        ],
    )

    suggestions = normalize_profile_suggestions(output)

    assert suggestions["service_capabilities"] == [
        {
            "label": "日常记账支持",
            "value": "日常记账支持",
            "source": "ai",
            "desc": "适合需要规范账务处理的客户。",
        }
    ]
