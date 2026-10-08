"""Phase D.1 — user-side content API.

Merges capabilities previously split across the frontend /dashboard/compose and /dashboard/posts screens:

  - GET    /api/content/topics/{topic_id}/docs?status=&source=  — all drafts of the current user under a topic
  - GET    /api/content/topics/{topic_id}/stats                  — aggregation for 5 stat cards
  - POST   /api/content/topics/{topic_id}/docs                   — user submits an article they wrote
  - PATCH  /api/content/docs/{doc_id}                            — user edits their own draft / rejected draft
  - POST   /api/content/docs/{doc_id}/submit-review              — push draft to pending_review
  - PATCH  /api/content/topics/{topic_id}/auto-generate          — set the AI auto-generation schedule
  - POST   /api/content/topics/{topic_id}/run-now                — run a batch now with the current config
  - GET    /api/content/docs/{doc_id}                            — single item details

Permissions: all endpoints require owner = current_user (topic.user_id == current_user.id).
Admin cross-user access is provided by /api/admin/content-review/*, not here.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import get_current_user, is_staff
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, AutoGenerateConfigPayload,
    GeneratedDocOut, TopicGeneratedDocORM,
    UserDocSubmitPayload, UserDocUpdatePayload,
)
from geo.models.user import UserORM
from geo.services import content_marking
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    apply_enterprise_scope,
    require_resource_access,
    resource_not_found,
    create_resource_operation_context,
)
from geo.services.system_audit import append_event as append_system_audit
from geo.services.content_generator import schedule_generation
from geo.services.content_error_public import public_generation_error
from geo.services.plan_link import doc_out

log = logging.getLogger(__name__)

router = APIRouter(prefix="/content")


_HH_MM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _own_topic_or_404(
    db: Session, topic_id: int, user_id: int, *, allow_admin_user=None,
    action: str = ACTION_WRITE,
) -> AiTelemetryTopicORM:
    """Topic reads and writes all go through enterprise authorization."""
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t:
        raise resource_not_found()
    actor = allow_admin_user or db.get(UserORM, user_id)
    if actor is None:
        raise resource_not_found()
    require_resource_access(db, actor, t, action)
    return t


def _own_doc_or_404(
    db: Session, doc_id: int, user_id: int, *, allow_admin_user=None,
    action: str = ACTION_WRITE,
) -> TopicGeneratedDocORM:
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise resource_not_found()
    actor = allow_admin_user or db.get(UserORM, user_id)
    if actor is None:
        raise resource_not_found()
    require_resource_access(db, actor, d, action)
    return d


def _require_complete_doc(d: TopicGeneratedDocORM) -> None:
    """A failed placeholder is not reviewable even if its storage status says pending."""
    incomplete = (
        (d.generation_error or "").strip()
        or not (d.title or "").strip()
        or not (d.body_markdown or "").strip()
    )
    if incomplete:
        raise HTTPException(409, {
            "code": "GENERATION_INCOMPLETE",
            "message": "文章尚未生成完成，请重新生成后再审核",
        })


def _public_doc(result: GeneratedDocOut) -> GeneratedDocOut:
    """Redact provider/HTTP/token details from the customer-facing content API."""
    result.generation_error = public_generation_error(result.generation_error)
    return result


# ─────────────────────── List / details ──────────────────────────

class ContentStats(BaseModel):
    draft: int
    pending_review: int
    approved: int
    rejected: int
    published: int
    total: int
    ai_count: int
    user_count: int
    # Drafts whose generation failed. In the DB they also have status='pending_review'
    # (content_generator deliberately moves rows out of 'generating' on failure, otherwise they would be stuck "generating" forever),
    # but counting them as "pending review" is misleading — no body was generated, there is nothing to review.
    # Count them separately via generation_error and subtract from pending_review, same definition as the frontend badge.
    gen_failed: int = 0


@router.get("/topics/{topic_id}/docs", response_model=list[GeneratedDocOut])
def list_my_docs(
    topic_id: int,
    status: Optional[str] = None,
    source: Optional[str] = None,
    query_hit: Optional[str] = None,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Shared endpoint for the placement report page / Content list.

    - status: single status / `to_review` / None = all
    - source: ai / user
    - query_hit: `hit` = the AI really cited the doc's placement URL (see cited_by_json);
                 `miss` = has a placement URL but not cited yet; None = no filter
    """
    _own_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    q = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.topic_id == topic_id)
           .filter(TopicGeneratedDocORM.deleted_at.is_(None)))          # Soft-deleted ones are not shown
    if status:
        if status == "to_review":
            q = q.filter(TopicGeneratedDocORM.status.in_(["draft", "pending_review"]))
        else:
            q = q.filter(TopicGeneratedDocORM.status == status)
    if source in ("ai", "user"):
        q = q.filter(TopicGeneratedDocORM.source == source)
    rows = q.order_by(TopicGeneratedDocORM.id.desc()).all()

    if query_hit in ("hit", "miss"):
        def _has_publish_url(r: TopicGeneratedDocORM) -> bool:
            return bool(r.publish_targets_json and r.publish_targets_json not in ("", "[]"))

        def _is_cited(r: TopicGeneratedDocORM) -> bool:
            raw = getattr(r, "cited_by_json", None) or "{}"
            if raw in ("", "{}"):
                return False
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001
                return False
            return isinstance(data, dict) and any(data.values())

        if query_hit == "hit":
            rows = [r for r in rows if _is_cited(r)]
        else:
            rows = [r for r in rows if _has_publish_url(r) and not _is_cited(r)]

    # Template name injection (for frontend "filter by template"): one query for template id→name of this batch of docs
    tpl_ids = {r.template_id for r in rows if getattr(r, "template_id", None)}
    tpl_names: dict[int, str] = {}
    tpl_kinds: dict[int, str] = {}
    if tpl_ids:
        from ..models.ai_telemetry import ContentTemplateORM
        for tid, name, kind in (
            db.query(ContentTemplateORM.id, ContentTemplateORM.name, ContentTemplateORM.kind)
              .filter(ContentTemplateORM.id.in_(tpl_ids)).all()
        ):
            tpl_names[tid] = name
            tpl_kinds[tid] = kind

    # Link back to publishing plan rows (frontend shows "plan #N · date" so drafts match plans)
    from ..services.plan_link import plan_item_index
    plan_idx = plan_item_index(db, topic_id)

    return [
        _public_doc(GeneratedDocOut.from_orm_row(
            r,
            template_name=tpl_names.get(getattr(r, "template_id", None)),
            template_kind=tpl_kinds.get(getattr(r, "template_id", None)),
            plan_row=plan_idx.get(getattr(r, "plan_item_id", None) or ""),
        ))
        for r in rows
    ]


@router.get("/topics/{topic_id}/stats", response_model=ContentStats)
def topic_stats(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    rows = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))             # Soft-deleted ones are excluded from stats
          .all()
    )
    stats = ContentStats(
        draft=0, pending_review=0, approved=0, rejected=0, published=0,
        total=len(rows), ai_count=0, user_count=0,
    )
    for d in rows:
        st = (d.status or "").lower()
        # Only reclassify in states "not yet handled by a person": if the draft was already reviewed / published,
        # go with the human's conclusion; do not drag it back into the failure bucket with a stale error.
        if (d.generation_error or "").strip() and st in ("draft", "pending_review"):
            stats.gen_failed += 1
        elif st == "draft":
            stats.draft += 1
        elif st == "pending_review":
            stats.pending_review += 1
        elif st == "approved":
            stats.approved += 1
        elif st == "rejected":
            stats.rejected += 1
        elif st == "published":
            stats.published += 1
        if (d.source or "ai") == "user":
            stats.user_count += 1
        else:
            stats.ai_count += 1
    return stats


@router.get("/docs/{doc_id}", response_model=GeneratedDocOut)
def get_doc(
    doc_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _own_doc_or_404(
        db, doc_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    return _public_doc(doc_out(db, d))


# ─────────────────────── User submit / edit ──────────────────────

@router.post("/topics/{topic_id}/docs", response_model=GeneratedDocOut)
def submit_user_doc(
    topic_id: int,
    payload: UserDocSubmitPayload,
    request: Request,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _own_topic_or_404(db, topic_id, current_user.id)
    if (t.submission_status or "draft") != "approved":
        raise HTTPException(409, {
            "code": "TOPIC_NOT_APPROVED",
            "message": "主题资料还未审核通过,暂不能投稿",
        })
    d = TopicGeneratedDocORM(
        topic_id=topic_id,
        title=payload.title.strip(),
        body_markdown=payload.body_markdown,
        summary=(payload.summary or "")[:500],
        source_query_text=(payload.source_query_text or "").strip(),
        status="pending_review" if payload.submit_for_review else "draft",
        selected_for_review=bool(payload.submit_for_review),
        llm_model="",
        source="user",
    )
    db.add(d)
    db.commit()
    # User-supplied drafts also enter the provenance ledger (who submitted when from which IP), but are **not given the AI label** —
    # they are not AI-generated, so labeling them would be false. stamp decides internally by source; we just call it.
    with content_marking.gen_origin(
            user_id=current_user.id,
            client_ip=content_marking.client_ip(request)):
        if content_marking.stamp(db, d):
            db.commit()
    db.refresh(d)
    return _public_doc(doc_out(db, d))


@router.patch("/docs/{doc_id}", response_model=GeneratedDocOut)
def update_user_doc(
    doc_id: int,
    payload: UserDocUpdatePayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _own_doc_or_404(db, doc_id, current_user.id)
    if d.source != "user":
        raise HTTPException(403, {
            "code": "AI_DOC_READONLY",
            "message": "AI 生成的稿件不能由用户直接编辑,请联系管理员",
        })
    if d.status not in ("draft", "rejected"):
        raise HTTPException(409, {
            "code": "WRONG_STATUS",
            "message": f"稿件 status={d.status},无法编辑",
        })
    was_rejected = d.status == "rejected"
    if payload.title is not None:
        d.title = payload.title.strip()
    if payload.body_markdown is not None:
        d.body_markdown = payload.body_markdown
    if payload.summary is not None:
        d.summary = payload.summary[:500]
    if payload.source_query_text is not None:
        d.source_query_text = payload.source_query_text.strip()

    # Rejected drafts are rescanned after editing: if they pass, they automatically return to draft and the user can resubmit;
    # if they still hit, they stay rejected with refreshed rejection reasons (rejection is no longer a dead end).
    if was_rejected:
        t = db.get(AiTelemetryTopicORM, d.topic_id)
        if t is not None:
            from geo.services import compliance_scanner
            try:
                compliance_scanner.rescan_and_reopen(
                    db, d, t, actor_type="user", actor_id=current_user.id,
                    actor_name=getattr(current_user, "email", "") or "",
                )
            except Exception:  # noqa: BLE001
                log.exception("compliance rescan failed for doc %s", d.id)
    db.commit()
    db.refresh(d)
    return _public_doc(doc_out(db, d))


@router.post("/docs/{doc_id}/submit-review", response_model=GeneratedDocOut)
def submit_doc_for_review(
    doc_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Push a draft / rejected draft to pending_review.

    Users can resubmit their own rejected drafts; AI drafts can also be submitted by the user (not editable, but the user chooses whether to submit).
    """
    d = _own_doc_or_404(db, doc_id, current_user.id)
    if d.status not in ("draft", "rejected"):
        raise HTTPException(409, {
            "code": "WRONG_STATUS",
            "message": f"稿件 status={d.status},无法提交审核",
        })
    _require_complete_doc(d)

    # Compliance gate hook point 2: scan on submit (user drafts + rescan after edits).
    # ban / missing mandatory labeling → automatically rejected, no manual review; see design doc 5.2.
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    if t is not None:
        from geo.services import compliance_scanner

        row = compliance_scanner.scan_and_apply(
            db, d, t, hook="submit_review",
            actor_type="user", actor_id=current_user.id,
            actor_name=getattr(current_user, "email", "") or "",
        )
        if row.verdict == "returned":
            db.commit()
            raise HTTPException(422, {
                "code": "COMPLIANCE_BLOCKED",
                "message": d.reject_reason or "合规扫描未通过,稿件已退回修改",
            })

    # External soft signals (P2, design doc 5.7): appended after the internal gate passes, only as reference for manual first review
    # + riskWords into the candidate pool; failures degrade without blocking submission.
        try:
            compliance_scanner.external_check_and_feed(db, d, t, row)
        except Exception:  # noqa: BLE001
            log.exception("external moderation failed for doc %s", d.id)

    d.status = "pending_review"
    d.selected_for_review = True
    d.reject_reason = None
    db.commit()
    db.refresh(d)
    return _public_doc(doc_out(db, d))


class RejectPayload(BaseModel):
    reason: str = ""


@router.post("/docs/{doc_id}/approve", response_model=GeneratedDocOut)
def approve_my_doc(
    doc_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User self-approves a draft under their own topic (draft / pending_review → approved). Once approved it can be published."""
    from datetime import datetime

    d = _own_doc_or_404(db, doc_id, current_user.id)
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS", "message": f"稿件 status={d.status},不可通过"})
    _require_complete_doc(d)
    d.status = "approved"
    d.selected_for_review = True
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = current_user.id
    d.reject_reason = None
    db.commit()
    db.refresh(d)
    # Publishing plan presets: automatically create placement tasks from plan rows (platform, media) (failure does not affect the review result)
    from geo.services.publish_preset import apply_preset_on_approve
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    if t is not None:
        apply_preset_on_approve(db, d, t, actor_id=current_user.id)
    return _public_doc(doc_out(db, d))


@router.post("/docs/{doc_id}/reject", response_model=GeneratedDocOut)
def reject_my_doc(
    doc_id: int,
    payload: RejectPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User self-rejects a draft under their own topic (draft / pending_review → rejected, with reason). It can be edited and resubmitted."""
    from datetime import datetime

    d = _own_doc_or_404(db, doc_id, current_user.id)
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS", "message": f"稿件 status={d.status},不可驳回"})
    _require_complete_doc(d)
    d.status = "rejected"
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = current_user.id
    d.reject_reason = (payload.reason or "").strip()[:500]
    db.commit()
    db.refresh(d)
    return _public_doc(doc_out(db, d))


# ─────────────────────── AI auto-generation schedule ──────────────────────

@router.patch("/topics/{topic_id}/auto-generate")
def configure_auto_generate(
    topic_id: int,
    payload: AutoGenerateConfigPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _own_topic_or_404(db, topic_id, current_user.id)
    if not _HH_MM_RE.match(payload.time):
        raise HTTPException(422, "time must be HH:MM 24h format")
    t.auto_generate_enabled = bool(payload.enabled)
    t.auto_generate_time = payload.time
    t.auto_generate_count = max(1, min(20, int(payload.count)))
    if payload.publish_enabled is not None:
        t.auto_publish_enabled = bool(payload.publish_enabled)
    if payload.approve_enabled is not None:
        t.auto_approve_enabled = bool(payload.approve_enabled)
    if payload.publish_paid_enabled is not None:
        t.auto_publish_paid_enabled = bool(payload.publish_paid_enabled)
    if payload.publish_time is not None:
        # Same yardstick as time: bad format → 422; do not silently persist a broken value,
        # which would make all date-only rows of this topic fall back to 11:00 while the UI shows the user's value
        if not _HH_MM_RE.match(payload.publish_time):
            raise HTTPException(422, "publish_time must be HH:MM 24h format")
        t.auto_publish_time = payload.publish_time
    db.commit()
    db.refresh(t)
    return {
        "auto_generate_enabled": t.auto_generate_enabled,
        "auto_generate_time": t.auto_generate_time,
        "auto_generate_count": t.auto_generate_count,
        "auto_publish_enabled": t.auto_publish_enabled,
        "auto_approve_enabled": t.auto_approve_enabled,
        "auto_publish_paid_enabled": t.auto_publish_paid_enabled,
        "auto_publish_time": t.auto_publish_time,
        "auto_generate_last_run_at":
            t.auto_generate_last_run_at.isoformat() if t.auto_generate_last_run_at else None,
    }


@router.post("/topics/{topic_id}/run-now", status_code=202)
def run_now(
    topic_id: int,
    request: Request,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Immediately sample N items from the selected query pool by the current auto_generate_count (or 3 as fallback) and generate.

    Does not change auto_generate_last_run_at (that is the cron batch-run metric).
    """
    t = _own_topic_or_404(db, topic_id, current_user.id)
    if (t.submission_status or "draft") != "approved":
        raise HTTPException(409, {
            "code": "TOPIC_NOT_APPROVED",
            "message": "主题资料还未审核通过,暂不能触发生成",
        })
    n = int(t.auto_generate_count or 3)

    # Unified call gate: internal channels with unlimited always pass (behavior unchanged), external tiers are limited by daily count / RPM;
    # both write a call_audit row. Draft generation had no gate at all before; this is the first.
    from geo.services.call_policy import CallDenied, CallerRef, enforce
    try:
        enforce(db, CallerRef(channel="web", account_id=current_user.id, user_id=current_user.id),
                "content_run_now", endpoint="/api/content/topics/{id}/run-now",
                topic_id=topic_id, cells=n)
    except CallDenied as e:
        raise HTTPException(e.code, e.reason)

    _, operation = create_resource_operation_context(
        db,
        current_user,
        t,
        action="content.generate.create",
        target_resource_type="topic",
        target_resource_id=t.id,
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="content.generate.create",
        object_type="topic",
        object_id=t.id,
        result="success",
    )
    db.commit()

    # Provenance context: generation runs in a background thread, the request object cannot reach the persistence point, so a contextvar carries it
    with content_marking.gen_origin(
            user_id=current_user.id,
            client_ip=content_marking.client_ip(request)):
        schedule_generation(
            topic_id=topic_id, max_docs=n, mark_auto_run=False,
            trigger_source="run_now",
            operation_context_id=operation.id,
        )
    log.info("user run-now content gen topic=%d count=%d", topic_id, n)
    return {"queued": n, "topic_id": topic_id}


# ─────────────────── Publishing term-selection ledger (2026-08-17) ──────────────────────────
# Two views answer two questions: batch ledger = "which terms did last week's publishing cover";
# term ledger = "how many times was this term selected for publishing, and when last".
# Data comes from snapshots persisted at generation time (services/gen_batch.py), not recomputed from current config.


class GenBatchOut(BaseModel):
    id: int
    created_at: datetime
    finished_at: Optional[datetime] = None
    trigger_source: str = "unknown"
    actor_id: Optional[int] = None          # Empty = system automatic (scheduled / Siyuan / retry)
    plan_id: Optional[int] = None
    trace_id: Optional[str] = None          # Join llm_calls on it to see this batch's model cost
    queries: list[str] = []
    doc_ids: list[int] = []
    doc_count: int = 0
    stats: Optional[dict] = None


class QueryGenStatRow(BaseModel):
    query: str
    count: int                              # Times selected for publishing
    last_at: Optional[datetime] = None
    doc_ids: list[int] = []
    deleted: bool = False                   # Whether the term is soft-deleted (still shown, frontend adds a badge)


@router.get("/topics/{topic_id}/gen-batches", response_model=list[GenBatchOut])
def list_gen_batches(
    topic_id: int, limit: int = 50,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Publishing batch ledger: one row per generation trigger (time / trigger source / operator / selected terms / resulting articles)."""
    from geo.models.ai_telemetry import TopicContentGenBatchORM

    _own_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    rows = (
        db.query(TopicContentGenBatchORM)
          .filter(TopicContentGenBatchORM.topic_id == topic_id)
          .order_by(TopicContentGenBatchORM.created_at.desc())
          .limit(max(1, min(limit, 200)))
          .all()
    )

    def _loads(raw, default):
        try:
            v = json.loads(raw or "")
            return v if isinstance(v, type(default)) else default
        except Exception:  # noqa: BLE001
            return default

    return [
        GenBatchOut(
            id=r.id, created_at=r.created_at, finished_at=r.finished_at,
            trigger_source=r.trigger_source or "unknown", actor_id=r.actor_id,
            plan_id=r.plan_id, trace_id=r.trace_id,
            queries=[str(x) for x in _loads(r.queries_json, [])],
            doc_ids=[int(x) for x in _loads(r.doc_ids_json, [])],
            doc_count=int(r.doc_count or 0),
            stats=_loads(r.stats_json, {}) or None,
        )
        for r in rows
    ]


@router.get("/topics/{topic_id}/query-gen-stats", response_model=list[QueryGenStatRow])
def query_gen_stats(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Term ledger: how many times each prompt was selected for publishing, the latest time, and the corresponding articles.

    ★ Soft-deleted terms still appear if they were ever published (with a deleted flag) — a published draft was written for that term,
      and hiding it from the ledger would make history inconsistent.
    """
    from geo.models.ai_telemetry import deleted_query_texts
    from geo.services.gen_batch import query_gen_stats as _stats

    t = _own_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    gone = deleted_query_texts(t)
    rows = _stats(db, topic_id)
    out = [
        QueryGenStatRow(query=q, count=v["count"], last_at=v["last_at"],
                        doc_ids=sorted(v["doc_ids"]), deleted=q in gone)
        for q, v in rows.items()
    ]
    out.sort(key=lambda r: (-r.count, r.query))
    return out


# ─────────────────── Publish tasks / URL backfill (flywheel P1 §5.3) ──────────────────
# User "chooses to publish" to manual/semi-automatic/external media → create publish task (pending backfill) → publish manually on the platform →
# backfill the real article URL → register retest and enter the reward loop. Core invariant: no real URL, no real placement.


class PublishTaskOut(BaseModel):
    id: int
    topic_id: int
    doc_id: int
    vscl_action_id: Optional[int] = None
    platform: str
    media_name: str
    channel_type: str
    publish_mode: str
    status: str
    planned_publish_at: Optional[str] = None
    actual_published_at: Optional[str] = None
    published_url: Optional[str] = None
    proof: Optional[dict] = None
    channel_meta: Optional[dict] = None
    cost_cents: Optional[int] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    # For user-side display: platform is the vendor's Chinese name (internal anchor, cannot change); display uses platform_display
    platform_display: Optional[str] = None
    retail_price_yuan: Optional[str] = None    # External price
    charged_cents: Optional[int] = None        # Actual amount charged for this task (net)


class PublishTargetIn(BaseModel):
    platform: str
    media_name: str = ""
    channel_type: Optional[str] = None


class CreatePublishTasksPayload(BaseModel):
    targets: list[PublishTargetIn]


class SubmitUrlPayload(BaseModel):
    url: str
    actual_published_at: Optional[str] = None
    proof: Optional[dict] = None
    verify_before_publish: bool = False


class PublishVerificationReportPayload(BaseModel):
    platform_code: str
    external_post_id: str
    status: str
    raw_status: Optional[int] = None
    audit_fail_reason: Optional[str] = None
    publish_time: Optional[str] = None
    observation_id: Optional[str] = None
    observed_at: Optional[str] = None
    error_message: Optional[str] = None


def _own_task_or_404(
    db: Session, task_id: int, user_id: int, *, allow_admin_user=None,
    action: str = ACTION_WRITE,
):
    from geo.models.ai_telemetry import TopicDocPublishTaskORM
    task = db.get(TopicDocPublishTaskORM, task_id)
    if not task:
        raise resource_not_found()
    actor = allow_admin_user or db.get(UserORM, user_id)
    if actor is None:
        raise resource_not_found()
    require_resource_access(db, actor, task, action)
    return task


@router.post("/docs/{doc_id}/publish-tasks", response_model=list[PublishTaskOut])
def create_publish_tasks(
    doc_id: int,
    payload: CreatePublishTasksPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """"Choose to publish": create publish tasks for an approved draft (one per media, URL pending backfill)."""
    from geo.services import publish_task_service

    d = _own_doc_or_404(db, doc_id, current_user.id, allow_admin_user=current_user)
    if d.status not in ("approved", "published"):
        raise HTTPException(409, {"code": "NEED_APPROVED", "message": "请先通过审核再选择发布"})
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    _, operation = create_resource_operation_context(
        db, current_user, d,
        action="publish.task.create", target_resource_type="document",
    )
    tasks = publish_task_service.create_tasks(
        db, d, t, [tg.model_dump() for tg in payload.targets],
        assigned_to_user_id=current_user.id,
        operation_context_id=operation.id,
    )
    append_system_audit(
        db, operation_context=operation, event_type="task_created",
        action="publish.task.create", object_type="document",
        object_id=d.id, result="success",
    )
    db.commit()
    return [publish_task_service.task_to_dict(
        x, redact=not is_staff(current_user), db=db) for x in tasks]


@router.get("/topics/{topic_id}/publish-tasks", response_model=list[PublishTaskOut])
def list_publish_tasks(
    topic_id: int,
    status: Optional[str] = None,
    doc_id: Optional[int] = None,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Publish task list (pending backfill / placed / failed, etc.), for the "URL pending backfill" panel."""
    from geo.services import publish_task_service

    _own_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    statuses = [status] if status else None
    rows = publish_task_service.list_tasks(db, topic_id, doc_id=doc_id, statuses=statuses)
    return [publish_task_service.task_to_dict(
        x, redact=not is_staff(current_user), db=db) for x in rows]


@router.get("/my/publish-tasks", response_model=list[PublishTaskOut])
def list_my_publish_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """**Cross-topic** "my placements" overview.

    Previously there was only a per-topic_id list — multi-topic accounts wanting to know "what did I place in total, which went live,
    which still await link backfill" had to click through one topic at a time. Paid placements cost money,
    so not having a single ledger page is unacceptable.
    """
    from geo.models.ai_telemetry import TopicDocPublishTaskORM
    from geo.services import publish_task_service

    topic_query = apply_enterprise_scope(
        db.query(AiTelemetryTopicORM.id), db, current_user, AiTelemetryTopicORM.user_id,
    )
    tids = [t.id for t in topic_query.all()]
    if not tids:
        return []
    q = (db.query(TopicDocPublishTaskORM)
         .filter(TopicDocPublishTaskORM.topic_id.in_(tids)))
    if status:
        q = q.filter(TopicDocPublishTaskORM.status == status)
    rows = (q.order_by(TopicDocPublishTaskORM.id.desc())
            .limit(max(1, min(int(limit or 50), 200))).all())
    return [publish_task_service.task_to_dict(
        x, redact=not is_staff(current_user), db=db) for x in rows]


class WithdrawTaskPayload(BaseModel):
    reason: str = Field("", max_length=200)


# States that can be withdrawn. **Paid proxy publishing has its own set** — its pending_url means "order placed upstream,
# awaiting receipt"; the money is spent and cannot be withdrawn; a manual task's pending_url only means "waiting for someone to paste a link",
# nothing has happened, so of course it can be withdrawn. The same status string means opposite things in the two channels; they cannot share a table.
_WITHDRAWABLE_MANUAL = ("planned", "pending_publish", "pending_url")
_WITHDRAWABLE_PAID = ("planned",)


@router.post("/publish-tasks/{task_id}/withdraw", response_model=PublishTaskOut)
def withdraw_publish_task(
    task_id: int,
    payload: Optional[WithdrawTaskPayload] = None,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Withdraw a placement task not yet sent out (set cancelled). Owner or staff.

    Why this exists: previously `planned` could only move forward (place order), with no way back — if a paid media slot in a plan
    was no longer wanted, the UI had no way to cancel it; the only option was "nobody click that publish button".

    **Paid tasks can only be withdrawn while planned**: past that, publishing / pending_url mean the upstream order
    has been submitted and money charged; setting cancelled would only make accounts and facts disagree (task shows cancelled,
    balance is short, upstream still publishing). That case requires a refund (services/publish_refund), not withdrawal.
    """
    from geo.services import publish_task_service

    task = _own_task_or_404(db, task_id, current_user.id, allow_admin_user=current_user)
    is_paid = (task.platform or "") in publish_task_service._paid_platform_names()
    allowed = _WITHDRAWABLE_PAID if is_paid else _WITHDRAWABLE_MANUAL
    if (task.status or "") not in allowed:
        raise HTTPException(409, {
            "code": "NOT_WITHDRAWABLE",
            "message": ("这条已经下单/发布,撤不回来了。" if is_paid
                        else "这条已经发布,撤不回来了。"),
            "status": task.status,
        })
    # Anything that has incurred charges cannot be withdrawn — money movement must go through the refund flow; do not balance the books by withdrawing
    if int(getattr(task, "cost_cents", 0) or 0) > 0:
        raise HTTPException(409, {
            "code": "ALREADY_CHARGED",
            "message": "这条已经产生过费用,请走退款,不能直接撤回。",
        })

    task.status = "cancelled"
    task.last_error = None
    meta = publish_task_service._task_meta(task)
    meta["withdrawn_at"] = datetime.utcnow().isoformat()
    meta["withdrawn_by"] = current_user.id
    if payload is not None and (payload.reason or "").strip():
        meta["withdraw_reason"] = payload.reason.strip()[:200]
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
    db.commit()
    db.refresh(task)
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(current_user), db=db)


@router.post("/publish-tasks/{task_id}/submit-url", response_model=PublishTaskOut)
def submit_publish_url(
    task_id: int,
    payload: SubmitUrlPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Backfill the article URL.

    Manual backfill is recorded directly by default; automatic/plugin channels may pass verify_before_publish=true
    to show a candidate link first, entering the published loop only after probing or manual confirmation.
    """
    from datetime import datetime
    from geo.services import publish_task_service

    task = _own_task_or_404(db, task_id, current_user.id, allow_admin_user=current_user)
    apat = None
    if payload.actual_published_at:
        try:
            apat = datetime.fromisoformat(payload.actual_published_at.replace("Z", "+00:00"))
            if apat.tzinfo is not None:
                apat = apat.astimezone(tz=None).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(422, "actual_published_at 格式应为 ISO 时间")
    try:
        fn = (publish_task_service.submit_candidate_url
              if payload.verify_before_publish else publish_task_service.submit_url)
        task = fn(db, task, payload.url, actual_published_at=apat,
                  proof=payload.proof, actor_id=current_user.id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, {"code": "SUBMIT_URL_REJECTED", "message": str(e)})
    db.commit()
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(current_user), db=db)


@router.post("/publish-tasks/{task_id}/confirm-candidate-url", response_model=PublishTaskOut)
def confirm_publish_candidate_url(
    task_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record into the official placement ledger after a human confirms the candidate URL is valid."""
    from geo.services import publish_task_service

    task = _own_task_or_404(db, task_id, current_user.id, allow_admin_user=current_user)
    try:
        task = publish_task_service.confirm_candidate_url(
            db, task, actor_id=current_user.id,
            proof={"confirmed_by": "human", "user_id": current_user.id},
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, {"code": "CANDIDATE_URL_REJECTED", "message": str(e)})
    db.commit()
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(current_user), db=db)


@router.post("/publish-tasks/{task_id}/report-verification", response_model=PublishTaskOut)
def report_publish_verification(
    task_id: int,
    payload: PublishVerificationReportPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Receive the publish status obtained by the browser extension within the platform login session."""
    from geo.services import publish_task_service

    task = _own_task_or_404(db, task_id, current_user.id, allow_admin_user=current_user)
    try:
        task = publish_task_service.report_platform_verification(
            db,
            task,
            platform_code=payload.platform_code,
            external_post_id=payload.external_post_id,
            status=payload.status,
            raw_status=payload.raw_status,
            audit_fail_reason=payload.audit_fail_reason or "",
            publish_time=payload.publish_time or "",
            observation_id=payload.observation_id or "",
            observed_at=payload.observed_at or "",
            error_message=payload.error_message or "",
            actor_id=current_user.id,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, {"code": "VERIFICATION_REPORT_REJECTED", "message": str(e)})
    db.commit()
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(current_user), db=db)


@router.post("/publish-tasks/{task_id}/refresh-verification", response_model=PublishTaskOut)
def refresh_publish_verification(
    task_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move Ctrip platform verification forward to now; the actual query is still done by the original executor."""
    from geo.services import publish_task_service

    task = _own_task_or_404(db, task_id, current_user.id, allow_admin_user=current_user)
    meta = publish_task_service._task_meta(task)
    state = meta.get("url_verification")
    if task.status != "url_submitted" or not isinstance(state, dict) \
            or state.get("verification_method") != "platform_api" \
            or state.get("platform_code") != "ctrip":
        raise HTTPException(409, {
            "code": "VERIFICATION_REFRESH_REJECTED",
            "message": "该任务当前不支持刷新携程审核状态",
        })
    state["next_at"] = datetime.utcnow().isoformat()
    meta["url_verification"] = state
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
    db.commit()
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(current_user), db=db)
