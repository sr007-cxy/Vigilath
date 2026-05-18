"""Engine session pool — upload (harvester) + check-out/check-in (browser-service).

三个角色:
  1. **harvester** (scripts/harvest_sessions.py 用户机器上跑):
       POST /api/engine-sessions/upload  +  header X-Harvest-Token
  2. **browser-service** (vm03 跑 search 前 fetch session):
       POST /api/engine-sessions/check-out  +  header X-Service-Token
       POST /api/engine-sessions/check-in   +  header X-Service-Token
  3. **dashboard / 运维** (查池子健康):
       GET  /api/engine-sessions/pool-status  (无 auth,只读统计)

环境变量(deploy 时配):
  ENGINE_SESSION_HARVEST_TOKEN  — 给所有用户分发,upload 鉴权
  ENGINE_SESSION_SERVICE_TOKEN  — backend ↔ browser-service 之间的内部 token
  ENGINE_SESSION_VALID_ENGINES  — 逗号分隔,默认 doubao,qwen,deepseek,wenxin,yuanbao
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.engine_sessions import (
    EngineSessionORM,
    FailureType,
    PoolStatusEntry,
    SessionCheckIn,
    SessionCheckedOut,
    SessionUploadIn,
)


router = APIRouter()


# ── DB session helper ───────────────────────────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Auth helpers ────────────────────────────────────────────────────


def _require_harvest_token(x_harvest_token: Optional[str] = Header(None)) -> None:
    expected = (os.environ.get("ENGINE_SESSION_HARVEST_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "harvest endpoint disabled (server token not configured)")
    if not x_harvest_token or x_harvest_token != expected:
        raise HTTPException(401, "invalid X-Harvest-Token")


def _require_service_token(x_service_token: Optional[str] = Header(None)) -> None:
    expected = (os.environ.get("ENGINE_SESSION_SERVICE_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "service endpoints disabled (server token not configured)")
    if not x_service_token or x_service_token != expected:
        raise HTTPException(401, "invalid X-Service-Token")


# ── Config ──────────────────────────────────────────────────────────


def _valid_engines() -> set[str]:
    raw = os.environ.get("ENGINE_SESSION_VALID_ENGINES") or "doubao,qwen,deepseek,wenxin,yuanbao"
    return {e.strip() for e in raw.split(",") if e.strip()}


_CAPTCHA_QUARANTINE_THRESHOLD = 3   # 累计被挑 CAPTCHA 3 次 → quarantine

# ── Quarantine policy(D2 — P1 失败信号 enum 化)──────────────────
#
# 各 FailureType 触发 quarantine 的政策:
#   - threshold: 累计达 N 次 quarantine
#   - immediate: 单次就 quarantine(像 login_lost 这种不可逆 fail)
#   - skip: 不计入 session 账,通常是 worker 端问题(crash)
#
# policy=None 表示 SUCCESS,重置所有 fail_counts(防止旧失败累积到永远)。
_QUARANTINE_POLICY: dict[FailureType, dict] = {
    FailureType.SUCCESS:        {"reset": True},
    FailureType.CAPTCHA:        {"threshold": 3, "legacy_col": "captcha_count"},
    FailureType.LOGIN_LOST:     {"immediate": True},
    FailureType.EMPTY_ANSWER:   {"threshold": 5},
    FailureType.DOM_NOT_FOUND:  {"threshold": 3, "alert": True},  # alert: 可能引擎改版
    FailureType.TIMEOUT:        {"threshold": 5},
    FailureType.CRASH:          {"skip": True},
}


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/engine-sessions/upload", status_code=201)
def upload_session(
    payload: SessionUploadIn,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_harvest_token),
):
    """Harvester 上传一份新登录拿到的 storage_state。

    简单 sanity check + 落库,不去 dedupe(同 source 多次上传也允许 ——
    后传的更新鲜,check-out 时 use_count=0 + 最新 captured_at 会被优先选)。
    """
    if payload.engine not in _valid_engines():
        raise HTTPException(400, f"unsupported engine: {payload.engine!r}")
    cookies = payload.storage_state.get("cookies") or []
    if not cookies:
        raise HTTPException(400, "storage_state has no cookies — likely not logged in")

    row = EngineSessionORM(
        engine=payload.engine,
        source_label=payload.source_label,
        storage_state=json.dumps(payload.storage_state, ensure_ascii=False),
        user_agent=payload.user_agent,
        platform=payload.platform,
        captured_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=payload.ttl_days),
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "engine": row.engine, "expires_at": row.expires_at.isoformat()}


@router.post("/engine-sessions/check-out", response_model=SessionCheckedOut)
def check_out(
    engine: str,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """browser-service 拿一条最少被用的 active session。

    选择策略:active + captcha_count < 阈值 + 未过期,按
    (use_count ASC, last_used_at ASC NULLS FIRST) 排序 — 没被用过的优先,
    其次最久没被用的。
    """
    if engine not in _valid_engines():
        raise HTTPException(400, f"unsupported engine: {engine!r}")

    now = datetime.utcnow()
    q = (
        db.query(EngineSessionORM)
        .filter(EngineSessionORM.engine == engine)
        .filter(EngineSessionORM.status == "active")
        .filter(EngineSessionORM.captcha_count < _CAPTCHA_QUARANTINE_THRESHOLD)
        .filter((EngineSessionORM.expires_at == None) | (EngineSessionORM.expires_at > now))  # noqa: E711
        .order_by(
            EngineSessionORM.use_count.asc(),
            EngineSessionORM.last_used_at.asc().nullsfirst(),
        )
    )
    row = q.first()
    if row is None:
        raise HTTPException(404, f"no active session available for engine={engine}")

    # 即刻 +1 use_count + last_used_at,避免并发 check-out 同一条
    row.use_count = (row.use_count or 0) + 1
    row.last_used_at = now
    db.commit()
    db.refresh(row)

    return SessionCheckedOut(
        id=row.id,
        engine=row.engine,
        source_label=row.source_label,
        storage_state=json.loads(row.storage_state),
        use_count=row.use_count,
    )


@router.post("/engine-sessions/check-in")
def check_in(
    payload: SessionCheckIn,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """browser-service 报告 session 用完 + 本次 FailureType。

    SUCCESS  → 重置 fail_counts(防止历史失败永远累积)
    CAPTCHA  → captcha_count++,阈值 3 → quarantine
    LOGIN_LOST → 立即 quarantine(session 已失效,不留)
    EMPTY_ANSWER / TIMEOUT → fail_counts[type]++,阈值 5 → quarantine
    DOM_NOT_FOUND → fail_counts[type]++,阈值 3 → quarantine + alert
    CRASH    → skip(不计 session 账,认为是 worker 端问题)

    Backward-compat:旧 worker 仍发 captcha_triggered=true/false,SessionCheckIn
    的 validator 已经把它翻译成 result=CAPTCHA/SUCCESS,这里只看 payload.result.
    """
    row = db.query(EngineSessionORM).get(payload.id)
    if row is None:
        raise HTTPException(404, f"session id={payload.id} not found")

    result = payload.result
    policy = _QUARANTINE_POLICY.get(result, {})

    # 解析现有 fail_counts(SQLite 存 JSON string)
    try:
        fc = json.loads(row.fail_counts_json or "{}")
    except json.JSONDecodeError:
        fc = {}

    if policy.get("reset"):
        # SUCCESS:清空累积失败,但保留 captcha_count(captcha 是独立 counter)
        fc = {}
    elif policy.get("skip"):
        # CRASH:认为是 worker 端问题,不动 session 账
        pass
    elif policy.get("immediate"):
        # LOGIN_LOST:立即 quarantine
        row.status = "quarantined"
        fc[result.value] = fc.get(result.value, 0) + 1
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
    else:
        # 累计型:计数 + 达阈值 quarantine
        fc[result.value] = fc.get(result.value, 0) + 1
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
        # captcha 用历史独立列,其他用 fail_counts_json
        if policy.get("legacy_col") == "captcha_count":
            row.captcha_count = (row.captcha_count or 0) + 1
            count_for_threshold = row.captcha_count
        else:
            count_for_threshold = fc[result.value]
        threshold = policy.get("threshold")
        if threshold is not None and count_for_threshold >= threshold:
            row.status = "quarantined"

    row.fail_counts_json = json.dumps(fc)

    # 释放 lease(P3 D4 才用,但 D2 顺手清,避免下次 check-out 看到 stale lease)
    row.leased_by_worker_id = None
    row.leased_until = None

    db.commit()
    return {
        "id": row.id,
        "status": row.status,
        "use_count": row.use_count,
        "captcha_count": row.captcha_count,
        "fail_counts": fc,
        "last_fail_type": row.last_fail_type,
    }


@router.get("/engine-sessions/pool-status", response_model=List[PoolStatusEntry])
def pool_status(db: Session = Depends(get_db)):
    """Dashboard 用:每个 engine 当前 active / quarantined / expired 各几条。"""
    now = datetime.utcnow()
    out: List[PoolStatusEntry] = []
    for engine in sorted(_valid_engines()):
        rows = db.query(EngineSessionORM).filter(EngineSessionORM.engine == engine).all()
        active = sum(1 for r in rows if r.status == "active" and (r.expires_at is None or r.expires_at > now))
        quarantined = sum(1 for r in rows if r.status == "quarantined")
        expired = sum(1 for r in rows if r.expires_at is not None and r.expires_at <= now)
        oldest_active = (
            db.query(func.min(EngineSessionORM.captured_at))
            .filter(EngineSessionORM.engine == engine)
            .filter(EngineSessionORM.status == "active")
            .scalar()
        )
        out.append(PoolStatusEntry(
            engine=engine,
            active=active,
            quarantined=quarantined,
            expired=expired,
            oldest_active_captured_at=oldest_active,
        ))
    return out
