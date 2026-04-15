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
