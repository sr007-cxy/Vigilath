"""Back-link index of publish plan rows <-> drafts.

The draft table only stores plan_item_id (uuid), and humans cannot tell which plan row it is; the plan row's sequence number and
publish date live in the execution_plan.publishing_plan_json snapshot. To show
"plan #12 · Aug 3" in the article list the two sides must be joined -- this provides a topic-level index.

Usage (build the index once in the list API, then inject row by row):
    idx = plan_item_index(db, topic_id)
    GeneratedDocOut.from_orm_row(r, plan_row=idx.get(r.plan_item_id or ""))
"""
from __future__ import annotations

import json
import logging
import os

from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM

log = logging.getLogger(__name__)

# When a plan row has no prompts, take a few from the seed as fallback (the generation and display sides share the same cap)
_FALLBACK_COVER_QUERIES = int(os.environ.get("GEO_CONTENT_FALLBACK_COVER_QUERIES", "3"))


def seed_monitored_queries(topic) -> dict[str, list[str]]:
    """Seed prompt -> the list of queries under that seed checked for monitoring (taken from topic.queries_json on the fly).

    Plan rows should carry queries, but old plan rows / seeds without enough queries to distribute will have them empty. Generation uses this as fallback
    (content_generator), and display uses the same fallback; the two metric definitions must match, otherwise the "prompts" shown on the detail page
    would not match what the body actually covers.
    """
    out: dict[str, list[str]] = {}
    try:
        items = json.loads(getattr(topic, "queries_json", None) or "[]")
    except Exception:  # noqa: BLE001
        return out
    rows = [
        it for it in items
        if isinstance(it, dict)
        and str(it.get("text") or "").strip()
        and str(it.get("seed") or "").strip()
        and it.get("status") in (None, "", "approved")
        and not it.get("deleted_at")            # Soft-deleted terms no longer fall back into publishing
    ]
    # Same metric definition as vscl_rl._monitored_queries: if there are selection marks, only selected ones count;
    # when the whole topic has no selected field (true of most old topics in production), fall back to all approved --
    # otherwise this index would always be empty on real data, and the fallback might as well not exist.
    picked = [it for it in rows if it.get("selected")] or rows
    for it in picked:
        out.setdefault(str(it["seed"]).strip(), []).append(str(it["text"]).strip())
    return out


def doc_out(db, d):
    """Single draft -> output: fill in template name/kind and the publish plan row (seed prompt / prompts).

    List APIs use plan_item_index + bulk template queries, not this (N queries).
    Responses of single-draft GET / PATCH / review actions go through here, so related info is not lost after refreshing the detail page.
    """
    from geo.models.ai_telemetry import ContentTemplateORM, GeneratedDocOut

    tpl_name = tpl_kind = None
    tid = getattr(d, "template_id", None)
    if tid:
        row = (
            db.query(ContentTemplateORM.name, ContentTemplateORM.kind)
              .filter(ContentTemplateORM.id == tid).first()
        )
        if row:
            tpl_name, tpl_kind = row[0], row[1]
    plan_row = plan_item_index(db, d.topic_id).get(getattr(d, "plan_item_id", None) or "")
    return GeneratedDocOut.from_orm_row(
        d, template_name=tpl_name, template_kind=tpl_kind, plan_row=plan_row)


def plan_item_index(db, topic_id: int) -> dict[str, dict]:
    """plan_item_id -> {seq (1-based), publish_date, plan_id, seed, queries, platform}.

    2026-08-05 -- besides seq/date, also carries which seed prompt this row chose, which prompts to cover,
    and which platform to place on: the article detail must show "which prompts this draft is linked to", which only exists in the plan snapshot.

    All historical plans of the same topic are included: drafts may hang off rows of old plans, and looking only at the latest plan would miss them.
    Row ids are uuids and never collide across plans.
    """
    out: dict[str, dict] = {}
    try:
        plans = (
            db.query(AiTelemetryTopicExecutionPlanORM)
              .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
              .order_by(AiTelemetryTopicExecutionPlanORM.id.asc())
              .all()
        )
    except Exception:  # noqa: BLE001
        log.exception("plan_item_index: load plans failed for topic %s", topic_id)
        return out
    for p in plans:
        try:
            items = json.loads(p.publishing_plan_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            iid = str(it.get("id") or "").strip()
            if not iid:
                continue
            # Covered prompts: new rows use multi-select queries, legacy rows only have a single query
            queries = [str(x).strip() for x in (it.get("queries") or []) if str(x).strip()]
            single_q = str(it.get("query") or "").strip()
            if not queries and single_q:
                queries = [single_q]
            # Backend seq is 0-based; display uniformly adds 1 (aligned with #N in the plan table)
            out[iid] = {
                "seq": int(it.get("seq") or 0) + 1,
                "publish_date": str(it.get("publish_date") or ""),
                "plan_id": p.id,
                "seed": str(it.get("seed") or "").strip(),
                "queries": queries,
                "platform": str(it.get("platform") or "").strip(),
            }
    # Note: rows with empty queries are **not** filled with seed fallback here -- the generation side falls back, but that happens after regeneration;
    # display must truthfully reflect what this draft was asked to cover when written; filling it in would invent a link that never happened for old drafts.
    return out
