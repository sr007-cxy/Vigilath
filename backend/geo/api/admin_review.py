"""Admin 审核 — Phase C(单条种子/query 审核)+ Phase D(整张申请审核).

Phase D 新增端点(以 topic 为整体审核单位):
- GET    /admin/review/topics                          — 列出全部 topic 申请,带 status 过滤
- GET    /admin/review/topic/{id}                      — 单条申请的完整资料(资料 / 种子 / 监测问题 / 日志)
- PATCH  /admin/review/topic/{id}                      — admin 修改资料 / 种子 / 监测问题(走 changelog)
- POST   /admin/review/topic/{id}/approve              — 通过:跑一次 + 生成执行计划书 + 异步生成文案稿 + 邮件
- POST   /admin/review/topic/{id}/reject               — 拒绝 + 邮件
- GET    /admin/review/topic/{id}/execution-plan       — 拿执行计划书(含 4 分区 + 运行进度,轮询用)

Phase C 单条端点(种子词逐条 / queries 批量)保留向后兼容.
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import require_admin
from geo.api.ai_telemetry import get_db, _append_changelog
from geo.models.ai_telemetry import (
    AiTelemetryQueryHitORM, AiTelemetryRunORM, AiTelemetryTopicORM,
    AiTelemetryTopicExecutionPlanORM, BrandProfile, MAX_SELECTED_QUERIES,
    TopicChangelogEntry, TopicExecutionPlanOut, TopicProgressCell,
    ExpansionLogEntry, TopicOut,
)
from geo.models.user import UserORM

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/review")

TELEMETRY_SERVICE_URL = os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095")


# ═════════════════ Phase C — pending(种子 / 单条 query)═════════════════


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
    _admin = Depends(require_admin),
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


def _update_seed_status(t: AiTelemetryTopicORM, idx: int, new_status: str, reviewer_id: int) -> None:
    try:
        seeds = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds = []
    if not isinstance(seeds, list) or idx < 0 or idx >= len(seeds):
        raise HTTPException(404, "seed prompt index not found")
    item = seeds[idx]
    if not isinstance(item, dict):
        raise HTTPException(400, "malformed seed prompt entry")
    if item.get("status") != "pending":
        raise HTTPException(409, f"seed prompt is already {item.get('status')}")
    item["status"] = new_status
    now = datetime.utcnow().isoformat()
    if new_status == "approved":
        item["approved_at"] = now
    else:
        item["rejected_at"] = now
    item["reviewer_id"] = reviewer_id
    seeds[idx] = item
    t.seed_prompts_json = json.dumps(seeds, ensure_ascii=False)


@router.post("/seed/{topic_id}/{idx}/approve", status_code=204)
def approve_seed(
    topic_id: int, idx: int,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    _update_seed_status(t, idx, "approved", admin.id)
    db.commit()


@router.post("/seed/{topic_id}/{idx}/reject", status_code=204)
def reject_seed(
    topic_id: int, idx: int,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    _update_seed_status(t, idx, "rejected", admin.id)
    db.commit()


class QueriesBatchPayload(BaseModel):
    indices: list[int] = Field(..., min_length=1, max_length=50)


def _update_queries_status(t: AiTelemetryTopicORM, indices: list[int],
                           new_status: str, reviewer_id: int) -> None:
    try:
        q_list = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        q_list = []
    if not isinstance(q_list, list):
        raise HTTPException(400, "malformed queries_json")
    now = datetime.utcnow().isoformat()
    for idx in indices:
        if idx < 0 or idx >= len(q_list):
            raise HTTPException(404, f"query index {idx} out of range")
        item = q_list[idx]
        if not isinstance(item, dict):
            raise HTTPException(400, f"query[{idx}] not a dict")
        if item.get("status") != "pending":
            raise HTTPException(409, f"query[{idx}] is already {item.get('status')}")
        item["status"] = new_status
        if new_status == "approved":
            item["approved_at"] = now
        else:
            item["rejected_at"] = now
        item["reviewer_id"] = reviewer_id
        q_list[idx] = item
    t.queries_json = json.dumps(q_list, ensure_ascii=False)


@router.post("/queries/{topic_id}/approve", status_code=204)
def approve_queries(
    topic_id: int,
    payload: QueriesBatchPayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    _update_queries_status(t, payload.indices, "approved", admin.id)
    db.commit()


@router.post("/queries/{topic_id}/reject", status_code=204)
def reject_queries(
    topic_id: int,
    payload: QueriesBatchPayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    _update_queries_status(t, payload.indices, "rejected", admin.id)
    db.commit()


# ═════════════════ Phase D — 整张申请审核 ═════════════════


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
    version: int = 1                          # 修订号,跟 TopicOut.version 对齐


class TopicReviewDetailOut(TopicOut):
    user_email: str = ""
    topic_changelog: list[TopicChangelogEntry] = Field(default_factory=list)
    expansion_log: list[ExpansionLogEntry] = Field(default_factory=list)


def _parse_log_list(raw: str | None) -> list[dict]:
    try:
        arr = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    return [x for x in arr if isinstance(x, dict)]


@router.get("/topics", response_model=list[TopicReviewListItem])
def list_topic_reviews(
    status: Optional[str] = None,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """全平台 topic 申请列表;status 过滤:draft / pending / approved / rejected.

    默认按 submitted_at desc 排;无 submitted_at 的退回 created_at desc.
    """
    q = db.query(AiTelemetryTopicORM)
    if status:
        q = q.filter(AiTelemetryTopicORM.submission_status == status)
    rows = q.all()
    user_ids = {r.user_id for r in rows}
    email_by_uid: dict[int, str] = {}
    if user_ids:
        for u in db.query(UserORM).filter(UserORM.id.in_(user_ids)).all():
            email_by_uid[u.id] = u.email
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
        ))
    out.sort(key=lambda x: (x.submitted_at or datetime.min, x.topic_id), reverse=True)
    return out


@router.get("/topic/{topic_id}", response_model=TopicReviewDetailOut)
def get_topic_review_detail(
    topic_id: int,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    base = TopicOut.from_orm_row(t)
    user = db.get(UserORM, t.user_id)
    out = TopicReviewDetailOut(
        **base.model_dump(),
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
    """admin 编辑资料 / 种子 / queries 任一字段;字段缺省 = 不改."""
    profile: Optional[BrandProfile] = None
    seed_prompts: Optional[list[str]] = None         # 若给,以这份替换种子列表(text 集合)
    selected_query_texts: Optional[list[str]] = None  # 若给,把这些 text 的 selected 设 True,其余 False
    add_queries: Optional[list[str]] = None           # 追加 query 候选(status=approved,直接可选)
    note: Optional[str] = Field(None, max_length=500)


@router.patch("/topic/{topic_id}", response_model=TopicReviewDetailOut)
def admin_patch_topic(
    topic_id: int,
    payload: AdminPatchTopicPayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """admin 在审核期编辑申请.changelog 全程追加,actor_role=admin."""
    t = _load_topic_or_404(db, topic_id)
    if t.submission_status not in ("pending", "draft", "rejected"):
        raise HTTPException(409, {"code": "LOCKED_STATUS",
                                  "message": f"submission_status={t.submission_status}"})

    actor_id = admin.id

    if payload.profile is not None:
        t.profile_json = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
        if payload.profile.industry:
            t.industry = payload.profile.industry
        if payload.profile.company_short_name:
            t.target = payload.profile.company_short_name
        if payload.profile.profile_name:
            t.name = payload.profile.profile_name
        _append_changelog(t, actor_id=actor_id, actor_role="admin",
                          field="profile",
                          after=payload.profile.profile_name or payload.profile.company_short_name)

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
                "message": f"最多 {MAX_SELECTED_QUERIES} 个监测问题,当前 {len(desired)}",
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
        # 添加 desired 中不在 qarr 的(admin 直接挑了新文本进监测)
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
        **base.model_dump(),
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
    """发审核结果邮件;失败不抛(邮件挂了不应该阻塞审核)."""
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


def _trigger_run_topic_sync(topic_id: int) -> Optional[int]:
    """同步触发 telemetry-service /run-topic,返回 run_id(失败返回 None)."""
    url = f"{TELEMETRY_SERVICE_URL}/run-topic"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, json={"topic_id": topic_id})
            r.raise_for_status()
            data = r.json()
            run_id = data.get("run_id") if isinstance(data, dict) else None
            return int(run_id) if isinstance(run_id, int) else None
    except Exception as e:  # noqa: BLE001
        log.warning("trigger run failed for topic %d: %s", topic_id, e)
        return None


def _build_execution_plan_snapshot(t: AiTelemetryTopicORM, run_id: int | None) -> dict:
    """通过审核时落 ExecutionPlan 那一刻的快照."""
    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    monitored = [
        q["text"] for q in qarr
        if isinstance(q, dict) and q.get("text") and q.get("selected", True)
        and q.get("status") == "approved"
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
        "engines": engines,
        "engines_count": len(engines),
        "estimated_cells": len(monitored) * len(engines),
        "snapshot_at": datetime.utcnow().isoformat(),
        "approved_by_run_id": run_id,
    }
    return {
        "overview": overview,
        "monitored_queries": monitored,
        "changelog": _parse_log_list(t.topic_changelog_json),
        "expansion_log": _parse_log_list(t.expansion_log_json),
    }


def _schedule_content_generation(topic_id: int, plan_id: int) -> None:
    """异步触发内容文案生成(由 Task 4 的 content_generator 真正实现)."""
    try:
        from geo.services.content_generator import schedule_generation
        schedule_generation(topic_id=topic_id, plan_id=plan_id)
    except Exception as e:  # noqa: BLE001
        log.warning("schedule content generation failed: %s", e)


@router.post("/topic/{topic_id}/approve", response_model=TopicExecutionPlanOut)
def approve_topic(
    topic_id: int,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """通过整张申请.副作用:

    1. submission_status=approved + approved_at + reviewer_id
    2. 所有 selected query 同步 status=approved(approved_at / reviewer_id 也填)
    3. 触发 telemetry-service /run-topic 跑一次 → 拿 run_id
    4. 创建 ExecutionPlan 行(snapshot + run_id),返回给前端
    5. 异步:基于资料 + 监测问题生成内容文案稿
    6. 异步:发邮件通知用户
    """
    t = _load_topic_or_404(db, topic_id)
    if t.submission_status == "approved":
        raise HTTPException(409, {"code": "ALREADY_APPROVED", "message": "已通过审核"})

    now = datetime.utcnow()
    # 步骤 1: 状态机
    prev_status = t.submission_status
    t.submission_status = "approved"
    t.approved_at = now
    t.rejected_at = None
    t.reviewer_id = admin.id

    # 步骤 2: 同步收尾子项状态 —— topic 已通过意味着 admin 接纳了整张申请,
    # 候选池里所有还在 pending 的子项视为同步通过。selected 是独立的「是否纳入
    # 监测」开关,跟审核状态解耦。不动 rejected(admin 已明确拒过的)和 approved。
    #
    # 历史 bug:这里只翻 `selected=True && status!=approved`,导致 unselected 候选
    # 和 seed_prompts 永远卡 pending,前端「外面已通过、里面待审」的状态裂开。
    now_iso = now.isoformat()
    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    q_promoted = 0
    for q in qarr:
        if not isinstance(q, dict) or not q.get("text"):
            continue
        if q.get("status") == "pending":
            q["status"] = "approved"
            q["approved_at"] = now_iso
            q["reviewer_id"] = admin.id
            q_promoted += 1
    t.queries_json = json.dumps(qarr, ensure_ascii=False)

    try:
        sarr = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        sarr = []
    s_promoted = 0
    for s in sarr:
        if not isinstance(s, dict) or not s.get("text"):
            continue
        if s.get("status") == "pending":
            s["status"] = "approved"
            s["approved_at"] = now_iso
            s["reviewer_id"] = admin.id
            s_promoted += 1
    t.seed_prompts_json = json.dumps(sarr, ensure_ascii=False)

    _append_changelog(t, actor_id=admin.id, actor_role="admin",
                      field="submission_status", before=prev_status, after="approved",
                      note=f"queries_pending→approved={q_promoted}, seeds_pending→approved={s_promoted}")

    db.flush()  # 让 topic 拿到最新状态用于 snapshot

    # 步骤 3: 触发跑一次
    run_id = _trigger_run_topic_sync(topic_id)

    # 步骤 4: 创建 ExecutionPlan
    snapshot = _build_execution_plan_snapshot(t, run_id)
    plan = AiTelemetryTopicExecutionPlanORM(
        topic_id=topic_id,
        generated_by_reviewer_id=admin.id,
        overview_json=json.dumps(snapshot["overview"], ensure_ascii=False),
        topic_changelog_snapshot_json=json.dumps(snapshot["changelog"], ensure_ascii=False),
        expansion_log_snapshot_json=json.dumps(snapshot["expansion_log"], ensure_ascii=False),
        monitored_queries_snapshot_json=json.dumps(snapshot["monitored_queries"], ensure_ascii=False),
        run_id=run_id,
        status="ready" if run_id is not None else "failed",
        error=None if run_id is not None else "telemetry-service /run-topic failed",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    # 步骤 5/6: 异步任务
    user = db.get(UserORM, t.user_id)
    plan_url = None
    try:
        from geo.database import settings as _s
        plan_url = f"{(_s.FRONTEND_URL or '').rstrip('/')}/workbench/topics/{topic_id}/execution-plan"
    except Exception:  # noqa: BLE001
        pass

    if user and user.email:
        background.add_task(_send_review_email_safe,
                            to=user.email, topic_name=t.name,
                            decision="approved", execution_plan_url=plan_url)
    background.add_task(_schedule_content_generation,
                        topic_id=topic_id, plan_id=plan.id)

    return _to_plan_out(db, plan)


class RejectTopicPayload(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/topic/{topic_id}/reject", status_code=204)
def reject_topic(
    topic_id: int,
    payload: RejectTopicPayload,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = _load_topic_or_404(db, topic_id)
    if t.submission_status == "approved":
        raise HTTPException(409, {"code": "ALREADY_APPROVED",
                                  "message": "已通过审核,不可拒绝"})
    prev_status = t.submission_status
    t.submission_status = "rejected"
    t.rejected_at = datetime.utcnow()
    t.reviewer_id = admin.id
    _append_changelog(t, actor_id=admin.id, actor_role="admin",
                      field="submission_status", before=prev_status, after="rejected",
                      note=payload.reason or None)
    db.commit()
    user = db.get(UserORM, t.user_id)
    if user and user.email:
        background.add_task(_send_review_email_safe,
                            to=user.email, topic_name=t.name,
                            decision="rejected", reject_reason=payload.reason or None)


# ───────────── 执行计划书 — 拉取 + 运行进度 ─────────────


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

    # 实时聚合运行进度
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
            progress_cells.append(TopicProgressCell(
                query=c.query, engine=c.engine, status=c.status,
                hit=(True if (c.total_hits or 0) > 0
                     else (False if c.status == "done" else None)),
                last_checked_at=c.last_checked_at,
            ))
            if c.status == "done":
                done += 1
    return TopicExecutionPlanOut(
        id=plan.id, topic_id=plan.topic_id,
        generated_at=plan.generated_at,
        generated_by_reviewer_id=plan.generated_by_reviewer_id,
        status=plan.status, error=plan.error,
        overview=overview,
        topic_changelog=changelog,
        expansion_log=expansion,
        monitored_queries=[str(x) for x in monitored if x],
        run_id=plan.run_id, run_status=run_status,
        progress=progress_cells,
        progress_done=done,
        progress_total=len(progress_cells),
    )


@router.get("/topic/{topic_id}/execution-plan", response_model=TopicExecutionPlanOut)
def get_execution_plan(
    topic_id: int,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """拿这个 topic 最新的执行计划书(含运行进度).没有则 404."""
    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if not plan:
        raise HTTPException(404, "no execution plan generated")
    return _to_plan_out(db, plan)
