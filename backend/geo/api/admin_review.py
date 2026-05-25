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
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import require_admin
from geo.api.ai_telemetry import get_db, _append_changelog
from geo.models.ai_telemetry import (
    AiTelemetryQueryHitORM, AiTelemetryRunORM, AiTelemetryTopicORM,
    AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicSolutionORM,
    BrandProfile, GenerateSolutionPayload, MAX_SELECTED_QUERIES,
    PROFILE_REQUIRED_FIELDS, PublishPlanItem, SolutionDiagnosis,
    SolutionDiagnosisCheck, SolutionDiagnosisCluster, SolutionKeywordTier,
    SolutionQueriesSnapshot, SolutionQueryCluster, SolutionQueryItem,
    SolutionSevenStepItem, SolutionVisionItem, TopicChangelogEntry,
    TopicExecutionPlanOut, TopicGeneratedDocORM, TopicProgressCell,
    TopicSolutionOut, ExpansionLogEntry, TopicOut,
)
from geo.models.user import UserORM

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/review")
# 去审核新通道:admin 直接「启动」项目,不再走 pending → approved 那一步.
# 旧 router 在 PR 2 退役;本 router 是长期入口.
topics_router = APIRouter(prefix="/admin/topics")

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


# Phase C 子项审核端点(approve/reject seed + queries)在去审核流里退役了.
# admin 直建项目就落 approved,不再需要逐条审批通道.helpers 保留供 PATCH 用.


# ═════════════════ Phase D — 整张申请审核 ═════════════════


# 项目进度 stage 状态值域 — 与前端 AdminCockpit.tsx 的 StageState 保持一致.
# done / running / pending / blocked / idle 共 5 态.
StageState = str

# 审核未通过 → 后续 stage 一律 idle(还没批准就不能开工).这条规则是闸门,
# 避免每个映射函数都重复写,统一在调用点判断后再传 approved=True 进来.

def _diagnose_state(approved: bool, sol: Optional[AiTelemetryTopicSolutionORM]) -> StageState:
    """健康度诊断报告 — TopicSolution 表(每 topic 唯一)."""
    if not approved or sol is None:
        return "idle"
    s = (sol.status or "").lower()
    return {"ready": "done", "generating": "running", "failed": "blocked"}.get(s, "idle")


def _plan_state(approved: bool, plan: Optional[AiTelemetryTopicExecutionPlanORM]) -> StageState:
    """执行策略与规划 — TopicExecutionPlan(取最新一条).审批通过时自动生成."""
    if not approved or plan is None:
        return "idle"
    s = (plan.status or "").lower()
    return {"ready": "done", "generating": "running", "failed": "blocked"}.get(s, "idle")


def _content_state(approved: bool, agg: dict[str, int]) -> StageState:
    """内容发布与审核 — TopicGeneratedDoc 按 status 聚合.

    agg = {"draft": n, "pending_review": n, "approved": n, "rejected": n, "published": n}.
    优先级:已发布 > 待 admin 操作(待审 / 已批准未标发布) > 仅 draft(LLM 出稿中)
    > 全部 rejected > 无文稿.
    """
    if not approved:
        return "idle"
    total = sum(agg.values())
    if total == 0:
        return "idle"
    if agg.get("published", 0) > 0:
        return "done"
    if agg.get("pending_review", 0) > 0 or agg.get("approved", 0) > 0:
        return "pending"  # 等 admin 审 / 等 admin 标记发布
    if agg.get("draft", 0) > 0:
        return "running"  # LLM 还在出稿
    return "blocked"      # 剩下只能是 rejected 全部死光


def _insight_state(approved: bool, last_status: Optional[str], last_at) -> StageState:
    """效果查验与更新 — 直接读 topic 表的 last_run_status / last_run_at,免 N+1."""
    if not approved or last_at is None:
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
    version: int = 1                          # 修订号,跟 TopicOut.version 对齐
    # 项目进度 stage 3-6 状态(stage 1 永远 done、stage 2 由 submission_status 推).
    diagnose_status: StageState = "idle"
    plan_status: StageState = "idle"
    content_status: StageState = "idle"
    insight_status: StageState = "idle"


class TopicReviewDetailOut(TopicOut):
    user_id: int = 0
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

    # 项目进度 stage 3-6 的批量预取(全部按 topic_id IN (...) 一次拿).
    topic_ids = [r.id for r in rows]
    sol_by_tid: dict[int, AiTelemetryTopicSolutionORM] = {}
    plan_by_tid: dict[int, AiTelemetryTopicExecutionPlanORM] = {}
    doc_agg_by_tid: dict[int, dict[str, int]] = {}
    if topic_ids:
        for s in db.query(AiTelemetryTopicSolutionORM).filter(
            AiTelemetryTopicSolutionORM.topic_id.in_(topic_ids)
        ).all():
            sol_by_tid[s.topic_id] = s   # unique 约束保证每 topic 至多一条
        # 执行计划可能多条(重新批准会再生成),按 id desc 取最新一条
        for p in db.query(AiTelemetryTopicExecutionPlanORM).filter(
            AiTelemetryTopicExecutionPlanORM.topic_id.in_(topic_ids)
        ).order_by(AiTelemetryTopicExecutionPlanORM.id.desc()).all():
            plan_by_tid.setdefault(p.topic_id, p)
        # 文稿按 (topic_id, status) 聚合,只拉两列避免拖出 body_markdown 巨量文本
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
        approved = (r.submission_status == "approved")
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
            diagnose_status=_diagnose_state(approved, sol_by_tid.get(r.id)),
            plan_status=_plan_state(approved, plan_by_tid.get(r.id)),
            content_status=_content_state(approved, doc_agg_by_tid.get(r.id, {})),
            insight_status=_insight_state(approved, r.last_run_status, r.last_run_at),
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
        user_id=t.user_id,
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
    """admin 编辑申请.changelog 全程追加,actor_role=admin.
    任意 submission_status 都允许 — admin 是终审,approved 之后也能修正
    资料/种子/监测问题(已生成的内容稿件不会自动重跑,需手动触发).
    """
    t = _load_topic_or_404(db, topic_id)

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
        user_id=t.user_id,
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


def _post_approval_pipeline(
    db: Session,
    t: AiTelemetryTopicORM,
    actor_id: int,
    background: BackgroundTasks,
) -> AiTelemetryTopicExecutionPlanORM:
    """启动项目的副作用串:跑分 + 落 ExecutionPlan + 异步生成文案 + 异步发邮件.

    被 approve_topic(旧审核流)和 start_topic(去审核流)共用.
    调用前提:t 的状态变更必须已经 db.commit() —— 否则 SQLite 写锁会阻塞 telemetry-service
    那一侧的 ai_telemetry_runs 写入(就是原 approve_topic 注释里写的「database is locked」根因).
    """
    topic_id = t.id
    run_id = _trigger_run_topic_sync(topic_id)
    snapshot = _build_execution_plan_snapshot(t, run_id)
    plan = AiTelemetryTopicExecutionPlanORM(
        topic_id=topic_id,
        generated_by_reviewer_id=actor_id,
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
    return plan


# approve_topic / reject_topic 在去审核流里退役了.功能改由 POST /admin/topics/{id}/start
# 承担(见本文件 start_topic 端点).rerun_topic 与 _post_approval_pipeline 仍在,给重启复用.


# ───────────── 执行计划书 — 拉取 + 运行进度 ─────────────


DEFAULT_PUBLISH_PLATFORMS = ["公众号", "小红书", "抖音", "视频号"]


def _build_publishing_plan(
    db: Session, topic_id: int,
    monitored: list[str], overview: dict,
) -> list[PublishPlanItem]:
    """按 query 命中率排优先级,每天 1 篇,关联现有 generated docs."""
    # 拿所有相关 cell,按 query 聚合
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

    # 拿已生成的内容文档,by source_query_text 对齐
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .all()
    )
    doc_by_query: dict[str, TopicGeneratedDocORM] = {}
    for d in docs:
        if d.source_query_text and d.source_query_text not in doc_by_query:
            doc_by_query[d.source_query_text] = d

    # 平台:从 overview 里取(plan 生成时画像快照),空则用默认
    snapshot_profile = overview.get("profile_snapshot") if isinstance(overview, dict) else None
    platforms: list[str] = []
    if isinstance(snapshot_profile, dict):
        raw = snapshot_profile.get("target_platforms") or []
        if isinstance(raw, list):
            platforms = [str(p) for p in raw if p]
    if not platforms:
        platforms = DEFAULT_PUBLISH_PLATFORMS

    items_raw: list[dict] = []
    for q in monitored:
        agg = by_query.get(q, {"runs": 0, "hits": 0})
        runs = agg["runs"]
        hits = agg["hits"]
        coverage = round(hits / runs * 100, 1) if runs > 0 else 0.0
        if coverage == 0:
            priority = "high"
        elif coverage < 50:
            priority = "med"
        else:
            priority = "low"
        doc = doc_by_query.get(q)
        items_raw.append({
            "query": q,
            "coverage_pct": coverage,
            "priority": priority,
            "doc_id": doc.id if doc else None,
            "doc_status": doc.status if doc else None,
        })

    # 排序:优先级高 → 低,同级按 query 字典序
    pri_rank = {"high": 0, "med": 1, "low": 2}
    items_raw.sort(key=lambda x: (pri_rank.get(x["priority"], 9), x["query"]))

    today = datetime.utcnow().date()
    out: list[PublishPlanItem] = []
    for idx, it in enumerate(items_raw):
        out.append(PublishPlanItem(
            day=idx,
            publish_date=(today + timedelta(days=idx)).isoformat(),
            query=it["query"],
            coverage_pct=it["coverage_pct"],
            priority=it["priority"],
            doc_id=it["doc_id"],
            doc_status=it["doc_status"],
            suggested_platforms=platforms,
        ))
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

    publishing_plan = _build_publishing_plan(db, plan.topic_id, monitored, overview)

    return TopicExecutionPlanOut(
        id=plan.id, topic_id=plan.topic_id,
        generated_at=plan.generated_at,
        generated_by_reviewer_id=plan.generated_by_reviewer_id,
        status=plan.status, error=plan.error,
        overview=overview,
        topic_changelog=changelog,
        expansion_log=expansion,
        monitored_queries=monitored,
        run_id=plan.run_id, run_status=run_status,
        progress=progress_cells,
        progress_done=done,
        progress_total=len(progress_cells),
        publishing_plan=publishing_plan,
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


@router.post("/topic/{topic_id}/rerun", response_model=TopicExecutionPlanOut)
def rerun_topic(
    topic_id: int,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """重新触发跑批 — 给 plan failed / run_id 缺失 / 想再跑一次的场景用.

    调用 telemetry-service /run-topic 拿新 run_id,
    更新当前 topic 最新的 execution plan(run_id / status / error)并返回.
    没 plan 时新建一条.
    """
    t = _load_topic_or_404(db, topic_id)
    run_id = _trigger_run_topic_sync(topic_id)

    plan = (
        db.query(AiTelemetryTopicExecutionPlanORM)
          .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
          .first()
    )
    if plan is None:
        # 没历史 plan,直接补一条
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
    else:
        plan.run_id = run_id
        plan.status = "ready" if run_id is not None else "failed"
        plan.error = None if run_id is not None else "telemetry-service /run-topic failed"
        plan.generated_at = datetime.utcnow()
        plan.generated_by_reviewer_id = admin.id

    _append_changelog(t, actor_id=admin.id, actor_role="admin",
                      field="rerun", after=f"run_id={run_id}",
                      note="admin 手动重新触发跑批")

    db.commit()
    db.refresh(plan)
    return _to_plan_out(db, plan)


# ═════════════════ v3.3 — GEO 品牌增长战略方案 ═════════════════


def _solution_to_out(sol: AiTelemetryTopicSolutionORM) -> TopicSolutionOut:
    """ORM → TopicSolutionOut。status != ready 时各内容块为 None / 空,
    前端按 status 渲染 loading / error / content 三态."""
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
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """拿这个 topic 最新的战略方案。无记录时返回 status='idle' 的存根 —
    前端按 status 切「未生成 / 生成中 / 已生成 / 失败」四态,不用关心 404."""
    _load_topic_or_404(db, topic_id)   # 校验 topic 存在
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
    # 双保险:startup 兜底之外,读路径再检一次。daemon thread 死掉时 updated_at
    # 不会再涨,超过阈值即视为僵尸 — 翻成 failed 让前端能拿到重试入口。
    if sol.status == "generating":
        from geo.services.solution_generator import STALE_GENERATING_THRESHOLD
        if sol.updated_at and datetime.utcnow() - sol.updated_at > STALE_GENERATING_THRESHOLD:
            sol.status = "failed"
            sol.error = "后端在生成途中重启,后台任务被中断。请点重试重新生成。"
            sol.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(sol)
    return _solution_to_out(sol)


def _trigger_solution_generation(topic_id: int, website_url: str, admin_id: int) -> None:
    """后台触发战略方案生成 — 走 solution_generator 的 daemon thread."""
    try:
        from geo.services.solution_generator import schedule_solution_generation
        schedule_solution_generation(
            topic_id=topic_id, website_url=website_url, admin_id=admin_id,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("schedule solution generation failed: %s", e)


@router.post("/topic/{topic_id}/solution/generate", response_model=TopicSolutionOut)
def generate_strategic_solution(
    topic_id: int,
    payload: GenerateSolutionPayload,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """触发(或重新触发)战略方案生成 — 异步后台跑.

    流程:
      1. 先 upsert solution 行,status=generating + website_url + 清空 error
      2. BackgroundTasks 触发 solution_generator daemon thread
      3. 立刻返回 status=generating 给前端;前端 3s 轮询 GET solution 直到 ready/failed
    """
    t = _load_topic_or_404(db, topic_id)

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
    sol.generated_by_admin_id = admin.id
    sol.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sol)

    background.add_task(
        _trigger_solution_generation,
        topic_id=topic_id, website_url=sol.website_url, admin_id=admin.id,
    )
    _append_changelog(t, actor_id=admin.id, actor_role="admin",
                      field="solution", after=sol.website_url,
                      note="admin 触发战略方案生成")
    db.commit()
    return _solution_to_out(sol)


# ════════════ 去审核新通道:POST /admin/topics/{id}/start ════════════
# 替代 approve_topic 在新流程里的位置.状态翻转那一段(approve_topic 步骤 1/2)
# 不再需要,因为 admin 直建主题已经落 submission_status=approved.start 只负责
# 触发后续 5 个副作用(跑分 / plan / 文案 / 邮件).
#
# 幂等规则:同 topic 已有 ready/generating 的 plan 时,GET 行为(返回最新一条);
# 只有显式 ?force=true 才重新触发.前端「重新启动」按钮带 force=true.


def _validate_topic_runnable(t: AiTelemetryTopicORM) -> None:
    """启动前校验:画像必填齐 + ≥1 个 selected query + ≥1 engine.沿用 submit 那套逻辑."""
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
            "message": "请至少勾选 1 个监测问题",
        })
    if selected_n > MAX_SELECTED_QUERIES:
        raise HTTPException(422, {
            "code": "TOO_MANY_SELECTED", "field": "queries",
            "message": f"最多 {MAX_SELECTED_QUERIES} 个监测问题,当前 {selected_n}",
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


@topics_router.post("/{topic_id}/start", response_model=TopicExecutionPlanOut)
def start_topic(
    topic_id: int,
    background: BackgroundTasks,
    force: bool = False,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """启动项目(去审核流).

    - 默认幂等:同 topic 已有 ready/generating 的 plan → 直接返回最新一条
    - ?force=true → 跳过幂等,强制再触发一次(对应「重新启动」按钮)
    - 校验:画像必填齐 + ≥1 selected query + ≥1 engine
    """
    t = _load_topic_or_404(db, topic_id)
    _validate_topic_runnable(t)

    if not force:
        existing = (
            db.query(AiTelemetryTopicExecutionPlanORM)
              .filter(AiTelemetryTopicExecutionPlanORM.topic_id == topic_id)
              .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
              .first()
        )
        if existing and existing.status in ("ready", "generating"):
            return _to_plan_out(db, existing)

    # 若 topic 还在 draft(用户自建未提交),启动顺便翻成 approved.
    # admin 直建本来就是 approved,此处幂等.
    if t.submission_status != "approved":
        prev = t.submission_status
        t.submission_status = "approved"
        t.approved_at = datetime.utcnow()
        t.rejected_at = None
        t.reviewer_id = admin.id
        _append_changelog(t, actor_id=admin.id, actor_role="admin",
                          field="submission_status", before=prev, after="approved",
                          note="start_topic")
    db.commit()
    db.refresh(t)

    plan = _post_approval_pipeline(db, t, admin.id, background)
    return _to_plan_out(db, plan)
