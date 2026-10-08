"""Model call usage (admin-only): data endpoints for the admin "model usage" page.

Endpoints (prefix /admin/llm-usage):
- GET /admin/llm-usage/overview     - overview KPIs + period-over-period change vs the previous equal-length window
- GET /admin/llm-usage/trend        - daily trend (groupable by service / provider / call_kind)
- GET /admin/llm-usage/by-feature   - ranking by feature scenario (with share and mini trend)
- GET /admin/llm-usage/logs         - call details, paginated on the backend
- GET /admin/llm-usage/filters      - candidate values for filters
- GET /admin/llm-usage/trace/{id}   - all calls under one business action (detail dialog)

The aggregation metric definition is not implemented in this file: everything goes through geo/services/llm_usage_rollup.aggregate_range(),
which is also the implementation of the daily rollup build_day, so the page and the rollup table can never compute different numbers.
History reads llm_usage_daily, today aggregates details in real time (live tail), stitched together in aggregate_range.

"Call count" = SUM(requests_count) rather than row count; see metric definition 2 in geo/models/llm_usage.py for why.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_admin
from geo.models.llm_usage import LlmCallLogORM, LlmUsageDailyORM
from geo.services.llm_usage_rollup import (
    aggregate_range,
    shift_bj,
    today_bj,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm-usage", dependencies=[Depends(require_admin)])

_METRIC_KEYS = ("calls", "rows_n", "ok_calls", "error_calls",
                "prompt_tokens", "completion_tokens", "total_tokens",
                "cached_tokens", "reasoning_tokens",
                "calls_with_tokens", "calls_without_tokens", "latency_ms_sum", "users_n")

_TREND_GROUPS = {"none": [], "service": ["service"], "provider": ["provider"],
                 "model": ["model"], "call_kind": ["call_kind"], "feature": ["feature"]}


def _range(days: int, day_from: Optional[str], day_to: Optional[str]) -> tuple[str, str]:
    """Window parsing: explicit day_from/day_to take precedence, otherwise go back by days (including today)."""
    end = day_to or today_bj()
    start = day_from or shift_bj(end, -(max(1, days) - 1))
    if start > end:
        start, end = end, start
    return start, end


def _filters(service, feature, provider, model, call_kind, status=None,
             user_id=None, topic_id=None) -> dict:
    return {"service": service, "feature": feature, "provider": provider,
            "model": model, "call_kind": call_kind, "status": status,
            "user_id": user_id, "topic_id": topic_id}


def _sum_rows(rows: list[dict]) -> dict:
    out = {k: 0 for k in _METRIC_KEYS}
    for r in rows:
        for k in _METRIC_KEYS:
            v = int(r.get(k) or 0)
            # users_n is a distinct count; summing across groups double-counts people, so take the max as a lower bound
            out[k] = max(out[k], v) if k == "users_n" else out[k] + v
    return out


def _kpi(agg: dict) -> dict:
    calls = agg["calls"]
    with_tok = agg["calls_with_tokens"]
    return {
        **agg,
        "error_rate_pct": round(agg["error_calls"] * 100.0 / calls, 2) if calls else 0.0,
        "avg_latency_ms": int(agg["latency_ms_sum"] / agg["rows_n"]) if agg["rows_n"] else 0,
        # Token coverage: calls without tokens are not mixed into the main numbers; the page shows a badge accordingly
        "token_coverage_pct": round(with_tok * 100.0 / calls, 1) if calls else 0.0,
    }


def _pct_delta(cur: int, prev: int) -> Optional[float]:
    if not prev:
        return None      # Period-over-period is meaningless when the previous period is 0; return null so the frontend shows "—"
    return round((cur - prev) * 100.0 / prev, 1)


@router.get("/overview")
def overview(
    days: int = Query(30, ge=1, le=400),
    day_from: Optional[str] = None,
    day_to: Optional[str] = None,
    service: Optional[str] = None,
    feature: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_kind: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Overview KPIs. delta is the change vs **the previous equal-length window**."""
    start, end = _range(days, day_from, day_to)
    f = _filters(service, feature, provider, model, call_kind)
    span = (_dt.date.fromisoformat(end) - _dt.date.fromisoformat(start)).days + 1

    cur = _sum_rows(aggregate_range(db, start, end, group_by=[], filters=f))
    prev = _sum_rows(aggregate_range(db, shift_bj(start, -span), shift_bj(start, -1),
                                     group_by=[], filters=f))
    kpi = _kpi(cur)
    prev_kpi = _kpi(prev)
    return {
        "range": {"start": start, "end": end, "days": span, "today": today_bj()},
        "kpi": kpi,
        "delta": {
            "calls_pct": _pct_delta(cur["calls"], prev["calls"]),
            "total_tokens_pct": _pct_delta(cur["total_tokens"], prev["total_tokens"]),
            "error_rate_pp": round(kpi["error_rate_pct"] - prev_kpi["error_rate_pct"], 2),
        },
    }


@router.get("/trend")
def trend(
    days: int = Query(30, ge=1, le=400),
    day_from: Optional[str] = None,
    day_to: Optional[str] = None,
    group_by: str = Query("none"),
    service: Optional[str] = None,
    feature: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_kind: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Daily trend. Returned as a date array + parallel series, assembled directly by frontend recharts."""
    start, end = _range(days, day_from, day_to)
    gcols = _TREND_GROUPS.get(group_by, [])
    rows = aggregate_range(db, start, end, group_by=gcols,
                           filters=_filters(service, feature, provider, model, call_kind),
                           by_day=True)

    d0, d1 = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
    all_days = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    idx = {d: i for i, d in enumerate(all_days)}

    series: dict[str, dict] = {}
    for r in rows:
        key = "__all__" if not gcols else "/".join(str(r.get(c) or "") for c in gcols)
        s = series.setdefault(key, {
            "key": key,
            "calls": [0] * len(all_days), "total_tokens": [0] * len(all_days),
            "prompt_tokens": [0] * len(all_days), "completion_tokens": [0] * len(all_days),
            "error_calls": [0] * len(all_days),
        })
        i = idx.get(r.get("bj_date"))
        if i is None:
            continue
        for m in ("calls", "total_tokens", "prompt_tokens", "completion_tokens", "error_calls"):
            s[m][i] += int(r.get(m) or 0)

    ordered = sorted(series.values(), key=lambda s: -sum(s["total_tokens"]))
    return {"days": all_days, "group_by": group_by if gcols else "none", "series": ordered}


@router.get("/by-feature")
def by_feature(
    days: int = Query(30, ge=1, le=400),
    day_from: Optional[str] = None,
    day_to: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    order_by: str = Query("total_tokens"),
    service: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_kind: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Ranking by feature scenario: "which feature burns the most", the core question of this requirement."""
    start, end = _range(days, day_from, day_to)
    f = _filters(service, None, provider, model, call_kind)

    rows = aggregate_range(db, start, end, group_by=["feature"], filters=f)
    by_day = aggregate_range(db, start, end, group_by=["feature"], filters=f, by_day=True)
    top_model = {}
    for r in aggregate_range(db, start, end, group_by=["feature", "model"], filters=f):
        k = r.get("feature")
        if int(r.get("calls") or 0) > int((top_model.get(k) or {}).get("calls") or 0):
            top_model[k] = {"model": r.get("model"), "calls": int(r.get("calls") or 0)}

    d0, d1 = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
    all_days = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    idx = {d: i for i, d in enumerate(all_days)}
    spark: dict[str, list[int]] = {}
    for r in by_day:
        arr = spark.setdefault(r.get("feature") or "", [0] * len(all_days))
        i = idx.get(r.get("bj_date"))
        if i is not None:
            arr[i] += int(r.get("total_tokens") or 0)

    total_all = sum(int(r.get("total_tokens") or 0) for r in rows) or 0
    calls_all = sum(int(r.get("calls") or 0) for r in rows) or 0

    items = []
    for r in rows:
        k = r.get("feature") or ""
        calls = int(r.get("calls") or 0)
        toks = int(r.get("total_tokens") or 0)
        items.append({
            "feature": k,
            "calls": calls,
            "rows_n": int(r.get("rows_n") or 0),
            "error_calls": int(r.get("error_calls") or 0),
            "error_rate_pct": round(int(r.get("error_calls") or 0) * 100.0 / calls, 2) if calls else 0.0,
            "prompt_tokens": int(r.get("prompt_tokens") or 0),
            "completion_tokens": int(r.get("completion_tokens") or 0),
            "total_tokens": toks,
            "cached_tokens": int(r.get("cached_tokens") or 0),
            "avg_latency_ms": int(int(r.get("latency_ms_sum") or 0) / int(r.get("rows_n") or 1)),
            "share_pct": round(toks * 100.0 / total_all, 1) if total_all else 0.0,
            "calls_share_pct": round(calls * 100.0 / calls_all, 1) if calls_all else 0.0,
            "top_model": (top_model.get(k) or {}).get("model") or "",
            "sparkline": spark.get(k) or [0] * len(all_days),
        })

    keyf = {"calls": lambda x: x["calls"], "total_tokens": lambda x: x["total_tokens"],
            "error_rate": lambda x: x["error_rate_pct"]}.get(order_by, lambda x: x["total_tokens"])
    items.sort(key=keyf, reverse=True)
    return {"range": {"start": start, "end": end}, "total_tokens_all": total_all,
            "calls_all": calls_all, "items": items[:limit]}


def _log_item(r: LlmCallLogORM) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "bj_date": r.bj_date,
        "service": r.service, "host": r.host, "feature": r.feature,
        "provider": r.provider, "model": r.model, "endpoint_host": r.endpoint_host,
        "call_kind": r.call_kind, "status": r.status, "error_kind": r.error_kind,
        "http_status": r.http_status,
        "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens, "cached_tokens": r.cached_tokens,
        "reasoning_tokens": r.reasoning_tokens, "token_source": r.token_source,
        "requests_count": r.requests_count, "attempt_no": r.attempt_no,
        "latency_ms": r.latency_ms,
        "user_id": r.user_id, "topic_id": r.topic_id,
        "ref_type": r.ref_type, "ref_id": r.ref_id,
        "trace_id": r.trace_id, "call_group_id": r.call_group_id,
        "extra": json.loads(r.extra_json) if r.extra_json else None,
    }


@router.get("/logs")
def logs(
    days: int = Query(7, ge=1, le=400),
    day_from: Optional[str] = None,
    day_to: Optional[str] = None,
    service: Optional[str] = None,
    feature: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    call_kind: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    topic_id: Optional[int] = None,
    trace_id: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Call details. Paginated on the backend: detail rows reach millions and cannot all be sent to the frontend at once like the user list."""
    start, end = _range(days, day_from, day_to)
    query = db.query(LlmCallLogORM).filter(
        LlmCallLogORM.bj_date >= start, LlmCallLogORM.bj_date <= end)
    for col, val in (("service", service), ("feature", feature), ("provider", provider),
                     ("model", model), ("call_kind", call_kind), ("status", status),
                     ("user_id", user_id), ("topic_id", topic_id), ("trace_id", trace_id)):
        if val not in (None, ""):
            query = query.filter(getattr(LlmCallLogORM, col) == val)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            LlmCallLogORM.model.ilike(like) | LlmCallLogORM.ref_id.ilike(like)
            | LlmCallLogORM.feature.ilike(like) | LlmCallLogORM.error_kind.ilike(like))

    total = query.count()
    rows = (query.order_by(LlmCallLogORM.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_log_item(r) for r in rows]}


@router.get("/filters")
def filters(db: Session = Depends(get_db)):
    """Candidate values for filters. Distinct from the rollup table (two orders of magnitude fewer rows than details), plus details for today's new scenarios."""
    out: dict[str, list[str]] = {}
    for col in ("service", "feature", "provider", "model", "call_kind"):
        vals = {v[0] for v in db.query(distinct(getattr(LlmUsageDailyORM, col))).all() if v[0]}
        today = today_bj()
        vals |= {v[0] for v in db.query(distinct(getattr(LlmCallLogORM, col)))
                 .filter(LlmCallLogORM.bj_date == today).all() if v[0]}
        out[col] = sorted(vals)
    out["status"] = ["ok", "truncated", "parse_error", "http_error", "network_error"]
    return out


@router.get("/trace/{trace_id}")
def trace_detail(trace_id: str, db: Session = Depends(get_db)):
    """All calls under one business action (an article / a diagnosis / a chat round), sorted by time."""
    rows = (db.query(LlmCallLogORM)
            .filter(LlmCallLogORM.trace_id == trace_id)
            .order_by(LlmCallLogORM.id.asc()).limit(500).all())
    items = [_log_item(r) for r in rows]
    return {
        "trace_id": trace_id,
        "calls": sum(int(r.requests_count or 0) for r in rows),
        "total_tokens": sum(int(r.total_tokens or 0) for r in rows),
        "latency_ms": sum(int(r.latency_ms or 0) for r in rows),
        "items": items,
    }
