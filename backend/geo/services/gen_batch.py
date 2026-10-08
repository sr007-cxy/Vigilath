"""Publish batch ledger -- the fact layer for "which terms each generation trigger selected".

Why this layer exists: before it, there was no authoritative record of "which terms this batch selected". Drafts only had the single value
`source_query_text` (freely editable by users), plus a uuid pointing to a row in `plan.publishing_plan_json`,
and that JSON is rewritten wholesale on every PUT / reorder / renewal, breaking links whenever a row uuid changes. So every display spot
(DocRelationBlock / DocWhy / retest / describe_generation_inputs) was **recomputed after the fact from the current configuration**
-- `content_generator.describe_generation_inputs` even has a disclaimer written in its code.

Two edges answering two different questions; do not mix them:
  · `doc.gen_batch_id` + `doc.gen_queries_json` -- "why this article was written in the first place".
    **Written only once when empty**; regeneration does not overwrite (regeneration made no term selection, the plan did).
  · `batch.doc_ids_json` -- "which articles this trigger produced", including new + regenerated.
    doc_count alone is not enough: a batch of only regenerations would show doc_count=5 with no article
    whose gen_batch_id points to this batch, a bizarre ledger.

The batch id reuses the `trace_id` already minted by `usage_ctx` (not minted separately): it passes through `spawn_thread`'s contextvar
snapshot into every LLM call of this batch, is already persisted in `llm_calls.trace_id` and indexed --
so "how much this batch cost" comes for free.

Empty actor_id = no human trigger (cron / Siyuan / failure retry). **No fallback to the topic owner**: this is an operations ledger,
and writing "Zhang San triggered a batch at 03:00" while Zhang San was asleep is a forged record (different from the "owner" semantics of gen_user_id,
whose fallback is required by the "Labeling Measures").
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def _trace_id() -> Optional[str]:
    try:
        from geo.services.llm_usage import current_ctx

        ctx = current_ctx()
        return getattr(ctx, "trace_id", None) if ctx else None
    except Exception:  # noqa: BLE001
        return None


def _actor_id() -> Optional[int]:
    """Trigger user; cron / Siyuan / retry have no trigger user -> None (no fallback to the topic owner)."""
    try:
        from geo.services.content_marking import current_origin

        origin = current_origin()
        return getattr(origin, "user_id", None) if origin else None
    except Exception:  # noqa: BLE001
        return None


def open_batch(db, topic_id: int, *, trigger_source: str = "unknown",
               plan_id: Optional[int] = None,
               operation_context_id: Optional[int] = None):
    """Open a batch row (terms and drafts are backfilled at the end). Failure does not affect the main generation flow; returns None."""
    try:
        from geo.models.ai_telemetry import TopicContentGenBatchORM

        row = TopicContentGenBatchORM(
            topic_id=topic_id,
            operation_context_id=operation_context_id,
            trace_id=_trace_id(),
            actor_id=_actor_id(),
            trigger_source=(trigger_source or "unknown")[:24],
            plan_id=plan_id,
            queries_json="[]",
            doc_ids_json="[]",
            doc_count=0,
        )
        db.add(row)
        db.flush()                             # Get the id for doc.gen_batch_id
        return row
    except Exception:  # noqa: BLE001
        log.exception("open_batch failed topic=%s", topic_id)
        db.rollback()
        return None


def stamp_docs(batch, docs) -> None:
    """Stamp the batch id + term snapshot on drafts touched by this batch. **Written only once when empty**.

    Same discipline as content_marking.stamp: regeneration overwrites in place (doc id unchanged),
    and overwriting would rewrite "why this article was written originally" into what the later regeneration looked like.
    doc_queries is passed by the caller via a temporary `_gen_queries` attribute on the doc (a transient attribute, not persisted).
    """
    if batch is None:
        return
    for d in docs or []:
        if d is None:
            continue
        if getattr(d, "gen_batch_id", None) is None:
            d.gen_batch_id = batch.id
        if not getattr(d, "gen_queries_json", None):
            qs = [str(q) for q in (getattr(d, "_gen_queries", None) or []) if str(q).strip()]
            if qs:
                d.gen_queries_json = json.dumps(qs, ensure_ascii=False)


def close_batch(db, batch, docs, *, queries: Optional[list[str]] = None,
                stats: Optional[dict] = None) -> None:
    """Finish: backfill the full set of terms selected this time, the drafts touched, and the article count."""
    if batch is None:
        return
    try:
        if queries is None:                    # If not given explicitly, take the union of each draft's snapshot (order-preserving dedup)
            seen: dict[str, None] = {}
            for d in docs or []:
                for q in (getattr(d, "_gen_queries", None) or []):
                    q = str(q).strip()
                    if q:
                        seen.setdefault(q, None)
            queries = list(seen)
        ids = sorted({int(d.id) for d in (docs or []) if getattr(d, "id", None)})
        batch.queries_json = json.dumps(
            [str(q) for q in queries if str(q).strip()], ensure_ascii=False)
        batch.doc_ids_json = json.dumps(ids)
        batch.doc_count = len(ids)
        batch.finished_at = datetime.utcnow()
        if stats:
            batch.stats_json = json.dumps(stats, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        log.exception("close_batch failed batch=%s", getattr(batch, "id", None))


def query_gen_stats(db, topic_id: int) -> dict[str, dict]:
    """Per-term ledger: term -> {count, last_at, doc_ids}. Expanded and aggregated from each draft's term snapshot, no extra table.

    ★ **No active filter** here -- terms soft-deleted but already published for must still appear:
      a published article was written for that term, and hiding it would make the ledger not add up. The frontend just shows a "deleted" badge.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    out: dict[str, dict] = {}
    rows = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Soft-deleted drafts are not counted
          .filter(TopicGeneratedDocORM.gen_queries_json.isnot(None))
          .all()
    )
    for d in rows:
        try:
            qs = json.loads(d.gen_queries_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        at = d.created_at
        for q in qs:
            q = str(q).strip()
            if not q:
                continue
            cur = out.setdefault(q, {"count": 0, "last_at": None, "doc_ids": []})
            cur["count"] += 1
            cur["doc_ids"].append(int(d.id))
            if at and (cur["last_at"] is None or at > cur["last_at"]):
                cur["last_at"] = at
    return out
