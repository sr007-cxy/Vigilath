"""response_detail -- parse job for the per-response flattened detail table (parse layer).

Parses ai_telemetry_responses' JSON strings (competitors/citations) and derived verdicts (negative / own citation /
scene type) into real columns of ai_telemetry_response_detail. Three-layer structure:

    responses (raw) -> response_detail (parsed detail, this module) -> report_daily (daily rollup)

Rules:
  - Incremental: responses missing detail rows are fully parsed; existing rows from the last N days (default 3) are refreshed,
    absorbing negative-window / own-URL-set changes (verdicts frozen at persist, same semantics as the rollup wide table)
  - Today is parsed too (the detail layer doesn't distinguish in-progress; only the rollup layer leaves today to the live tail)
  - rows with source='demo' are never overwritten by the job (unless --overwrite-demo)
  - competitor_count strictly matches the rollup definition (length of the _comp_names list)

Called automatically by report_daily_rollup.build_topic before aggregation; usually no need to run separately;
manual CLI:
  python -m geo.services.response_detail --topic 2 --backfill   # parse full history
  python -m geo.services.response_detail --topic 2 --verify     # diff detail vs raw re-parse
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from geo.api.ai_telemetry import _load_owned_urls, _norm_url
from geo.api.ai_telemetry_reports import (
    _comp_universe, comp_hits_in_text,
    BJ_OFFSET, _bj_date, _cites_owned, _is_negative_resp, _load_negative_windows,
)
from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    RESPONSE_DETAIL_SCHEMA_VERSION,
    AiTelemetryResponseDetailORM, AiTelemetryResponseORM, AiTelemetryTopicORM,
    metric_brand_terms,
)
from geo_checker.analyzers.source_factor import classify_factor

SLOTS = 5  # 5 slots each for competitors / citations; overflow goes to count + *_text


def _scene_by_query(topic: AiTelemetryTopicORM) -> dict[str, str]:
    try:
        items = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for it in items:
        if isinstance(it, dict) and it.get("text"):
            out[it["text"]] = it.get("scene_type") or "unknown"
        elif isinstance(it, str):
            out[it] = "unknown"
    return out


def _parse_competitors(raw: str | None) -> list[tuple[str, int]]:
    """Supports both list[str] and list[{name,count,snippet}] shapes. Length = original list length."""
    try:
        v = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(v, list):
        return []
    out: list[tuple[str, int]] = []
    for x in v:
        if not x:
            continue
        if isinstance(x, dict):
            out.append((str(x.get("name") or ""), int(x.get("count") or 1)))
        else:
            out.append((str(x), 1))
    return out


def _parse_citations(raw: str | None) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    return [c for c in v if isinstance(c, dict) and c.get("url")] if isinstance(v, list) else []


def _competitors_of(r: AiTelemetryResponseORM, universe: dict | None):
    """Competitors in this response [(name, occurrence count)].

    ★ Since 2026-09-10 this uses **body-text matching**, no longer the extractor (competitors_json). Extractor recall varies by
      two orders of magnitude across topics (measured topic 32: Gongniu appears in 70.6% of bodies, the extractor listed only 1.4%),
      and competitor_count is the denominator of the "share of voice" metric -- a denominator missing most real competitor mentions
      makes share of voice systematically high. The extractor now only **discovers which competitors exist** (builds the candidate set).
      Definition is identical to the report read path's `comp_hits_in_text` -- the same function.
    """
    if not universe:
        return _parse_competitors(r.competitors_json)
    text = r.answer or ""
    names = comp_hits_in_text(text, universe)
    return [(n, text.count(n)) for n in names]


def parse_response(r: AiTelemetryResponseORM, scene_by_query: dict[str, str],
                   owned: set[str], neg, comp_universe: dict | None = None) -> dict:
    """One response -> detail column dict (without source/generated_at).

    When `comp_universe` is non-empty, competitors are taken by **body-text matching** (same yardstick as the report read path / leaderboard);
    when empty it falls back to the extractor definition, behaving byte-for-byte as before the change.
    """
    is_valid = not r.error
    hit = bool(r.hit)
    is_neg = bool(hit and _is_negative_resp(r, neg))
    comps = _competitors_of(r, comp_universe)
    cits = _parse_citations(r.citations_json)
    owned_flags = [(_norm_url(c["url"]) in owned) if owned else False for c in cits]

    d: dict = {
        "response_id": r.id, "topic_id": r.topic_id, "run_id": r.run_id,
        "day": _bj_date(r.created_at).isoformat(),
        "ts_bj": r.created_at + BJ_OFFSET,
        "engine": r.engine or "", "query": r.query,
        "scene_type": scene_by_query.get(r.query, "unknown"),
        "is_valid": is_valid, "error_text": r.error,
        "hit": hit, "brand_rank": r.brand_rank if isinstance(r.brand_rank, int) else None,
        "is_top1": bool(hit and r.brand_rank == 1),
        "is_top3": bool(hit and isinstance(r.brand_rank, int) and r.brand_rank <= 3),
        "mention_position": r.mention_position,
        "is_negative": is_neg,
        "sentiment": "negative" if is_neg else ("positive" if hit else "unknown"),
        "answer_format": r.answer_format,
        "competitor_count": len(comps),   # definition = _comp_names length, consistent with rollup share-of-voice denominator
        "comp_names_text": " | ".join(n for n, _ in comps),
        "citation_count": len(cits),
        "cites_owned": _cites_owned(r, owned),
        "owned_cite_count": sum(owned_flags),
        "cite_domains_text": " | ".join(
            (c.get("domain") or "") for c in cits if c.get("domain")),
        "cite_urls_text": " | ".join(c["url"] for c in cits),
        "source_url": r.source_url, "hit_excerpt": r.hit_excerpt,
        "answer_chars": len(r.answer or ""),
        "schema_version": RESPONSE_DETAIL_SCHEMA_VERSION,
    }
    for i in range(SLOTS):
        n = i + 1
        d[f"comp{n}_name"] = comps[i][0] if i < len(comps) else None
        d[f"comp{n}_count"] = comps[i][1] if i < len(comps) else None
        if i < len(cits):
            c = cits[i]
            d[f"cite{n}_url"] = c["url"]
            d[f"cite{n}_type"] = classify_factor(
                c["url"], c.get("title") or "", c.get("domain") or "", owned_flags[i])
            d[f"cite{n}_owned"] = owned_flags[i]
        else:
            d[f"cite{n}_url"] = d[f"cite{n}_type"] = d[f"cite{n}_owned"] = None
    return d


def _topic_context(db: Session, topic_id: int):
    topic = db.get(AiTelemetryTopicORM, topic_id)
    scene = _scene_by_query(topic) if topic else {}
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    # Competitor candidate set: built once per topic. The extractor is only used here (to discover names); coverage/counts always use body matching.
    universe: dict = {}
    try:
        rows = db.query(AiTelemetryResponseORM).filter(
            AiTelemetryResponseORM.topic_id == topic_id).all()
        universe = _comp_universe(
            rows, tuple(metric_brand_terms(topic)) if topic else ())
    except Exception:  # noqa: BLE001
        pass                     # if it can't be built, fall back to the extractor definition; don't let the whole parse job die
    return scene, owned, neg, universe


def parse_topic(
    db: Session, topic_id: int, *,
    days: int = 3, backfill: bool = False, force: bool = False,
    source: str = "job", overwrite_demo: bool = False,
) -> dict:
    """Parse one topic. Returns {'inserted': n, 'refreshed': n, 'skipped_demo': n}."""
    scene, owned, neg, comp_universe = _topic_context(db, topic_id)

    existing = {
        row.response_id: row for row in
        db.query(AiTelemetryResponseDetailORM)
          .filter(AiTelemetryResponseDetailORM.topic_id == topic_id)
          .all()
    }
    q = db.query(AiTelemetryResponseORM).filter(
        AiTelemetryResponseORM.topic_id == topic_id)
    if not backfill:
        # Incremental window: everything in the last N days (refresh verdicts) + rows outside the window missing detail (catch historical gaps)
        since = datetime.combine(
            _bj_date(datetime.utcnow()) - timedelta(days=days),
            datetime.min.time()) - BJ_OFFSET
        rows = list(q.filter(AiTelemetryResponseORM.created_at >= since).all())
        have = set(existing)
        old_ids = [rid for (rid,) in
                   db.query(AiTelemetryResponseORM.id)
                     .filter(AiTelemetryResponseORM.topic_id == topic_id)
                     .filter(AiTelemetryResponseORM.created_at < since)
                     .all()
                   if rid not in have]
        for i in range(0, len(old_ids), 500):
            rows += q.filter(AiTelemetryResponseORM.id.in_(old_ids[i:i + 500])).all()
    else:
        rows = q.all()

    stats = {"inserted": 0, "refreshed": 0, "skipped_demo": 0, "skipped_existing": 0}
    for r in rows:
        old = existing.get(r.id)
        if old is not None:
            if old.source == "demo" and not overwrite_demo:
                stats["skipped_demo"] += 1
                continue
            # Rows on an older version are force re-parsed (self-heal on definition upgrade); same-version rows skip per the force rule
            if (backfill and not force
                    and old.schema_version == RESPONSE_DETAIL_SCHEMA_VERSION):
                stats["skipped_existing"] += 1
                continue
        vals = parse_response(r, scene, owned, neg, comp_universe)
        if old is not None:
            for k, v in vals.items():
                setattr(old, k, v)
            old.source = source
            old.generated_at = datetime.utcnow()
            stats["refreshed"] += 1
        else:
            db.add(AiTelemetryResponseDetailORM(
                **vals, source=source, generated_at=datetime.utcnow()))
            stats["inserted"] += 1
    db.commit()
    return stats


def verify_topic(db: Session, topic_id: int) -> list[str]:
    """Column-by-column diff of detail rows vs raw JSON re-parse. Returns a list of difference descriptions."""
    scene, owned, neg, comp_universe = _topic_context(db, topic_id)
    details = {
        row.response_id: row for row in
        db.query(AiTelemetryResponseDetailORM)
          .filter(AiTelemetryResponseDetailORM.topic_id == topic_id)
          .all()
    }
    diffs: list[str] = []
    rows = (db.query(AiTelemetryResponseORM)
              .filter(AiTelemetryResponseORM.topic_id == topic_id).all())
    seen = set()
    for r in rows:
        seen.add(r.id)
        row = details.get(r.id)
        if row is None:
            diffs.append(f"response {r.id}: 缺明细行")
            continue
        expect = parse_response(r, scene, owned, neg, comp_universe)
        for k, v in expect.items():
            got = getattr(row, k)
            if k == "ts_bj":   # DateTime microsecond truncation tolerance
                if abs((got - v).total_seconds()) > 1:
                    diffs.append(f"response {r.id}.{k}: 明细={got} 重算={v}")
                continue
            if got != v:
                diffs.append(f"response {r.id}.{k}: 明细={got!r} 重算={v!r}")
    for rid in set(details) - seen:
        diffs.append(f"detail response_id={rid}: 原始行已不存在(孤行)")
    return diffs


def run_job() -> None:
    db = SessionLocal()
    try:
        topic_ids = [tid for (tid,) in
                     db.query(AiTelemetryResponseORM.topic_id).distinct().all()]
        for tid in topic_ids:
            print(f"[response_detail] topic={tid} {parse_topic(db, tid, source='job')}")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="应答级打横明细表解析")
    ap.add_argument("--topic", type=int, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="全历史(缺省近 3 日+补漏)")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="backfill 时已有行也重解析")
    ap.add_argument("--overwrite-demo", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if not args.topic and not args.all and not args.verify:
        run_job()
        return
    db = SessionLocal()
    try:
        topic_ids = args.topic or [
            tid for (tid,) in
            db.query(AiTelemetryResponseORM.topic_id).distinct().all()]
        failed = False
        for tid in topic_ids:
            if args.verify:
                diffs = verify_topic(db, tid)
                if diffs:
                    failed = True
                    print(f"[verify] topic={tid} 差异 {len(diffs)} 处:")
                    for d in diffs[:30]:
                        print(f"  {d}")
                else:
                    print(f"[verify] topic={tid} OK")
            else:
                src = "backfill" if args.backfill else "job"
                print(f"[response_detail] topic={tid} "
                      f"{parse_topic(db, tid, days=args.days, backfill=args.backfill, force=args.force, source=src, overwrite_demo=args.overwrite_demo)}")
        if failed:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
