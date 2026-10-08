"""Admin review: Phase C (single seed/query review) + Phase D (whole application review).

New Phase D endpoints (with the topic as the unit of review):
- GET    /admin/review/topics                          - list all topic applications, with status filter
- GET    /admin/review/topic/{id}                      - full materials of one application (profile / seeds / prompts / logs)
- PATCH  /admin/review/topic/{id}                      - admin edits profile / seeds / prompts (via changelog)
- POST   /admin/review/topic/{id}/approve              - approve: run once + generate execution plan + async draft generation + email
- POST   /admin/review/topic/{id}/reject               - reject + email
- GET    /admin/review/topic/{id}/execution-plan       - get the execution plan (with 4 sections + run progress, for polling)

Phase C single-item endpoints (seed queries one by one / queries in bulk) are kept for backward compatibility.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import require_workbench, get_current_user, is_staff
from geo.api.ai_telemetry import get_db, _append_changelog, _sync_sales_brand
from geo.models.ai_telemetry import (
    AiTelemetryCellInsightORM,
    AiTelemetryQueryHitORM, AiTelemetryRunORM, AiTelemetryTopicORM,
    AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicSolutionORM,
    BrandProfile, ContentTemplateORM, ExecutionPlanUpdatePayload,
    GenerateSolutionPayload, MAX_SELECTED_QUERIES, PlanAddItemPayload,
    PlanBatchGenOut, PlanBatchGenPayload, PlanGeneratePayload, PlanItemDraft,
    PROFILE_REQUIRED_FIELDS, PublishPlanItem, SolutionDiagnosis, merge_profile_json,
    SolutionDiagnosisCheck, SolutionDiagnosisCluster, SolutionKeywordTier,
    SolutionQueriesSnapshot, SolutionQueryCluster, SolutionQueryItem,
    SolutionSevenStepItem, SolutionVisionItem, StartTopicPayload,
    TopicChangelogEntry,
    TopicExecutionPlanOut, TopicGeneratedDocORM, TopicProgressCell,
    TopicSolutionOut, ExpansionLogEntry, TopicOut,
)
from geo.models.user import UserORM
from geo.services import plan_basis
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    apply_enterprise_scope,
    create_resource_operation_context,
    require_resource_access,
    resource_not_found,
)
from geo.services.system_audit import append_event as append_system_audit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/review")
# New review-free channel: admin "starts" the project directly, no longer going through pending -> approved.
# The old router retires in PR 2; this router is the long-term entry point.
topics_router = APIRouter(prefix="/admin/topics")

TELEMETRY_SERVICE_URL = os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095")
from geo.services.telemetry_client import internal_headers  # noqa: E402

# When starting a project, if there was a successful batch run within this many days, reuse it as the baseline instead of triggering a new round.
# See the notes on _recent_success_run_id.
BASELINE_REUSE_DAYS = int(os.environ.get("GEO_PLAN_BASELINE_REUSE_DAYS", "7"))


# ═════════════════ Phase C: pending (seeds / single query) ═════════════════


class PendingSeedItem(BaseModel):
    topic_id: int
    topic_name: str
    target: str
    user_id: int
    user_email: str
    idx: int
    text: str
    submitted_at: Optional[str] = None


class PendingQueryItem(BaseModel):
    topic_id: int
    topic_name: str
    target: str
    user_id: int
    user_email: str
    idx: int
    text: str
    cluster_id: Optional[int] = None
    submitted_at: Optional[str] = None


class PendingOut(BaseModel):
    seed_prompts: list[PendingSeedItem]
    queries: list[PendingQueryItem]


@router.get("/pending", response_model=PendingOut)
def list_pending(
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    rows = db.query(AiTelemetryTopicORM).all()
    user_ids = {r.user_id for r in rows}
    email_by_uid: dict[int, str] = {}
    if user_ids:
        for u in db.query(UserORM).filter(UserORM.id.in_(user_ids)).all():
            email_by_uid[u.id] = u.email
    seeds: list[PendingSeedItem] = []
    queries: list[PendingQueryItem] = []
    for r in rows:
        email = email_by_uid.get(r.user_id, "")
        try:
            seed_list = json.loads(r.seed_prompts_json or "[]")
        except Exception:  # noqa: BLE001
            seed_list = []
        for i, s in enumerate(seed_list):
            if isinstance(s, dict) and s.get("status") == "pending" and s.get("text"):
                seeds.append(PendingSeedItem(
                    topic_id=r.id, topic_name=r.name, target=r.target or "",
                    user_id=r.user_id, user_email=email,
                    idx=i, text=s["text"], submitted_at=s.get("submitted_at"),
                ))
        try:
            q_list = json.loads(r.queries_json or "[]")
        except Exception:  # noqa: BLE001
            q_list = []
        for i, q in enumerate(q_list):
            if isinstance(q, dict) and q.get("status") == "pending" and q.get("text"):
                queries.append(PendingQueryItem(
                    topic_id=r.id, topic_name=r.name, target=r.target or "",
                    user_id=r.user_id, user_email=email,
                    idx=i, text=q["text"],
                    cluster_id=q.get("cluster_id") if isinstance(q.get("cluster_id"), int) else None,
                    submitted_at=q.get("submitted_at"),
                ))
    return PendingOut(seed_prompts=seeds, queries=queries)


def _load_topic_or_404(db: Session, topic_id: int) -> AiTelemetryTopicORM:
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t:
        raise HTTPException(404, "topic not found")
    return t


# Phase C sub-item review endpoints (approve/reject seed + queries) were retired in the review-free flow.
# Projects created directly by admin land as approved, so no per-item approval channel is needed. Helpers are kept for PATCH.


# ═════════════════ Phase D: whole application review ═════════════════


# Value domain of project progress stage states: keep consistent with StageState in frontend AdminCockpit.tsx.
# 5 states in total: done / running / pending / blocked / idle.
StageState = str

# 2026-09-10: **stage states are derived from facts, not from the approval flag**.
#
# Each of these four functions used to start with `if not approved: return "idle"`, where approved came from
# `submission_status == "approved"`. So stages 3~6 of draft topics were unconditionally shown as "not started",
# even if they had actually run. Topics 31/32 are live examples: two batch runs dispatched 2714 cells with 2490 succeeding, yet the card
# showed "1/6, five not started", and **this would never resolve**: queries are no longer reviewed (see
# api/ai_telemetry.update_topic), so nobody will ever approve these two topics.
#
# And this gate was fake to begin with: drip dispatching only checks enabled + having queries and engines, and never looks at
# submission_status (scheduler._run_drip). Draft topics ran regardless; only the **display** was blocked,
# so the UI disagreed with reality: users saw "not started" while the backend was burning engine capacity.
#
# Now each stage looks at its own evidence: diagnosis checks for a solution, strategy for a plan,
# content for drafts, effect for whether it has run. Stages 1/2 (submit/review) are still derived from submission_status:
# those two stages are about approval by nature and were left untouched.

def _diagnose_state(sol: Optional[AiTelemetryTopicSolutionORM]) -> StageState:
    """Health diagnosis report: TopicSolution table (one per topic)."""
    if sol is None:
        return "idle"
    s = (sol.status or "").lower()
    return {"ready": "done", "generating": "running", "failed": "blocked"}.get(s, "idle")


def _plan_state(plan: Optional[AiTelemetryTopicExecutionPlanORM]) -> StageState:
    """Execution strategy and planning: TopicExecutionPlan (latest one). Created as draft when the project starts,
    and becomes confirmed after ops edits the publishing table in the editor and confirms."""
    if plan is None:
        return "idle"
    s = (plan.status or "").lower()
    # draft = being edited, shown by the frontend as pending (waiting for ops); confirmed = done.
    return {
        "draft": "pending", "confirmed": "done", "failed": "blocked",
        # Compatibility with old values
        "ready": "done", "generating": "running",
    }.get(s, "idle")


def _content_state(agg: dict[str, int]) -> StageState:
    """Content publishing and review: TopicGeneratedDoc aggregated by status.

    agg = {"draft": n, "pending_review": n, "approved": n, "rejected": n, "published": n}.
    Priority: published > awaiting admin action (pending review / approved but not marked published) > draft only (LLM generating)
    > all rejected > no drafts.
    """
    total = sum(agg.values())
    if total == 0:
        return "idle"
    if agg.get("published", 0) > 0:
        return "done"
    if agg.get("pending_review", 0) > 0 or agg.get("approved", 0) > 0:
        return "pending"  # Waiting for admin review / admin to mark published
    if agg.get("draft", 0) > 0:
        return "running"  # LLM still generating drafts
    return "blocked"      # The only remaining case: all rejected


def _insight_state(last_status: Optional[str], last_at) -> StageState:
    """Effect verification and updates: read last_run_status / last_run_at from the topic table directly, avoiding N+1."""
    if last_at is None:
        return "idle"
    s = (last_status or "").lower()
    return {"success": "done", "running": "running", "failed": "blocked"}.get(s, "idle")


class TopicReviewListItem(BaseModel):
    topic_id: int
    topic_name: str
    user_id: int
    user_email: str
    submission_status: str
    submitted_at: Optional[datetime]
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    profile_name: str
    company_short_name: str
    industry: str
    seed_count: int
    selected_query_count: int
    version: int = 1                          # Revision number, aligned with TopicOut.version
    # Project progress stage 3-6 states (stage 1 is always done, stage 2 is derived from submission_status).
    diagnose_status: StageState = "idle"
    plan_status: StageState = "idle"
    content_status: StageState = "idle"
    insight_status: StageState = "idle"


class TopicReviewDetailOut(TopicOut):
    # user_id is provided by TopicOut (added during the agency workspace overhaul); do **not** declare it again here:
    # both places below construct TopicReviewDetailOut(**base.model_dump(), ...),
    # and base already carries user_id, so adding an explicit user_id= gives a "multiple values" 500.
    user_email: str = ""
    topic_changelog: list[TopicChangelogEntry] = Field(default_factory=list)
    expansion_log: list[ExpansionLogEntry] = Field(default_factory=list)


def _parse_log_list(raw: str | None) -> list[dict]:
    """Parse the topic_changelog / expansion_log JSON arrays, returning list[dict].

    Historical changelogs store before/after as list/dict when list-type profile fields (e.g. core_business_lines) are rewritten,
    while the TopicChangelogEntry model expects Optional[str].
    Here non-str before/after values are uniformly serialized to str, avoiding downstream Pydantic 500s.
    """
    try:
        arr = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for x in arr:
        if not isinstance(x, dict):
            continue
        for k in ("before", "after"):
            v = x.get(k)
            if v is not None and not isinstance(v, str):
                x[k] = json.dumps(v, ensure_ascii=False)
        out.append(x)
    return out


def build_topic_review_items(
    db: Session, rows: list[AiTelemetryTopicORM]
) -> list[TopicReviewListItem]:
    """Convert a batch of topic rows into project progress list items (including bulk prefetch of stage 3-6 states).

    The admin platform-wide list and the user self-service list share this logic: the only difference is the scope of rows
    passed in (whole table vs filtered by user_id), isolated in the callers, so the two stage computations do not drift apart.
    """
    user_ids = {r.user_id for r in rows}
    email_by_uid: dict[int, str] = {}
    if user_ids:
        for u in db.query(UserORM).filter(UserORM.id.in_(user_ids)).all():
            email_by_uid[u.id] = u.email

    # Bulk prefetch for project progress stages 3-6 (all fetched at once via topic_id IN (...)).
    topic_ids = [r.id for r in rows]
    sol_by_tid: dict[int, AiTelemetryTopicSolutionORM] = {}
    plan_by_tid: dict[int, AiTelemetryTopicExecutionPlanORM] = {}
    doc_agg_by_tid: dict[int, dict[str, int]] = {}
    if topic_ids:
        for s in db.query(AiTelemetryTopicSolutionORM).filter(
            AiTelemetryTopicSolutionORM.topic_id.in_(topic_ids)
        ).all():
            sol_by_tid[s.topic_id] = s   # The unique constraint guarantees at most one per topic
        # There may be several execution plans (re-approval generates another); take the latest by id desc
        for p in db.query(AiTelemetryTopicExecutionPlanORM).filter(
            AiTelemetryTopicExecutionPlanORM.topic_id.in_(topic_ids)
        ).order_by(AiTelemetryTopicExecutionPlanORM.id.desc()).all():
            plan_by_tid.setdefault(p.topic_id, p)
        # Aggregate drafts by (topic_id, status), pulling only two columns to avoid dragging out the huge body_markdown text
        for tid, st in db.query(
            TopicGeneratedDocORM.topic_id, TopicGeneratedDocORM.status
        ).filter(TopicGeneratedDocORM.topic_id.in_(topic_ids)).all():
            doc_agg_by_tid.setdefault(tid, {}).setdefault(st or "", 0)
            doc_agg_by_tid[tid][st or ""] += 1

    out: list[TopicReviewListItem] = []
    for r in rows:
        try:
            profile = json.loads(r.profile_json or "{}")
        except Exception:  # noqa: BLE001
            profile = {}
        if not isinstance(profile, dict):
            profile = {}
        try:
            seeds = json.loads(r.seed_prompts_json or "[]")
        except Exception:  # noqa: BLE001
            seeds = []
        seed_count = sum(1 for s in seeds if isinstance(s, dict) and s.get("text"))
        try:
            qarr = json.loads(r.queries_json or "[]")
        except Exception:  # noqa: BLE001
            qarr = []
        sel_n = sum(
            1 for q in qarr
            if isinstance(q, dict) and q.get("text") and q.get("selected", True)
        )
        out.append(TopicReviewListItem(
            topic_id=r.id, topic_name=r.name,
            user_id=r.user_id, user_email=email_by_uid.get(r.user_id, ""),
            submission_status=r.submission_status or "draft",
            submitted_at=r.submitted_at,
            approved_at=r.approved_at, rejected_at=r.rejected_at,
            profile_name=str(profile.get("profile_name") or ""),
            company_short_name=str(profile.get("company_short_name") or ""),
            industry=str(profile.get("industry") or r.industry or ""),
            seed_count=seed_count,
            selected_query_count=sel_n,
            version=int(getattr(r, "version", 1) or 1),
            diagnose_status=_diagnose_state(sol_by_tid.get(r.id)),
            plan_status=_plan_state(plan_by_tid.get(r.id)),
            content_status=_content_state(doc_agg_by_tid.get(r.id, {})),
            insight_status=_insight_state(r.last_run_status, r.last_run_at),
        ))
    out.sort(key=lambda x: (x.submitted_at or datetime.min, x.topic_id), reverse=True)
    return out


@router.get("/topics", response_model=list[TopicReviewListItem])
def list_topic_reviews(
    status: Optional[str] = None,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Platform-wide topic application list; status filter: draft / pending / approved / rejected.

    Sorted by submitted_at desc by default; rows without submitted_at fall back to created_at desc.
    """
    q = db.query(AiTelemetryTopicORM)
    if status:
        q = q.filter(AiTelemetryTopicORM.submission_status == status)
    return build_topic_review_items(db, q.all())


# User self-service version: with the workspace opened externally, customers see only their own project progress on the "topic info" page.
# Separate router (prefix=/topics) mounted on /api -> /api/topics/mine/reviews; gated by get_current_user
# + user_id filter, reusing build_topic_review_items, consistent with the admin platform-wide list's metric definition.
self_router = APIRouter(prefix="/topics")


def _require_topic_access(db: Session, topic_id: int, user, action: str = ACTION_READ):
    """Topic deliverables are allowed by enterprise data scope.

    Used for read-only views reached via deep links from the "topic info" page stepper into solution / execution-plan;
    these GETs were admin-only, and relaxing them to owners lets customers see their own projects' deliverables.

    `action=ACTION_WRITE` is for "start project": the customer presses start themselves; it is their own project.
    The step that actually spends money producing articles (execution-plan/confirm) is still admin-only and not opened up.
    """
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise resource_not_found()
    require_resource_access(db, user, t, action)
    return t


@self_router.get("/mine/reviews", response_model=list[TopicReviewListItem])
def list_my_topic_reviews(
    status: Optional[str] = None,
    enterprise_user_id: int | None = None,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Project progress list of enterprises accessible to the current logged-in user."""
    q = apply_enterprise_scope(
        db.query(AiTelemetryTopicORM), db, current_user, AiTelemetryTopicORM.user_id,
        enterprise_user_id,
    )
    if status:
        q = q.filter(AiTelemetryTopicORM.submission_status == status)
    return build_topic_review_items(db, q.all())


@router.get("/topic/{topic_id}", response_model=TopicReviewDetailOut)
def get_topic_review_detail(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Topic details (including changelog / query expansion log). **owner or staff**: same metric definition as other
    deliverable GETs in this file (see the notes on _require_topic_access).

    The direct reason for opening it up: the start-project dialog relies on it to list "covered query" candidates. Customers can now start projects too,
    and if it still blocked them as admin-only, opening the dialog would show an empty covered-query table: no error,
    just a silently missing piece, harder to track down than an error. Everything returned belongs to their own topic."""
    t = _require_topic_access(db, topic_id, current_user)
    base = TopicOut.from_orm_row(t)
    user = db.get(UserORM, t.user_id)
    out = TopicReviewDetailOut(
        **base.model_dump(),                       # user_id is already included
        user_email=(user.email if user else ""),
        topic_changelog=[
            TopicChangelogEntry(**e) for e in _parse_log_list(t.topic_changelog_json)
            if "at" in e
        ],
        expansion_log=[
            ExpansionLogEntry(**e) for e in _parse_log_list(t.expansion_log_json)
            if "at" in e
        ],
    )
    return out


class AdminPatchTopicPayload(BaseModel):
    """Admin edits any of profile / seeds / queries; omitted fields = unchanged."""
    profile: Optional[BrandProfile] = None
    seed_prompts: Optional[list[str]] = None         # If given, replaces the seed list with this one (set of texts)
    selected_query_texts: Optional[list[str]] = None  # If given, set selected True for these texts and False for the rest
    add_queries: Optional[list[str]] = None           # Append query candidates (status=approved, directly selectable)
    note: Optional[str] = Field(None, max_length=500)


@router.patch("/topic/{topic_id}", response_model=TopicReviewDetailOut)
def admin_patch_topic(
    topic_id: int,
    payload: AdminPatchTopicPayload,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Admin edits the application. The changelog is appended throughout, actor_role=admin.
    Any submission_status is allowed: admin has final review and can correct
    profile/seeds/prompts even after approved (already generated drafts are not rerun automatically; trigger manually).
    """
    t = _load_topic_or_404(db, topic_id)

    actor_id = admin.id

    if payload.profile is not None:
        # Must use merge_profile_json: BrandProfile has no `_admin` section, and json.dumps directly
        # would wipe the admin-configured fixed answer denominator / share-of-voice metric definition, silently reverting reports to the actual denominator.
        # (The other five places writing profile_json have always used merge; only this one was missed.)
        t.profile_json = merge_profile_json(t, payload.profile.model_dump())
        if payload.profile.industry:
            t.industry = payload.profile.industry
        if payload.profile.company_short_name:
            t.target = payload.profile.company_short_name
        if payload.profile.profile_name:
            t.name = payload.profile.profile_name
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="profile",
                          after=payload.profile.profile_name or payload.profile.company_short_name)
        _sync_sales_brand(db, t)

    if payload.seed_prompts is not None:
        cleaned = [s.strip() for s in payload.seed_prompts if s and s.strip()]
        now_iso = datetime.utcnow().isoformat()
        try:
            existing = json.loads(t.seed_prompts_json or "[]")
        except Exception:  # noqa: BLE001
            existing = []
        existing_by_text = {
            s["text"]: s for s in existing
            if isinstance(s, dict) and s.get("text")
        }
        new_list = []
        for text in cleaned:
            if text in existing_by_text:
                new_list.append(existing_by_text[text])
            else:
                new_list.append({
                    "text": text, "status": "approved",
                    "submitted_at": now_iso, "approved_at": now_iso,
                    "reviewer_id": actor_id,
                })
        t.seed_prompts_json = json.dumps(new_list, ensure_ascii=False)
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="seed_prompts",
                          after=f"count={len(new_list)}")

        # 2026-05-26: each approved seed automatically derives a same-named query (is_seed=True, locked=True).
        # Existing same-named query -> upgrade its flags; none -> add one. This way:
        #   - seed prompts get their own row in the monitoring list and can be filtered separately
        #   - locked=True makes the frontend picker unable to untick it, guaranteeing seeds are always monitored
        #   - shares the same ai_telemetry_responses with expanded queries, no re-run needed
        approved_texts = [s["text"] for s in new_list
                          if isinstance(s, dict) and s.get("status") == "approved" and s.get("text")]
        if approved_texts:
            try:
                qarr = json.loads(t.queries_json or "[]")
            except Exception:  # noqa: BLE001
                qarr = []
            q_by_text = {q.get("text"): q for q in qarr if isinstance(q, dict)}
            seed_added = 0
            seed_marked = 0
            for txt in approved_texts:
                if txt in q_by_text:
                    q = q_by_text[txt]
                    if not q.get("is_seed"):
                        q["is_seed"] = True
                        q["locked"] = True
                        q["selected"] = True
                        q["seed"] = txt
                        seed_marked += 1
                else:
                    qarr.append({
                        "text": txt, "status": "approved",
                        "submitted_at": now_iso, "approved_at": now_iso,
                        "reviewer_id": actor_id,
                        "seed": txt, "is_seed": True, "locked": True,
                        "selected": True, "cluster_id": -1,
                    })
                    seed_added += 1
            if seed_added or seed_marked:
                t.queries_json = json.dumps(qarr, ensure_ascii=False)
                _append_changelog(t, actor_id=actor_id, actor_role="admin",
                                  field="queries.is_seed",
                                  after=f"seed_added={seed_added}, seed_marked={seed_marked}")

    if payload.add_queries:
        try:
            qarr = json.loads(t.queries_json or "[]")
        except Exception:  # noqa: BLE001
            qarr = []
        existing_q_texts = {
            q["text"] for q in qarr
            if isinstance(q, dict) and q.get("text")
        }
        now_iso = datetime.utcnow().isoformat()
        added = 0
        for text in payload.add_queries:
            text = (text or "").strip()
            if not text or text in existing_q_texts:
                continue
            qarr.append({
                "text": text, "status": "approved",
                "submitted_at": now_iso, "approved_at": now_iso,
                "reviewer_id": actor_id, "selected": False,
            })
            existing_q_texts.add(text)
            added += 1
        t.queries_json = json.dumps(qarr, ensure_ascii=False)
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="queries", after=f"added={added}")

    if payload.selected_query_texts is not None:
        desired = {s.strip() for s in payload.selected_query_texts if s and s.strip()}
        if len(desired) > MAX_SELECTED_QUERIES:
            raise HTTPException(422, {
                "code": "TOO_MANY_SELECTED",
                "message": f"最多 {MAX_SELECTED_QUERIES} 个提示词,当前 {len(desired)}",
            })
        try:
            qarr = json.loads(t.queries_json or "[]")
        except Exception:  # noqa: BLE001
            qarr = []
        upgraded: list[dict] = []
        for q in qarr:
            if isinstance(q, str):
                upgraded.append({"text": q, "status": "approved", "selected": q in desired})
            elif isinstance(q, dict) and q.get("text"):
                qq = dict(q)
                qq["selected"] = qq["text"] in desired
                upgraded.append(qq)
        # Add those in desired that are not in qarr (admin directly picked new text for monitoring)
        existing_texts = {q["text"] for q in upgraded}
        for text in desired - existing_texts:
            upgraded.append({
                "text": text, "status": "approved",
                "selected": True, "approved_at": datetime.utcnow().isoformat(),
                "reviewer_id": actor_id,
            })
        t.queries_json = json.dumps(upgraded, ensure_ascii=False)
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="selected_queries",
                          after=f"selected_count={len(desired)}")

    if payload.note:
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="note", note=payload.note)

    db.commit()
    db.refresh(t)

    user = db.get(UserORM, t.user_id)
    base = TopicOut.from_orm_row(t)
    return TopicReviewDetailOut(
        **base.model_dump(),                       # Same as above, user_id is already included
        user_email=(user.email if user else ""),
        topic_changelog=[
            TopicChangelogEntry(**e) for e in _parse_log_list(t.topic_changelog_json) if "at" in e
        ],
        expansion_log=[
            ExpansionLogEntry(**e) for e in _parse_log_list(t.expansion_log_json) if "at" in e
        ],
    )


def _send_review_email_safe(*, to: str, topic_name: str, decision: str,
                             reject_reason: str | None = None,
                             execution_plan_url: str | None = None) -> None:
    """Send the review result email; failures do not raise (a broken email service should not block review)."""
    if not to:
        log.warning("review email skipped: no recipient for topic '%s'", topic_name)
        return
    try:
        from geo.services.email_service import email_service
        email_service.send_review_result_email(
            to=to, topic_name=topic_name, decision=decision,
            reject_reason=reject_reason, execution_plan_url=execution_plan_url,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("review email failed: %s", e)


def _trigger_run_topic_sync(
    topic_id: int, operation_context_id: int | None = None,
) -> Optional[int]:
    """Synchronously trigger telemetry-service /run-topic, returning run_id (None on failure).

    seed_only=True: baseline audits triggered by start/rerun only run seed prompts (queries with is_seed),
    leaving expanded questions to periodic batch runs; when the topic has no is_seed marks (old data) telemetry falls back to everything.
    """
    url = f"{TELEMETRY_SERVICE_URL}/run-topic"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, headers=internal_headers(), json={
                "topic_id": topic_id,
                "seed_only": True,
                "operation_context_id": operation_context_id,
            })
            r.raise_for_status()
            data = r.json()
            run_id = data.get("run_id") if isinstance(data, dict) else None
            return int(run_id) if isinstance(run_id, int) else None
    except Exception as e:  # noqa: BLE001
        log.warning("trigger run failed for topic %d: %s", topic_id, e)
        return None


def _build_execution_plan_snapshot(t: AiTelemetryTopicORM, run_id: int | None) -> dict:
    """Snapshot at the moment the ExecutionPlan is persisted on approval."""
    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    # Soft-deleted queries do not enter the snapshot: the snapshot is the vocabulary this plan uses from now on, and anything in it keeps getting scheduled for publishing
    monitored = [
        q["text"] for q in qarr
        if isinstance(q, dict) and q.get("text") and q.get("selected", True)
        and q.get("status") == "approved" and not q.get("deleted_at")
    ]
    # The baseline audit (seed_only) only runs the original seed queries; the estimated cell count is computed from seeds, and old data without seed marks falls back to everything
    seed_monitored = [
        q["text"] for q in qarr
        if isinstance(q, dict) and q.get("text") and q.get("selected", True)
        and q.get("status") == "approved" and q.get("is_seed") and not q.get("deleted_at")
    ]
    try:
        profile = json.loads(t.profile_json or "{}")
    except Exception:  # noqa: BLE001
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    try:
        engines = json.loads(t.engines_json or "[]")
    except Exception:  # noqa: BLE001
        engines = []
    overview = {
        "topic_id": t.id,
        "topic_name": t.name,
        "company_short_name": profile.get("company_short_name") or t.target,
        "industry": profile.get("industry") or t.industry,
        "service_geo": profile.get("service_geo") or "",
        "monitored_queries_count": len(monitored),
        "seed_queries_count": len(seed_monitored),
        "engines": engines,
        "engines_count": len(engines),
        "estimated_cells": len(seed_monitored or monitored) * len(engines),
        "snapshot_at": datetime.utcnow().isoformat(),
        "approved_by_run_id": run_id,
    }
    return {
        "overview": overview,
        "monitored_queries": monitored,
        "changelog": _parse_log_list(t.topic_changelog_json),
        "expansion_log": _parse_log_list(t.expansion_log_json),
    }


def _schedule_content_generation(
    topic_id: int, plan_id: int,
    *, plan_item_ids: Optional[list[str]] = None,
    origin_user_id: Optional[int] = None, origin_ip: str = "",
    trigger_source: str = "unknown",
) -> None:
    """Asynchronously trigger content copy generation. Supports running single items via plan_item_ids; None runs the whole table.

    origin_* is the trigger source for content provenance (implicit marking under the Labeling Measures). Values must be **passed explicitly** here rather than
    read from the contextvar directly: this function runs in BackgroundTasks, the endpoint scope has long exited,
    and the request context in the contextvar has been reset; only values copied and passed in are available.
    """
    try:
        from geo.services import content_marking
        from geo.services.content_generator import schedule_generation
        operation_context_id = None
        if origin_user_id is not None:
            from geo.database import SessionLocal

            context_db = SessionLocal()
            try:
                topic = context_db.get(AiTelemetryTopicORM, topic_id)
                actor = context_db.get(UserORM, origin_user_id)
                if topic is not None and actor is not None:
                    _, operation = create_resource_operation_context(
                        context_db,
                        actor,
                        topic,
                        action="content.generate.create",
                        target_resource_type="topic",
                        target_resource_id=topic.id,
                    )
                    append_system_audit(
                        context_db,
                        operation_context=operation,
                        event_type="task_created",
                        action="content.generate.create",
                        object_type="topic",
                        object_id=topic.id,
                        result="success",
                    )
                    context_db.commit()
                    operation_context_id = operation.id
            finally:
                context_db.close()
        with content_marking.gen_origin(user_id=origin_user_id, client_ip=origin_ip):
            schedule_generation(
                topic_id=topic_id, plan_id=plan_id,
                plan_item_ids=plan_item_ids,
                trigger_source=trigger_source,
                operation_context_id=operation_context_id,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("schedule content generation failed: %s", e)


def _recent_success_run_id(db: Session, topic_id: int,
                           within_days: int = BASELINE_REUSE_DAYS) -> Optional[int]:
    """The most recent successful batch run, or None. Reused as the baseline by "start project".

    Why reuse is fine: on this path plan.run_id is **only a gate**: `_to_plan_out` waits for it to be
    `success` before laying out the publishing table. What actually decides placement platforms is `services/source_plan.py`, and that file
    never mentions run_id; it aggregates all citations by topic. So for a topic already running,
    another seed_only baseline round adds not a single byte of information to the quotas; it just charges again and makes the user wait
    another round, and the page would show two conflicting sentences, "monitoring is already running" and "a baseline audit batch runs first after starting".

    Cases where it still runs: the topic has never run successfully (new topic), or the latest successful batch run is older than
    `within_days` days (data too old; placement platforms should follow fresh measured sources); and the ?force=true
    "restart", whose semantics are "I want a new round" to begin with.
    """
    since = datetime.utcnow() - timedelta(days=max(1, int(within_days)))
    row = (db.query(AiTelemetryRunORM.id)
             .filter(AiTelemetryRunORM.topic_id == topic_id,
                     AiTelemetryRunORM.status == "success",
                     AiTelemetryRunORM.started_at >= since)
             .order_by(AiTelemetryRunORM.id.desc())
             .first())
    return int(row[0]) if row else None


def _create_draft_plan(
    db: Session,
    t: AiTelemetryTopicORM,
    actor_id: int,
    background: BackgroundTasks,
    *,
    actor_is_staff: bool = True,
    reuse_recent_run: bool = False,
) -> AiTelemetryTopicExecutionPlanORM:
    """Side effects when starting a project: run monitoring + persist the draft plan + send email. Does **not** trigger article generation.

    Article generation moved to _confirm_plan / regenerate-item.
    Precondition: t's status change has been db.commit()-ed (to avoid the SQLite write lock blocking telemetry-service).
    """
    topic_id = t.id
    actor = db.get(UserORM, actor_id)
    _, operation = create_resource_operation_context(
        db,
        actor,
        t,
        action="telemetry.baseline.create",
        target_resource_type="topic",
        target_resource_id=t.id,
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="telemetry.baseline.create",
        object_type="topic",
        object_id=t.id,
        result="success",
    )
    db.commit()
    run_id = _recent_success_run_id(db, topic_id) if reuse_recent_run else None
    if run_id is not None:
        log.info("[plan] topic=%d reusing the most recent successful batch run run=%d as the baseline; not triggering a new one",
                 topic_id, run_id)
    else:
        run_id = _trigger_run_topic_sync(topic_id, operation.id)
    snapshot = _build_execution_plan_snapshot(t, run_id)
    plan = AiTelemetryTopicExecutionPlanORM(
        topic_id=topic_id,
        generated_by_reviewer_id=actor_id,
        overview_json=json.dumps(snapshot["overview"], ensure_ascii=False),
        topic_changelog_snapshot_json=json.dumps(snapshot["changelog"], ensure_ascii=False),
        expansion_log_snapshot_json=json.dumps(snapshot["expansion_log"], ensure_ascii=False),
        monitored_queries_snapshot_json=json.dumps(snapshot["monitored_queries"], ensure_ascii=False),
        publishing_plan_json="[]",
        run_id=run_id,
        # If triggering monitoring fails, go straight to failed; if it succeeds -> enter draft and wait for ops to edit the publishing table
        status="draft" if run_id is not None else "failed",
        error=None if run_id is not None else "telemetry-service /run-topic failed",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    user = db.get(UserORM, t.user_id)
    plan_url = None
    try:
        from geo.database import settings as _s
        base = (_s.FRONTEND_URL or "").rstrip("/")
        # The recipient is the topic owner (the customer), not ops. This used to always be
        # /workbench/topics/{id}/execution-plan, the admin workspace URL, which customers cannot open.
        # The admin workspace link only makes sense when staff start it themselves; customers always get their own page.
        # The customer-side plan has no dedicated route: it is step 5 of the wizard in /app/topics
        # (CustomerTopics switches steps via in-page state, not the URL), so it can only land on the list page.
        plan_url = (f"{base}/ops/topics/{topic_id}/edit?step=5" if actor_is_staff
                    else f"{base}/app/topics")
    except Exception:  # noqa: BLE001
        pass

    if user and user.email:
        background.add_task(_send_review_email_safe,
                            to=user.email, topic_name=t.name,
                            decision="approved", execution_plan_url=plan_url)
    return plan


def _kick_content_generation(
    plan: AiTelemetryTopicExecutionPlanORM,
    background: BackgroundTasks,
    *, plan_item_ids: Optional[list[str]] = None,
    request=None, actor=None, trigger_source: str = "unknown",
) -> None:
    """Dispatch article generation to the background. Shared by confirm / regenerate-item.

    request / actor are only used for content provenance (recording "who triggered this generation batch from which IP").
    When both are omitted, the generator in provenance falls back to the topic owner and the IP is recorded as 'system'.
    """
    from geo.services import content_marking

    background.add_task(
        _schedule_content_generation,
        topic_id=plan.topic_id, plan_id=plan.id,
        plan_item_ids=plan_item_ids,
        origin_user_id=getattr(actor, "id", None),
        origin_ip=content_marking.client_ip(request),
        trigger_source=trigger_source,
    )


def _post_approval_pipeline(
    db: Session,
    t: AiTelemetryTopicORM,
    actor_id: int,
    background: BackgroundTasks,
) -> AiTelemetryTopicExecutionPlanORM:
    """[Retired entry point] Kept only for compatibility; actually still only goes through _create_draft_plan.

    In the old approve_topic / rerun semantics it would trigger article generation immediately; the new flow requires
    ops to confirm before generating, so this **no longer** kicks the content generator automatically.
    """
    return _create_draft_plan(db, t, actor_id, background)


# approve_topic / reject_topic were retired in the review-free flow. Their function is now handled by POST /admin/topics/{id}/start
# (see the start_topic endpoint in this file). rerun_topic and _post_approval_pipeline remain, reused for restarts.


# ───────────── Execution plan: fetch + run progress ─────────────


DEFAULT_PUBLISH_PLATFORMS = ["公众号", "小红书", "抖音", "视频号"]

# The publishing plan is "continuously rolling": each batch covers at most one month (~30 days), and the scheduled job schedules the next month near the end.
PLAN_WINDOW_DAYS = int(os.environ.get("GEO_PLAN_WINDOW_DAYS", "30"))
# When the plan's end date is within this many days of today, runway is insufficient and it is renewed automatically (see extend_plan_one_month).
PLAN_REFILL_LEAD_DAYS = int(os.environ.get("GEO_PLAN_REFILL_LEAD_DAYS", "7"))


def _pick_default_template_id(db: Session) -> Optional[int]:
    """Pick a default template for a new draft's seed rows: prefer system scope, earliest one."""
    t = (
        db.query(ContentTemplateORM)
          .filter(ContentTemplateORM.scope == "system")
          .order_by(ContentTemplateORM.id.asc())
          .first()
    )
    return t.id if t else None


# Primary kind for GEO content placement: placed first in the seed x template list, prioritizing "industry roundup · recommendation ranking".
# See the system template injected by migration c5f4a8b9e3d2_add_industry_listicle_template.py.
_PRIMARY_TEMPLATE_KIND = "榜单合辑"


# Template kinds excluded from automatic scheduling: English templates only apply when template_ids are specified manually in the plan editor,
# otherwise Chinese topics would get English drafts scheduled.
_AUTO_PLAN_EXCLUDED_KINDS = {"英文长文", "英文榜单"}
# Size cap of the automatic scheduling template pool (distinct kinds). The pool is larger than the daily count: still _MAX_PER_DAY per day,
# but rotated by day, so every template in the pool gets used across days and topics are not forever limited to the first few.
_TEMPLATE_POOL_SIZE = 12
_MAX_PER_DAY = 5


def _pick_template_kinds_per_seed(db: Session, n: int = _TEMPLATE_POOL_SIZE) -> list[ContentTemplateORM]:
    """Pick N system-scope templates with distinct kinds, for the seed x template publishing table.

    Order: _PRIMARY_TEMPLATE_KIND (ranking roundup) first -> others by id ascending.
    Only one per kind (among several system templates of the same kind, take the first by id).
    Returns a shorter list when system templates cover fewer than n kinds; returns [] when empty, and the caller falls back to legacy.
    """
    sys_templates = [
        t for t in (
            db.query(ContentTemplateORM)
              .filter(ContentTemplateORM.scope == "system")
              .order_by(ContentTemplateORM.id.asc())
              .all()
        )
        if (t.kind or "").strip() not in _AUTO_PLAN_EXCLUDED_KINDS
    ]
    # Move the primary kind to the front
    primary = [t for t in sys_templates if t.kind == _PRIMARY_TEMPLATE_KIND]
    others = [t for t in sys_templates if t.kind != _PRIMARY_TEMPLATE_KIND]
    ordered = primary + others

    seen_kinds: set[str] = set()
    picked: list[ContentTemplateORM] = []
    for tmpl in ordered:
        if tmpl.kind in seen_kinds:
            continue
        seen_kinds.add(tmpl.kind)
        picked.append(tmpl)
        if len(picked) >= n:
            break
    return picked


def _legacy_slot(platform_slots: Optional[list], i: int, default_platform: str) -> dict:
    """Placement fields of legacy rows: a slot may be a platform name string, or a dict with channel/candidate resources."""
    if not platform_slots:
        return {"platform": default_platform}
    s = platform_slots[i % len(platform_slots)]
    if isinstance(s, str):
        return {"platform": s}
    out = {"platform": s.get("platform") or default_platform}
    if s.get("channel"):
        out["channel"] = s["channel"]
    cands = s.get("candidates") or []
    if cands:
        pick = cands[i % len(cands)]
        # Candidates are ranked across Ruanwenjiang/Meijieku together, so the channel must follow the selected one: numbers are not shared across sites
        out["channel"] = pick.get("provider") or out.get("channel") or "rwj"
        out["rwj_resource_id"] = pick["resource_id"]
        out["media"] = pick["title"]
    return out


def _active_only(db: Session, topic_id: int, texts: list[str]) -> list[str]:
    """Filter the vocabulary down to "not soft-deleted" (order-preserving dedup). Plan scheduling / query selection always go through this first.

    It is a separate function because callers get monitored from all sorts of sources: some fetch queries_json fresh,
    some take the plan's frozen monitored_queries_snapshot_json, which is not updated on soft delete.
    """
    from geo.models.ai_telemetry import deleted_query_texts

    t = db.get(AiTelemetryTopicORM, topic_id)
    gone = deleted_query_texts(t) if t is not None else frozenset()
    return [q for q in texts if q not in gone] if gone else list(texts)


def _legacy_query_window(
    db: Session, topic_id: int, monitored: list[str],
    *, start_date, days: int, start_seq: int = 0,
    platform_slots: Optional[list] = None,
) -> list[dict]:
    """Legacy fallback: sort by query hit rate and cycle through [start_date, start_date+days), 1 article per day.

    Falls back here when the new version has no system templates / no approved seeds.
    When there are fewer queries than days, they are reused in a cycle (high-priority ones at the front first).
    """
    import uuid as _uuid
    monitored = _active_only(db, topic_id, monitored)     # ★ Soft-deleted queries are not scheduled into the publishing plan
    cells = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    by_query: dict[str, dict] = {}
    for c in cells:
        if c.query not in monitored:
            continue
        agg = by_query.setdefault(c.query, {"runs": 0, "hits": 0})
        agg["runs"] += c.total_runs or 0
        agg["hits"] += c.total_hits or 0

    def _priority_rank(q: str) -> int:
        agg = by_query.get(q, {"runs": 0, "hits": 0})
        if agg["runs"] <= 0:
            return 0
        cov = agg["hits"] / agg["runs"] * 100
        if cov == 0: return 0
        if cov < 50: return 1
        return 2

    sorted_queries = sorted(monitored, key=lambda q: (_priority_rank(q), q))
    if not sorted_queries:
        return []

    default_tmpl_id = _pick_default_template_id(db)
    default_tmpl = db.get(ContentTemplateORM, default_tmpl_id) if default_tmpl_id else None
    default_platform: Optional[str] = None
    if default_tmpl:
        try:
            platforms = json.loads(default_tmpl.target_platforms_json or "[]")
        except Exception:  # noqa: BLE001
            platforms = []
        if isinstance(platforms, list) and platforms:
            default_platform = str(platforms[0])
    if not default_platform:
        default_platform = DEFAULT_PUBLISH_PLATFORMS[0]

    items: list[dict] = []
    for day_offset in range(days):
        q = sorted_queries[day_offset % len(sorted_queries)]
        items.append({
            "id": str(_uuid.uuid4()),
            "seq": start_seq + day_offset,
            "publish_date": (start_date + timedelta(days=day_offset)).isoformat(),
            "seed": None,
            "query": q,
            "template_id": default_tmpl_id,
            # The fallback path also uses source quotas: projects without seeds/templates need placement by measured sources even more
            **_legacy_slot(platform_slots, day_offset, default_platform),
            "note": None,
            # The old path also writes a minimal basis, otherwise the frontend would misjudge it as a "legacy row without basis"
            "why": plan_basis.row_why(
                platform={"reason": "legacy_fallback"},
                queries={"reason": "legacy_single_query", "count": 1},
            ),
        })
    return items


# 2026-06-12: the initial plan automatically assigns per-row creative preferences (creative direction x copy type) by template kind,
# instead of leaving them empty and relying on profile defaults (profile defaults are a Cartesian product, exploding one row into several variants).
# Values align with the DIRECTION_HINTS / TYPE_HINTS keys of content_generator.
_KIND_COMBO: dict[str, tuple[str, str]] = {
    "榜单合辑": ("product_review", "long_form"),
    "长文":     ("industry_insight", "long_form"),
    "短文":     ("trend_forecast", "short_social"),
    "FAQ":      ("faq", "faq_list"),
    "问答":     ("faq", "faq_list"),
    "案例":     ("case_story", "medium_post"),
    "对比":     ("product_review", "medium_post"),
    "测评":     ("product_review", "medium_post"),
    "教程":     ("how_to_guide", "medium_post"),
}
_DIR_CYCLE = ["industry_insight", "case_story", "how_to_guide",
              "trend_forecast", "product_review", "customer_story", "faq"]
_TYPE_CYCLE = ["long_form", "medium_post", "short_social",
               "video_script_short", "faq_list"]


def _default_row_combo(kind: str, idx: int) -> tuple[str, str, dict]:
    """Template kind -> (creative direction, copy type, basis); unknown kinds fall back to rotation by idx."""
    k = (kind or "").strip()
    pair = _KIND_COMBO.get(k)
    if pair:
        return pair[0], pair[1], {"reason": "kind_map", "kind": k}
    return (_DIR_CYCLE[idx % len(_DIR_CYCLE)],
            _TYPE_CYCLE[idx % len(_TYPE_CYCLE)],
            {"reason": "rotation"})


def _resolve_plan_templates(
    db: Session, topic_id: int, template_ids: Optional[list[int]] = None,
) -> list[ContentTemplateORM]:
    """Template set for the publishing table: with template_ids, take them in the given order (order-preserving dedup,
    filtering out nonexistent ones / topic-level templates not belonging to this topic); otherwise auto-pick system templates with distinct kinds."""
    if template_ids:
        rows = (
            db.query(ContentTemplateORM)
              .filter(ContentTemplateORM.id.in_(list(set(template_ids))))
              .all()
        )
        by_id = {t.id: t for t in rows}
        seen: set[int] = set()
        picked: list[ContentTemplateORM] = []
        for i in template_ids:
            t = by_id.get(i)
            if t is None or t.id in seen:
                continue
            if t.scope == "topic" and t.topic_id != topic_id:
                continue
            seen.add(t.id)
            picked.append(t)
        if picked:
            return picked
    return _pick_template_kinds_per_seed(db)


def _default_seed_entries(db: Session, topic_id: int, monitored: list[str]) -> list[dict]:
    """Default seed rotation table: the topic's approved seeds + the monitoring queries attached to each. Empty -> []."""
    topic = db.get(AiTelemetryTopicORM, topic_id)
    if not topic:
        return []
    try:
        seeds_raw = json.loads(topic.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds_raw = []
    approved_seeds = [
        str(s.get("text") or "").strip()
        for s in seeds_raw
        if isinstance(s, dict) and s.get("status") == "approved" and s.get("text")
    ]
    if not approved_seeds:
        return []

    # 2026-06-11: seed -> list of selected monitoring queries under that seed (each day's N articles take turns grouping these queries,
    # one article carries several queries, and body generation is asked to cover this group of questions naturally).
    monitored_set = set(monitored)
    seed_to_queries: dict[str, list[str]] = {}
    try:
        qarr = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    for qd in qarr:
        if not (isinstance(qd, dict) and qd.get("text")):
            continue
        if qd["text"] not in monitored_set:
            continue
        seed_to_queries.setdefault(str(qd.get("seed") or ""), []).append(str(qd["text"]))
    return [
        {"seed": s, "queries": seed_to_queries.get(s) or [], "note": None, "origin": None}
        for s in approved_seeds
    ]


def _strategy_seed_entries(db: Session, topic_id: int, monitored: list[str]) -> list[dict]:
    """Strategy plan -> seed rotation table: the keyword system is expanded layer by layer, one keyword per day.

    Prompts are attached to rows where "the question text contains the keyword"; 409 when there is no ready plan / the plan has no keywords.
    """
    sol = (
        db.query(AiTelemetryTopicSolutionORM)
          .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicSolutionORM.id.desc())
          .first()
    )
    if not sol or sol.status != "ready":
        raise HTTPException(409, {
            "code": "NO_STRATEGY",
            "message": "该主题还没有已生成的战略方案,请先在主题页生成战略方案",
        })
    try:
        k = json.loads(sol.keywords_json or "{}")
    except Exception:  # noqa: BLE001
        k = {}
    entries: list[dict] = []
    seen: set[str] = set()
    for tier in (k.get("tiers") or []) if isinstance(k, dict) else []:
        if not isinstance(tier, dict):
            continue
        tier_title = str(tier.get("title_zh") or tier.get("tier") or "").strip()
        for kw in tier.get("keywords") or []:
            kw = str(kw).strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            entries.append({
                "seed": kw,
                "queries": [q for q in monitored if kw in q],
                "note": f"策略方案 · {tier_title}" if tier_title else "策略方案",
                "origin": {"type": "strategy", "tier": str(tier.get("tier") or "")},
            })
    if not entries:
        raise HTTPException(409, {
            "code": "EMPTY_STRATEGY",
            "message": "战略方案里没有可用的关键词,无法按方案生成发文表",
        })
    return entries


def _build_plan_window(
    db: Session, topic_id: int, monitored: list[str],
    *, start_date, days: int, start_seq: int = 0,
    per_day: Optional[int] = None,
    template_ids: Optional[list[int]] = None,
    seed_entries: Optional[list[dict]] = None,
    platform_slots: Optional[list] = None,
) -> list[dict]:
    """Rolling seed x template publishing plan: fills [start_date, start_date+days), N articles per day.

    N = per_day (when unspecified = number of templates, usually 5); templates are reused in rotation when fewer than N. Seeds round-robin by day:
      day 0: seeds[0] x N articles
      day 1: seeds[1] x N articles
      ...   seeds are reused cyclically once exhausted, until all days are filled.

    - template_ids: if given, use these templates in the given order, otherwise auto-pick system templates with distinct kinds
    - seed_entries: external seed rotation table ({seed, queries, note, origin}),
      for "generate from strategy plan" / renewals keeping the original pace; None uses the topic's approved seeds
    - platform_slots: daily platform quota computed from measured sources (length = articles per day);
      if given, platform is set per slot; None falls back to the template's own target_platforms

    Falls back to _legacy_query_window when there are no seeds / no templates.
    Returns the internal storage format of publishing_plan_json (persistent fields only). Each batch is at most days days -> satisfies "at most one month at a time".
    """
    import uuid as _uuid

    # ★ Soft-deleted queries are blocked here once and for all: the monitored passed in often comes from the plan's **frozen**
    #   monitored_queries_snapshot_json, which soft delete does not touch; filtering only _default_seed_entries /
    #   _strategy_seed_entries cannot cover it (the latter is [q for q in monitored if kw in q],
    #   sourced from that stale snapshot). This one place covers first scheduling / renewal / rescheduling / lazy_seed paths.
    monitored = _active_only(db, topic_id, monitored)

    picked_templates = _resolve_plan_templates(db, topic_id, template_ids)
    if not picked_templates:
        return _legacy_query_window(
            db, topic_id, monitored, start_date=start_date, days=days,
            start_seq=start_seq, platform_slots=platform_slots)

    if seed_entries is None:
        seed_entries = _default_seed_entries(db, topic_id, monitored)
    if not seed_entries:
        return _legacy_query_window(
            db, topic_id, monitored, start_date=start_date, days=days,
            start_seq=start_seq, platform_slots=platform_slots)

    # Default platform: the first of the template's own target_platforms, fall back if empty.
    def _default_platform(tmpl: ContentTemplateORM) -> str:
        platforms = _platforms_of(tmpl)
        if platforms:
            return platforms[0]
        return DEFAULT_PUBLISH_PLATFORMS[0]

    def _slot_at(i: int) -> dict:
        """Slot normalization: old callers pass a platform name string, new callers (source quotas) pass
        {platform, channel, candidates}; both are accepted, internally only dicts are used."""
        if not platform_slots:
            return {}
        s = platform_slots[i % len(platform_slots)]
        return {"platform": s} if isinstance(s, str) else dict(s or {})

    def _platforms_of(tmpl: ContentTemplateORM) -> list[str]:
        try:
            arr = json.loads(tmpl.target_platforms_json or "[]")
        except Exception:  # noqa: BLE001
            return []
        return [str(x) for x in arr] if isinstance(arr, list) else []

    monitored_set = set(monitored)
    # Articles per day: explicit value first, otherwise the number of templates capped at _MAX_PER_DAY: expanding the template pool
    # (raised from 5 to 8 on 2026-08-05) must not double daily output along with it; generation cost is per article.
    n_per_day = (per_day if (per_day and per_day > 0)
                 else min(len(picked_templates), _MAX_PER_DAY))

    def _day_slots(day_offset: int) -> list[tuple[ContentTemplateORM, dict]]:
        """The day's N template slots: the starting point is offset by day so every template in the pool gets scheduled across days.

        With source quotas, templates follow platforms: prefer templates whose target_platforms cover that platform,
        falling back to rotation if none, otherwise you get "a Zhihu long-form slot assigned a Xiaohongshu image-text template".

        Returns a (template, basis) pair: whether it was a "platform reverse-lookup hit" or a "rotation fallback"; this fact
        used to be lost on the spot, and the panel could not answer "why does this row have an unsuitable template".
        """
        rotate = [picked_templates[(day_offset + i) % len(picked_templates)]
                  for i in range(n_per_day)]
        if not platform_slots:
            return [(tp, {"reason": "rotation"}) for tp in rotate]
        out: list[tuple[ContentTemplateORM, dict]] = []
        for i in range(n_per_day):
            want = _slot_at(i).get("platform") or ""
            hit = next((tp for tp in picked_templates if want in _platforms_of(tp)), None)
            if hit is not None:
                out.append((hit, {"reason": "platform_match", "matched_platform": want}))
            else:
                out.append((rotate[i], {"reason": "rotation", "wanted_platform": want}))
        return out

    items: list[dict] = []
    seq = start_seq
    for day_offset in range(days):
        entry = seed_entries[day_offset % len(seed_entries)]
        day_slots = _day_slots(day_offset)
        # The seed's queries are distributed in turn across the day's N articles (article i gets items i, i+N, i+2N...)
        day_queries = [
            str(q) for q in (entry.get("queries") or []) if q and str(q) in monitored_set
        ]
        for ti, (tmpl, tmpl_why) in enumerate(day_slots):
            direction, ctype, combo_why = _default_row_combo(tmpl.kind, day_offset + ti)
            slot = _slot_at(ti)
            # 2026-08-05: when there are fewer queries than articles per day, stride slicing gives later rows empty lists,
            # and those articles would only be written from the seed, covering no prompt. When none is assigned, reuse one in rotation:
            # writing another article on the same question with a different template and angle is normal practice; leaving it empty is the gap.
            row_queries = day_queries[ti::n_per_day]
            queries_why = {"reason": "stride_split", "count": len(row_queries)}
            if not row_queries and day_queries:
                row_queries = [day_queries[ti % len(day_queries)]]
                queries_why = {"reason": "reuse_rotate",
                               "day_total": len(day_queries), "per_day": n_per_day}
            elif not row_queries:
                queries_why = {"reason": "none"}
            row = {
                "id": str(_uuid.uuid4()),
                "seq": seq,
                "publish_date": (start_date + timedelta(days=day_offset)).isoformat(),
                "seed": entry.get("seed") or "",
                "query": "",
                "queries": row_queries,
                "template_id": tmpl.id,
                # Source quotas first: citation data shows which kinds of platforms models like to cite, so place there
                "platform": slot.get("platform") or _default_platform(tmpl),
                "note": entry.get("note"),
                "creation_directions": [direction],
                "copywriting_types": [ctype],
            }
            # Scheduling basis: why the platform/template/preferences/questions/seed are each the way they are.
            # All of this used to be discarded after computation, leaving only a conclusion in the plan.
            platform_why = (
                {k: v for k, v in {
                    "reason": slot.get("reason") or "source_quota",
                    "share_pct": slot.get("share_pct"),
                    "citations": slot.get("citations"),
                    "day_slots": slot.get("day_slots"),
                    "rank": slot.get("rank"),
                }.items() if v is not None}
                if slot.get("platform") else {"reason": "template_default"}
            )
            seed_origin = entry.get("origin") or {}
            if seed_origin.get("type") == "strategy":
                seed_why = {"reason": "strategy_tier", "tier": seed_origin.get("tier")}
            elif seed_origin.get("type"):
                seed_why = {"reason": str(seed_origin["type"])}
            else:
                seed_why = {"reason": "approved_seed"}
            media_why = None
            # Paid slots: rotate candidate resources by day (pouring 30 articles a month into the same account carries a high
            # indexing risk on the Ruanwenjiang side), writing channel + resource number + media name together into the plan row,
            # so after approval publish_preset knows whether to go through Ruanwenjiang placement or our own channel.
            if slot.get("channel"):
                row["channel"] = slot["channel"]
            cands = slot.get("candidates") or []
            if cands:
                # Rotation must include which article of the day it is (ti), otherwise two same-platform slots on the same day would pick
                # the same account: two articles a day on one account carry an even higher indexing risk than repeats across days
                pos = (day_offset + ti) % len(cands)
                pick = cands[pos]
                row["channel"] = pick.get("provider") or row.get("channel") or "rwj"
                row["rwj_resource_id"] = pick["resource_id"]
                row["media"] = pick["title"]
                media_why = {
                    "reason": "rwj_rotation", "rank": pos + 1, "pool": len(cands),
                    "publish_rate": pick.get("publish_rate"),
                    "price_yuan": pick.get("price_yuan"),
                }
            row["why"] = plan_basis.row_why(
                platform=platform_why, template=tmpl_why, combo=combo_why,
                queries=queries_why, seed=seed_why, media=media_why,
            )
            if entry.get("origin"):
                row["origin"] = entry["origin"]
            items.append(row)
            seq += 1
    return items


def _seed_initial_plan_items(
    db: Session, topic_id: int, monitored: list[str],
) -> list[dict]:
    """First publishing plan batch: fills one month starting today (PLAN_WINDOW_DAYS). Thin wrapper around _build_plan_window."""
    return _build_plan_window(
        db, topic_id, monitored,
        start_date=datetime.utcnow().date(),
        days=PLAN_WINDOW_DAYS, start_seq=0,
    )


def extend_plan_one_month(db: Session, plan: AiTelemetryTopicExecutionPlanORM) -> int:
    """When plan runway is insufficient, schedule another month (PLAN_WINDOW_DAYS) starting the day after the last day.

    When runway is sufficient (end date more than PLAN_REFILL_LEAD_DAYS from today) returns 0 without renewing: built-in idempotency,
    so even though the scheduled job scans daily it only really renews near the end. Returns the number of rows added.
    """
    items = _load_publishing_items(plan)
    if not items:
        return 0

    def _pdate(it: dict):
        try:
            return datetime.fromisoformat(str(it.get("publish_date"))).date()
        except Exception:  # noqa: BLE001
            return None

    dates = [d for d in (_pdate(it) for it in items) if d is not None]
    if not dates:
        return 0
    last_date = max(dates)
    today = datetime.utcnow().date()
    if (last_date - today).days > PLAN_REFILL_LEAD_DAYS:
        return 0  # Runway is still enough, no renewal

    try:
        monitored = json.loads(plan.monitored_queries_snapshot_json or "[]")
    except Exception:  # noqa: BLE001
        monitored = []
    monitored = [str(x) for x in monitored if x] if isinstance(monitored, list) else []

    # Normal rolling continues from the day after the end; when an old one-off plan's end is already in the past, continue from today to avoid scheduling past dates.
    next_start = max(last_date + timedelta(days=1), today)
    max_seq = max((int(it.get("seq") or 0) for it in items), default=-1)

    # Renewal keeps the existing plan's pace: articles per day / template rotation inferred from the last day's rows;
    # plans "generated from a strategy plan" continue the original keyword rotation rather than falling back to topic seeds.
    last_day_rows = sorted(
        [it for it in items if _pdate(it) == last_date],
        key=lambda x: int(x.get("seq") or 0),
    )
    per_day = len(last_day_rows) or None
    template_ids = [
        int(it["template_id"]) for it in last_day_rows if it.get("template_id")
    ] or None
    seed_entries: Optional[list[dict]] = None
    strat_rows = [
        it for it in items
        if isinstance(it.get("origin"), dict) and it["origin"].get("type") == "strategy"
    ]
    if strat_rows:
        # Prefer re-fetching keywords from the strategy plan's original text (so trailing keywords that did not fit in the first month's window get their turn);
        # when the plan is deleted / no longer ready, fall back to inferring from existing rows.
        try:
            seed_entries = _strategy_seed_entries(db, plan.topic_id, monitored)
        except HTTPException:
            by_seed: dict[str, dict] = {}
            order: list[str] = []
            for it in sorted(strat_rows, key=lambda x: int(x.get("seq") or 0)):
                s = str(it.get("seed") or "").strip()
                if not s:
                    continue
                if s not in by_seed:
                    by_seed[s] = {"seed": s, "queries": [], "note": it.get("note"),
                                  "origin": it["origin"]}
                    order.append(s)
                for q in it.get("queries") or []:
                    if q and str(q) not in by_seed[s]["queries"]:
                        by_seed[s]["queries"].append(str(q))
            seed_entries = [by_seed[s] for s in order] or None
        # Continue from the keyword after the last one scheduled, rather than starting from the top every month
        if seed_entries:
            last_seed = str(max(strat_rows, key=lambda x: int(x.get("seq") or 0))
                            .get("seed") or "").strip()
            idx = next((i for i, e in enumerate(seed_entries)
                        if e["seed"] == last_seed), -1)
            if idx >= 0:
                seed_entries = seed_entries[idx + 1:] + seed_entries[:idx + 1]

    # Closed loop: renewal does not just copy last month's pace: placement platforms recompute quotas from the **latest round** of measured sources;
    # content placed last month changed the citation distribution, so next month should follow.
    slots = None
    refill_quota: Optional[dict] = None
    refill_strategy: Optional[dict] = None
    opts = _start_options_of(plan)
    if opts is not None:
        # Renewal must keep the metric definition set at start: a plan scheduled by placement strategy must still be scheduled by strategy next month,
        # and must not silently fall back to the plain source mode
        mode = str(opts.get("platform_mode", "sources") or "sources")
        try:
            from geo.services.source_plan import resolve_quota
            refill_quota, refill_strategy = resolve_quota(
                db, plan.topic_id, per_day or 5, mode)
            if refill_quota:
                slots = refill_quota.get("slot_plan") or refill_quota["slots"]
        except Exception:  # noqa: BLE001
            log.exception("[plan-refill] platform quota failed topic=%d", plan.topic_id)

    new_items = _build_plan_window(
        db, plan.topic_id, monitored,
        start_date=next_start,
        days=PLAN_WINDOW_DAYS, start_seq=max_seq + 1,
        per_day=per_day, template_ids=template_ids, seed_entries=seed_entries,
        platform_slots=slots,
    )
    if not new_items:
        return 0
    # Flywheel P1-B automatic mode: on renewal mix the top-N missed questions into the new window (env switch, off by default)
    if os.environ.get("GEO_TOPIC_BACKFLOW_AUTO", "0") == "1":
        try:
            new_items.extend(_backflow_unhit_items(
                db, plan.topic_id, items + new_items,
                start_date=next_start,
                start_seq=max((int(it.get("seq") or 0) for it in new_items),
                              default=max_seq) + 1,
            ))
        except Exception:  # noqa: BLE001
            log.exception("[plan-refill] backflow failed topic=%d", plan.topic_id)
    items.extend(new_items)
    plan.publishing_plan_json = json.dumps(items, ensure_ascii=False)
    # Renewal recomputes quotas from the **latest round** of measured sources, so the basis must be updated too;
    # otherwise the plan still shows last month's explanation, which does not match the newly scheduled rows
    plan_basis.persist(plan, plan_basis.build(
        trigger="refill", options=(opts or {}), quota=refill_quota,
        strategy=refill_strategy,
        templates=plan_basis.templates_digest(
            _resolve_plan_templates(db, plan.topic_id, template_ids)),
        seed_source=({"kind": "strategy", "seed_count": len(seed_entries)}
                     if seed_entries else {"kind": "seeds"}),
        prev=plan_basis.load(plan),
    ))
    plan.last_edited_at = datetime.utcnow()
    db.commit()
    return len(new_items)


def _backflow_unhit_items(
    db: Session, topic_id: int, existing_items: list[dict],
    *, start_date, start_seq: int,
) -> list[dict]:
    """Automatic topic backflow: missed questions -> plan rows (origin.auto=true provenance).

    Same metric definition as plan_candidates (total_hits=0 in the hit table of the placement set); excludes questions already covered by plan rows
    (queries lists), preventing repeated backflow on monthly renewals; count is GEO_TOPIC_BACKFLOW_TOP_N.
    """
    import uuid as _uuid

    from sqlalchemy import distinct

    top_n = int(os.environ.get("GEO_TOPIC_BACKFLOW_TOP_N", "3"))
    if top_n <= 0:
        return []
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        return []
    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    query_seed = {
        str(q["text"]): str(q.get("seed") or "")
        for q in qarr if isinstance(q, dict) and q.get("text")
    }
    hit_set = {r[0] for r in db.query(distinct(AiTelemetryQueryHitORM.query)).filter(
        AiTelemetryQueryHitORM.topic_id == topic_id,
        AiTelemetryQueryHitORM.total_hits > 0).all()}
    covered: set[str] = set()
    for it in existing_items:
        if isinstance(it, dict):
            covered.update(str(q) for q in (it.get("queries") or []) if q)
    unhit = [q for q in query_seed if q not in hit_set and q not in covered][:top_n]
    if not unhit:
        return []

    picked_templates = _pick_template_kinds_per_seed(db)
    out: list[dict] = []
    for idx, q in enumerate(unhit):
        tmpl = picked_templates[idx % len(picked_templates)] if picked_templates else None
        direction, ctype, combo_why = _default_row_combo(tmpl.kind if tmpl else "", idx)
        platform = DEFAULT_PUBLISH_PLATFORMS[0]
        if tmpl is not None:
            try:
                platforms = json.loads(tmpl.target_platforms_json or "[]")
                if isinstance(platforms, list) and platforms:
                    platform = str(platforms[0])
            except Exception:  # noqa: BLE001
                pass
        out.append({
            "id": str(_uuid.uuid4()),
            "seq": start_seq + idx,
            "publish_date": (start_date + timedelta(days=idx)).isoformat(),
            "seed": query_seed.get(q) or q,
            "query": "",
            "queries": [q],
            "template_id": tmpl.id if tmpl else None,
            "platform": platform,
            "note": "未命中问题自动回流",
            "creation_directions": [direction],
            "copywriting_types": [ctype],
            "origin": {"type": "unhit_query", "auto": True},
            "why": plan_basis.row_why(
                seed={"reason": "backflow_unhit"}, combo=combo_why,
                queries={"reason": "backflow_unhit", "count": 1},
            ),
        })
    if out:
        log.info("[plan-refill] backflow topic=%d +%d unhit queries", topic_id, len(out))
    return out


def _enrich_publishing_items(
    db: Session, plan_id: int, topic_id: int,
    monitored: list[str], items: list[dict],
) -> list[PublishPlanItem]:
    """Combine persisted publishing rows + real-time aggregation (hit rate / doc / template) into a PublishPlanItem list."""
    # 1) Hit rate aggregation
    cells = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    by_query: dict[str, dict] = {}
    for c in cells:
        agg = by_query.setdefault(c.query, {"runs": 0, "hits": 0})
        agg["runs"] += c.total_runs or 0
        agg["hits"] += c.total_hits or 0

    # 2) Doc association: prefer plan_item_id, fall back to source_query_text (old data)
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))             # Soft-deleted drafts do not count as "draft produced"; that row reverts to not produced
          .filter(
              (TopicGeneratedDocORM.execution_plan_id == plan_id) |
              (TopicGeneratedDocORM.execution_plan_id.is_(None))
          )
          .all()
    )
    doc_by_item: dict[str, TopicGeneratedDocORM] = {}
    doc_by_query: dict[str, TopicGeneratedDocORM] = {}
    for d in docs:
        # One plan item may have several combo drafts: the one being generated represents this row first,
        # otherwise the plan table would show "draft produced" while other combos are still running.
        if d.plan_item_id:
            cur = doc_by_item.get(d.plan_item_id)
            if cur is None or (d.status == "generating" and cur.status != "generating"):
                doc_by_item[d.plan_item_id] = d
        if d.source_query_text:
            cur_q = doc_by_query.get(d.source_query_text)
            if cur_q is None or (d.status == "generating" and cur_q.status != "generating"):
                doc_by_query[d.source_query_text] = d

    # 3) Fetch templates in bulk
    tmpl_ids = {it.get("template_id") for it in items if it.get("template_id")}
    tmpl_by_id: dict[int, ContentTemplateORM] = {}
    if tmpl_ids:
        for tmpl in (
            db.query(ContentTemplateORM)
              .filter(ContentTemplateORM.id.in_(list(tmpl_ids)))
              .all()
        ):
            tmpl_by_id[tmpl.id] = tmpl

    out: list[PublishPlanItem] = []
    for it in sorted(items, key=lambda x: (x.get("seq") or 0, x.get("publish_date") or "")):
        q = str(it.get("query") or "")
        seed = str(it.get("seed") or "")
        row_queries = [str(x) for x in (it.get("queries") or []) if str(x).strip()]
        # Coverage: multi-select rows aggregate over this group of queries; legacy rows reverse-lookup by query; seed-based rows aggregate "all queries under the seed".
        if row_queries:
            runs = sum(by_query.get(k, {"runs": 0})["runs"] for k in row_queries)
            hits = sum(by_query.get(k, {"hits": 0})["hits"] for k in row_queries)
        elif seed:
            runs = sum(v["runs"] for k, v in by_query.items()
                       if k in monitored and _query_seed_of(monitored, k) == seed)
            hits = sum(v["hits"] for k, v in by_query.items()
                       if k in monitored and _query_seed_of(monitored, k) == seed)
        else:
            agg = by_query.get(q, {"runs": 0, "hits": 0})
            runs, hits = agg["runs"], agg["hits"]
        cov = round(hits / runs * 100, 1) if runs > 0 else 0.0
        if cov == 0:
            priority = "high"
        elif cov < 50:
            priority = "med"
        else:
            priority = "low"

        item_id = str(it.get("id") or "")
        d = doc_by_item.get(item_id) or (doc_by_query.get(q) if q else None)
        tmpl = tmpl_by_id.get(it.get("template_id")) if it.get("template_id") else None
        suggested: list[str] = []
        if tmpl:
            try:
                raw = json.loads(tmpl.target_platforms_json or "[]")
                if isinstance(raw, list):
                    suggested = [str(p) for p in raw if p]
            except Exception:  # noqa: BLE001
                suggested = []
        if not suggested:
            suggested = list(DEFAULT_PUBLISH_PLATFORMS)

        out.append(PublishPlanItem(
            id=item_id,
            seq=int(it.get("seq") or 0),
            publish_date=str(it.get("publish_date") or ""),
            seed=seed or None,
            query=q,
            queries=row_queries,
            template_id=it.get("template_id"),
            platform=it.get("platform"),
            channel=it.get("channel"),
            extra_platforms=[str(p) for p in (it.get("extra_platforms") or []) if p],
            media=it.get("media"),
            rwj_resource_id=it.get("rwj_resource_id"),
            note=it.get("note"),
            creation_directions=[s for s in (it.get("creation_directions") or []) if s],
            copywriting_types=[s for s in (it.get("copywriting_types") or []) if s],
            day=int(it.get("seq") or 0),     # Compatibility with the old field
            coverage_pct=cov,
            priority=priority,
            doc_id=d.id if d else None,
            doc_status=d.status if d else None,
            doc_failed=bool((getattr(d, "generation_error", "") or "").strip()) if d else False,
            doc_error=((d.generation_error or "").strip()[:160] or None) if d else None,
            template_name=tmpl.name if tmpl else None,
            template_kind=tmpl.kind if tmpl else None,
            suggested_platforms=suggested,
            # Scheduling basis + how this row came about: the plan must be able to justify "why it was scheduled this way".
            # origin has always been persisted to DB; it just was not sent out before.
            why=it.get("why") if isinstance(it.get("why"), dict) else None,
            origin=it.get("origin") if isinstance(it.get("origin"), dict) else None,
        ))
    return out


def _query_seed_of(_monitored: list[str], _query: str) -> str:
    """[Placeholder] query -> seed reverse lookup. The topic.queries_json/query_seeds relationship is not pulled in yet,
    so this returns an empty string (seed-based coverage falls back to "0% by seed"). Join query_seeds when needed later.
    """
    return ""


def _build_publishing_plan(
    db: Session, topic_id: int,
    monitored: list[str], overview: dict,
) -> list[PublishPlanItem]:
    """[Compatibility] For old call sites: goes straight to the "compute now" path without reading publishing_plan_json.

    New code uses _enrich_publishing_items(items_from_json) instead.
    """
    items = _seed_initial_plan_items(db, topic_id, monitored)
    return _enrich_publishing_items(db, 0, topic_id, monitored, items)


def _load_publishing_items(plan: AiTelemetryTopicExecutionPlanORM) -> list[dict]:
    """Read the storage format of publishing_plan_json (list[dict]). Returns [] when invalid / empty."""
    try:
        items = json.loads(plan.publishing_plan_json or "[]")
    except Exception:  # noqa: BLE001
        items = []
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def _plan_rows_before(plan: AiTelemetryTopicExecutionPlanORM, cutoff) -> list[dict]:
    """Take historical rows whose publish_date is before cutoff, sort by (date, seq) and renumber seq from 0.

    Used to preserve history when rescheduling the publishing table: these rows carry produced / published articles, and once a row is gone,
    doc.plan_item_id dangles: the plan back-link on the detail page, the re-test probe's question group, and the calendar page history
    all break along with it. Rows whose dates cannot be parsed (empty / dirty data) are kept too, not deleted collaterally in the reschedule.
    """
    def _pd(it: dict):
        try:
            return datetime.fromisoformat(str(it.get("publish_date"))[:10]).date()
        except Exception:  # noqa: BLE001
            return None

    kept = [it for it in _load_publishing_items(plan)
            if (d := _pd(it)) is None or d < cutoff]
    kept.sort(key=lambda it: (str(it.get("publish_date") or ""), int(it.get("seq") or 0)))
    for i, it in enumerate(kept):
        it["seq"] = i
    return kept


def _build_seed_query_groups(topic, monitored: list[str]) -> dict[str, list[str]]:
    """Seed -> prompts under that seed, for grouped query selection in the row editor (TopicExecutionPlanOut.seed_queries).

    **Must be intersected with the monitored snapshot**: monitored comes from monitored_queries_snapshot_json
    (the snapshot at scheduling time), while seed_monitored_queries is fetched **fresh** from topic.queries_json,
    and the two drift. If queries not in the snapshot are sent down, as soon as the user ticks one and saves, _validate_plan_items rejects it with 422.

    Queries in the snapshot without a seed owner (legacy queries / deleted seeds) go into the empty-string group, placed last: leaving them out
    would hide these queries from the selector.
    """
    from geo.services.plan_link import seed_monitored_queries

    mon_set = set(monitored)
    out: dict[str, list[str]] = {}
    for s, qs in (seed_monitored_queries(topic) if topic is not None else {}).items():
        kept = [q for q in qs if q in mon_set]
        if kept:
            out[s] = kept
    grouped = {q for qs in out.values() for q in qs}
    ungrouped = [q for q in monitored if q not in grouped]
    if ungrouped:
        out[""] = ungrouped
    return out


def _to_plan_out(db: Session, plan: AiTelemetryTopicExecutionPlanORM) -> TopicExecutionPlanOut:
    overview = json.loads(plan.overview_json or "{}")
    if not isinstance(overview, dict):
        overview = {}
    changelog = [
        TopicChangelogEntry(**e) for e in _parse_log_list(plan.topic_changelog_snapshot_json)
        if "at" in e
    ]
    expansion = [
        ExpansionLogEntry(**e) for e in _parse_log_list(plan.expansion_log_snapshot_json)
        if "at" in e
    ]
    try:
        monitored = json.loads(plan.monitored_queries_snapshot_json or "[]")
    except Exception:  # noqa: BLE001
        monitored = []
    if not isinstance(monitored, list):
        monitored = []
    monitored = [str(x) for x in monitored if x]
    # The snapshot is frozen and soft deletes happen after it: display and progress converge on the current active vocabulary
    monitored = _active_only(db, plan.topic_id, monitored)

    seed_queries = _build_seed_query_groups(
        db.get(AiTelemetryTopicORM, plan.topic_id), monitored)

    # Aggregate run progress in real time
    run_status: str | None = None
    progress_cells: list[TopicProgressCell] = []
    done = 0
    if plan.run_id:
        run = db.get(AiTelemetryRunORM, plan.run_id)
        run_status = run.status if run else None
        cells = (
            db.query(AiTelemetryQueryHitORM)
              .filter(AiTelemetryQueryHitORM.topic_id == plan.topic_id)
              .all()
        )
        for c in cells:
            if c.query not in monitored:
                continue
            # Only count cells actually scheduled into the batch run: under a seed_only baseline audit, expanded questions' cells
            # stay pending and never run; putting them in the denominator would keep progress from ever reaching 100%.
            if c.status == "pending" and (c.total_runs or 0) == 0:
                continue
            progress_cells.append(TopicProgressCell(
                query=c.query, engine=c.engine, status=c.status,
                hit=(True if (c.total_hits or 0) > 0
                     else (False if c.status == "done" else None)),
                last_checked_at=c.last_checked_at,
            ))
            if c.status == "done":
                done += 1

    # Publishing table: read persisted items; empty the first time -> lay out a version and write it back.
    #   - with start config (new flow): wait for the baseline to finish (run success), then set platforms from measured sources and lay out the table;
    #     during the batch run keep the table empty, and the frontend shows "batch run in progress; the plan will be generated from sources when done"
    #   - without start config (old plans / direct start): keep the original behavior, laying out immediately with template defaults
    raw_items = _load_publishing_items(plan)
    pending_reason: Optional[str] = None
    if not raw_items and monitored:
        opts = _start_options_of(plan)
        if opts is None:
            raw_items = _seed_initial_plan_items(db, plan.topic_id, monitored)
            plan.publishing_plan_json = json.dumps(raw_items, ensure_ascii=False)
            # Old path without start config: laid out by the template's own platforms, without source quotas.
            # quota stays empty, which the frontend uses to explain "this plan did not assign platforms from measured citation data"
            plan_basis.persist(plan, plan_basis.build(
                trigger="lazy_seed", options={"platform_mode": "template"}, quota=None,
                seed_source={"kind": "seeds", "seed_count": len(monitored)},
                prev=plan_basis.load(plan),
            ))
            db.commit()
            db.refresh(plan)
        elif run_status == "success":
            raw_items = _seed_plan_by_sources(db, plan, opts)
            if not raw_items:
                pending_reason = "no_seeds"
        else:
            pending_reason = "waiting_run"
    publishing_plan = _enrich_publishing_items(
        db, plan.id, plan.topic_id, monitored, raw_items,
    )

    return TopicExecutionPlanOut(
        id=plan.id, topic_id=plan.topic_id,
        generated_at=plan.generated_at,
        generated_by_reviewer_id=plan.generated_by_reviewer_id,
        status=plan.status, error=plan.error,
        overview=overview,
        topic_changelog=changelog,
        expansion_log=expansion,
        monitored_queries=monitored,
        seed_queries=seed_queries,
        run_id=plan.run_id, run_status=run_status,
        progress=progress_cells,
        progress_done=done,
        progress_total=len(progress_cells),
        publishing_plan=publishing_plan,
        confirmed_at=plan.confirmed_at,
        confirmed_by_id=plan.confirmed_by_id,
        last_edited_at=plan.last_edited_at,
        plan_pending_reason=pending_reason,
    )


@router.get("/topic/{topic_id}/execution-plan", response_model=TopicExecutionPlanOut)
def get_execution_plan(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get this topic's latest execution plan (including run progress). 404 if none.

    owner-or-staff: customers view their own project's execution plan read-only on the "topic info" page; write access is still admin-only.
    """
    _require_topic_access(db, topic_id, current_user)
    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if not plan:
        raise HTTPException(404, "no execution plan generated")
    return _to_plan_out(db, plan)


def _latest_plan_or_404(db: Session, topic_id: int) -> AiTelemetryTopicExecutionPlanORM:
    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if not plan:
        raise HTTPException(404, "no execution plan")
    return plan


def _validate_plan_items(
    db: Session, monitored: list[str], items: list[PlanItemDraft],
    existing: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """PUT /execution-plan input validation + conversion to storage format (list[dict]).

    - at least one of seed or query must be non-empty
    - if query is non-empty it must be in monitored; seed-based rows allow an empty query
    - template_id must exist
    - platform must be in template.target_platforms (no restriction if the list is empty)
    Returns the list[dict] written back to publishing_plan_json.

    2026-08-06: existing (old rows indexed by id) is used to pass machine fields through. Row dicts here are **rebuilt from a whitelist**,
    so keys the frontend does not send back are silently erased: `origin` (strategy provenance, which monthly renewal relies on to decide
    whether to keep the strategy rotation) was lost exactly this way: after ops saved the publishing table once in the editor, renewal fell back
    to seed rotation. The newly added scheduling basis `why` is the same. Along with passing through, partial invalidation is applied per changed field; see
    plan_basis.merge_machine_keys.
    """
    import uuid as _uuid
    monitored_set = set(monitored)
    existing = existing or {}
    out: list[dict] = []
    for it in items:
        q = (it.query or "").strip()
        seed = (it.seed or "").strip()
        # 2026-06-11: multi-select queries: drop empties, dedup preserving order, each must be in monitored
        queries = list(dict.fromkeys(
            str(x).strip() for x in (it.queries or []) if str(x).strip()
        ))
        if not q and not seed and not queries:
            raise HTTPException(422, {
                "code": "MISSING_TOPIC", "field": "seed",
                "message": "seed 与 query 至少要填一个",
            })
        if q and q not in monitored_set:
            raise HTTPException(422, {
                "code": "INVALID_QUERY", "field": "query",
                "message": f"query 不在监测列表里:{q}",
            })
        bad_q = [x for x in queries if x not in monitored_set]
        if bad_q:
            raise HTTPException(422, {
                "code": "INVALID_QUERY", "field": "queries",
                "message": f"query 不在监测列表里:{bad_q[0]}",
            })
        tmpl = db.get(ContentTemplateORM, it.template_id)
        if not tmpl:
            raise HTTPException(422, {
                "code": "INVALID_TEMPLATE", "field": "template_id",
                "message": f"模板不存在:{it.template_id}",
            })
        try:
            allowed = json.loads(tmpl.target_platforms_json or "[]")
        except Exception:  # noqa: BLE001
            allowed = []
        # Platform x template suitability only warns, it does not block: publishing table platforms are now decided by **measured source quotas**
        # (source_plan), while a template's target_platforms is a small set its author filled in back then,
        # so the two naturally do not line up. Hard validation would 422 the Sohu/Zhihu rows the backend itself scheduled as soon as they are saved.
        if isinstance(allowed, list) and allowed and it.platform not in allowed:
            log.info("[plan] platform %s is not among the applicable platforms of template '%s'; allowed per source quota",
                     it.platform, tmpl.name)
        row = {
            "id": it.id or str(_uuid.uuid4()),
            "seq": it.seq,
            "publish_date": it.publish_date,
            "seed": seed or None,
            "query": q,
            "queries": queries,
            "template_id": it.template_id,
            "platform": it.platform,
            "channel": (it.channel or None),
            # Extra placement platforms: dedup + drop ones duplicating the main platform, persisted in order
            "extra_platforms": list(dict.fromkeys(
                q2 for q2 in (str(p).strip() for p in (it.extra_platforms or []))
                if q2 and q2 != (it.platform or "").strip()
            )),
            "media": (it.media or None),
            "rwj_resource_id": it.rwj_resource_id,
            "note": (it.note or None),
            # 2026-06-12: row-level creative preferences used to be dropped here (ticked in the editor but not persisted); fixed
            "creation_directions": [s for s in (it.creation_directions or []) if s],
            "copywriting_types": [s for s in (it.copywriting_types or []) if s],
        }
        # Machine fields (origin / why) are not sent back by the frontend, so pass them through from the old row; manually changed parts are downgraded
        plan_basis.merge_machine_keys(row, existing.get(str(row["id"])))
        out.append(row)
    # Normalize seq: 0..N in input order
    out.sort(key=lambda x: (x.get("seq", 0)))
    for idx, row in enumerate(out):
        row["seq"] = idx
    return out


@router.put("/topic/{topic_id}/execution-plan", response_model=TopicExecutionPlanOut)
def update_execution_plan(
    topic_id: int,
    payload: ExecutionPlanUpdatePayload,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace the whole publishing table (allowed in both draft / confirmed).

    Calling in confirmed state does not revert to draft nor clear already generated docs; new rows
    are generated at the next confirm or when ops clicks [↻].
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    plan = _latest_plan_or_404(db, topic_id)
    monitored = json.loads(plan.monitored_queries_snapshot_json or "[]") or []
    monitored = [str(x) for x in monitored if x]
    existing = {str(it.get("id")): it for it in _load_publishing_items(plan) if it.get("id")}
    new_items = _validate_plan_items(db, monitored, payload.items, existing)
    plan.publishing_plan_json = json.dumps(new_items, ensure_ascii=False)
    plan.last_edited_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return _to_plan_out(db, plan)


@router.post("/topic/{topic_id}/execution-plan/confirm", response_model=TopicExecutionPlanOut)
def confirm_execution_plan(
    topic_id: int,
    background: BackgroundTasks,
    request: Request,
    force: bool = False,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm the publishing table and trigger article generation.

    - status=draft -> generate the whole table, set confirmed
    - status=confirmed: idempotent; by default only dispatches single generation for **plan_items without drafts yet**;
      ?force=true forces regenerating the whole table
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    plan = _latest_plan_or_404(db, topic_id)
    items = _load_publishing_items(plan)
    if not items:
        raise HTTPException(422, {
            "code": "EMPTY_PLAN", "message": "发文表为空,请先添加至少一行",
        })

    if plan.status == "draft":
        plan.status = "confirmed"
        plan.confirmed_at = datetime.utcnow()
        plan.confirmed_by_id = actor.id
        db.commit()
        db.refresh(plan)
        _kick_content_generation(plan, background, request=request, actor=actor,
                                 trigger_source="plan_confirm")
    else:
        # confirmed -> incremental by default: only items without drafts; force=True forces everything
        if force:
            _kick_content_generation(plan, background, request=request, actor=actor,
                                     trigger_source="plan_confirm_force")
        else:
            done_ids = {
                d.plan_item_id for d in (
                    db.query(TopicGeneratedDocORM)
                      .filter(TopicGeneratedDocORM.topic_id == topic_id)
                      .filter(TopicGeneratedDocORM.plan_item_id.isnot(None))
                      .filter(TopicGeneratedDocORM.deleted_at.is_(None))   # Soft-deleted ones do not count as produced and will be dispatched again
                      .all()
                ) if d.plan_item_id
            }
            todo = [it["id"] for it in items if it.get("id") and it["id"] not in done_ids]
            if todo:
                _kick_content_generation(plan, background, plan_item_ids=todo,
                                         request=request, actor=actor,
                                         trigger_source="plan_confirm_incr")
    return _to_plan_out(db, plan)


@router.post(
    "/topic/{topic_id}/execution-plan/items/{item_id}/regenerate",
    response_model=TopicExecutionPlanOut,
)
def regenerate_plan_item(
    topic_id: int, item_id: str,
    background: BackgroundTasks,
    request: Request,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Single regenerate / generate now: set the corresponding doc to generating + dispatch a single generation task.

    Rows without a draft (no doc yet) dispatch a generation task directly; the doc is created by the generation thread (as generating on creation).
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    plan = _latest_plan_or_404(db, topic_id)
    items = _load_publishing_items(plan)
    if not any(it.get("id") == item_id for it in items):
        raise HTTPException(404, "plan item not found")
    if plan.status != "confirmed":
        raise HTTPException(409, {
            "code": "NOT_CONFIRMED",
            "message": "请先确认执行计划再重生单条",
        })
    # The balance pre-check must happen here, not only via the gate in the background task: when that side finds no money it just writes a
    # WARNING line and returns, while the API has long since returned 200 and the row is already set to "generating", so it stays
    # "generating" forever with no error cause, and ops can only keep clicking regenerate (on 2026-08-27 we found 5 zombie rows in the DB,
    # the oldest stuck for nearly a month). Here we return 402 directly so the frontend can explain clearly.
    from geo.services.content_generator import insufficient_balance_msg
    t_for_bill = db.get(AiTelemetryTopicORM, topic_id)
    if t_for_bill is not None:
        short = insufficient_balance_msg(db, t_for_bill, 1)
        if short:
            raise HTTPException(402, {"code": "INSUFFICIENT_BALANCE", "message": short})

    d = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id,
                  TopicGeneratedDocORM.plan_item_id == item_id)
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))             # Soft-deleted ones count as never produced; regeneration creates a new one
          .first()
    )
    if d:
        # Set generating immediately so the frontend row shows "generating" right away instead of waiting for the generation thread to start
        d.status = "generating"
        d.generation_error = None
        # Do not clear body, so content edited by ops is not wiped instantly; a successful LLM run overwrites it
        db.commit()
    _kick_content_generation(plan, background, plan_item_ids=[item_id],
                             request=request, actor=actor,
                             trigger_source="regenerate_item")
    db.refresh(plan)
    return _to_plan_out(db, plan)


@router.post("/topic/{topic_id}/rerun", response_model=TopicExecutionPlanOut)
def rerun_topic(
    topic_id: int,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrigger the batch run: for plan failed / missing run_id / wanting another run.

    Calls telemetry-service /run-topic to get a new run_id,
    updates the topic's latest execution plan (run_id / status / error) and returns it.
    Creates one if there is no plan.
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    t = _load_topic_or_404(db, topic_id)
    _, operation = create_resource_operation_context(
        db,
        actor,
        t,
        action="telemetry.rerun.create",
        target_resource_type="topic",
        target_resource_id=t.id,
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="telemetry.rerun.create",
        object_type="topic",
        object_id=t.id,
        result="success",
    )
    db.commit()
    run_id = _trigger_run_topic_sync(topic_id, operation.id)

    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if plan is None:
        # No historical plan; just add a draft
        snapshot = _build_execution_plan_snapshot(t, run_id)
        plan = AiTelemetryTopicExecutionPlanORM(
            topic_id=topic_id,
            generated_by_reviewer_id=actor.id,
            overview_json=json.dumps(snapshot["overview"], ensure_ascii=False),
            topic_changelog_snapshot_json=json.dumps(snapshot["changelog"], ensure_ascii=False),
            expansion_log_snapshot_json=json.dumps(snapshot["expansion_log"], ensure_ascii=False),
            monitored_queries_snapshot_json=json.dumps(snapshot["monitored_queries"], ensure_ascii=False),
            publishing_plan_json="[]",
            run_id=run_id,
            status="draft" if run_id is not None else "failed",
            error=None if run_id is not None else "telemetry-service /run-topic failed",
        )
        db.add(plan)
    else:
        # Only refresh run_id without touching status: a confirmed plan must not revert to draft because monitoring was rerun
        plan.run_id = run_id
        if run_id is None:
            plan.status = "failed"
            plan.error = "telemetry-service /run-topic failed"
        else:
            plan.error = None
        plan.generated_at = datetime.utcnow()
        plan.generated_by_reviewer_id = actor.id

    _append_changelog(t, actor_id=actor.id, actor_role="actor",
                      field="rerun", after=f"run_id={run_id}",
                      note="actor 手动重新触发跑批")

    db.commit()
    db.refresh(plan)
    return _to_plan_out(db, plan)


@router.post("/topic/{topic_id}/execution-plan/regenerate", response_model=TopicExecutionPlanOut)
def regenerate_execution_plan(
    topic_id: int,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate the plan snapshots: rebuild the three snapshots from the topic's current profile / prompts / logs.

    For when "seed queries / expanded queries were changed after approval": the snapshots shown in the plan are
    frozen copies from the moment of generation, and later topic edits do not sync automatically; the actor must rebuild them manually.
    The publishing table (publishing_plan_json) and status / run_id / confirmed_at all stay unchanged;
    only the snapshots and generated_at / generated_by_reviewer_id are refreshed.
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    t = _load_topic_or_404(db, topic_id)
    plan = _latest_plan_or_404(db, topic_id)
    snapshot = _build_execution_plan_snapshot(t, plan.run_id)
    # Merge rather than overwrite wholesale: overview_json also carries start_options (start config) and
    # plan_basis (scheduling basis), which the snapshot functions do not produce; overwriting wholesale would erase them.
    # Once start_options is lost, extend_plan_one_month's platform_mode check comes up empty,
    # and monthly automatic renewal silently degrades to not scheduling by sources.
    try:
        prev_overview = json.loads(plan.overview_json or "{}")
    except Exception:  # noqa: BLE001
        prev_overview = {}
    if not isinstance(prev_overview, dict):
        prev_overview = {}
    prev_overview.update(snapshot["overview"])
    plan.overview_json = json.dumps(prev_overview, ensure_ascii=False)
    plan.topic_changelog_snapshot_json = json.dumps(snapshot["changelog"], ensure_ascii=False)
    plan.expansion_log_snapshot_json = json.dumps(snapshot["expansion_log"], ensure_ascii=False)
    plan.monitored_queries_snapshot_json = json.dumps(snapshot["monitored_queries"], ensure_ascii=False)
    plan.generated_at = datetime.utcnow()
    plan.generated_by_reviewer_id = actor.id
    _append_changelog(t, actor_id=actor.id, actor_role="actor",
                      field="execution_plan",
                      after=f"queries={len(snapshot['monitored_queries'])}",
                      note="actor 重新生成计划书快照")
    db.commit()
    db.refresh(plan)
    return _to_plan_out(db, plan)


@router.post(
    "/topic/{topic_id}/execution-plan/items",
    response_model=TopicExecutionPlanOut,
)
def add_plan_item(
    topic_id: int,
    payload: PlanAddItemPayload,
    background: BackgroundTasks,
    request: Request,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a plan row on a given date, optionally generating immediately: the shortcut for "schedule one article on some day + choose template params + produce now".

    Row validation reuses the PUT /execution-plan rules; id/seq are set by the backend (appended at the end of the table;
    display groups by date so position does not matter); with generate_now=True a single generation is dispatched,
    and plan.status is unchanged (both draft/confirmed allowed).
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    import uuid as _uuid

    plan = _latest_plan_or_404(db, topic_id)
    try:
        monitored = json.loads(plan.monitored_queries_snapshot_json or "[]")
    except Exception:  # noqa: BLE001
        monitored = []
    monitored = [str(x) for x in monitored if x] if isinstance(monitored, list) else []

    new_rows = _validate_plan_items(db, monitored, [payload.item])
    if not new_rows:
        raise HTTPException(422, {"code": "INVALID_ITEM", "message": "计划行无效"})
    row = new_rows[0]
    items = _load_publishing_items(plan)
    row["id"] = str(_uuid.uuid4())
    row["seq"] = max((int(it.get("seq") or 0) for it in items), default=-1) + 1
    items.append(row)
    plan.publishing_plan_json = json.dumps(items, ensure_ascii=False)
    plan.last_edited_at = datetime.utcnow()

    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is not None:
        _append_changelog(
            t, actor_id=actor.id, actor_role="actor",
            field="plan_item_add",
            after=f"date={row.get('publish_date')} generate={payload.generate_now}",
            note="actor 指定日期新增计划行",
        )
    db.commit()
    db.refresh(plan)
    if payload.generate_now:
        _kick_content_generation(plan, background, plan_item_ids=[row["id"]],
                                 request=request, actor=actor,
                                 trigger_source="plan_item_add")
    return _to_plan_out(db, plan)


@router.post(
    "/topic/{topic_id}/execution-plan/generate-docs",
    response_model=PlanBatchGenOut,
)
def batch_generate_docs(
    topic_id: int,
    payload: PlanBatchGenPayload,
    background: BackgroundTasks,
    request: Request,
    dry_run: bool = False,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batch-generate articles in one go: the default pace produces drafts day by day; this is the entry for "stockpile a batch of drafts first".

    Plan rows are selected by days (next N days) / limit (first N rows); by default only rows without drafts are filled,
    and with include_existing=True rows with drafts are regenerated too. After dispatch the existing generation threads take over and the frontend polls.

    dry_run=True only computes the scope without dispatching, returning the same PlanBatchGenOut, as a preview for the confirmation dialog / the agent's
    two-step confirmation. **It must share the same filtering logic as the real dispatch**: recomputing "how many will be dispatched" on the agent side or frontend
    will inevitably diverge from this (both rules, skipping produced drafts and blocking published ones, are below).
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    plan = _latest_plan_or_404(db, topic_id)
    items = _load_publishing_items(plan)
    if not items:
        raise HTTPException(422, {
            "code": "EMPTY_PLAN", "message": "发文表为空,请先生成/添加计划行",
        })

    def _pdate(it: dict):
        # publish_date may be YYYY-MM-DD or YYYY-MM-DDTHH:MM; take the date part
        try:
            return datetime.fromisoformat(str(it.get("publish_date"))[:10]).date()
        except Exception:  # noqa: BLE001
            return None

    today = datetime.utcnow().date()
    matched = [it for it in items if it.get("id")]
    # Named mode (one day on the calendar page): only these ids count, and neither the date window nor the count cap applies:
    # that page must be able to reschedule and regenerate any whole day (including past days), which the future window of days cannot express.
    if payload.item_ids:
        want = {str(x) for x in payload.item_ids}
        matched = [it for it in matched if str(it.get("id")) in want]
    # Date window: when days is given, only rows in [today, today+days) (rows with unparseable dates are not in the window)
    elif payload.days:
        end = today + timedelta(days=payload.days)
        matched = [
            it for it in matched
            if (d := _pdate(it)) is not None and today <= d < end
        ]
    matched.sort(key=lambda it: (str(it.get("publish_date") or ""), int(it.get("seq") or 0)))

    doc_rows = (
        db.query(TopicGeneratedDocORM.plan_item_id, TopicGeneratedDocORM.status)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.plan_item_id.isnot(None))
          .filter(TopicGeneratedDocORM.deleted_at.is_(None))             # Soft-deleted ones do not count as produced and can be dispatched again
          .all()
    )
    done_ids = {d.plan_item_id for d in doc_rows if d.plan_item_id}
    # Published drafts do not accept batch regeneration: regeneration overwrites the body in place + reverts status to pending review, while the article placed
    # out there does not change with it; after overwriting, online content and the DB disagree, and URL-based citation attribution is distorted too.
    # Rewriting a published draft can only go through the single /items/{id}/regenerate (an explicit single-point operation).
    published_ids = {d.plan_item_id for d in doc_rows
                     if d.plan_item_id and d.status == "published"}
    if payload.include_existing:
        todo = [it["id"] for it in matched if it["id"] not in published_ids]
        skipped = 0
    else:
        todo = [it["id"] for it in matched if it["id"] not in done_ids]
        skipped = len(matched) - len(todo)
    skipped_published = len([it for it in matched if it["id"] in published_ids]) \
        if payload.include_existing else 0
    if payload.limit and not payload.item_ids:
        todo = todo[:payload.limit]

    if dry_run:
        return PlanBatchGenOut(
            queued=len(todo), matched=len(matched), skipped_existing=skipped,
            skipped_published=skipped_published,
        )

    if todo:
        _kick_content_generation(plan, background, plan_item_ids=todo,
                                 request=request, actor=actor,
                                 trigger_source="batch_generate")
        t = db.get(AiTelemetryTopicORM, topic_id)
        if t is not None:
            _append_changelog(
                t, actor_id=actor.id, actor_role="actor",
                field="batch_generate",
                after=f"queued={len(todo)} days={payload.days} limit={payload.limit}",
                note="actor 一次性批量生成文章",
            )
            db.commit()
    return PlanBatchGenOut(
        queued=len(todo), matched=len(matched), skipped_existing=skipped,
        skipped_published=skipped_published,
    )


@router.post(
    "/topic/{topic_id}/execution-plan/generate-items",
    response_model=TopicExecutionPlanOut,
)
def generate_plan_items(
    topic_id: int,
    payload: PlanGeneratePayload,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reschedule the publishing table by generation options: **only today and later are rescheduled**; historical rows are kept as-is.

    - per_day: articles per day (1-10)
    - template_ids: content templates to use, rotated in the given order; empty = auto-pick system templates
    - source=seeds: approved seeds rotated by day (default);
      source=strategy: rotate by the strategy plan's keyword system (the plan must be generated, otherwise 409)
    - platform_mode=template: keep the template's own applicable platforms;
      platform_mode=sources: assign by measured citation share;
      platform_mode=strategy: first produce a placement strategy, then assign by the citation distribution weighted by engine priority;
      the strategy snapshot is persisted with the plan, and infrastructure actions in the checklist are pushed into the optimization task center at the same time

    2026-08-06: this used to replace the whole table: the new window started from today, and a single json.dumps erased **all past rows**
    along with it. Those rows carried produced / published articles; with the rows gone -> plan_item_id dangles ->
    the detail page plan back-link, the re-test probe's question group and the calendar page history all break. Now it is
    "keep all rows before today intact + reschedule from today", so rescheduling only affects the future.

    Rows from today on that already have drafts still lose their article association (articles are not deleted); the frontend confirmation dialog says so truthfully;
    after rescheduling a confirmed plan, it must be "confirmed" again before articles are generated for new rows.
    Monthly automatic renewal (extend_plan_one_month) keeps the pace set here.
    """
    # The plan step is **the customer's own deliverable**, with the same functionality as for ops (owner or staff);
    # the money spent is charged to the topic payer (drafts KIND_CONTENT_DOC / reruns via telemetry prepay).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    if payload.source not in ("seeds", "strategy"):
        raise HTTPException(422, {
            "code": "INVALID_SOURCE",
            "message": f"未知的生成方式:{payload.source}",
        })
    # An unrecognized platform_mode must raise an error, not silently schedule by template platforms: that would turn a typo in "reschedule by placement
    # strategy" into a plan that looks normal but was not scheduled by strategy at all
    if payload.platform_mode not in ("template", "sources", "strategy"):
        raise HTTPException(422, {
            "code": "INVALID_PLATFORM_MODE",
            "message": f"未知的投放平台口径:{payload.platform_mode}",
        })
    plan = _latest_plan_or_404(db, topic_id)
    try:
        monitored = json.loads(plan.monitored_queries_snapshot_json or "[]")
    except Exception:  # noqa: BLE001
        monitored = []
    monitored = [str(x) for x in monitored if x] if isinstance(monitored, list) else []

    seed_entries = None
    if payload.source == "strategy":
        seed_entries = _strategy_seed_entries(db, topic_id, monitored)

    # Platforms by sources: measured cited domain share -> platform quotas for N articles per day;
    # with platform_mode=strategy, first produce a placement strategy, then compute quotas from the distribution weighted by engine priority
    from geo.services.source_plan import resolve_quota
    quota, strategy = resolve_quota(db, topic_id, payload.per_day,
                                    payload.platform_mode, months=payload.months)
    slots = (quota.get("slot_plan") or quota["slots"]) if quota else None

    # Historical rows are kept as-is: the cut-off and the new window's start use the same today, so nothing is missed or scheduled twice
    today = datetime.utcnow().date()
    kept = _plan_rows_before(plan, today)

    items = _build_plan_window(
        db, topic_id, monitored,
        start_date=today,
        days=PLAN_WINDOW_DAYS * payload.months, start_seq=len(kept),
        per_day=payload.per_day,
        template_ids=payload.template_ids or None,
        seed_entries=seed_entries,
        platform_slots=slots,
    )
    if not items:
        raise HTTPException(422, {
            "code": "EMPTY_PLAN",
            "message": "没有可用的种子 / 提示词,无法生成发文表",
        })
    plan.publishing_plan_json = json.dumps(kept + items, ensure_ascii=False)
    plan_basis.persist(plan, plan_basis.build(
        trigger="generate_items", actor_id=actor.id,
        options=payload.model_dump(), quota=quota, strategy=strategy,
        templates=plan_basis.templates_digest(
            _resolve_plan_templates(db, topic_id, payload.template_ids or None)),
        seed_source=({"kind": "strategy", "seed_count": len(seed_entries)}
                     if seed_entries else
                     {"kind": "seeds", "seed_count": len(monitored)}),
        prev=plan_basis.load(plan),
    ))
    if strategy is not None:
        try:
            from geo.services.placement_strategy import push_infra_actions
            push_infra_actions(db, topic_id, strategy)
        except Exception:  # noqa: BLE001
            log.exception("[plan] push infra actions failed topic=%d", topic_id)
    plan.last_edited_at = datetime.utcnow()
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is not None:
        _append_changelog(
            t, actor_id=actor.id, actor_role="actor",
            field="publishing_plan",
            after=(f"kept={len(kept)} rows={len(items)} per_day={payload.per_day} "
                   f"source={payload.source} platform={payload.platform_mode}"),
            note=("actor 按投放策略重排发文表(今天起,历史保留)"
                  if payload.platform_mode == "strategy"
                  else "actor 按生成选项重排发文表(今天起,历史保留)"),
        )
    db.commit()
    db.refresh(plan)
    return _to_plan_out(db, plan)


# ─────────── Source analysis (the "source analysis" tab on the plan page) ───────────
# Pure aggregation, no LLM: measured cited sources -> domain ranking / sources per prompt / platform quota preview.
#
# Metric definition: this is **recomputed from current data**, not "what was seen when this plan was scheduled".
# Account authorizations, the Ruanwenjiang resource library and batch-run results all drift, so the quotas computed today for the same topic
# may be completely different from when the table was scheduled last month. "The original basis" lives in plan.overview_json.plan_basis
# (services/plan_basis), and the frontend must label the two separately, never mixing them.


class SourceInsightsOut(BaseModel):
    topic_id: int
    responses_total: int = 0
    own_cited_responses: int = 0
    # External source domain ranking (with mapped placeable channels; platform=None means not placeable, reference only)
    top_sources: list[dict] = Field(default_factory=list)
    source_type_share: list[dict] = Field(default_factory=list)
    per_engine: list[dict] = Field(default_factory=list)
    # Sources per prompt (full set, missed ones first)
    per_query: list[dict] = Field(default_factory=list)
    # Platform quotas computed from the **current** citation share (recomputed live, not the snapshot from scheduling time)
    quota: dict = Field(default_factory=dict)
    # Always True: reminds the frontend this data is computed now, and its metric definition and time must be labeled when displayed
    is_live: bool = True
    computed_at: Optional[datetime] = None


@router.get("/topic/{topic_id}/source-insights", response_model=SourceInsightsOut)
def get_source_insights(
    topic_id: int,
    per_day: int = 5,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Data source for the plan's "source analysis" tab.

    owner-or-staff: same metric definition as the execution plan; customers can see their own project's source analysis.
    per_day only affects the number of slots in the quota preview; nothing is persisted.
    """
    t = _require_topic_access(db, topic_id, current_user)

    from geo.services.source_plan import build_platform_quota, build_query_sources
    from geo.services.source_strategy import build_source_stats

    stats = build_source_stats(db, topic_id)
    top_sources = stats.get("top_sources") or []
    from geo.services.source_plan import resolve_domain, rwj_domain_index
    idx = rwj_domain_index(db)
    for s in top_sources:
        r = resolve_domain(db, str(s.get("domain") or ""),
                           source_type=str(s.get("source_type") or ""), index=idx)
        # action: platform = place via platform / rwj_site = Ruanwenjiang paid placement /
        #         profile = not placeable but can update own profile / none = reference only
        s["action"] = r["kind"]
        s["platform"] = r["platform"]
        s["media"] = r.get("media") or []

    quota = build_platform_quota(
        db, topic_id, max(1, min(10, per_day)), top_sources=top_sources)

    return SourceInsightsOut(
        topic_id=topic_id,
        responses_total=int(stats.get("responses_total") or 0),
        own_cited_responses=int(stats.get("own_cited_responses") or 0),
        top_sources=top_sources,
        source_type_share=stats.get("source_type_share") or [],
        per_engine=stats.get("per_engine") or [],
        per_query=build_query_sources(db, topic_id),
        quota=quota,
        is_live=True,
        computed_at=datetime.utcnow(),
    )


# ─────────── Placement strategy (the "placement strategy" tab on the plan page) ───────────
# Knowledge base (which sources each of the five major engines trusts) + this topic's measured citation data -> engine priority / per-engine placement
# checklist / content formats / placement pace. Generated deterministically, without the LLM (see services/placement_strategy).
#
# What is returned here is **a version computed from current data**; "the strategy used when this plan was scheduled" is in
# plan.overview_json.plan_basis.strategy, and the frontend must label the two separately.


class PlacementStrategyOut(BaseModel):
    topic_id: int
    strategy: dict = Field(default_factory=dict)
    # Always True: reminds the frontend this is computed now, not the snapshot from scheduling time
    is_live: bool = True
    computed_at: Optional[datetime] = None


@router.get("/topic/{topic_id}/placement-strategy", response_model=PlacementStrategyOut)
def get_placement_strategy(
    topic_id: int,
    per_day: int = 5,
    months: int = 1,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Data source for the plan's "placement strategy" tab.

    owner-or-staff: same metric definition as the execution plan / source analysis.
    per_day / months only affect the article count conversion in the pace table; nothing is persisted.
    """
    t = _require_topic_access(db, topic_id, current_user)

    from geo.services.placement_strategy import build_placement_strategy

    strategy = build_placement_strategy(
        db, topic_id, per_day=max(1, min(10, per_day)), months=max(1, min(3, months)))
    return PlacementStrategyOut(
        topic_id=topic_id, strategy=strategy, is_live=True,
        computed_at=datetime.utcnow(),
    )


# ═════════════════ v3.3: GEO brand growth strategy plan ═════════════════


def _solution_to_out(sol: AiTelemetryTopicSolutionORM) -> TopicSolutionOut:
    """ORM -> TopicSolutionOut. When status != ready the content blocks are None / empty,
    and the frontend renders loading / error / content by status."""
    diagnosis: SolutionDiagnosis | None = None
    seven_steps: list[SolutionSevenStepItem] = []
    keyword_tiers: list[SolutionKeywordTier] = []
    vision: list[SolutionVisionItem] = []
    brand_snapshot: BrandProfile | None = None
    queries_snapshot: SolutionQueriesSnapshot | None = None

    if sol.status == "ready":
        try:
            d = json.loads(sol.diagnosis_json or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        try:
            n = json.loads(sol.narrative_json or "{}")
        except Exception:  # noqa: BLE001
            n = {}
        try:
            k = json.loads(sol.keywords_json or "{}")
        except Exception:  # noqa: BLE001
            k = {}
        try:
            bp = json.loads(sol.brand_snapshot_json or "{}")
        except Exception:  # noqa: BLE001
            bp = {}

        if isinstance(bp, dict):
            try:
                brand_snapshot = BrandProfile(**bp)
            except Exception:  # noqa: BLE001
                brand_snapshot = None

        if isinstance(d, dict) and d:
            clusters_raw = d.get("clusters") or []
            cluster_summaries = (n.get("cluster_summaries") if isinstance(n, dict) else {}) or {}
            clusters: list[SolutionDiagnosisCluster] = []
            for c in clusters_raw:
                if not isinstance(c, dict):
                    continue
                checks_raw = c.get("checks") or []
                checks_typed = [
                    SolutionDiagnosisCheck(
                        category=str(x.get("category") or ""),
                        status=str(x.get("status") or ""),
                        message=str(x.get("message") or ""),
                        fix=(str(x.get("fix")) if x.get("fix") else None),
                    )
                    for x in checks_raw if isinstance(x, dict)
                ]
                clusters.append(SolutionDiagnosisCluster(
                    key=str(c.get("key") or ""),
                    title_zh=str(c.get("title_zh") or ""),
                    severity=str(c.get("severity") or "low"),
                    summary=str(cluster_summaries.get(c.get("key")) or c.get("summary") or ""),
                    bullets=[str(b) for b in (c.get("bullets") or []) if b],
                    checks=checks_typed,
                ))
            all_checks_raw = d.get("all_checks") or []
            all_checks_typed = [
                SolutionDiagnosisCheck(
                    category=str(x.get("category") or ""),
                    status=str(x.get("status") or ""),
                    message=str(x.get("message") or ""),
                    fix=(str(x.get("fix")) if x.get("fix") else None),
                )
                for x in all_checks_raw if isinstance(x, dict)
            ]
            diagnosis = SolutionDiagnosis(
                url=str(d.get("url") or sol.website_url),
                score=int(d.get("score") or 0),
                grade=str(d.get("grade") or ""),
                pass_count=int(d.get("pass_count") or 0),
                warn_count=int(d.get("warn_count") or 0),
                fail_count=int(d.get("fail_count") or 0),
                info_count=int(d.get("info_count") or 0),
                clusters=clusters,
                execution_layers=[x for x in (d.get("execution_layers") or []) if isinstance(x, dict)],
                all_checks=all_checks_typed,
            )

        if isinstance(n, dict):
            for s in n.get("seven_steps") or []:
                if not isinstance(s, dict):
                    continue
                try:
                    seven_steps.append(SolutionSevenStepItem(
                        step=int(s.get("step") or 0),
                        name=str(s.get("name") or ""),
                        core_goal=str(s.get("core_goal") or ""),
                        core_action=str(s.get("core_action") or ""),
                        output_value=str(s.get("output_value") or ""),
                    ))
                except Exception:  # noqa: BLE001
                    continue
            for v in n.get("vision") or []:
                if not isinstance(v, dict):
                    continue
                vision.append(SolutionVisionItem(
                    title=str(v.get("title") or ""),
                    body=str(v.get("body") or ""),
                ))

        if isinstance(k, dict):
            for tt in k.get("tiers") or []:
                if not isinstance(tt, dict):
                    continue
                keyword_tiers.append(SolutionKeywordTier(
                    tier=str(tt.get("tier") or ""),
                    title_zh=str(tt.get("title_zh") or ""),
                    description=str(tt.get("description") or ""),
                    keywords=[str(x) for x in (tt.get("keywords") or []) if str(x).strip()],
                ))

        try:
            q = json.loads(sol.queries_snapshot_json or "{}")
        except Exception:  # noqa: BLE001
            q = {}
        if isinstance(q, dict) and (q.get("queries") or q.get("clusters")):
            query_items: list[SolutionQueryItem] = []
            for item in (q.get("queries") or []):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                try:
                    cid = int(item.get("cluster_id", -1))
                except (TypeError, ValueError):
                    cid = -1
                query_items.append(SolutionQueryItem(
                    text=text, cluster_id=cid,
                    seed=str(item.get("seed") or ""),
                ))
            cluster_items: list[SolutionQueryCluster] = []
            for c in (q.get("clusters") or []):
                if not isinstance(c, dict):
                    continue
                try:
                    cid = int(c.get("cluster_id"))
                except (TypeError, ValueError):
                    continue
                label = str(c.get("label") or "").strip()
                if not label:
                    continue
                cluster_items.append(SolutionQueryCluster(cluster_id=cid, label=label))
            queries_snapshot = SolutionQueriesSnapshot(
                clusters=cluster_items, queries=query_items,
            )
        else:
            queries_snapshot = None
    else:
        queries_snapshot = None

    return TopicSolutionOut(
        id=sol.id, topic_id=sol.topic_id, status=sol.status or "idle",
        website_url=sol.website_url or "",
        error=sol.error, generated_by_admin_id=sol.generated_by_admin_id,
        llm_model=sol.llm_model or "",
        created_at=sol.created_at, updated_at=sol.updated_at,
        brand_snapshot=brand_snapshot, diagnosis=diagnosis,
        seven_steps=seven_steps, keyword_tiers=keyword_tiers, vision=vision,
        queries_snapshot=queries_snapshot,
    )


@router.get("/topic/{topic_id}/solution", response_model=TopicSolutionOut)
def get_strategic_solution(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get this topic's latest strategy plan. With no record, return a stub with status='idle':
    the frontend switches among four states "not generated / generating / generated / failed" by status, without caring about 404.

    owner-or-staff: customers view their own project's strategy plan read-only on the "topic info" page; generate/retry is still admin-only."""
    _require_topic_access(db, topic_id, current_user)   # Verify the topic exists + ownership
    sol = (
        db.query(AiTelemetryTopicSolutionORM)
          .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
          .first()
    )
    if not sol:
        return TopicSolutionOut(
            id=0, topic_id=topic_id, status="idle", website_url="",
            error=None, generated_by_admin_id=None, llm_model="",
            created_at=None, updated_at=None,
            brand_snapshot=None, diagnosis=None,
            seven_steps=[], keyword_tiers=[], vision=[],
            queries_snapshot=None,
        )
    # Double insurance: besides the startup fallback, check again on the read path. When the daemon thread dies updated_at
    # stops advancing; beyond the threshold it is treated as a zombie and flipped to failed so the frontend gets a retry entry.
    if sol.status == "generating":
        from geo.services.solution_generator import STALE_GENERATING_THRESHOLD
        if sol.updated_at and datetime.utcnow() - sol.updated_at > STALE_GENERATING_THRESHOLD:
            sol.status = "failed"
            sol.error = "后端在生成途中重启,后台任务被中断。请点重试重新生成。"
            sol.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(sol)
    return _solution_to_out(sol)


def _trigger_solution_generation(
    topic_id: int, website_url: str, admin_id: int,
    operation_context_id: int | None = None,
) -> None:
    """Trigger strategy plan generation in the background, via solution_generator's daemon thread."""
    try:
        from geo.services.solution_generator import schedule_solution_generation
        schedule_solution_generation(
            topic_id=topic_id, website_url=website_url, admin_id=admin_id,
            operation_context_id=operation_context_id,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("schedule solution generation failed: %s", e)


def _charge_solution(db: Session, topic, *, operation_id: int) -> None:
    """Charge for one GEO strategy optimization plan. Raises 402 if it cannot be charged, **does not commit** (follows the caller's transaction).

    The idempotency key is pinned to `operation_id` rather than the solution row id: the solution table is unique(topic_id),
    a topic only ever has one row, and regeneration overwrites in place; using the row id as the key would make every
    "regenerate" after the first hit the key and charge nothing (the comment on `doc:{doc_id}:{attempt_no}` in balance.py
    records exactly this: the metric definition is "charge for every real generation"). operation_context creates a new row per trigger,
    naturally one per run, which also lines this charge up with the audit chain.
    """
    from geo.models.balance import KIND_SOLUTION, PRICE_SOLUTION
    from geo.services import balance_service, topic_billing

    fee = balance_service.get_price(db, PRICE_SOLUTION)
    if fee <= 0:
        return
    res = topic_billing.charge(
        db, topic, kind=KIND_SOLUTION, amount_cents=fee,
        idem_key=f"solution:{int(operation_id)}",
        ref_type="solution", ref_id=str(topic.id),
        note={"price_cents": fee, "via": "solution_generate"},
    )
    if res is None:            # Ops topic / master switch off: allow through, consistent with other categories
        return
    if res.status == balance_service.INSUFFICIENT:
        db.rollback()          # Do not leave the half-finished generating state in the transaction
        raise HTTPException(402, {
            "code": "INSUFFICIENT_BALANCE",
            "message": (f"积分不足:生成一份 GEO策略优化方案需要 {fee} 积分,"
                        f"当前 {res.balance_cents} 积分。充值后可重新发起。"),
        })


@router.post("/topic/{topic_id}/solution/generate", response_model=TopicSolutionOut)
def generate_strategic_solution(
    topic_id: int,
    payload: GenerateSolutionPayload,
    background: BackgroundTasks,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger (or retrigger) strategy plan generation, run asynchronously in the background. **owner or staff**, charged per plan.

    Flow:
      1. upsert the solution row first, status=generating + website_url + clear error
      2. charge for one `solution`; if it cannot be charged return 402 directly, **without setting generating or starting a background task**
      3. BackgroundTasks triggers the solution_generator daemon thread
      4. immediately return status=generating to the frontend; the frontend polls GET solution every 3s until ready/failed

    Why it is open to owners: previously only staff had this button, and the customer version was a sentence
    "the GEO strategy optimization plan has not been generated yet; it will be shown here once generated", with no subject, no time and no
    next step, leaving them not knowing who would generate it or whether they needed to do anything.

    Why billing is wired in at the same time: generation is a full geo_checker scan + several long LLM writeups, with real cost.
    Before this it charged nothing, and handing the button to customers would open a free heavy job that can be clicked endlessly
    (with a "regenerate" next to it). With a charge, "they press it with their own money" holds up.

    **Who pays**: the topic's payer (`topic_billing.payer_id`), regardless of who clicks; same metric definition as publishing
    (KIND_CONTENT_DOC) and one-click diagnosis. Ops' own topics have payer None and are still not charged.
    """
    t = _require_topic_access(db, topic_id, actor, ACTION_WRITE)

    sol = (
        db.query(AiTelemetryTopicSolutionORM)
          .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
          .first()
    )
    if sol is None:
        sol = AiTelemetryTopicSolutionORM(topic_id=topic_id)
        db.add(sol)
    sol.status = "generating"
    sol.website_url = payload.website_url.strip()
    sol.error = None
    sol.generated_by_admin_id = actor.id
    sol.updated_at = datetime.utcnow()
    _, operation = create_resource_operation_context(
        db,
        actor,
        t,
        action="solution.generate",
        target_resource_type="topic",
        target_resource_id=t.id,
    )
    sol.operation_context_id = operation.id
    # Charging happens **before** starting the background task, and if it cannot be charged the whole request returns 402.
    # It cannot be judged only in the background task: when that side finds no money it just logs a line and returns, while the API has long since returned 200
    # and the row is already set to generating, so it stays "generating" forever with no error cause, and users can only keep clicking regenerate.
    # regenerate-item already fell into this pit once (see the "the balance pre-check must happen here" section in this file).
    #
    # This button **only** generates the plan, it does not trigger a batch run: batch runs all go through the "monitoring & batch runs" panel
    # (topic settings page -> top of the prompts tab / top of wizard step 4) via POST /topics/{id}/run.
    _charge_solution(db, t, operation_id=operation.id)
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="solution.generate",
        object_type="solution",
        object_id=sol.id,
        result="success",
    )
    db.commit()
    db.refresh(sol)

    background.add_task(
        _trigger_solution_generation,
        topic_id=topic_id, website_url=sol.website_url, admin_id=actor.id,
        operation_context_id=operation.id,
    )
    # actor_role is written by actual role: a customer's own click should not be recorded as done by admin in the changelog.
    _staff = is_staff(actor)
    _append_changelog(t, actor_id=actor.id,
                      actor_role="admin" if _staff else "user",
                      field="solution", after=sol.website_url,
                      note=("admin 触发战略方案生成" if _staff
                            else "用户自助触发战略方案生成"))
    db.commit()
    return _solution_to_out(sol)


# ════════════ New review-free channel: POST /admin/topics/{id}/start ════════════
# Replaces approve_topic's position in the new flow. The status flip part (approve_topic steps 1/2)
# is no longer needed, because topics created directly by admin already have submission_status=approved. start only
# triggers the 5 downstream side effects (scoring / plan / copy / email).
#
# Idempotency rule: when the topic already has a ready/generating plan, it behaves like GET (returns the latest one);
# only an explicit ?force=true retriggers. The frontend "restart" button sends force=true.


def _validate_topic_runnable(t: AiTelemetryTopicORM) -> None:
    """Pre-start validation: required profile fields complete + >=1 selected query + >=1 engine. Reuses the submit logic."""
    try:
        profile_raw = json.loads(t.profile_json or "{}")
    except Exception:  # noqa: BLE001
        profile_raw = {}
    if not isinstance(profile_raw, dict):
        profile_raw = {}
    missing: list[str] = []
    for f in PROFILE_REQUIRED_FIELDS:
        v = profile_raw.get(f)
        if v is None or v == "" or v == [] or v == {}:
            missing.append(f)
    if missing:
        raise HTTPException(422, {
            "code": "PROFILE_INCOMPLETE", "field": "profile",
            "message": "资料必填项缺失", "missing": missing,
        })

    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    selected_n = sum(
        1 for q in qarr
        if isinstance(q, dict) and q.get("text") and q.get("selected", True)
    )
    if selected_n < 1:
        raise HTTPException(422, {
            "code": "NO_SELECTED", "field": "queries",
            "message": "请至少勾选 1 个提示词",
        })
    if selected_n > MAX_SELECTED_QUERIES:
        raise HTTPException(422, {
            "code": "TOO_MANY_SELECTED", "field": "queries",
            "message": f"最多 {MAX_SELECTED_QUERIES} 个提示词,当前 {selected_n}",
        })

    try:
        engines = json.loads(t.engines_json or "[]")
    except Exception:  # noqa: BLE001
        engines = []
    if not isinstance(engines, list) or len(engines) < 1:
        raise HTTPException(422, {
            "code": "NO_ENGINE", "field": "engines",
            "message": "请至少选择 1 个引擎",
        })


def _start_options_of(plan: AiTelemetryTopicExecutionPlanORM) -> Optional[dict]:
    """Read the start config (stored in overview_json.start_options, no DB column -> no migration needed).

    None = old plan / started without config -> use the original default lazy generation.
    """
    try:
        overview = json.loads(plan.overview_json or "{}")
    except Exception:  # noqa: BLE001
        return None
    opts = overview.get("start_options") if isinstance(overview, dict) else None
    return opts if isinstance(opts, dict) else None


def _seed_plan_by_sources(
    db: Session,
    plan: AiTelemetryTopicExecutionPlanORM,
    opts: dict,
) -> list[dict]:
    """After the baseline finishes, lay out the publishing table by "measured sources + start config". Returns the persisted items.

    Placement platforms no longer come from template defaults but are assigned by citation domain share (source_plan);
    when the placement strategy metric definition was chosen at start, platforms are decided by the strategy-weighted distribution (placement_strategy).
    Coverage queries (coverage_queries) only narrow the query allocation of the **publishing table**; the plan's
    monitored_queries snapshot stays complete: monitoring scope and content coverage are two different things.
    """
    try:
        monitored = json.loads(plan.monitored_queries_snapshot_json or "[]")
    except Exception:  # noqa: BLE001
        monitored = []
    monitored = [str(x) for x in monitored if x] if isinstance(monitored, list) else []

    picked = {str(q) for q in (opts.get("coverage_queries") or []) if q}
    scope = [q for q in monitored if q in picked] if picked else monitored

    per_day = int(opts.get("per_day") or 5)
    months = max(1, min(3, int(opts.get("months") or 1)))
    source = str(opts.get("source") or "seeds")

    seed_entries = None
    if source == "strategy":
        try:
            seed_entries = _strategy_seed_entries(db, plan.topic_id, scope)
        except HTTPException:
            seed_entries = None  # Plan not generated -> fall back to seed rotation so the plan can still be scheduled

    slots: Optional[list[str]] = None
    quota: Optional[dict] = None
    strategy: Optional[dict] = None
    try:
        from geo.services.source_plan import resolve_quota
        quota, strategy = resolve_quota(
            db, plan.topic_id, per_day,
            str(opts.get("platform_mode", "sources") or "sources"), months=months)
        if quota:
            slots = quota.get("slot_plan") or quota["slots"]
    except Exception:  # noqa: BLE001
        log.exception("[plan] platform quota failed topic=%d", plan.topic_id)

    tmpl_ids = [int(x) for x in (opts.get("template_ids") or [])] or None
    items = _build_plan_window(
        db, plan.topic_id, scope,
        start_date=datetime.utcnow().date(),
        days=PLAN_WINDOW_DAYS * months, start_seq=0,
        per_day=per_day,
        template_ids=tmpl_ids,
        seed_entries=seed_entries,
        platform_slots=slots,
    )
    if not items:
        return []
    plan.publishing_plan_json = json.dumps(items, ensure_ascii=False)
    # Record "why it was scheduled this way": quotas, metric definition, template pool and seed source are all judgments of this moment;
    # a few days later after resource library syncs / accounts going offline, a recomputation would no longer match
    plan_basis.persist(plan, plan_basis.build(
        trigger="start", options=opts, quota=quota, strategy=strategy,
        templates=plan_basis.templates_digest(
            _resolve_plan_templates(db, plan.topic_id, tmpl_ids)),
        seed_source=({"kind": "strategy", "seed_count": len(seed_entries)}
                     if seed_entries else
                     {"kind": "seeds", "seed_count": len(scope)}),
        prev=plan_basis.load(plan),
    ))
    if strategy is not None:
        # Strategy actions that cannot go into the publishing table (encyclopedia entries / account verification / webmaster verification) go to the task center,
        # otherwise this half of the strategy is just a paragraph of text on a page that nobody acts on
        try:
            from geo.services.placement_strategy import push_infra_actions
            push_infra_actions(db, plan.topic_id, strategy)
        except Exception:  # noqa: BLE001
            log.exception("[plan] push infra actions failed topic=%d", plan.topic_id)
    plan.last_edited_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return items


@topics_router.post("/{topic_id}/start", response_model=TopicExecutionPlanOut)
def start_topic(
    topic_id: int,
    background: BackgroundTasks,
    payload: Optional[StartTopicPayload] = None,
    force: bool = False,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start the project (review-free flow). **owner or staff**, no longer admin-only.

    New flow:
    - starting only triggers **a monitoring run + persisting a draft plan**, and does **not** generate articles
    - ops edits the publishing table on the execution plan page and clicks confirm -> POST /execution-plan/confirm produces articles
    - idempotent by default: if the topic already has a draft/confirmed plan -> return the latest one directly
    - ?force=true -> skip idempotency and force another trigger (the "restart" button). **Staff only**:
      it bypasses idempotency and directly triggers another billed baseline round, and customers should not have this switch
    - validation: required profile fields complete + >=1 selected query + >=1 engine

    Why it is open to owners: previously only staff could start, while batch runs were started by drip scheduling on its own,
    so a topic had already run thousands of rounds and consumed credits while the plan page kept saying "project not started yet"; customers
    could see that sentence but had no button to click and did not know whom to ask. Starting spends their own money
    (the baseline batch run is prepaid via telemetry billing.prepay and is cancelled straight away if it cannot be charged), so they should press it themselves.
    **The step that actually produces articles (execution-plan/confirm) is still admin-only** and was not opened up along with it.

    body is optional (StartTopicPayload): if given, "months / articles per day / templates / source / coverage queries"
    are stored in the plan, and **after the baseline finishes** placement platforms are set from measured sources and the publishing table is laid out (see _seed_plan_by_sources);
    without it = old behavior: the publishing table is lazily generated with the template default pace when entering the plan page.
    """
    if payload is not None and payload.source not in ("seeds", "strategy"):
        raise HTTPException(422, {
            "code": "INVALID_SOURCE",
            "message": f"未知的生成方式:{payload.source}",
        })
    t = _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    staff = is_staff(actor)
    if force and not staff:
        # Idempotency is the only thing on the customer side preventing "repeated clicks = repeated runs = repeated charges"; callers must not be able to turn it off
        raise HTTPException(403, "force restart is staff-only")
    _validate_topic_runnable(t)

    if not force:
        existing = (
            db.query(AiTelemetryTopicExecutionPlanORM)
              .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
              .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
              .first()
        )
        # Compatibility with old status values ready / generating
        if existing and existing.status in ("draft", "confirmed", "ready", "generating"):
            return _to_plan_out(db, existing)

    # If the topic is still in draft (user-created, not submitted), starting flips it to approved along the way.
    # Topics created directly by admin are already approved, so this is idempotent.
    if t.submission_status != "approved":
        prev = t.submission_status
        t.submission_status = "approved"
        t.approved_at = datetime.utcnow()
        t.rejected_at = None
        # reviewer_id is only written when staff start it: a customer pressing start themselves does not mean "someone reviewed it",
        # and putting their id in the reviewer field would make the admin accountability chain fake.
        if staff:
            t.reviewer_id = actor.id
        _append_changelog(t, actor_id=actor.id,
                          actor_role="admin" if staff else "user",
                          field="submission_status", before=prev, after="approved",
                          note="start_topic")
    db.commit()
    db.refresh(t)

    # force = "restart", whose semantics are wanting a new round; no reuse.
    plan = _create_draft_plan(db, t, actor.id, background,
                              actor_is_staff=staff, reuse_recent_run=not force)

    # The start config is stored in the plan's overview snapshot: the publishing table waits until the baseline finishes and measured sources are available
    # before laying out (_to_plan_out checks run_status == success); here we only record "how to lay it out".
    if payload is not None:
        try:
            overview = json.loads(plan.overview_json or "{}")
        except Exception:  # noqa: BLE001
            overview = {}
        if not isinstance(overview, dict):
            overview = {}
        overview["start_options"] = payload.model_dump()
        plan.overview_json = json.dumps(overview, ensure_ascii=False)
        db.commit()
        db.refresh(plan)

    return _to_plan_out(db, plan)


# ─────────── Flywheel P1-B: topic backflow (candidate topics -> one-click plan items) ───────────
# Audit weaknesses no longer just sit on the dashboard: missed queries + cell insight suggestions become candidate topics,
# and after ops ticks them they are inserted into the publishing plan with one click; inserted rows follow the existing hourly_tick generation -> publish_tick
# publishing chain, with zero new orchestration. Items carry origin provenance (unhit_query / cell_insight).


class PlanCandidateUnhit(BaseModel):
    query: str
    seed: str = ""


class PlanCandidateInsight(BaseModel):
    query: str
    engine: str
    verdict: str = ""
    priority: str = ""
    title: str = ""
    action: str = ""
    why: str = ""


class PlanCandidatesOut(BaseModel):
    topic_id: int
    unhit_queries: list[PlanCandidateUnhit]
    insight_recommendations: list[PlanCandidateInsight]


class InsertPlanItemPayload(BaseModel):
    seed: str = ""
    query: str = ""
    queries: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    origin: Optional[dict] = None       # {"type": "unhit_query"|"cell_insight", "ref": ...}
    # Specified placement platform (empty = keep the template's own applicable platforms). The caller knows why it specified one,
    # so the reason is passed along, otherwise this row's platform in the plan becomes "without basis".
    platform: str = ""
    platform_reason: str = ""           # Default manual; media_recommendation when converted from a media suggestion


class InsertPlanItemsPayload(BaseModel):
    items: list[InsertPlanItemPayload]


@topics_router.get("/{topic_id}/plan/candidates", response_model=PlanCandidatesOut)
def plan_candidates(
    topic_id: int,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Candidate topics: (1) missed queries in the placement set (with their seed) (2) optimization suggestions from cell insights."""
    # Candidate topics / one-click plan items live in the plan editor, with the same permissions as that page (owner or staff).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise HTTPException(404, "topic not found")

    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    query_seed = {
        str(q["text"]): str(q.get("seed") or "")
        for q in qarr if isinstance(q, dict) and q.get("text")
    }
    # Same metric definition as the agent's list_unhit_queries: queries in the placement set with total_hits=0 in the hit table
    from sqlalchemy import distinct
    hit_set = {r[0] for r in db.query(distinct(AiTelemetryQueryHitORM.query)).filter(
        AiTelemetryQueryHitORM.topic_id == topic_id,
        AiTelemetryQueryHitORM.total_hits > 0).all()}
    unhit = [PlanCandidateUnhit(query=q, seed=s)
             for q, s in query_seed.items() if q not in hit_set][:60]

    # Cell insight suggestions: take the latest insight per cell and expand recommendations
    recs: list[PlanCandidateInsight] = []
    seen_cells: set[tuple[str, str]] = set()
    rows = (
        db.query(AiTelemetryCellInsightORM)
          .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
          .order_by(AiTelemetryCellInsightORM.generated_at.desc())
          .all()
    )
    for r in rows:
        key = (r.query, r.engine)
        if key in seen_cells:
            continue
        seen_cells.add(key)
        try:
            r_recs = json.loads(r.recommendations_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        for rec in r_recs:
            if not isinstance(rec, dict):
                continue
            recs.append(PlanCandidateInsight(
                query=r.query, engine=r.engine, verdict=r.verdict or "",
                priority=str(rec.get("priority") or ""),
                title=str(rec.get("title") or ""),
                action=str(rec.get("action") or ""),
                why=str(rec.get("why") or ""),
            ))
        if len(recs) >= 60:
            break
    return PlanCandidatesOut(topic_id=topic_id, unhit_queries=unhit,
                             insight_recommendations=recs[:60])


@topics_router.post("/{topic_id}/plan/insert-items", response_model=TopicExecutionPlanOut)
def insert_plan_items(
    topic_id: int, payload: InsertPlanItemsPayload,
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Insert candidate topics into the latest execution plan (scheduled from tomorrow), with the same row structure as _build_plan_window + origin provenance."""
    # Candidate topics / one-click plan items live in the plan editor, with the same permissions as that page (owner or staff).
    _require_topic_access(db, topic_id, actor, ACTION_WRITE)
    import uuid as _uuid

    if not payload.items:
        raise HTTPException(422, "items 为空")
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise HTTPException(404, "topic not found")
    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if plan is None or plan.status not in ("draft", "confirmed", "ready", "generating"):
        raise HTTPException(404, "该主题没有可编辑的执行计划")

    items = _load_publishing_items(plan)
    max_seq = max((int(it.get("seq") or 0) for it in items), default=-1)
    picked_templates = _pick_template_kinds_per_seed(db)
    tomorrow = datetime.utcnow().date() + timedelta(days=1)

    added = 0
    for idx, req in enumerate(payload.items):
        queries = [q.strip() for q in (req.queries or []) if q.strip()]
        if not queries and req.query.strip():
            queries = [req.query.strip()]
        seed = req.seed.strip() or (queries[0] if queries else "")
        if not seed and not queries:
            continue
        tmpl = picked_templates[idx % len(picked_templates)] if picked_templates else None
        direction, ctype, combo_why = _default_row_combo(tmpl.kind if tmpl else "", idx)
        platform = DEFAULT_PUBLISH_PLATFORMS[0]
        if tmpl is not None:
            try:
                platforms = json.loads(tmpl.target_platforms_json or "[]")
                if isinstance(platforms, list) and platforms:
                    platform = str(platforms[0])
            except Exception:  # noqa: BLE001
                pass
        platform_why = {"reason": "template_default"}
        if req.platform.strip():
            platform = req.platform.strip()
            platform_why = {"reason": req.platform_reason.strip() or "manual"}
        max_seq += 1
        items.append({
            "id": str(_uuid.uuid4()),
            "seq": max_seq,
            "publish_date": (tomorrow + timedelta(days=idx)).isoformat(),
            "seed": seed,
            "query": "",
            "queries": queries,
            "template_id": tmpl.id if tmpl else None,
            "platform": platform,
            "note": req.note,
            "creation_directions": [direction],
            "copywriting_types": [ctype],
            "origin": req.origin or {"type": "manual"},
            "why": plan_basis.row_why(
                seed={"reason": "candidate_insert"}, combo=combo_why,
                platform=platform_why,
            ),
        })
        added += 1
    if not added:
        raise HTTPException(422, "没有有效的候选选题")
    plan.publishing_plan_json = json.dumps(items, ensure_ascii=False)
    plan.last_edited_at = datetime.utcnow()
    _append_changelog(t, actor_id=actor.id, actor_role="actor",
                      field="publishing_plan", before="", after=f"+{added} items",
                      note="plan_insert_candidates")
    db.commit()
    return _to_plan_out(db, plan)
