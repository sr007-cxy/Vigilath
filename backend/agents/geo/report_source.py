"""Report-center data adapter layer — the **only** entry for the agent side to read report data.

Why a separate layer (2026-07 redesign): the agent's original GEO metric tools were hand-written aggregations (scanning
ai_telemetry_responses directly), without valid_count / negative window / own-URL citation detection / category term counts,
a different metric definition from the report-center page, so the numbers didn't match.

Since 2026-08 the generic data-fetching skeleton moved to `geo.services.report_metrics_source` (the brand growth page hit
the same pitfall and must share one copy); this module keeps the agent-specific processing layer (target evaluation, AI summary body,
condensed period-over-period, etc.) and re-exports the generic parts as-is — the agent-side import paths don't change.

Why not call the daily_report / weekly_report / monthly_report endpoint functions directly: they depend on
`current_user: User` and `raise HTTPException`, and their return bodies carry hundreds of lines of per-item detail.

★ Critical: `_wide_stats`' `excl` parameter defaults to empty; **omitting it doesn't error but exposure rate/TOP1/TOP3 silently run high**.
  All data fetching must go through `load_stats()`, which forces computing excl. The tool layer must not call `_wide_stats` itself.

All imports are lazy (inside functions), consistent with the existing style of tools.py / report_daily_rollup.py.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# The single implementation of the generic data-fetching skeleton; only re-exported here, don't rewrite any of it in this file.
from geo.services.report_metrics_source import (  # noqa: F401
    METRIC_ORDER,
    Prepared,
    closed_key,
    load_stats,
    metric_labels,
    period_series,
    prev_key,
    responses_of,
    slim,
    today_key,
)


def daily_accuracy_7d(stats: dict, keys: list[str], idx: int) -> float:
    """Daily report positive/neutral rate = rolling 7-day window (copied from daily_report L874-877).

    A single day has only a few hit samples; recomputing per day zigzags between 100<->66 and distorts day-over-day.
    """
    from geo.api.ai_telemetry_reports import ALL_ENGINE, _metrics_from_counts, _sum_counts

    # A rolling 7-day window isn't a period; no granularity passed = no floor (the positive/neutral rate denominator is hit count, unaffected anyway)
    agg = _sum_counts([stats[k].get(ALL_ENGINE, {}) for k in keys[max(0, idx - 6): idx + 1]])
    return _metrics_from_counts(agg)["accuracy"]


def pin_baseline(metrics: dict, topic_id: int, idx: int) -> dict:
    """Baseline-date anchoring: the first day (and the next day's "yesterday" column) shows fixed numbers per the delivery baseline (copied from L908-913).

    Without it, the demo topic's first-day numbers would differ a lot from the page.
    """
    from geo.api.ai_telemetry_reports import BASELINE_DAY_PIN

    if idx == 0 and topic_id in BASELINE_DAY_PIN:
        return {**metrics, **BASELINE_DAY_PIN[topic_id]}
    return metrics


def engine_exposure(stats: Optional[dict], period_type: str, key: str, limit: int = 6) -> list[dict]:
    """Overall exposure rate per platform (descending, at most limit entries). Returns an empty list when there are no wide-table stats.

    Only exposure is given: six metrics × multiple engines would blow up the return body; exposure per platform is enough.
    """
    if not stats:
        return []
    from geo.api.ai_telemetry_reports import (
        _GROUPERS, ALL_ENGINE, _metrics_from_counts, _sum_counts,
    )

    days = [d for d in stats if _GROUPERS[period_type](date.fromisoformat(d)) == key]
    if not days:
        return []
    engines = {e for d in days for e in stats[d] if e != ALL_ENGINE}
    out = []
    for eng in engines:
        agg = _sum_counts([stats[d].get(eng, {}) for d in days if eng in stats[d]])
        if agg.get("cat_valid_count") or agg.get("valid_count"):
            # Per-engine denominator pinned to an equal share (copied from the report center weekly_report's eng_metrics)
            out.append({"engine": eng, "exposure": _metrics_from_counts(
                agg, engine_slice=True, period_type=period_type)["exposure"]})
    out.sort(key=lambda x: x["exposure"], reverse=True)
    return out[:limit]


def targets_for(topic, keys: list[str], series: dict, key: str) -> dict:
    """{metric: {target, target_high, progress}} — target evaluation, only used for the weekly report metric definition.

    baseline takes the first period's value, same metric definition as kpi_summary (L775-786).
    """
    from geo.api.ai_telemetry_reports import _progress_of, _resolve_target

    tier = getattr(topic, "brand_tier", "") or ""
    cur = series.get(key) or {}
    base_key = keys[0] if keys else None
    out = {}
    for mk in METRIC_ORDER:
        baseline = (series.get(base_key) or {}).get(mk) if base_key else None
        target, target_high = _resolve_target(mk, baseline, tier)
        if target is None:
            continue
        out[mk] = {
            "target": target,
            "target_high": target_high,
            "progress": _progress_of(cur.get(mk, 0.0), target),
        }
    return out


def ai_summary(db, topic_id: int, period_type: str, key: str, cap: int = 800) -> Optional[str]:
    """The AI summary body for the period. monthly has no body (the briefing table only has daily/weekly period_types).

    ★ Self-contained implementation, **not depending on** ai_telemetry_reports' private helper: that side's
      `_daily_ai_summary` was added later and older deployments lack it; a hard import would crash the whole tool.
      The lookup here is line-for-line equivalent (period_start = the naive UTC corresponding to Beijing midnight of the day).
    """
    from datetime import datetime, timedelta

    from geo.models.ai_telemetry import AiTelemetryTopicBriefingORM

    bj = timedelta(hours=8)
    body: Optional[str] = None
    # period_type is a later-added column: older deployments' briefing only has weekly reports, no daily body.
    has_pt = hasattr(AiTelemetryTopicBriefingORM, "period_type")
    if period_type == "daily":
        if not has_pt:
            return None                       # Old schema has no daily briefing
        p_start = datetime.combine(date.fromisoformat(key), datetime.min.time()) - bj
        b = (db.query(AiTelemetryTopicBriefingORM)
               .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
               .filter(AiTelemetryTopicBriefingORM.period_type == "daily")
               .filter(AiTelemetryTopicBriefingORM.period_start == p_start)
               .order_by(AiTelemetryTopicBriefingORM.generated_at.desc())
               .first())
        body = (b.body_md or None) if b is not None else None
    elif period_type == "weekly":
        q = db.query(AiTelemetryTopicBriefingORM).filter(
            AiTelemetryTopicBriefingORM.topic_id == topic_id)
        if has_pt:
            q = q.filter(AiTelemetryTopicBriefingORM.period_type == "weekly")
        for b in q.all():
            if not b.period_start:
                continue
            d = (b.period_start + bj).date()
            y, w, _ = d.isocalendar()
            if f"{y}-W{w:02d}" == key:
                body = b.body_md or None
                break
    if body and len(body) > cap:
        body = body[:cap] + "…(正文较长,已截断)"
    return body


def delta_pp(cur: dict, prev: Optional[dict]) -> dict:
    """Period-over-period (percentage points). Empty when there's no previous period; flat/missing items aren't filled with null, to save tokens."""
    if not prev:
        return {}
    out = {}
    for mk in METRIC_ORDER:
        if mk in cur and mk in prev:
            d = round(cur[mk] - prev[mk], 1)
            if d:
                out[mk] = d
    return out


__all__ = [
    # Re-exported from geo.services.report_metrics_source
    "METRIC_ORDER", "Prepared", "metric_labels", "load_stats", "period_series",
    "today_key", "closed_key", "prev_key", "slim", "responses_of",
    # Agent-specific processing layer
    "daily_accuracy_7d", "pin_baseline", "engine_exposure", "targets_for",
    "ai_summary", "delta_pp",
]
