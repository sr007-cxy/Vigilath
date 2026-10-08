"""Placement attribution aggregation -- historical citation effectiveness by (platform, media), for publish center recommendations/badges.

Metric definition (doc-level approximation):
  - Data source: drafts under the topic with non-empty publish_targets_json; any non-empty engine list in cited_by_json
    counts as "this draft cited". cited_by is stored per draft (written when any target URL is cited),
    so a multi-platform placement draft records "cited" on **all** its targets -- optimistic.
    Precise URL-level attribution needs url_canonical comparison, left for a later iteration.
  - Cost: topic_doc_publish_tasks (status='published') joined by doc_id,
    sum(cost_cents) recorded to the corresponding (platform, media_name) group; only paid channel tasks have values.
Pure read-time aggregation, not persisted.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import TopicDocPublishTaskORM, TopicGeneratedDocORM

log = logging.getLogger(__name__)


def _safe_json(raw: Optional[str], default: Any) -> Any:
    try:
        out = json.loads(raw) if raw else default
    except ValueError:
        return default
    return out if isinstance(out, type(default)) else default


def compute_publish_attribution(db: Session, topic_id: int) -> dict:
    """Returns {rows: [(platform, media) details], platform_rollup: [platform-level rollup]}.

    Row structure: {platform, media, published_docs, cited_docs, cited_rate,
             engines, total_cost_cents, last_published_at}
    Sorting: cited_docs descending, then published_docs descending.
    """
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(
              TopicGeneratedDocORM.topic_id == topic_id,
              TopicGeneratedDocORM.publish_targets_json.isnot(None),
              TopicGeneratedDocORM.publish_targets_json != "[]",
          ).all()
    )

    groups: dict[tuple[str, str], dict] = {}

    def _bucket(platform: str, media: str) -> dict:
        key = (platform, media)
        if key not in groups:
            groups[key] = {
                "platform": platform, "media": media,
                "published_docs": 0, "cited_docs": 0,
                "engines": set(), "total_cost_cents": None,
                "last_published_at": None,
            }
        return groups[key]

    for d in docs:
        targets = _safe_json(d.publish_targets_json, [])
        if not targets:
            continue
        cited_by = _safe_json(d.cited_by_json, {})
        cited = any(v for v in cited_by.values() if v)
        engines = sorted(k for k, v in cited_by.items() if v)
        # Same draft and same (platform, media) counted only once
        seen_keys: set[tuple[str, str]] = set()
        for tg in targets:
            if not isinstance(tg, dict):
                continue
            platform = str(tg.get("platform") or "").strip()
            if not platform:
                continue
            media = str(tg.get("media") or "").strip()
            key = (platform, media)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            g = _bucket(platform, media)
            g["published_docs"] += 1
            if cited:
                g["cited_docs"] += 1
                g["engines"].update(engines)
            marked = str(tg.get("marked_at") or "")
            if marked and (g["last_published_at"] is None or marked > g["last_published_at"]):
                g["last_published_at"] = marked

    # Cost: published tasks summed by (platform, media_name)
    tasks = (
        db.query(TopicDocPublishTaskORM)
          .filter(
              TopicDocPublishTaskORM.topic_id == topic_id,
              TopicDocPublishTaskORM.status == "published",
              TopicDocPublishTaskORM.cost_cents.isnot(None),
          ).all()
    )
    for t in tasks:
        g = _bucket((t.platform or "").strip(), (t.media_name or "").strip())
        g["total_cost_cents"] = (g["total_cost_cents"] or 0) + int(t.cost_cents or 0)

    def _row(g: dict) -> dict:
        pub = g["published_docs"]
        cited = g["cited_docs"]
        return {
            "platform": g["platform"], "media": g["media"],
            "published_docs": pub, "cited_docs": cited,
            "cited_rate": round(cited / pub * 100, 1) if pub else 0.0,
            "engines": sorted(g["engines"]),
            "total_cost_cents": g["total_cost_cents"],
            "last_published_at": g["last_published_at"],
        }

    rows = sorted((_row(g) for g in groups.values()),
                  key=lambda r: (-r["cited_docs"], -r["published_docs"]))

    # Platform-level rollup (media dimension merged; published/cited counted per draft within the platform -- the dedup metric definition
    # is ensured by seen_keys so the same draft in the same group counts once, but summing across media here would double-count the same draft on multiple
    # media -- to be conservative, re-aggregate docs by (platform))
    p_groups: dict[str, dict] = {}
    for d in docs:
        targets = _safe_json(d.publish_targets_json, [])
        cited_by = _safe_json(d.cited_by_json, {})
        cited = any(v for v in cited_by.values() if v)
        engines = sorted(k for k, v in cited_by.items() if v)
        seen_p: set[str] = set()
        for tg in targets:
            if not isinstance(tg, dict):
                continue
            platform = str(tg.get("platform") or "").strip()
            if not platform or platform in seen_p:
                continue
            seen_p.add(platform)
            g = p_groups.setdefault(platform, {
                "platform": platform, "media": "",
                "published_docs": 0, "cited_docs": 0,
                "engines": set(), "total_cost_cents": None,
                "last_published_at": None,
            })
            g["published_docs"] += 1
            if cited:
                g["cited_docs"] += 1
                g["engines"].update(engines)
            marked = str(tg.get("marked_at") or "")
            if marked and (g["last_published_at"] is None or marked > g["last_published_at"]):
                g["last_published_at"] = marked
    for t in tasks:
        platform = (t.platform or "").strip()
        if platform in p_groups:
            g = p_groups[platform]
            g["total_cost_cents"] = (g["total_cost_cents"] or 0) + int(t.cost_cents or 0)

    platform_rollup = sorted((_row(g) for g in p_groups.values()),
                             key=lambda r: (-r["cited_docs"], -r["published_docs"]))
    return {"rows": rows, "platform_rollup": platform_rollup}
