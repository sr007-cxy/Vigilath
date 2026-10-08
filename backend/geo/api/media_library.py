"""Media library — the unified paid-media entry on the user side.

Division of labor with `/api/rwj/*`:

| | `/api/media/*` | `/api/rwj/*` |
|---|---|---|
| Audience | All logged-in users | Ops (admin/operator) |
| Data source | Mirror table (deduped and merged across both vendors) | Both vendors' live APIs, per site |
| Expose supplier | **No**, only media_id | Yes: provider / resource_id / cost price |
| Price shown | External price | Cost price + external price |

The user side has no notion of "channel / supplier" at all: one media library, one price, one submit button.
When both vendors carry the same media, the system automatically uses the cheaper one, and the user sees one row.

**Mixing vendors is allowed**: drafts are pushed once per (provider, res_type) group and the results merged.
`PushDraftPayload.provider` is single-valued, the only thing in the old endpoint preventing mixing; the new endpoint bypasses it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import get_current_user, is_staff
from geo.database import settings
from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicGeneratedDocORM
from geo.services import media_catalog, publish_task_service
from geo.services import ruanwenjiang_publisher as rwj_pub
from geo.services.compliance_scanner import publish_gate
from geo.services.rwj_client import RwjApiError
from geo.services.access_control import ACTION_WRITE, require_resource_access, resource_not_found

log = logging.getLogger(__name__)
router = APIRouter(prefix="/media", dependencies=[Depends(get_current_user)])


def _own_doc_or_404(db: Session, doc_id: int, user) -> TopicGeneratedDocORM:
    doc = db.get(TopicGeneratedDocORM, doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, user, doc, ACTION_WRITE)
    return doc


class SearchPayload(BaseModel):
    res_type: str = "Website"
    platform: str = ""
    channel: str = ""
    region: str = ""
    include: str = ""
    keyword: str = ""
    price_min_cents: Optional[int] = None
    price_max_cents: Optional[int] = None
    sort: str = media_catalog.DEFAULT_SORT
    page: int = 1
    page_size: int = 20


class PushDraftPayload(BaseModel):
    media_ids: list[int] = Field(default_factory=list)


@router.get("/facets")
def media_facets(res_type: str = "Website", db: Session = Depends(get_db)):
    if not media_catalog.available(db):
        return {"available": False, "facets": {},
                "message": "媒体库正在同步,请稍后再试"}
    return {"available": True, "facets": media_catalog.facets(db, res_type),
            "types": list(media_catalog.BROWSABLE_TYPES)}


@router.post("/search")
def search_media(payload: SearchPayload,
                 current_user=Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if not media_catalog.available(db):
        # Report an empty table honestly and do not fall back to the live API — falling back would re-expose vendor differences
        return {"available": False, "rows": [], "total": 0,
                "page": 1, "page_size": payload.page_size,
                "message": "媒体库正在同步,请稍后再试"}
    return media_catalog.search(
        db,
        res_type=payload.res_type, platform=payload.platform,
        channel=payload.channel, region=payload.region, include=payload.include,
        keyword=payload.keyword,
        price_min_cents=payload.price_min_cents,
        price_max_cents=payload.price_max_cents,
        sort=payload.sort, page=payload.page, page_size=payload.page_size,
        staff=is_staff(current_user),
    )


@router.post("/docs/{doc_id}/push-draft")
def push_draft(doc_id: int, payload: PushDraftPayload,
               current_user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Push a draft to the media drafts box (no charge) and create planned placement tasks.

    A real outbound point — must pass `publish_gate`.
    """
    if not payload.media_ids:
        raise HTTPException(422, {"code": "NO_RESOURCE", "message": "请先勾选要投放的媒体"})
    if len(payload.media_ids) > settings.RWJ_MULTI_MAX:
        raise HTTPException(422, {
            "code": "TOO_MANY_RESOURCES",
            "message": f"一次最多投放 {settings.RWJ_MULTI_MAX} 家媒体,请分批提交",
        })

    doc = _own_doc_or_404(db, doc_id, current_user)
    if doc.status not in ("approved", "published"):
        raise HTTPException(409, {"code": "NEED_APPROVED", "message": "请先通过审核再投放付费媒体"})
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if topic is None:
        raise HTTPException(404, "topic not found")

    picked = media_catalog.resolve(db, payload.media_ids)
    if not picked:
        raise HTTPException(409, {"code": "NO_RESOURCE",
                                  "message": "勾选的媒体已下架,请重新选择"})
    missing = [i for i in payload.media_ids
               if i not in {p["media_id"] for p in picked}]

    ok, reason = publish_gate(
        db, doc, topic, hook="publish_paid_media",
        actor_id=current_user.id, actor_name=getattr(current_user, "name", "") or "",
    )
    if not ok:
        db.commit()                      # Record the block for audit
        raise HTTPException(409, {"code": "COMPLIANCE_BLOCKED", "message": reason})

    # Push once per (supplier, resource type) group — the two vendors number resources independently and cannot be pushed together
    groups: dict[tuple[str, str], list[dict]] = {}
    for m in picked:
        groups.setdefault((m["provider"], m["res_type"]), []).append(m)

    quoted_at = datetime.utcnow().isoformat()
    task_targets: list[dict] = []
    failed: list[dict] = []
    dropped_images: list[dict] = []      # Images not carried over in the body, one list per article
    for (provider, res_type), members in groups.items():
        targets = [{"resource_id": m["resource_id"], "resource_title": m["title"]}
                   for m in members]
        try:
            results, grp_failed = rwj_pub.push_drafts(
                doc, type_=res_type, provider=provider, targets=targets)
        except (RwjApiError, httpx.RequestError) as e:
            log.warning("push_drafts failed provider=%s type=%s: %s", provider, res_type, e)
            # One vendor being unavailable should not stop the other's drafts from going out; record it and continue
            failed.extend({"media_name": m["title"], "message": "媒体通道暂不可用,请稍后重试"}
                          for m in members)
            continue
        failed.extend({"media_name": f.get("resource_title") or "",
                       "message": "该媒体暂时无法接稿"} for f in (grp_failed or []))
        for r in results:
            task_targets.append({
                "platform": rwj_pub.platform_name(provider),
                "media_name": r.resource_title,
                "channel_type": "external_media", "publish_mode": "paid_media",
                "channel_meta": {
                    "channel": provider, "provider": provider, "type": res_type,
                    "resource_id": r.resource_id, "resource_title": r.resource_title,
                    "draft_id": r.draft_id, "delivery": r.delivery,
                    "quoted_price": r.price, "quoted_level": r.level,
                    "quoted_at": quoted_at,
                    "dropped_images": r.dropped_images,
                },
            })
            dropped_images = r.dropped_images

    failed.extend({"media_name": "", "media_id": i, "message": "该媒体已下架"}
                  for i in missing)

    if not task_targets:
        db.commit()
        raise HTTPException(502, {"code": "PUSH_FAILED",
                                  "message": "所有勾选的媒体都没能接稿,未建任务",
                                  "failed": failed})

    tasks = publish_task_service.create_tasks(
        db, doc, topic, task_targets,
        assigned_to_user_id=current_user.id, initial_status="planned",
    )
    db.commit()
    redact = not is_staff(current_user)
    return {
        "tasks": [publish_task_service.task_to_dict(t, redact=redact, db=db)
                  for t in tasks],
        "failed": failed,
        "dropped_images": dropped_images,
    }
