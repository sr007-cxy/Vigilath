"""Flywheel P1-C -- targeted post-placement re-test: register probe + baseline snapshot.

Called schedule_for_doc() on successful publish (auto publish_tick / admin manual publish):
  - queries: the queries group of the doc's linked plan_item, fallback source_query_text
  - engines: topic's monitored engines
  - baseline: snapshot of exposure/TOP3/own-citation metrics for these (query, engine) pairs within
    GEO_RETEST_BASELINE_DAYS days before publishing
  - scheduled_for = now + GEO_RETEST_DELAY_DAYS

Execution when due and result collection live in telemetry-service (app/retests.py): dispatch inserts with
priority=0, automatically counted against the engine daily cap; after the window closes, post metrics are computed with the same definition into result_json.

ENV:
    GEO_RETEST_ENABLED         default 1; 0 = don't register probes on publish (emergency switch)
    GEO_RETEST_DELAY_DAYS      days after publishing to re-test, default 7
    GEO_RETEST_BASELINE_DAYS   baseline window in days before publishing, default 7
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from geo.models.ai_telemetry import (
    AiTelemetryResponseORM, AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicORM,
    GeoRetestProbeORM, TopicGeneratedDocORM,
)
from geo.services.citation_match import canonicalize, extract_publish_urls

log = logging.getLogger(__name__)

RETEST_ENABLED = os.environ.get("GEO_RETEST_ENABLED", "1") == "1"
RETEST_DELAY_DAYS = int(os.environ.get("GEO_RETEST_DELAY_DAYS", "7"))
RETEST_BASELINE_DAYS = int(os.environ.get("GEO_RETEST_BASELINE_DAYS", "7"))


def compute_metrics(rows, doc_urls: list[str]) -> dict:
    """Compute metrics for a set of responses using the re-test definition. Same definition as telemetry-service app/retests.py
    _compute_metrics (maintained from the same source on both sides; change both together)."""
    canon_owned = {c for c in (canonicalize(u) for u in doc_urls) if c}
    total = 0
    hit = 0
    top3 = 0
    cited_own = 0
    for r in rows:
        if r.error:
            continue
        total += 1
        if r.hit:
            hit += 1
            if isinstance(r.brand_rank, int) and 1 <= r.brand_rank <= 3:
                top3 += 1
        if canon_owned:
            try:
                cits = json.loads(r.citations_json or "[]")
            except Exception:  # noqa: BLE001
                cits = []
            for c in cits:
                if isinstance(c, dict) and canonicalize(c.get("url") or "") in canon_owned:
                    cited_own += 1
                    break

    def pct(n: int) -> float:
        return round(n / total * 100, 1) if total else 0.0

    return {
        "responses": total,
        "hit_pct": pct(hit),
        "top3_pct": pct(top3),
        "cited_own_pct": pct(cited_own),
    }


def _doc_queries(db, doc: TopicGeneratedDocORM) -> list[str]:
    """The queries group of the doc's linked plan_item; fallback source_query_text."""
    if doc.plan_item_id and doc.execution_plan_id:
        plan = db.get(AiTelemetryTopicExecutionPlanORM, doc.execution_plan_id)
        if plan is not None:
            try:
                items = json.loads(plan.publishing_plan_json or "[]")
            except Exception:  # noqa: BLE001
                items = []
            for it in items:
                if isinstance(it, dict) and str(it.get("id") or "") == str(doc.plan_item_id):
                    qs = [str(q).strip() for q in (it.get("queries") or []) if str(q).strip()]
                    if not qs and it.get("query"):
                        qs = [str(it["query"]).strip()]
                    if qs:
                        return qs
                    break
    q = (doc.source_query_text or "").strip()
    return [q] if q else []


def schedule_for_doc(db, doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM) -> GeoRetestProbeORM | None:
    """Register a re-test probe after successful publish. Skips if the doc already has an unfinished probe. Does not commit (follows caller's transaction)."""
    if not RETEST_ENABLED:
        return None
    existing = (
        db.query(GeoRetestProbeORM)
          .filter(GeoRetestProbeORM.doc_id == doc.id)
          .filter(GeoRetestProbeORM.status.in_(("scheduled", "enqueued")))
          .first()
    )
    if existing is not None:
        return existing

    # _doc_queries is the historical-tracing definition (still returns soft-deleted words, the detail page shows them); but a re-test probe is a **forward-looking**
    # real batch run, and soft-deleted words shouldn't spend engine budget anymore
    from geo.models.ai_telemetry import deleted_query_texts
    _gone = deleted_query_texts(topic)
    queries = [q for q in _doc_queries(db, doc) if q not in _gone]
    try:
        engines = [str(e) for e in json.loads(topic.engines_json or "[]") if e]
    except Exception:  # noqa: BLE001
        engines = []
    if not queries or not engines:
        log.info("[retest] doc=%d skipped (queries=%d engines=%d)",
                 doc.id, len(queries), len(engines))
        return None

    doc_urls = extract_publish_urls(doc.publish_targets_json)
    if doc.mediumsly_url and doc.mediumsly_url not in doc_urls:
        doc_urls.append(doc.mediumsly_url)

    now = datetime.utcnow()
    base_rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic.id)
          .filter(AiTelemetryResponseORM.query.in_(queries))
          .filter(AiTelemetryResponseORM.engine.in_(engines))
          .filter(AiTelemetryResponseORM.created_at >= now - timedelta(days=RETEST_BASELINE_DAYS))
          .all()
    )
    baseline = compute_metrics(base_rows, doc_urls)
    baseline["window_days"] = RETEST_BASELINE_DAYS

    probe = GeoRetestProbeORM(
        topic_id=topic.id, doc_id=doc.id,
        queries_json=json.dumps(queries, ensure_ascii=False),
        engines_json=json.dumps(engines, ensure_ascii=False),
        doc_urls_json=json.dumps(doc_urls, ensure_ascii=False),
        baseline_json=json.dumps(baseline, ensure_ascii=False),
        scheduled_for=now + timedelta(days=RETEST_DELAY_DAYS),
        status="scheduled",
    )
    db.add(probe)
    db.flush()                       # get probe.id for the idempotency key

    # ⚠️ **No charge** here. Registration only books an appointment N days out, and it often never happens:
    # when due, if the telemetry side finds the topic disabled / words or engines empty, it marks the probe cancelled
    # (that `topic disabled or empty queries/engines` part of app/retests.py).
    # We used to charge the service fee right at registration, so the user backfilled a link -> immediately charged a "citation re-test" ->
    # seven days later that re-test was cancelled -> money spent, re-test never happened.
    #
    # Now post-paid: charged only after the probe is really enqueued (status=enqueued), settled by
    # `retest_billing.settle_probes`; the idempotency key is still `retest:{probe.id}`
    # (same key as the historical charge -- already charged ones won't be charged again).
    log.info("[retest] doc=%d probe scheduled for %s (%d queries × %d engines);"
             " service fee settled after enqueue",
             doc.id, probe.scheduled_for.date(), len(queries), len(engines))
    return probe


def probe_fee_cents(db) -> int:
    """Service fee (credits) for one re-test probe. Publish/backfill flows must **tell the user this number upfront**."""
    from geo.models.balance import PRICE_RETEST_PROBE
    from geo.services import topic_billing

    try:
        return int(topic_billing.price(db, PRICE_RETEST_PROBE))
    except Exception:  # noqa: BLE001
        return 0
