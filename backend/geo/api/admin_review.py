"""Admin 审核 — Phase C 接入真实数据 + approve/reject 端点.

待审核数据来源 = Topic.seed_prompts_json 和 Topic.queries_json 里 status=pending 的项.
审核通过后写 approved_at + reviewer_id;拒绝则写 rejected_at.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.auth import require_admin
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.models.user import UserORM

router = APIRouter(prefix="/admin/review")


# ─────────────────── 列出 pending ───────────────────


class PendingSeedItem(BaseModel):
    topic_id: int
    topic_name: str
    target: str                           # 该 topic 的检测词,审核时帮 admin 看清上下文
    user_id: int
    user_email: str                       # 提交人邮箱
    idx: int                              # 在 seed_prompts_json 里的下标
    text: str
    submitted_at: Optional[str] = None


class PendingQueryItem(BaseModel):
    topic_id: int
    topic_name: str
    target: str
    user_id: int
    user_email: str
    idx: int                              # 在 queries_json 里的下标
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
    # 跨用户全表扫(admin 视角)— 同步取一次 user.email 字典,避免 N+1
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


# ─────────────────── approve / reject ───────────────────


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
