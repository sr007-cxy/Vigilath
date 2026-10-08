"""Generation provenance API — reverse-lookup entry for implicit labels under the "Measures for Labeling AI-Generated Synthetic Content".

Delivery vehicle for item 5 of the algorithm filing correction list; plan in
`docs/09-软著与专利/备案补正/02-方案与代办台账.md` §4.2. The frontend page is at `/workbench/content-trace`.

Endpoints:
- GET  /admin/content-trace              — recent generation records (ledger list, filterable by topic/keyword)
- GET  /admin/content-trace/{trace_no}   — reverse-lookup a single record by content number
- POST /admin/content-trace/extract      — paste body text → decode the content number from the zero-width watermark → look up

Read-only, no data changed. Permissions follow the workbench (require_workbench), same level as the content review console.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_workbench
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicGeneratedDocORM, TopicMediaORM,
)
from geo.models.user import UserORM
from geo.services import content_marking, image_marking

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/content-trace")


class ContentTraceOut(BaseModel):
    """One generation provenance record — exactly the "generator + IP + time + model + content number" auditors want to see.

    Text drafts (kind='text', number VGL-…) and AI images (kind='image', number VGI-…) share this structure:
    their fields are largely the same, and two schemas would make the frontend render the same table twice. Fields images lack
    (review status, generation IP) stay empty; the frontend decides display by kind.
    """
    kind: str = "text"
    trace_no: Optional[str] = None
    doc_id: int
    title: str = ""
    status: str = ""
    # 'ai' = generated/synthesized by this platform; 'user' = customer-supplied draft (no AI label, but provenance is still recorded)
    source: str = "ai"
    generated_at: Optional[datetime] = None      # Generation time (naive UTC, frontend shows Beijing time)
    updated_at: Optional[datetime] = None        # Last time the body was overwritten
    llm_model: str = ""                          # Base model called
    gen_user_id: Optional[int] = None
    gen_user_email: str = ""                     # Generator (account that triggered generation)
    gen_client_ip: str = ""                      # 'system' = automatic batch run, no manual trigger
    topic_id: int
    topic_name: str = ""
    owner_user_id: Optional[int] = None
    owner_email: str = ""                        # Customer owning the content (topic owner)
    # Whether both explicit / implicit labels are in place — these two ✅ are what evidence screenshots need
    # Text (since 2026-08-17): explicit = label bar in the display UI; implicit = provenance ledger in the DB
    #   — the body itself no longer carries a label line or zero-width watermark; drafts are auto-placed to external media,
    #      and labels in the body would be published verbatim by platforms; see the services/content_marking module header
    # Images: corner watermark + EXIF/tEXt metadata (still written into the file)
    has_explicit_mark: bool = False
    has_implicit_mark: bool = False
    body_chars: int = 0
    # ─── kind='image' only ───
    media_id: Optional[int] = None
    media_url: str = ""              # Public preview URL, can be opened directly to check the watermark when screenshotting
    media_filename: str = ""
    media_meta: dict[str, str] = Field(default_factory=dict)   # File metadata read back


class TraceListOut(BaseModel):
    total: int
    items: list[ContentTraceOut]
    # Legacy drafts (generated before the feature launched) lack the three provenance columns; the page notes this so it is not misread as "missed collection"
    untraced: int = 0


class ExtractPayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)


class ExtractOut(BaseModel):
    trace_no: Optional[str] = None
    found: bool = False
    # Number decoded but not found in the DB → most likely forged elsewhere, or the draft was cleaned up
    record: Optional[ContentTraceOut] = None
    message: str = ""


def _to_out(db: Session, d: TopicGeneratedDocORM) -> ContentTraceOut:
    t = db.get(AiTelemetryTopicORM, d.topic_id)
    owner_id = getattr(t, "user_id", None) if t else None
    gen_user = db.get(UserORM, d.gen_user_id) if d.gen_user_id else None
    owner = db.get(UserORM, owner_id) if owner_id else None
    body = d.body_markdown or ""
    return ContentTraceOut(
        trace_no=d.gen_trace_no,
        doc_id=d.id,
        title=d.title or "",
        status=d.status or "",
        source=d.source or "ai",
        generated_at=d.created_at,
        updated_at=getattr(d, "updated_at", None) or d.created_at,
        llm_model=d.llm_model or "",
        gen_user_id=d.gen_user_id,
        gen_user_email=getattr(gen_user, "email", "") or "",
        gen_client_ip=d.gen_client_ip or "",
        topic_id=d.topic_id,
        topic_name=getattr(t, "name", "") or "",
        owner_user_id=owner_id,
        owner_email=getattr(owner, "email", "") or "",
        # Explicit label: always present for AI drafts in the product display UI (label bar on article details); customer-supplied drafts are not labeled.
        # Implicit label: in place once all three provenance ledger columns are filled — the zero-width watermark in the body is retired,
        # but for early legacy drafts a watermark still left in the body also counts.
        has_explicit_mark=content_marking.is_ai_generated(d),
        has_implicit_mark=bool(d.gen_trace_no)
        or bool(content_marking.extract_trace_no(body)),
        body_chars=len(body),
    )


def _media_to_out(db: Session, m: TopicMediaORM) -> ContentTraceOut:
    """Provenance record of AI images. Criterion is "metadata can be read back" — the visible watermark has been off by default
    since 2026-08-17 (images are placed to external media along with articles), so no text on the image is normal;
    media_url is also given to the frontend so the original image can be opened to check metadata during evidence collection."""
    t = db.get(AiTelemetryTopicORM, m.topic_id)
    owner = db.get(UserORM, m.user_id) if m.user_id else None
    meta = image_marking.read_marks(m.storage_path) if m.storage_path else {}
    sp = (m.storage_path or "").replace("\\", "/")
    fname = sp.rsplit("/", 1)[-1] if "/" in sp else sp
    return ContentTraceOut(
        kind="image",
        trace_no=m.gen_trace_no,
        doc_id=0,
        title=m.filename or f"配图 #{m.id}",
        status="",
        source="ai" if m.is_ai_generated else "user",
        generated_at=m.uploaded_at,
        updated_at=m.uploaded_at,
        llm_model=m.gen_model or "",
        gen_user_id=m.user_id,
        gen_user_email=getattr(owner, "email", "") or "",
        # Image generation runs in the generation thread without its own client IP; ownership and trigger info come from the batch of drafts it belongs to
        gen_client_ip="",
        topic_id=m.topic_id,
        topic_name=getattr(t, "name", "") or "",
        owner_user_id=m.user_id,
        owner_email=getattr(owner, "email", "") or "",
        has_explicit_mark=bool(m.gen_trace_no),
        has_implicit_mark=(meta.get("ContentTraceNo") == m.gen_trace_no
                           or m.gen_trace_no in (meta.get("Description") or "")
                           if m.gen_trace_no else False),
        body_chars=int(m.size or 0),
        media_id=m.id,
        media_url=f"/api/ai-telemetry/public/topic-media/{m.topic_id}/{fname}",
        media_filename=fname,
        media_meta=meta,
    )


@router.get("", response_model=TraceListOut)
def list_traces(
    topic_id: Optional[int] = None,
    q: Optional[str] = None,
    kind: str = "text",
    limit: int = 50,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Generation ledger — by default the latest N records ordered by generation time descending.

    kind: text (text drafts, default) / image (AI images) / all (both merged, sorted by time).
    q matches both content number and title (the number is the main use; title is a convenience).
    Soft-deleted drafts are excluded from the ledger: they no longer exist for the business, and listing them would make auditors think they are still in use.
    """
    limit = max(1, min(200, int(limit or 50)))
    kw = f"%{q.strip()}%" if (q and q.strip()) else None
    items: list[ContentTraceOut] = []
    total = 0
    untraced = 0

    if kind in ("text", "all"):
        base = (db.query(TopicGeneratedDocORM)
                  .filter(TopicGeneratedDocORM.deleted_at.is_(None)))
        if topic_id:
            base = base.filter(TopicGeneratedDocORM.topic_id == topic_id)
        if kw:
            base = base.filter(
                TopicGeneratedDocORM.gen_trace_no.ilike(kw)
                | TopicGeneratedDocORM.title.ilike(kw)
            )
        total += base.count()
        untraced += base.filter(TopicGeneratedDocORM.gen_trace_no.is_(None)).count()
        items += [_to_out(db, d) for d in
                  base.order_by(TopicGeneratedDocORM.created_at.desc()).limit(limit).all()]

    if kind in ("image", "all"):
        # Only AI-generated images — user-uploaded assets are not generated/synthesized content and do not belong in the labeling ledger
        mb = (db.query(TopicMediaORM)
                .filter(TopicMediaORM.is_ai_generated.is_(True)))
        if topic_id:
            mb = mb.filter(TopicMediaORM.topic_id == topic_id)
        if kw:
            mb = mb.filter(
                TopicMediaORM.gen_trace_no.ilike(kw)
                | TopicMediaORM.filename.ilike(kw)
            )
        total += mb.count()
        untraced += mb.filter(TopicMediaORM.gen_trace_no.is_(None)).count()
        items += [_media_to_out(db, m) for m in
                  mb.order_by(TopicMediaORM.uploaded_at.desc()).limit(limit).all()]

    if kind == "all":
        items.sort(key=lambda r: r.generated_at or datetime.min, reverse=True)
        items = items[:limit]
    return TraceListOut(total=total, untraced=untraced, items=items)


@router.get("/{trace_no}", response_model=ContentTraceOut)
def get_trace(
    trace_no: str,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Reverse-lookup by content number. Returns 404 when the number does not exist — that itself is a conclusion (not content of this platform).

    Routed by prefix: VGI-… queries AI images, others (VGL-…) query text drafts.
    """
    key = (trace_no or "").strip().upper()
    if image_marking.is_image_trace_no(key):
        m = (db.query(TopicMediaORM)
               .filter(TopicMediaORM.gen_trace_no == key).first())
        if not m:
            raise HTTPException(404, {
                "code": "TRACE_NOT_FOUND",
                "message": f"图片编号 {key} 不存在,该图片非本平台生成",
            })
        return _media_to_out(db, m)

    d = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.gen_trace_no == key)
           .filter(TopicGeneratedDocORM.deleted_at.is_(None))
           .first())
    if not d:
        raise HTTPException(404, {
            "code": "TRACE_NOT_FOUND",
            "message": f"内容编号 {key} 不存在,该内容非本平台生成",
        })
    return _to_out(db, d)


@router.post("/extract", response_model=ExtractOut)
def extract_trace(
    payload: ExtractPayload,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Paste body text → decode the implicit watermark → look up the ledger.

    This is the real use of implicit labels: once content is reposted externally, the body text confirms whether we generated it,
    and who generated it when. If the zero-width watermark was scrubbed, fall back to the plaintext number in the explicit label line.
    """
    trace_no = content_marking.extract_trace_no(payload.text or "")
    if not trace_no:
        return ExtractOut(found=False, message="这段内容里没有本平台的生成标识")
    d = (db.query(TopicGeneratedDocORM)
           .filter(TopicGeneratedDocORM.gen_trace_no == trace_no)
           .filter(TopicGeneratedDocORM.deleted_at.is_(None))
           .first())
    if not d:
        return ExtractOut(trace_no=trace_no, found=False,
                          message=f"解出内容编号 {trace_no},但台账中查无此稿")
    return ExtractOut(trace_no=trace_no, found=True, record=_to_out(db, d),
                      message="")
