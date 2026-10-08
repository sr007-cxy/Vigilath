"""Asset library management (2026-08-18): overview of images/videos under the workspace "content & publishing" group.

Before this, assets could only be viewed per topic in the article editor's upload panel: no cross-topic view, no way to tell AI-generated from user
uploads, and content markings and external moderation conclusions were not shown at all. The image side of filing remediation list item 9 (third-party review interface)
and item 10 (manual review, for both text and images) requires exactly such a view.

Endpoints:
- GET    /admin/media                  - cross-topic list (filter by topic/source/risk/marking, paginated)
- GET    /admin/media/stats            - top counters (total, AI-generated, missing marking, risky)
- POST   /admin/media/{id}/rescan      - resubmit for external moderation (overwrites the old conclusion)
- POST   /admin/media/{id}/remark      - reapply content marking (remedy legacy images missing markings)
- DELETE /admin/media/{id}             - delete the asset (including the file)

Permissions follow the workspace require_workbench, at the same level as the content review console and generation provenance.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.api.auth import require_workbench
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicMediaORM
from geo.models.user import UserORM
from geo.services import image_marking, media_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/media")


class MediaOut(BaseModel):
    id: int
    topic_id: int
    topic_name: str = ""
    owner_email: str = ""
    filename: str = ""
    kind: str = "image"
    mime: str = ""
    size: int = 0
    uploaded_at: Optional[datetime] = None
    # Public preview URL: the same URL embedded in the body
    url: str = ""
    # Whether the file is on **this machine**. The test site and production share appdb but each has its own disk, so row present but file absent is normal;
    # the list must distinguish "the image has a problem" from "the image is on the other machine" (one stat, no content read)
    file_present: bool = True
    # Notes written for the asset by the user/admin
    description: str = ""
    useCount: int = 0
    # ─── Source and content marking ───
    is_ai_generated: bool = False
    gen_model: str = ""
    gen_trace_no: str = ""
    has_implicit_mark: bool = False          # Metadata can be read back from the file
    has_explicit_mark: bool = False          # A visible watermark is drawn on the image
    meta: dict[str, str] = Field(default_factory=dict)
    # ─── External moderation soft signals ───
    external_status: str = ""                # ok / degraded / skipped / "" (not checked)
    external_risk: str = ""                  # high / medium / low / "" (no risk)
    external_labels: list[str] = Field(default_factory=list)
    external_descriptions: list[str] = Field(default_factory=list)


class MediaListOut(BaseModel):
    total: int
    items: list[MediaOut]


class MediaStatsOut(BaseModel):
    total: int = 0
    ai_generated: int = 0
    missing_mark: int = 0        # AI-generated but no content number: marking missing, needs fixing
    risky: int = 0               # External moderation judged high/medium
    unscanned: int = 0           # Images not yet sent for external moderation


def _serialize(m: TopicMediaORM, *, topic_name: str = "", owner_email: str = "",
               with_meta: bool = False) -> MediaOut:
    ext = {}
    try:
        ext = json.loads(m.external_json or "{}")
    except Exception:  # noqa: BLE001
        ext = {}
    meta: dict[str, str] = {}
    f = media_storage.media_file(m)
    present = bool(f and f.is_file())
    if with_meta and (m.kind or "image") == "image" and present:
        try:
            meta = image_marking.read_marks(f) or {}
        except Exception:  # noqa: BLE001
            meta = {}
    return MediaOut(
        id=m.id, topic_id=m.topic_id, topic_name=topic_name, owner_email=owner_email,
        filename=m.filename or "", kind=m.kind or "image", mime=m.mime or "",
        size=m.size or 0, uploaded_at=m.uploaded_at, url=media_storage.public_url(m),
        file_present=present, description=m.description or "",
        useCount=int(getattr(m, "use_count", 0) or 0),
        is_ai_generated=bool(m.is_ai_generated), gen_model=m.gen_model or "",
        gen_trace_no=m.gen_trace_no or "",
        has_implicit_mark=bool(meta) if with_meta else bool(m.gen_trace_no),
        # Explicit marking = a visible watermark was drawn when the image was produced. The watermark switch flips back and forth, so only the record in the file counts;
        # legacy images before 2026-08-18 lack this key and are all treated as "not drawn" (it was indeed off by default)
        has_explicit_mark=(meta.get("VisibleWatermark") == "on") if with_meta else False,
        meta={k: str(v) for k, v in (meta or {}).items()},
        external_status=str(ext.get("status") or ""),
        external_risk=str(ext.get("risk_level") or ""),
        external_labels=[str(x) for x in (ext.get("labels") or [])],
        external_descriptions=[str(x) for x in (ext.get("descriptions") or [])],
    )


@router.get("", response_model=MediaListOut)
def list_media(
    topic_id: Optional[int] = None,
    kind: str = Query("image", pattern="^(image|video|all)$"),
    source: str = Query("all", pattern="^(ai|user|all)$"),
    risk: str = Query("all", pattern="^(high|medium|low|risky|none|unscanned|all)$"),
    mark: str = Query("all", pattern="^(marked|unmarked|all)$"),
    q: str = "",
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Cross-topic asset list. Images only by default: videos are outside this phase's external moderation scope, but can be viewed with kind=video."""
    qry = db.query(TopicMediaORM)
    if topic_id:
        qry = qry.filter(TopicMediaORM.topic_id == topic_id)
    if kind != "all":
        qry = qry.filter(TopicMediaORM.kind == kind)
    if source == "ai":
        qry = qry.filter(TopicMediaORM.is_ai_generated.is_(True))
    elif source == "user":
        qry = qry.filter(TopicMediaORM.is_ai_generated.is_(False))
    if risk == "risky":
        qry = qry.filter(TopicMediaORM.external_risk.in_(("high", "medium")))
    elif risk == "none":
        qry = qry.filter(TopicMediaORM.external_json.isnot(None),
                         func.coalesce(TopicMediaORM.external_risk, "") == "")
    elif risk == "unscanned":
        qry = qry.filter(TopicMediaORM.external_json.is_(None))
    elif risk != "all":
        qry = qry.filter(TopicMediaORM.external_risk == risk)
    # Marking only matters for AI-generated images: assets uploaded by users should not carry AI markings anyway
    if mark == "marked":
        qry = qry.filter(TopicMediaORM.gen_trace_no.isnot(None))
    elif mark == "unmarked":
        qry = qry.filter(TopicMediaORM.is_ai_generated.is_(True),
                         TopicMediaORM.gen_trace_no.is_(None))
    if q.strip():
        like = f"%{q.strip()}%"
        qry = qry.filter(TopicMediaORM.filename.ilike(like)
                         | TopicMediaORM.gen_trace_no.ilike(like))

    total = qry.count()
    rows = (qry.order_by(TopicMediaORM.uploaded_at.desc(), TopicMediaORM.id.desc())
               .offset(offset).limit(limit).all())

    # Fetch topic names and owning customers in one query, avoiding N+1
    tids = {r.topic_id for r in rows}
    tmap: dict[int, tuple[str, str]] = {}
    if tids:
        for t, email in (db.query(AiTelemetryTopicORM, UserORM.email)
                           .outerjoin(UserORM, UserORM.id == AiTelemetryTopicORM.user_id)
                           .filter(AiTelemetryTopicORM.id.in_(tids)).all()):
            tmap[t.id] = (t.name or "", email or "")
    items = [_serialize(r, topic_name=tmap.get(r.topic_id, ("", ""))[0],
                        owner_email=tmap.get(r.topic_id, ("", ""))[1]) for r in rows]
    return MediaListOut(total=total, items=items)


@router.get("/stats", response_model=MediaStatsOut)
def media_stats(
    topic_id: Optional[int] = None,
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    base = db.query(TopicMediaORM).filter(TopicMediaORM.kind == "image")
    if topic_id:
        base = base.filter(TopicMediaORM.topic_id == topic_id)
    return MediaStatsOut(
        total=base.count(),
        ai_generated=base.filter(TopicMediaORM.is_ai_generated.is_(True)).count(),
        missing_mark=base.filter(TopicMediaORM.is_ai_generated.is_(True),
                                 TopicMediaORM.gen_trace_no.is_(None)).count(),
        risky=base.filter(TopicMediaORM.external_risk.in_(("high", "medium"))).count(),
        unscanned=base.filter(TopicMediaORM.external_json.is_(None)).count(),
    )


@router.get("/{media_id}", response_model=MediaOut)
def get_media(
    media_id: int,
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Single item details. **File metadata is only read here**: reading files row by row on the list page would drag the list down."""
    m = db.query(TopicMediaORM).get(media_id)
    if not m:
        raise HTTPException(404, "素材不存在")
    t = db.query(AiTelemetryTopicORM).get(m.topic_id)
    email = ""
    if t:
        u = db.query(UserORM).get(t.user_id)
        email = (u.email if u else "") or ""
    return _serialize(m, topic_name=(t.name if t else "") or "", owner_email=email, with_meta=True)


@router.post("/{media_id}/rescan", response_model=MediaOut)
def rescan_media(
    media_id: int,
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Resubmit for external moderation: clear the old conclusion and check again, for re-checks after moderation degradation."""
    m = db.query(TopicMediaORM).get(media_id)
    if not m:
        raise HTTPException(404, "素材不存在")
    if (m.kind or "image") != "image":
        raise HTTPException(400, "只有图片支持外检")
    from geo.services.compliance_scanner import external_check_media
    m.external_json = None
    m.external_risk = None
    external_check_media(db, m)
    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.post("/{media_id}/remark", response_model=MediaOut)
def remark_media(
    media_id: int,
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Apply content marking to AI images missing it (number + metadata + watermark per switch).

    Only available for is_ai_generated rows: putting AI markings on user-uploaded assets would be false labeling.
    """
    m = db.query(TopicMediaORM).get(media_id)
    if not m:
        raise HTTPException(404, "素材不存在")
    if not m.is_ai_generated:
        raise HTTPException(400, "只有本平台生成的图才打 AI 内容标识")
    if m.gen_trace_no:
        raise HTTPException(409, "该图已有内容编号,不重复打标")
    if not image_marking.stamp_media(db, m, model=m.gen_model or ""):
        raise HTTPException(500, "打标失败,详见服务日志")
    db.commit()
    db.refresh(m)
    return _serialize(m, with_meta=True)


@router.delete("/{media_id}", status_code=204)
def delete_media(
    media_id: int,
    _admin: UserORM = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    m = db.query(TopicMediaORM).get(media_id)
    if not m:
        raise HTTPException(404, "素材不存在")
    tid, sp = m.topic_id, m.storage_path or ""
    db.delete(m)
    db.commit()
    media_storage.delete_file(tid, sp)
    return None
