"""Phase D: Admin content review API.

Document lifecycle: draft -> pending_review -> approved -> published (or rejected)

Endpoints:
- GET    /admin/content-review/topics                - list (account, topic) pairs that have docs
- GET    /admin/content-review/topics/{topic_id}/docs?status=draft|all
- POST   /admin/content-review/docs/select           - move the selected docs to pending_review
- POST   /admin/content-review/docs/{id}/approve     - approve one
- POST   /admin/content-review/docs/{id}/reject      - reject one + reason + email
- POST   /admin/content-review/docs/{id}/rescan      - compliance rescan; returned drafts that pass go back to draft automatically
- POST   /admin/content-review/docs/{id}/publish     - mark the publishing platform/media + email
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.api.auth import get_current_user, require_workbench
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, GeneratedDocOut, PublishPayload, RejectDocPayload,
    SelectDocsPayload, TopicGeneratedDocORM,
)
from geo.models.user import UserORM
from geo.services.plan_link import doc_out

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/content-review")


class TopicWithDocsItem(BaseModel):
    topic_id: int
    topic_name: str
    user_id: int
    user_email: str
    doc_count: int
    draft_count: int
    pending_review_count: int
    approved_count: int
    published_count: int
    rejected_count: int


@router.get("/topics", response_model=list[TopicWithDocsItem])
def list_topics_with_docs(
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """List topics that have content docs (for the frontend dropdown)."""
    # Aggregate doc counts
    # Soft-deleted drafts never enter the list or the counts (not shown in the frontend)
    docs = db.query(TopicGeneratedDocORM).filter(TopicGeneratedDocORM.deleted_at.is_(None)).all()
    if not docs:
        return []
    by_topic: dict[int, dict[str, int]] = {}
    for d in docs:
        b = by_topic.setdefault(d.topic_id, {
            "doc_count": 0, "draft": 0, "pending_review": 0,
            "approved": 0, "published": 0, "rejected": 0,
        })
        b["doc_count"] += 1
        b[d.status] = b.get(d.status, 0) + 1
    topic_ids = list(by_topic.keys())
    topics = {t.id: t for t in db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.id.in_(topic_ids)).all()}
    user_ids = {t.user_id for t in topics.values()}
    emails: dict[int, str] = {}
    if user_ids:
        for u in db.query(UserORM).filter(UserORM.id.in_(user_ids)).all():
            emails[u.id] = u.email
    out: list[TopicWithDocsItem] = []
    for tid, b in by_topic.items():
        t = topics.get(tid)
        if not t:
            continue
        out.append(TopicWithDocsItem(
            topic_id=tid, topic_name=t.name,
            user_id=t.user_id, user_email=emails.get(t.user_id, ""),
            doc_count=b["doc_count"],
            draft_count=b.get("draft", 0),
            pending_review_count=b.get("pending_review", 0),
            approved_count=b.get("approved", 0),
            published_count=b.get("published", 0),
            rejected_count=b.get("rejected", 0),
        ))
    out.sort(key=lambda x: (-x.pending_review_count, -x.draft_count, x.topic_name))
    return out


@router.get("/topics/{topic_id}/docs", response_model=list[GeneratedDocOut])
def list_topic_docs(
    topic_id: int,
    status: Optional[str] = None,
    source: Optional[str] = None,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """List docs under a topic.

    status filter:
      - omitted: all
      - "draft" / "pending_review" / "approved" / "rejected" / "published": exact match
      - "to_review": draft + pending_review (common filter, what the admin view wants by default)
    source filter:
      - omitted / "all": all
      - "ai" / "user": only that source
    """
    q = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.topic_id == topic_id)
           .filter(TopicGeneratedDocORM.deleted_at.is_(None)))          # Soft-deleted ones are not shown
    if status == "to_review":
        q = q.filter(TopicGeneratedDocORM.status.in_(["draft", "pending_review"]))
    elif status:
        q = q.filter(TopicGeneratedDocORM.status == status)
    if source in ("ai", "user"):
        q = q.filter(TopicGeneratedDocORM.source == source)
    rows = q.order_by(TopicGeneratedDocORM.id.desc()).all()
    # Link back to the publishing plan row (the frontend shows "Plan #N · date" and can jump back to the plan table)
    from geo.services.plan_link import plan_item_index
    plan_idx = plan_item_index(db, topic_id)
    # Bulk-inject template names/kinds (the detail dialog's "creative links" block renders directly from list data)
    from geo.models.ai_telemetry import ContentTemplateORM
    tpl_ids = {r.template_id for r in rows if getattr(r, "template_id", None)}
    tpl_meta: dict[int, tuple[str, str]] = {}
    if tpl_ids:
        for tid, name, kind in (
            db.query(ContentTemplateORM.id, ContentTemplateORM.name, ContentTemplateORM.kind)
              .filter(ContentTemplateORM.id.in_(list(tpl_ids))).all()
        ):
            tpl_meta[tid] = (name, kind)
    return [
        GeneratedDocOut.from_orm_row(
            r,
            template_name=tpl_meta.get(getattr(r, "template_id", None), (None, None))[0],
            template_kind=tpl_meta.get(getattr(r, "template_id", None), (None, None))[1],
            plan_row=plan_idx.get(getattr(r, "plan_item_id", None) or ""))
        for r in rows
    ]


def _require_doc_access(db: Session, doc, actor) -> None:
    """Whether this draft belongs to the caller. Staff are always allowed; others are judged by the enterprise data scope of the draft's topic."""
    from geo.api.auth import is_staff
    from geo.models.ai_telemetry import AiTelemetryTopicORM
    from geo.services.access_control import (
        ACTION_READ, require_resource_access, resource_not_found,
    )
    if is_staff(actor):
        return
    t = db.get(AiTelemetryTopicORM, getattr(doc, "topic_id", None))
    if t is None:
        raise resource_not_found()
    require_resource_access(db, actor, t, ACTION_READ)


@router.get("/docs/{doc_id}", response_model=GeneratedDocOut)
def get_doc(
    doc_id: int,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Single draft details. **owner or staff**: clicking a row on the plan page (now available to customers too) previews the draft.

    ★ Also adds the ownership check: previously this was admin-only and returned directly given an id; once opened up, that would be
      "change the doc_id and read someone else's draft". Opening up permissions requires adding this layer at the same time.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    _require_doc_access(db, d, actor)
    return doc_out(db, d)


class AdminDocUpdatePayload(BaseModel):
    title: Optional[str] = None
    body_markdown: Optional[str] = None
    summary: Optional[str] = None


@router.patch("/docs/{doc_id}", response_model=GeneratedDocOut)
def admin_update_doc(
    doc_id: int,
    payload: AdminDocUpdatePayload,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Admin edits the body of an AI / user draft. No source / status restriction; purely for manual correction.

    If the edited draft was returned -> a compliance rescan runs automatically after saving: if it passes it reverts to draft,
    and can then be submitted for review / approved / published; if not, it stays rejected with the rejection reason refreshed.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    was_rejected = d.status == "rejected"
    if payload.title is not None:
        d.title = payload.title.strip()[:200]
    if payload.body_markdown is not None:
        d.body_markdown = payload.body_markdown
    if payload.summary is not None:
        d.summary = payload.summary[:500]

    # Bodies carry no marking (2026-08-17); here legacy markings pasted in are stripped on save,
    # and the three provenance columns are filled in; this runs before the compliance rescan, so the rescan sees the cleaned body.
    if payload.body_markdown is not None:
        _remark_body(db, d)

    if was_rejected:
        _rescan_rejected(db, d, admin)
    db.commit()
    db.refresh(d)
    return doc_out(db, d)


def _remark_body(db: Session, d: TopicGeneratedDocORM) -> None:
    """Strip legacy markings from the body (explicit marking lines + zero-width watermarks) and fill in the three provenance columns.

    A processing failure does not block saving: editing the body is an essential need for ops, and this can be rerun later.
    """
    try:
        from geo.services import content_marking

        content_marking.stamp(db, d)
    except Exception:  # noqa: BLE001
        log.exception("content marking failed for doc %s", d.id)


def _rescan_rejected(db: Session, d: TopicGeneratedDocORM, admin) -> Optional[dict]:
    """Compliance rescan unlock for returned drafts; a missing topic or a scan blowing up never blocks saving (log only)."""
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    if t is None:
        return None
    from geo.services import compliance_scanner
    try:
        return compliance_scanner.rescan_and_reopen(
            db, d, t, actor_type="admin", actor_id=admin.id,
            actor_name=getattr(admin, "email", "") or "",
        )
    except Exception:  # noqa: BLE001
        log.exception("compliance rescan failed for doc %s", d.id)
        return None


@router.post("/docs/{doc_id}/rescan", response_model=GeneratedDocOut)
def rescan_doc(
    doc_id: int,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Manually rerun the compliance scan: used to unlock after editing a returned draft / adjusting the lexicon.

    Rescan passes -> status back to draft, rejection reason cleared; still hits -> stays rejected with the reason refreshed.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status in ("approved", "published"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},无需复扫"})
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    if t is None:
        raise HTTPException(404, "topic not found")
    from geo.services import compliance_scanner
    try:
        if d.status == "rejected":
            compliance_scanner.rescan_and_reopen(
                db, d, t, actor_type="admin", actor_id=admin.id,
                actor_name=getattr(admin, "email", "") or "",
            )
        else:
            compliance_scanner.scan_and_apply(
                db, d, t, hook="manual", actor_type="admin", actor_id=admin.id,
                actor_name=getattr(admin, "email", "") or "",
            )
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("manual rescan doc %d failed: %s", doc_id, e)
        raise HTTPException(502, {"code": "RESCAN_FAILED", "message": str(e)[:300]})
    db.commit()
    db.refresh(d)
    return doc_out(db, d)


@router.post("/docs/select", status_code=204)
def select_docs_for_review(
    payload: SelectDocsPayload,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Move the selected draft docs to pending_review."""
    rows = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.id.in_(payload.doc_ids))
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))             # Soft-deleted ones are not submitted for review
          .all()
    )
    n = 0
    for d in rows:
        if d.status == "draft":
            d.status = "pending_review"
            d.selected_for_review = True
            n += 1
    db.commit()
    log.info("content review: selected %d docs to pending_review", n)


@router.post("/docs/{doc_id}/approve", response_model=GeneratedDocOut)
def approve_doc(
    doc_id: int,
    background: BackgroundTasks,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},不可通过"})
    d.status = "approved"
    d.selected_for_review = True
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = admin.id
    d.reject_reason = None
    db.commit()
    db.refresh(d)
    # Publishing plan preset: automatically create placement tasks per plan row (platform, media) (Ruanwenjiang auto-pushes drafts);
    # failures are only logged and do not affect the review result
    from geo.services.publish_preset import apply_preset_on_approve
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    if t is not None:
        apply_preset_on_approve(db, d, t, actor_id=admin.id)
    _notify_user(db, background, d, decision="approved")
    return doc_out(db, d)


@router.post("/docs/{doc_id}/regenerate", response_model=GeneratedDocOut)
def regenerate_doc(
    doc_id: int,
    request: Request,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Regenerate a single doc: runs the LLM synchronously once (~30-60s), overwrites the body and returns the new doc.

    Reproduces the original generation path with the doc's own prompt / creative direction / copy type / template.
    Approved / published drafts cannot be regenerated (to avoid wiping adopted content).
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status in ("approved", "published"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},不可重新生成"})
    from geo.services import content_marking, gen_retry
    from geo.services.content_generator import regenerate_doc as _regen
    try:
        # Regeneration overwrites the body -> marking is reapplied internally; when a legacy draft first gets provenance, it is attributed to this operator
        with content_marking.gen_origin(
                user_id=admin.id, client_ip=content_marking.client_ip(request)):
            _regen(db, d)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("regenerate doc %d failed: %s", doc_id, e)
        # A failed manual regeneration is also recorded + the next automatic retry is scheduled (not for terminal error causes), otherwise each click is just a wasted failure
        d = db.get(TopicGeneratedDocORM, doc_id)
        if d is not None:
            gen_retry.mark_failure(db, d, str(e), commit=True)
        raise HTTPException(502, {"code": "REGEN_FAILED", "message": str(e)[:300]})
    return doc_out(db, d)


@router.post("/docs/{doc_id}/cancel-retry", response_model=GeneratedDocOut)
def cancel_doc_retry(
    doc_id: int,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Give up automatic retries for this draft: dequeue it but keep the error cause; "regenerate" can still be clicked manually.

    For cases where the error cause makes it obvious retrying is pointless (quota exhausted / the seed query itself is bad),
    so it does not burn an LLM call every 10 minutes.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    from geo.services import gen_retry
    gen_retry.give_up(db, d)
    log.info("admin gave up auto-retry for doc=%d", doc_id)
    return doc_out(db, d)


@router.post("/docs/{doc_id}/reject", response_model=GeneratedDocOut)
def reject_doc(
    doc_id: int,
    payload: RejectDocPayload,
    background: BackgroundTasks,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},不可拒绝"})
    d.status = "rejected"
    d.selected_for_review = True
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = admin.id
    d.reject_reason = payload.reason or None
    db.commit()
    db.refresh(d)
    _notify_user(db, background, d, decision="rejected", reject_reason=payload.reason or None)
    return doc_out(db, d)


@router.post("/docs/{doc_id}/publish", response_model=GeneratedDocOut)
async def publish_doc(
    doc_id: int,
    payload: PublishPayload,
    background: BackgroundTasks,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Mark publish targets + (optionally) push to Mediumsly.

    publish_targets_json is always written (old semantics unchanged, just "noting where it was published");
    with push_to_mediumsly=true, additionally call Mediumsly's internal API and write the returned post id/url
    into the doc's mediumsly_* columns. A Mediumsly push failure does not roll back the publish on the GEO side;
    the error is stored in mediumsly_last_error for the admin to retry.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status != "approved":
        raise HTTPException(409, {"code": "NEED_APPROVED",
                                  "message": "请先通过审核再选发布平台"})

    # Compliance gate hook point 4: forced rescan on the manually selected push exit + gate-0 qualification check (design doc 5.2).
    # Automatic and manual are two independent push exits and both must be gated, otherwise after a lexicon upgrade
    # previously approved drafts could bypass it via the manual channel.
    gate_topic = db.get(AiTelemetryTopicORM, d.topic_id)
    if gate_topic is not None:
        from geo.services import compliance_scanner

        gate_ok, gate_reason = compliance_scanner.publish_gate(
            db, d, gate_topic, hook="publish_manual",
            actor_id=admin.id, actor_name=getattr(admin, "email", "") or "",
        )
        if not gate_ok:
            db.commit()   # The block trail (scan_results + audit log) must be persisted to DB
            raise HTTPException(409, {"code": "COMPLIANCE_BLOCKED",
                                      "message": f"合规闸门拦截:{gate_reason}"})

    try:
        targets = json.loads(d.publish_targets_json or "[]")
    except Exception:  # noqa: BLE001
        targets = []
    if not isinstance(targets, list):
        targets = []
    now_iso = datetime.utcnow().isoformat()
    new_targets = [
        {
            "platform": t.platform,
            "media": t.media,
            "url": (t.url or "").strip(),
            "marked_at": now_iso,
            "marked_by": admin.id,
        }
        for t in payload.publish_targets
    ]
    targets.extend(new_targets)
    d.publish_targets_json = json.dumps(targets, ensure_ascii=False)
    d.status = "published"

    # ── Mediumsly synchronous push (optional) ──────────────────────────────
    if payload.push_to_mediumsly:
        # Lazy import: no need to load httpx / the publisher module when not selected.
        from geo.services import mediumsly_publisher

        topic = db.get(AiTelemetryTopicORM, d.topic_id)
        user = db.get(UserORM, topic.user_id) if topic else None
        if not user or not user.email:
            d.mediumsly_last_error = "Topic owner or email missing — cannot push"
        else:
            try:
                result = await mediumsly_publisher.push(d, user, topic)
                d.mediumsly_post_id   = result.post_id
                d.mediumsly_url       = result.url
                d.mediumsly_pushed_at = datetime.utcnow()
                d.mediumsly_last_error = None
            except mediumsly_publisher.MediumslyPostGone:
                # Manually deleted on the Mediumsly side: clear the id so the next retry recreates it via POST
                d.mediumsly_post_id = None
                d.mediumsly_last_error = "Mediumsly post was deleted upstream — id cleared, retry to re-create"
            except mediumsly_publisher.MediumslyError as e:
                # Record the error without interrupting GEO's own publish flow
                d.mediumsly_last_error = f"{e.code or 'ERROR'}: {e}"
                log.warning("[mediumsly_publisher] doc=%s push failed: %s", d.id, d.mediumsly_last_error)

    # Flywheel P1-C: register a targeted post-placement re-test probe (failure does not block publishing).
    # Siyuan P0 (design doc §5.2/§6): without a real URL it is not a real placement: only register the re-test when the publish targets marked
    # this time include at least one non-empty URL (or an existing Mediumsly URL), otherwise the reward window would include
    # content not yet live, polluting the RL reward. The draft is still set to published as a display marker.
    has_real_url = any((t.url or "").strip() for t in payload.publish_targets) or bool(
        (d.mediumsly_url or "").strip()
    )
    if has_real_url:
        try:
            from geo.services.retest_service import schedule_for_doc

            retest_topic = db.get(AiTelemetryTopicORM, d.topic_id)
            if retest_topic is not None:
                schedule_for_doc(db, d, retest_topic)
        except Exception:  # noqa: BLE001
            log.exception("[publish] retest probe failed doc=%s", d.id)
    else:
        log.info("[publish] doc=%s has no real URL; marked published but no re-test registered", d.id)

    db.commit()
    db.refresh(d)
    _notify_user(db, background, d, decision="published", publish_targets=new_targets)
    return doc_out(db, d)


class RetestProbeOut(BaseModel):
    id: int
    doc_id: int
    status: str
    queries: list[str]
    engines: list[str]
    baseline: dict
    result: dict                       # {before, after, delta}; {} when unfinished
    scheduled_for: Optional[datetime] = None
    enqueued_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


@router.get("/docs/{doc_id}/retest", response_model=list[RetestProbeOut])
def get_doc_retest(
    doc_id: int,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Flywheel P1-C: targeted post-placement re-test probes for this doc (before vs after placement metric comparison)."""
    from geo.models.ai_telemetry import GeoRetestProbeORM

    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    rows = (
        db.query(GeoRetestProbeORM)
          .filter(GeoRetestProbeORM.doc_id == doc_id)
          .order_by(GeoRetestProbeORM.id.desc())
          .all()
    )
    out = []
    for p in rows:
        def _j(s, dflt):
            try:
                v = json.loads(s or "")
                return v if isinstance(v, type(dflt)) else dflt
            except Exception:  # noqa: BLE001
                return dflt
        out.append(RetestProbeOut(
            id=p.id, doc_id=p.doc_id, status=p.status,
            queries=_j(p.queries_json, []), engines=_j(p.engines_json, []),
            baseline=_j(p.baseline_json, {}), result=_j(p.result_json, {}),
            scheduled_for=p.scheduled_for, enqueued_at=p.enqueued_at,
            completed_at=p.completed_at, error=p.error,
        ))
    return out


@router.get("/docs/{doc_id}/rationale")
def get_doc_rationale(
    doc_id: int,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """"Strategy basis": what was injected into the prompt at generation time, recomputed with the current config.

    Pure read: no LLM request, nothing persisted. Deliberately **not put into GeneratedDocOut**: the list API would have to
    query platform rules and aggregate the last 300 answers for competitors per row, noticeably slowing the list. Only fetched on demand
    when opening a single draft's details.

    The frontend must display the returned caveat as-is: this is a recomputation, not an archive from that time.
    """
    from geo.services.content_generator import describe_generation_inputs

    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    try:
        return describe_generation_inputs(db, d)
    except Exception as e:  # noqa: BLE001
        # A broken explanation page should not affect viewing the draft: return an empty shell so the frontend does not render this block
        log.exception("[doc-rationale] failed doc=%s", doc_id)
        return {"doc_id": doc_id, "topic_id": d.topic_id, "blocks": [],
                "error": str(e)}


class PlatformReviewPayload(BaseModel):
    status: str                       # passed / rejected
    reason: Optional[str] = None      # Platform rejection reason when rejected (material for rule learning)


@router.post("/docs/{doc_id}/platform-review", response_model=GeneratedDocOut)
def set_platform_review(
    doc_id: int,
    payload: PlatformReviewPayload,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """2026-06-12: backfill the platform's review result (approved/rejected by the platform after publishing).

    Reasons for rejected are aggregated by /admin/platform-rules/learn and distilled by the LLM into
    incremental review rules for that platform. Backfill is only allowed for drafts already published.
    """
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status != "published":
        raise HTTPException(409, {"code": "NEED_PUBLISHED",
                                  "message": "先标记发布,再回填平台审核结果"})
    if payload.status not in ("passed", "rejected"):
        raise HTTPException(422, "status must be passed / rejected")
    if payload.status == "rejected" and not (payload.reason or "").strip():
        raise HTTPException(422, {"code": "NEED_REASON",
                                  "message": "平台被拒必须填写拒稿原因(规则学习的素材)"})
    d.platform_review_status = payload.status
    d.platform_reject_reason = (payload.reason or "").strip() or None
    db.commit()
    db.refresh(d)
    return doc_out(db, d)


def _notify_user(db: Session, background: BackgroundTasks, doc: TopicGeneratedDocORM,
                  *, decision: str, reject_reason: str | None = None,
                  publish_targets: list[dict] | None = None) -> None:
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if not topic:
        return
    user = db.get(UserORM, topic.user_id)
    if not user or not user.email:
        return
    background.add_task(_send_safe,
                        to=user.email, doc_title=doc.title or doc.source_query_text or f"#{doc.id}",
                        decision=decision, reject_reason=reject_reason,
                        publish_targets=publish_targets)


def _send_safe(*, to: str, doc_title: str, decision: str,
                reject_reason: str | None, publish_targets: list[dict] | None) -> None:
    try:
        from geo.services.email_service import email_service
        email_service.send_content_review_result_email(
            to=to, doc_title=doc_title, decision=decision,
            reject_reason=reject_reason, publish_targets=publish_targets,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("content review email failed: %s", e)


# ─────────────────────── URL-level ROI recompute ──────────────────────────

class RebuildCitedByPayload(BaseModel):
    topic_id: Optional[int] = None  # Omitted = all topics
    replace: bool = False            # Merge by default; True = recompute and overwrite


@router.post("/rebuild-cited-by")
def rebuild_cited_by(
    payload: RebuildCitedByPayload,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),  # noqa: ARG001  # backfill uses its own session
):
    """Intersect ai_telemetry_responses.citations_json with doc.publish_targets_json and
    backfill doc.cited_by_json. Omitting topic_id processes everything.

    Idempotent; callable by admin / cron.
    """
    from geo.services.backfill_cited_by import run as run_backfill
    stats = run_backfill(topic_id=payload.topic_id, dry_run=False, replace=payload.replace)
    return stats
