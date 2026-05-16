"""Phase D — Admin 内容审核 API.

文档生命周期:draft → pending_review → approved → published(或 rejected)

端点:
- GET    /admin/content-review/topics                — 列出有 docs 的 (account, topic) 二元组
- GET    /admin/content-review/topics/{topic_id}/docs?status=draft|all
- POST   /admin/content-review/docs/select           — 把勾选的 doc 切到 pending_review
- POST   /admin/content-review/docs/{id}/approve     — 单条通过
- POST   /admin/content-review/docs/{id}/reject      — 单条拒绝 + reason + 邮件
- POST   /admin/content-review/docs/{id}/publish     — 标记发布平台/媒体 + 邮件
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.api.auth import require_admin
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, GeneratedDocOut, PublishPayload, RejectDocPayload,
    SelectDocsPayload, TopicGeneratedDocORM,
)
from geo.models.user import UserORM

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/content-review")


class TopicWithDocsItem(BaseModel):
    topic_id: int
    topic_name: str
    user_id: int
    user_email: str
    doc_count: int
    draft_count: int
    pending_review_count: int
    approved_count: int
    published_count: int
    rejected_count: int


@router.get("/topics", response_model=list[TopicWithDocsItem])
def list_topics_with_docs(
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """列出有内容文档的 topic(给前端下拉用)."""
    # 聚合 doc 数量
    docs = db.query(TopicGeneratedDocORM).all()
    if not docs:
        return []
    by_topic: dict[int, dict[str, int]] = {}
    for d in docs:
        b = by_topic.setdefault(d.topic_id, {
            "doc_count": 0, "draft": 0, "pending_review": 0,
            "approved": 0, "published": 0, "rejected": 0,
        })
        b["doc_count"] += 1
        b[d.status] = b.get(d.status, 0) + 1
    topic_ids = list(by_topic.keys())
    topics = {t.id: t for t in db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.id.in_(topic_ids)).all()}
    user_ids = {t.user_id for t in topics.values()}
    emails: dict[int, str] = {}
    if user_ids:
        for u in db.query(UserORM).filter(UserORM.id.in_(user_ids)).all():
            emails[u.id] = u.email
    out: list[TopicWithDocsItem] = []
    for tid, b in by_topic.items():
        t = topics.get(tid)
        if not t:
            continue
        out.append(TopicWithDocsItem(
            topic_id=tid, topic_name=t.name,
            user_id=t.user_id, user_email=emails.get(t.user_id, ""),
            doc_count=b["doc_count"],
            draft_count=b.get("draft", 0),
            pending_review_count=b.get("pending_review", 0),
            approved_count=b.get("approved", 0),
            published_count=b.get("published", 0),
            rejected_count=b.get("rejected", 0),
        ))
    out.sort(key=lambda x: (-x.pending_review_count, -x.draft_count, x.topic_name))
    return out


@router.get("/topics/{topic_id}/docs", response_model=list[GeneratedDocOut])
def list_topic_docs(
    topic_id: int,
    status: Optional[str] = None,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """列出某 topic 下的文档.

    status 过滤:
      - 不传:全部
      - "draft" / "pending_review" / "approved" / "rejected" / "published":精确匹配
      - "to_review":draft + pending_review(常用过滤,默认 admin 视角想看的)
    """
    q = db.query(TopicGeneratedDocORM).filter(TopicGeneratedDocORM.topic_id == topic_id)
    if status == "to_review":
        q = q.filter(TopicGeneratedDocORM.status.in_(["draft", "pending_review"]))
    elif status:
        q = q.filter(TopicGeneratedDocORM.status == status)
    rows = q.order_by(TopicGeneratedDocORM.id.desc()).all()
    return [GeneratedDocOut.from_orm_row(r) for r in rows]


@router.get("/docs/{doc_id}", response_model=GeneratedDocOut)
def get_doc(
    doc_id: int,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    return GeneratedDocOut.from_orm_row(d)


@router.post("/docs/select", status_code=204)
def select_docs_for_review(
    payload: SelectDocsPayload,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """把勾选的 draft docs 切到 pending_review."""
    rows = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.id.in_(payload.doc_ids))
          .all()
    )
    n = 0
    for d in rows:
        if d.status == "draft":
            d.status = "pending_review"
            d.selected_for_review = True
            n += 1
    db.commit()
    log.info("content review: selected %d docs to pending_review", n)


@router.post("/docs/{doc_id}/approve", response_model=GeneratedDocOut)
def approve_doc(
    doc_id: int,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},不可通过"})
    d.status = "approved"
    d.selected_for_review = True
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = admin.id
    d.reject_reason = None
    db.commit()
    db.refresh(d)
    _notify_user(db, background, d, decision="approved")
    return GeneratedDocOut.from_orm_row(d)


@router.post("/docs/{doc_id}/reject", response_model=GeneratedDocOut)
def reject_doc(
    doc_id: int,
    payload: RejectDocPayload,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status not in ("draft", "pending_review"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"doc status={d.status},不可拒绝"})
    d.status = "rejected"
    d.selected_for_review = True
    d.review_decision_at = datetime.utcnow()
    d.reviewer_id = admin.id
    d.reject_reason = payload.reason or None
    db.commit()
    db.refresh(d)
    _notify_user(db, background, d, decision="rejected", reject_reason=payload.reason or None)
    return GeneratedDocOut.from_orm_row(d)


@router.post("/docs/{doc_id}/publish", response_model=GeneratedDocOut)
def publish_doc(
    doc_id: int,
    payload: PublishPayload,
    background: BackgroundTasks,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """标记发布目标 — 只落 DB,不调外部平台 OpenAPI."""
    d = db.get(TopicGeneratedDocORM, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    if d.status != "approved":
        raise HTTPException(409, {"code": "NEED_APPROVED",
                                  "message": "请先通过审核再选发布平台"})
    try:
        targets = json.loads(d.publish_targets_json or "[]")
    except Exception:  # noqa: BLE001
        targets = []
    if not isinstance(targets, list):
        targets = []
    now_iso = datetime.utcnow().isoformat()
    new_targets = [
        {
            "platform": t.platform,
            "media": t.media,
            "marked_at": now_iso,
            "marked_by": admin.id,
        }
        for t in payload.publish_targets
    ]
    targets.extend(new_targets)
    d.publish_targets_json = json.dumps(targets, ensure_ascii=False)
    d.status = "published"
    db.commit()
    db.refresh(d)
    _notify_user(db, background, d, decision="published", publish_targets=new_targets)
    return GeneratedDocOut.from_orm_row(d)


def _notify_user(db: Session, background: BackgroundTasks, doc: TopicGeneratedDocORM,
                  *, decision: str, reject_reason: str | None = None,
                  publish_targets: list[dict] | None = None) -> None:
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if not topic:
        return
    user = db.get(UserORM, topic.user_id)
    if not user or not user.email:
        return
    background.add_task(_send_safe,
                        to=user.email, doc_title=doc.title or doc.source_query_text or f"#{doc.id}",
                        decision=decision, reject_reason=reject_reason,
                        publish_targets=publish_targets)


def _send_safe(*, to: str, doc_title: str, decision: str,
                reject_reason: str | None, publish_targets: list[dict] | None) -> None:
    try:
        from geo.services.email_service import email_service
        email_service.send_content_review_result_email(
            to=to, doc_title=doc_title, decision=decision,
            reject_reason=reject_reason, publish_targets=publish_targets,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("content review email failed: %s", e)
