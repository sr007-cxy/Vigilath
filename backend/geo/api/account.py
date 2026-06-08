"""Personal center (/account) endpoints.

Exposes per-user data the `/account` UI needs beyond the existing auth and
membership routes: detection history (list/detail/delete) and password
change. Everything here requires a valid Bearer token — `get_current_user`
returns the `UserORM` row for the caller.
"""
import json
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from geo.api.auth import get_current_user
from geo.database import SessionLocal
from geo.models.detection import (
    DetectionRecordDetail,
    DetectionRecordList,
    DetectionRecordSummary,
)
from geo.models.payment import PaymentSessionORM
from geo.models.membership import MembershipORM
from geo.models.user import UserORM
from geo.services.detection_service import (
    delete_detection,
    get_detection,
    list_detections,
)
from geo.utils.error_handler import AppException


router = APIRouter()


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


@router.get("/detections", response_model=DetectionRecordList)
async def list_user_detections(
    page: int = 1,
    size: int = 10,
    current_user: UserORM = Depends(get_current_user),
):
    rows, total = list_detections(user_id=current_user.id, page=page, size=size)
    items = [DetectionRecordSummary.model_validate(r) for r in rows]
    return DetectionRecordList(items=items, total=total, page=page, size=size)


@router.get("/detections/{record_id}", response_model=DetectionRecordDetail)
async def get_user_detection(
    record_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    row = get_detection(user_id=current_user.id, record_id=record_id)
    if row is None:
        raise AppException(status_code=404, message="Detection record not found")
    try:
        snapshot = json.loads(row.result_snapshot) if row.result_snapshot else {}
    except json.JSONDecodeError:
        snapshot = {}
    return DetectionRecordDetail(
        id=row.id,
        url=row.url,
        score=row.score,
        grade=row.grade,
        tier=row.tier,
        mode=row.mode,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        result=snapshot,
    )


@router.delete("/detections/{record_id}")
async def delete_user_detection(
    record_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    ok = delete_detection(user_id=current_user.id, record_id=record_id)
    if not ok:
        raise AppException(status_code=404, message="Detection record not found")
    return {"success": True}


class PaymentRecordItem(BaseModel):
    id: int
    membership_slug: str
    amount_cents: int
    currency: str
    status: str
    provider: str = "stripe"
    checkout_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class PaymentRecordList(BaseModel):
    items: list[PaymentRecordItem]
    total: int
    page: int
    size: int


@router.get("/payments", response_model=PaymentRecordList)
async def list_user_payments(
    page: int = 1,
    size: int = 10,
    current_user: UserORM = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        q = (
            db.query(PaymentSessionORM, MembershipORM.slug)
            .outerjoin(MembershipORM, PaymentSessionORM.membership_id == MembershipORM.id)
            .filter(PaymentSessionORM.user_id == current_user.id)
            .order_by(PaymentSessionORM.created_at.desc())
        )
        total = q.count()
        rows = q.offset((page - 1) * size).limit(size).all()
        items = [
            PaymentRecordItem(
                id=ps.id,
                membership_slug=slug or "",
                amount_cents=ps.amount_cents,
                currency=ps.currency,
                status=ps.status,
                provider=ps.provider or "stripe",
                checkout_url=ps.checkout_url if ps.status == "pending" and (ps.provider or "stripe") == "stripe" else None,
                created_at=str(ps.created_at),
                completed_at=str(ps.completed_at) if ps.completed_at else None,
            )
            for ps, slug in rows
        ]
        return PaymentRecordList(items=items, total=total, page=page, size=size)
    finally:
        db.close()


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: UserORM = Depends(get_current_user),
):
    # Truncate to bcrypt's 72-byte limit, matching user_service.create_user.
    old_pw = payload.old_password[:72]
    new_pw = payload.new_password[:72]

    if not bcrypt.checkpw(old_pw.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        raise AppException(status_code=400, message="Current password is incorrect")

    new_hash = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db = SessionLocal()
    try:
        row = db.query(UserORM).filter(UserORM.id == current_user.id).first()
        if row is None:
            raise AppException(status_code=404, message="User not found")
        row.password_hash = new_hash
        db.commit()
    finally:
        db.close()

    return {"success": True}


# ── 对外开放:账号自助 embed token(给外部 agent / 小龙虾链接本账号)──────────
# 签发模块 geo.agent.embed.tokens 不依赖 pydantic-ai,可在主后端安全调用;
# 签名密钥与 agent service 共用(同 .env),故主后端签的 token 在 :8010 校验通过。

class IssueTokenRequest(BaseModel):
    label: str = ""
    days: int = 365


@router.get("/agent-tokens")
async def list_agent_tokens(current_user: UserORM = Depends(get_current_user)):
    """列出本账号的 embed token(不返回明文,只元数据)。"""
    from geo.models.agent import AgentTokenORM

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTokenORM)
            .filter(AgentTokenORM.account_id == current_user.id)
            .order_by(AgentTokenORM.id.desc())
            .all()
        )
        return {
            "tokens": [
                {
                    "tid": r.tid,
                    "caps": r.caps,
                    "label": r.label,
                    "enabled": r.enabled == 1,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }
    finally:
        db.close()


@router.post("/agent-token")
async def issue_agent_token(
    payload: IssueTokenRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """自助领号:为当前账号签发 1 年期 embed token(明文只此一次返回)。

    能力一律全开(读 + 诊断/产稿);真实对外发布不在 token 能力内,由平台护栏控制(can_publish 恒 False)。
    """
    from geo.agent.embed.tokens import issue_token

    caps = ["read", "write"]
    days = max(1, min(int(payload.days or 365), 366))
    db = SessionLocal()
    try:
        token, tid = issue_token(
            db, current_user.id, caps=caps, label=(payload.label or "")[:255], ttl_days=days,
        )
        return {"token": token, "tid": tid, "caps": caps, "days": days, "can_publish": False}
    finally:
        db.close()


@router.post("/agent-token/{tid}/revoke")
async def revoke_agent_token(tid: str, current_user: UserORM = Depends(get_current_user)):
    """吊销本账号的某个 token(只能吊销自己的)。"""
    from geo.models.agent import AgentTokenORM

    db = SessionLocal()
    try:
        row = (
            db.query(AgentTokenORM)
            .filter(AgentTokenORM.tid == tid, AgentTokenORM.account_id == current_user.id)
            .first()
        )
        if row is None:
            raise AppException(status_code=404, message="token not found")
        row.enabled = 0
        db.commit()
        return {"success": True, "tid": tid}
    finally:
        db.close()
