"""AI candidate suggestions for Sales Agent when the profile is empty.

Only generates card candidates; does not write the profile or option_catalog. Existing brand profiles are intercepted by api.py first and never reach here.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from agents.sales_agent.model import get_sales_agent_model, get_sales_agent_model_settings, resolve_sales_agent_model_config
from agents.sales_agent.runtime_log import duration_ms, model_config_fields, new_model_call_id, now_monotonic, write_model_call_log
from agents.sales_agent.service.common import _clean_list, _clean_profile_option_text

logger: logging.Logger = logging.getLogger(__name__)


class ProfileSuggestionItem(BaseModel):
    label: str = Field(default="", max_length=60)
    desc: str = Field(default="", max_length=120)


class ProfileSuggestions(BaseModel):
    service_capabilities: list[ProfileSuggestionItem] = Field(default_factory=list, max_length=5)
    service_scope: list[ProfileSuggestionItem] = Field(default_factory=list, max_length=4)
    typical_customers: list[ProfileSuggestionItem] = Field(default_factory=list, max_length=5)


_BANNED_TERMS: tuple[str, ...] = ("律师事务所", "律所")
_DESC_MARKER_RE = re.compile(r"[;；,，\s]*(?:desc|description|描述|说明)\s*[:：]?\s*", re.IGNORECASE)
_LABEL_PREFIX_RE = re.compile(r"^\s*(?:[\w一-鿿]{0,16}?(?:客户|能力|方向|服务|类型|候选|选项)\s*)?[0-9一二三四五六七八九十]+\s*[\.、:：-]?\s*")

_PROFILE_SUGGESTION_PROMPT: str = """你是 Sales Agent 的画像候选生成器。你只根据用户提供的业务方向生成可勾选候选项。

硬性规则:
- 只输出结构化结果,不要写正文说明。
- 不生成具体公司名单。
- 不编造已有客户案例、资质、团队人数、效果数据。
- 不把目标客户行业误当成我方服务能力。
- 尊重用户原词;如果用户写的是“法律服务”,不要改写成“律师事务所”“律所”等说法。
- 每个 label 都必须是一条独立选项;多条候选要放成 JSON 数组里的多项,不要塞进同一个 label。
- label 可以包含正常业务标点,但只能放用户能直接勾选的文案;不要把字段名 desc/description 写进 label。
- typical_customers.label 只能写客户类型/企业类型,不要追加产品介绍、品牌介绍或“X 是一款/一个/一种...”这类业务方向原句。
- desc 只解释适用场景,不要承诺效果;前端卡片不展示 desc,也不要重复产品/品牌自述。

输出:
- service_capabilities: 3-5 个“我方能提供什么”的服务能力点,15 个汉字以内,推荐格式如“房产家居获客能力”“客户分层触达能力”“金融产品推广能力”。只表达能力本身,不要追加产品/品牌自述。
- service_scope: 2-4 个服务/产品方向,15 个汉字以内,推荐格式如“聊天机器人线索跟进”“房产家居获客咨询”“金融保险咨询对接”。只表达本次要推广的服务/产品方向,不要追加适合客户或产品/品牌自述。
- typical_customers: 3-5 个适合服务的客户类型,可以写成较完整短句,推荐格式如“房地产开发商、楼盘项目或房产经纪公司”“家装、家居建材类企业或门店”“财税服务公司、代账机构或独立财税从业者”。只表达客户类型,不要追加产品介绍、服务说明或品牌自述。
"""


def profile_suggestion_context_text(context: dict[str, Any]) -> str:
    def list_text(key: str) -> str:
        return "、".join(_clean_list(context.get(key), 10))

    lines = [
        f"业务方向/服务产品: {list_text('business_hint') or '未提供'}",
        f"品牌名: {str(context.get('brand_name') or '').strip() or '未提供'}",
        f"已确认能力: {list_text('service_capabilities') or '未提供'}",
        f"已知服务范围: {list_text('service_scope') or '未提供'}",
        f"本次目标行业: {list_text('target_industries') or '未提供'}",
        f"本次目标地区: {list_text('target_regions') or '未提供'}",
        f"目标企业规模: {str(context.get('target_company_size') or '').strip() or '未提供'}",
    ]
    return "\n".join(lines)


def _clean_suggestion_item(label: str, desc: str) -> tuple[str, str]:
    label = str(label or "").strip(" \t\r\n-—:：;；")
    desc = str(desc or "").strip(" \t\r\n-—:：;；")
    parts = _DESC_MARKER_RE.split(label, maxsplit=1)
    if len(parts) == 2:
        label = parts[0].strip(" \t\r\n-—:：;；")
        desc = desc or parts[1].strip(" \t\r\n-—:：;；")
    label = _LABEL_PREFIX_RE.sub("", label).strip(" \t\r\n-—:：;；")
    if not desc:
        for sep in (" - ", " — ", "——", "--"):
            if sep in label:
                left, right = label.split(sep, 1)
                if left.strip() and right.strip():
                    label, desc = left.strip(), right.strip()
                    break
    return label, desc


def normalize_profile_suggestions(output: ProfileSuggestions | Any) -> dict[str, list[dict[str, str]]]:
    def option_items(
        items: list[ProfileSuggestionItem] | None,
        limit: int,
        *,
        strip_product_desc: bool = False,
    ) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in items or []:
            label, desc = _clean_suggestion_item(
                str(getattr(item, "label", "") or ""),
                str(getattr(item, "desc", "") or ""),
            )
            if strip_product_desc:
                # The model occasionally appends the user's business direction sentence verbatim, e.g.
                # "... Xiaoming is an intelligent chatbot."; product/brand self-descriptions are cut here.
                label = _clean_profile_option_text(label)
                # desc is a complete sentence and the period is part of it. Stripping punctuation only applies to label (a phrase);
                # for desc it would strip "suitable for customers needing standardized bookkeeping." into an unpunctuated fragment.
                cleaned_desc = _clean_profile_option_text(desc)
                tail = desc.rstrip()[-1:] if desc.rstrip() else ""
                if cleaned_desc and tail in ("。", ".") and not cleaned_desc.endswith(("。", ".")):
                    cleaned_desc += tail
                desc = cleaned_desc
            # No length truncation of label here: length is constrained by the prompt; if the model gives more, show the whole entry.
            # Hard cuts like "...precise outreach capability, and" get ticked into profiles by users and flow all the way into email bodies.
            if not label or any(term in label or term in desc for term in _BANNED_TERMS):
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            option = {"label": label[:60], "value": label[:60], "source": "ai"}
            if desc:
                option["desc"] = desc[:120]
            options.append(option)
            if len(options) >= limit:
                break
        return options

    return {
        "service_capabilities": option_items(
            getattr(output, "service_capabilities", None), 5, strip_product_desc=True,
        ),
        "service_scope": option_items(
            getattr(output, "service_scope", None), 4, strip_product_desc=True,
        ),
        "typical_customers": option_items(getattr(output, "typical_customers", None), 5, strip_product_desc=True),
    }


def _empty_suggestions() -> dict[str, list[dict[str, str]]]:
    return {"service_capabilities": [], "service_scope": [], "typical_customers": []}


def _suggestion_counts(suggestions: dict[str, list[dict[str, str]]]) -> dict[str, int]:
    return {
        "service_capabilities_count": len(suggestions.get("service_capabilities") or []),
        "service_scope_count": len(suggestions.get("service_scope") or []),
        "typical_customers_count": len(suggestions.get("typical_customers") or []),
    }


async def suggest_profile_options(context: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Generate profile candidates from the business direction given by the user; return empty candidates on failure."""
    has_context = bool(
        _clean_list(context.get("business_hint"), 5)
        or _clean_list(context.get("service_capabilities"), 5)
        or _clean_list(context.get("service_scope"), 5)
    )
    if not has_context:
        return _empty_suggestions()
    request_id = new_model_call_id()
    started_at = now_monotonic()
    model_cfg_fields: dict[str, Any] = {}
    try:
        model_cfg_fields = model_config_fields(resolve_sales_agent_model_config("chat"))
    except Exception:  # noqa: BLE001
        model_cfg_fields = {}
    write_model_call_log(
        request_id=request_id,
        event="profile_suggestions",
        status="started",
        scene="profile_suggestions",
        model_config=model_cfg_fields,
        extra={
            "business_hint_count": len(_clean_list(context.get("business_hint"), 5)),
            "service_capabilities_context_count": len(_clean_list(context.get("service_capabilities"), 5)),
            "service_scope_context_count": len(_clean_list(context.get("service_scope"), 5)),
        },
    )
    try:
        from pydantic_ai import Agent

        agent = Agent(
            get_sales_agent_model("chat"),
            output_type=ProfileSuggestions,
            system_prompt=_PROFILE_SUGGESTION_PROMPT,
            tools=[],
            retries=1,
            model_settings=get_sales_agent_model_settings("chat"),
        )
        result = await agent.run(profile_suggestion_context_text(context))
        suggestions = normalize_profile_suggestions(result.output)
        write_model_call_log(
            request_id=request_id,
            event="profile_suggestions",
            status="ok",
            scene="profile_suggestions",
            duration=duration_ms(started_at),
            model_config=model_cfg_fields,
            usage=(result.usage() if hasattr(result, "usage") else None),
            extra=_suggestion_counts(suggestions),
        )
        return suggestions
    except Exception as e:  # noqa: BLE001
        logger.warning("Sales Agent profile suggestion generation failed", exc_info=True)
        write_model_call_log(
            request_id=request_id,
            event="profile_suggestions",
            status="failed",
            scene="profile_suggestions",
            duration=duration_ms(started_at),
            model_config=model_cfg_fields,
            error=e,
        )
        return _empty_suggestions()
