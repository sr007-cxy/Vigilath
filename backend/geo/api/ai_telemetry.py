"""AI telemetry topic API: CRUD + run now.

Scheduled batch runs are triggered by telemetry-service's own cron; this file only handles:
1. users' topic CRUD
2. /run-now, synchronously forwarded to telemetry-service for one result ("run now" in the modal)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import threading
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
import mimetypes
import uuid
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from datetime import date, datetime, timedelta, timezone

from geo.api.auth import get_current_user, require_admin
from geo.database import SessionLocal
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    RESOURCE_NOT_FOUND,
    apply_enterprise_scope,
    require_resource_access,
    create_operation_context,
    create_resource_operation_context,
    revalidate_operation_context,
    require_enterprise_access,
)
from geo.services.system_audit import append_event as append_system_audit
from geo.services import media_intake, media_storage
from geo.utils.error_handler import AppException
from geo.models.ai_telemetry import (
    ALL_SCENES,
    EXPAND_SCENES,
    AiTelemetryReportDailyORM,
    AiTelemetryResponseORM, AiTelemetryRunORM, AiTelemetryTopicORM,
    AiTelemetryQueryHitORM, AiTelemetryCellInsightORM, AiTelemetryTopicBriefingORM,
    BrandProduct, BrandProfile, BriefingAction, BriefingFeedbackPayload, BriefingOut,
    CellDrawerOut, CellEvidence, CellInsightOut, CellInsightRec, CompetitorMention,
    CompetitorShareEntry, ClusterBreakdownItem, DomainCount, EngineFirstHit,
    FeedbackPayload, GrowthQueryRow, GrowthSnapshot, GrowthReportOut,
    IntentBreakdownOut, KpiBlock, MAX_EXPANSION_CANDIDATES, MAX_SELECTED_QUERIES, MaterialDocOut,
    FIXED_RESPONSE_KEY, MENTION_CAT_KEY, PROFILE_ADMIN_KEY, fixed_response_count,
    mention_cat_scope, merge_profile_json, metric_brand_terms, _profile_dict, profile_product_terms,
    OverviewOut, OwnedSplit, PROFILE_REQUIRED_FIELDS, PositionDist,
    PositionBreakdown, PositionBreakdownOut, GroupBreakdown, EngineSlice,
    IndustryBenchmarkOut,
    CompetitorSubstitutionItem, CompetitorSubstitutionOut,
    ProfileExtractOut, ProfileExtractPayload, QueryHitCell,
    ResponseOut, RunNowCitation,
    RunNowResult, RunOut, AdminRunOut, SeedPromptSubmitPayload,
    SelectedQueriesPayload, ShareOfVoiceOut,
    TopicGeneratedDocORM,
    TopicMediaOut, TopicMediaORM,
    TopicDraftPayload, TopicOut, TopicPayload, TrackingMatrixOut,
    TrendPoint, VALID_ENGINES,
)
from urllib.parse import urlparse as _urlparse
from geo.services.profile_extractor import (
    FileParseError, MAX_EXTRACTED_TEXT,
    extract_profile_from_text, file_bytes_to_text,
)
from geo.services.query_expander import (
    DEFAULT_TIMEOUT, expand_one_scene, route_brand_words,
)
from geo.services.topic_expand import (
    brand_terms_for_routing, expand_topic_seed, expand_with_fields, matched_product_out,
)
from geo.services.material_store import (
    default_title, delete_document, ingest_document, ingest_document_sync, list_documents, text_source,
)
from geo.models.user import User, UserORM
from geo_checker.analyzers.source_factor import classify_factor
from sqlalchemy import bindparam, func, text as sa_text

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-telemetry")

TELEMETRY_SERVICE_URL = os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095")
from geo.services.telemetry_client import internal_headers  # noqa: E402  X-Internal-Token for batch-run center business endpoints


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Report center brand tags: submitted together from topic settings step 1. The valid set matches ai_telemetry_reports.py;
# invalid values are always reset to "" rather than returning 422 and interrupting the whole topic save.
_BRAND_TIERS = ("", "new", "mid", "leader")
_INDUSTRY_CLASSES = ("", "regular", "special")


def _clean_brand_tier(v: str | None) -> str:
    return v if v in _BRAND_TIERS else ""


def _clean_industry_class(v: str | None) -> str:
    return v if v in _INDUSTRY_CLASSES else ""


def _topic_engines(topic) -> set[str]:
    """Engine keys currently stored on the topic (as-is, without whitelist filtering)."""
    try:
        raw = json.loads(getattr(topic, "engines_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return set()
    return {str(e) for e in raw if e} if isinstance(raw, list) else set()


def _validate_payload(payload: TopicPayload, *,
                      known_engines: Optional[set[str]] = None) -> None:
    """Whitelist validation. `known_engines` = engine keys **already stored** on this topic, which are allowed through as well.

    Why already-stored values are allowed: the topic editor's engine selection UI is hidden (see the frontend TopicEditor section
    "engine selection UI is hidden"), and `engines` just echoes back the topic's existing values. After ops switches a topic
    to another collection channel (e.g. on 2026-09-09 switching Qwen/Wenxin to the physical-device qwen_browser /
    wenxin_browser), as long as this backend whitelist has not been synced, a user clicking "submit" on the query page,
    an action entirely unrelated to engines, would be unable to save the whole page, with an error naming an internal key they can neither understand
    nor change. The user did not provide this value, so they should not bear the consequences of it being invalid.

    Newly **added** invalid engines still get 400: that is a value the caller provided, and rejecting it is right.
    """
    allowed = VALID_ENGINES | (known_engines or set())
    bad = [e for e in payload.engines if e not in allowed]
    if bad:
        raise HTTPException(400, f"unknown engines: {bad}")
    # De-dup + strip: also remove/align query_cluster_ids / query_seeds / query_scene_types by position
    cluster_ids_in = payload.query_cluster_ids if payload.query_cluster_ids else []
    seeds_in = payload.query_seeds if payload.query_seeds else []
    scenes_in = payload.query_scene_types if payload.query_scene_types else []
    cleaned_queries: list[str] = []
    cleaned_clusters: list[int] = []
    cleaned_seeds: list[str] = []
    cleaned_scenes: list[str] = []
    seen: set[str] = set()
    for i, q in enumerate(payload.queries):
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            cleaned_queries.append(q)
            if i < len(cluster_ids_in):
                cleaned_clusters.append(int(cluster_ids_in[i]))
            if i < len(seeds_in):
                cleaned_seeds.append((seeds_in[i] or "").strip()[:200])
            if i < len(scenes_in):
                sc = (scenes_in[i] or "").strip()
                cleaned_scenes.append(sc if sc in ALL_SCENES else "")
    if not cleaned_queries:
        raise HTTPException(400, "queries cannot be empty")
    payload.queries = cleaned_queries
    payload.query_cluster_ids = (
        cleaned_clusters if len(cleaned_clusters) == len(cleaned_queries) else None
    )
    payload.query_seeds = (
        cleaned_seeds if len(cleaned_seeds) == len(cleaned_queries) else None
    )
    payload.query_scene_types = (
        cleaned_scenes if len(cleaned_scenes) == len(cleaned_queries) else None
    )
    payload.engines = list(dict.fromkeys(payload.engines))
    # target / aliases cleanup
    payload.target = (payload.target or "").strip()
    aliases_clean: list[str] = []
    seen_a: set[str] = set()
    for a in (payload.target_aliases or []):
        a = a.strip()
        if a and a.lower() not in seen_a:
            seen_a.add(a.lower())
            aliases_clean.append(a)
    payload.target_aliases = aliases_clean


def _queries_with_meta(payload_queries: list[str], existing_raw: str | None,
                       cluster_ids: list[int] | None = None,
                       seeds: list[str] | None = None,
                       scene_types: list[str] | None = None,
                       *, new_status: str = "approved",
                       reviewer_id: int | None = None) -> str:
    """Upgrade the query list to the [{text, created_at, cluster_id?, status, ...}] shape.

    Since Phase C every query carries `status` (pending/approved/rejected):
    - existing queries: keep the original status / created_at / cluster_id / submitted_at / approved_at
    - newly added queries: default status="approved" (queries are no longer reviewed since 2026-09-09, see the
      notes in update_topic); pass new_status="pending" to restore the review flow
    - queries are no longer reviewed, and there is no longer the "approved queries cannot be deleted" lock (see update_topic)
    """
    now_iso = datetime.utcnow().isoformat()
    existing_by_text: dict[str, dict] = {}
    try:
        for q in json.loads(existing_raw or "[]"):
            if isinstance(q, dict) and q.get("text"):
                existing_by_text[q["text"]] = q
            elif isinstance(q, str):
                # Old plain string: treat as existing since topic creation and add approved status
                existing_by_text[q] = {
                    "text": q, "created_at": now_iso,
                    "status": "approved", "approved_at": now_iso,
                }
    except Exception:  # noqa: BLE001
        pass
    cluster_ok = isinstance(cluster_ids, list) and len(cluster_ids) == len(payload_queries)
    seeds_ok = isinstance(seeds, list) and len(seeds) == len(payload_queries)
    scenes_ok = isinstance(scene_types, list) and len(scene_types) == len(payload_queries)
    out = []
    seen_text: set[str] = set()
    for i, q in enumerate(payload_queries):
        # Dedup within the payload: take only the first occurrence of each text, dropping the corresponding cluster_id / seed
        if q in seen_text:
            continue
        seen_text.add(q)
        prev = existing_by_text.get(q)
        if prev is not None:
            item = dict(prev)
            item.setdefault("created_at", now_iso)
            item.setdefault("status", "approved")
            # A soft-deleted query appearing in the payload again = the user added it back -> restore it.
            # Without clearing, "adding it again" would be the same as not adding it (the query would still be filtered out throughout the pipeline).
            item.pop("deleted_at", None)
            item.pop("deleted_by_id", None)
        else:
            item = {
                "text": q,
                "created_at": now_iso,
                "status": new_status,
                "submitted_at": now_iso,
            }
            if new_status == "approved":
                item["approved_at"] = now_iso
                if reviewer_id is not None:
                    item["reviewer_id"] = reviewer_id
        cid: int | None = None
        if cluster_ok and isinstance(cluster_ids[i], int) and int(cluster_ids[i]) >= 0:
            cid = int(cluster_ids[i])
        elif isinstance(item.get("cluster_id"), int) and item["cluster_id"] >= 0:
            cid = int(item["cluster_id"])
        if cid is not None:
            item["cluster_id"] = cid
        # seed: backfill if the payload carries one (an empty string does not overwrite an existing non-empty seed, to avoid accidentally clearing historical ownership).
        if seeds_ok:
            new_seed = (seeds[i] or "").strip()
            if new_seed and not item.get("seed"):
                item["seed"] = new_seed
        # scene_type: backfill if the payload carries a valid value; existing non-search tags are not overwritten
        # (the legacy default search was not truly chosen by the user, so it may be overwritten by non-search; same rule as selected-queries).
        if scenes_ok:
            new_scene = (scene_types[i] or "").strip()
            if new_scene in ALL_SCENES:
                existing_scene = item.get("scene_type")
                if not existing_scene or existing_scene == "search":
                    item["scene_type"] = new_scene
        out.append(item)
    # ★ Soft-deleted items are passed through intact. This function is the **only** write point in the repo that physically drops query elements
    #   (out is rebuilt only from the payload), and the frontend query list hides soft-deleted queries by default, so the payload naturally lacks them;
    #   without this section, a single casual "save" by an admin would erase soft-deleted queries: no change log, unrecoverable,
    #   scene_type lost along with them, and historical brand query answers would degrade into category queries, inflating the exposure rate
    #   (see the comments on models.QueryItem.deleted_at and deleted_query_texts).
    for text, prev in existing_by_text.items():
        if text not in seen_text and prev.get("deleted_at"):
            out.append(dict(prev))
    return json.dumps(out, ensure_ascii=False)


def _append_seed_prompts(existing_raw: str | None, texts: list[str] | None,
                          *, new_status: str = "approved",
                          reviewer_id: int | None = None) -> str:
    """Append payload.seed_drafts (if non-empty) to seed_prompts_json one by one.

    Idempotent: skip if the text is already in the list (regardless of status).
    If texts is None / an empty list / all empty strings, return existing as-is.
    new_status defaults to "approved": seed queries are no longer reviewed since 2026-09-09.
    """
    raw = existing_raw or "[]"
    if not texts:
        return raw
    try:
        items = json.loads(raw)
    except Exception:  # noqa: BLE001
        items = []
    if not isinstance(items, list):
        items = []
    existing_texts = {s["text"] for s in items if isinstance(s, dict) and s.get("text")}
    now_iso = datetime.utcnow().isoformat()
    changed = False
    for raw_text in texts:
        cleaned = (raw_text or "").strip()
        if not cleaned or cleaned in existing_texts:
            continue
        item = {
            "text": cleaned,
            "status": new_status,
            "submitted_at": now_iso,
        }
        if new_status == "approved":
            item["approved_at"] = now_iso
            if reviewer_id is not None:
                item["reviewer_id"] = reviewer_id
        items.append(item)
        existing_texts.add(cleaned)
        changed = True
    return json.dumps(items, ensure_ascii=False) if changed else raw


def _validate_query_diff(payload_queries: list[str], existing_raw: str | None) -> None:
    """**Disabled** (2026-09-09: with queries no longer reviewed, this lock would lock users out of queries they just added,
    see the notes in update_topic). The function body is kept so it can be reconnected in one line if the review flow is restored.

    Phase C is append-only: approved queries must not disappear from the payload.

    text is the query's identity; if the user changes the wording of an approved query, that equals "delete one, add one",
    and this operation is blocked: raise 422 with code=LOCKED_FIELD.
    """
    approved_texts: set[str] = set()
    try:
        for q in json.loads(existing_raw or "[]"):
            # Soft-deleted queries are not in the frontend query list to begin with and are naturally absent from the payload, so they must not block saving
            # (the elements remain in queries_json and are passed through as-is by _queries_with_meta)
            if isinstance(q, dict) and q.get("deleted_at"):
                continue
            if isinstance(q, dict) and q.get("status") == "approved" and q.get("text"):
                approved_texts.add(q["text"])
    except Exception:  # noqa: BLE001
        return  # Skip on parse failure; do not block the update
    payload_set = set(payload_queries)
    missing = approved_texts - payload_set
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LOCKED_FIELD",
                "field": "queries",
                "message": "approved queries 不可删除或修改 text",
                "locked_items": sorted(missing),
            },
        )


def _get_topic_or_404(
    db: Session,
    topic_id: int,
    user_id: int,
    *,
    allow_admin_user: Optional[User] = None,
    action: str = ACTION_WRITE,
) -> AiTelemetryTopicORM:
    """Topic reads/writes all go through enterprise authorization."""
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t:
        from geo.services.access_control import resource_not_found
        raise resource_not_found()
    actor = allow_admin_user or db.get(UserORM, user_id)
    if actor is None:
        from geo.services.access_control import resource_not_found
        raise resource_not_found()
    require_resource_access(db, actor, t, action)
    return t


def _resolve_period(
    period: int, topic: AiTelemetryTopicORM, now: Optional[datetime] = None,
) -> tuple[int, datetime]:
    """Parse the time window, returning (period_days, period_start).

    - period <= 0 -> all history: counted from topic.created_at (falling back to 10 years ago if absent) until now,
      with period_days converging to the actual span (it will not balloon into a trend of tens of thousands of days).
    - period > 0  -> the last period days (capped at 90, following the historical metric definition).
    """
    now = now or datetime.utcnow()
    if period <= 0:
        start = topic.created_at or (now - timedelta(days=3650))
        days = max(1, (now - start).days + 1)
        return days, start
    days = max(1, min(period, 90))
    return days, now - timedelta(days=days)


# ─────────────────────────── CRUD ──────────────────────────────


# ─────── admin cross-user management (/workbench/accounts) ────────


def _require_admin(user: User) -> None:
    # The dispatch center / external gateway (admin/* endpoints at line 3445+ of this file) are open to the maintenance role;
    # other admin data remains admin-only (not visible in the maintenance role's menu either).
    from geo.api.auth import _user_role
    if _user_role(user) not in ("admin", "maintainer"):
        raise HTTPException(403, "admin only")


def _require_workbench(user: User) -> None:
    """The five workspace modules (project progress / profiles / copy re-review / audit reports / batch-run results): admin | operator"""
    from geo.api.auth import is_staff
    if not is_staff(user):
        raise HTTPException(403, "权限不足")


class AdminAccountSummary(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    # The brand name the customer filled in ai_telemetry_topics.target (e.g. "Example Cleaning").
    # The admin profile list shows this first: better than users.name (an internal admin note)
    # at representing the customer's identity. Take the earliest-created topic.target, the customer's "main brand".
    brand_target: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    topic_count: int
    has_prompt_extension: bool


@router.get("/admin/accounts", response_model=list[AdminAccountSummary])
def admin_list_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_workbench(current_user)
    from geo.models.user import UserORM
    # Sort by id desc: UserORM has no created_at field and id auto-increments, so it serves as a proxy for "creation time".
    # The admin profile list wants the most recently registered customers first.
    users = db.query(UserORM).order_by(UserORM.id.desc()).all()
    out: list[AdminAccountSummary] = []
    for u in users:
        topics = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.user_id == u.id)
              .order_by(AiTelemetryTopicORM.id.asc())
              .all()
        )
        has_ext = any((t.prompt_extension or "").strip() for t in topics)
        first_target = next(
            ((t.target or "").strip() for t in topics if (t.target or "").strip()),
            None,
        )
        out.append(AdminAccountSummary(
            id=u.id,
            email=u.email or "",
            name=getattr(u, "name", None),
            brand_target=first_target,
            is_active=getattr(u, "is_active", True),
            is_admin=bool(getattr(u, "is_admin", False)),
            topic_count=len(topics),
            has_prompt_extension=has_ext,
        ))
    return out


@router.get("/admin/users/{user_id}/topics", response_model=list[TopicOut])
def admin_list_user_topics(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_workbench(current_user)
    rows = (
        db.query(AiTelemetryTopicORM)
          .filter_by(user_id=user_id)
          .order_by(AiTelemetryTopicORM.created_at.desc())
          .all()
    )
    return [TopicOut.from_orm_row(r) for r in rows]


@router.get("/admin/runs", response_model=list[AdminRunOut])
def admin_list_runs(
    day: Optional[str] = None,
    user_id: Optional[int] = None,
    topic_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin cross-user / cross-topic batch-run overview.
    - day=YYYY-MM-DD: filter by that day (UTC); if omitted, list the most recent
    - user_id / topic_id / status: optional filters
    - sorted by started_at DESC, 100 rows by default
    """
    _require_workbench(current_user)
    from geo.models.user import UserORM
    from sqlalchemy import case

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    q = (
        db.query(AiTelemetryRunORM, AiTelemetryTopicORM, UserORM)
          .join(AiTelemetryTopicORM, AiTelemetryRunORM.topic_id == AiTelemetryTopicORM.id)
          .join(UserORM, UserORM.id == AiTelemetryTopicORM.user_id)
          .order_by(AiTelemetryRunORM.started_at.desc())
    )
    if day:
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "day 必须是 YYYY-MM-DD")
        next_d = d + timedelta(days=1)
        q = q.filter(
            AiTelemetryRunORM.started_at >= d,
            AiTelemetryRunORM.started_at < next_d,
        )
    if user_id is not None:
        q = q.filter(UserORM.id == user_id)
    if topic_id is not None:
        q = q.filter(AiTelemetryRunORM.topic_id == topic_id)
    if status:
        q = q.filter(AiTelemetryRunORM.status == status)
    rows = q.offset(offset).limit(limit).all()

    run_ids = [run.id for run, _, _ in rows]
    stats: dict[int, dict[str, int]] = {}
    if run_ids:
        result = (
            db.query(
                AiTelemetryResponseORM.run_id,
                func.count(AiTelemetryResponseORM.id).label("total"),
                func.sum(case((AiTelemetryResponseORM.hit == True, 1), else_=0)).label("hits"),  # noqa: E712
                func.sum(case((AiTelemetryResponseORM.error.isnot(None), 1), else_=0)).label("errs"),
            )
            .filter(AiTelemetryResponseORM.run_id.in_(run_ids))
            .group_by(AiTelemetryResponseORM.run_id)
            .all()
        )
        for rid, total, hits, errs in result:
            stats[rid] = {
                "total": int(total or 0),
                "hits": int(hits or 0),
                "errs": int(errs or 0),
            }

    out: list[AdminRunOut] = []
    for run, topic, user in rows:
        s = stats.get(run.id, {"total": 0, "hits": 0, "errs": 0})
        out.append(AdminRunOut(
            run_id=run.id,
            topic_id=topic.id,
            topic_name=topic.name,
            topic_target=topic.target or "",
            user_id=user.id,
            user_email=user.email or "",
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            error=run.error,
            response_count=s["total"],
            hit_count=s["hits"],
            error_count=s["errs"],
        ))
    return out


@router.get("/admin/runs/{run_id}", response_model=AdminRunOut)
def admin_get_run(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin fetches single run metadata (for the top of the detail page)."""
    _require_workbench(current_user)
    from geo.models.user import UserORM
    from sqlalchemy import case

    row = (
        db.query(AiTelemetryRunORM, AiTelemetryTopicORM, UserORM)
          .join(AiTelemetryTopicORM, AiTelemetryRunORM.topic_id == AiTelemetryTopicORM.id)
          .join(UserORM, UserORM.id == AiTelemetryTopicORM.user_id)
          .filter(AiTelemetryRunORM.id == run_id)
          .first()
    )
    if not row:
        raise HTTPException(404, "run not found")
    run, topic, user = row

    stats = (
        db.query(
            func.count(AiTelemetryResponseORM.id).label("total"),
            func.sum(case((AiTelemetryResponseORM.hit == True, 1), else_=0)).label("hits"),  # noqa: E712
            func.sum(case((AiTelemetryResponseORM.error.isnot(None), 1), else_=0)).label("errs"),
        )
        .filter(AiTelemetryResponseORM.run_id == run_id)
        .one()
    )

    return AdminRunOut(
        run_id=run.id,
        topic_id=topic.id,
        topic_name=topic.name,
        topic_target=topic.target or "",
        user_id=user.id,
        user_email=user.email or "",
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        error=run.error,
        response_count=int(stats[0] or 0),
        hit_count=int(stats[1] or 0),
        error_count=int(stats[2] or 0),
    )


@router.post(
    "/admin/users/{user_id}/topics",
    response_model=TopicOut,
    status_code=201,
)
def admin_create_topic_for_user(
    user_id: int,
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin creates a topic directly on behalf of a given user: skips review, approved on persistence.

    Unlike the user's three-step create_topic + submit-for-review + admin approve,
    this is done in one step: all queries/seeds status=approved, topic.submission_status=approved.
    """
    _require_workbench(current_user)
    from geo.models.user import UserORM
    target_user = db.get(UserORM, user_id)
    if not target_user:
        raise HTTPException(404, "user not found")
    _validate_payload(payload)
    clusters_dump = [c.model_dump() for c in payload.clusters] if payload.clusters else []
    seed_prompts_init = _append_seed_prompts(
        "[]", payload.seed_drafts,
        new_status="approved", reviewer_id=current_user.id,
    )
    profile_init = "{}"
    if payload.profile is not None:
        profile_init = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
    now = datetime.utcnow()
    t = AiTelemetryTopicORM(
        user_id=user_id,
        name=payload.name.strip(),
        target=payload.target,
        target_aliases_json=json.dumps(payload.target_aliases, ensure_ascii=False),
        industry=payload.industry,
        seed_prompts_json=seed_prompts_init,
        queries_json=_queries_with_meta(
            payload.queries, None, payload.query_cluster_ids, payload.query_seeds,
            payload.query_scene_types,
            new_status="approved", reviewer_id=current_user.id,
        ),
        clusters_json=json.dumps(clusters_dump, ensure_ascii=False),
        engines_json=json.dumps(payload.engines, ensure_ascii=False),
        enabled=payload.enabled,
        schedule_tier=_tier_for_enabled(payload.enabled),   # The two fields must agree; see the ORM comment
        profile_json=profile_init,
        prompt_extension=payload.prompt_extension,
        submission_status="approved",
        approved_at=now,
        reviewer_id=current_user.id,
        brand_tier=_clean_brand_tier(payload.brand_tier),
        industry_class=_clean_industry_class(payload.industry_class),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    _append_changelog(
        t, actor_id=current_user.id, actor_role="admin",
        field="submission_status", after="approved",
        note=f"admin created for user {user_id}",
    )
    _sync_sales_brand(db, t)
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


def _last_run_errors(db: Session, topic_ids: list[int]) -> dict[int, str]:
    """The raw error cause of **the latest run** of each topic. One join, no N+1.

    Why not add a column on ai_telemetry_topics: the `last_run_status` cache column is written by
    telemetry-service (storage.start_run / finish_run), and another column would have to be maintained in sync in a second
    codebase, the same kind of debt as the "intentionally duplicated" pair parse_topic / active_query_texts.
    The run table is the single source of truth for error causes anyway; querying it now is cheaper and cannot drift.

    "Latest run" uses max(id) rather than max(started_at): when two runs are created in the same second
    started_at ties, id does not.
    """
    if not topic_ids:
        return {}
    # expanding bindparam: works on both PG and sqlite, no need for two dialect-specific versions
    # (`= ANY(:ids)` is PG-only; binding a tuple to a bare `IN :ids` in SQLAlchemy 2.x raw text
    #  blows up as `near "?": syntax error`; the self-check DB is sqlite, and we hit this).
    # **Return empty if unavailable, do not raise**: last_run_error is a decorative field giving the card an explanation,
    # while the topic list is a core path: it must not 500 the whole page because the runs table is missing / the query crashed.
    # (The self-check has a slim fixture that creates topics but not runs, and this must quietly back off then.)
    stmt = sa_text("""
        SELECT r.topic_id, r.error
        FROM ai_telemetry_runs r
        JOIN (SELECT topic_id, MAX(id) AS mid
                FROM ai_telemetry_runs
               WHERE topic_id IN :ids
               GROUP BY topic_id) m ON m.mid = r.id
    """).bindparams(bindparam("ids", expanding=True))
    try:
        rows = db.execute(stmt, {"ids": list(topic_ids)}).all()
    except Exception:  # noqa: BLE001
        log.debug("last_run_error query failed; topic list returned as usual", exc_info=True)
        return {}
    return {int(tid): err for tid, err in rows if err}


@router.get("/topics", response_model=list[TopicOut])
def list_topics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    enterprise_user_id: int | None = None,
):
    query = apply_enterprise_scope(
        db.query(AiTelemetryTopicORM), db, current_user, AiTelemetryTopicORM.user_id,
        enterprise_user_id,
    )
    rows = query.order_by(AiTelemetryTopicORM.created_at.desc()).all()
    errs = _last_run_errors(db, [r.id for r in rows])
    return [TopicOut.from_orm_row(r, last_run_error=errs.get(r.id)) for r in rows]


@router.get("/topics/{topic_id}", response_model=TopicOut)
def get_topic(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Single topic metadata: owners fetch their own; admins can fetch any topic (used by the brand growth panel to resolve non-owned topics via ?topic=)."""
    t = _get_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    return TopicOut.from_orm_row(
        t, last_run_error=_last_run_errors(db, [t.id]).get(t.id))


@router.post("/topics", response_model=TopicOut, status_code=201)
def create_topic(
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _validate_payload(payload)
    owner_user_id = payload.enterprise_user_id or current_user.id
    require_enterprise_access(db, current_user, owner_user_id, ACTION_WRITE)
    clusters_dump = [c.model_dump() for c in payload.clusters] if payload.clusters else []
    seed_prompts_init = _append_seed_prompts("[]", payload.seed_drafts)
    profile_init = "{}"
    if payload.profile is not None:
        profile_init = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
    t = AiTelemetryTopicORM(
        user_id=owner_user_id,
        name=payload.name.strip(),
        target=payload.target,
        target_aliases_json=json.dumps(payload.target_aliases, ensure_ascii=False),
        industry=payload.industry,
        seed_prompts_json=seed_prompts_init,
        queries_json=_queries_with_meta(
            payload.queries, None, payload.query_cluster_ids, payload.query_seeds,
            payload.query_scene_types,
        ),
        clusters_json=json.dumps(clusters_dump, ensure_ascii=False),
        engines_json=json.dumps(payload.engines, ensure_ascii=False),
        enabled=payload.enabled,
        schedule_tier=_tier_for_enabled(payload.enabled),   # The two fields must agree; see the ORM comment
        profile_json=profile_init,
        # Topics created by admin themselves can write prompt_extension; ignored for regular users even if submitted
        prompt_extension=(payload.prompt_extension if getattr(current_user, "is_admin", False) else None),
        brand_tier=_clean_brand_tier(payload.brand_tier),
        industry_class=_clean_industry_class(payload.industry_class),
    )
    db.add(t)
    db.flush()
    _, operation = create_operation_context(
        db, current_user, owner_user_id,
        action="topic.create", target_resource_type="topic",
        target_resource_id=t.id,
    )
    append_system_audit(
        db, operation_context=operation, event_type="resource_created",
        action="topic.create", object_type="topic", object_id=t.id,
        result="success",
    )
    # Append the profile creation action to the changelog (the changelog is still empty at creation, so add one directly)
    if payload.profile is not None:
        _append_changelog(
            t, actor_id=current_user.id, actor_role="user", field="profile",
            after=payload.profile.profile_name or payload.profile.company_short_name,
            note="created with profile",
        )
        _sync_sales_brand(db, t)
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.post("/topics/draft", response_model=TopicOut, status_code=201)
def create_draft_topic(
    payload: TopicDraftPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1 "next" persists immediately: only stores the brand profile (6 modules) + basic fields; queries/engines may be empty,
    submission_status=draft. Users get a topic.id as soon as the profile is filled, and later steps update incrementally instead of saving everything at the end.
    Later step 3 uses PUT /topics/{id} to add queries/engines + submit for review."""
    owner_user_id = payload.enterprise_user_id or current_user.id
    require_enterprise_access(db, current_user, owner_user_id, ACTION_WRITE)
    # Validate engines against the whitelist only if given (may be empty at the draft stage); dedup preserving order
    engines: list[str] = []
    for e in (payload.engines or []):
        if e in VALID_ENGINES and e not in engines:
            engines.append(e)
    name = payload.name.strip()
    target = payload.target.strip()
    industry = payload.industry.strip()
    profile_init = "{}"
    if payload.profile is not None:
        profile_init = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
        # Same as update_topic_profile: backfill the profile's short name / industry / name into the topic's top-level fields
        if payload.profile.industry and not industry:
            industry = payload.profile.industry
        if payload.profile.company_short_name and not target:
            target = payload.profile.company_short_name
        if payload.profile.profile_name and not name:
            name = payload.profile.profile_name
    t = AiTelemetryTopicORM(
        user_id=owner_user_id,
        name=name or "(unnamed)",
        target=target,
        target_aliases_json=json.dumps(payload.target_aliases, ensure_ascii=False),
        industry=industry,
        seed_prompts_json="[]",
        queries_json="[]",
        engines_json=json.dumps(engines, ensure_ascii=False),
        enabled=payload.enabled,
        schedule_tier=_tier_for_enabled(payload.enabled),   # The two fields must agree; see the ORM comment
        profile_json=profile_init,
        submission_status="draft",
        brand_tier=_clean_brand_tier(payload.brand_tier),
        industry_class=_clean_industry_class(payload.industry_class),
    )
    db.add(t)
    db.flush()
    _, operation = create_operation_context(
        db, current_user, owner_user_id,
        action="topic.create_draft", target_resource_type="topic",
        target_resource_id=t.id,
    )
    append_system_audit(
        db, operation_context=operation, event_type="resource_created",
        action="topic.create_draft", object_type="topic", object_id=t.id,
        result="success",
    )
    if payload.profile is not None:
        _append_changelog(
            t, actor_id=current_user.id, actor_role="user", field="profile",
            after=payload.profile.profile_name or payload.profile.company_short_name,
            note="draft created with profile",
        )
        _sync_sales_brand(db, t)
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.put("/topics/{topic_id}", response_model=TopicOut)
def update_topic(
    topic_id: int,
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Fetch the topic before validating: the whitelist must also allow "engine keys this topic already stores",
    # and without the topic we cannot tell which are newly provided by the user and which are just echoed back.
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    _validate_payload(payload, known_engines=_topic_engines(t))
    # Phase C: append-only: approved queries must not disappear from the payload.
    # 2026-05-31: the constraint is skipped for admin edits (admin already has auto-approve rights,
    # so deleting / changing approved queries should be allowed; regular user edits are still guarded, to prevent bypassing review).
    is_admin_actor = bool(getattr(current_user, "is_admin", False))
    # 2026-09-09: queries no longer go through review (see the new_status comment below). Since they are approved
    # as soon as they come in, the append-only constraint "approved queries must not disappear from the payload" would lock users
    # out of queries they just added: fixing a typo or deleting a query they do not want monitored would all get 422 LOCKED_FIELD.
    # With review gone, this lock has no reason to exist.
    t.name = payload.name.strip()
    t.target = payload.target
    t.target_aliases_json = json.dumps(payload.target_aliases, ensure_ascii=False)
    t.industry = payload.industry
    # Report center brand tags: written only if given (None = old client did not submit; leave existing tags untouched to avoid clearing them)
    if payload.brand_tier is not None:
        t.brand_tier = _clean_brand_tier(payload.brand_tier)
    if payload.industry_class is not None:
        t.industry_class = _clean_industry_class(payload.industry_class)
    # Phase D: when editing with a profile, write to the DB + record the changelog
    if payload.profile is not None:
        # merge: profiles submitted by the client have no admin section, and overwriting directly would erase config like the fixed answer denominator
        t.profile_json = merge_profile_json(t, payload.profile.model_dump())
        _append_changelog(
            t, actor_id=current_user.id, actor_role="user", field="profile",
            after=payload.profile.profile_name or payload.profile.company_short_name,
        )
        _sync_sales_brand(db, t)
    # Phase C: append when the payload carries seed_prompt / query.
    #
    # Since 2026-09-09 **neither seed queries nor expanded queries are reviewed**; whoever adds them, they are approved directly.
    # Queries added by regular users used to land as pending, waiting for ops to click in the admin backend before running, but in practice
    # nobody ever clicked: 849 pending queries in the DB were batch-run anyway (parse_topic never looks at
    # status), and the review step was reduced to a misleading "pending review" badge.
    # Now what stops uncontrolled query expansion is **money** (pre-charged per cell before running, geo/services/batch_prepay.py),
    # not manual review.
    new_status = "approved"
    reviewer_id = current_user.id if is_admin_actor else None
    t.seed_prompts_json = _append_seed_prompts(
        t.seed_prompts_json, payload.seed_drafts,
        new_status=new_status, reviewer_id=reviewer_id,
    )
    t.queries_json = _queries_with_meta(
        payload.queries, t.queries_json, payload.query_cluster_ids, payload.query_seeds,
        payload.query_scene_types,
        new_status=new_status, reviewer_id=reviewer_id,
    )
    # clusters is only overwritten when the payload passes it explicitly, so a PUT without clusters does not clear the old clusters
    if payload.clusters is not None:
        t.clusters_json = json.dumps(
            [c.model_dump() for c in payload.clusters], ensure_ascii=False,
        )
    t.engines_json = json.dumps(payload.engines, ensure_ascii=False)
    t.enabled = payload.enabled
    # Reverse sync: enabled is a derived cache of schedule_tier, and this full PUT still allows changing enabled
    # (user self-service disabling), but tier must be pulled back into line, otherwise the two fields contradict -> admin display conflicts with actual scheduling.
    from geo.services.topic_schedule import TIER_OFF, TIER_WEEKLY, normalize_tier
    _cur_tier = normalize_tier(getattr(t, "schedule_tier", None))
    if not payload.enabled and _cur_tier != TIER_OFF:
        t.schedule_tier = TIER_OFF
    elif payload.enabled and _cur_tier == TIER_OFF:
        t.schedule_tier = TIER_WEEKLY    # Recovering from "off" goes back to the default weekly
    # Only admin can change prompt_extension; regular users' payload.prompt_extension is always ignored (DB untouched)
    if getattr(current_user, "is_admin", False) and payload.prompt_extension is not None:
        t.prompt_extension = payload.prompt_extension.strip() or None
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _get_topic_or_404(db, topic_id, current_user.id)
    # With the profile gone, the corresponding brand should no longer appear on the sales assistant's selection card. **Soft delete**:
    # historical recommendation tasks, sent emails and "our profile" in old reports are all kept, just no longer selectable.
    try:
        from agents.sales_agent.service.brandprofile import archive_brand_for_topic
        archive_brand_for_topic(db, t.id, t.user_id)
    except Exception:  # noqa: BLE001
        log.exception("archive sales-agent brand for deleted topic failed (topic_id=%s)", topic_id)
    db.delete(t)
    db.commit()
    # topic_media rows go away with CASCADE, but files do not disappear on their own: clean the directory too, leave no orphans
    try:
        n = media_storage.remove_topic_dir(topic_id)
        if n:
            log.info("delete_topic %s: removed %d asset files", topic_id, n)
    except Exception:  # noqa: BLE001
        log.warning("delete_topic %s: asset directory cleanup failed", topic_id, exc_info=True)


# ─────────── Phase C: seed prompt review hardening ────────────


@router.post("/topics/{topic_id}/seed-prompts", response_model=TopicOut)
def submit_seed_prompt(
    topic_id: int,
    payload: SeedPromptSubmitPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Append new seed prompts. **No review**: approved directly whoever submits (2026-09-09)."""
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    try:
        seeds = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds = []
    if not isinstance(seeds, list):
        seeds = []
    text = payload.text.strip()
    # Seed query texts must not repeat within the same topic
    if any(isinstance(s, dict) and s.get("text") == text for s in seeds):
        raise HTTPException(409, {"code": "DUPLICATE", "message": "种子提示词已存在"})
    is_admin = bool(getattr(current_user, "is_admin", False))
    now_iso = datetime.utcnow().isoformat()
    item: dict = {
        "text": text,
        "status": "approved",
        "submitted_at": now_iso,
        "approved_at": now_iso,
    }
    if is_admin:
        item["reviewer_id"] = current_user.id
    seeds.append(item)
    t.seed_prompts_json = json.dumps(seeds, ensure_ascii=False)
    _append_changelog(
        t, actor_id=current_user.id,
        actor_role="admin" if is_admin else "user",
        field="seed_prompts", after=text,
        note="admin submitted new seed (auto-approved)" if is_admin else "user submitted new seed",
    )

    # 2026-05-26: seeds submitted by admin are auto-approved -> immediately derive a same-named query
    # (is_seed=True, locked=True, selected=True). Regular user submissions go to pending and, after admin
    # approval, are derived uniformly via admin_patch_topic; skipped here.
    if is_admin:
        try:
            qarr = json.loads(t.queries_json or "[]")
        except Exception:  # noqa: BLE001
            qarr = []
        q_by_text = {q.get("text"): q for q in qarr if isinstance(q, dict)}
        if text in q_by_text:
            q = q_by_text[text]
            q.update({"is_seed": True, "locked": True,
                      "selected": True, "seed": text})
            # The seed was deleted by delete_seed_prompt and added back: restore the same-named monitoring row with it
            q.pop("deleted_at", None)
            q.pop("deleted_by_id", None)
        else:
            qarr.append({
                "text": text, "status": "approved",
                "submitted_at": now_iso, "approved_at": now_iso,
                "reviewer_id": current_user.id,
                "seed": text, "is_seed": True, "locked": True,
                "selected": True, "cluster_id": -1,
            })
        t.queries_json = json.dumps(qarr, ensure_ascii=False)

    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


class SeedPromptDeletePayload(BaseModel):
    """Admin deletes seed prompts: POST /topics/{id}/seed-prompts/delete."""
    text: str = Field(..., min_length=1, max_length=256)
    # Also fully recompute this topic's wide table with the new metric definition (no recompute by default, same as delete_queries)
    rebuild_history: bool = False


@router.post("/topics/{topic_id}/seed-prompts/delete", response_model=TopicOut)
def delete_seed_prompt(
    topic_id: int,
    payload: SeedPromptDeletePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin deletes a seed prompt, **taking prompts expanded from it offline too**.

    · the seed itself is removed from seed_prompts_json (it has no historical answers to reverse-lookup; once removed, it is gone)
    · queries derived from it (seed == text, including the same-named is_seed one) are soft-deleted with deleted_at:
      same reason as delete_queries: elements are never removed from queries_json, as the historical metric definition relies on
      reverse lookup by wording; locked is also cleared, otherwise the frontend picker still treats it as a must-run query
    · if the same query also hangs under another seed (the seed field only records one) it is unaffected: matching is by the seed field only
    Side effects same as delete_queries: the report metric definition changes and in-flight Siyuan actions go stale. The frontend second confirmation must state this.
    """
    _require_admin(current_user)
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    text = payload.text.strip()
    try:
        seeds = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds = []
    if not isinstance(seeds, list):
        seeds = []
    kept = [s for s in seeds if not (isinstance(s, dict) and s.get("text") == text)]
    if len(kept) == len(seeds):
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "种子提示词不存在"})
    t.seed_prompts_json = json.dumps(kept, ensure_ascii=False)

    try:
        arr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        arr = []
    if not isinstance(arr, list):
        arr = []
    now_iso = datetime.utcnow().isoformat()
    deleted_n = 0
    upgraded: list[dict] = []
    for q in arr:
        item = dict(q) if isinstance(q, dict) else {
            "text": q, "status": "approved", "selected": True, "created_at": now_iso,
        }
        derived = (item.get("seed") or "") == text or (
            item.get("is_seed") and item.get("text") == text
        )
        if derived and not item.get("deleted_at"):
            item["deleted_at"] = now_iso
            item["deleted_by_id"] = current_user.id
            item["locked"] = False
            item["selected"] = False
            deleted_n += 1
        upgraded.append(item)
    if deleted_n:
        t.queries_json = json.dumps(upgraded, ensure_ascii=False)

    _append_changelog(
        t, actor_id=current_user.id, actor_role="admin",
        field="seed_prompts", before=text,
        after=f"deleted seed, expansions_deleted={deleted_n}",
        note="admin deleted seed (expansions soft-deleted)",
    )
    db.commit()
    db.refresh(t)
    if payload.rebuild_history and deleted_n:
        _spawn_rebuild_report_daily(topic_id)
    return TopicOut.from_orm_row(t)


# ─────────── Phase D: profile / submit for review / prompt selection ────────────


def _append_changelog(t: AiTelemetryTopicORM, *, actor_id: int | None, actor_role: str,
                       field: str, before: str | None = None, after: str | None = None,
                       note: str | None = None) -> None:
    """Append a record to the end of topic_changelog_json (append-only) + version += 1.

    Incrementing version is core to the audit and "revision number" semantics: each changelog entry corresponds to a version,
    and `version=N` is the snapshot after the Nth "meaningful state change". Callers must not bypass this, change
    the topic directly and forget to record the changelog.
    """
    try:
        log_arr = json.loads(t.topic_changelog_json or "[]")
    except Exception:  # noqa: BLE001
        log_arr = []
    if not isinstance(log_arr, list):
        log_arr = []
    # version increment: old tables (before the migration) may lack this column; getattr falls back to 1
    new_version = int(getattr(t, "version", 1) or 1) + 1
    t.version = new_version
    entry: dict = {
        "at": datetime.utcnow().isoformat(),
        "actor_id": actor_id,
        "actor_role": actor_role,
        "field": field,
        "version": new_version,
    }
    if before is not None:
        entry["before"] = before[:500]
    if after is not None:
        entry["after"] = after[:500]
    if note is not None:
        entry["note"] = note
    log_arr.append(entry)
    t.topic_changelog_json = json.dumps(log_arr, ensure_ascii=False)


def _sync_sales_brand(db: Session, t: AiTelemetryTopicORM) -> None:
    """Write this profile into the sales assistant's brand mirror row (`sales_agent_seller_profiles`).

    The profile is the **single source of truth** for profiles: the sales assistant's "our profile", the options on capability / target customer cards,
    email body material, and "whether it has report capability" are all derived from it. Previously each side kept its own copy, unrelated,
    so profiles customers carefully filled in never reached the sales pipeline, and the sales side asked all over again.

    Import inside the function: `agents.*` and `geo.*` reference each other (geo/__init__ already imports
    sales_agent.models), so a module-level import would create a cycle. Same approach as skillpay_api / account.
    A failure to write should not make saving the profile itself fail: the mirror is filled in automatically on the next profile save.
    """
    try:
        from agents.sales_agent.service.brandprofile import sync_brand_from_topic
        sync_brand_from_topic(db, t)
    except Exception:  # noqa: BLE001
        log.exception("sync sales-agent brand from topic failed (topic_id=%s)", getattr(t, "id", None))


def _append_expansion_log(t: AiTelemetryTopicORM, *, seed: str, model: str,
                           expanded_count: int, raw_excerpt: str,
                           scene: str = "search") -> None:
    """Append a "seed query expansion" call record to the end of expansion_log_json.

    Since 2026-05-28 a scene field records which dimension this expansion took (search/qa/intent/brand).
    Legacy logs lack the scene field; the frontend falls back to "search" when displaying.
    """
    try:
        log_arr = json.loads(t.expansion_log_json or "[]")
    except Exception:  # noqa: BLE001
        log_arr = []
    if not isinstance(log_arr, list):
        log_arr = []
    log_arr.append({
        "at": datetime.utcnow().isoformat(),
        "seed": seed[:200],
        "scene": scene,
        "model": model,
        "expanded_count": int(expanded_count),
        "raw_excerpt": (raw_excerpt or "")[:300],
    })
    t.expansion_log_json = json.dumps(log_arr, ensure_ascii=False)


def _tier_for_enabled(enabled: bool) -> str:
    """When creating a topic, derive schedule_tier from payload.enabled: new topics default to off (not in periodic batch runs),
    and only an explicit enabled=True lands as weekly. enabled is a derived cache of tier, and both must be written together on row creation,
    otherwise ORM default off + enabled=True means "admin shows not running, drip runs anyway"."""
    from geo.services.topic_schedule import TIER_OFF, TIER_WEEKLY

    return TIER_WEEKLY if enabled else TIER_OFF


def _ensure_editable(t: AiTelemetryTopicORM) -> None:
    """Profile edit permission: users can edit when submission_status is in {draft, rejected}; pending/approved cannot be touched."""
    s = (t.submission_status or "draft")
    if s not in ("draft", "rejected"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOCKED_STATUS",
                "field": "submission_status",
                "message": f"submission_status={s},不允许编辑;请等待 admin 审核",
            },
        )


def _validate_selected_cap(queries_json_str: str | None) -> None:
    """The total number of selected=True queries must not exceed MAX_SELECTED_QUERIES."""
    try:
        arr = json.loads(queries_json_str or "[]")
    except Exception:  # noqa: BLE001
        return
    if not isinstance(arr, list):
        return
    n = sum(1 for q in arr if isinstance(q, dict) and bool(q.get("selected", True)))
    if n > MAX_SELECTED_QUERIES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TOO_MANY_SELECTED",
                "field": "queries",
                "message": f"最多勾选 {MAX_SELECTED_QUERIES} 个提示词,当前 {n}",
            },
        )


@router.put("/topics/{topic_id}/profile", response_model=TopicOut)
def update_topic_profile(
    topic_id: int,
    payload: BrandProfile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the topic's brand profile (6 modules). Only allowed in draft/rejected status."""
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    old_profile_summary = ""
    try:
        old = json.loads(t.profile_json or "{}")
        old_profile_summary = old.get("profile_name") or old.get("company_short_name") or ""
    except Exception:  # noqa: BLE001
        pass
    t.profile_json = merge_profile_json(t, payload.model_dump())   # Keep the admin section
    # Also backfill industry / target into the topic's top-level fields, consistent with profile
    if payload.industry:
        t.industry = payload.industry
    if payload.company_short_name and not t.target:
        t.target = payload.company_short_name
    if payload.profile_name and (t.name == "" or t.name == "(unnamed)"):
        t.name = payload.profile_name
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="profile",
        before=old_profile_summary or None,
        after=payload.profile_name or payload.company_short_name or None,
    )
    _sync_sales_brand(db, t)
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


# ── Fixed answer denominator (admin config, stored in the admin section of the profile) ────────────────────────


class FixedDenominatorPayload(BaseModel):
    """Admin switches for the report metric definition. fixed_response_count = None / 0 -> clear the setting.

    Both fields are "omitted = unchanged": when the frontend changes only one switch it need not send back the other.
    """
    fixed_response_count: Optional[int] = Field(None, ge=0, le=1_000_000)
    # The share-of-voice numerator only counts category query hits (excluding brand queries); omitted = keep as-is
    mention_cat_scope: Optional[bool] = None


class FixedDenominatorOut(BaseModel):
    topic_id: int
    fixed_response_count: Optional[int]
    mention_cat_scope: bool = False
    # Reference value: theoretical full coverage computed from the current vocabulary x engine count, for the admin to compare when entering a number
    query_count: int
    engine_count: int
    full_coverage: int
    # Theoretical full coverage under the category query metric definition: the exposure rate denominator is **category query** answers (excluding brand/competitor comparison queries),
    # while the citation rate denominator is answers across the whole vocabulary; the two scales share the same floor value, so look at both when entering a number
    cat_query_count: int = 0
    cat_full_coverage: int = 0
    # Median of actual category query answers over the last 4 closed ISO weeks: a floor below it is as good as having no effect
    recent_cat_valid: int = 0


def _fixed_denominator_out(t, db=None) -> "FixedDenominatorOut":
    try:
        qs = json.loads(t.queries_json or "[]")
        n_q = len(qs) if isinstance(qs, list) else 0
    except Exception:  # noqa: BLE001
        qs, n_q = [], 0
    try:
        n_e = len(json.loads(t.engines_json or "[]"))
    except Exception:  # noqa: BLE001
        n_e = 0
    n_cat = _cat_query_count(t, qs)
    return FixedDenominatorOut(
        topic_id=t.id, fixed_response_count=fixed_response_count(t),
        mention_cat_scope=mention_cat_scope(t),
        query_count=n_q, engine_count=n_e, full_coverage=n_q * n_e,
        cat_query_count=n_cat, cat_full_coverage=n_cat * n_e,
        recent_cat_valid=_recent_cat_valid(db, t.id) if db is not None else 0,
    )


def _cat_query_count(t, qs: list) -> int:
    """Category query count (excluding brand/competitor comparison scenes): the scale of the exposure rate denominator."""
    from geo.api.ai_telemetry_reports import EXPOSURE_ONLY_SCENES

    n = 0
    for q in qs:
        if not isinstance(q, dict):
            n += 1
            continue
        if (q.get("scene_type") or "unknown") not in EXPOSURE_ONLY_SCENES:
            n += 1
    return n


def _recent_cat_valid(db, topic_id: int) -> int:
    """Median of actual category query **combination counts** over the last 4 closed ISO weeks (cell metric definition; returns 0 if unavailable).

    ★ Must not sum over the wide table: the wide table stores daily answer counts, and summing across days counts the same "question x platform"
      combination several times (3 rounds a week means 3x). An admin entering a floor from that number, inflated twofold,
      would set a floor that never takes effect. Metric definition: see _cell_counts_for.
    """
    from collections import defaultdict

    from geo.api.ai_telemetry_reports import (
        _bj_date, _cell_counts_for, _deleted_queries, _metric_excl_queries, _week_key,
    )

    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        return 0
    try:
        rows = (
            db.query(AiTelemetryResponseORM)
              .filter(AiTelemetryResponseORM.topic_id == topic_id)
              .all()
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        return 0
    excl = _metric_excl_queries(t)
    gone = _deleted_queries(db, topic_id)
    by_week: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.query in gone:
            continue
        by_week[_week_key(_bj_date(r.created_at))].append(r)
    cur = _week_key(_bj_date(datetime.utcnow()))
    keys = [k for k in sorted(by_week) if k != cur][-4:]     # The current in-progress week does not count
    vals = sorted(_cell_counts_for(by_week[k], set(), {}, excl)["cat_valid_count"]
                  for k in keys)
    return int(vals[len(vals) // 2]) if vals else 0


@router.get("/topics/{topic_id}/fixed-denominator", response_model=FixedDenominatorOut)
def get_fixed_denominator(
    topic_id: int,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail="topic not found")
    return _fixed_denominator_out(t, db)


@router.put("/topics/{topic_id}/fixed-denominator", response_model=FixedDenominatorOut)
def set_fixed_denominator_endpoint(
    topic_id: int,
    payload: FixedDenominatorPayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set this topic's answer denominator **floor value**: keeps curves comparable while queries are added in batches and actual volume has not ramped up.

    Semantics is a denominator lower bound: denom = max(actual denominator, floor), automatically returning to the real denominator once actual volume exceeds the floor
    (metric definition in ai_telemetry_reports._fixed_valid_ctx and docs/03-系统设计/报告与指标/报告口径-应答分母托底.md).
    Does not go through _ensure_editable: this number is most needed after the project is running, and must not be blocked by "only editable in draft".
    """
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail="topic not found")
    try:
        prof = json.loads(t.profile_json or "{}")
        if not isinstance(prof, dict):
            prof = {}
    except Exception:  # noqa: BLE001
        prof = {}
    seg = dict(prof.get(PROFILE_ADMIN_KEY) or {})
    before = seg.get(FIXED_RESPONSE_KEY)
    n = payload.fixed_response_count
    # ★ Judge by "whether the field appears in the request body", not by whether the value is None:
    #   when the frontend toggles one switch it only sends back that one field, and treating None as "clear" would silently erase the other setting
    #   (measured 2026-08-07: ticking the share-of-voice metric definition wiped out the fixed denominator 800).
    provided = payload.model_fields_set
    if "fixed_response_count" in provided:
        if n:
            seg[FIXED_RESPONSE_KEY] = int(n)
        else:
            seg.pop(FIXED_RESPONSE_KEY, None)      # Only an explicit null / 0 clears it
    if "mention_cat_scope" in provided:
        if payload.mention_cat_scope:
            seg[MENTION_CAT_KEY] = True
        else:
            seg.pop(MENTION_CAT_KEY, None)
    if seg:
        prof[PROFILE_ADMIN_KEY] = seg
    else:
        prof.pop(PROFILE_ADMIN_KEY, None)
    t.profile_json = json.dumps(prof, ensure_ascii=False)
    _append_changelog(
        t, actor_id=admin.id, actor_role="admin", field="report_basis_settings",
        before=str(before) if before else None,
        after=f"分母托底={seg.get(FIXED_RESPONSE_KEY) or '不托底'} "
              f"声量度品类词={bool(seg.get(MENTION_CAT_KEY))}",
    )
    db.commit()
    db.refresh(t)
    return _fixed_denominator_out(t, db)


@router.post("/topics/{topic_id}/selected-queries", response_model=TopicOut)
def update_selected_queries(
    topic_id: int,
    payload: SelectedQueriesPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update each item's `selected` field in queries_json based on the user's selection.

    Input items match existing queries by text; texts not in the payload default to selected=False.
    New texts (in the payload but not in queries_json) are added to queries_json directly, status=pending + selected=value.
    """
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    try:
        arr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        arr = []
    if not isinstance(arr, list):
        arr = []
    # Upgrade legacy str -> dict
    upgraded: list[dict] = []
    for q in arr:
        if isinstance(q, str):
            upgraded.append({"text": q, "status": "approved", "selected": True})
        elif isinstance(q, dict) and q.get("text"):
            upgraded.append(dict(q))
    by_text = {q["text"]: q for q in upgraded}
    now_iso = datetime.utcnow().isoformat()
    desired_texts: set[str] = set()
    for item in payload.items:
        text = item.text.strip()
        if not text:
            continue
        desired_texts.add(text)
        seed = (getattr(item, "seed", None) or "").strip()[:200]
        scene_type = getattr(item, "scene_type", None) or "search"
        if scene_type not in ALL_SCENES:
            scene_type = "search"
        if text in by_text:
            by_text[text]["selected"] = bool(item.selected)
            # Backfill when the first submission carries a seed; an existing non-empty seed is not overwritten, to avoid accidentally changing historical ownership
            if seed and not by_text[text].get("seed"):
                by_text[text]["seed"] = seed
            # Likewise, backfill when scene_type is first provided; when a legacy item lacks the field or is search,
            # a newly passed non-search scene may overwrite it (the legacy default search was not truly chosen by the user).
            existing_scene = by_text[text].get("scene_type")
            if scene_type != "search" and (not existing_scene or existing_scene == "search"):
                by_text[text]["scene_type"] = scene_type
        else:
            new_q: dict = {
                "text": text,
                "status": "approved",       # Queries are no longer reviewed (2026-09-09)
                "submitted_at": now_iso,
                "selected": bool(item.selected),
                "scene_type": scene_type,
            }
            if seed:
                new_q["seed"] = seed
            upgraded.append(new_q)
            by_text[text] = upgraded[-1]
    # Those not in the payload: keep the original status, selected=False, unless locked (original seed query)
    for text, q in by_text.items():
        if text not in desired_texts:
            # 2026-05-26: queries with locked=True (original seed text) are forced to stay selected=True,
            # preventing the frontend / old clients from accidentally unticking them
            if q.get("locked"):
                q["selected"] = True
            else:
                q["selected"] = False
    # 2026-05-26: locked queries stay selected=True even if explicitly unset in the payload
    for q in upgraded:
        if q.get("locked") and not q.get("selected"):
            q["selected"] = True
    selected_n = sum(1 for q in upgraded if q.get("selected") and not q.get("deleted_at"))
    if selected_n > MAX_SELECTED_QUERIES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TOO_MANY_SELECTED",
                "field": "queries",
                "message": f"最多勾选 {MAX_SELECTED_QUERIES} 个提示词,当前 {selected_n}",
            },
        )
    t.queries_json = json.dumps(upgraded, ensure_ascii=False)
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="selected_queries",
        after=f"selected_count={selected_n}",
    )
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


class DeleteQueriesPayload(BaseModel):
    """Soft delete / restore prompts. deleted=True in items deletes, False restores; queries not mentioned are untouched."""
    items: list["DeleteQueryItem"] = Field(default_factory=list)
    # Also fully recompute this topic's wide table with the new metric definition (no recompute by default: historical numbers in delivered reports stay unchanged)
    rebuild_history: bool = False


class DeleteQueryItem(BaseModel):
    text: str
    deleted: bool = True


DeleteQueriesPayload.model_rebuild()


@router.post("/topics/{topic_id}/delete-queries", response_model=TopicOut)
def delete_queries(
    topic_id: int,
    payload: DeleteQueriesPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft delete / restore prompts: deleted queries are no longer batch-run, excluded from the report metric definition and from publishing query selection.

    **Flag only; elements are never removed from queries_json** (see models.QueryItem.deleted_at):
    the historical metric definition relies on reverse lookup of scene_type by wording, and removing elements would degrade this query's brand query answers into category queries,
    inflating the exposure rate; and text is the only identity, so once the element is gone there is no restoring it.

    **Does not go through _ensure_editable**: that only allows editing draft/rejected topics, while deleting queries is most needed after the project is running
    (finding some queries worthless, or misspelled).

    Two side effects, which the frontend second confirmation must state:
      · report numbers change: frozen wide-table days keep the old metric definition while the live tail switches to the new one immediately,
        leaving a step in between (trend charts show a "metric definition change" vertical line); rebuild_history=True recomputes everything
      · this topic's in-flight Siyuan (VSCL-RL) actions go stale and do not return to their arms: the query pack is part of context_fingerprint
    """
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    try:
        arr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        arr = []
    if not isinstance(arr, list):
        arr = []
    want = {i.text: i.deleted for i in payload.items if (i.text or "").strip()}
    if not want:
        return TopicOut.from_orm_row(t)

    now_iso = datetime.utcnow().isoformat()
    deleted_n = restored_n = 0
    upgraded: list[dict] = []
    for q in arr:
        item = dict(q) if isinstance(q, dict) else {
            "text": q, "status": "approved", "selected": True, "created_at": now_iso,
        }
        text = item.get("text") or ""
        if text in want:
            if want[text] and not item.get("deleted_at"):
                item["deleted_at"] = now_iso
                item["deleted_by_id"] = current_user.id
                deleted_n += 1
            elif not want[text] and item.get("deleted_at"):
                item.pop("deleted_at", None)
                item.pop("deleted_by_id", None)
                restored_n += 1
        upgraded.append(item)

    if not (deleted_n or restored_n):
        return TopicOut.from_orm_row(t)
    t.queries_json = json.dumps(upgraded, ensure_ascii=False)
    _append_changelog(
        t, actor_id=current_user.id,
        actor_role="admin" if getattr(current_user, "is_admin", False) else "user",
        field="delete_queries",
        after=f"deleted={deleted_n} restored={restored_n}",
    )
    db.commit()
    db.refresh(t)

    if payload.rebuild_history:
        _spawn_rebuild_report_daily(topic_id)
    return TopicOut.from_orm_row(t)


def _spawn_rebuild_report_daily(topic_id: int) -> None:
    """Recompute this topic's wide table in full with the new metric definition in the background. A full recompute is O(all detail rows) and must not hold the request.

    ★ force=True is required: report_daily_rollup.build_topic has
      `if old and backfill and not force: skipped_existing`; a backfill without force skips every
      day that already has wide-table rows, finishing with pretty logs, written=0 and reports unchanged (looks done but did nothing).
    """
    def _run() -> None:
        from geo.database import SessionLocal
        from geo.services.report_daily_rollup import build_topic

        s = SessionLocal()
        try:
            stats = build_topic(s, topic_id, backfill=True, force=True, source="backfill")
            log.info("delete_queries rebuild report_daily topic=%s %s", topic_id, stats)
        except Exception:  # noqa: BLE001
            log.exception("delete_queries rebuild report_daily failed topic=%s", topic_id)
        finally:
            s.close()

    threading.Thread(target=_run, daemon=True).start()


@router.post("/topics/{topic_id}/reclassify-queries", response_model=TopicOut)
def reclassify_queries(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """2026-05-28: re-tag the topic's historical queries with scene_type using heuristics.

    Uses query_expander.classify_query(text, target, aliases, product_terms) to classify every query in one sweep.
    - text contains target / aliases / product names from the profile (profile_json.products) -> brand
    - contains intent words (如何 / 攻略 / 教程 / 怎么选, "how to / guide / tutorial / how to choose") -> intent
    - contains Q&A words (哪家 / 怎么样 / 对比 / 吗, "which one / how is it / compare / question particle") -> qa
    - everything else -> search

    Idempotent and rerunnable. Overwrites existing scene_type (including manually chosen ones).
    Returns the full TopicOut (with query_scene_types[]); the frontend refreshes badges on receipt.
    """
    from geo.services.query_expander import classify_query

    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    try:
        arr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        arr = []
    if not isinstance(arr, list):
        arr = []

    target = (t.target or "").strip()
    try:
        aliases = json.loads(t.target_aliases_json or "[]")
        if not isinstance(aliases, list):
            aliases = []
        aliases = [str(a) for a in aliases if isinstance(a, str)]
    except Exception:  # noqa: BLE001
        aliases = []
    product_terms = profile_product_terms(_profile_dict(t), industry=t.industry or "")

    counts: dict[str, int] = {s: 0 for s in ALL_SCENES}
    changed = 0
    upgraded: list[dict] = []
    for q in arr:
        if isinstance(q, str):
            scene = classify_query(q, target=target, aliases=aliases, product_terms=product_terms)
            counts[scene] = counts.get(scene, 0) + 1
            upgraded.append({"text": q, "status": "approved", "selected": True,
                             "scene_type": scene})
            changed += 1
        elif isinstance(q, dict) and q.get("text"):
            new_q = dict(q)
            scene = classify_query(str(q.get("text") or ""), target=target, aliases=aliases, product_terms=product_terms)
            counts[scene] = counts.get(scene, 0) + 1
            if new_q.get("scene_type") != scene:
                new_q["scene_type"] = scene
                changed += 1
            upgraded.append(new_q)
    t.queries_json = json.dumps(upgraded, ensure_ascii=False)
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="reclassify_queries",
        after=f"changed={changed} total={len(upgraded)} dist={counts}",
    )
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


# The submit-for-review endpoint was retired in the review-free flow. Customers no longer submit; admin "starts the project" directly in the workspace.
# Validation logic moved down into _validate_topic_runnable of admin_review.start_topic.


# ─────────────────────────── Run-now passthrough ──────────────


@router.post("/topics/run-now", response_model=None)
async def run_now(
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run a single topic once. By default returns results synchronously to the frontend modal (trial preview semantics, not persisted).

    **First passes the unified call gate** (geo/services/call_policy): the tier decides whether this run
    "executes immediately" or "queues via scheduling", and which engines are allowed. Internal channels with unlimited always pass,
    behaving exactly as before the change; external channels are constrained by tier. Whether allowed or rejected, one call_audit row is written.
    """
    from geo.models.call_policy import RUN_QUEUE
    from geo.services.call_policy import CallDenied, CallerRef, enforce

    _validate_payload(payload)
    cells = len(payload.queries or []) * len(payload.engines or [])
    caller = CallerRef(channel="web", account_id=current_user.id, user_id=current_user.id)
    try:
        decision = enforce(
            db, caller, "run_now",
            endpoint="/api/ai-telemetry/topics/run-now",
            cells=cells, want_run_mode="immediate",
        )
    except CallDenied as e:
        raise HTTPException(e.code, e.reason)

    enterprise_user_id = payload.enterprise_user_id or current_user.id
    _, operation = create_operation_context(
        db,
        current_user,
        enterprise_user_id,
        action="telemetry.preview.create",
        target_resource_type="telemetry_preview",
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="telemetry.preview.create",
        object_type="telemetry_preview",
        result="success",
    )
    db.commit()

    # Intersection with engines allowed by the tier (empty whitelist = allow all)
    engines = list(payload.engines or [])
    if decision.policy.engines:
        engines = [e for e in engines if e in decision.policy.engines]
        if not engines:
            raise HTTPException(403, "该档未开放所选引擎")

    url = f"{TELEMETRY_SERVICE_URL}/run-now"
    body = {
        "name": payload.name,
        "queries": payload.queries,
        "engines": engines,
        "user_id": current_user.id,
        "operation_context_id": operation.id,
        "account_id": current_user.id,
        "channel": "web",
        # The execution policy is sent with the request: the telemetry side uses it to decide whether to run synchronously or enqueue,
        # and whether to use only direct-API engines or allow browser automation.
        "run_mode": decision.run_mode,
        "exec_mode": decision.exec_mode,
        "priority": decision.priority,
        "policy_key": decision.policy.key,
    }
    if decision.run_mode == RUN_QUEUE:
        # Queue tier: do not wait synchronously; return right after enqueueing, and the caller polls the job status
        url = f"{TELEMETRY_SERVICE_URL}/enqueue"
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(url, json=body, headers=internal_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("telemetry-service run-now failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")


# ─────────────── AI smart fill: raw materials -> brand profile ────────────


# Upload size limit per file: 10MB.
# Extracted text is further truncated by file_bytes_to_text -> MAX_EXTRACTED_TEXT;
# this 10MB mainly blocks accidents like "dropped in a PDF of several hundred MB".
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _do_extract(text: str, user_id: int) -> ProfileExtractOut:
    if not text or len(text.strip()) < 10:
        raise HTTPException(400, "原始资料过短(<10 字符),无法抽取资料")
    if len(text) > MAX_EXTRACTED_TEXT:
        text = text[:MAX_EXTRACTED_TEXT]
    try:
        profile, seed_suggestions, model_id = extract_profile_from_text(text)
    except Exception as e:  # noqa: BLE001
        log.warning("profile extract failed for user %s: %s", user_id, e)
        raise HTTPException(502, f"AI 解析失败: {e}")
    if not model_id:
        raise HTTPException(503, "未配置 DEEPSEEK_API_KEY / OPENROUTER_API_KEY,AI 智能填充不可用")
    return ProfileExtractOut(
        profile=profile, used_model=model_id, seed_suggestions=seed_suggestions,
    )


@router.post("/profile/extract", response_model=ProfileExtractOut)
def extract_profile_endpoint(
    payload: ProfileExtractPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """JSON path: raw text pasted by the user (textarea) goes here.

    Files (PDF / Word / TXT etc.) go through /profile/extract-file.
    503 = DEEPSEEK_API_KEY or OPENROUTER_API_KEY is not configured on the backend.
    """
    enterprise_user_id = payload.enterprise_user_id or current_user.id
    _, operation = create_operation_context(
        db,
        current_user,
        enterprise_user_id,
        action="profile.extract",
        target_resource_type="profile_draft",
    )
    db.commit()
    revalidate_operation_context(db, operation.id)
    db.commit()     # Release the FOR UPDATE lock taken by revalidation; do not hold it while calling an LLM for tens of seconds (same cause as the 2026-09-21 incident)
    out = _do_extract(payload.text, enterprise_user_id)
    if payload.persist:
        out.material = _persist_material(
            db, current_user, owner_user_id=enterprise_user_id, topic_id=payload.topic_id,
            source=text_source(payload.text), title=default_title(payload.text, "粘贴文本"),
            text=payload.text, sync=True,
        )
    db.commit()
    return out


@router.post("/profile/extract-file", response_model=ProfileExtractOut)
async def extract_profile_file_endpoint(
    file: UploadFile = File(...),
    enterprise_user_id: Optional[int] = Form(None),
    topic_id: Optional[int] = Form(None),
    persist: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Multipart path: drag in PDF / Word / plain text files. Form fields: file / topic_id / persist.
    After the form is extracted, the whole original text goes into the vector store (see _persist_material).

    Flow: UploadFile -> bytes -> dispatch parser by suffix -> extract text -> the same
    LLM pipeline. Frontend form field name: `file`.

    400 = file too large / empty file / text too short
    415 = old .doc format or scanned PDF with no extractable text
    502 = LLM call failed
    503 = backend LLM key not configured
    """
    if not file or not file.filename:
        raise HTTPException(400, "请上传一个文件")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400,
            f"文件过大({len(raw) // 1024} KB > {MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限)",
        )
    try:
        text = file_bytes_to_text(file.filename, raw)
    except FileParseError as e:
        raise HTTPException(415, str(e))
    owner_user_id = enterprise_user_id or current_user.id
    _, operation = create_operation_context(
        db,
        current_user,
        owner_user_id,
        action="profile.extract_file",
        target_resource_type="profile_draft",
    )
    db.commit()
    revalidate_operation_context(db, operation.id)
    db.commit()     # Release the FOR UPDATE lock taken by revalidation; do not hold it while calling an LLM for tens of seconds (same cause as the 2026-09-21 incident)
    out = _do_extract(text, owner_user_id)
    if persist:
        out.material = await _persist_material_async(
            db, current_user, owner_user_id=owner_user_id, topic_id=topic_id,
            source=file.filename, title=default_title(text, file.filename), text=text,
        )
    db.commit()
    return out


def _material_topic_id(db, current_user, owner_user_id: int, topic_id: Optional[int]) -> Optional[int]:
    """Verify topic ownership (404 same as _get_topic_or_404); empty -> account level."""
    if topic_id is None:
        return None
    _get_topic_or_404(db, topic_id, owner_user_id, allow_admin_user=current_user, action=ACTION_WRITE)
    return topic_id


def _persist_material(db, current_user, *, owner_user_id: int, topic_id: Optional[int],
                      source: str, title: str, text: str, sync: bool) -> Optional[dict]:
    """Put the original text into the vector store; any failure only logs a warning and returns None: form extraction already succeeded and must not 502 because of persistence."""
    try:
        tid = _material_topic_id(db, current_user, owner_user_id, topic_id)
        return ingest_document_sync(db, account_id=owner_user_id, topic_id=tid,
                                    source=source, title=title, text=text)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.warning("material ingest failed user=%s topic=%s source=%s", owner_user_id, topic_id, source, exc_info=True)
        db.rollback()
        return None


async def _persist_material_async(db, current_user, *, owner_user_id: int, topic_id: Optional[int],
                                  source: str, title: str, text: str) -> Optional[dict]:
    try:
        tid = _material_topic_id(db, current_user, owner_user_id, topic_id)
        return await ingest_document(db, account_id=owner_user_id, topic_id=tid,
                                     source=source, title=title, text=text)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.warning("material ingest failed user=%s topic=%s source=%s", owner_user_id, topic_id, source, exc_info=True)
        db.rollback()
        return None


@router.get("/topics/{topic_id}/materials", response_model=list[MaterialDocOut])
def list_topic_materials(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List of materials in the vector store (this topic's + account-level), aggregated by source."""
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    return list_documents(db, account_id=t.user_id, topic_id=t.id)


@router.delete("/topics/{topic_id}/materials")
def delete_topic_material(
    topic_id: int,
    source: str,
    scope: str = "topic",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one material (all its chunks). scope=topic deletes this topic's copy; scope=account deletes the account-level copy."""
    t = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_WRITE)
    n = delete_document(db, account_id=t.user_id, topic_id=(None if scope == "account" else t.id), source=source)
    db.commit()
    return {"deleted_chunks": n}


# ─────────────── Topic media assets (images / videos): for the material upload dialog ──────────
#
# Material upload: text goes through /profile/extract*, where the LLM extracts and backfills the form directly without persisting;
# media (images/videos) go through this section: stored at data/topic_media/{topic_id}/{uuid}.ext + registered in the DB,
# referenced later when generating drafts / publishing.

# The single source of whitelists and limits is services/media_intake; old names are just kept here
MEDIA_DIR_NAME = media_storage.MEDIA_DIR_NAME
MAX_MEDIA_BYTES = media_intake.MAX_MEDIA_BYTES
ALLOWED_IMAGE_EXTS = media_intake.ALLOWED_IMAGE_EXTS
ALLOWED_VIDEO_EXTS = media_intake.ALLOWED_VIDEO_EXTS
ALLOWED_MEDIA_EXTS = media_intake.ALLOWED_MEDIA_EXTS


def _serialize_media(m: TopicMediaORM) -> TopicMediaOut:
    return TopicMediaOut(
        id=m.id, topic_id=m.topic_id,
        filename=m.filename or "", kind=m.kind or "image",
        mime=m.mime or "", size=m.size or 0,
        url=f"/api/ai-telemetry/topics/{m.topic_id}/media/{m.id}/blob",
        public_url=media_storage.public_url(m) or None,
        description=getattr(m, "description", "") or "",
        useCount=int(getattr(m, "use_count", 0) or 0),
        uploaded_at=m.uploaded_at,
    )


@router.get("/topics/{topic_id}/media", response_model=list[TopicMediaOut])
def list_topic_media(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    rows = (
        db.query(TopicMediaORM)
          .filter_by(topic_id=topic_id, user_id=topic.user_id)
          .order_by(TopicMediaORM.uploaded_at.desc())
          .all()
    )
    return [_serialize_media(m) for m in rows]


@router.post("/topics/{topic_id}/media", response_model=TopicMediaOut, status_code=201)
async def upload_topic_media(
    topic_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Material upload: images / videos.

    400 = missing file / empty / too large / content does not match the suffix
    415 = suffix not in the whitelist (.jpg/.png/.mp4/...)
    409 = the topic's asset count / total size has reached the limit

    2026-09-16: trust only the content, not the suffix (services/media_intake.sniff); images have EXIF stripped before being written to disk,
    and the same content under the same topic returns the existing row (no longer stored twice or sent for external moderation twice). description is
    "what this image is", used for asset library display and manual selection.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    if not file or not file.filename:
        raise HTTPException(400, "请上传一个文件")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_MEDIA_EXTS:
        raise HTTPException(415, f"不支持的格式 {ext or '未知'};只接受图片(.jpg/.png/.gif/.webp)与视频(.mp4/.mov/.webm)")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    if len(raw) > MAX_MEDIA_BYTES:
        raise HTTPException(
            400,
            f"文件过大({len(raw) // 1024 // 1024} MB > {MAX_MEDIA_BYTES // 1024 // 1024} MB 上限)",
        )
    try:
        kind, mime = media_intake.sniff(raw, ext)
    except media_intake.MediaRejected as e:
        raise HTTPException(400, str(e))
    if kind == "image":
        raw, ext = media_intake.sanitize(raw, ext)

    # Dedup: only one row per content per topic. The hash is computed after stripping metadata: the same original phone photo
    # dragged in twice may have different EXIF, but identical pixels should count as the same image
    content_hash = media_intake.digest(raw)
    dup = (db.query(TopicMediaORM)
             .filter(TopicMediaORM.topic_id == topic_id,
                     TopicMediaORM.content_hash == content_hash)
             .order_by(TopicMediaORM.id.asc()).first())
    if dup is not None:
        if description.strip() and not (dup.description or "").strip():
            dup.description = description.strip()[:500]
            db.commit()
            db.refresh(dup)
        return _serialize_media(dup)

    # Quota: count and total size. Reject at the limit; no presumptuous "evict the oldest"
    n, total = (db.query(func.count(TopicMediaORM.id), func.coalesce(func.sum(TopicMediaORM.size), 0))
                  .filter(TopicMediaORM.topic_id == topic_id).one())
    if n >= media_intake.MAX_MEDIA_PER_TOPIC:
        raise HTTPException(409, f"该主题素材已达 {media_intake.MAX_MEDIA_PER_TOPIC} 个上限,请先删除不用的")
    if int(total or 0) + len(raw) > media_intake.MAX_MEDIA_BYTES_PER_TOPIC:
        raise HTTPException(409, f"该主题素材总容量已达 "
                                 f"{media_intake.MAX_MEDIA_BYTES_PER_TOPIC // 1024 // 1024 // 1024} GB 上限")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    try:
        stored_path = media_storage.store_bytes(topic_id, stored_name, raw)
    except Exception as e:  # noqa: BLE001 - if shared storage cannot be written, do not persist to DB, otherwise the other machine cannot get it
        log.error("media store failed topic=%s: %s", topic_id, e)
        raise HTTPException(503, "素材存储暂时不可用,请稍后再试")

    # user_id records the topic owner, not the operator: list / blob both filter by topic.user_id,
    # and recording an admin's id for images uploaded on someone's behalf would hide them from the topic owner
    row = TopicMediaORM(
        topic_id=topic_id, user_id=topic.user_id,
        filename=file.filename[:255], kind=kind, mime=mime[:128],
        size=len(raw), storage_path=str(stored_path),
        description=description.strip()[:500], content_hash=content_hash,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # External moderation soft-signal hook point (2026-08-18, image side of remediation list items 9/10): user-uploaded assets are sent for checking too.
    # **Offloaded to a background thread**: a single Aliyun call takes around 3s, and holding the upload response for it would leave users waiting;
    # besides, external moderation is a soft signal and does not decide blocking, so there is no reason to slow down uploads.
    if kind == "image":
        _scan_media_async(row.id)

    return _serialize_media(row)


class TopicMediaPatch(BaseModel):
    description: str = Field("", max_length=500)


@router.patch("/topics/{topic_id}/media/{media_id}", response_model=TopicMediaOut)
def patch_topic_media(
    topic_id: int,
    media_id: int,
    payload: TopicMediaPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit the asset description. This is the only editable field: the file itself cannot be changed; to replace it, delete and re-upload."""
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user)
    row = db.get(TopicMediaORM, media_id)
    if not row or row.topic_id != topic_id or row.user_id != topic.user_id:
        raise HTTPException(404, "media not found")
    row.description = (payload.description or "").strip()
    db.commit()
    db.refresh(row)
    return _serialize_media(row)


def _scan_media_async(media_id: int) -> None:
    """Run external image moderation in a background thread. Has its own session and never raises exceptions back to the request thread."""
    def _run() -> None:
        s = SessionLocal()
        try:
            from geo.services.compliance_scanner import external_check_media
            m = s.query(TopicMediaORM).get(media_id)
            if m is not None:
                external_check_media(s, m)
                s.commit()
        except Exception:  # noqa: BLE001
            log.warning("bg image external scan failed media=%d", media_id, exc_info=True)
        finally:
            s.close()

    threading.Thread(target=_run, daemon=True).start()


@router.get("/public/topic-media/{topic_id}/{filename}")
def get_topic_media_public(topic_id: int, filename: str):
    """2026-05-28: public image/video access endpoint, **no Bearer auth needed**.

    The URL contains a 32-hex UUID filename (128 bits of entropy, unguessable), relying on the UUID for obscurity-style protection.
    LLM-generated drafts embed this URL in markdown ![](url), which browsers / WeChat official accounts / Zhihu can all render.

    Security constraints:
      - filename must be a valid 32-hex + extension format (prevents traversal / injection)
      - the path must fall within data/topic_media/{topic_id}/
      - the file must exist
    """
    # Simple validation: filename = 32hex + dot + 2~4 char extension
    import re as _re
    if not _re.fullmatch(r"[a-f0-9]{32}\.[a-zA-Z0-9]{2,5}", filename):
        raise HTTPException(404, "media not found")
    # The filename is already 32hex + suffix, leaving no room for traversal; with the s3 backend, a missing local file is pulled from the bucket first
    p_real = media_storage.fetch(topic_id, filename)
    if p_real is None or not p_real.is_file():
        raise HTTPException(404, "media not found")
    mime, _ = mimetypes.guess_type(str(p_real))
    return FileResponse(p_real, media_type=mime or "application/octet-stream",
                        filename=p_real.name)


@router.get("/topics/{topic_id}/media/{media_id}/blob")
def get_topic_media_blob(
    topic_id: int,
    media_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream the media file back: readable only by the topic owner."""
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    row = db.get(TopicMediaORM, media_id)
    if not row or row.topic_id != topic_id or row.user_id != topic.user_id:
        raise HTTPException(404, "media not found")
    p = media_storage.media_file(row)
    if p is None or not p.exists():
        raise HTTPException(410, "文件已丢失")
    return FileResponse(p, media_type=row.mime or "application/octet-stream",
                        filename=row.filename or p.name)


@router.delete("/topics/{topic_id}/media/{media_id}", status_code=204)
def delete_topic_media(
    topic_id: int,
    media_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(db, topic_id, current_user.id)
    row = db.get(TopicMediaORM, media_id)
    if not row or row.topic_id != topic_id or row.user_id != current_user.id:
        raise HTTPException(404, "media not found")
    media_storage.delete_file(row.topic_id, row.storage_path or "")
    db.delete(row)
    db.commit()


# ─────────────── Candidate query generation (DeepSeek): used when creating a topic ───────────


def _parse_suggest_args(payload: dict) -> dict:
    """Parse the body of /suggest-queries[/stream] -> a normalized parameter dict.
    Shared by the regular and SSE streaming versions, to avoid the two parsers drifting apart."""
    seed = (payload.get("seed") or "").strip()
    if not seed:
        raise HTTPException(400, "seed cannot be empty")
    # Validate the scene subset
    raw_scenes = payload.get("scenes")
    if raw_scenes is None:
        # By default only fan out the original four kinds: since ALL_SCENES gained compare/scenario,
        # using it as the default set would raise query expansion LLM calls from 4 to 6
        scenes: list[str] = list(EXPAND_SCENES)
    elif isinstance(raw_scenes, list):
        scenes = [s for s in raw_scenes if isinstance(s, str)]
    else:
        raise HTTPException(400, "scenes must be an array")
    invalid = [s for s in scenes if s not in ALL_SCENES]
    if invalid:
        raise HTTPException(400, f"invalid scenes: {invalid}")
    if not scenes:
        raise HTTPException(400, "at least one scene must be selected")
    # Output count per scene: default = the cap (30), compatible with the old count
    cap_per_scene = max(5, MAX_EXPANSION_CANDIDATES // len(EXPAND_SCENES))
    if "count_per_scene" in payload:
        try:
            count_per_scene = int(payload["count_per_scene"])
        except (TypeError, ValueError):
            count_per_scene = cap_per_scene
    elif "count" in payload:
        try:
            count_per_scene = max(5, int(payload["count"]) // len(EXPAND_SCENES))
        except (TypeError, ValueError):
            count_per_scene = cap_per_scene
    else:
        count_per_scene = cap_per_scene
    count_per_scene = max(5, min(count_per_scene, cap_per_scene))

    aliases_in = payload.get("aliases") or []
    if not isinstance(aliases_in, list):
        aliases_in = []

    def _list_from_payload(key: str, *, max_items: int = 20, per_max: int = 300) -> list[str]:
        raw = payload.get(key)
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for x in raw:
            s = str(x or "").strip()
            if s:
                out.append(s[:per_max])
            if len(out) >= max_items:
                break
        return out

    return {
        "seed": seed,
        "scenes": scenes,
        "count_per_scene": count_per_scene,
        "target": (payload.get("target") or "").strip(),
        "industry": (payload.get("industry") or "").strip(),
        "entity_type": (payload.get("entity_type") or "").strip()[:32],
        "service_geo": (payload.get("service_geo") or "").strip()[:200],
        "aliases": [str(a).strip() for a in aliases_in if str(a).strip()][:20],
        "profile_cases": _list_from_payload("profile_cases", max_items=20, per_max=300),
        "core_credentials": _list_from_payload("core_credentials", max_items=20, per_max=300),
        "brand_diff_tags": _list_from_payload("brand_diff_tags", max_items=10, per_max=80),
        "core_service_overview": (payload.get("core_service_overview") or "").strip()[:500],
        "products": _products_from_payload(payload.get("products")),
    }


def _products_from_payload(raw) -> list[dict]:
    """At the wizard stage the topic is not created yet, so the product list comes with the body; each item is validated with BrandProduct, bad rows dropped, at most 30."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for x in raw:
        if not isinstance(x, dict) or not str(x.get("name") or "").strip():
            continue
        try:
            out.append(BrandProduct(**x).model_dump())
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= 30:
            break
    return out


async def _expand_scene_call(scene: str, args: dict, client):
    """Run one single-scene expansion per args (used per scene by the streaming endpoint; the regular endpoint uses topic_expand.expand_with_fields)."""
    return await expand_one_scene(
        scene=scene, seed=args["seed"], count=args["count_per_scene"],
        target=args["target"], aliases=args["aliases"],
        industry=args["industry"], service_geo=args["service_geo"],
        entity_type=args["entity_type"],
        profile_cases=args["profile_cases"],
        core_credentials=args["core_credentials"],
        brand_diff_tags=args["brand_diff_tags"],
        core_service_overview=args["core_service_overview"],
        products=args["products"],
        client=client,
    )


@router.post("/suggest-queries")
async def suggest_queries(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Seed topic -> candidate queries (4-dimension scene expansion). The frontend picker multi-selects and backfills.

    Since 2026-05-28 it is a local 4-dimension fan-out (same as /topics/{id}/expand-queries), no longer proxied to telemetry-service.
    At topic creation there is no topic id yet, so this endpoint is used one step before /expand-queries; both share query_expander.

    body:
      seed:              str   seed query
      scenes:            list  ["search","qa","intent","brand"] (all 4 by default)
      count_per_scene:   int   output count per scene, default 30 (30 per dimension x 4 dimensions = 120; since 2026-09-21, previously 50)
      target:            str   full brand name (needed for the brand scene; brand is skipped if empty)
      aliases:           list  brand aliases
      industry:          str   industry context
      service_geo:       str   service region (can be used as a keyword prefix)
      count:             int   compatibility for old callers: if count_per_scene is not passed, count/4 is used

    resp:
      {
        "seed":  str,
        "total_count": int,
        "scenes": {
          "search": {"queries":[...], "model":"...", "error":null|"..."},
          "qa":     {...},
          "intent": {...},
          "brand":  {...}
        },
        // Old caller compatibility: the queries field is the flattened union of the 4 scenes' output, in order search->qa->intent->brand
        "queries": [str, ...]
      }
    """
    args = _parse_suggest_args(payload)
    enterprise_user_id = int(payload.get("enterprise_user_id") or current_user.id)
    _, operation = create_operation_context(
        db,
        current_user,
        enterprise_user_id,
        action="query.suggest",
        target_resource_type="topic_draft",
    )
    db.commit()
    revalidate_operation_context(db, operation.id)
    # Release the lock right after revalidation, then await the LLM; otherwise concurrent requests from the same account doing INSERT operation_contexts
    # would wait on this users row lock in PG, and since that runs on the event loop thread, the whole process freezes
    # (see the explanation in suggest_queries_stream, 2026-09-21 production incident).
    db.commit()
    seed = args["seed"]
    scenes = args["scenes"]

    # 4-way parallel fan-out (the brand scene is skipped automatically when target is empty; product mode + brand query fallback: see topic_expand)
    out = await expand_with_fields(args, seed=seed, scenes=scenes, count_per_scene=args["count_per_scene"])
    scene_results = out["scenes"]

    # Aggregate: tag each query with a scene field (for grouped rendering in the frontend picker), and provide a flat queries list for old caller compatibility
    flat_queries: list[dict] = []
    total_count = 0
    for s in ALL_SCENES:
        info = scene_results.get(s) or {}
        qs = info.get("queries") or []
        for q in qs:
            flat_queries.append({"text": q, "score": 0, "sources": [], "scene": s})
        total_count += len(qs)

    db.commit()
    return {
        "seed": seed,
        "total_count": total_count,
        "scenes": scene_results,
        # Flat array compatibility for old callers: each item has a scene field
        "queries": flat_queries,
        # Which product in the profile the seed matched (None = no match, expanded as a regular category seed)
        "matched_product": out["matched_product"],
    }


@router.post("/suggest-queries/stream")
async def suggest_queries_stream(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE streaming version of /suggest-queries: 4 scenes in parallel; whichever scene finishes first pushes a frame first, and the frontend renders as it receives,
    without waiting for the whole package (a noticeable difference with slow models). Prompts / parsing are identical to the regular version; results just stream out per scene.

    Events (all `data: <json>\\n\\n`):
      {"type":"meta","matched_product": {name,category,is_featured} | null}   <- first frame (added 2026-09-22)
      {"type":"scene","scene":"search","queries":[{text,score,sources,scene}],"model":..,"error":..}
      {"type":"done","total": N}
    In category scene frames, items whose wording contains the brand / product name have item.scene changed to "brand" (the frame's scene is unchanged);
    when the brand scene is not ticked this time, those items are dropped.
    """
    args = _parse_suggest_args(payload)
    enterprise_user_id = int(payload.get("enterprise_user_id") or current_user.id)
    _, operation = create_operation_context(
        db,
        current_user,
        enterprise_user_id,
        action="query.suggest_stream",
        target_resource_type="topic_draft",
    )
    db.commit()
    operation_context_id = operation.id
    scenes = args["scenes"]
    target = args["target"]

    runnable: list[str] = []
    skipped: dict[str, str] = {}
    for s in scenes:
        if s == "brand" and not target:
            skipped[s] = "brand scene requires a brand name (target field is empty)"
        else:
            runnable.append(s)

    def _sse(obj: dict) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    routing_terms = brand_terms_for_routing(args)
    matched_product = matched_product_out(args["products"], args["seed"])

    async def gen():
        total = 0
        worker_db = SessionLocal()
        try:
            try:
                revalidate_operation_context(worker_db, operation_context_id)
            except Exception:
                worker_db.rollback()
                yield _sse({"type": "error", "error": "Resource not found",
                            "key": RESOURCE_NOT_FOUND})
                yield _sse({"type": "done", "total": 0})
                return
            # ★ Commit right after revalidation to release the FOR UPDATE lock, then wait for the LLM.
            # The 50-minute production hang on 2026-09-21 was exactly here: revalidate locks the operation_contexts row
            # **and the users row** (lock order of access_control.revalidate_operation_context),
            # then held them throughout awaiting the 4-way LLM (up to 90s); any concurrent request from the same account
            # (page polling /auth/me, /user-membership ...) goes through create_operation_context to insert into
            # operation_contexts, whose foreign key needs a KEY SHARE lock on the users row -> queued in PG;
            # and that INSERT executes synchronously on the event loop thread -> the whole uvicorn loop freezes ->
            # this coroutine never gets to commit -> the lock is never released. pg_stat_activity showed:
            # INSERT operation_contexts waiting on Lock for 50 minutes, blocked_by an idle in
            # transaction session whose last statement was SELECT users ... FOR UPDATE, in the same process.
            # Revalidation means "still authorized at this moment"; the lock only needs to cover revalidation itself, not network calls.
            worker_db.commit()
            yield _sse({"type": "meta", "matched_product": matched_product})
            # Push skipped scenes out first, so the frontend immediately knows they will not run.
            for s, err in skipped.items():
                yield _sse({
                    "type": "scene", "scene": s, "queries": [], "error": err,
                })
            if runnable:
                try:
                    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                        async def _labeled(scene: str):
                            try:
                                res = await _expand_scene_call(scene, args, client)
                                return scene, res
                            except Exception as e:  # noqa: BLE001
                                return scene, {"queries": [], "error": str(e)[:300]}

                        for fut in asyncio.as_completed(
                            [_labeled(s) for s in runnable]
                        ):
                            scene, res = await fut
                            # Items in category scenes containing the brand / product name are reassigned to brand (same fallback as route_brand_words)
                            routed = {scene: {"queries": list(res.get("queries") or [])}}
                            if scene != "brand":
                                route_brand_words(routed, routing_terms)
                            qs = routed[scene]["queries"]
                            items = [
                                {
                                    "text": q, "score": 0, "sources": [],
                                    "scene": scene,
                                }
                                for q in qs
                            ]
                            if "brand" in scenes:
                                items += [
                                    {"text": q, "score": 0, "sources": [], "scene": "brand"}
                                    for q in (routed.get("brand") or {}).get("queries") or []
                                ]
                            total += len(items)
                            yield _sse({
                                "type": "scene", "scene": scene, "queries": items,
                                "model": res.get("model"), "error": res.get("error"),
                            })
                except Exception as e:  # noqa: BLE001
                    yield _sse({"type": "error", "error": str(e)[:300]})
            worker_db.commit()
            yield _sse({"type": "done", "total": total})
        finally:
            worker_db.close()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Turn off nginx buffering so SSE reaches the frontend frame by frame
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/topics/{topic_id}/expand-queries")
async def expand_queries_for_topic(
    topic_id: int, payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Phase D + 2026-05-28: 4-dimension scene expansion.

    body:
      seed:              str   seed query (neutral, no scene)
      scenes:            list  ["search","qa","intent","brand"] (all 4 by default)
      count_per_scene:   int   output per scene <= 30, total <= MAX_EXPANSION_CANDIDATES
      count:             int   compatibility with the old parameter (ignored; count_per_scene takes precedence)

    resp v2:
      {
        "seed":  str,
        "total_count": int,
        "scenes": {
          "search": {"queries":[...], "model":"deepseek-chat", "error":null|"..."},
          "qa":     {...},
          "intent": {...},
          "brand":  {...}
        }
      }

    Expansion candidates are not written to queries_json directly: the frontend lets the user tick them, then calls /selected-queries.
    The brand scene is skipped automatically when target is empty (returns an error); the other 3 scenes run in parallel unaffected.
    """
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    seed = (payload.get("seed") or "").strip()
    if not seed:
        raise HTTPException(400, "seed cannot be empty")

    # Validate the scene subset
    raw_scenes = payload.get("scenes")
    if raw_scenes is None:
        # By default only fan out the original four kinds: since ALL_SCENES gained compare/scenario,
        # using it as the default set would raise query expansion LLM calls from 4 to 6
        scenes: list[str] = list(EXPAND_SCENES)
    elif isinstance(raw_scenes, list):
        scenes = [s for s in raw_scenes if isinstance(s, str)]
    else:
        raise HTTPException(400, "scenes must be an array")
    invalid = [s for s in scenes if s not in ALL_SCENES]
    if invalid:
        raise HTTPException(400, f"invalid scenes: {invalid}")
    if not scenes:
        raise HTTPException(400, "at least one scene must be selected")

    # Output count per scene: cap = MAX_EXPANSION_CANDIDATES / 4 = 30 (since 2026-09-21, previously 50; see the constant comment)
    cap_per_scene = max(5, MAX_EXPANSION_CANDIDATES // len(EXPAND_SCENES))
    try:
        count_per_scene = int(payload.get("count_per_scene", cap_per_scene))
    except (TypeError, ValueError):
        count_per_scene = cap_per_scene
    count_per_scene = max(5, min(count_per_scene, cap_per_scene))

    # Profile fields + 4-way fan-out + product mode + brand query fallback: all in topic_expand (the same code as the agent tool)
    out = await expand_topic_seed(t, seed, scenes, count_per_scene)
    scene_results = out["scenes"]

    # Aggregate + write expansion_log_json (1 log per scene)
    total_count = 0
    for s in scenes:
        info = scene_results.get(s) or {}
        qs = info.get("queries") or []
        total_count += len(qs)
        excerpt = ", ".join(qs[:3])
        _append_expansion_log(
            t, seed=seed,
            model=info.get("model") or "deepseek",
            expanded_count=len(qs), raw_excerpt=excerpt,
            scene=s,
        )
    db.commit()

    return {
        "seed": seed,
        "total_count": total_count,
        "scenes": scene_results,
        "matched_product": out["matched_product"],
    }


# ─────────────────────────── Trigger run + result queries ─────


@router.post("/topics/{topic_id}/run", status_code=202)
async def trigger_run(
    topic_id: int,
    only_new: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger a formal batch run that persists to DB and return immediately (202). Results are polled via GET /topics/{id}/runs.

    **Charge before running** (2026-09-09): this does a pre-check + human-readable errors; the real charge is booked by
    telemetry after it creates the run, calling back /internal/batch-prepay: the idempotency key needs
    run_id, which only that side has. The pre-check avoids creating a run row for a batch run doomed to fail:
    when the balance is insufficient the user should get 402 and "how much is missing", not 202 plus a
    cancelled record.

    only_new=true (2026-09-21, the "add batch run" button on the plan page): only runs cells that have not succeeded yet:
    after expanding queries, run the new ones without redoing the whole matrix. When not a single cell is new telemetry returns nothing_new,
    creating no run and charging nothing, which is passed through as-is here (still 202).
    """
    from geo.services import batch_prepay

    topic = _get_topic_or_404(db, topic_id, current_user.id)
    cells = (batch_prepay.pending_cells_for_topic(db, topic) if only_new
             else batch_prepay.cells_for_topic(topic))
    quote = batch_prepay.quote(db, topic, cells)
    if quote["chargeable"] and quote["balance_cents"] < quote["need_cents"]:
        raise AppException(
            status_code=402,
            message=batch_prepay.insufficient_message(quote),
            key="INSUFFICIENT_BALANCE",
            details=quote,
        )
    _, operation_context = create_resource_operation_context(
        db,
        current_user,
        topic,
        action="telemetry.run.create",
        target_resource_type="ai_telemetry_topic",
        target_resource_id=topic.id,
    )
    append_system_audit(
        db,
        operation_context=operation_context,
        event_type="task_created",
        action="telemetry.run.create",
        object_type="ai_telemetry_topic",
        object_id=topic.id,
        result="success",
    )
    # The telemetry service and backend use separate transactions; commit the context first so the service can revalidate in real time.
    db.commit()
    url = f"{TELEMETRY_SERVICE_URL}/run-topic"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, headers=internal_headers(), json={
                "topic_id": topic_id,
                "operation_context_id": operation_context.id,
                "only_new": only_new,
            })
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log.warning("telemetry-service /run-topic failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    # An HTTP call separates the pre-check from the real charge, during which the balance may be spent by another path (drafts / another topic's
    # batch run). If the charge fails there, the run is set to cancelled and this status is returned: still return 402, so the
    # frontend does not poll with a 202 for a run that will never have results.
    if isinstance(data, dict) and data.get("status") == "insufficient_balance":
        raise AppException(
            status_code=402,
            message=data.get("message") or "余额不足,本轮监测未开跑。",
            key="INSUFFICIENT_BALANCE",
            details=data,
        )
    return data


@router.get("/topics/{topic_id}/run-quote")
def get_topic_run_quote(
    topic_id: int,
    only_new: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """How many credits one round costs, quote only, no charge. For the line next to the "generate" button on the plan page: clicking charges immediately,
    so before clicking the user must see how many cells it will run, how much it costs and whether the balance suffices.

    only_new (default) = only count cells that have not succeeded yet, the same metric definition as the round triggered alongside POST solution/generate;
    only_new=false = the whole matrix (same metric definition as POST /topics/{id}/run).
    """
    from geo.services import batch_prepay

    t = _get_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    cells = (batch_prepay.pending_cells_for_topic(db, t) if only_new
             else batch_prepay.cells_for_topic(t))
    q = batch_prepay.quote(db, t, cells)
    return {"topic_id": t.id, "only_new": only_new, "total_cells": batch_prepay.cells_for_topic(t), **q}


@router.get("/topics/{topic_id}/schedule")
def get_topic_schedule(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """This topic's continuous monitoring pace: how often it runs / when it starts / credits per round / how to stop.

    Read-only. It exists because of the felt problem "after persisting, nothing happened"; see
    the comment on services/topic_schedule.describe.
    """
    t = _get_topic_or_404(
        db,
        topic_id,
        current_user.id,
        allow_admin_user=current_user,
        action=ACTION_READ,
    )
    from geo.services.topic_schedule import describe

    return {"topic_id": t.id, "topic_name": t.name, **describe(t, db)}


@router.get("/topics/{topic_id}/progress")
def get_topic_progress(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Which step this topic has reached and what to do next. For the status bar on the page.

    Division of labor with `/schedule`: that one is "pace" (how often it runs, how to stop), a whole paragraph for humans;
    this one is "progress" (current state, what is missing, which cell this round has reached), for UI logic.
    Both are read-only and neither writes to the DB.
    """
    from geo.services import topic_progress

    t = _get_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    return {"topic_id": t.id, "topic_name": t.name, **topic_progress.describe(db, t)}


# Models users can tick themselves (canonical keys, matching the 6 CN engines in frontend constants/engines.ts).
USER_SELECTABLE_ENGINES: tuple[str, ...] = ("deepseek", "qwen", "doubao", "wenxin", "yuanbao", "kimi")

# **Terminals** users can tick themselves (two rows in the frontend: a browser row and a mobile row).
#   Browser row -> the key is the model name (deepseek / qwen ...). Which channel it actually uses underneath is up to ops:
#              the server account pool (qwen), the office physical-device browser (qwen_browser) and Doubao mobile web
#              (doubao_mobile) are different keys in the DB, but to users they are all "browser · Qwen".
#   Mobile row -> the key is always {model}_app, executed by the emulator fleet with region=android in the dispatch center
#              (V-networks/workers/emulator-cn), collecting from each vendor's native app.
# Why it is open to users: the dispatch center side has fully integrated *_app (runner.APP_ENGINES goes into WORKER_ENGINES
# through the dispatch_tasks queue, dispatch has capacity slots, the gateway recognizes these keys); only the entry point was missing.
APP_CHANNEL_SUFFIX = "_app"
# The **single list** of channel suffixes: _canonical_engine relies on it to fold channel keys back into model names.
# _mobile is Doubao mobile web (account pool forced to browser automation), _browser is desktop physical devices, _app is emulator apps.
_CHANNEL_SUFFIXES = ("_browser", "_mobile", APP_CHANNEL_SUFFIX)


def user_selectable_engine_keys() -> tuple[str, ...]:
    """The engines whitelist for PATCH /topics/{id}/schedule = 6 models x 2 terminals.

    diandian_app (Xiaohongshu Diandian) is deliberately excluded: it only exists as an app and is not a channel variant of these 6 models,
    and it is not in the frontend's 6-model enum either: ops can attach it to a topic, users cannot tick it (but it is not
    erased when ticking other models; see the passthrough in _merge_user_engines).
    """
    return (*USER_SELECTABLE_ENGINES,
            *(m + APP_CHANNEL_SUFFIX for m in USER_SELECTABLE_ENGINES))


def _canonical_engine(key: str) -> str:
    for suf in _CHANNEL_SUFFIXES:
        if key.endswith(suf):
            return key[: -len(suf)]
    return key


def _merge_user_engines(topic, chosen: list[str]) -> list[str]:
    """User ticks by "model x terminal" -> list of engine keys to persist.

    The browser row ticks models (qwen), while the DB may store an ops-specified channel (qwen_browser /
    doubao_mobile). Rules are computed once per row:
      · browser row still ticked -> **keep the existing browser-side channel key** untouched (otherwise a casual click by the user would
        revert ops' physical-device channel to the server channel); newly ticked -> use the canonical key;
      · mobile row ticked -> {model}_app (it has no channel variants; the only executor is the emulator fleet);
      · unticked -> removed.
    Order: browser row first, then mobile row, each per USER_SELECTABLE_ENGINES.

    Trailing passthrough: existing keys whose canonical form falls outside the 6 models (ops-attached diandian_app,
    overseas engines ...) are kept as-is. Users are changing their own two rows and should not incidentally erase things they cannot tick or see.
    """
    existing_web: dict[str, str] = {}
    for k in _topic_engines(topic):
        if k.endswith(APP_CHANNEL_SUFFIX):
            continue
        existing_web.setdefault(_canonical_engine(k), k)
    picked = set(chosen)
    out: list[str] = [existing_web.get(m, m)
                      for m in USER_SELECTABLE_ENGINES if m in picked]
    out += [m + APP_CHANNEL_SUFFIX for m in USER_SELECTABLE_ENGINES
            if m + APP_CHANNEL_SUFFIX in picked]
    out += sorted(k for k in _topic_engines(topic)
                  if _canonical_engine(k) not in USER_SELECTABLE_ENGINES)
    return out


class SelfSchedulePatch(BaseModel):
    """Customers change this topic's monitoring themselves: pace / pause / which models / which weekday and hour to start.

    Before 2026-09-21 the anchor was not open ("capacity layout belongs to ops"); it is now open: users set the **cycle start**,
    while actual dispatch is still constrained by engine daily limits / machine capacity (drip trickles hourly), so "what time to start" means
    "what time to start scheduling", not "what time it finishes"; the card must say this clearly.
    """
    schedule_tier: Optional[Literal["daily", "weekly", "off"]] = None
    pause_days: Optional[int] = None          # Pause N days (1-90); resumes automatically when due
    clear_pause: bool = False                 # True = resume immediately
    # Which models x which terminals: see user_selectable_engine_keys() (6 models + 6 {model}_app),
    # at least 1; None = unchanged. The cap leaves headroom at 6x2.
    engines: Optional[list[str]] = Field(default=None, max_length=20)
    # Cycle anchor: weekday 0=Monday..6=Sunday (weekly only), hour 0-23 Beijing time;
    # -1 = clear the custom value back to the platform default; None = unchanged. Same sentinels as the ops console's TopicSchedulePatch.
    cycle_anchor_weekday: Optional[int] = None
    cycle_anchor_hour: Optional[int] = None


@router.patch("/topics/{topic_id}/schedule")
def patch_own_topic_schedule(
    topic_id: int,
    payload: SelfSchedulePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Customer self-service toggle for continuous monitoring: daily / weekly / off / pause N days.

    **Why this endpoint was added**: the only previous write path for `schedule_tier` was
    `PATCH /admin/topics/{id}/schedule` (requiring workspace permission). So the page and the agent
    kept saying "turn off scheduled audits on the topic settings page", while that switch only ever existed in the ops console:
    users searched the whole front end and could not find it, and the agent, forbidden to say "I can't reach it", could only invent a nonexistent button.

    The anchor is not open to customers (capacity layout belongs to ops); tier and pause are open.
    """
    from geo.services.topic_schedule import (
        describe, enabled_for_tier, normalize_anchor_hour, normalize_anchor_weekday,
        normalize_tier,
    )

    t = _get_topic_or_404(db, topic_id, current_user.id)

    def _snapshot(row) -> str:
        wd = getattr(row, "cycle_anchor_weekday", None)
        hh = getattr(row, "cycle_anchor_hour", None)
        return (f"{normalize_tier(getattr(row, 'schedule_tier', None))}"
                f"{'/paused' if getattr(row, 'paused_until', None) else ''}"
                f"@{'默认' if wd is None else wd}/{'默认' if hh is None else hh}"
                f" engines={','.join(sorted(_topic_engines(row)))}")

    before = _snapshot(t)

    if payload.schedule_tier is not None:
        t.schedule_tier = payload.schedule_tier
        t.enabled = enabled_for_tier(payload.schedule_tier)   # The single derived write point
    if payload.clear_pause:
        t.paused_until = None
    elif payload.pause_days is not None:
        n = int(payload.pause_days)
        if not 1 <= n <= 90:
            raise HTTPException(422, "pause_days 必须是 1~90 天")
        t.paused_until = datetime.utcnow() + timedelta(days=n)

    if payload.engines is not None:
        # Engines cannot be changed while running: the run's matrix (initialize_pending_cells) is built from the engines at start,
        # and adding/removing midway would break progress / prepay refund reconciliation. Change after it finishes.
        running = (db.query(AiTelemetryRunORM.id)
                     .filter(AiTelemetryRunORM.topic_id == t.id,
                             AiTelemetryRunORM.status == "running")
                     .first())
        if running is not None:
            raise HTTPException(409, {
                "code": "RUN_IN_PROGRESS", "field": "engines",
                "message": "这一轮还在跑,跑完再改模型。",
            })
        chosen = [str(e).strip() for e in payload.engines if str(e).strip()]
        allowed = user_selectable_engine_keys()
        bad = [e for e in chosen if e not in allowed]
        if bad:
            raise HTTPException(422, {
                "code": "INVALID_ENGINE", "field": "engines",
                "message": f"不支持的模型:{', '.join(bad)};可选:{', '.join(allowed)}",
            })
        # "At least 1" checks **the user's own two rows**, not merged: merged has a trailing
        # passthrough (ops-attached diandian_app etc.), which may be non-empty even with nothing ticked, so checking it would miss this.
        if not chosen:
            raise HTTPException(422, {
                "code": "NO_ENGINE", "field": "engines", "message": "至少保留 1 个模型",
            })
        merged = _merge_user_engines(t, chosen)
        t.engines_json = json.dumps(merged, ensure_ascii=False)

    if payload.cycle_anchor_weekday is not None:
        if payload.cycle_anchor_weekday < 0:
            t.cycle_anchor_weekday = None          # Sentinel: back to the platform default
        elif normalize_anchor_weekday(payload.cycle_anchor_weekday) is None:
            raise HTTPException(422, "cycle_anchor_weekday 必须是 0(周一)~6(周日)")
        else:
            t.cycle_anchor_weekday = payload.cycle_anchor_weekday
    if payload.cycle_anchor_hour is not None:
        if payload.cycle_anchor_hour < 0:
            t.cycle_anchor_hour = None
        elif normalize_anchor_hour(payload.cycle_anchor_hour) is None:
            raise HTTPException(422, "cycle_anchor_hour 必须是 0~23")
        else:
            t.cycle_anchor_hour = payload.cycle_anchor_hour

    after = _snapshot(t)
    if before != after:
        _append_changelog(t, actor_id=current_user.id, actor_role="user",
                          field="schedule", before=before, after=after,
                          note="self_patch_schedule")
    db.commit()
    db.refresh(t)
    return {"topic_id": t.id, "topic_name": t.name, **describe(t, db)}


@router.get("/topics/{topic_id}/runs", response_model=list[RunOut])
def list_runs(
    topic_id: int,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_READ,
    )
    rows = (
        db.query(AiTelemetryRunORM)
          .filter_by(topic_id=topic_id)
          .order_by(AiTelemetryRunORM.id.desc())
          .limit(min(limit, 100))
          .all()
    )
    # response counts in one query
    counts = dict(
        db.query(AiTelemetryResponseORM.run_id, func.count(AiTelemetryResponseORM.id))
          .filter(AiTelemetryResponseORM.run_id.in_([r.id for r in rows]))
          .group_by(AiTelemetryResponseORM.run_id)
          .all()
    ) if rows else {}
    return [RunOut.from_orm_row(r, response_count=counts.get(r.id, 0)) for r in rows]


@router.get("/topics/{topic_id}/overview", response_model=OverviewOut)
def topic_overview(
    topic_id: int,
    period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Overview page aggregation: 4 KPI cards + daily per-engine trend series.

    KPI metric definitions:
      - visibility = **the report center's overall exposure rate** (latest weekly period, see report_metrics_source).
        Before 2026-08 this was a locally hand-written "hit / successful response", with no category query filter, no Beijing-day bucketing,
        and a lifetime window, so none of the numbers matched the report center pages; it now delegates data fetching, do not change it back.
      - citations = sum(len(citations_json)), counting only **successful** responses (same metric definition as _domain_stats below)
      - growth = pct change of citations vs the previous period (period-over-period of citation **count**, not one of the six metrics)
      - engines_covered = number of engines with >=1 successful response this period / total topic.engines
    Brand terms source: topic.target + topic.target_aliases_json (same source as when runner records hits).
    """
    from geo.services import report_metrics_source as RMS

    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    now = datetime.utcnow()
    period_days, period_start = _resolve_period(period, topic, now)
    prev_start = period_start - timedelta(days=period_days)

    # Brand terms: use the topic's own detection terms (target / aliases / product names), same source as runner detect_hit
    brand_keywords: list[str] = metric_brand_terms(topic)
    brand_lc = [k.lower() for k in brand_keywords]

    topic_engines = json.loads(topic.engines_json or "[]")
    engines_total = len(topic_engines)

    rows_curr = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= period_start)
          .all()
    )
    rows_prev = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= prev_start)
          .filter(AiTelemetryResponseORM.created_at < period_start)
          .all()
    )

    def _aggregate(rows: list[AiTelemetryResponseORM]) -> dict:
        """Citation count / engines covered: both are **counts**, not ratios, so there is no metric definition conflict with the report center.
        The exposure rate is not computed here (see the function docstring: delegated to the report center data layer)."""
        cit_total = 0
        succ_engines: set[str] = set()
        total = 0
        for r in rows:
            if r.error:                       # Error rows do not count citations (consistent with _domain_stats)
                continue
            cit_total += len(json.loads(r.citations_json or "[]"))
            total += 1
            succ_engines.add(r.engine)
        return {
            "citations": cit_total,
            "engines": len(succ_engines),
            "succ_total": total,
        }

    curr = _aggregate(rows_curr)
    prev = _aggregate(rows_prev)

    # ── Overall exposure rate: delegated to the report center data layer, digit-for-digit equal to the /reports home KPI card ──
    prepared = RMS.load_stats(db, topic)
    _week_key, m_cur, m_prev = RMS.latest_period(db, topic, "weekly", prepared=prepared)
    vis_value = float(m_cur.get("exposure") or 0.0)
    vis_prev = m_prev.get("exposure")
    # The exposure rate is itself a percentage, so period-over-period uses **percentage points** (same as report center delta_pp), not relative growth
    vis_delta = round(vis_value - float(vis_prev), 1) if vis_prev is not None else None
    sparkline_vis = [v for _d, v in RMS.daily_metric_series(
        db, topic, "exposure", limit=period_days, prepared=prepared)]

    def _delta(curr_val: float, prev_val: float) -> Optional[float]:
        if prev_val == 0:
            return None
        return round((curr_val - prev_val) / prev_val * 100, 1)

    # Daily trend: date -> {engine: citation_count} (the exposure rate curve comes from the report center, see sparkline_vis above)
    bucket: dict[str, dict[str, int]] = {}
    engines_in_window: set[str] = set()
    for r in rows_curr:
        if r.error:
            continue
        d = r.created_at.strftime("%Y-%m-%d")
        if d not in bucket:
            bucket[d] = {}
        cits = json.loads(r.citations_json or "[]")
        bucket[d][r.engine] = bucket[d].get(r.engine, 0) + len(cits)
        engines_in_window.add(r.engine)

    # Fill missing dates with empty values so the sparkline is continuous
    trend: list[TrendPoint] = []
    sparkline_cit: list[float] = []
    for i in range(period_days):
        d = (period_start + timedelta(days=i)).strftime("%Y-%m-%d")
        day_vals = bucket.get(d, {})
        trend.append(TrendPoint(date=d, values=day_vals))
        sparkline_cit.append(float(sum(day_vals.values())))

    # ── Citation analysis: Top domains + owned/other + engine x domain matrix ──
    def _domain_stats(rows: list[AiTelemetryResponseORM]) -> tuple[dict[str, int], int, int, dict[str, dict[str, int]], dict[str, dict[str, int]]]:
        """Returns (domain_total_count, owned_count, total_cit_count, engine_domain[engine][domain] = count, engine_factor[engine][factor] = count)."""
        domain_count: dict[str, int] = {}
        owned = 0
        total = 0
        engine_domain: dict[str, dict[str, int]] = {}
        engine_factor: dict[str, dict[str, int]] = {}   # engine -> {GEO factor: count}
        for r in rows:
            if r.error:
                continue
            cits = json.loads(r.citations_json or "[]")
            for c in cits:
                d = (c.get("domain") or "").lower().strip()
                if not d:
                    continue
                domain_count[d] = domain_count.get(d, 0) + 1
                total += 1
                eng = engine_domain.setdefault(r.engine, {})
                eng[d] = eng.get(d, 0) + 1
                is_owned = bool(brand_lc and any(k in d for k in brand_lc))
                if is_owned:
                    owned += 1
                # Secondary GEO factor analysis: attribute by title + url (FirstPageSage metric definition)
                factor = classify_factor(c.get("url") or "", c.get("title") or "", d, owned=is_owned)
                efac = engine_factor.setdefault(r.engine, {})
                efac[factor] = efac.get(factor, 0) + 1
        return domain_count, owned, total, engine_domain, engine_factor

    curr_domains, curr_owned, curr_total_cit, curr_engine_domain, curr_engine_factor = _domain_stats(rows_curr)
    _, prev_owned, prev_total_cit, _, _ = _domain_stats(rows_prev)

    top_n = sorted(curr_domains.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    top_domains_out = [
        DomainCount(
            domain=d, count=c,
            pct=round(c / curr_total_cit * 100, 1) if curr_total_cit > 0 else 0.0,
        )
        for d, c in top_n
    ]
    top_set = {d for d, _ in top_n}

    # Keep only the engine matrix for top domains, for the frontend heatmap
    engine_domain_filtered: dict[str, dict[str, int]] = {}
    for eng, dom_map in curr_engine_domain.items():
        engine_domain_filtered[eng] = {d: c for d, c in dom_map.items() if d in top_set}

    curr_owned_pct = round(curr_owned / curr_total_cit * 100, 1) if curr_total_cit > 0 else 0.0
    prev_owned_pct = (prev_owned / prev_total_cit * 100) if prev_total_cit > 0 else 0.0
    owned_split = OwnedSplit(
        owned=curr_owned,
        other=curr_total_cit - curr_owned,
        owned_pct=curr_owned_pct,
        delta_pct=_delta(curr_owned_pct, prev_owned_pct),
    )

    return OverviewOut(
        topic_id=topic_id,
        period_days=period_days,
        brand_keywords=brand_keywords,
        visibility=KpiBlock(
            value=vis_value,
            delta_pct=vis_delta,           # Percentage points (report center metric definition), not relative growth
            sparkline=sparkline_vis,
        ),
        citations=KpiBlock(
            value=float(curr["citations"]),
            delta_pct=_delta(curr["citations"], prev["citations"]),
            sparkline=sparkline_cit,
        ),
        growth=KpiBlock(
            value=_delta(curr["citations"], prev["citations"]) or 0.0,
            delta_pct=None,
            sparkline=[],
        ),
        engines_covered=KpiBlock(
            value=float(curr["engines"]),
            delta_pct=_delta(curr["engines"], prev["engines"]),
            sparkline=[],
        ),
        engines_total=engines_total,
        trend=trend,
        engines=sorted(engines_in_window),
        top_domains=top_domains_out,
        owned_split=owned_split,
        engine_domain_matrix=engine_domain_filtered,
        engine_factor_matrix=curr_engine_factor,
    )


@router.get("/topics/{topic_id}/intent-breakdown", response_model=IntentBreakdownOut)
def topic_intent_breakdown(
    topic_id: int,
    period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Group this period's responses by the cluster_id aggregated on the picker side, producing per-cluster mention / citation rates.

    If the topic's queries_json has no cluster_id (all 0 for old topics or failed clustering), there is only one cluster,
    or responses fall into the uncategorized bucket when query text and cluster_id do not match.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    period_days, period_start = _resolve_period(period, topic)

    # Brand terms (same as the overview metric definition: target / aliases / product names, same source as runner detect_hit)
    brand_keywords: list[str] = metric_brand_terms(topic)

    # query -> cluster_id mapping
    queries_raw = json.loads(topic.queries_json or "[]")
    query_to_cluster: dict[str, int] = {}
    query_count_by_cluster: dict[int, int] = {}
    for q in queries_raw:
        if isinstance(q, dict):
            text = q.get("text") or ""
            cid = q.get("cluster_id")
            cid_int = int(cid) if isinstance(cid, int) else -1
        elif isinstance(q, str):
            text, cid_int = q, -1
        else:
            continue
        if not text:
            continue
        query_to_cluster[text] = cid_int
        query_count_by_cluster[cid_int] = query_count_by_cluster.get(cid_int, 0) + 1

    # Cluster labels
    clusters_raw = json.loads(topic.clusters_json or "[]")
    cluster_labels: dict[int, str] = {}
    cluster_order: list[int] = []
    for c in clusters_raw:
        if isinstance(c, dict) and "cluster_id" in c:
            cid = int(c["cluster_id"])
            cluster_labels[cid] = c.get("label") or f"cluster_{cid}"
            cluster_order.append(cid)

    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= period_start)
          .all()
    )

    # Aggregate response dimensions by cluster_id
    agg: dict[int, dict] = {}
    for r in rows:
        if r.error:
            continue
        cid = query_to_cluster.get(r.query, -1)
        bucket = agg.setdefault(cid, {"response": 0, "mention": 0, "citation": 0})
        bucket["response"] += 1
        if r.hit:
            bucket["mention"] += 1
        bucket["citation"] += len(json.loads(r.citations_json or "[]"))

    def _mk(cid: int, label: str) -> ClusterBreakdownItem:
        b = agg.get(cid, {"response": 0, "mention": 0, "citation": 0})
        return ClusterBreakdownItem(
            cluster_id=cid,
            label=label,
            query_count=query_count_by_cluster.get(cid, 0),
            response_count=b["response"],
            mention_count=b["mention"],
            mention_rate=round(b["mention"] / b["response"], 3) if b["response"] else 0.0,
            citation_count=b["citation"],
        )

    # Sort named clusters by size descending; unlabeled / old topics fall into uncategorized
    out_clusters: list[ClusterBreakdownItem] = []
    seen_cids: set[int] = set()
    for cid in cluster_order:
        out_clusters.append(_mk(cid, cluster_labels[cid]))
        seen_cids.add(cid)
    out_clusters.sort(key=lambda c: c.query_count, reverse=True)

    uncat_b = agg.get(-1, {"response": 0, "mention": 0, "citation": 0})
    # Accumulate all responses whose query did not match a cluster into uncategorized
    for cid, b in agg.items():
        if cid != -1 and cid not in seen_cids:
            uncat_b["response"] += b["response"]
            uncat_b["mention"] += b["mention"]
            uncat_b["citation"] += b["citation"]
    uncat = ClusterBreakdownItem(
        cluster_id=-1,
        label="uncategorized",
        query_count=query_count_by_cluster.get(-1, 0)
                  + sum(c for cid, c in query_count_by_cluster.items()
                        if cid not in seen_cids and cid != -1),
        response_count=uncat_b["response"],
        mention_count=uncat_b["mention"],
        mention_rate=round(uncat_b["mention"] / uncat_b["response"], 3)
                   if uncat_b["response"] else 0.0,
        citation_count=uncat_b["citation"],
    )

    return IntentBreakdownOut(
        topic_id=topic_id,
        period_days=period_days,
        brand_keywords=brand_keywords,
        clusters=out_clusters,
        uncategorized=uncat,
    )


def _clean_citations_for_out(raw: str | None) -> list[dict]:
    """Parse a list from citations_json and fill in domain / title: old responses may lack fields,
    and feeding them directly to RunNowCitation fails pydantic validation (endpoint 500).
    """
    from urllib.parse import urlparse
    try:
        items = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for c in items:
        if not isinstance(c, dict):
            continue
        url = (c.get("url") or "").strip()
        if not url:
            continue
        domain = (c.get("domain") or "").strip()
        if not domain:
            try:
                domain = urlparse(url).netloc
            except Exception:  # noqa: BLE001
                domain = ""
        out.append({
            "url": url,
            "domain": domain,
            "title": (c.get("title") or "").strip(),
        })
    return out


@router.get("/runs/{run_id}/responses", response_model=list[ResponseOut])
def list_responses(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(AiTelemetryRunORM, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    topic = db.get(AiTelemetryTopicORM, run.topic_id)
    if not topic:
        raise HTTPException(404, "run not found")
    if topic.user_id != current_user.id and not getattr(current_user, "is_admin", False):
        raise HTTPException(404, "run not found")
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter_by(run_id=run_id)
          .order_by(AiTelemetryResponseORM.id.asc())
          .all()
    )
    out = []
    for r in rows:
        out.append(ResponseOut(
            id=r.id, engine=r.engine, query=r.query,
            answer=r.answer, citations=_clean_citations_for_out(r.citations_json),
            video_url=r.video_url, source_url=r.source_url, error=r.error,
            created_at=r.created_at,
            hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
            mention_position=r.mention_position, brand_rank=r.brand_rank,
            reasoning=r.reasoning,
        ))
    return out


@router.get("/topics/{topic_id}/run-results", response_model=list[AdminRunOut])
def list_topic_run_results(
    topic_id: int,
    day: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Overview of all batch runs for a topic (account) of the current user: used by the task monitoring page.

    - owner check (admin can view others' topics); all time, sorted by started_at DESC
    - day=YYYY-MM-DD / status optional filters; lists everything by default
    - reuses the AdminRunOut shape; user/topic fields are redundant for the own view and not shown by the frontend
    """
    from geo.models.user import UserORM
    from sqlalchemy import case

    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    q = (
        db.query(AiTelemetryRunORM)
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .order_by(AiTelemetryRunORM.started_at.desc())
    )
    if day:
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, "day 必须是 YYYY-MM-DD")
        q = q.filter(
            AiTelemetryRunORM.started_at >= d,
            AiTelemetryRunORM.started_at < d + timedelta(days=1),
        )
    if status:
        q = q.filter(AiTelemetryRunORM.status == status)
    runs = q.offset(offset).limit(limit).all()

    run_ids = [r.id for r in runs]
    stats: dict[int, dict[str, int]] = {}
    if run_ids:
        result = (
            db.query(
                AiTelemetryResponseORM.run_id,
                func.count(AiTelemetryResponseORM.id).label("total"),
                func.sum(case((AiTelemetryResponseORM.hit == True, 1), else_=0)).label("hits"),  # noqa: E712
                func.sum(case((AiTelemetryResponseORM.error.isnot(None), 1), else_=0)).label("errs"),
            )
            .filter(AiTelemetryResponseORM.run_id.in_(run_ids))
            .group_by(AiTelemetryResponseORM.run_id)
            .all()
        )
        for rid, total, hits, errs in result:
            stats[rid] = {"total": int(total or 0), "hits": int(hits or 0), "errs": int(errs or 0)}

    owner = db.get(UserORM, topic.user_id)
    owner_email = (owner.email if owner else "") or ""
    out: list[AdminRunOut] = []
    for run in runs:
        s = stats.get(run.id, {"total": 0, "hits": 0, "errs": 0})
        out.append(AdminRunOut(
            run_id=run.id,
            topic_id=topic.id,
            topic_name=topic.name,
            topic_target=topic.target or "",
            user_id=topic.user_id,
            user_email=owner_email,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            error=run.error,
            response_count=s["total"],
            hit_count=s["hits"],
            error_count=s["errs"],
        ))
    return out


@router.get("/topics/{topic_id}/responses", response_model=list[ResponseOut])
def list_topic_responses(
    topic_id: int,
    engine: Optional[str] = None,
    domain: Optional[str] = None,
    query: Optional[str] = None,
    period: int = 30,
    limit: int = 200,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Topic-level responses list: for drill-down in brand growth sub-pages (sources / platforms / keywords).

    - engine: filter by a single engine
    - domain: hit that domain (contained in citation_domains_json)
    - query: lock to a single query
    - period: last N days (1-90)
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    _, cutoff = _resolve_period(period, topic)

    q = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= cutoff)
    )
    if engine:
        q = q.filter(AiTelemetryResponseORM.engine == engine)
    if query:
        q = q.filter(AiTelemetryResponseORM.query == query)
    rows = q.order_by(AiTelemetryResponseORM.created_at.desc()).limit(max(1, min(limit, 500))).all()

    # Domain filtering is done in the application layer: citation_domains_json is a JSON list, and cross-dialect LIKE is unreliable
    if domain:
        d = domain.strip().lower()
        filtered = []
        for r in rows:
            try:
                doms = json.loads(r.citation_domains_json or "[]")
            except Exception:  # noqa: BLE001
                doms = []
            if any(d == (x or "").strip().lower() for x in doms):
                filtered.append(r)
        rows = filtered

    out = []
    for r in rows:
        out.append(ResponseOut(
            id=r.id, engine=r.engine, query=r.query,
            answer=r.answer, citations=_clean_citations_for_out(r.citations_json),
            video_url=r.video_url, source_url=r.source_url, error=r.error,
            created_at=r.created_at,
            hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
            mention_position=r.mention_position, brand_rank=r.brand_rank,
            reasoning=r.reasoning,
        ))
    return out


# ─────────────────── v1 citation tracking ──────────────────────────


def _cell_to_out(c: AiTelemetryQueryHitORM) -> QueryHitCell:
    return QueryHitCell(
        query=c.query, engine=c.engine, status=c.status,
        first_hit_at=c.first_hit_at,
        first_hit_response_id=c.first_hit_response_id,
        last_checked_at=c.last_checked_at,
        total_runs=c.total_runs or 0, total_hits=c.total_hits or 0,
    )


@router.get("/topics/{topic_id}/tracking-matrix", response_model=TrackingMatrixOut)
def get_tracking_matrix(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """v1 citation tracking: the (query x engine) matrix of the whole topic + first-hit timeline."""
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    queries_raw = json.loads(topic.queries_json or "[]")
    queries: list[str] = []
    for q in queries_raw:
        if isinstance(q, dict):
            if q.get("deleted_at"):            # Soft-deleted queries no longer occupy matrix rows
                continue
            t = q.get("text") or ""
            if t:
                queries.append(t)
        elif isinstance(q, str):
            queries.append(q)
    engines: list[str] = json.loads(topic.engines_json or "[]")
    target_aliases: list[str] = json.loads(topic.target_aliases_json or "[]")

    cell_rows = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    by_key: dict[tuple[str, str], AiTelemetryQueryHitORM] = {
        (c.query, c.engine): c for c in cell_rows
    }

    # 2026-05-26: scan responses to aggregate the list of target / aliases each cell has hit.
    # Used by the frontend's "filter by hit term".
    #
    # Compound alias matching rules (2026-05-26 v2):
    #   • single-word alias (e.g. "Zhang"): the answer contains this literal -> hit
    #   • compound alias (containing separators & + | ＆ ｜ 和 与 或, i.e. ASCII/full-width symbols and the words "and/with/or"): split into parts,
    #     **all parts appear in the answer** (any position, any order, any characters in between) -> hit
    #     e.g. alias "Example Law Firm & Zhang", answer "...Example Law Firm｜Zhang (partner)..."
    #         splits into ["Example Law Firm", "Zhang"]; both parts are in the answer -> counts as a hit on this compound alias
    #     This way, whatever separator the actual answer uses, as long as both are mentioned it is recognized as a "brand + person" compound hit.
    #
    # Substring dedup: uses "match longer strings first + replace in the haystack after matching", so that when "Example Law FirmZhang" (target)
    # appears, the inner "Zhang" is not counted as well.
    import re
    _SEPARATOR_RE = re.compile(r"\s*[&+|＆｜和与或]\s*|\s+")

    def _split_compound(term: str) -> list[str]:
        """Split an alias into parts if it contains separators, otherwise return a single element."""
        parts = [p.strip() for p in _SEPARATOR_RE.split(term) if p.strip()]
        return parts if len(parts) >= 2 else [term]

    # Since 2026-09-22 product names / product aliases are also hit terms (same metric definition as batch-run detect_hit), otherwise a cell judged hit could not be filtered
    raw_terms = ([topic.target.strip()] + [a.strip() for a in target_aliases]
                 + profile_product_terms(_profile_dict(topic), industry=topic.industry or ""))
    seen_terms: set[str] = set()
    hit_terms_pool: list[tuple[str, list[str]]] = []  # (display_term, parts_to_match)
    for t in raw_terms:
        if t and t not in seen_terms:
            seen_terms.add(t)
            hit_terms_pool.append((t, _split_compound(t)))
    # Sort: compound aliases (more parts) first, then by character length descending
    hit_terms_pool.sort(key=lambda x: (len(x[1]), len(x[0])), reverse=True)

    aliases_hit_by_cell: dict[tuple[str, str], set[str]] = {}
    if hit_terms_pool:
        hit_resp_rows = (
            db.query(AiTelemetryResponseORM.query, AiTelemetryResponseORM.engine,
                     AiTelemetryResponseORM.hit_excerpt, AiTelemetryResponseORM.answer)
              .filter(AiTelemetryResponseORM.topic_id == topic_id)
              .filter(AiTelemetryResponseORM.hit.is_(True))  # noqa: E712
              .all()
        )
        for r in hit_resp_rows:
            key = (r.query, r.engine)
            bucket = aliases_hit_by_cell.setdefault(key, set())
            haystack = (r.hit_excerpt or r.answer or "").lower()
            if not haystack:
                continue
            for term, parts in hit_terms_pool:
                parts_low = [p.lower() for p in parts]
                if all(p in haystack for p in parts_low):
                    bucket.add(term)
                    # Cut out the part (so smaller sub-aliases are not double-counted)
                    for p in parts_low:
                        haystack = haystack.replace(p, " ")

    # Fill every (query × engine) — cells never run get a pending placeholder (not persisted, returned in-memory only)
    cells: list[QueryHitCell] = []
    for q in queries:
        for e in engines:
            c = by_key.get((q, e))
            aliases_hit = sorted(aliases_hit_by_cell.get((q, e), set()))
            if c is not None:
                cell_out = _cell_to_out(c)
                cell_out.aliases_hit = aliases_hit
                cells.append(cell_out)
            else:
                cells.append(QueryHitCell(
                    query=q, engine=e, status="pending",
                    first_hit_at=None, first_hit_response_id=None,
                    last_checked_at=None, total_runs=0, total_hits=0,
                    aliases_hit=[],
                ))

    # Timeline — for each engine, the earliest first_hit_at across all queries
    timeline: list[EngineFirstHit] = []
    for e in engines:
        best: AiTelemetryQueryHitORM | None = None
        for c in cell_rows:
            if c.engine != e:
                continue
            if c.first_hit_at is None:
                continue
            if best is None or (c.first_hit_at < best.first_hit_at):
                best = c
        if best is None:
            timeline.append(EngineFirstHit(
                engine=e, first_hit_at=None, first_hit_query=None, days_after_start=None,
            ))
        else:
            days = (best.first_hit_at - topic.created_at).days if topic.created_at else None
            timeline.append(EngineFirstHit(
                engine=e, first_hit_at=best.first_hit_at,
                first_hit_query=best.query,
                days_after_start=max(0, days) if days is not None else None,
            ))

    # KPI
    total_cells = len(queries) * len(engines)
    hit_cells = sum(1 for c in cells if c.total_hits >= 1)
    hit_pct = round(hit_cells / total_cells * 100, 1) if total_cells > 0 else 0.0

    total_runs = (
        db.query(func.count(AiTelemetryRunORM.id))
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .scalar() or 0
    )

    return TrackingMatrixOut(
        topic_id=topic_id,
        target=topic.target or topic.name or "",
        target_aliases=target_aliases,
        queries=queries,
        engines=engines,
        started_at=topic.created_at,
        cells=cells,
        timeline=timeline,
        total_runs=int(total_runs),
        total_cells=total_cells,
        hit_cells=hit_cells,
        hit_cells_pct=hit_pct,
    )


@router.get("/topics/{topic_id}/growth-report", response_model=GrowthReportOut)
def get_growth_report(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Growth report — compares, per prompt, the "first batch run result" with the "current cumulative result".

    Metric definition:
      baseline = all responses of the topic's earliest run.
      current  = all historical responses of the topic (cumulative).
    Per-question aggregation: hit = any engine hit; rank = best brand_rank among hits;
    engines = set of engines that ever hit. status reflects the change from the first run to now.

    ★ hit_rate_pct is a **question-level** hit rate (hit questions / total prompts), deliberately **not** the report center's
      overall exposure rate (response-level · single period · category-term definition) — this report is a per-item "first run vs now" comparison,
      both sides must share one metric definition to be comparable; current cannot switch to the report-center definition while baseline cannot.
      Frontend copy must say "question hit rate", not exposure rate, or the two pages will again be seen as inconsistent.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)

    # Prompt set + seed mapping (same source as tracking-matrix: all non-soft-deleted query texts)
    queries_raw = json.loads(topic.queries_json or "[]")
    queries: list[str] = []
    seed_of: dict[str, str] = {}
    for q in queries_raw:
        if isinstance(q, dict):
            if q.get("deleted_at"):
                continue
            txt = q.get("text") or ""
            if txt:
                queries.append(txt)
                if q.get("seed"):
                    seed_of[txt] = q["seed"]
        elif isinstance(q, str) and q:
            queries.append(q)
    query_set = set(queries)

    # First batch run
    first_run = (
        db.query(AiTelemetryRunORM)
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .order_by(AiTelemetryRunORM.id.asc())
          .first()
    )
    has_baseline = first_run is not None

    baseline_rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.run_id == first_run.id)
          .all()
    ) if first_run else []
    current_rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .all()
    )

    # 2026-08: removed the hard-coded {"yuanbao"} engine exclusion — it was added in 2026-06 when Yuanbao was temporarily unavailable,
    # existed only in this endpoint, and kept the growth report's engine coverage out of sync with other site metrics. Engine availability
    # is decided by the account pool / dispatch; the reporting layer should not pin a list.
    def _group(rows: list[AiTelemetryResponseORM]) -> dict[str, list[AiTelemetryResponseORM]]:
        g: dict[str, list[AiTelemetryResponseORM]] = {}
        for r in rows:
            if r.query in query_set:
                g.setdefault(r.query, []).append(r)
        return g

    base_g = _group(baseline_rows)
    curr_g = _group(current_rows)

    def _agg(rows: list[AiTelemetryResponseORM]) -> tuple[bool, Optional[int], list[str]]:
        """returns (hit, best_rank, engines_hit)."""
        hit = False
        best_rank: Optional[int] = None
        engines: set[str] = set()
        for r in rows:
            if r.error:                       # Error rows do not count as hits (consistent with other site metrics)
                continue
            if r.hit:
                hit = True
                engines.add(r.engine)
                if r.brand_rank is not None:
                    best_rank = r.brand_rank if best_rank is None else min(best_rank, r.brand_rank)
        return hit, best_rank, sorted(engines)

    def _status(b_hit: bool, b_rank: Optional[int], c_hit: bool, c_rank: Optional[int]) -> str:
        if c_hit and not b_hit:
            return "new_hit"
        if c_hit and b_hit:
            if b_rank is not None and c_rank is not None:
                if c_rank < b_rank:
                    return "improved"
                if c_rank > b_rank:
                    return "regressed"
            return "steady"
        if b_hit and not c_hit:
            return "regressed"
        return "still_miss"

    rows_out: list[GrowthQueryRow] = []
    for q in queries:
        b_hit, b_rank, b_eng = _agg(base_g.get(q, []))
        c_hit, c_rank, c_eng = _agg(curr_g.get(q, []))
        rows_out.append(GrowthQueryRow(
            query=q, seed=seed_of.get(q),
            baseline_hit=b_hit, baseline_rank=b_rank, baseline_engines=b_eng,
            current_hit=c_hit, current_rank=c_rank, current_engines=c_eng,
            status=_status(b_hit, b_rank, c_hit, c_rank),
        ))

    def _snapshot(grouped: dict[str, list[AiTelemetryResponseORM]], run_at) -> GrowthSnapshot:
        total = len(queries)
        hit_count = 0
        ranks: list[int] = []
        engines: set[str] = set()
        for q in queries:
            hit, rank, eng = _agg(grouped.get(q, []))
            if hit:
                hit_count += 1
                engines.update(eng)
                if rank is not None:
                    ranks.append(rank)
        return GrowthSnapshot(
            run_at=run_at,
            queries_total=total,
            queries_hit=hit_count,
            hit_rate_pct=round(hit_count / total * 100, 1) if total else 0.0,
            avg_rank=round(sum(ranks) / len(ranks), 2) if ranks else None,
            engines_covered=len(engines),
        )

    return GrowthReportOut(
        topic_id=topic_id,
        target=topic.target or topic.name or "",
        generated_at=datetime.utcnow(),
        has_baseline=has_baseline,
        baseline=_snapshot(base_g, first_run.started_at if first_run else None),
        current=_snapshot(curr_g, datetime.utcnow()),
        rows=rows_out,
    )


@router.get("/topics/{topic_id}/share-of-voice", response_model=ShareOfVoiceOut)
def get_share_of_voice(
    topic_id: int, period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Brand share of voice + competitor mention ranking — brand vs competitor mention counts.

    Metric definition:
      saiv_pct = **the report center's "brand share of voice"** (latest period · weekly, see report_metrics_source).
        Before 2026-08 this was computed locally as brand / (brand + Σ competitors[].count): the numerator was
        "response count" but the denominator mixed in "mention count", inconsistent units, off from the report center — now delegated.
      competitors[] / competitors_count_total = competitor mention-count ranking aggregated by name over the last period days,
        a **ranking detail**, not sharing a denominator with saiv_pct; do not derive share of voice from it.
      position_dist = the brand's **paragraph position** in answers (lead/body/tail), unrelated to "rank distribution".
      optimal_rate_pct = SUM(QueryHit.total_hits) / SUM(QueryHit.total_runs) × 100
    """
    from geo.services import report_metrics_source as RMS

    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    period_days, cutoff = _resolve_period(period, topic)

    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= cutoff)
          .all()
    )

    brand_count = 0
    competitors_map: dict[str, int] = {}
    pos = {"lead": 0, "body": 0, "tail": 0, "unknown": 0}
    for r in rows:
        if r.error:
            continue
        if r.hit:
            brand_count += 1
            p = (r.mention_position or "unknown").lower()
            if p not in pos:
                p = "unknown"
            pos[p] += 1
        if r.competitors_json:
            try:
                arr = json.loads(r.competitors_json or "[]")
            except Exception:  # noqa: BLE001
                arr = []
            for c in arr:
                if not isinstance(c, dict):
                    continue
                name = (c.get("name") or "").strip()
                cnt = int(c.get("count") or 0)
                if name and cnt > 0:
                    competitors_map[name] = competitors_map.get(name, 0) + cnt

    comp_total = sum(competitors_map.values())
    total = brand_count + comp_total
    # Share of voice uses the report-center definition (digit-for-digit equal to card 6 on the /reports home);
    # the competitor ranking pct below still uses this period's detail denominator, only for intra-ranking ratios, not share of voice
    _k, m_cur, _prev = RMS.latest_period(db, topic, "weekly")
    saiv_pct = float(m_cur.get("mention") or 0.0)

    top_comps = sorted(competitors_map.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    comp_entries = [
        CompetitorShareEntry(
            name=name, count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for name, cnt in top_comps
    ]

    # Preference rate = sum(QueryHit.total_hits) / sum(QueryHit.total_runs)
    qh_rows = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    sum_hits = sum((c.total_hits or 0) for c in qh_rows)
    sum_runs = sum((c.total_runs or 0) for c in qh_rows)
    optimal_pct = round(sum_hits / sum_runs * 100, 1) if sum_runs > 0 else 0.0

    total_runs = (
        db.query(func.count(AiTelemetryRunORM.id))
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .scalar() or 0
    )

    return ShareOfVoiceOut(
        topic_id=topic_id,
        target=topic.target or topic.name or "",
        period_days=period_days,
        brand_count=brand_count,
        competitors_count_total=comp_total,
        saiv_pct=saiv_pct,
        competitors=comp_entries,
        position_dist=PositionDist(**pos),
        optimal_rate_pct=optimal_pct,
        total_runs=int(total_runs),
        sample_size=len(rows),
    )


def _norm_url(u: str) -> str:
    """URL normalization: lowercase scheme+host, strip trailing slash, keep path/query."""
    if not u:
        return ""
    u = u.strip()
    try:
        p = _urlparse(u)
        host = p.netloc.lower()
        path = p.path.rstrip("/") or "/"
        q = ("?" + p.query) if p.query else ""
        return f"{p.scheme.lower()}://{host}{path}{q}"
    except Exception:
        return u


def _load_owned_urls(db: Session, topic_id: int) -> set[str]:
    """Load the normalized URL set of a topic's "published owned articles" (numerator check for source share)."""
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(
              TopicGeneratedDocORM.topic_id == topic_id,
              TopicGeneratedDocORM.status == "published",
          )
          .all()
    )
    owned: set[str] = set()
    for d in docs:
        try:
            tgts = json.loads(d.publish_targets_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(tgts, list):
            continue
        for t in tgts:
            if isinstance(t, dict):
                url = t.get("url") or ""
                if url:
                    owned.add(_norm_url(url))
    # Historically auto-publish left targets url empty; mediumsly_url is the fallback (same definition as backfill_cited_by)
    for d in docs:
        if d.mediumsly_url:
            owned.add(_norm_url(d.mediumsly_url))
    return owned


def _seed_coverage_pct(
    rows: list[AiTelemetryResponseORM],
    query_to_seed: dict[str, str],
    approved_seeds: set[str],
    *,
    allowed_engines: Optional[set[str]] = None,
) -> float:
    """Seed coverage — a brand-growth-only metric with no report-center counterpart, so it has its own definition.

    · Denominator = number of approved seed prompts (status=approved in topic.seed_prompts_json)
    · Numerator = seeds with at least one expanded query ever hit by any engine
    · Full vocabulary + full lifetime: it measures the **breadth of coverage** of the seed strategy (ever hit = covered),
      deliberately not using the exposure-rate "category term + single period" definition — hits from brand-term expansions are real coverage too.
      The frontend tooltip must state this, or it will be mistaken for one of the six metrics.
    """
    if not approved_seeds or not query_to_seed:
        return 0.0
    seeds_with_hit: set[str] = set()
    for r in rows:
        if r.error or not r.hit:
            continue
        if allowed_engines is not None and r.engine not in allowed_engines:
            continue
        seed = query_to_seed.get(r.query)
        if seed and seed in approved_seeds:
            seeds_with_hit.add(seed)
    return round(len(seeds_with_hit) / len(approved_seeds) * 100, 1)


def _breakdown_of(metrics: dict, seed_coverage_pct: float) -> PositionBreakdown:
    """Report center's six metrics → brand growth PositionBreakdown (field names are legacy; see schema comments for semantics)."""
    return PositionBreakdown(
        visible_pct=float(metrics.get("exposure") or 0.0),
        top1_pct=float(metrics.get("top1") or 0.0),
        top3_pct=float(metrics.get("top3") or 0.0),
        accuracy_pct=float(metrics.get("accuracy") or 0.0),
        source_pct=float(metrics.get("citation") or 0.0),
        mention_pct=float(metrics.get("mention") or 0.0),
        seed_coverage_pct=seed_coverage_pct,
    )


def _topic_seed_index(topic) -> tuple[int, dict[str, str], set[str], list[str]]:
    """→ (prompt count N, query→seed, approved seed set, engine list)."""
    try:
        queries_raw = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        queries_raw = []
    total_queries = 0
    query_to_seed: dict[str, str] = {}
    for q in queries_raw:
        if isinstance(q, str) and q.strip():
            total_queries += 1
        elif isinstance(q, dict) and (q.get("text") or "").strip():
            total_queries += 1
            seed_txt = (q.get("seed") or "").strip()
            if seed_txt:
                query_to_seed[q["text"].strip()] = seed_txt
    try:
        engines_raw = json.loads(topic.engines_json or "[]")
    except Exception:  # noqa: BLE001
        engines_raw = []
    engines_list = [e.strip() for e in engines_raw if isinstance(e, str) and e.strip()]
    approved_seeds: set[str] = set()
    try:
        for s in json.loads(topic.seed_prompts_json or "[]"):
            if isinstance(s, dict) and s.get("status") == "approved":
                txt = (s.get("text") or "").strip()
                if txt:
                    approved_seeds.add(txt)
    except Exception:  # noqa: BLE001
        pass
    return total_queries, query_to_seed, approved_seeds, engines_list


@router.get("/topics/{topic_id}/position-breakdown", response_model=PositionBreakdownOut)
def get_position_breakdown(
    topic_id: int, period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Brand growth radar + core metric cards.

    ★ 2026-08: all six percentages are delegated to the **report center** data layer (latest period · weekly), digit-for-digit
      equal to the /reports home KPI cards. Previously this was a local hand-written "cell-level × full lifetime × no term-type filter"
      aggregation and none of the numbers matched between the two pages — do not revert to local aggregation.
      The only exception is seed_coverage_pct (seed coverage), which the report center lacks; see _seed_coverage_pct.
    """
    from geo.services import report_metrics_source as RMS

    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    # period is kept for backward compatibility (frontend still sends it); the window is now set by the report-center definition (latest week).
    period_days = max(1, min(period, 90))

    total_queries, query_to_seed, approved_seeds, engines_list = _topic_seed_index(topic)
    total_engines = len(engines_list)

    prepared = RMS.load_stats(db, topic)
    week_key, m_cur, _m_prev = RMS.latest_period(db, topic, "weekly", prepared=prepared)
    eng_metrics = (RMS.period_engine_metrics(db, topic, "weekly", week_key, prepared=prepared)
                   if week_key else {})

    # Seed coverage is full-lifetime breadth and needs all responses (deliberately different from the six metrics' single-period definition)
    rows = prepared.rows()

    query_overall = _breakdown_of(
        m_cur, _seed_coverage_pct(rows, query_to_seed, approved_seeds))
    query_by_engine: list[EngineSlice] = []
    for eng in engines_list:
        query_by_engine.append(EngineSlice(
            engine=eng,
            breakdown=_breakdown_of(
                eng_metrics.get(eng) or {},
                _seed_coverage_pct(rows, query_to_seed, approved_seeds,
                                   allowed_engines={eng}),
            ),
            total_queries=total_queries,
        ))
    query_group = GroupBreakdown(
        scope="query",
        total_queries=total_queries,
        total_engines=total_engines,
        total_cells=total_queries * total_engines,
        breakdown=query_overall,
        by_engine=query_by_engine,
    )

    industry = (topic.industry or "").strip()
    industry_baseline = _compute_industry_baseline(db, industry) if industry else None

    return PositionBreakdownOut(
        topic_id=topic_id,
        period_days=period_days,
        industry=industry,
        total_cells=total_queries * total_engines,
        total_queries=total_queries,
        breakdown=query_overall,
        industry_baseline=industry_baseline,
        query_group=query_group,
        engines=engines_list,
    )


MIN_INDUSTRY_BASELINE_SAMPLES = 3


def _compute_industry_baseline(
    db: Session, industry: str,
) -> Optional[PositionBreakdown]:
    """Cross-tenant P50 breakdown over all topics in an industry.

    ★ 2026-08: now goes through the report-center data layer per topic (consistent with the page), and no longer
      full-scans responses per topic — topics with a wide table read it directly.
    Fewer than 3 topics in the sample returns None (frontend hides the benchmark polygon / industry caption), avoiding mock data.
    """
    from geo.services import report_metrics_source as RMS

    if not industry:
        return None
    topics = (
        db.query(AiTelemetryTopicORM)
          .filter(AiTelemetryTopicORM.industry == industry)
          .all()
    )
    if len(topics) < MIN_INDUSTRY_BASELINE_SAMPLES:
        return None

    from geo.api.ai_telemetry_reports import no_fixed_denominator

    per_topic: list[PositionBreakdown] = []
    # ★ Cross-tenant aggregation forces no floor: the floor is a **single topic's** display definition (see _fixed_valid_ctx);
    #   letting one topic's ops config raise/lower its own value and then feed P50 would pollute the whole industry baseline.
    with no_fixed_denominator():
        for t in topics:
            tq, q2s, approved_t, engines_t = _topic_seed_index(t)
            if tq == 0 or not engines_t:
                continue
            prepared = RMS.load_stats(db, t)
            _k, m_cur, _prev = RMS.latest_period(db, t, "weekly", prepared=prepared)
            if not m_cur:
                continue
            per_topic.append(_breakdown_of(
                m_cur, _seed_coverage_pct(prepared.rows(), q2s, approved_t)))

    if len(per_topic) < MIN_INDUSTRY_BASELINE_SAMPLES:
        return None

    def _p50(values: list[float]) -> float:
        s = sorted(values)
        n = len(s)
        if n == 0:
            return 0.0
        if n % 2 == 1:
            return s[n // 2]
        return round((s[n // 2 - 1] + s[n // 2]) / 2, 1)

    return PositionBreakdown(
        top1_pct=_p50([b.top1_pct for b in per_topic]),
        top3_pct=_p50([b.top3_pct for b in per_topic]),
        visible_pct=_p50([b.visible_pct for b in per_topic]),
        source_pct=_p50([b.source_pct for b in per_topic]),
        accuracy_pct=_p50([b.accuracy_pct for b in per_topic]),
        mention_pct=_p50([b.mention_pct for b in per_topic]),
        seed_coverage_pct=_p50([b.seed_coverage_pct for b in per_topic]),
    )


@router.get("/benchmarks/industry", response_model=IndustryBenchmarkOut)
def get_industry_benchmark(
    industry: str, period: int = 30,
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    db: Session = Depends(get_db),
):
    """Cross-tenant industry benchmark — returns breakdown=None when the sample is insufficient."""
    industry_clean = (industry or "").strip()
    period_days = max(1, min(period, 90))
    sample_topics = (
        db.query(func.count(AiTelemetryTopicORM.id))
          .filter(AiTelemetryTopicORM.industry == industry_clean)
          .scalar() or 0
    )
    # period is kept for backward compatibility; the industry benchmark now uses a lifetime definition
    _ = period_days
    breakdown = _compute_industry_baseline(db, industry_clean) if industry_clean else None
    return IndustryBenchmarkOut(
        industry=industry_clean,
        sample_size=int(sample_topics),
        breakdown=breakdown,
    )


@router.get("/topics/{topic_id}/competitor-substitutions", response_model=CompetitorSubstitutionOut)
def get_competitor_substitutions(
    topic_id: int, period: int = 30,
    competitor: Optional[str] = None, limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """C3 "displacement evidence" — queries that mentioned a competitor but not us.

    Metric definition: within the last period days, responses at the (query, engine) level with hit=False (our brand not hit)
          but non-empty competitors_json; count aggregated by query × competitor.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    period_days, cutoff = _resolve_period(period, topic)

    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= cutoff)
          .filter(AiTelemetryResponseORM.hit == False)  # noqa: E712
          .filter(AiTelemetryResponseORM.error.is_(None))
          .filter(AiTelemetryResponseORM.competitors_json.isnot(None))
          .all()
    )

    # (query, competitor_name) -> {count, sample_response_id, sample_snippet}
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        try:
            comps = json.loads(r.competitors_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        for c in comps:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            cnt = int(c.get("count") or 0)
            snippet = (c.get("snippet") or "")[:200]
            if not name or cnt <= 0:
                continue
            if competitor and competitor.strip() and name != competitor.strip():
                continue
            key = (r.query, name)
            cur = agg.get(key)
            if cur is None:
                agg[key] = {
                    "count": cnt,
                    "sample_response_id": r.id,
                    "sample_snippet": snippet,
                }
            else:
                cur["count"] += cnt

    items = [
        CompetitorSubstitutionItem(
            query=q, competitor_name=name,
            competitor_count=v["count"],
            sample_response_id=v["sample_response_id"],
            sample_snippet=v["sample_snippet"],
        )
        for (q, name), v in agg.items()
    ]
    items.sort(key=lambda x: (-x.competitor_count, x.query, x.competitor_name))
    return CompetitorSubstitutionOut(
        topic_id=topic_id, period_days=period_days,
        competitor_filter=(competitor.strip() if competitor else None),
        items=items[:limit], total=len(items),
    )


@router.get("/topics/{topic_id}/cells/drawer", response_model=CellDrawerOut)
def get_cell_drawer(
    topic_id: int, query: str, engine: str,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Single cell drawer — status + past answers (latest N).

    The diagnostic insight is not fetched here (it is an LLM call / on demand); the frontend POSTs /cell-insight separately.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    cell = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .filter(AiTelemetryQueryHitORM.query == query)
          .filter(AiTelemetryQueryHitORM.engine == engine)
          .one_or_none()
    )
    if cell is None:
        cell_out = QueryHitCell(
            query=query, engine=engine, status="pending",
            first_hit_at=None, first_hit_response_id=None,
            last_checked_at=None, total_runs=0, total_hits=0,
        )
    else:
        cell_out = _cell_to_out(cell)

    resp_rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.query == query)
          .filter(AiTelemetryResponseORM.engine == engine)
          .order_by(AiTelemetryResponseORM.created_at.desc())
          .limit(min(limit, 30))
          .all()
    )
    evidence = [
        CellEvidence(
            response_id=r.id, run_id=r.run_id, created_at=r.created_at,
            engine=r.engine, query=r.query,
            hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
            mention_position=r.mention_position,
            source_url=r.source_url, answer=r.answer or "",
            citations=[RunNowCitation(**c) for c in json.loads(r.citations_json or "[]")],
            reasoning=r.reasoning,
        )
        for r in resp_rows
    ]

    # Existing insight (latest) — do not force generation
    insight_row = (
        db.query(AiTelemetryCellInsightORM)
          .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
          .filter(AiTelemetryCellInsightORM.query == query)
          .filter(AiTelemetryCellInsightORM.engine == engine)
          .order_by(AiTelemetryCellInsightORM.generated_at.desc())
          .first()
    )
    insight_out = _insight_orm_to_out(insight_row) if insight_row else None

    return CellDrawerOut(cell=cell_out, evidence=evidence, insight=insight_out)


def _insight_orm_to_out(r: AiTelemetryCellInsightORM) -> CellInsightOut:
    comps_raw = json.loads(r.competitors_top3_json or "[]")
    recs_raw = json.loads(r.recommendations_json or "[]")
    return CellInsightOut(
        id=r.id, topic_id=r.topic_id, query=r.query, engine=r.engine,
        window_start=r.window_start, window_end=r.window_end,
        verdict=r.verdict, summary=r.summary or "",
        competitors_top3=[CompetitorMention(**c) for c in comps_raw if isinstance(c, dict)],
        recommendations=[CellInsightRec(**c) for c in recs_raw if isinstance(c, dict)],
        evidence_response_ids=json.loads(r.evidence_response_ids_json or "[]"),
        llm_model=r.llm_model or "", prompt_version=r.prompt_version or "",
        generated_at=r.generated_at, feedback=r.feedback,
    )


# ─────────────────── v1.1 LLM diagnosis ──────────────────────────


@router.post("/topics/{topic_id}/cells/insight", response_model=CellInsightOut)
async def fetch_cell_insight(
    topic_id: int, query: str, engine: str, force: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Triggered by the drawer's "Analyze" button — forwarded to telemetry-service for synchronous generation (LLM 3-8s)."""
    _get_topic_or_404(db, topic_id, current_user.id)
    url = f"{TELEMETRY_SERVICE_URL}/cell-insight"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, headers=internal_headers(), json={
                "topic_id": topic_id, "query": query, "engine": engine, "force": force,
            })
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    if not isinstance(data, dict) or data.get("status") == "not_found":
        raise HTTPException(404, "cell insight not generated")
    # Persisting to DB is done on the telemetry-service side; here raw dict → CellInsightOut
    return CellInsightOut(
        id=data["id"], topic_id=data["topic_id"], query=data["query"], engine=data["engine"],
        window_start=datetime.fromisoformat(data["window_start"]) if data.get("window_start") else datetime.utcnow(),
        window_end=datetime.fromisoformat(data["window_end"]) if data.get("window_end") else datetime.utcnow(),
        verdict=data["verdict"], summary=data.get("summary") or "",
        competitors_top3=[CompetitorMention(**c) for c in data.get("competitors_top3") or [] if isinstance(c, dict)],
        recommendations=[CellInsightRec(**c) for c in data.get("recommendations") or [] if isinstance(c, dict)],
        answer_format=data.get("answer_format"),
        citation_domains=data.get("citation_domains") or [],
        evidence_response_ids=data.get("evidence_response_ids") or [],
        llm_model=data.get("llm_model") or "", prompt_version=data.get("prompt_version") or "",
        generated_at=datetime.fromisoformat(data["generated_at"]) if data.get("generated_at") else datetime.utcnow(),
        feedback=data.get("feedback"),
    )


@router.post("/cell-insights/{insight_id}/feedback", status_code=204)
def post_cell_insight_feedback(
    insight_id: int, payload: FeedbackPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """👍 / 👎 / wrong feedback in the drawer."""
    row = db.get(AiTelemetryCellInsightORM, insight_id)
    if row is None:
        raise HTTPException(404, "insight not found")
    topic = db.get(AiTelemetryTopicORM, row.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "insight not found")
    row.feedback = payload.feedback
    db.commit()


# ─────────────────── v1.2 weekly report ──────────────────────────


def _briefing_orm_to_out(b: AiTelemetryTopicBriefingORM) -> BriefingOut:
    actions_raw = json.loads(b.top_actions_json or "[]")
    kpi_snapshot = json.loads(b.kpi_snapshot_json or "{}")
    period_type = b.period_type or "weekly"
    # Historical fallback rows once stored provider / key / raw errors. The API boundary converts them
    # into a customer-safe summary based on hard metrics, so old data stops leaking without waiting for a migration.
    from geo.services.briefing_public import sanitize_briefing_body
    return BriefingOut(
        id=b.id, topic_id=b.topic_id,
        period_start=b.period_start, period_end=b.period_end,
        period_type=period_type,
        body_md=sanitize_briefing_body(
            b.body_md,
            llm_model=b.llm_model,
            kpi=kpi_snapshot,
            period_type=period_type,
        ),
        kpi_snapshot=kpi_snapshot,
        top_actions=[BriefingAction(**a) for a in actions_raw if isinstance(a, dict)],
        delivered_email_at=b.delivered_email_at,
        feedback_score=b.feedback_score,
        llm_model=b.llm_model or "", prompt_version=b.prompt_version or "",
        generated_at=b.generated_at,
    )


@router.get("/topics/{topic_id}/briefings", response_model=list[BriefingOut])
def list_briefings(
    topic_id: int, limit: int = 12, period_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(db, topic_id, current_user.id, allow_admin_user=current_user, action=ACTION_READ)
    q = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
    )
    if period_type in ("daily", "weekly"):
        q = q.filter(AiTelemetryTopicBriefingORM.period_type == period_type)
    rows = (
        q.order_by(AiTelemetryTopicBriefingORM.period_end.desc())
         .limit(min(limit, 52))
         .all()
    )
    return [_briefing_orm_to_out(b) for b in rows]


@router.get("/briefings/{briefing_id}", response_model=BriefingOut)
def get_briefing(
    briefing_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    b = db.get(AiTelemetryTopicBriefingORM, briefing_id)
    if b is None:
        raise HTTPException(404, "briefing not found")
    topic = db.get(AiTelemetryTopicORM, b.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "briefing not found")
    return _briefing_orm_to_out(b)


@router.post("/briefings/{briefing_id}/feedback", status_code=204)
def post_briefing_feedback(
    briefing_id: int, payload: BriefingFeedbackPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    b = db.get(AiTelemetryTopicBriefingORM, briefing_id)
    if b is None:
        raise HTTPException(404, "briefing not found")
    topic = db.get(AiTelemetryTopicORM, b.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "briefing not found")
    b.feedback_score = payload.score
    db.commit()


@router.post("/topics/{topic_id}/briefings/generate", response_model=BriefingOut)
async def trigger_briefing_generation(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger generation of last week's report (admin / customer clicking regenerate)."""
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    _, operation = create_resource_operation_context(
        db,
        current_user,
        topic,
        action="briefing.generate",
        target_resource_type="topic",
        target_resource_id=topic.id,
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="briefing.generate",
        object_type="topic",
        object_id=topic.id,
        result="success",
    )
    db.commit()
    url = f"{TELEMETRY_SERVICE_URL}/briefing/generate"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, headers=internal_headers(), json={
                "topic_id": topic_id,
                "operation_context_id": operation.id,
            })
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    if not isinstance(data, dict) or data.get("status") in {"not_found", "cancelled"}:
        raise resource_not_found()
    # Re-read the ORM (already persisted)
    b = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.id == data["id"])
          .one_or_none()
    )
    if b is None:
        raise HTTPException(500, "briefing inconsistency")
    return _briefing_orm_to_out(b)


# ─────────────────── Internal email delivery (called by briefings) ────────────


@router.post("/internal/telemetry-briefing-email", include_in_schema=False)
def internal_briefing_email(payload: dict):
    """Called by telemetry-service after a weekly report is generated; delivers email via the internal network (Resend).

    Only logs for now; actual email code will be filled in once SMTP / Resend config is confirmed.
    No auth needed (internal call, backend ↔ telemetry-service in the same VPC).
    """
    topic_id = payload.get("topic_id")
    label = payload.get("label")
    body_md = (payload.get("body_md") or "")[:500]
    log.info("[BRIEFING DELIVER] topic=%s week=%s body=%s", topic_id, label, body_md)
    return {"status": "logged"}


@router.post("/internal/batch-prepay", include_in_schema=False)
def internal_batch_prepay(payload: dict, db: Session = Depends(get_db)):
    """Batch run **prepaid charge** — called by telemetry-service after creating a run and before dispatching any cell.

    Why charge in backend instead of telemetry itself: the charging invariants (conditional UPDATE +
    rowcount + SAVEPOINT + idem_key) have exactly one implementation in `balance_service`;
    telemetry is deployed independently and does not import backend code, and copying a second version is the
    riskiest option in this refactor (see the module docs of telemetry/app/billing.py). So that side only sends one HTTP call.

    No auth needed: internal call, backend ↔ telemetry-service in the same VPC (same as
    /internal/telemetry-briefing-email).

    Returns `{"status": "ok"|"insufficient"|"skipped", ...}` — only insufficient
    should block the batch run; skipped means "should not be charged anyway" (ops topic / master gate off / unit price 0).
    """
    from geo.services import batch_prepay

    topic_id = payload.get("topic_id")
    run_id = payload.get("run_id")
    if not topic_id or not run_id:
        raise HTTPException(400, "topic_id and run_id are required")
    topic = db.get(AiTelemetryTopicORM, int(topic_id))
    if topic is None:
        return {"status": batch_prepay.SKIPPED, "detail": "topic not found"}

    cells = payload.get("cells")
    cells = batch_prepay.cells_for_topic(topic) if cells is None else int(cells)
    info = batch_prepay.prepay(
        db, topic, run_id=int(run_id), cells=cells,
        reason=str(payload.get("reason") or ""),
    )
    db.commit()                            # prepay does not commit; it follows the caller's transaction
    if info["status"] == batch_prepay.INSUFFICIENT:
        info["message"] = batch_prepay.insufficient_message(info)
    return info


@router.post("/internal/batch-prepay-quote", include_in_schema=False)
def internal_batch_prepay_quote(payload: dict, db: Session = Depends(get_db)):
    """Quote only, no charge — used by drip to learn this round's cost before creating a run."""
    from geo.services import batch_prepay

    topic = db.get(AiTelemetryTopicORM, int(payload.get("topic_id") or 0))
    if topic is None:
        return {"chargeable": False, "cells": 0, "need_cents": 0, "balance_cents": 0}
    cells = payload.get("cells")
    cells = batch_prepay.cells_for_topic(topic) if cells is None else int(cells)
    return batch_prepay.quote(db, topic, cells)


# ─────── Dispatch center / worker management (/workbench/workers, admin) ────────
# Forwarded to telemetry-service /dispatch/*. telemetry validates X-Dispatch-Token;
# we carry the shared token here; frontend auth is handled by _require_admin.
DISPATCH_TOKEN = os.environ.get("DISPATCH_TOKEN", "").strip()


def _dispatch_headers() -> dict:
    return {"X-Dispatch-Token": DISPATCH_TOKEN} if DISPATCH_TOKEN else {}


@router.get("/admin/workers")
async def admin_list_workers(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEMETRY_SERVICE_URL}/dispatch/workers", headers=_dispatch_headers())
        r.raise_for_status()
        return r.json()


@router.get("/admin/workers-queue-stats")
async def admin_workers_queue_stats(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEMETRY_SERVICE_URL}/dispatch/queue/stats", headers=_dispatch_headers())
        r.raise_for_status()
        return r.json()


# This group must be **registered before** /admin/workers/{uid}/{action} — FastAPI matches
# in registration order, and that catch-all would swallow /admin/workers/xxx/forget too.
@router.get("/admin/workers/{worker_uid}/policy")
async def admin_get_worker_policy(
    worker_uid: str, current_user: User = Depends(get_current_user),
):
    """Effective runtime parameters of this machine (global and per-machine overrides returned separately)."""
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{TELEMETRY_SERVICE_URL}/dispatch/workers/{worker_uid}/policy",
            headers=_dispatch_headers(),
        )
        r.raise_for_status()
        return r.json()


@router.put("/admin/workers/{worker_uid}/policy")
async def admin_put_worker_policy(
    worker_uid: str, payload: dict, current_user: User = Depends(get_current_user),
):
    """Change this machine's runtime parameters. Takes effect on the next heartbeat (≤30s), no login or center restart needed."""
    _require_admin(current_user)
    body = {**(payload or {}), "updated_by": current_user.email or f"user:{current_user.id}"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            f"{TELEMETRY_SERVICE_URL}/dispatch/workers/{worker_uid}/policy",
            json=body, headers=_dispatch_headers(),
        )
        r.raise_for_status()
        return r.json()


@router.get("/admin/workers/{worker_uid}/diagnostics")
async def admin_worker_diagnostics(
    worker_uid: str, days: int = 3, current_user: User = Depends(get_current_user),
):
    """This machine's event timeline + failures grouped by type. Addresses "no idea why it stopped"."""
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{TELEMETRY_SERVICE_URL}/dispatch/workers/{worker_uid}/diagnostics",
            params={"days": days}, headers=_dispatch_headers(),
        )
        r.raise_for_status()
        return r.json()


@router.post("/admin/workers/{worker_uid}/forget")
async def admin_forget_worker(
    worker_uid: str, current_user: User = Depends(get_current_user),
):
    """Remove ghost rows from the registry (only long-offline ones allowed; historical tasks kept)."""
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{TELEMETRY_SERVICE_URL}/dispatch/workers/{worker_uid}/forget",
            headers=_dispatch_headers(),
        )
        if r.status_code == 409:      # "still heartbeating, disable first" — pass through to the panel as-is
            raise HTTPException(409, r.json().get("detail", "worker 仍在心跳"))
        r.raise_for_status()
        return r.json()


@router.post("/admin/workers/{worker_uid}/{action}")
async def admin_worker_action(
    worker_uid: str, action: str, current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    if action not in ("enable", "disable", "drain"):
        raise HTTPException(400, "action must be enable|disable|drain")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{TELEMETRY_SERVICE_URL}/dispatch/workers/{worker_uid}/{action}",
            headers=_dispatch_headers(),
        )
        r.raise_for_status()
        return r.json()


@router.post("/admin/authorize-account")
def admin_authorize_account(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    """Admin console "add account · password authorization" — proxies to browser-service /authorize:
    the server logs in automatically with the username/password (via proxy) → captures the login session into the pool.

    body: {engine, identifier, password}. Admin only. The password is only passed through to the internal worker, never persisted here.
    """
    _require_admin(current_user)
    engine = (payload or {}).get("engine")
    identifier = (payload or {}).get("identifier")
    password = (payload or {}).get("password")
    if not (engine and identifier and password):
        raise HTTPException(status_code=400, detail="engine/identifier/password 必填")
    import os
    import httpx
    url = os.environ.get("BROWSER_SERVICE_AUTHORIZE_URL", "http://127.0.0.1:8092").rstrip("/") + "/authorize"
    try:
        with httpx.Client(timeout=140) as c:
            r = c.post(url, json={"engine": engine, "identifier": identifier, "password": password})
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"authorize 代理失败: {e}")


def _bs_authorize_url(path: str) -> str:
    base = os.environ.get("BROWSER_SERVICE_AUTHORIZE_URL", "http://127.0.0.1:8092").rstrip("/")
    return base + path


@router.post("/admin/authorize-qr/start")
def admin_authorize_qr_start(payload: dict, current_user: User = Depends(get_current_user)):
    """QR authorization - start: proxies to browser-service /authorize/qr/start. Returns {session_id, qr_image}."""
    _require_admin(current_user)
    engine = (payload or {}).get("engine")
    if not engine:
        raise HTTPException(status_code=400, detail="engine 必填")
    import httpx
    try:
        with httpx.Client(timeout=95) as c:
            r = c.post(_bs_authorize_url("/authorize/qr/start"), json={"engine": engine})
        return r.json() if r.status_code < 400 else {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"qr start 代理失败: {e}")


@router.post("/admin/authorize-qr/poll")
def admin_authorize_qr_poll(payload: dict, current_user: User = Depends(get_current_user)):
    """QR authorization - poll: proxies to browser-service /authorize/qr/poll."""
    _require_admin(current_user)
    sid = (payload or {}).get("session_id")
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 必填")
    import httpx
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(_bs_authorize_url("/authorize/qr/poll"), json={"session_id": sid})
        return r.json() if r.status_code < 400 else {"status": "error", "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"qr poll 代理失败: {e}")


@router.post("/admin/authorize-sms/start")
def admin_authorize_sms_start(payload: dict, current_user: User = Depends(get_current_user)):
    """SMS code authorization - start: proxies to browser-service /authorize/sms/start. Returns {session_id, status, screenshot}."""
    _require_admin(current_user)
    engine = (payload or {}).get("engine")
    phone = (payload or {}).get("phone")
    if not engine or not phone:
        raise HTTPException(status_code=400, detail="engine/phone 必填")
    import httpx
    try:
        with httpx.Client(timeout=95) as c:
            r = c.post(_bs_authorize_url("/authorize/sms/start"),
                       json={"engine": engine, "phone": phone,
                             "country_code": str((payload or {}).get("country_code") or "86")})
        return r.json() if r.status_code < 400 else {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"sms start 代理失败: {e}")


@router.post("/admin/authorize-sms/submit")
def admin_authorize_sms_submit(payload: dict, current_user: User = Depends(get_current_user)):
    """SMS code authorization - submit code: proxies to browser-service /authorize/sms/submit."""
    _require_admin(current_user)
    sid = (payload or {}).get("session_id")
    code = (payload or {}).get("code")
    if not sid or not code:
        raise HTTPException(status_code=400, detail="session_id/code 必填")
    import httpx
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(_bs_authorize_url("/authorize/sms/submit"), json={"session_id": sid, "code": code})
        return r.json() if r.status_code < 400 else {"status": "error", "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"sms submit 代理失败: {e}")


@router.post("/admin/authorize-sms/poll")
def admin_authorize_sms_poll(payload: dict, current_user: User = Depends(get_current_user)):
    """SMS code authorization - poll/cancel: proxies to browser-service /authorize/sms/poll."""
    _require_admin(current_user)
    sid = (payload or {}).get("session_id")
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 必填")
    import httpx
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(_bs_authorize_url("/authorize/sms/poll"),
                       json={"session_id": sid, "cancel": bool((payload or {}).get("cancel"))})
        return r.json() if r.status_code < 400 else {"status": "error", "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"sms poll 代理失败: {e}")


@router.get("/admin/engine-sessions")
def admin_list_engine_sessions(
    page: int = 1,
    page_size: int = 20,
    engine: Optional[str] = None,
    status: Optional[str] = None,   # active | expired | quarantined
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dispatch center "account pool" list — engine/identifier/status/usage/expiry of each login session (account).

    Paging + filters (engine / status; status uses the "effective status": active but past
    expires_at counts as expired). Returns {items, total, grand_total, active_total,
    engines, page, page_size}: total is the filtered count (drives paging),
    grand_total / active_total are whole-pool stats (for the header "available n / total m"),
    engines lists engines ever seen in the pool (for the filter dropdown).
    """
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineSessionORM
    from geo.api.engine_sessions import _session_daily_cap, _local_today
    now = datetime.utcnow()
    today_local = _local_today()
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    not_expired = (EngineSessionORM.expires_at == None) | (EngineSessionORM.expires_at > now)  # noqa: E711

    # Per-engine offline counts and the engine dropdown are whole-pool (the dropdown must list every engine);
    # other counts (all/available/quarantined/expired) follow engine — pick "Qwen" and the numbers on the status pills
    # are Qwen's; otherwise the four numbers never change with filters and look broken.
    quar_rows = (db.query(EngineSessionORM.engine, func.count())
                 .filter(EngineSessionORM.status == "quarantined")
                 .group_by(EngineSessionORM.engine).all())
    quarantined_by_engine = {e: c for e, c in quar_rows}
    engines = [r[0] for r in
               db.query(EngineSessionORM.engine).distinct().order_by(EngineSessionORM.engine)]

    base = db.query(EngineSessionORM)
    if engine:
        base = base.filter(EngineSessionORM.engine == engine)
    grand_total = base.count()
    active_total = (base.filter(EngineSessionORM.status == "active")
                    .filter(not_expired).count())
    quarantined_total = base.filter(EngineSessionORM.status == "quarantined").count()

    q = base
    if status == "active":
        q = q.filter(EngineSessionORM.status == "active").filter(not_expired)
    elif status == "expired":
        # Effective status expired = marked active but expired (quarantined stays quarantined even if expired)
        q = q.filter(EngineSessionORM.status == "active").filter(~not_expired)
    elif status == "quarantined":
        q = q.filter(EngineSessionORM.status == "quarantined")
    total = q.count()
    rows = (q.order_by(EngineSessionORM.engine, EngineSessionORM.id)
            .offset((page - 1) * page_size).limit(page_size).all())
    items = []
    for r in rows:
        expired = r.expires_at is not None and r.expires_at <= now
        eff = "expired" if (expired and r.status == "active") else r.status
        # Used today: only counts when used_date is the local today, otherwise 0 (resets across days)
        used_today = (r.used_today or 0) if r.used_date == today_local else 0
        # Effective per-account cap: per-account daily_cap overrides the engine default; None=use default, 0=unlimited
        eff_cap = r.daily_cap if r.daily_cap is not None else _session_daily_cap(r.engine)
        remaining = None if not eff_cap else max(0, eff_cap - used_today)
        # Manually disabled check: unschedulable_until is in the future
        disabled = r.unschedulable_until is not None and r.unschedulable_until > now
        items.append({
            "id": r.id, "engine": r.engine, "label": r.source_label,
            "account_handle": r.account_handle,
            "status": eff,
            "use_count": r.use_count or 0, "captcha_count": r.captcha_count or 0,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "last_fail_type": r.last_fail_type,
            "used_today": used_today,
            "auth_type": r.auth_type,
            "egress": r.egress,
            "proxy_override": r.proxy_override,
            "proxy_id": r.proxy_id,
            "exit_ip": r.exit_ip,
            "exit_ip_at": r.exit_ip_at.isoformat() if r.exit_ip_at else None,
            "daily_cap": r.daily_cap,           # Per-account override (None=use engine default)
            "effective_cap": eff_cap,           # Actual effective cap (0=unlimited)
            "remaining": remaining,             # Remaining quota today (None=unlimited)
            "priority": r.priority if r.priority is not None else 50,
            "disabled": disabled,               # Whether manually disabled
            "rate_limited": r.rate_limited_until is not None and r.rate_limited_until > now,
        })
    return {"items": items, "total": total, "grand_total": grand_total,
            "active_total": active_total, "engines": engines,
            "quarantined_total": quarantined_total,
            "quarantined_by_engine": quarantined_by_engine,
            "page": page, "page_size": page_size}


class ScheduleTopicRow(BaseModel):
    topic_id: int
    name: str
    target: str
    user_id: int
    user_email: str = ""
    schedule_tier: str
    paused_until: Optional[datetime] = None
    effective_state: str
    resumes_at: Optional[datetime] = None
    # Cycle anchor: None = follow the global default (default_anchor_* gives the current default; frontend renders "default (Mon 02:05)")
    cycle_anchor_weekday: Optional[int] = None
    cycle_anchor_hour: Optional[int] = None
    query_count: int          # Number of selected prompts
    engines: list[str]
    pairs_per_cycle: int      # terms × engines = workload of one round
    last_run_at: Optional[datetime] = None
    backlog: int = 0          # In-flight (queued/claimed) tasks of this topic


class ScheduleEngineRow(BaseModel):
    engine: str
    channel: str = "browser"           # browser (uses the account pool) / api (no account, no daily cap)
    usable_accounts: int
    daily_cap: Optional[int] = None    # None = unlimited (API channel / cap=0)
    used_today: int
    demand_per_day: int                # daily-topic demand + weekly-topic demand/7
    sustainable: bool                  # Whether the daily cap can cover daily demand


def _engine_channel(engine: str) -> str:
    """Whether an engine uses the browser account pool or the API — same definition as telemetry `_is_browser_engine`.

    Doubao uses the API when an ARK key is configured: no account pool, no daily cap. The key lives in the telemetry process;
    when backend cannot read it, conservatively assume browser (better to show "cannot finish" than miss a capacity shortfall).

    Yuanbao (2026-09-21): **the criterion changed from "is TENCENT_SEARCH_API_KEY set" to "is the emergency egress on"**.
    Yuanbao is back to being an account-pool engine, yet that key is still configured (DeepSeek's web search also uses it) —
    judging by the key, this panel would report Yuanbao as "API = unlimited", while its real capacity is
    Σ cap of available Yuanbao accounts. The difference is exactly where this change is most likely to break: with no Yuanbao account
    in the pool, real capacity is 0, tasks stay queued and are cleared by the reaper after 24h — while the panel shows
    unlimited, blinding the one place that could catch it early. The definition lives in telemetry's
    `yuanbao_search.yuanbao_route_is_api()` (the single routing criterion for the whole service).
    """
    e = (engine or "").lower()
    if e == "doubao" and os.environ.get("ARK_API_KEY") and os.environ.get("DOUBAO_BOT_ID"):
        return "api"
    if (e == "yuanbao"
            and (os.environ.get("TELEMETRY_YUANBAO_ROUTE") or "pool").strip().lower() == "api"
            and os.environ.get("TENCENT_SEARCH_API_KEY")):
        return "api"
    return "browser"


class ScheduleOverviewOut(BaseModel):
    topics: list[ScheduleTopicRow]
    engines: list[ScheduleEngineRow]
    # Global default anchor (env SCHEDULER_CRON_*), lets the frontend render "follow default" as a concrete weekday and time.
    # These two env vars live in the telemetry process; if backend cannot read them, use the same in-code defaults (Mon 02:05).
    default_anchor_weekday: int = 0
    default_anchor_hour: int = 2
    default_anchor_minute: int = 5


@router.get("/admin/schedule-overview", response_model=ScheduleOverviewOut)
def admin_schedule_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batch-run scheduling overview: every topic's tier + whether each engine's capacity can keep up.

    Without this, configuring tiers in the back office hands ops a hidden "cannot finish" landmine — especially when some engine
    has only one or two usable accounts left; capacity must be visible before changing tiers.
    """
    _require_workbench(current_user)
    from geo.api.engine_sessions import _session_daily_cap
    from geo.models.engine_sessions import EngineSessionORM
    from geo.models.user import UserORM
    from geo.services.topic_schedule import resolve_schedule

    now = datetime.utcnow()
    topics = db.query(AiTelemetryTopicORM).order_by(AiTelemetryTopicORM.id).all()
    emails = {u.id: u.email for u in db.query(UserORM).all()}
    backlog_by_topic = {
        r[0]: r[1] for r in db.execute(sa_text(
            "SELECT topic_id, COUNT(*) FROM dispatch_tasks "
            "WHERE status IN ('queued','claimed') GROUP BY topic_id"
        )).all()
    }

    rows: list[ScheduleTopicRow] = []
    demand: dict[str, float] = {}
    for t in topics:
        dec = resolve_schedule(
            getattr(t, "schedule_tier", None), bool(t.enabled),
            getattr(t, "paused_until", None), now)
        try:
            qs = json.loads(t.queries_json or "[]")
        except Exception:  # noqa: BLE001
            qs = []
        # Same definition as telemetry parse_topic: dict form counts the selected ones, str form counts all
        sel = [q for q in qs if (isinstance(q, dict) and q.get("text") and q.get("selected", True))
               or isinstance(q, str)]
        try:
            engines = [str(e) for e in json.loads(t.engines_json or "[]") if e]
        except Exception:  # noqa: BLE001
            engines = []
        pairs = len(sel) * len(engines)
        rows.append(ScheduleTopicRow(
            topic_id=t.id, name=t.name, target=t.target or "",
            user_id=t.user_id, user_email=emails.get(t.user_id, ""),
            schedule_tier=dec.tier, paused_until=getattr(t, "paused_until", None),
            effective_state=dec.effective_state, resumes_at=dec.resumes_at,
            cycle_anchor_weekday=getattr(t, "cycle_anchor_weekday", None),
            cycle_anchor_hour=getattr(t, "cycle_anchor_hour", None),
            query_count=len(sel), engines=engines, pairs_per_cycle=pairs,
            last_run_at=t.last_run_at, backlog=backlog_by_topic.get(t.id, 0),
        ))
        # Daily demand: daily topics run a full round each day; weekly topics spread over 7 days
        if dec.should_run and engines:
            per_engine = len(sel)
            factor = 1.0 if dec.tier == "daily" else 1.0 / 7
            for e in engines:
                demand[e] = demand.get(e, 0.0) + per_engine * factor

    # Engine capacity: Σ(available accounts × their cap); any account with cap=0 or no account constraint → unlimited
    eng_rows: list[ScheduleEngineRow] = []
    for e in sorted(set(list(demand.keys()) + [
        r[0] for r in db.query(EngineSessionORM.engine).distinct().all()
    ])):
        default_cap = _session_daily_cap(e)
        sessions = db.query(EngineSessionORM).filter(
            EngineSessionORM.engine == e,
            EngineSessionORM.status == "active",
        ).all()
        usable = [s for s in sessions if
                  (s.expires_at is None or s.expires_at > now)
                  and (s.unschedulable_until is None or s.unschedulable_until <= now)
                  and (s.rate_limited_until is None or s.rate_limited_until <= now)]
        channel = _engine_channel(e)
        caps = [(s.daily_cap if s.daily_cap is not None else default_cap) for s in usable]
        unlimited = channel == "api" or default_cap == 0 or any(c == 0 for c in caps)
        total_cap = None if unlimited else sum(caps)
        used = db.execute(sa_text(
            "SELECT COUNT(*) FROM ai_telemetry_responses "
            "WHERE engine=:e AND created_at >= :ds"
        ), {"e": e, "ds": now.replace(hour=16, minute=0, second=0, microsecond=0)
            - timedelta(days=1 if now.hour < 16 else 0)}).scalar() or 0
        d = int(round(demand.get(e, 0.0)))
        eng_rows.append(ScheduleEngineRow(
            engine=e, channel=channel, usable_accounts=len(usable), daily_cap=total_cap,
            used_today=used, demand_per_day=d,
            sustainable=(total_cap is None or total_cap >= d),
        ))
    return ScheduleOverviewOut(
        topics=rows, engines=eng_rows,
        default_anchor_weekday=int(os.environ.get("SCHEDULER_CRON_WEEKDAY", "0") or 0),
        default_anchor_hour=int(os.environ.get("SCHEDULER_CRON_HOUR", "2") or 0),
        default_anchor_minute=int(os.environ.get("SCHEDULER_CRON_MINUTE", "5") or 0),
    )


class TopicSchedulePatch(BaseModel):
    """Topic batch-run tier change (workbench topic list). Only the provided fields are changed.

    Writing schedule_tier also derives enabled (the single write point); paused_until is a temporary pause,
    touching neither tier nor enabled — it resumes automatically when due, config preserved.
    clear_pause is a separate boolean: pydantic's Optional[datetime]=None cannot distinguish
    "not sent" from "sent null"; same sentinel idea as EngineSessionPatch but a boolean is clearer.
    """
    schedule_tier: Optional[Literal["daily", "weekly", "off"]] = None
    paused_until: Optional[datetime] = None   # Pause until this moment (UTC); must be in the future
    clear_pause: bool = False                 # True = resume immediately
    # Cycle anchor (topic-level override): weekday 0=Mon..6=Sun, hour 0-23 local time.
    # -1 = clear the override back to the global default (same sentinel idea as EngineSessionPatch); None = unchanged.
    cycle_anchor_weekday: Optional[int] = None
    cycle_anchor_hour: Optional[int] = None


@router.patch("/admin/topics/{topic_id}/schedule", response_model=TopicOut)
def admin_patch_topic_schedule(
    topic_id: int,
    payload: TopicSchedulePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Back-office batch-run tiers: daily / weekly / off / pause until a time / cycle anchor (weekday and hour to start).

    The scheduler reads the DB on every drip tick, so changes take effect immediately (next tick), no restart.
    If the anchor is moved to "today and already past the hour", this cycle starts today → the next tick opens a round;
    this is the proper ops entry for "start running today" (no need to change env or inject tasks manually).
    """
    _require_workbench(current_user)
    from geo.services.topic_schedule import (
        enabled_for_tier, normalize_anchor_hour, normalize_anchor_weekday, normalize_tier,
    )

    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise HTTPException(404, "topic not found")

    def _anchor_str(row) -> str:
        wd = getattr(row, "cycle_anchor_weekday", None)
        hh = getattr(row, "cycle_anchor_hour", None)
        return f"@{'默认' if wd is None else wd}/{'默认' if hh is None else hh}"

    before = f"{normalize_tier(getattr(t, 'schedule_tier', None))}" \
             f"{'/paused' if getattr(t, 'paused_until', None) else ''}" \
             f"{_anchor_str(t)}"

    if payload.schedule_tier is not None:
        t.schedule_tier = payload.schedule_tier
        t.enabled = enabled_for_tier(payload.schedule_tier)   # Single derived write point
    if payload.clear_pause:
        t.paused_until = None
    elif payload.paused_until is not None:
        pu = payload.paused_until
        if pu.tzinfo is not None:          # Frontend sends ISO with timezone → convert to naive UTC to match the DB
            pu = pu.astimezone(timezone.utc).replace(tzinfo=None)
        if pu <= datetime.utcnow():
            raise HTTPException(422, "paused_until 必须是将来的时间")
        t.paused_until = pu

    if payload.cycle_anchor_weekday is not None:
        if payload.cycle_anchor_weekday < 0:
            t.cycle_anchor_weekday = None          # Sentinel: back to global default
        elif normalize_anchor_weekday(payload.cycle_anchor_weekday) is None:
            raise HTTPException(422, "cycle_anchor_weekday 必须是 0(周一)~6(周日)")
        else:
            t.cycle_anchor_weekday = payload.cycle_anchor_weekday
    if payload.cycle_anchor_hour is not None:
        if payload.cycle_anchor_hour < 0:
            t.cycle_anchor_hour = None
        elif normalize_anchor_hour(payload.cycle_anchor_hour) is None:
            raise HTTPException(422, "cycle_anchor_hour 必须是 0~23")
        else:
            t.cycle_anchor_hour = payload.cycle_anchor_hour

    after = f"{normalize_tier(t.schedule_tier)}" \
            f"{'/paused' if t.paused_until else ''}" \
            f"{_anchor_str(t)}"
    if before != after:
        _append_changelog(t, actor_id=current_user.id, actor_role="admin",
                          field="schedule", before=before, after=after,
                          note="patch_schedule")
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


class EngineSessionPatch(BaseModel):
    """Account management change (worker management page). Only the provided fields are changed."""
    disabled: Optional[bool] = None    # True=disable (unschedulable) / False=restore
    daily_cap: Optional[int] = None    # Per-account daily cap: -1=clear override (engine default) / 0=unlimited / N=cap
    priority: Optional[int] = None     # Scheduling priority, lower value first
    proxy_override: Optional[str] = None   # Bound proxy string (http://user:pass@host:port); ""=unbind; None=unchanged
    proxy_id: Optional[int] = None     # Bound proxy-library entry; -1=unbind / N=bind proxy N / None=unchanged (takes precedence over the raw proxy_override string)


@router.patch("/admin/engine-sessions/{session_id}")
def admin_patch_engine_session(
    session_id: int,
    payload: EngineSessionPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Worker management: enable/disable account / change per-account daily cap / change priority / bind proxy."""
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineProxyORM, EngineSessionORM
    now = datetime.utcnow()
    row = db.query(EngineSessionORM).get(session_id)
    if row is None:
        raise HTTPException(404, f"session id={session_id} not found")
    if payload.disabled is not None:
        # Disable = set unschedulable_until far in the future; restore = clear it
        row.unschedulable_until = (now + timedelta(days=3650)) if payload.disabled else None
    if payload.daily_cap is not None:
        row.daily_cap = None if payload.daily_cap < 0 else payload.daily_cap
    if payload.priority is not None:
        row.priority = max(0, min(100, payload.priority))
    if payload.proxy_override is not None:
        # Empty string = unbind (back to egress/global); non-empty = bind this raw proxy string (highest priority)
        row.proxy_override = (payload.proxy_override.strip() or None)
    if payload.proxy_id is not None:
        # -1 = unbind the proxy-library reference; N = bind proxy-library entry N (must exist and not be disabled)
        if payload.proxy_id < 0:
            row.proxy_id = None
        else:
            px = db.query(EngineProxyORM).get(payload.proxy_id)
            if px is None:
                raise HTTPException(404, f"proxy id={payload.proxy_id} not found")
            row.proxy_id = px.id
    db.commit()
    db.refresh(row)
    disabled = row.unschedulable_until is not None and row.unschedulable_until > now
    return {"id": row.id, "daily_cap": row.daily_cap,
            "priority": row.priority, "disabled": disabled,
            "proxy_override": row.proxy_override, "proxy_id": row.proxy_id}


#: If the round trip to a domestic (CN) target exceeds this, the egress is judged "actually overseas" (regardless of the geo DB).
#: 1.5s sits between the two bands: measured CN egress 0.08~0.12s, cross-border from 1.7s, no samples in between.
_PROXY_CN_RTT_MS = int(os.environ.get("PROXY_CN_RTT_MS", "1500"))


def _probe_proxy(proxy: str) -> dict:
    """Run one real probe and return {ok, ip, country, status/error}.

    trust_env=False is key: geo-backend has an HTTP(S)_PROXY egress proxy configured (test host SS egress);
    without disabling it httpx would use that env proxy instead of this Bright Data proxy → the probe cannot see the real egress IP,
    and the frontend always shows failure. IP/country come from Bright Data's own lumtest (reachable from CN/VN egress, returns JSON)."""
    proxy = (proxy or "").strip()
    if not proxy:
        return {"ok": False, "ip": "", "country": "", "error": "empty proxy"}
    try:
        with httpx.Client(proxy=proxy, timeout=20.0, trust_env=False) as c:
            j = c.get("https://lumtest.com/myip.json").json()
            ip = str(j.get("ip") or "").strip()
            country = str(j.get("country") or "").strip()
            # **What the geo DB says does not count; only the round trip to the target site does.**
            # Measured 2026-09-10: lumtest classified several proxy egress IPs /
            # all as CN, labels even read "中国-" ("China-"), while a domestic geo DB (ipip) put them in
            # Tokyo, Japan / Singapore, with ISP even commed-info.ru. The resource inventory therefore assumed
            # 10 CN IPs, when there were only 4.
            # Do not argue about which geo DB is right — measure the round trip to a CN target directly:
            #     CN egress → Tongyi  0.08s
            #     these egress → Tongyi  1.7 ~ 17s
            # A 20~200x difference. An egress physically in China cannot take 17s to reach Tongyi; no room for misjudgment.
            # This number also directly answers "can it serve physical devices": browser collection sends dozens of requests per question,
            # and second-level round trips would break Playwright's timeout handling.
            rtt_ms = None
            try:
                t0 = time.time()
                c.get("https://www.tongyi.com/", timeout=20.0)
                rtt_ms = int((time.time() - t0) * 1000)
            except Exception:  # noqa: BLE001 — if it cannot be measured, skip it; does not affect ip/country
                pass
        verdict = country
        if rtt_ms is not None and rtt_ms > _PROXY_CN_RTT_MS:
            # Written into last_country because that is the field every display reads — if we only added a new column,
            # the admin page, account page and procurement inventory would still decide based on "CN".
            verdict = f"{country or '?'}·实测跨境{rtt_ms / 1000:.1f}s"
        return {"ok": bool(ip), "ip": ip, "country": verdict,
                "geo_country": country, "cn_rtt_ms": rtt_ms, "status": 200}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ip": "", "country": "", "error": repr(e)[:200]}

def _mask_proxy(url: str) -> str:
    """Mask the password in a proxy string: http://user:pass@host → http://user:****@host."""
    import re
    return re.sub(r"(://[^:/@]+:)([^@]+)(@)", r"\1****\3", url or "")


class ProxyTestIn(BaseModel):
    proxy: str   # http://user:pass@host:port


@router.post("/admin/engine-sessions/test-proxy")
def admin_test_proxy(payload: ProxyTestIn, current_user: User = Depends(get_current_user)):
    """Pre-bind self-check: run one geo probe and return the proxy's egress IP + country (avoid binding a dead/wrong-region IP)."""
    _require_admin(current_user)
    proxy = (payload.proxy or "").strip()
    if not proxy:
        raise HTTPException(400, "proxy is required")
    return _probe_proxy(proxy)


# ── Proxy library (engine_proxies): centrally registered proxies, accounts reference them by proxy_id ──────────
class ProxyCreateIn(BaseModel):
    label: str
    proxy_url: str            # http://user:pass@host:port
    note: Optional[str] = None


@router.get("/admin/proxies")
def admin_list_proxies(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List the proxy library (passwords masked) + how many accounts each one is bound to."""
    _require_admin(current_user)
    from sqlalchemy import func
    from geo.models.engine_sessions import EngineProxyORM, EngineSessionORM
    counts = dict(
        db.query(EngineSessionORM.proxy_id, func.count(EngineSessionORM.id))
        .filter(EngineSessionORM.proxy_id.isnot(None))
        .group_by(EngineSessionORM.proxy_id).all()
    )
    items = [{
        "id": p.id, "label": p.label, "proxy_url": _mask_proxy(p.proxy_url), "note": p.note,
        "disabled": bool(p.disabled), "bound_count": counts.get(p.id, 0),
        "last_tested_at": p.last_tested_at.isoformat() if p.last_tested_at else None,
        "last_exit_ip": p.last_exit_ip, "last_country": p.last_country,
    } for p in db.query(EngineProxyORM).order_by(EngineProxyORM.id).all()]
    return {"items": items}


@router.post("/admin/proxies")
def admin_create_proxy(payload: ProxyCreateIn, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Add a proxy. The frontend should pass test-proxy self-check before creating."""
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineProxyORM
    label = (payload.label or "").strip()
    url = (payload.proxy_url or "").strip()
    if not label or not url:
        raise HTTPException(400, "label and proxy_url required")
    p = EngineProxyORM(label=label, proxy_url=url, note=(payload.note or "").strip() or None)
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "label": p.label}


@router.post("/admin/proxies/{proxy_id}/test")
def admin_test_proxy_by_id(proxy_id: int, current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """Self-check one proxy-library entry and write egress IP/country/time back to it."""
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineProxyORM
    p = db.query(EngineProxyORM).get(proxy_id)
    if p is None:
        raise HTTPException(404, "proxy not found")
    res = _probe_proxy(p.proxy_url)
    p.last_tested_at = datetime.utcnow()
    p.last_exit_ip = res.get("ip") or None
    p.last_country = res.get("country") or None
    db.commit()
    return res


@router.delete("/admin/proxies/{proxy_id}")
def admin_delete_proxy(proxy_id: int, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Delete a proxy: first unbind accounts referencing it (proxy_id cleared, falls back to egress/global), then delete."""
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineProxyORM, EngineSessionORM
    p = db.query(EngineProxyORM).get(proxy_id)
    if p is None:
        raise HTTPException(404, "proxy not found")
    n = (db.query(EngineSessionORM).filter(EngineSessionORM.proxy_id == proxy_id)
         .update({"proxy_id": None}))
    db.delete(p); db.commit()
    return {"ok": True, "unbound": n}


@router.get("/admin/engine-sessions/by-ip")
def admin_engine_sessions_by_ip(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate the account pool by egress IP: accounts per IP, engines, total usage today, density.
    Used for the "per IP × per account" view to see which IP is overcrowded (prone to risk control).

    cooling / muted_24h signal "has this egress already been flagged by the platform": rate-limit mutes are judged per IP,
    issued in per-IP clusters (accounts on the same egress muted in the same minute), with escalating penalties. 2 or more
    accounts muted on one IP within 24h = time to change the IP, not to add accounts."""
    _require_admin(current_user)
    from geo.models.engine_sessions import EngineSessionORM
    from geo.api.engine_sessions import _local_today
    now = datetime.utcnow()
    today_local = _local_today()
    since_24h = now - timedelta(hours=24)
    not_expired = (EngineSessionORM.expires_at == None) | (EngineSessionORM.expires_at > now)  # noqa: E711
    rows = (db.query(EngineSessionORM)
            .filter(EngineSessionORM.status == "active").filter(not_expired).all())
    agg: dict = {}
    for r in rows:
        ip = r.exit_ip or "(未探测)"
        key = (ip, r.engine)
        g = agg.setdefault(key, {"exit_ip": ip, "engine": r.engine,
                                 "accounts": 0, "used_today": 0,
                                 "cooling": 0, "muted_24h": 0})
        g["accounts"] += 1
        g["used_today"] += (r.used_today or 0) if r.used_date == today_local else 0
        if r.rate_limited_until and r.rate_limited_until > now:
            g["cooling"] += 1
        if r.last_fail_type == "rate_limited" and r.last_fail_at and r.last_fail_at > since_24h:
            g["muted_24h"] += 1
    groups = sorted(agg.values(), key=lambda x: (x["exit_ip"], x["engine"]))
    # Total accounts per IP (across engines) — to see density
    ip_totals: dict = {}
    for g in groups:
        ip_totals[g["exit_ip"]] = ip_totals.get(g["exit_ip"], 0) + g["accounts"]
    for g in groups:
        g["ip_account_total"] = ip_totals[g["exit_ip"]]
    return {"groups": groups, "ip_count": len(ip_totals)}


@router.post("/admin/heal-engine/{engine}")
def admin_heal_engine(
    engine: str,
    current_user: User = Depends(get_current_user),
):
    """Engine self-healing (browser-agent Phase 2): run the structure probe → LLM proposes new selectors.
    **Proposes only, never writes automatically** (apply after human review, to avoid overwriting with bad config). Returns {probe, proposal}."""
    _require_admin(current_user)
    from geo.api.platform_rules import _llm_text
    base = os.environ.get("BROWSER_SERVICE_AUTHORIZE_URL", "http://127.0.0.1:8092").rstrip("/")
    # 1) Structure probe (browser-service subprocess, read-only)
    try:
        with httpx.Client(timeout=140) as c:
            probe = c.post(f"{base}/heal-probe", json={"engine": engine, "query": "canary"}).json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"heal-probe 失败: {e}")
    # 2) LLM proposes selectors
    prompt = (
        f"你是浏览器自动化专家。下面是 AI 搜索引擎「{engine}」网页(可能刚改版)的结构探针。\n"
        "请定位并**只输出 JSON**(不要任何解释/markdown),字段:\n"
        '{"input_sels":[...],"send_sels":[...],"answer_sels":[...],"chat_api":"<completion API url 子串>"}\n'
        "- input_sels: 输入框 CSS 候选链(优先 contenteditable / textarea)\n"
        "- send_sels: 发送按钮 CSS 候选(优先 aria-label 含 发送/Send)\n"
        "- answer_sels: 答案容器 CSS 候选(从候选答案容器里挑最像正文外壳的 class/data 属性)\n"
        "- chat_api: completion 流 API 的 url 子串(从网络 API 里挑含 web_source/messages/stream 的)\n\n"
        f"可交互元素: {json.dumps((probe.get('interactive') or [])[:30], ensure_ascii=False)}\n"
        f"候选答案容器: {json.dumps((probe.get('answer_candidates') or [])[:12], ensure_ascii=False)}\n"
        f"网络API: {json.dumps((probe.get('apis') or [])[:8], ensure_ascii=False)}\n"
    )
    raw = _llm_text(prompt)
    # Parse the JSON returned by the LLM (strip ```json fences)
    proposal = None
    s = raw.strip()
    if "```" in s:
        s = s.split("```")[1] if s.count("```") >= 2 else s
        s = s.replace("json", "", 1).strip() if s.lstrip().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            proposal = json.loads(s[i:j + 1])
        except Exception:  # noqa: BLE001
            proposal = None
    # 3) Validation gate: run a canary with the proposed selectors, confirm it really yields answer + citations (only then worth applying)
    validation = None
    if proposal:
        try:
            with httpx.Client(timeout=150) as c:
                validation = c.post(f"{base}/heal-validate",
                                    json={"engine": engine, "override": proposal}).json()
        except Exception as e:  # noqa: BLE001
            validation = {"error": f"validate 调用失败: {e}"[:150], "ans_len": 0, "cites": 0}
    validated = bool(validation and validation.get("ans_len", 0) > 0)
    return {"engine": engine, "probe_error": probe.get("error"),
            "proposal": proposal, "validation": validation, "validated": validated,
            "llm_raw": raw[:800]}


_SELECTORS_OVERRIDE_FILE = os.environ.get(
    "ENGINE_SELECTORS_OVERRIDE_FILE", "/opt/geo/engine_selectors_override.json")


@router.get("/engine-selectors-config")
def engine_selectors_config():
    """Central selector override config (pulled by each browser worker). Self-heal apply writes here → takes effect fleet-wide.
    No auth (only selectors, not sensitive), read-only."""
    try:
        with open(_SELECTORS_OVERRIDE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


class HealApplyIn(BaseModel):
    input_sels: Optional[list] = None
    send_sels: Optional[list] = None
    answer_sels: Optional[list] = None
    chat_api: Optional[str] = None
    url: Optional[str] = None


@router.post("/admin/heal-engine/{engine}/apply")
def admin_heal_engine_apply(
    engine: str,
    payload: HealApplyIn,
    current_user: User = Depends(get_current_user),
):
    """Write a reviewed selector proposal into the central config (overriding that engine); all workers pick it up on their next query."""
    _require_admin(current_user)
    patch = {k: v for k, v in payload.dict().items() if v}
    if not patch:
        raise HTTPException(400, "空提议,无可写入")
    try:
        with open(_SELECTORS_OVERRIDE_FILE, encoding="utf-8") as f:
            store = json.load(f)
    except Exception:  # noqa: BLE001
        store = {}
    cur = store.get(engine) if isinstance(store.get(engine), dict) else {}
    cur.update(patch)
    store[engine] = cur
    with open(_SELECTORS_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    return {"engine": engine, "applied": cur}


@router.get("/admin/engine-health")
def admin_engine_health(current_user: User = Depends(get_current_user)):
    """Latest engine health sentinel results (JSON written periodically by engine_health_check.py).
    {checked_at, engines:[{engine, ok, ans_len, cites, error}]}. File missing → empty."""
    _require_admin(current_user)
    path = os.environ.get("ENGINE_HEALTH_FILE", "/opt/geo/engine_health.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"checked_at": None, "engines": []}


@router.get("/admin/engine-capacity")
def admin_engine_capacity(current_user: User = Depends(get_current_user)):
    """Latest guarantee-line audit results (JSON written every 30min by scheduler._check_capacity_guarantee).
    {checked_at, engines:[{engine, target, ceiling, used_today, headroom, projected_eod,
    structural_shortfall, recoverable_shortfall, refresh_candidates_pending, cooling:[...]}]}.
    File missing → empty."""
    _require_admin(current_user)
    path = os.environ.get("ENGINE_CAPACITY_FILE", "/opt/geo/engine_capacity.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"checked_at": None, "engines": []}


# ─────── External gateway operations (/workbench/gateway, admin) ────────
# Forwarded to telemetry-service /v1/admin/* (validates X-Admin-Token); frontend auth is handled by
# _require_admin. GATEWAY_ADMIN_TOKEN is held only server-side and never sent to the browser via normal endpoints —
# the sole exception is /admin/vnet-console below: handed once to an admin who clicks "enter collection network console".
GATEWAY_ADMIN_TOKEN = os.environ.get("GATEWAY_ADMIN_TOKEN", "").strip()


def _gateway_headers() -> dict:
    return {"X-Admin-Token": GATEWAY_ADMIN_TOKEN} if GATEWAY_ADMIN_TOKEN else {}


# One-click entry to the collection network console (V-networks worker-center/ui, mounted at <worker-center-host>/admin).
# That side only accepts a single GATEWAY_ADMIN_TOKEN and does not know our users (V-networks docs/03 §1); "who may enter" is decided
# by the admin gate here: after admin validation, the key is placed in the URL fragment for the browser to jump with (fragments do not
# reach nginx logs or the server, and the console wipes it on receipt). Cannot use 302: the JWT is in localStorage and direct browser
# navigation does not carry Authorization, so the frontend fetches the url first, then window.open.
# Not derived from TELEMETRY_SERVICE_URL: in production that is a compose-internal name (http://telemetry:8080) the browser cannot open.
VNET_CONSOLE_URL = os.environ.get("VNET_CONSOLE_URL", "https://<worker-center-host>/admin").strip()


@router.get("/admin/vnet-console")
async def admin_vnet_console(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    if not GATEWAY_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="GATEWAY_ADMIN_TOKEN 未配置,无法进入采集网络管理台")
    return {"url": f"{VNET_CONSOLE_URL}#token={GATEWAY_ADMIN_TOKEN}"}


@router.get("/admin/gateway/tenants")
async def admin_gateway_tenants(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants", headers=_gateway_headers())
        r.raise_for_status()
        data = r.json()
    # enrich: user_id → platform account email (AdminGateway / AdminUsers show the bound account)
    from geo.models.user import UserORM
    from geo.database import SessionLocal
    uids = [t.get("user_id") for t in data.get("tenants", []) if t.get("user_id")]
    if uids:
        db = SessionLocal()
        try:
            rows = db.query(UserORM.id, UserORM.email).filter(UserORM.id.in_(uids)).all()
            emails = {i: e for i, e in rows}
        finally:
            db.close()
        for t in data.get("tenants", []):
            t["bound_email"] = emails.get(t.get("user_id"))
    return data


class GatewayCreateTenantPayload(BaseModel):
    name: str
    tier: str = "free"
    engines: list[str] = []
    daily_quota_default: int = 20
    daily_quota: dict = {}
    # Purpose: customer = external customer / internal = own systems / test = integration testing
    kind: str = "customer"
    # Account setup, one of three: no binding / email+password creates a new platform account / user_id binds an existing account
    email: Optional[str] = None
    password: Optional[str] = None
    user_id: Optional[int] = None


@router.post("/admin/gateway/tenants")
async def admin_gateway_create_tenant(payload: GatewayCreateTenantPayload,
                                      current_user: User = Depends(get_current_user)):
    """Create a tenant, optionally also creating a platform account (role=tenant) and binding it.

    Orchestration: create/validate user first → proxy to telemetry to create the tenant (with user_id) → if telemetry
    fails, compensate by deleting the just-created user (no real cross-service transaction; compensating delete is simplest and reliable).
    """
    _require_admin(current_user)
    from geo.services.user_service import user_service
    from geo.models.user import UserCreate, UserORM
    from geo.database import SessionLocal

    if payload.email and payload.user_id:
        raise HTTPException(400, "邮箱开户与绑定已有账号只能二选一")

    bind_user_id: Optional[int] = None
    created_user_id: Optional[int] = None
    bound_email: Optional[str] = None

    if payload.email:
        if not payload.password or len(payload.password) < 6:
            raise HTTPException(400, "初始密码至少 6 位")
        if user_service.get_user_by_email(payload.email):
            raise HTTPException(400, "该邮箱已注册,请改用「绑定已有账号」")
        new_user = user_service.create_user(
            UserCreate(email=payload.email, password=payload.password), role="tenant")
        bind_user_id = created_user_id = new_user.id
        bound_email = new_user.email
    elif payload.user_id:
        db = SessionLocal()
        try:
            u = db.get(UserORM, payload.user_id)
            if not u:
                raise HTTPException(404, "要绑定的账号不存在")
            role = getattr(u, "role", None) or ("admin" if u.is_admin else "user")
            if role in ("admin", "operator"):
                raise HTTPException(400, "管理员/运营账号不能绑定为租户")
            bound_email = u.email
        finally:
            db.close()
        upgraded_from_user = False
        if role == "user":
            user_service.update_user_role(payload.user_id, "tenant")
            upgraded_from_user = True
        bind_user_id = payload.user_id

    body = {
        "name": payload.name, "tier": payload.tier, "engines": payload.engines,
        "daily_quota_default": payload.daily_quota_default,
        "daily_quota": payload.daily_quota,
        "kind": payload.kind,
        # Audit trail: who created the tenant. Tenants created directly by manual scripts will be identified by this column.
        "created_by": current_user.email,
    }
    if bind_user_id is not None:
        body["user_id"] = bind_user_id
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants",
                                  json=body, headers=_gateway_headers())
            _raise_gateway_error(r)
            out = r.json()
    except Exception:
        # Compensation: telemetry tenant creation failed, roll back this request's changes to users
        if created_user_id is not None:
            try:
                user_service.delete_user(created_user_id)
            except Exception:  # noqa: BLE001
                log.warning("compensating delete of user %s failed", created_user_id)
        elif payload.user_id and upgraded_from_user:
            try:
                user_service.update_user_role(payload.user_id, "user")
            except Exception:  # noqa: BLE001
                log.warning("compensating role revert of user %s failed", payload.user_id)
        raise
    if bound_email:
        out["bound_email"] = bound_email
    return out


def _raise_gateway_error(r: "httpx.Response") -> None:
    """telemetry 4xx (e.g. 409 for one account bound to multiple tenants) is passed through with the original status code + reason,
    instead of being swallowed into a 500 by raise_for_status."""
    if r.status_code < 400:
        return
    detail = None
    try:
        detail = (r.json() or {}).get("detail")
    except Exception:  # noqa: BLE001
        pass
    raise HTTPException(r.status_code, detail or f"gateway error {r.status_code}")


@router.patch("/admin/gateway/tenants/{tenant_id}")
async def admin_gateway_update_tenant(tenant_id: int, payload: dict,
                                      current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.patch(f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants/{tenant_id}",
                               json=payload, headers=_gateway_headers())
        _raise_gateway_error(r)
        return r.json()


@router.post("/admin/gateway/tenants/{tenant_id}/credits")
async def admin_gateway_topup(tenant_id: int, payload: dict,
                              current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants/{tenant_id}/credits",
                              json=payload, headers=_gateway_headers())
        r.raise_for_status()
        return r.json()


@router.post("/admin/gateway/tenants/{tenant_id}/keys")
async def admin_gateway_reissue_key(tenant_id: int, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{TELEMETRY_SERVICE_URL}/v1/admin/tenants/{tenant_id}/keys",
                              headers=_gateway_headers())
        r.raise_for_status()
        return r.json()


@router.post("/admin/gateway/keys/{key_id}/revoke")
async def admin_gateway_revoke_key(key_id: int, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{TELEMETRY_SERVICE_URL}/v1/admin/keys/{key_id}/revoke",
                              headers=_gateway_headers())
        r.raise_for_status()
        return r.json()


@router.get("/admin/gateway/jobs")
async def admin_gateway_jobs(tenant_id: int | None = None, limit: int = 50, offset: int = 0,
                             engine: str | None = None, status: str | None = None,
                             since: str | None = None, until: str | None = None,
                             source: str | None = None,
                             current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    params: dict = {"limit": limit, "offset": offset}
    if tenant_id is not None:
        params["tenant_id"] = tenant_id
    if engine:
        params["engine"] = engine
    if status:
        params["status"] = status
    if source:
        params["source"] = source      # batch = internal batch run / api = external tenant call
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TELEMETRY_SERVICE_URL}/v1/admin/jobs",
                             params=params, headers=_gateway_headers())
        r.raise_for_status()
        return r.json()
