"""report_daily_rollup -- daily-granularity wide-table rollup job for the report center.

Aggregates ai_telemetry_responses by (topic, Beijing day, engine) into count rows written to
ai_telemetry_report_daily (engine='__all__' is the daily total row). The report center's 5
main endpoints read the wide table first; **today, still in progress, is never persisted** (the read path's live tail handles today),
so the wide-table boundary is always yesterday.

Rules:
  - cron mode: per topic recompute the last 3 closed Beijing days (absorbs late data / negative-window updates)
  - rows with source='demo' are never overwritten by the job (unless --overwrite-demo), protecting hand-made data
  - negative-window verdicts are frozen at persist time -- historical reports use the verdict of their period, not rewritten by later verdicts

cron (prod; appended after the crawl_snapshot line, UTC 00:25 = Beijing 08:25):
  25 0 * * * cd <repo-root>/backend && ./.venv/bin/python -m geo.services.report_daily_rollup >> data/report_daily_rollup.log 2>&1

CLI:
  python -m geo.services.report_daily_rollup                       # cron mode (all topics, last 3 days)
  python -m geo.services.report_daily_rollup --topic 18 --backfill # single topic full-history backfill
  python -m geo.services.report_daily_rollup --topic 18 --verify   # recompute and diff; exit 1 on differences
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from geo.api.ai_telemetry import _load_owned_urls
from geo.api.ai_telemetry_reports import (
    ALL_ENGINE, BJ_OFFSET, COUNT_KEYS, EXPOSURE_ONLY_SCENES, _bj_date,
    _day_engine_counts, _load_negative_windows, _load_rows,
    _metric_excl_queries, _metrics_from_counts, no_fixed_denominator,
)
from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    REPORT_DAILY_SCHEMA_VERSION,
    AiTelemetryReportDailyORM, AiTelemetryResponseDetailORM,
    AiTelemetryResponseORM, AiTelemetryTopicORM, deleted_query_texts, metric_brand_terms,
)
from geo.services.response_detail import parse_topic

_PCT_FIELDS = {
    "exposure_pct": "exposure", "top1_pct": "top1", "top3_pct": "top3",
    "accuracy_pct": "accuracy", "citation_pct": "citation", "mention_pct": "mention",
}


def _make_row(topic_id: int, day: str, engine: str, counts: dict, source: str) -> AiTelemetryReportDailyORM:
    # ★ Hard guardrail: the wide table is the authoritative source and must store **real** counts and real percentages. The response-denominator floor is a
    #   read-path display definition (see ai_telemetry_reports._fixed_valid_ctx); once written here it's irreversible,
    #   and verify_topic would immediately flag it as a diff. Today rollup is only entered from CLI/scheduler (ctx empty),
    #   but don't rely on "nobody in the call chain happens to get it wrong" -- someone adding a background recompute endpoint later would hit it.
    with no_fixed_denominator():
        metrics = _metrics_from_counts(counts)
    return AiTelemetryReportDailyORM(
        topic_id=topic_id, day=day, engine=engine,
        **{k: counts[k] for k in COUNT_KEYS},
        **{col: metrics[mk] for col, mk in _PCT_FIELDS.items()},
        schema_version=REPORT_DAILY_SCHEMA_VERSION, source=source,
        generated_at=datetime.utcnow(),
    )


def counts_from_pcts(targets: dict, valid_count: int) -> dict:
    """Pure data-fabrication helper: target percentages -> seven counts (used to INSERT wide-table rows directly when not fabricating responses).

    targets keys: exposure/top1/top3/accuracy/citation/mention (percentages, 0-100).
    Conversion: hit = exposure x valid; top1/top3/neg = rate x hit; cited = citation rate x valid;
    comp_mention is solved back from share of voice (mention = hit/(hit+comp)).
    Note: after round(), percentages recomputed from counts may differ from targets by about +/-0.1; counts are authoritative.
    Fabricated rows are treated as having no brand/competitor-comparison words; cat_* category-word counts = the corresponding totals, ratios unchanged.
    """
    valid = int(valid_count)
    hit = round(float(targets.get("exposure", 0)) / 100 * valid)
    acc = float(targets.get("accuracy", 100.0))
    mention = float(targets.get("mention", 0))
    comp = round(hit / (mention / 100) - hit) if mention > 0 else 0
    top1 = round(float(targets.get("top1", 0)) / 100 * hit)
    top3 = round(float(targets.get("top3", 0)) / 100 * hit)
    return {
        "valid_count": valid,
        "hit_count": hit,
        "top1_count": top1,
        "top3_count": top3,
        "neg_hit_count": round((1 - acc / 100) * hit),
        "cited_count": round(float(targets.get("citation", 0)) / 100 * valid),
        "comp_mention_count": max(0, comp),
        "cat_valid_count": valid,
        "cat_hit_count": hit,
        "cat_top1_count": top1,
        "cat_top3_count": top3,
    }


def make_row_from_counts(topic_id: int, day: str, engine: str, counts: dict,
                         source: str = "demo") -> AiTelemetryReportDailyORM:
    """Pure data-fabrication entry: counts -> wide-table ORM row (percentage columns computed automatically; source defaults to demo to prevent nightly overwrite)."""
    return _make_row(topic_id, day, engine, counts, source)


def _day_engine_counts_from_detail(day_rows) -> dict[str, dict]:
    """One day's detail rows -> {engine: eleven counts}. Same metric definition as computing directly from raw JSON (_counts_for):
    valid/cited/comp_mention count only is_valid rows; the hit family counts only valid AND hit rows;
    cat_* category-word counts exclude rows with scene_type in EXPOSURE_ONLY_SCENES (brand words / competitor-comparison words)."""
    def agg(rows) -> dict:
        c = {k: 0 for k in COUNT_KEYS}
        for r in rows:
            if not r.is_valid:
                continue
            is_cat = (r.scene_type or "unknown") not in EXPOSURE_ONLY_SCENES
            c["valid_count"] += 1
            c["comp_mention_count"] += int(r.competitor_count or 0)
            if is_cat:
                c["cat_valid_count"] += 1
            if r.cites_owned:
                c["cited_count"] += 1
            if r.hit:
                c["hit_count"] += 1
                if is_cat:
                    c["cat_hit_count"] += 1
                if r.is_top1:
                    c["top1_count"] += 1
                    if is_cat:
                        c["cat_top1_count"] += 1
                if r.is_top3:
                    c["top3_count"] += 1
                    if is_cat:
                        c["cat_top3_count"] += 1
                if r.is_negative:
                    c["neg_hit_count"] += 1
        return c

    by_engine: dict[str, list] = defaultdict(list)
    for r in day_rows:
        by_engine[r.engine or ""].append(r)
    out = {ALL_ENGINE: agg(day_rows)}
    for eng, rows in by_engine.items():
        out[eng] = agg(rows)
    return out


def build_topic(
    db: Session, topic_id: int, *,
    days: int = 3, backfill: bool = False, force: bool = False,
    source: str = "job", overwrite_demo: bool = False,
) -> dict:
    """Roll up one topic: first ensure detail-layer coverage (response_detail.parse_topic), then aggregate from details.

    Returns {'written': n, 'skipped_demo': n, 'skipped_existing': n, 'parse': {...}}.

    ★ To recompute history under a new metric definition (e.g. after soft-deleting prompts) you **must pass backfill=True and force=True**:
      below there's `if old and backfill and not force: skipped_existing` -- with backfill alone,
      every day that already has wide-table rows gets skipped; the log looks great, written=0, the report doesn't budge --
      it looks done but isn't.
    """
    today_bj = _bj_date(datetime.utcnow())
    parse_stats = parse_topic(
        db, topic_id, days=days, backfill=backfill, force=force,
        source=source, overwrite_demo=overwrite_demo)

    dq = (db.query(AiTelemetryResponseDetailORM)
            .filter(AiTelemetryResponseDetailORM.topic_id == topic_id))
    if not backfill:
        since = (today_bj - timedelta(days=days)).isoformat()
        dq = dq.filter(AiTelemetryResponseDetailORM.day >= since)
    # Exclude brand words: the wide table aggregates only category words (text contains no brand name/alias).
    # ★ Since 2026-09-10 this applies to **all topics**, no longer a hardcoded switch by topic id --
    #   "ask about the brand, answer with the brand, inflate exposure" is universal, not a one-account quirk, and scene_type tagging
    #   is done manually by ops and can't be the only defense (measured topic 32: 225 of 849 historical query texts contained the brand name,
    #   scene caught only 8; the other 217 were all counted as category words in exposure rate).
    _t = db.query(AiTelemetryTopicORM).get(topic_id)
    _excl_terms = metric_brand_terms(_t) if _t else []
    # Soft-deleted prompts: their historical responses are excluded from the wide table entirely (same metric definition as read path reports._load_rows).
    # Note the response_detail detail layer doesn't filter this -- it's the evidence layer; restored soft-deleted words must recompute as before.
    _gone = deleted_query_texts(_t) if _t else frozenset()
    by_day: dict[str, list] = defaultdict(list)
    for r in dq.filter(AiTelemetryResponseDetailORM.day < today_bj.isoformat()).all():
        if _excl_terms and any(t in (r.query or "") for t in _excl_terms):
            continue
        if r.query in _gone:
            continue
        by_day[r.day].append(r)
    target_days = sorted(by_day)                       # today already filtered out, never persisted

    existing_by_day: dict[str, list[AiTelemetryReportDailyORM]] = defaultdict(list)
    if target_days:
        for row in (
            db.query(AiTelemetryReportDailyORM)
              .filter(AiTelemetryReportDailyORM.topic_id == topic_id)
              .filter(AiTelemetryReportDailyORM.day.in_(target_days))
              .all()
        ):
            existing_by_day[row.day].append(row)

    stats = {"written": 0, "skipped_demo": 0, "skipped_existing": 0, "parse": parse_stats}
    for day in target_days:
        old = existing_by_day.get(day, [])
        if any(r.source == "demo" for r in old) and not overwrite_demo:
            stats["skipped_demo"] += 1
            continue
        if old and backfill and not force:
            stats["skipped_existing"] += 1
            continue
        for r in old:                       # delete whole day then insert: idempotent, no leftover rows for vanished engines
            db.delete(r)
        if old:
            db.flush()                      # flush DELETE before INSERT to avoid unique-constraint conflicts
        for engine, counts in _day_engine_counts_from_detail(by_day[day]).items():
            db.add(_make_row(topic_id, day, engine, counts, source))
        stats["written"] += 1
    db.commit()
    return stats


def verify_topic(db: Session, topic_id: int) -> list[str]:
    """Recompute day by day and diff against stored wide-table rows (counts + percentages). Returns a list of difference descriptions."""
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    rows = _load_rows(db, topic_id)
    _t = db.query(AiTelemetryTopicORM).get(topic_id)
    excl = _metric_excl_queries(_t) if _t else frozenset()
    # Same metric definition as _excl_terms above: exclude brand words for all topics, no per-topic-id switch
    _terms = metric_brand_terms(_t) if _t else []
    if _terms:
        rows = [r for r in rows if not any(t in (r.query or "") for t in _terms)]
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        by_day[_bj_date(r.created_at).isoformat()].append(r)

    stored = (
        db.query(AiTelemetryReportDailyORM)
          .filter(AiTelemetryReportDailyORM.topic_id == topic_id)
          .all()
    )
    stored_map = {(r.day, r.engine): r for r in stored}
    diffs: list[str] = []
    for (day, engine), row in sorted(stored_map.items()):
        expect = _day_engine_counts(by_day.get(day, []), owned, neg, excl).get(engine)
        if expect is None:
            diffs.append(f"{day}/{engine}: 宽表有行但 responses 无对应数据")
            continue
        with no_fixed_denominator():           # must match the persisted definition to reconcile (see _make_row)
            metrics = _metrics_from_counts(expect)
        for k in COUNT_KEYS:
            if int(getattr(row, k)) != int(expect[k]):
                diffs.append(f"{day}/{engine}.{k}: 宽表={getattr(row, k)} 重算={expect[k]}")
        for col, mk in _PCT_FIELDS.items():
            if abs(float(getattr(row, col)) - metrics[mk]) > 1e-6:
                diffs.append(f"{day}/{engine}.{col}: 宽表={getattr(row, col)} 重算={metrics[mk]}")
    today = _bj_date(datetime.utcnow()).isoformat()
    for day in sorted(by_day):
        if day < today and (day, ALL_ENGINE) not in stored_map:
            diffs.append(f"{day}: 已收口但宽表缺行(需 backfill)")
    return diffs


def run_job() -> None:
    db = SessionLocal()
    try:
        topic_ids = [
            tid for (tid,) in
            db.query(AiTelemetryResponseORM.topic_id).distinct().all()
        ]
        for tid in topic_ids:
            stats = build_topic(db, tid, source="job")
            print(f"[report_daily_rollup] topic={tid} {stats}")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="报告中心日粒度宽表汇总")
    ap.add_argument("--topic", type=int, action="append", help="话题 id,可重复;缺省=全部")
    ap.add_argument("--all", action="store_true", help="全部话题")
    ap.add_argument("--backfill", action="store_true", help="全历史回填(缺省只算近 3 日)")
    ap.add_argument("--days", type=int, default=3, help="cron 模式重算的近 N 日(默认 3)")
    ap.add_argument("--force", action="store_true", help="backfill 时已有行也重算")
    ap.add_argument("--overwrite-demo", action="store_true", help="允许覆写 source=demo 的行")
    ap.add_argument("--verify", action="store_true", help="重算 diff 而不写入,有差异 exit 1")
    args = ap.parse_args()

    if not args.topic and not args.all and not args.verify:
        run_job()
        return

    db = SessionLocal()
    try:
        if args.topic:
            topic_ids = args.topic
        else:
            topic_ids = [
                tid for (tid,) in
                db.query(AiTelemetryResponseORM.topic_id).distinct().all()
            ]
        failed = False
        for tid in topic_ids:
            if args.verify:
                diffs = verify_topic(db, tid)
                if diffs:
                    failed = True
                    print(f"[verify] topic={tid} 差异 {len(diffs)} 处:")
                    for d in diffs[:50]:
                        print(f"  {d}")
                    if len(diffs) > 50:
                        print(f"  … 共 {len(diffs)} 处")
                else:
                    print(f"[verify] topic={tid} OK")
            else:
                src = "backfill" if args.backfill else "job"
                stats = build_topic(
                    db, tid, days=args.days, backfill=args.backfill, force=args.force,
                    source=src, overwrite_demo=args.overwrite_demo,
                )
                print(f"[report_daily_rollup] topic={tid} {stats}")
        if failed:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
