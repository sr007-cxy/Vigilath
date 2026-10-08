"""Daily rollup + detail cleanup for model call accounting.

Three responsibilities:
  · aggregate_detail / aggregate_daily -- **the single implementation of the aggregation metric definition**. The admin usage page
    (geo/api/admin_llm_usage.py) and this module's build_day both use it, so the two can never compute different numbers.
  · build_day / run_job -- aggregate details into llm_usage_daily. Following the report_daily_rollup pattern:
    each cron run recomputes the last N **closed** Beijing dates (absorbing late writes); **today is never persisted**
    -- today's numbers come from the read path aggregating details directly (live tail).
  · purge_details -- delete expired details. **Before deleting, confirm the day has been rolled up**, otherwise one failed rollup
    plus one successful purge = that day's records vanish forever.

cron (see .claude/apply-llm-usage-cron.sh):
    50 0 * * * cd $BK && $PY scripts/rollup_llm_usage.py --days 3
    20 4 * * * cd $BK && $PY scripts/rollup_llm_usage.py --purge-only

Counting metric definition (read the docstring of geo/models/llm_usage.py before changing):
  · calls  = SUM(requests_count), **not COUNT(*)** -- one pydantic-ai row represents multiple round trips
  · ok_calls  = status in BILLED_STATUSES (ok / truncated / parse_error) --
                requests billed by the provider, used to reconcile with platform bills
  · error_calls = the rest (http_error / network_error) -- wasted requests that cost nothing
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.llm_usage import BILLED_STATUSES, LlmCallLogORM, LlmUsageDailyORM
from geo.services.llm_usage import BJ_OFFSET

log = logging.getLogger(__name__)

# Group-by keys of the rollup table -- consistent with uq_llm_usage_daily_cell
GROUP_COLS = ("service", "feature", "provider", "model", "call_kind")
# Filterable dimensions of details (the page filters + rollup both use these names)
FILTER_COLS = ("service", "feature", "provider", "model", "call_kind", "status",
               "user_id", "topic_id", "trace_id", "host", "endpoint_host")

DEFAULT_KEEP_DAYS = int(os.environ.get("LLM_USAGE_DETAIL_KEEP_DAYS", "90"))


def today_bj() -> str:
    return (datetime.utcnow() + BJ_OFFSET).date().isoformat()


def shift_bj(day: str, delta: int) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=delta)).date().isoformat()


def _zero_metrics() -> dict:
    return {
        "calls": 0, "rows_n": 0, "ok_calls": 0, "error_calls": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "cached_tokens": 0, "reasoning_tokens": 0,
        "calls_with_tokens": 0, "calls_without_tokens": 0,
        "latency_ms_sum": 0, "users_n": 0,
    }


def _apply_filters(q, model, filters: Optional[dict]):
    for k, v in (filters or {}).items():
        if v in (None, "", []):
            continue
        col = getattr(model, k, None)
        if col is None:
            continue
        if isinstance(v, (list, tuple, set)):
            q = q.filter(col.in_(list(v)))
        else:
            q = q.filter(col == v)
    return q


def aggregate_detail(
    db: Session,
    day_from: str,
    day_to: str,
    *,
    group_by: Iterable[str] = GROUP_COLS,
    filters: Optional[dict] = None,
    by_day: bool = False,
) -> list[dict]:
    """Aggregate the Beijing dates [day_from, day_to] directly from the detail table.

    The read path's live tail (today) and build_day (recomputing a closed day) share this one implementation.
    users_n is count(distinct user_id) -- not additive across groups, so only computed at this level;
    merging multiple segments degrades it to "take the max" (see the comment in merge_rows).
    """
    group_by = list(group_by)
    cols = ([LlmCallLogORM.bj_date] if by_day else []) + [getattr(LlmCallLogORM, c) for c in group_by]
    billed = LlmCallLogORM.status.in_(list(BILLED_STATUSES))
    req = LlmCallLogORM.requests_count

    q = db.query(
        *cols,
        func.coalesce(func.sum(req), 0).label("calls"),
        func.count(LlmCallLogORM.id).label("rows_n"),
        func.coalesce(func.sum(case((billed, req), else_=0)), 0).label("ok_calls"),
        func.coalesce(func.sum(case((billed, 0), else_=req)), 0).label("error_calls"),
        func.coalesce(func.sum(LlmCallLogORM.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LlmCallLogORM.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(LlmCallLogORM.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LlmCallLogORM.cached_tokens), 0).label("cached_tokens"),
        func.coalesce(func.sum(LlmCallLogORM.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(case((LlmCallLogORM.total_tokens.isnot(None), req), else_=0)),
                      0).label("calls_with_tokens"),
        func.coalesce(func.sum(case((LlmCallLogORM.total_tokens.is_(None), req), else_=0)),
                      0).label("calls_without_tokens"),
        func.coalesce(func.sum(LlmCallLogORM.latency_ms), 0).label("latency_ms_sum"),
        func.count(distinct(LlmCallLogORM.user_id)).label("users_n"),
    ).filter(
        LlmCallLogORM.bj_date >= day_from,
        LlmCallLogORM.bj_date <= day_to,
    )
    q = _apply_filters(q, LlmCallLogORM, filters)
    if cols:                      # Empty group_by = full aggregate (overview card); cannot call group_by() with nothing
        q = q.group_by(*cols)

    out: list[dict] = []
    for row in q.all():
        d = dict(row._mapping)
        out.append({k: (int(v) if isinstance(v, (int, float)) and k not in ("bj_date",) + tuple(group_by)
                        else v) for k, v in d.items()})
    return out


def aggregate_daily(
    db: Session,
    day_from: str,
    day_to: str,
    *,
    group_by: Iterable[str] = GROUP_COLS,
    filters: Optional[dict] = None,
    by_day: bool = False,
) -> list[dict]:
    """Aggregate results of the same shape from llm_usage_daily (the history segment goes this way)."""
    group_by = list(group_by)
    cols = ([LlmUsageDailyORM.bj_date] if by_day else []) + [getattr(LlmUsageDailyORM, c) for c in group_by]
    sums = ["calls", "rows_n", "ok_calls", "error_calls", "prompt_tokens", "completion_tokens",
            "total_tokens", "cached_tokens", "reasoning_tokens",
            "calls_with_tokens", "calls_without_tokens", "latency_ms_sum"]

    q = db.query(
        *cols,
        *[func.coalesce(func.sum(getattr(LlmUsageDailyORM, s)), 0).label(s) for s in sums],
        # users_n is a distinct count, not additive across rows -- take the max as a lower bound, reference only on the page
        func.coalesce(func.max(LlmUsageDailyORM.users_n), 0).label("users_n"),
    ).filter(
        LlmUsageDailyORM.bj_date >= day_from,
        LlmUsageDailyORM.bj_date <= day_to,
    )
    q = _apply_filters(q, LlmUsageDailyORM, filters)
    if cols:
        q = q.group_by(*cols)
    return [dict(r._mapping) for r in q.all()]


def merge_rows(*batches: Iterable[dict], key_cols: Iterable[str]) -> list[dict]:
    """Merge multi-segment aggregate results (history segment + today segment) by key.

    users_n takes the max instead of summing -- it is a distinct count, and summing would double-count people.
    An exact cross-day distinct user count requires querying details separately, not worth the cost on the page.
    """
    key_cols = list(key_cols)
    merged: dict[tuple, dict] = {}
    for batch in batches:
        for row in batch:
            k = tuple(row.get(c) for c in key_cols)
            cur = merged.get(k)
            if cur is None:
                merged[k] = dict(row)
                continue
            for m in _zero_metrics():
                if m == "users_n":
                    cur[m] = max(int(cur.get(m) or 0), int(row.get(m) or 0))
                else:
                    cur[m] = int(cur.get(m) or 0) + int(row.get(m) or 0)
    return list(merged.values())


def aggregate_range(
    db: Session,
    day_from: str,
    day_to: str,
    *,
    group_by: Iterable[str] = GROUP_COLS,
    filters: Optional[dict] = None,
    by_day: bool = False,
) -> list[dict]:
    """The single entry for the page read path: history reads the rollup table, today reads details (live tail), merged.

    This stitching is deliberate -- numbers are visible on the page the same day instrumentation lands, no waiting for the next day's rollup.
    """
    today = today_bj()
    key_cols = (["bj_date"] if by_day else []) + list(group_by)
    batches = []
    if day_from < today:
        batches.append(aggregate_daily(db, day_from, min(day_to, shift_bj(today, -1)),
                                       group_by=group_by, filters=filters, by_day=by_day))
    if day_to >= today:
        batches.append(aggregate_detail(db, max(day_from, today), day_to,
                                        group_by=group_by, filters=filters, by_day=by_day))
    return merge_rows(*batches, key_cols=key_cols) if batches else []


# ---------------------------------------------------------------------------
# rollup
# ---------------------------------------------------------------------------
def build_day(db: Session, day: str, *, source: str = "rollup") -> dict:
    """Recompute the rollup of one Beijing date. Delete the day's old rows first then insert -- simpler than writing a cross SQLite/PG upsert."""
    rows = aggregate_detail(db, day, day)
    db.query(LlmUsageDailyORM).filter(LlmUsageDailyORM.bj_date == day).delete(
        synchronize_session=False)
    now = datetime.utcnow()
    for r in rows:
        db.add(LlmUsageDailyORM(
            bj_date=day, source=source, updated_at=now,
            **{c: r.get(c) or "" for c in GROUP_COLS},
            **{m: int(r.get(m) or 0) for m in _zero_metrics()},
        ))
    db.commit()
    return {"day": day, "cells": len(rows),
            "calls": sum(int(r.get("calls") or 0) for r in rows)}


def run_job(days: int = 3) -> list[dict]:
    """Cron mode: recompute the last `days` **closed** Beijing dates (excluding today)."""
    db = SessionLocal()
    try:
        today = today_bj()
        out = []
        for i in range(1, days + 1):
            day = shift_bj(today, -i)
            r = build_day(db, day)
            log.info("llm usage rollup %s: %d cells, %d calls", r["day"], r["cells"], r["calls"])
            out.append(r)
        return out
    finally:
        db.close()


def verify(days: int = 3) -> list[str]:
    """Recompute without writing and compare against the table. Returns a list of difference descriptions if any."""
    db = SessionLocal()
    try:
        today = today_bj()
        diffs: list[str] = []
        for i in range(1, days + 1):
            day = shift_bj(today, -i)
            fresh = {tuple(r.get(c) for c in GROUP_COLS): int(r.get("calls") or 0)
                     for r in aggregate_detail(db, day, day)}
            stored = {tuple(getattr(r, c) for c in GROUP_COLS): int(r.calls or 0)
                      for r in db.query(LlmUsageDailyORM).filter(LlmUsageDailyORM.bj_date == day)}
            for k in set(fresh) | set(stored):
                if fresh.get(k, 0) != stored.get(k, 0):
                    diffs.append(f"{day} {k}: detail={fresh.get(k, 0)} daily={stored.get(k, 0)}")
        return diffs
    finally:
        db.close()


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------
def purge_details(keep_days: int = DEFAULT_KEEP_DAYS, *, dry_run: bool = False,
                  batch: int = 5000) -> dict:
    """Delete details older than keep_days.

    **Hard precondition gate**: check day by day that llm_usage_daily has rows for that day before deleting. If the rollup did not succeed, skip and WARN --
    better to keep taking up space than let that day's records vanish forever.
    """
    db = SessionLocal()
    try:
        cutoff = shift_bj(today_bj(), -keep_days)
        days = [d[0] for d in db.query(distinct(LlmCallLogORM.bj_date))
                .filter(LlmCallLogORM.bj_date < cutoff).all()]
        deleted = skipped = 0
        for day in sorted(days):
            has_daily = db.query(LlmUsageDailyORM.id).filter(
                LlmUsageDailyORM.bj_date == day).first() is not None
            n = db.query(func.count(LlmCallLogORM.id)).filter(
                LlmCallLogORM.bj_date == day).scalar() or 0
            if not has_daily:
                log.warning("purge skip %s: day not rolled up, keeping %d detail rows", day, n)
                skipped += n
                continue
            if dry_run:
                log.info("purge dry-run %s: %d rows to delete", day, n)
                deleted += n
                continue
            # Delete in batches to avoid long transactions locking the table / hitting the SQLite write lock
            while True:
                sub = (db.query(LlmCallLogORM.id)
                       .filter(LlmCallLogORM.bj_date == day)
                       .limit(batch).subquery())
                got = (db.query(LlmCallLogORM)
                       .filter(LlmCallLogORM.id.in_(db.query(sub.c.id)))
                       .delete(synchronize_session=False))
                db.commit()
                deleted += got
                if got < batch:
                    break
                time.sleep(0.2)
            log.info("purge %s: deleted %d rows", day, n)
        return {"cutoff": cutoff, "deleted": deleted, "skipped_unrolled": skipped,
                "dry_run": dry_run}
    finally:
        db.close()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="模型调用记账:日汇总 / 校验 / 清理")
    p.add_argument("--day", help="只算这一天(北京日 YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=3, help="重算最近 N 个已收口北京日(默认 3)")
    p.add_argument("--backfill", type=int, help="回填最近 N 天")
    p.add_argument("--verify", action="store_true", help="重算对拍,不写入;有差异 exit 1")
    p.add_argument("--purge-only", action="store_true", help="只跑明细清理")
    p.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.purge_only:
        print(purge_details(a.keep_days, dry_run=a.dry_run))
        return 0
    if a.verify:
        diffs = verify(a.days)
        for d in diffs:
            print("DIFF", d)
        print(f"verify {a.days} 天:{'无差异' if not diffs else str(len(diffs)) + ' 处差异'}")
        return 1 if diffs else 0
    if a.day:
        db = SessionLocal()
        try:
            print(build_day(db, a.day))
        finally:
            db.close()
        return 0

    n = a.backfill or a.days
    db = SessionLocal()
    try:
        today = today_bj()
        for i in range(1, n + 1):
            print(build_day(db, shift_bj(today, -i)))
    finally:
        db.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
