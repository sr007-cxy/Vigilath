"""Intersect ai_telemetry_responses.citations_json with topic_generated_docs.publish_targets_json
and backfill doc.cited_by_json.

Called from the following entry points:
  - CLI:scripts/backfill_doc_cited_by.py
  - HTTP:POST /api/admin/content-review/rebuild-cited-by
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from sqlalchemy import and_, or_

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryResponseORM, TopicGeneratedDocORM,
)
from geo.services.citation_match import (
    extract_publish_urls, match_citations_to_docs,
)

log = logging.getLogger(__name__)


def run(topic_id: int | None = None, *, dry_run: bool = False, replace: bool = False) -> dict:
    """Main entry point.

    Args:
        topic_id: restrict to a topic; None = all docs with publish_targets.
        dry_run: compute only, do not write.
        replace: True = full recompute, discard old cited_by; False = merge, keep old records.

    Returns:
        Stats dict {topics, docs, matched_docs, responses_scanned, hits, dry_run, replace}
    """
    s = SessionLocal()
    try:
        # Include docs whose publish_targets have a URL or that were actually published via Mediumsly (the auto-publish path historically had empty
        # targets url; mediumsly_url is the fallback attribution source).
        doc_q = s.query(TopicGeneratedDocORM).filter(
            or_(
                and_(
                    TopicGeneratedDocORM.publish_targets_json.isnot(None),
                    TopicGeneratedDocORM.publish_targets_json != "",
                    TopicGeneratedDocORM.publish_targets_json != "[]",
                ),
                and_(
                    TopicGeneratedDocORM.mediumsly_url.isnot(None),
                    TopicGeneratedDocORM.mediumsly_url != "",
                ),
            )
        )
        if topic_id is not None:
            doc_q = doc_q.filter(TopicGeneratedDocORM.topic_id == topic_id)
        docs_by_topic: dict[int, list[TopicGeneratedDocORM]] = defaultdict(list)
        for d in doc_q.all():
            docs_by_topic[d.topic_id].append(d)

        if not docs_by_topic:
            return {"topics": 0, "docs": 0, "matched_docs": 0,
                    "responses_scanned": 0, "hits": 0,
                    "dry_run": dry_run, "replace": replace}

        total_docs = 0
        matched_docs = 0
        total_responses_scanned = 0
        total_hits = 0

        for tid, docs in docs_by_topic.items():
            doc_urls: dict[int, list[str]] = {}
            for d in docs:
                urls = extract_publish_urls(d.publish_targets_json)
                mu = (d.mediumsly_url or "").strip()
                if mu and mu not in urls:
                    urls.append(mu)
                doc_urls[d.id] = urls
            doc_urls = {k: v for k, v in doc_urls.items() if v}
            if not doc_urls:
                continue
            total_docs += len(doc_urls)

            buckets: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
            if not replace:
                for d in docs:
                    if d.id not in doc_urls:
                        continue
                    try:
                        existing = json.loads(d.cited_by_json or "{}")
                    except Exception:  # noqa: BLE001
                        existing = {}
                    if not isinstance(existing, dict):
                        continue
                    for eng, rids in existing.items():
                        if not isinstance(rids, list):
                            continue
                        for r in rids:
                            try:
                                buckets[d.id][eng].add(int(r))
                            except (TypeError, ValueError):
                                pass

            q = s.query(AiTelemetryResponseORM).filter(
                AiTelemetryResponseORM.topic_id == tid
            )
            for r in q.yield_per(500):
                total_responses_scanned += 1
                try:
                    cits = json.loads(r.citations_json or "[]")
                except Exception:  # noqa: BLE001
                    continue
                hits = match_citations_to_docs(cits, doc_urls)
                if not hits:
                    continue
                for doc_id in hits.keys():
                    buckets[doc_id][r.engine].add(r.id)
                    total_hits += 1

            for d in docs:
                if d.id not in doc_urls:
                    continue
                new_payload = {
                    eng: sorted(rids) for eng, rids in buckets.get(d.id, {}).items() if rids
                }
                new_json = json.dumps(new_payload, ensure_ascii=False) if new_payload else "{}"
                if new_payload:
                    matched_docs += 1
                if (d.cited_by_json or "{}") != new_json:
                    if not dry_run:
                        # Engines that **newly appeared** this time -- only push for new ones, otherwise every backfill round would
                        # re-push the same batch of old hits
                        try:
                            was = set(json.loads(d.cited_by_json or "{}"))
                        except Exception:  # noqa: BLE001
                            was = set()
                        fresh = [e for e in new_payload if e not in was]
                        d.cited_by_json = new_json
                        if fresh:
                            _notify_cited(s, d, tid, fresh)

        if not dry_run:
            s.commit()

        stats = {
            "topics": len(docs_by_topic),
            "docs": total_docs,
            "matched_docs": matched_docs,
            "responses_scanned": total_responses_scanned,
            "hits": total_hits,
            "dry_run": dry_run,
            "replace": replace,
        }
        log.info("backfill cited_by done %s", stats)
        return stats
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _notify_cited(s, doc, topic_id: int, engines: list[str]) -> None:
    """Citation hit -> proactively push a notification.

    **This is the product's only aha moment**, and the only thing that justifies renewal: users will not come back on their own to ask
    "was my article from three weeks ago cited?". Without the push, this moment never happens.

    A failure to persist the notification never affects the backfill -- notify.push swallows its own exceptions, and we wrap it once more here.
    """
    try:
        from agents.geo import notify
        from geo.models.ai_telemetry import AiTelemetryTopicORM

        topic = s.get(AiTelemetryTopicORM, topic_id)
        if topic is not None:
            notify.cited_by_engine(s, doc, topic, engines)
    except Exception:  # noqa: BLE001
        log.debug("citation notification failed doc=%s", doc.id, exc_info=True)
