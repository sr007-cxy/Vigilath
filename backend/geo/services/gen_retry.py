"""Requeue failed article generation -- the failed draft row itself is the queue item.

Why no separate queue table: failed drafts are already in topic_generated_docs (status=pending_review,
non-empty generation_error), so they are the to-do items themselves. A separate queue table would have to handle "queue item and draft row state
out of sync" (draft manually regenerated, deleted, reviewed), not worth it. So only two columns are added to the draft row:

  gen_attempts       number of automatic retries so far (reset after a successful manual regeneration)
  gen_next_retry_at  time of the next automatic retry; None = not queued (terminal error / given up / succeeded)

Routing metric definition (classify): **whether retrying can fix it** is the only criterion.
  Retryable -- timeout / connection dropped / 429 rate limit / 5xx / empty body (model glitch)
  Terminal  -- 402 balance, 401/403 key, key not configured, output truncated (needs max_tokens change, not a retry),
           JSON parse failure (prompt or length issue, retrying will most likely reproduce it)

Backoff: 5min -> 30min -> 2h, at most GEO_GEN_RETRY_MAX (default 3) times. Then stop, leaving the error cause for humans.

How it runs:
  - environments with an APScheduler leader -> content_scheduler.attach mounts a 10-minute interval
  - prod has no leader (see reference_prod_no_scheduler_leader) -> runs via ubuntu crontab
      cd /opt/geo/backend && python3 -m geo.services.gen_retry
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import TopicGeneratedDocORM

log = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.environ.get("GEO_GEN_RETRY_MAX", "3"))
# Max articles regenerated per tick (20-60s per LLM call; do not let one cron round drag into the next)
BATCH_LIMIT = int(os.environ.get("GEO_GEN_RETRY_BATCH", "8"))
# Max articles per topic per round -- when a whole topic batch fails it should not monopolize the round's quota
PER_TOPIC_LIMIT = int(os.environ.get("GEO_GEN_RETRY_PER_TOPIC", "3"))
# Wait before retry 1/2/3; beyond the list length, use the last value
BACKOFF_MIN = [5, 30, 120]

# Error cause classification -- kept consistent with the buckets in scripts/diag_gen_failures.py (that one only counts; this one decides whether to retry)
_BUCKETS: list[tuple[str, bool, re.Pattern[str]]] = [
    ("timeout", True,
     re.compile(r"timed? ?out|timeout|ReadTimeout|ConnectTimeout|ConnectionError|"
                r"Connection aborted|请求失败", re.I)),
    ("rate_limit", True, re.compile(r"\b429\b|rate.?limit|too many requests", re.I)),
    ("server_5xx", True, re.compile(r"\b(500|502|503|504|522|524)\b|internal server error|"
                                    r"bad gateway|service unavailable", re.I)),
    ("empty_body", True, re.compile(r"empty title/body", re.I)),
    ("bad_shape", True, re.compile(r"unexpected .* response shape", re.I)),
    ("quota", False, re.compile(r"\b402\b|insufficient|balance|quota|credit", re.I)),
    ("auth", False, re.compile(r"\b(401|403)\b|unauthor|invalid api key", re.I)),
    ("no_key", False, re.compile(r"API_KEY.*未配置", re.I)),
    ("truncated", False, re.compile(r"finish_reason=length|输出被截断", re.I)),
    ("json_parse", False, re.compile(r"failed to parse JSON", re.I)),
    # The draft row itself lacks data (no seed/prompt text, topic deleted) -- retrying until dawn would be the same, so terminal directly
    ("bad_input", False, re.compile(r"缺少提示词文本|topic \d+ not found", re.I)),
]


def classify(err: str) -> tuple[str, bool]:
    """Error string -> (category name, worth auto-retrying). Unrecognized ones are treated as retryable (better to try once more)."""
    text = err or ""
    for name, retryable, pat in _BUCKETS:
        if pat.search(text):
            return name, retryable
    return "other", True


def _backoff(attempts: int) -> timedelta:
    idx = min(max(attempts - 1, 0), len(BACKOFF_MIN) - 1)
    return timedelta(minutes=BACKOFF_MIN[idx])


def mark_failure(db: Session, doc: TopicGeneratedDocORM, err: str,
                 *, commit: bool = False) -> tuple[str, bool]:
    """Unified persistence for generation failures: write the error cause + decide whether to schedule the next retry.

    Returns (category name, queued). The caller is responsible for commit (with commit=True it commits here as well).
    """
    msg = (err or "")[:500]
    bucket, retryable = classify(msg)
    doc.generation_error = msg
    attempts = int(getattr(doc, "gen_attempts", 0) or 0) + 1
    doc.gen_attempts = attempts
    queued = retryable and attempts <= MAX_ATTEMPTS
    doc.gen_next_retry_at = (datetime.utcnow() + _backoff(attempts)) if queued else None
    if commit:
        db.commit()
    log.info("[gen-retry] doc=%s failed (%s) attempt %d%s", doc.id, bucket, attempts,
             f",{_backoff(attempts)} 后重试" if queued
             else ("(终态错误,不重试)" if not retryable else "(已达重试上限)"))
    return bucket, queued


def mark_success(doc: TopicGeneratedDocORM) -> None:
    """Generation succeeded: clear the error cause + dequeue + reset the retry count (successful manual regeneration also goes here)."""
    doc.generation_error = None
    doc.gen_attempts = 0
    doc.gen_next_retry_at = None


def give_up(db: Session, doc: TopicGeneratedDocORM) -> None:
    """Manually give up on automatic retries: dequeue but keep the error cause (can still be regenerated manually)."""
    doc.gen_next_retry_at = None
    doc.gen_attempts = max(int(getattr(doc, "gen_attempts", 0) or 0), MAX_ATTEMPTS)
    db.commit()


def due_docs(db: Session, *, now: Optional[datetime] = None,
             limit: int = BATCH_LIMIT) -> list[TopicGeneratedDocORM]:
    """Drafts due for retry -- first come first served by next_retry_at, throttled per topic."""
    now = now or datetime.utcnow()
    rows = (db.query(TopicGeneratedDocORM)
              .filter(TopicGeneratedDocORM.gen_next_retry_at.isnot(None))
              .filter(TopicGeneratedDocORM.gen_next_retry_at <= now)
              .filter(TopicGeneratedDocORM.gen_attempts <= MAX_ATTEMPTS)
              # Do not touch the body of drafts already reviewed / published
              .filter(TopicGeneratedDocORM.status.in_(["pending_review", "draft", "generating"]))
              .order_by(TopicGeneratedDocORM.gen_next_retry_at.asc())
              .limit(limit * 4)
              .all())
    # After a prompt is soft-deleted, failed drafts written for that term should not keep retrying and burning money indefinitely -- dequeue directly.
    # Dequeueing does not delete the draft: the error cause remains and it can still be regenerated manually.
    gone_by_topic: dict[int, frozenset] = {}

    def _is_gone(r: TopicGeneratedDocORM) -> bool:
        from geo.models.ai_telemetry import AiTelemetryTopicORM, deleted_query_texts

        if r.topic_id not in gone_by_topic:
            t = db.get(AiTelemetryTopicORM, r.topic_id)
            gone_by_topic[r.topic_id] = deleted_query_texts(t) if t is not None else frozenset()
        gone = gone_by_topic[r.topic_id]
        return bool(gone) and (r.source_query_text or "") in gone

    out: list[TopicGeneratedDocORM] = []
    per_topic: dict[int, int] = {}
    for r in rows:
        if _is_gone(r):
            r.gen_next_retry_at = None
            continue
        n = per_topic.get(r.topic_id, 0)
        if n >= PER_TOPIC_LIMIT:
            continue
        per_topic[r.topic_id] = n + 1
        out.append(r)
        if len(out) >= limit:
            break
    db.commit()                                # Persist the gen_next_retry_at=None from the "soft-deleted term dequeue" above
    return out


def retry_tick() -> dict:
    """Cron entry: regenerate due failed drafts. Returns stats for logs/inspection."""
    from geo.services.content_generator import _resolve_provider, regenerate_doc

    db = SessionLocal()
    stat = {"due": 0, "ok": 0, "failed": 0, "requeued": 0, "gave_up": 0}
    # With no usable LLM provider, **skip the whole round** without touching anything.
    # Otherwise each draft would raise "key not configured" -> classified as no_key terminal -> the queue silently emptied,
    # while the logs make it look like "retries ran". This is a configuration problem and should not be charged to the drafts.
    if not _resolve_provider()[0]:
        stat["skipped_no_provider"] = True
        log.error("[gen-retry] no usable LLM key (DEEPSEEK_API_KEY / OPENROUTER_API_KEY), "
                  "skipping this round, queue untouched")
        db.close()
        return stat
    try:
        docs = due_docs(db)
        stat["due"] = len(docs)
        if not docs:
            log.info("[gen-retry] no drafts due for retry")
            return stat
        log.info("[gen-retry] %d drafts this round", len(docs))
        for doc in docs:
            doc_id = doc.id
            try:
                regenerate_doc(db, doc)     # On success, mark_success is already called internally
                stat["ok"] += 1
                log.info("[gen-retry] doc=%s regenerated successfully", doc_id)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                fresh = db.get(TopicGeneratedDocORM, doc_id)
                if fresh is None:
                    continue
                fresh.status = "pending_review"
                _, queued = mark_failure(db, fresh, str(e), commit=True)
                stat["failed"] += 1
                stat["requeued" if queued else "gave_up"] += 1
        return stat
    finally:
        log.info("[gen-retry] round finished %s", stat)
        db.close()


def requeue(*, bucket: Optional[str] = None, topic_id: Optional[int] = None,
            limit: int = 0, dry_run: bool = True) -> dict:
    """**Requeue** failed drafts that were already dequeued (manually triggered by operators).

    When to use: terminal classification means "retrying cannot fix it", but that no longer holds once **the environment itself changes** --
    e.g. on 2026-08-21 GEO_CONTENT_MAX_TOKENS went from 8000 to 16000 (the model had long since switched from
    deepseek-chat to v4-flash), making it meaningful to rerun all earlier truncated / json_parse drafts.
    This does not change classify's metric definition (the same failure next time still will not be auto-requeued); it only provides an
    explicit manual entry point.

    Pass bucket (truncated / json_parse / quota ...) to requeue only that category; omit for all.
    topic_id can restrict the operation to a single customer topic, avoiding touching other topics during bulk fixes.
    """
    db = SessionLocal()
    stat: dict = {"matched": 0, "requeued": 0, "by_bucket": {}}
    try:
        query = (db.query(TopicGeneratedDocORM)
                   .filter(TopicGeneratedDocORM.generation_error.isnot(None),
                           TopicGeneratedDocORM.generation_error != "",
                           TopicGeneratedDocORM.gen_next_retry_at.is_(None),
                           TopicGeneratedDocORM.deleted_at.is_(None),
                           TopicGeneratedDocORM.status.notin_(("published", "approved"))))
        if topic_id is not None:
            query = query.filter(TopicGeneratedDocORM.topic_id == topic_id)
        rows = query.order_by(TopicGeneratedDocORM.id.desc()).all()
        now = datetime.utcnow()
        for doc in rows:
            b, _ = classify(doc.generation_error or "")
            if bucket and b != bucket:
                continue
            stat["matched"] += 1
            stat["by_bucket"][b] = stat["by_bucket"].get(b, 0) + 1
            if limit and stat["requeued"] >= limit:
                continue
            if dry_run:
                continue
            doc.gen_attempts = 0            # Restart counting, otherwise it would exceed the limit as soon as it enters the queue
            doc.gen_next_retry_at = now
            stat["requeued"] += 1
        if not dry_run:
            db.commit()
    finally:
        db.close()
    return stat


if __name__ == "__main__":  # pragma: no cover -- crontab entry point
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ap = argparse.ArgumentParser(description="生成失败重试队列")
    ap.add_argument("--requeue", action="store_true",
                    help="把已出队的失败稿重新排进队列(环境变了之后用)")
    ap.add_argument("--bucket", default="", help="只重排某一类错因,如 truncated / json_parse")
    ap.add_argument("--topic-id", type=int, default=0, help="只重排指定主题的失败稿")
    ap.add_argument("--limit", type=int, default=0, help="最多重排几篇(0=不限)")
    ap.add_argument("--apply", action="store_true", help="真正写库(默认只统计)")
    args = ap.parse_args()
    # When crontab runs python -m the environment is clean, so .env must be loaded first (see geo/cron_env.py)
    from geo.cron_env import load_env_for_cron
    load_env_for_cron(required=("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"))
    if args.requeue:
        print(requeue(bucket=args.bucket or None, topic_id=args.topic_id or None,
                      limit=args.limit,
                      dry_run=not args.apply))
    else:
        print(retry_tick())
