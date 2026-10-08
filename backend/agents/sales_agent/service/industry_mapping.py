"""Sales Agent target industry understanding: map the user's wording to real industry tags in the company DB."""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from agents.sales_agent.model import get_sales_agent_model, get_sales_agent_model_settings, resolve_sales_agent_model_config
from agents.sales_agent.models import SalesAgentCompanyORM
from agents.sales_agent.runtime_log import duration_ms, model_config_fields, new_model_call_id, now_monotonic, write_model_call_log
from agents.sales_agent.service.common import _clean_list, _loads
from agents.sales_agent.service.companies import _db_seed_company_query, industry_search_terms

logger: logging.Logger = logging.getLogger(__name__)

# Tests can replace this name; production lazily imports pydantic_ai so importing the service layer does not fail without the dedicated agent venv.
Agent: Any | None = None

_UNLIMITED_INDUSTRY_VALUES = {"不限行业", "不限制行业", "行业不限", "全行业", "不限"}


class IndustryMappingResult(BaseModel):
    matched_industries: list[str] = Field(default_factory=list, max_length=12)


_INDUSTRY_MAPPING_PROMPT = """你是 Sales Agent 的行业理解器。你的任务是把用户输入的目标行业,映射到企业库中已经存在的行业标签。

硬性规则:
- 只能从“企业库可选行业标签”里选择,不要创造新行业标签。
- 用户输入“不限行业/全行业/不限”时,返回空数组,表示不做行业过滤。
- 如果用户说的是职业/服务叫法,要理解其所属行业。例如“律师/律所/法务”应优先匹配法律服务、律师事务所、商务服务等库中存在标签。
- 返回 1-8 个最相关标签;不确定时宁可少选,不要把明显无关行业选进去。
- 只输出结构化结果,不要解释。
"""


def _available_industry_labels(db: Session, limit: int = 300) -> list[str]:
    rows = (
        _db_seed_company_query(db)
        .with_entities(SalesAgentCompanyORM.industry, SalesAgentCompanyORM.industry_primary, SalesAgentCompanyORM.industry_tags_json)
        .filter(SalesAgentCompanyORM.industry != "")
        .group_by(SalesAgentCompanyORM.industry, SalesAgentCompanyORM.industry_primary, SalesAgentCompanyORM.industry_tags_json)
        .order_by(func.count(SalesAgentCompanyORM.id).desc(), SalesAgentCompanyORM.industry.asc())
        .limit(limit)
        .all()
    )
    labels: list[str] = []
    seen: set[str] = set()
    for industry, primary, tags_json in rows:
        text = str(industry or "").strip()
        parts = [text, str(primary or "").strip()]
        for raw in (text, str(tags_json or "")):
            try:
                parsed = _loads(raw, [])
            except Exception:  # noqa: BLE001
                parsed = []
            if isinstance(parsed, list):
                parts.extend(str(x).strip() for x in parsed)
        for sep in ("、", ",", "，", ";", "；", "/", "|"):
            if sep in text:
                parts.extend(part.strip() for part in text.split(sep))
        for part in parts:
            value = str(part or "").strip()
            if value and value not in seen:
                seen.add(value)
                labels.append(value)
    return labels[:limit]


def _fallback_map_to_available(requested: list[str], available: list[str]) -> list[str]:
    terms = industry_search_terms(requested)
    if not terms:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for label in available:
        haystack = label.casefold()
        if any(term.casefold() in haystack or haystack in term.casefold() for term in terms):
            if label not in seen:
                seen.add(label)
                matched.append(label)
        if len(matched) >= 8:
            break
    return matched


def _mapping_prompt_text(requested: list[str], available: list[str]) -> str:
    return "\n".join([
        "用户输入的目标行业: " + "、".join(requested),
        "企业库可选行业标签:",
        *[f"- {label}" for label in available[:300]],
    ])


async def map_target_industries_to_available(db: Session, requested: list[str]) -> list[str]:
    """Map the target industry to real industry tags in the DB; "any industry" returns the original value, meaning no industry filtering later."""
    values = _clean_list(requested, 20)
    if not values:
        return []
    unlimited = [value for value in values if value in _UNLIMITED_INDUSTRY_VALUES]
    if unlimited:
        return [unlimited[0]]
    available = _available_industry_labels(db)
    if not available:
        return industry_search_terms(values) or values

    request_id = new_model_call_id()
    started_at = now_monotonic()
    model_cfg_fields: dict[str, Any] = {}
    try:
        model_cfg_fields = model_config_fields(resolve_sales_agent_model_config("chat"))
    except Exception:  # noqa: BLE001
        model_cfg_fields = {}
    write_model_call_log(
        request_id=request_id,
        event="industry_mapping",
        status="started",
        scene="lead",
        model_config=model_cfg_fields,
        extra={"requested": values, "available_count": len(available)},
    )
    try:
        global Agent
        if Agent is None:
            from pydantic_ai import Agent as PydanticAgent

            Agent = PydanticAgent

        agent = Agent(
            get_sales_agent_model("chat"),
            output_type=IndustryMappingResult,
            system_prompt=_INDUSTRY_MAPPING_PROMPT,
            tools=[],
            retries=1,
            model_settings=get_sales_agent_model_settings("chat"),
        )
        result = await agent.run(_mapping_prompt_text(values, available))
        allowed = {label.casefold(): label for label in available}
        mapped: list[str] = []
        seen: set[str] = set()
        for item in getattr(result.output, "matched_industries", []) or []:
            text = str(item or "").strip()
            label = allowed.get(text.casefold())
            if label and label not in seen:
                seen.add(label)
                mapped.append(label)
            if len(mapped) >= 8:
                break
        if not mapped:
            mapped = _fallback_map_to_available(values, available)
        write_model_call_log(
            request_id=request_id,
            event="industry_mapping",
            status="ok",
            scene="lead",
            duration=duration_ms(started_at),
            model_config=model_cfg_fields,
            usage=(result.usage() if hasattr(result, "usage") else None),
            extra={"requested": values, "mapped": mapped},
        )
        return mapped or values
    except Exception as e:  # noqa: BLE001
        logger.warning("Sales Agent industry mapping failed", exc_info=True)
        mapped = _fallback_map_to_available(values, available)
        write_model_call_log(
            request_id=request_id,
            event="industry_mapping",
            status="failed",
            scene="lead",
            duration=duration_ms(started_at),
            model_config=model_cfg_fields,
            error=e,
            extra={"requested": values, "fallback_mapped": mapped},
        )
        return mapped or values
