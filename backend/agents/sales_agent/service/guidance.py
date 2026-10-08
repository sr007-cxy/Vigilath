"""Sales Agent service layer: guidance cards: missing-info question cards, option echo, entry guidance."""
from __future__ import annotations

import re
from typing import Any
from agents.sales_agent.a2ui import build_card_a2ui
from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS, TARGET_INDUSTRY_OPTIONS, TARGET_REGION_OPTIONS
from agents.sales_agent.models import (
    SalesAgentAccountICPORM,
    SalesAgentCustomerProfileORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSellerProfileORM,
)

from agents.sales_agent.service.catalog import (
    CAPABILITY,
    COMPANY_SIZE,
    INDUSTRY,
    REGION,
    TYPICAL_CUSTOMER,
)
from agents.sales_agent.service.common import COMPANY_SIZE_BUCKET_LABELS, _clamp_limit, _clean_list, _loads
from agents.sales_agent.service.companies import _usable_profile_industries
from agents.sales_agent.service.profiles import (
    BRAND_LIMIT,
    BRAND_NO_PROFILE_HEADLINE,
    _legacy_account_icp_payload,
    _legacy_seller_profile_payload,
    serialize_account_icp,
    serialize_profile,
    serialize_recommendation_campaign,
    serialize_seller_profile,
)


def _single_guidance_question(questions: list[str]) -> str:
    """Take only one question from the candidate follow-ups; when info is missing, Sales Agent asks only one per turn."""
    for raw in questions:
        text = str(raw or "").strip()
        if not text:
            continue
        lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            continue
        question = next((line for line in lines if "?" in line or "？" in line), lines[0]).strip()
        for prefix in ("1. ", "1.", "1、", "1) ", "① ", "- ", "• "):
            if question.startswith(prefix):
                question = question[len(prefix):].strip()
                break
        return question
    return ""


def _numbered_question_text(prefix: str, questions: list[str]) -> str:
    # Historically this returned 1/2/3 questions; now pre-recommendation follow-ups must ask only one per turn.
    question = _single_guidance_question(questions)
    return f"{prefix}\n{question}" if question else prefix


def _recommendation_requirements_message(
    missing_industry: bool = True,
    missing_region: bool = True,
    include_limit: bool = True,
) -> str:
    questions: list[str] = []
    if missing_industry:
        questions.append("目标行业：这次想找哪个行业的客户?")
    if missing_region:
        questions.append("目标地区：这次想找哪个地区的客户?")
    return _numbered_question_text("可以。请先确认这次推荐范围:", questions)


def _short_option_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    # Some models stuff the field name desc into label/value as trailing text; cards do not show this debug residue.
    text = re.sub(r"\s+desc\s*$", "", text, flags=re.IGNORECASE).strip()
    return f"{text[:max_len]}…" if len(text) > max_len else text


def _normalize_question_options(values: list[Any] | None, max_items: int = 8, source: str = "rule") -> list[dict[str, Any]]:
    seen: set[str] = set()
    options: list[dict[str, str]] = []
    for item in values or []:
        record = item if isinstance(item, dict) else {}
        label = str(record.get("label") or record.get("value") or ("" if isinstance(item, dict) else item) or "").strip()
        value = str(record.get("value") or label).strip()
        desc = str(record.get("desc") or record.get("description") or "").strip()
        if not label or not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        option: dict[str, Any] = {
            "label": _short_option_text(label, 90 if source == "typical_customer" else 42),
            "value": _short_option_text(value, 120 if source == "typical_customer" else 90),
        }
        if desc:
            option["desc"] = _short_option_text(desc, 110)
        for key in ("id", "kind", "seller_profile_id"):
            if record.get(key) is not None:
                option[key] = record.get(key)
        if record.get("editable") is not None:
            option["editable"] = bool(record.get("editable"))
        # Items from the catalog carry their own source (system / custom); keep it, the frontend uses it to tell
        # user-added items from presets (_merge_custom_options also writes "custom").
        item_source = str(record.get("source") or "").strip()
        if item_source:
            option["source"] = item_source
        elif source:
            option["source"] = source
        options.append(option)
        if len(options) >= max_items:
            break
    return options


# Max number of in-card custom items, matching the persistence limit of _clean_list(..., 20)
CUSTOM_OPTION_MAX = 20


def _question_options(values: list[Any], max_items: int = 8) -> list[dict[str, str]]:
    return _normalize_question_options(values, max_items=max_items, source="rule")


def _merge_custom_options(options: Any, selected: list[str]) -> list[dict[str, str]]:
    """Add selected values not present in options as options: those are items the user added in the card.

    Without this, on echo they only appear in selected_values with no clickable item on the card,
    showing up as "confirmed custom items disappear the next time the card is opened".
    """
    merged = [dict(o) for o in (options or []) if isinstance(o, dict)]
    known = {str(o.get("value") or "").casefold() for o in merged}
    for value in selected:
        text = str(value or "").strip()
        if not text or text.casefold() in known:
            continue
        known.add(text.casefold())
        merged.append({
            "label": _short_option_text(text, 42),
            "value": _short_option_text(text, 90),
            "source": "custom",
        })
        if len(merged) >= CUSTOM_OPTION_MAX + 8:
            break
    return merged


CatalogOptions = dict[str, list[dict[str, Any]]]

# The literal 「新增品牌」 ("add brand"). The brand selection card does **not** have this item, and since 2026-08-27 the manual brand creation
# entry has been removed entirely: brands are synced from brand profiles uploaded by customers. The constant remains because users may still
# type it; then it must be recognized and treated as "no brand selected" and asked again, not matched as a brand name.
BRAND_NEW_TOKEN = "新增品牌"

# Max number of items shown on the capability card. 7 system presets + some items accumulated by the brand,
# with headroom up to CUSTOM_OPTION_MAX, otherwise a user filling all 20 would push out the presets.
CAPABILITY_OPTION_MAX = 7 + CUSTOM_OPTION_MAX


def _pin_option_first(options: list[dict[str, str]], labels: set[str]) -> list[dict[str, str]]:
    if not labels:
        return options
    pinned: list[dict[str, str]] = []
    rest: list[dict[str, str]] = []
    for option in options:
        value = str(option.get("value") or option.get("label") or "").strip()
        label = str(option.get("label") or option.get("value") or "").strip()
        if value in labels or label in labels:
            pinned.append(option)
        else:
            rest.append(option)
    return pinned + rest


def _catalog_question_options(
    catalog_options: CatalogOptions | None,
    kind: str,
    fallback: list[Any],
    max_items: int = 8,
) -> list[dict[str, str]]:
    """Prefer catalog options injected by the caller; otherwise fall back to the literals in code.

    guidance.py stays a pure function and does not touch the db (test_service_layering.py guards this),
    so api.py loads them with `load_card_options(db, user_id, brand_id)` and passes them in;
    same convention as SlotCatalog's "injected by caller to avoid reverse dependencies".
    """
    source = TYPICAL_CUSTOMER if kind == TYPICAL_CUSTOMER else "rule"
    entries = (catalog_options or {}).get(kind) if catalog_options is not None else None
    if entries is not None:
        return _normalize_question_options(list(entries), max_items=max_items, source=source)
    return _normalize_question_options(fallback, max_items=max_items, source=source)


def _with_action_option(options: list[dict[str, str]], token: str, input_placeholder: str = "") -> list[dict[str, str]]:
    """Always append **action entries** like 「自定义能力」「自定义行业」「不限规模」 ("custom capability", "custom industry", "any size") at the end.

    They are not capabilities/industries/sizes themselves, so they do not go into the catalog (otherwise the profile would say
    "you are a company that provides custom capability"); for the same reason they must not be cut off by max_items.

    For action items with `input_placeholder`, the frontend **opens an input box in place** in the card: one click to fill,
    submitted as this step's answer without a separate follow-up round. Items like "any size" with nothing to fill get none.
    The backend follow-up branch (the `campaign_target_industries_custom` card) is kept as-is:
    users typing "custom industry" in the chat input still go through it.
    """
    known = {str(o.get("value") or "").casefold() for o in options}
    if token.casefold() in known:
        return options
    action = _question_options([token], max_items=1)
    if input_placeholder and action:
        action[0]["input_placeholder"] = _short_option_text(input_placeholder, 60)
    return options + action


def _seller_profile_card_fields(
    catalog_options: CatalogOptions | None,
    company_size_default: str = "",
) -> dict[str, Any]:
    """Our-profile card: shared by the two gaps "professional capability" and "company/team size or professional capability".

    Before 2026-08-26 this was copied twice in `_guidance_question_card`; fixing one and missing the other made behavior diverge.

    `company_size_default` is the size stored in the DB for **this brand** (from the brand profile's team_size). If omitted, fall back to the literal;
    previously only that fallback existed, so with brand A storing 101-500 and brand B 1001+, both cards defaulted to "11-100 employees",
    and clicking continue overwrote the stored value with the fallback (2026-08-27 user: "the database is authoritative").
    """
    options = _catalog_question_options(
        catalog_options,
        CAPABILITY,
        ["AI-GEO优化", "SEO技术团队", "官网建设/优化", "内容营销能力",
         "行业解决方案经验", "数据分析能力", "销售/交付团队"],
        max_items=CAPABILITY_OPTION_MAX,
    )
    return {
        "id": "seller_profile",
        "field": "seller_capabilities",
        "title": "画像",
        "helper_text": "确认我们自己的专业能力和企业员工规模;也可以直接添加新的专业能力,确认后会写入画像和下次可选项。自己添加的能力可删除并同步到画像。",
        "type": "checkbox",
        "layout": "vertical",
        "allow_custom_option": True,
        "custom_option_placeholder": "输入新的专业能力,例如「跨境独立站代运营」",
        "custom_option_max": CUSTOM_OPTION_MAX,
        "options": options,
        "secondary_title": "企业员工规模",
        "secondary_helper_text": "和专业能力合并进同一段我方画像。",
        "secondary_field": "company_size",
        "secondary_default_value": str(company_size_default or "").strip() or "11-100 名员工",
        "secondary_options": _catalog_question_options(
            catalog_options, COMPANY_SIZE, list(COMPANY_SIZE_BUCKET_LABELS),
        ),
    }


def _guidance_question_card(
    missing_item: str,
    question: str,
    question_options: list[Any] | None = None,
    catalog_options: CatalogOptions | None = None,
    company_size_default: str = "",
) -> dict[str, Any]:
    generated_options = _normalize_question_options(question_options, max_items=5, source="ai")

    def options_or(default_values: list[Any]) -> list[dict[str, str]]:
        return generated_options if len(generated_options) >= 3 else _question_options(default_values)

    def catalog_or(kind: str, default_values: list[Any], max_items: int = 8) -> list[dict[str, str]]:
        """AI-generated options first, then the catalog, finally code literals."""
        if len(generated_options) >= 3:
            return generated_options
        options = _catalog_question_options(catalog_options, kind, default_values, max_items=max_items)
        if kind == INDUSTRY:
            return _pin_option_first(options, {"不限行业"})
        if kind == REGION:
            return _pin_option_first(options, {"不限地区"})
        return options

    def generated_selected_values() -> list[str]:
        """AI-recommended options may be pre-ticked, but action entries like "custom industry" must not be."""
        return _clean_list([
            option.get("value") or option.get("label") for option in generated_options
            if str(option.get("value") or option.get("label") or "").strip() not in {"自定义行业", "其它行业", "其他行业"}
        ], 5)

    def catalog_with_generated(kind: str, default_values: list[Any], max_items: int = 8) -> list[dict[str, str]]:
        """Keep AI suggestions, but do not let them override the whole catalog/default options."""
        catalog = _catalog_question_options(catalog_options, kind, default_values, max_items=max_items)
        merged: list[dict[str, str]] = []
        seen: set[str] = set()
        for option in [*generated_options, *catalog]:
            value = str(option.get("value") or option.get("label") or "").strip()
            label = str(option.get("label") or option.get("value") or "").strip()
            if kind == INDUSTRY and {value, label} & {"自定义行业", "其它行业", "其他行业"}:
                continue
            if kind == REGION and value == "全国":
                # 「全国」 ("nationwide") used to come from the constant/system catalog fallback, but it is too broad on the region card and users explicitly asked to stop showing it.
                # Nationwide values stored in historical tasks are still handled by the recommendation layer as "any region"; here it is only removed from new card options.
                continue
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(option)
            if len(merged) >= max_items:
                break
        if kind == INDUSTRY:
            merged = _pin_option_first(merged, {"不限行业"})
        if kind == REGION:
            merged = _pin_option_first(merged, {"不限地区"})
        return merged

    def typical_customer_options(default_values: list[Any], max_items: int) -> list[dict[str, str]]:
        # "Target customers" are often full long phrases; they must not go through the generic 42-char label truncation, or the frontend looks fragmented.
        if len(generated_options) >= 3:
            return _normalize_question_options(question_options, max_items=max_items, source=TYPICAL_CUSTOMER)
        return _catalog_question_options(catalog_options, TYPICAL_CUSTOMER, default_values, max_items=max_items)

    base: dict[str, Any] = {
        "id": "sales_agent_guidance",
        "field": "message",
        "title": question,
        "helper_text": "你也可以直接在输入框里补充,我会继续按一个问题一个问题推进。",
        "type": "text_suggestions",
        "options": [],
        "allow_text_fallback": True,
    }
    if missing_item == "业务方向或服务产品":
        base.update({
            "id": "seller_business_hint",
            "field": "business_hint",
            "title": "业务方向",
            "helper_text": "先确认你们主要提供什么服务或产品;这只用于生成候选画像,不会直接写入长期品牌画像。也可以直接添加自己的业务方向。",
            "type": "checkbox",
            "layout": "vertical",
            "allow_custom_option": True,
            "custom_option_placeholder": "输入你们的服务/产品,例如「财税咨询」「法律服务」「跨境电商代运营」",
            "custom_option_max": CUSTOM_OPTION_MAX,
            "options": [
                option for option in _catalog_question_options(catalog_options, INDUSTRY, list(TARGET_INDUSTRY_OPTIONS), max_items=20)
                if str(option.get("value") or option.get("label") or "").strip() not in {"不限行业", "不限制行业", "行业不限"}
            ],
        })
    elif missing_item in {"专业能力", "我方画像"}:
        base.update(_seller_profile_card_fields(catalog_options, company_size_default))
    elif missing_item == "你准备推广/销售的服务范围":
        base.update({
            "id": "seller_service_scope",
            "field": "service_scope",
            "helper_text": "选择一个常见方向,或直接输入你的具体服务/产品。",
            "type": "checkbox",
            "layout": "vertical",
            "allow_custom_option": True,
            "custom_option_placeholder": "输入这次要推广的服务/产品,例如「AI 可见性检测」「法律顾问服务」",
            "custom_option_max": CUSTOM_OPTION_MAX,
            "options": options_or([
                {"label": "GEO / AI Visibility 优化", "desc": "提升品牌在 AI 搜索、问答和引用场景里的可见度。"},
                {"label": "SEO / 官网获客", "desc": "围绕搜索排名、内容和转化路径提升官网线索。"},
                {"label": "官网建设与转化优化", "desc": "适合官网基础薄弱或询盘转化不足的客户。"},
                {"label": "数字营销服务", "desc": "覆盖投放、内容、私域或整合营销等获客服务。"},
            ]),
        })
    elif missing_item == "公司/团队规模或专业能力":
        base.update(_seller_profile_card_fields(catalog_options, company_size_default))
    elif "目标企业规模" in missing_item or "目标客户企业规模" in missing_item or "企业规模" in question:
        base.update({
            "id": "campaign_target_company_size",
            "field": "target_company_size",
            "helper_text": "可多选这次想优先推荐的目标客户企业规模;不限制规模也可以直接输入“不限规模”。",
            "type": "checkbox",
            "layout": "vertical",
            "options": _with_action_option(
                _catalog_question_options(
                    catalog_options, COMPANY_SIZE, list(COMPANY_SIZE_BUCKET_LABELS), max_items=8,
                ),
                "不限规模",
            ),
        })
    elif missing_item == "典型客户或适合服务的客户类型":
        base.update({
            "id": "seller_typical_customers",
            "field": "typical_customers",
            "helper_text": "可多选接近的客户类型,也可以在卡片里直接添加自己的客户类型。",
            "type": "checkbox",
            "layout": "vertical",
            # In-card add: the input becomes a newly selected option, without leaving the card or adding a chat round
            # (the "custom industry" and "custom capability" ones leave the card for a follow-up round; not the same thing)
            "allow_custom_option": True,
            "custom_option_placeholder": "输入你们更适合服务的客户类型",
            "custom_option_max": CUSTOM_OPTION_MAX,
            "options": typical_customer_options([
                {"label": "有官网但搜索获客一般的企业", "desc": "已有官网,但自然搜索、询盘或咨询转化不理想。"},
                {"label": "想提升 AI 曝光的品牌方", "desc": "希望在 ChatGPT、Perplexity、AI Overviews 等场景被更多推荐。"},
                {"label": "有内容团队但转化不足的公司", "desc": "持续做内容,但排名、留资或成交线索没有明显增长。"},
                {"label": "正在做数字化获客的中小企业", "desc": "愿意投入官网、SEO、GEO 或 AI 搜索获客体系升级。"},
            ], max_items=4 + CUSTOM_OPTION_MAX),
        })
    elif "自定义能力" in question:
        base.update({
            "id": "seller_custom_capability",
            "field": "seller_capabilities",
            "helper_text": "请在输入框写清楚这项能力,例如「跨境独立站代运营」「医疗行业内容合规」。",
            "type": "text_suggestions",
            "options": [],
        })
    elif "自定义行业" in question:
        base.update({
            "id": "campaign_target_industries_custom",
            "field": "target_industries",
            "helper_text": "请在输入框输入具体目标行业,例如“口腔医疗”“康复医院”“医疗器械”。",
            "type": "text_suggestions",
            "options": [],
        })
    elif "目标行业" in missing_item or "行业" in question:
        selected = generated_selected_values()
        base.update({
            "id": "campaign_target_industries",
            "field": "target_industries",
            "helper_text": f"可多选本次想找的目标行业;也可以直接添加具体行业,推荐数量会在目标地区卡里一起确认,默认从数据库候选池推荐 {DEFAULT_RECOMMENDATIONS} 家。",
            "type": "checkbox",
            "layout": "vertical",
            "allow_custom_option": True,
            "custom_option_placeholder": "输入具体目标行业,例如「口腔正畸」「康复医院」",
            "custom_option_max": CUSTOM_OPTION_MAX,
            # Catalog industries include the brand's own custom industries; AI recommendations go first but do not override the system/custom catalog.
            "options": catalog_with_generated(INDUSTRY, list(TARGET_INDUSTRY_OPTIONS), max_items=20),
        })
        if selected:
            base["selected_values"] = selected
    elif "目标地区" in missing_item or "地区" in question:
        base.update({
            "id": "campaign_target_regions",
            "field": "target_regions",
            "helper_text": f"可多选本次想找的目标地区,也可以直接添加具体地区;下方确认推荐数量。默认从数据库候选池推荐 {DEFAULT_RECOMMENDATIONS} 家。",
            "type": "checkbox",
            "layout": "vertical",
            "allow_custom_option": True,
            "custom_option_placeholder": "输入具体目标地区,例如「江浙沪」「华北」「新加坡」",
            "custom_option_max": CUSTOM_OPTION_MAX,
            "options": catalog_with_generated(REGION, list(TARGET_REGION_OPTIONS), max_items=18),
            "secondary_title": "推荐多少家",
            "secondary_helper_text": f"默认推荐 {DEFAULT_RECOMMENDATIONS} 家。",
            "secondary_field": "requested_limit",
            "secondary_default_value": f"{DEFAULT_RECOMMENDATIONS}家",
            "secondary_options": _question_options(["10家", "20家", "30家", "50家", f"{DEFAULT_RECOMMENDATIONS}家"], max_items=5),
        })
    return base


def _split_selected_text(value: Any) -> list[str]:
    """Split echoed values on card option boundaries, but not on `/`, to avoid breaking 「官网建设/优化」 ("website building/optimization")."""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_split_selected_text(item))
        return parts
    return [part.strip() for part in re.split(r"[、,，;；\n\r]+", str(value or "")) if part.strip()]


def _selected_values_from_question_card_flow(question_card: dict[str, Any], lead_flow: dict[str, Any] | None) -> list[str]:
    if not isinstance(lead_flow, dict) or not lead_flow:
        return []
    card_id = str(question_card.get("id") or "")
    field = str(question_card.get("field") or "")
    keys: list[str] = []
    if card_id == "seller_business_hint" or field == "business_hint":
        keys = ["business_hint"]
    elif card_id == "seller_capabilities" or field == "seller_capabilities":
        keys = ["seller_capabilities"]
    elif card_id == "seller_service_scope" or field == "service_scope":
        keys = ["service_scope"]
    elif card_id == "seller_company_size" or field == "company_size":
        keys = ["seller_team", "seller_team_raw"]
    elif card_id == "seller_typical_customers" or field == "typical_customers":
        keys = ["typical_customers"]
    elif card_id == "campaign_target_company_size" or field == "target_company_size":
        keys = ["target_company_size"]
    elif card_id.startswith("campaign_target_industries") or field == "target_industries":
        keys = ["target_industries"]
    elif card_id == "campaign_target_regions" or field == "target_regions":
        keys = ["target_regions"]
    values: list[Any] = []
    for key in keys:
        raw = lead_flow.get(key)
        if key == "target_company_size":
            values.extend(_split_selected_text(raw))
        elif isinstance(raw, list):
            values.extend(raw)
        elif raw:
            values.append(raw)
        if values:
            break
    return _clean_list(values, 20)


def _secondary_selected_values_from_question_card_flow(question_card: dict[str, Any], lead_flow: dict[str, Any] | None) -> list[str]:
    if not isinstance(lead_flow, dict) or not lead_flow:
        return []
    card_id = str(question_card.get("id") or "")
    secondary_field = str(question_card.get("secondary_field") or "")
    keys: list[str] = []
    if card_id == "seller_profile" or secondary_field == "company_size":
        keys = ["seller_team", "seller_team_raw"]
    elif secondary_field == "requested_limit":
        keys = ["requested_limit"]
    values: list[Any] = []
    for key in keys:
        raw = lead_flow.get(key)
        if raw:
            values.append(f"{raw}家" if key == "requested_limit" and str(raw).isdigit() else raw)
            break
    return _clean_list(values, 5)


def _apply_lead_flow_selected_values_to_guidance(data: dict[str, Any], lead_flow: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    question_card = data.get("question_card")
    if not isinstance(question_card, dict):
        return data, False
    selected = _selected_values_from_question_card_flow(question_card, lead_flow)
    secondary_selected = _secondary_selected_values_from_question_card_flow(question_card, lead_flow)
    if not selected and not secondary_selected:
        return data, False
    if question_card.get("selected_values") == selected and question_card.get("secondary_selected_values") == secondary_selected:
        return data, False
    updated = dict(data)
    updated_card = dict(question_card)
    if selected:
        updated_card["selected_values"] = selected
        # User-added items are not in the preset options; without adding them they end up "ticked but not visible on the card"
        updated_card["options"] = _merge_custom_options(updated_card.get("options"), selected)
    if secondary_selected:
        updated_card["secondary_selected_values"] = secondary_selected
    updated["question_card"] = updated_card
    return updated, True


def _campaign_guidance_payload(
    missing_industry: bool = True,
    missing_region: bool = True,
    requested_limit: int = DEFAULT_RECOMMENDATIONS,
    seller_profile: SalesAgentSellerProfileORM | None = None,
    legacy_profile: SalesAgentCustomerProfileORM | None = None,
    account_icp: SalesAgentAccountICPORM | None = None,
    campaign: SalesAgentRecommendationCampaignORM | None = None,
    question_options: list[Any] | None = None,
    catalog_options: CatalogOptions | None = None,
) -> dict[str, Any]:
    question_map = {
        "本次想找的目标客户行业": "目标行业：这次想找哪个行业的客户?",
        "本次想找的目标地区": "目标地区：这次想找哪个地区的客户?",
    }
    missing: list[str] = []
    if missing_industry:
        missing.append("本次想找的目标客户行业")
    if missing_region:
        missing.append("本次想找的目标地区")
    candidate_questions = [question_map[item] for item in missing]
    question = _single_guidance_question(candidate_questions)
    questions = [question] if question else []
    first_missing = missing[0] if missing else "本次想找的目标客户行业"
    reply = "可以。请先确认这次推荐范围:"
    return {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": reply,
        "next_question": question,
        "questions": questions,
        "question_card": _guidance_question_card(
            first_missing, question, question_options=question_options, catalog_options=catalog_options,
        ),
        "missing_items": missing,
        "profile": serialize_profile(legacy_profile) if legacy_profile else None,
        "seller_profile": serialize_seller_profile(seller_profile) or _legacy_seller_profile_payload(legacy_profile),
        "account_icp": serialize_account_icp(account_icp) or _legacy_account_icp_payload(legacy_profile),
        "campaign": serialize_recommendation_campaign(campaign),
        "leads": [],
        "count": 0,
        "actual_count": 0,
        "requested_limit": _clamp_limit(requested_limit),
        "shortfall": _clamp_limit(requested_limit),
        "recommendation_table": [],
        "markdown_table": "",
    }


def build_guidance_card_from_result(tool: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a replayable card from a structured tool/workflow result."""
    card: dict[str, Any] = {"tool": tool, "data": data}
    a2ui = build_card_a2ui(tool, data)
    if a2ui:
        card["a2ui"] = a2ui
    return card


def _guidance_card_from_data(data: dict[str, Any]) -> dict[str, Any]:
    card: dict[str, Any] = {"tool": "recommend_companies", "data": data}
    a2ui = build_card_a2ui("recommend_companies", data)
    if a2ui:
        card["a2ui"] = a2ui
    return card


def build_entry_guidance_card() -> dict[str, Any]:
    """Build the generic entry-action card shown after a greeting."""
    question = "你想先做哪一步?"
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "我可以先帮你找客户,或先检测官网。",
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": "sales_agent_entry_action",
            "field": "entry_action",
            "title": question,
            "helper_text": "选择一个动作开始,也可以直接在输入框描述你的目标。",
            "type": "single_select",
            "options": _question_options(["找客户", "先检测官网"]),
            # This is an entry action card, not a Q&A: helper_text already says the goal can be described directly,
            # so having the frontend also add "you can also type another answer in the input box" would be redundant.
            "allow_text_fallback": False,
            "can_go_back": False,
        },
        "missing_items": ["本次想先做的销售动作"],
        "leads": [],
        "count": 0,
    }
    return _guidance_card_from_data(data)


WEBSITE_SCOPE_ONLY_OPTION = "只推有官网的企业"
WEBSITE_SCOPE_PROFILE_OPTION = "有官网但搜索获客一般的企业"


def build_website_scope_clarify_card() -> dict[str, Any]:
    """"Has a website" has two meanings; clarify before recommending.

    In the catalog, 「有官网但搜索获客一般的企业」 ("companies with a website but weak search acquisition") is a **target customer** preset (catalog.py),
    and prompts.py states that saying this counts as providing target customers, so "recommend a batch with websites"
    was judged as profile filled and went straight on. But in the recommendation chain a website is only a scoring bonus (recommend.py::+5),
    with no hard filter: users wanting to filter got a batch whose website column was all "-", without a single hint along the way
    (production 2026-09, 100 companies, none with a website). The two meanings yield different lists; we must not silently pick one.
    """
    question = "你说的「有官网」是哪个意思?"
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "先确认一下,这两种意思出来的名单不一样。",
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": "sales_agent_website_scope",
            "field": "website_scope",
            "title": question,
            "helper_text": "选一个,我按它继续推荐。",
            "type": "single_select",
            "options": _question_options([
                {"label": WEBSITE_SCOPE_ONLY_OPTION,
                 "desc": "说明:推荐不按官网硬性筛选候选,官网只作为排序加分项;我会把有官网的排前面,并告诉你这批里有几家有官网。"},
                {"label": WEBSITE_SCOPE_PROFILE_OPTION,
                 "desc": "已有官网,但自然搜索、询盘或咨询转化不理想 —— 作为适合客户画像继续推荐。"},
            ]),
            "allow_text_fallback": True,
            "can_go_back": False,
        },
        "missing_items": ["「有官网」指的是筛选条件还是客户画像"],
        "leads": [],
        "count": 0,
    }
    return _guidance_card_from_data(data)


def _brand_picker_subtitle(brand: dict[str, Any]) -> str:
    """Brand card subtitle = content summary + a "no linked profile" status marker.

    The marker goes first: users scanning this column are looking for "which one is usable", not reading three capability words first.
    Brands without a claimed profile can still be selected; their content just relies on manual sales-side input, so users must know where to fill it in.
    """
    headline = str(brand.get("headline") or "").strip()
    if brand.get("has_profile"):
        return headline
    return f"{BRAND_NO_PROFILE_HEADLINE} · {headline}" if headline else BRAND_NO_PROFILE_HEADLINE


def build_brand_picker_card(brands: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Multi-brand accounts must pick a brand as the first step of finding customers.

    Pure function: api.py loads the brand list via `list_brands(db, user_id)` and passes it in;
    same "injected by caller" convention as catalog_options; guidance.py does not touch the db.

    Uses the existing guidance card channel (`tool` is still `recommend_companies`),
    so the frontend question_card renderer displays it without changing a line; entry action cards do the same.
    """
    question = "这次要推的是哪个品牌/业务线?"
    options = _normalize_question_options(
        [
            {
                "label": str(brand.get("name") or "").strip(),
                # value uses the brand name rather than the id: the card sends back text, and `_slot_from_step` matches by name.
                # Brand names are unique among active brands (create/update both check duplicates), which is enough.
                "value": str(brand.get("name") or "").strip(),
                "desc": _brand_picker_subtitle(brand),
            }
            for brand in (brands or [])
        ],
        max_items=BRAND_LIMIT,
        source="rule",
    )
    # **No** "add brand" here. Brands are not created here: one brand profile maps to one brand,
    # synced automatically when the customer uploads a profile in the workspace. Mixed into the options, one click would
    # branch off mid find-customers flow to name something, leaving an empty business line that matches no profile.
    data = {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": "你名下有多个品牌,先确认这次要推哪一个 —— 每个品牌的资料和写信素材是分开的。",
        "next_question": question,
        "questions": [question],
        "question_card": {
            "id": "sales_agent_brand_picker",
            "field": "brand_id",
            "title": question,
            "helper_text": "每个品牌有自己的专业能力、适合客户和邮件口径,选错了推荐结果会跑偏。",
            "type": "single_select",
            "layout": "vertical",
            "options": options,
            "allow_text_fallback": True,
            "can_go_back": False,
        },
        "missing_items": ["本次要推的品牌"],
        "brands": list(brands or []),
        "leads": [],
        "count": 0,
    }
    return _guidance_card_from_data(data)


def build_missing_info_guidance_card(
    missing: list[str] | None = None,
    requested_limit: int = DEFAULT_RECOMMENDATIONS,
    question: str = "",
    question_options: list[Any] | None = None,
    catalog_options: CatalogOptions | None = None,
    company_size_default: str = "",
) -> dict[str, Any]:
    """Build a replayable Sales Agent guidance card for REST session metadata."""
    data = _profile_guidance_payload(
        missing or ["公司/团队规模或专业能力"],
        requested_limit=requested_limit,
        question_override=question,
        question_options=question_options,
        catalog_options=catalog_options,
        company_size_default=company_size_default,
    )
    return _guidance_card_from_data(data)


def _profile_guidance_payload(
    missing: list[str],
    seller_profile: SalesAgentSellerProfileORM | None = None,
    legacy_profile: SalesAgentCustomerProfileORM | None = None,
    account_icp: SalesAgentAccountICPORM | None = None,
    campaign: SalesAgentRecommendationCampaignORM | None = None,
    requested_limit: int = DEFAULT_RECOMMENDATIONS,
    question_override: str = "",
    question_options: list[Any] | None = None,
    catalog_options: CatalogOptions | None = None,
    company_size_default: str = "",
) -> dict[str, Any]:
    question_map = {
        "你准备推广/销售的服务范围": "服务/产品方向：这次你主要想推广什么服务或产品?",
        "公司/团队规模或专业能力": "我方画像",
        "典型客户或适合服务的客户类型": "适合客户：你最适合服务哪类客户?",
        "本次想找的目标客户行业": "目标行业：这次想找哪个行业的客户?",
        "本次想找的目标地区": "目标地区：这次想找哪个地区的客户?",
    }
    candidate_questions = [question_map.get(item, item) for item in missing] or list(question_map.values())
    question = _single_guidance_question([question_override]) if question_override else ""
    if not question:
        question = _single_guidance_question(candidate_questions)
    questions = [question] if question else []
    first_missing = missing[0] if missing else "你准备推广/销售的服务范围"
    reply = "这次新对话里我还不了解你的业务情况。为了推荐得更准,请先补充:"
    return {
        "status": "need_more_info",
        "needs_user_input": True,
        "message": reply,
        "next_question": question,
        "questions": questions,
        "question_card": _guidance_question_card(
            first_missing, question, question_options=question_options, catalog_options=catalog_options,
            company_size_default=company_size_default,
        ),
        "missing_items": missing,
        "profile": serialize_profile(legacy_profile) if legacy_profile else None,
        "seller_profile": serialize_seller_profile(seller_profile) or _legacy_seller_profile_payload(legacy_profile),
        "account_icp": serialize_account_icp(account_icp) or _legacy_account_icp_payload(legacy_profile),
        "campaign": serialize_recommendation_campaign(campaign),
        "leads": [],
        "count": 0,
        "actual_count": 0,
        "requested_limit": _clamp_limit(requested_limit),
        "shortfall": _clamp_limit(requested_limit),
        "recommendation_table": [],
        "markdown_table": "",
    }


def _profile_needs_refresh(
    profile: SalesAgentCustomerProfileORM | None,
    service_scope: list[str],
    industries: list[str],
    regions: list[str],
) -> bool:
    if profile is None:
        return True
    if industries and not all(x in _loads(profile.industries_json, []) for x in industries):
        return True
    if regions and not all(x in _loads(profile.regions_json, []) for x in regions):
        return True
    if service_scope and not all(x in _loads(profile.service_scope_json, []) for x in service_scope):
        return True
    if not _usable_profile_industries(profile):
        return True
    return False
