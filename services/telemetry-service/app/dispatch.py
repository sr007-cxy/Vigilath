"""调度中心(dispatch center)— pull 模型的 worker 接口 + 管理接口.

多机 browser-service worker 部署后只配 DISPATCH_CENTER_URL + DISPATCH_TOKEN,
启动即 register,然后周期 heartbeat + claim(领任务)+ result(交结果)。中心负责:
- worker 注册表(browser_workers)
- 任务队列(dispatch_tasks)的原子领取:Postgres FOR UPDATE SKIP LOCKED 防多 worker 重复领
- 每引擎日上限在 claim 侧执行(deepseek≤50/天 等),disabled/draining worker 领空
- result 落库走与 push 路径完全一致的 detect_hit → save_response → update_query_hit

鉴权:所有 /dispatch/* 路由校验 X-Dispatch-Token == env DISPATCH_TOKEN(未设则开放,dev)。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from .scheduler import _day_start_utc, _engine_daily_cap
from .storage import (
    DispatchTaskORM, ResponseORM, TopicORM, WorkerORM,
    db_session, engine as _db_engine, parse_target, save_response,
)
from .tracking import detect_hit, update_query_hit_after_response

log = logging.getLogger("telemetry-service.dispatch")

DISPATCH_TOKEN = os.environ.get("DISPATCH_TOKEN", "").strip()
WORKER_OFFLINE_SEC = int(os.environ.get("DISPATCH_WORKER_OFFLINE_SEC", "90"))
_IS_PG = _db_engine.dialect.name == "postgresql"


def _require_token(x_dispatch_token: str = Header(default="")) -> None:
    if DISPATCH_TOKEN and x_dispatch_token != DISPATCH_TOKEN:
        raise HTTPException(401, "bad dispatch token")


router = APIRouter(prefix="/dispatch", dependencies=[Depends(_require_token)])


# ── 请求/响应模型 ──────────────────────────────────────────────────
class RegisterBody(BaseModel):
    worker_uid: Optional[str] = None
    hostname: str = ""
    label: str = ""
    region: str = ""
    exit_ip: str = ""
    engines: list[str] = []
    max_concurrency: int = 3
    version: str = ""


class HeartbeatBody(BaseModel):
    worker_uid: str
    in_flight: int = 0
    breaker: dict = {}          # {engine: until_epoch} 本地熔断态(仅展示用)
    exit_ip: str = ""


class ClaimBody(BaseModel):
    worker_uid: str
    engines: list[str] = []
    free_slots: int = 1


class ClaimedTask(BaseModel):
    task_id: int
    run_id: int
    topic_id: int
    engine: str
    query: str


class ResultBody(BaseModel):
    worker_uid: str = ""
    task_id: int
    answer: str = ""
    citations: list[dict] = []
    source_url: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None


# ── worker 生命周期 ────────────────────────────────────────────────
@router.post("/register")
def register(body: RegisterBody) -> dict:
    uid = (body.worker_uid or "").strip() or uuid.uuid4().hex
    now = datetime.utcnow()
    with db_session() as s:
        w = s.query(WorkerORM).filter(WorkerORM.worker_uid == uid).one_or_none()
        if w is None:
            w = WorkerORM(worker_uid=uid, enabled=True, registered_at=now)
            s.add(w)
        w.hostname = body.hostname or w.hostname
        w.label = body.label or w.label
        w.region = body.region or w.region
        w.exit_ip = body.exit_ip or w.exit_ip
        w.engines_json = json.dumps(body.engines or [], ensure_ascii=False)
        w.max_concurrency = body.max_concurrency or 3
        w.version = body.version or w.version
        w.status = "online"
        w.last_heartbeat_at = now
    log.info("[dispatch] worker registered uid=%s host=%s ip=%s engines=%s",
             uid, body.hostname, body.exit_ip, body.engines)
    return {"worker_uid": uid, "heartbeat_sec": WORKER_OFFLINE_SEC // 3 or 30}


@router.post("/heartbeat")
def heartbeat(body: HeartbeatBody) -> dict:
    now = datetime.utcnow()
    with db_session() as s:
        w = s.query(WorkerORM).filter(WorkerORM.worker_uid == body.worker_uid).one_or_none()
        if w is None:
            raise HTTPException(404, "worker not registered")
        w.last_heartbeat_at = now
        if body.exit_ip:
            w.exit_ip = body.exit_ip
        if w.status not in ("draining", "disabled"):
            w.status = "online"
        w.meta_json = json.dumps({"in_flight": body.in_flight, "breaker": body.breaker},
                                  ensure_ascii=False)
        enabled = bool(w.enabled) and w.status != "draining"
    return {"ok": True, "enabled": enabled}


@router.post("/claim", response_model=list[ClaimedTask])
def claim(body: ClaimBody) -> list[ClaimedTask]:
    n = max(0, min(body.free_slots, 50))
    if n == 0 or not body.engines:
        return []
    now_utc = datetime.now(timezone.utc)
    day_start = _day_start_utc(now_utc)
    with db_session() as s:
        w = s.query(WorkerORM).filter(WorkerORM.worker_uid == body.worker_uid).one_or_none()
        if w is None or not w.enabled or w.status == "draining":
            return []                       # 未注册 / 被禁用 / 排空中 → 不派活
        # 今日各引擎已发起数(success+fail 都占额度),据此筛掉到顶的引擎
        rows = s.execute(text(
            "SELECT engine, COUNT(*) n FROM ai_telemetry_responses "
            "WHERE created_at >= :ds GROUP BY engine"
        ), {"ds": day_start}).all()
        used = {r.engine: r.n for r in rows}
        allowed = [e for e in body.engines
                   if used.get(e, 0) < _engine_daily_cap(e)]
        if not allowed:
            return []
        tasks = _claim_rows(s, allowed, n, body.worker_uid)
    return [ClaimedTask(**t) for t in tasks]


def _claim_rows(s, engines: list[str], n: int, uid: str) -> list[dict]:
    """原子领取 n 条 queued 任务并置 claimed。Postgres 用 SKIP LOCKED 防并发重复领。"""
    now = datetime.utcnow()
    if _IS_PG:
        sql = text(
            "WITH picked AS ("
            "  SELECT id FROM dispatch_tasks "
            "  WHERE status='queued' AND engine IN :engines "
            "  ORDER BY priority, id "
            "  FOR UPDATE SKIP LOCKED LIMIT :n"
            ") "
            "UPDATE dispatch_tasks t "
            "SET status='claimed', claimed_by=:uid, claimed_at=:now, attempts=attempts+1 "
            "FROM picked WHERE t.id = picked.id "
            "RETURNING t.id, t.run_id, t.topic_id, t.engine, t.query"
        ).bindparams(bindparam("engines", expanding=True))
        rows = s.execute(sql, {"engines": engines, "n": n, "uid": uid, "now": now}).all()
        return [{"task_id": r.id, "run_id": r.run_id, "topic_id": r.topic_id,
                 "engine": r.engine, "query": r.query} for r in rows]
    # SQLite / 其它(本地单 worker 验证)— 无 SKIP LOCKED,SELECT 后逐条更新
    picked = (s.query(DispatchTaskORM)
              .filter(DispatchTaskORM.status == "queued",
                      DispatchTaskORM.engine.in_(engines))
              .order_by(DispatchTaskORM.priority, DispatchTaskORM.id)
              .limit(n).all())
    out = []
    for t in picked:
        t.status = "claimed"; t.claimed_by = uid; t.claimed_at = now; t.attempts = (t.attempts or 0) + 1
        out.append({"task_id": t.id, "run_id": t.run_id, "topic_id": t.topic_id,
                    "engine": t.engine, "query": t.query})
    return out


@router.post("/result")
def result(body: ResultBody) -> dict:
    """交结果:落 response(命中判定+矩阵)与 push 路径一致;失败且有重试余量则退回队列。"""
    with db_session() as s:
        task = s.get(DispatchTaskORM, body.task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        topic = s.get(TopicORM, task.topic_id)
        target, aliases = parse_target(topic) if topic else ("", [])
        error = (body.error or "").strip() or None
        hit, excerpt = (False, None)
        if not error:
            hit, excerpt = detect_hit(body.answer, target, aliases)
        r = save_response(
            s, run_id=task.run_id, topic_id=task.topic_id, engine=task.engine,
            query=task.query, answer=body.answer, citations=body.citations,
            video_url=body.video_url, error=error, hit=hit, hit_excerpt=excerpt,
            source_url=body.source_url,
        )
        if not error and topic is not None:
            update_query_hit_after_response(s, response=r, topic=topic)
        # 失败且仍有重试余量 → 退回队列(下次任意 worker 再领);否则定状态收尾
        if error and (task.attempts or 0) < (task.max_attempts or 1):
            task.status = "queued"; task.claimed_by = None; task.claimed_at = None
            task.error = error
            requeued = True
        else:
            task.status = "failed" if error else "done"
            task.finished_at = datetime.utcnow()
            task.error = error
            requeued = False
    return {"ok": True, "hit": hit, "requeued": requeued}


# ── 管理接口(后端 require_admin 代理调用)────────────────────────────
@router.get("/workers")
def list_workers() -> list[dict]:
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=WORKER_OFFLINE_SEC)
    day_start_naive = _day_start_utc(datetime.now(timezone.utc))
    with db_session() as s:
        workers = s.query(WorkerORM).order_by(WorkerORM.id).all()
        # 每 worker 今日完成数 + 在飞行数(从 dispatch_tasks 的 claimed_by 统计)
        done_rows = s.execute(text(
            "SELECT claimed_by, COUNT(*) n FROM dispatch_tasks "
            "WHERE finished_at >= :ds AND status IN ('done','failed') GROUP BY claimed_by"
        ), {"ds": day_start_naive}).all()
        done_today = {r.claimed_by: r.n for r in done_rows}
        inflight_rows = s.execute(text(
            "SELECT claimed_by, COUNT(*) n FROM dispatch_tasks "
            "WHERE status='claimed' GROUP BY claimed_by"
        )).all()
        inflight = {r.claimed_by: r.n for r in inflight_rows}
        out = []
        for w in workers:
            online = bool(w.last_heartbeat_at and w.last_heartbeat_at >= cutoff)
            try:
                meta = json.loads(w.meta_json or "{}")
            except Exception:  # noqa: BLE001
                meta = {}
            out.append({
                "id": w.id, "worker_uid": w.worker_uid, "hostname": w.hostname,
                "label": w.label, "region": w.region, "exit_ip": w.exit_ip,
                "engines": json.loads(w.engines_json or "[]"),
                "max_concurrency": w.max_concurrency,
                "status": "online" if online else "offline",
                "raw_status": w.status, "enabled": bool(w.enabled),
                "version": w.version,
                "last_heartbeat_at": w.last_heartbeat_at.isoformat() if w.last_heartbeat_at else None,
                "done_today": done_today.get(w.worker_uid, 0),
                "in_flight": inflight.get(w.worker_uid, 0),
                "breaker": meta.get("breaker", {}),
            })
    return out


@router.get("/queue/stats")
def queue_stats() -> dict:
    day_start = _day_start_utc(datetime.now(timezone.utc))
    with db_session() as s:
        by = s.execute(text(
            "SELECT engine, status, COUNT(*) n FROM dispatch_tasks GROUP BY engine, status"
        )).all()
        queue: dict[str, dict] = {}
        for r in by:
            queue.setdefault(r.engine, {})[r.status] = r.n
        used_rows = s.execute(text(
            "SELECT engine, COUNT(*) n FROM ai_telemetry_responses "
            "WHERE created_at >= :ds GROUP BY engine"
        ), {"ds": day_start}).all()
        used_today = {r.engine: r.n for r in used_rows}
    caps = {e: (None if _engine_daily_cap(e) == float("inf") else int(_engine_daily_cap(e)))
            for e in set(list(queue.keys()) + list(used_today.keys()))}
    return {"queue": queue, "used_today": used_today, "daily_cap": caps}


class WorkerActionResult(BaseModel):
    ok: bool
    status: str


@router.post("/workers/{worker_uid}/{action}", response_model=WorkerActionResult)
def worker_action(worker_uid: str, action: str) -> WorkerActionResult:
    if action not in ("enable", "disable", "drain"):
        raise HTTPException(400, "action must be enable|disable|drain")
    with db_session() as s:
        w = s.query(WorkerORM).filter(WorkerORM.worker_uid == worker_uid).one_or_none()
        if w is None:
            raise HTTPException(404, "worker not found")
        if action == "enable":
            w.enabled = True
            w.status = "online" if w.last_heartbeat_at else "offline"
        elif action == "disable":
            w.enabled = False
            w.status = "disabled"
        else:  # drain：保留 enabled,但停止派活,跑完在飞行的
            w.status = "draining"
        status = w.status
    return WorkerActionResult(ok=True, status=status)
