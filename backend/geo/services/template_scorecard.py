"""Content template scorecard -- aggregates drafts' real outcomes into a score table per template_id.

Background: `content_templates` only stores static fields like prompt / length / locked; whether a template's drafts
were actually cited by AI engines, bounced by the compliance gate, or passed platform review is never recorded. Ops chose
templates by gut feeling; good and bad templates were treated the same.

All data comes from existing fields; no new tables:

  - `topic_generated_docs.template_id`  which template produced the draft (written at generation)
  - `cited_by_json`                     which engines cited it ({} = not cited yet) -- **primary metric**
  - `status` / `reject_reason`          compliance bounce / manual rejection / approved / published
  - `platform_review_status`            platform review result backfill
  - `quality_score`                     review score from multi-agent refinement (2026-08-04) -- a **leading** reference column,
                                        not counted in the composite score: citation rate needs 7 days to settle, quality score is available the same day

Scoring metric definition (GEO's end goal is being cited, so citation rate dominates):

    citation rate 60 · compliance pass rate 20 · manual approval rate 10 · platform review pass rate 10

When a dimension has no data (e.g. not a single platform review result backfilled), its weight is redistributed proportionally to the others;
"no data" isn't treated as a penalty. Published but too-recent drafts don't enter the citation rate denominator -- citations take time to settle;
new drafts within `MATURE_DAYS` days count toward output only, not effect.

**The citation rate denominator only counts drafts that "can be judged"**: `cited_by` is backfilled by `backfill_doc_cited_by`
matching placement URLs against engine citations; drafts never backfilled with a real link (auto-publish often only marks the
platform and `url` is an empty string) can never match, and putting them in the denominator systematically wrongs the template -- so
only mature drafts with a real placement link (`publish_targets[].url` or `mediumsly_url`) count;
when judgeable samples are fewer than `MIN_SAMPLE`, that dimension is "no data", its weight goes to the others, and
`untracked_published` reports how many couldn't be judged due to missing links.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import ContentTemplateORM, TopicGeneratedDocORM

log = logging.getLogger(__name__)

MATURE_DAYS = 7         # days after publishing before counting toward the citation rate denominator
MIN_SAMPLE = 3          # no score with fewer mature drafts than this

# Weights (sum to 100); renormalized proportionally when a dimension has no data
WEIGHTS = {
    "cited": 60,        # citation rate -- primary
    "compliance": 20,   # share not bounced by the compliance gate
    "review": 10,       # manual approval rate (approved + published)
    "platform": 10,     # platform review pass rate
}

_COMPLIANCE_PREFIXES = ("[合规扫描自动退回]", "[合规复扫仍未通过]")


def _publish_meta(doc: TopicGeneratedDocORM) -> tuple[Optional[datetime], bool]:
    """(time sent out, whether there's a real placement link).

    Time: prefer the placement marked_at, falling back to review decision time / creation time.
    Link: one real value in `publish_targets[].url` or `mediumsly_url` is enough -- drafts without a link
    can never match citation backfill and can't count toward the citation rate denominator.
    """
    try:
        targets = json.loads(doc.publish_targets_json or "[]")
    except Exception:  # noqa: BLE001
        targets = []
    stamps: list[datetime] = []
    has_url = bool((doc.mediumsly_url or "").strip())
    for t in targets if isinstance(targets, list) else []:
        t = t or {}
        if str(t.get("url") or "").strip():
            has_url = True
        raw = str(t.get("marked_at") or "")[:19]
        if not raw:
            continue
        try:
            stamps.append(datetime.fromisoformat(raw))
        except ValueError:
            continue
    at = max(stamps) if stamps else (doc.review_decision_at or doc.created_at)
    return at, has_url


def _is_compliance_reject(doc: TopicGeneratedDocORM) -> bool:
    reason = (doc.reject_reason or "").lstrip()
    return reason.startswith(_COMPLIANCE_PREFIXES)


def _blank_row() -> dict:
    return {
        "docs": 0, "rejected_compliance": 0, "rejected_manual": 0,
        "approved": 0, "published": 0, "mature_published": 0,
        # trackable = mature with a real placement link, so cited-or-not can be judged; untracked = missing link
        "trackable_published": 0, "untracked_published": 0,
        "cited_docs": 0, "cited_engines": {},
        "platform_passed": 0, "platform_rejected": 0,
        # 2026-08-04 -- review score from the refinement pipeline (leading metric): citation rate needs 7 days to settle,
        # quality score is available the day the draft is produced, so new templates immediately show "is the writing any good". Not in the composite score, reference column only.
        "quality_sum": 0, "quality_docs": 0,
        "first_used_at": None, "last_used_at": None,
    }


def _score(row: dict) -> tuple[Optional[int], dict]:
    """Compute the weighted composite score; dimensions with no data aren't penalized, their weight goes to the others.

    Returns (score | None, per-dimension percentage details). Fewer than MIN_SAMPLE mature drafts -> None.
    """
    dims: dict[str, Optional[float]] = {}
    # Too few judgeable samples counts as "no data", not 0 -- missing placement links isn't the template's fault
    dims["cited"] = (row["cited_docs"] / row["trackable_published"]
                     if row["trackable_published"] >= MIN_SAMPLE else None)
    dims["compliance"] = (1 - row["rejected_compliance"] / row["docs"]) if row["docs"] else None
    # The manual approval rate denominator only counts drafts "with a verdict": ones still in draft / pending review aren't failures,
    # otherwise templates that just started producing drafts get dragged down by in-flight drafts
    decided = row["approved"] + row["published"] + row["rejected_manual"]
    dims["review"] = ((row["approved"] + row["published"]) / decided) if decided else None
    platform_total = row["platform_passed"] + row["platform_rejected"]
    dims["platform"] = (row["platform_passed"] / platform_total) if platform_total else None

    detail = {k: (round(v * 100, 1) if v is not None else None) for k, v in dims.items()}
    if row["docs"] < MIN_SAMPLE:
        return None, detail          # hardly any drafts produced; scoring is meaningless

    usable = {k: v for k, v in dims.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in usable)
    if not total_w:
        return None, detail
    score = sum(WEIGHTS[k] * v for k, v in usable.items()) / total_w * 100
    return round(score), detail


def build(db: Session, *, topic_id: Optional[int] = None,
          days: Optional[int] = None) -> list[dict]:
    """Compute each template's scorecard. Sorted by score descending; those without a score (insufficient samples) last.

    If topic_id is given only that topic's drafts are counted; if days is given only drafts generated within those days.
    """
    q = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.template_id.isnot(None)))
    if topic_id is not None:
        q = q.filter(TopicGeneratedDocORM.topic_id == topic_id)
    if days:
        q = q.filter(TopicGeneratedDocORM.created_at >= datetime.utcnow() - timedelta(days=days))

    now = datetime.utcnow()
    mature_before = now - timedelta(days=MATURE_DAYS)
    agg: dict[int, dict] = {}

    for d in q.yield_per(500):
        row = agg.setdefault(d.template_id, _blank_row())
        row["docs"] += 1
        created = d.created_at or now
        if row["first_used_at"] is None or created < row["first_used_at"]:
            row["first_used_at"] = created
        if row["last_used_at"] is None or created > row["last_used_at"]:
            row["last_used_at"] = created

        qs = getattr(d, "quality_score", None)
        if isinstance(qs, int):
            row["quality_sum"] += qs
            row["quality_docs"] += 1

        if d.status == "rejected":
            if _is_compliance_reject(d):
                row["rejected_compliance"] += 1
            else:
                row["rejected_manual"] += 1
        elif d.status == "approved":
            row["approved"] += 1
        elif d.status == "published":
            row["published"] += 1
            if d.platform_review_status == "passed":
                row["platform_passed"] += 1
            elif d.platform_review_status == "rejected":
                row["platform_rejected"] += 1
            pub_at, has_url = _publish_meta(d)
            if pub_at and pub_at <= mature_before:
                row["mature_published"] += 1
                if not has_url:
                    row["untracked_published"] += 1     # no link, citation can't be judged
                    continue
                row["trackable_published"] += 1
                try:
                    cited = json.loads(d.cited_by_json or "{}")
                except Exception:  # noqa: BLE001
                    cited = {}
                if isinstance(cited, dict) and cited:
                    row["cited_docs"] += 1
                    for eng, rids in cited.items():
                        if rids:
                            row["cited_engines"][eng] = row["cited_engines"].get(eng, 0) + 1

    if not agg:
        return []

    tmpls = {t.id: t for t in db.query(ContentTemplateORM)
                                .filter(ContentTemplateORM.id.in_(list(agg))).all()}
    out: list[dict] = []
    for tid, row in agg.items():
        t = tmpls.get(tid)
        score, detail = _score(row)
        out.append({
            "template_id": tid,
            "name": (t.name if t else "") or f"#{tid}",
            "kind": t.kind if t else "",
            "scope": t.scope if t else "",
            "missing": t is None,          # template deleted; stats kept as history
            "score": score,
            "sample_enough": score is not None,
            # Whether the score includes the primary "cited" dimension -- when it doesn't the frontend must say it's "output quality only"
            "cited_measurable": detail["cited"] is not None,
            "rates": detail,               # {cited, compliance, review, platform} percentages, None = no data
            "docs": row["docs"],
            "published": row["published"],
            "mature_published": row["mature_published"],
            "trackable_published": row["trackable_published"],
            "untracked_published": row["untracked_published"],
            "cited_docs": row["cited_docs"],
            "cited_engines": row["cited_engines"],
            # Mean refinement review score (None = none of these drafts were reviewed); not counted in score, leading reference only
            "quality_avg": (round(row["quality_sum"] / row["quality_docs"])
                            if row["quality_docs"] else None),
            "quality_docs": row["quality_docs"],
            "rejected_compliance": row["rejected_compliance"],
            "rejected_manual": row["rejected_manual"],
            "approved": row["approved"],
            "platform_passed": row["platform_passed"],
            "platform_rejected": row["platform_rejected"],
            "first_used_at": row["first_used_at"].isoformat() if row["first_used_at"] else None,
            "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
        })
    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), -r["docs"]))
    return out
