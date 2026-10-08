"""Customer-safe rendering for telemetry briefings.

Telemetry briefings are persisted and may outlive the service version that created
them.  Older fallback rows included provider names, environment variables and raw
failure reasons in ``body_md``.  Sanitize at the API boundary so historical rows
become safe immediately, without waiting for a data migration.
"""
from __future__ import annotations

from typing import Any


_FALLBACK_MODELS = {"stub", "fallback", "error"}
_INTERNAL_MARKERS = (
    "llm_failed",
    "llm_provider",
    "api key",
    "api_key",
    "暂无周报 llm 输出",
    "配置 llm",
)


def _percent(value: Any) -> str:
    if value in (None, ""):
        return "暂无"
    text = str(value).strip()
    return text if text.endswith("%") else f"{text}%"


def public_fallback_body(kpi: dict[str, Any], period_type: str = "weekly") -> str:
    """Build a deterministic summary using only already-published KPI data."""
    if period_type == "daily" or "整体露出率" in kpi:
        return (
            "本期报告已按监测数据生成。"
            f"整体露出率 {_percent(kpi.get('整体露出率'))}，"
            f"文章引用率 {_percent(kpi.get('文章引用率'))}。"
            "智能解读暂未生成，请以本页指标和明细为准。"
        )

    ttfm = kpi.get("ttfm_days")
    ttfm_text = f"{ttfm} 天" if ttfm is not None else "暂无"
    return (
        "## 本周数据概览\n"
        f"- 监测命中率为 {_percent(kpi.get('hit_rate'))}。\n"
        f"- 首次命中中位周期：{ttfm_text}；"
        f"新增命中 {kpi.get('new_hits_count', 0)} 项，"
        f"流失 {kpi.get('lost_hits_count', 0)} 项。\n\n"
        "## 建议关注\n"
        "- 优先核查本周新增与流失的提示词，并结合各平台明细确认变化来源。\n\n"
        "## 说明\n"
        "- 本期智能解读暂未生成，以上为系统根据监测硬指标生成的数据摘要。"
    )


def sanitize_briefing_body(
    body: str | None,
    *,
    llm_model: str | None,
    kpi: dict[str, Any],
    period_type: str = "weekly",
) -> str:
    """Replace empty or internal-error fallback text with customer-safe copy."""
    raw = body or ""
    lowered = raw.lower()
    unsafe = (
        not raw.strip()
        or (llm_model or "").strip().lower() in _FALLBACK_MODELS
        or any(marker in lowered for marker in _INTERNAL_MARKERS)
    )
    return public_fallback_body(kpi, period_type) if unsafe else raw
