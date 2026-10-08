"""Call policies and call records (admin-only): data and config endpoints for the admin "call management" page.

Configure everything in one place: internal (web / ops console / platform automation) and external (open API / embed tokens / IM / Skill)
are tiered in the same call_policies table and traced in the same call_audit table.

Endpoints (prefix /admin/call-policies):
- GET  /                    tier list (seven default tiers created lazily on first access)
- POST /                    create a custom tier
- PUT  /{key}               edit a tier (cache invalidated immediately, no waiting for the 60s TTL)
- POST /bind                bind an external token / gateway tenant to a tier
- GET  /audit               unified call records (internal + external), filterable by channel/tier/account
- GET  /audit/summary       summary by channel (today / last 7 days), showing who is calling and how much
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_admin
from geo.models.agent import AgentTokenORM
from geo.models.call_policy import (
    ALL_CHANNELS,
    ALL_EXEC_MODES,
    ALL_RUN_MODES,
    CallAuditORM,
    CallPolicyORM,
)
from geo.services.call_policy import ensure_seed, invalidate_cache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/call-policies", dependencies=[Depends(require_admin)])

_INTERNAL = ("web", "admin", "cron")


class PolicyIn(BaseModel):
    key: str | None = None
    label: str | None = None
    channel: str | None = None
    allow_actions: str | None = None
    deny_actions: str | None = None
    exec_mode: str | None = None      # auto=browser automation | api=direct API | any=unrestricted
    run_mode: str | None = None       # immediate=execute now | queue=queue via scheduling | any=unrestricted
    engines: str | None = None
    daily_runs: int | None = None
    rpm: int | None = None
    max_concurrent: int | None = None
    priority: int | None = None
    unlimited: int | None = None
    enabled: int | None = None
    note: str | None = None


class BindIn(BaseModel):
    policy_key: str
    tid: str | None = None            # external agent token
    tenant_id: int | None = None      # open API gateway tenant


def _row_out(r: CallPolicyORM) -> dict:
    return {
        "key": r.key, "label": r.label, "channel": r.channel,
        "allow_actions": r.allow_actions, "deny_actions": r.deny_actions,
        "exec_mode": r.exec_mode, "run_mode": r.run_mode, "engines": r.engines,
        "daily_runs": r.daily_runs, "rpm": r.rpm, "max_concurrent": r.max_concurrent,
        "priority": r.priority, "unlimited": r.unlimited, "enabled": r.enabled,
        "note": r.note, "is_internal": r.channel in _INTERNAL,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _validate(payload: PolicyIn) -> None:
    if payload.channel and payload.channel not in ALL_CHANNELS:
        raise HTTPException(400, f"通道只能是:{'/'.join(ALL_CHANNELS)}")
    if payload.exec_mode and payload.exec_mode not in ALL_EXEC_MODES:
        raise HTTPException(400, f"执行路径只能是:{'/'.join(ALL_EXEC_MODES)}")
    if payload.run_mode and payload.run_mode not in ALL_RUN_MODES:
        raise HTTPException(400, f"执行时机只能是:{'/'.join(ALL_RUN_MODES)}")


@router.get("")
def list_policies(db: Session = Depends(get_db)) -> dict:
    ensure_seed(db)          # Lazily create default tiers: covers new and old environments uniformly, no data migration
    rows = db.query(CallPolicyORM).order_by(CallPolicyORM.channel, CallPolicyORM.key).all()
    return {"policies": [_row_out(r) for r in rows],
            "channels": list(ALL_CHANNELS),
            "exec_modes": list(ALL_EXEC_MODES),
            "run_modes": list(ALL_RUN_MODES)}


@router.post("")
def create_policy(payload: PolicyIn, db: Session = Depends(get_db),
                  admin=Depends(require_admin)) -> dict:
    if not payload.key:
        raise HTTPException(400, "key 必填")
    _validate(payload)
    if db.query(CallPolicyORM).filter(CallPolicyORM.key == payload.key).first():
        raise HTTPException(409, "该档已存在")
    now = datetime.utcnow()
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    row = CallPolicyORM(created_at=now, updated_at=now, updated_by_id=admin.id, **data)
    db.add(row)
    db.commit()
    invalidate_cache(row.key)
    return _row_out(row)


@router.put("/{key}")
def update_policy(key: str, payload: PolicyIn, db: Session = Depends(get_db),
                  admin=Depends(require_admin)) -> dict:
    row = db.query(CallPolicyORM).filter(CallPolicyORM.key == key).first()
    if row is None:
        raise HTTPException(404, "档位不存在")
    _validate(payload)
    for k, v in payload.model_dump().items():
        if k == "key" or v is None:
            continue
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    row.updated_by_id = admin.id
    db.commit()
    invalidate_cache(key)     # Takes effect immediately, without waiting for the 60s cache to expire
    return _row_out(row)


@router.post("/bind")
def bind_policy(payload: BindIn, db: Session = Depends(get_db)) -> dict:
    """Bind an external token / gateway tenant to a tier (overriding the channel default tier). Empty policy_key = unbind."""
    key = (payload.policy_key or "").strip()
    if key and not db.query(CallPolicyORM).filter(CallPolicyORM.key == key).first():
        raise HTTPException(404, "档位不存在")
    if payload.tid:
        row = db.query(AgentTokenORM).filter(AgentTokenORM.tid == payload.tid).first()
        if row is None:
            raise HTTPException(404, "token 不存在")
        row.policy_key = key
        db.commit()
        return {"bound": "token", "tid": payload.tid, "policy_key": key}
    if payload.tenant_id is not None:
        # api_tenants' ORM is declared by telemetry-service; use SQL directly here to avoid a cross-service import
        from sqlalchemy import text
        n = db.execute(text("UPDATE api_tenants SET policy_key = :k WHERE id = :i"),
                       {"k": key, "i": payload.tenant_id}).rowcount
        db.commit()
        if not n:
            raise HTTPException(404, "租户不存在")
        return {"bound": "tenant", "tenant_id": payload.tenant_id, "policy_key": key}
    raise HTTPException(400, "要么给 tid,要么给 tenant_id")


@router.get("/audit")
def list_audit(
    channel: str | None = Query(default=None),
    policy_key: str | None = Query(default=None),
    account_id: int | None = Query(default=None),
    decision: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    q = db.query(CallAuditORM).filter(CallAuditORM.ts >= since)
    if channel:
        q = q.filter(CallAuditORM.channel == channel)
    if policy_key:
        q = q.filter(CallAuditORM.policy_key == policy_key)
    if account_id is not None:
        q = q.filter(CallAuditORM.account_id == account_id)
    if decision:
        q = q.filter(CallAuditORM.decision == decision)
    total = q.count()
    rows = q.order_by(CallAuditORM.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": r.id, "ts": r.ts.isoformat() if r.ts else None, "bj_date": r.bj_date,
            "channel": r.channel, "policy_key": r.policy_key, "tenant_id": r.tenant_id,
            "tid": r.tid, "account_id": r.account_id, "endpoint": r.endpoint,
            "action": r.action, "topic_id": r.topic_id, "engine": r.engine,
            "exec_mode": r.exec_mode, "run_mode": r.run_mode,
            "decision": r.decision, "reason": r.reason, "cells": r.cells,
        } for r in rows],
    }


@router.get("/audit/summary")
def audit_summary(days: int = Query(default=7, ge=1, le=90),
                  db: Session = Depends(get_db)) -> dict:
    """Summary by channel: call count, rejection count, and the questions x engines scale triggered."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(
                CallAuditORM.channel,
                func.count(CallAuditORM.id).label("n"),
                func.sum(CallAuditORM.cells).label("cells"),
                func.sum(case((CallAuditORM.decision == "deny", 1), else_=0)).label("denied"),
            )
            .filter(CallAuditORM.ts >= since)
            .group_by(CallAuditORM.channel).all())
    return {"days": days, "by_channel": [
        {"channel": r.channel, "calls": int(r.n or 0),
         "cells": int(r.cells or 0), "denied": int(r.denied or 0)} for r in rows]}
