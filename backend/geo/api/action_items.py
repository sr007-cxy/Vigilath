"""Flywheel P2: optimization task center API: list / dismiss / convert.

Action items are produced on a side path by telemetry-service (daily report top_actions / re-test regressions /
cell insight suggestions, see storage.upsert_action_item); this is the consumer side:
  - GET  /admin/action-items/topics/{topic_id}          list (filter by status)
  - POST /admin/action-items/{item_id}/dismiss          dismiss
  - POST /admin/action-items/{item_id}/convert          convert:
      action=add_plan_item -> calls P1-B insert_plan_items (topics flow back into the publishing plan)
      action=retest        -> calls P1-C schedule_for_doc (requires linked_doc_id)
The task center is the manual steering wheel of the flywheel's "re-iterate" stage. Compliance tickets stay in the original system and are not migrated.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_workbench, get_current_user, is_staff
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, GeoActionItemORM, TopicGeneratedDocORM,
)
from geo.services.access_control import (
    ACTION_READ, ACTION_WRITE, require_resource_access, resource_not_found,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/action-items")
# User self-service version: with the workspace opened externally, customers manage their own topics' tasks on the "optimization tasks" page.
# Same list/dismiss/convert logic; the only difference is ownership gating here (_require_item_access).
self_router = APIRouter(prefix="/action-items")


class ActionItemOut(BaseModel):
    id: int
    topic_id: int
    source: str
    priority: str
    title: str
    body_md: str
    suggested_action: str
    status: str
    source_ref: dict
    linked_plan_item_id: Optional[str] = None
    linked_doc_id: Optional[int] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


def _to_out(r: GeoActionItemORM) -> ActionItemOut:
    try:
        ref = json.loads(r.source_ref_json or "{}")
        if not isinstance(ref, dict):
            ref = {}
    except Exception:  # noqa: BLE001
        ref = {}
    return ActionItemOut(
        id=r.id, topic_id=r.topic_id, source=r.source, priority=r.priority,
        title=r.title, body_md=r.body_md, suggested_action=r.suggested_action,
        status=r.status, source_ref=ref,
        linked_plan_item_id=r.linked_plan_item_id, linked_doc_id=r.linked_doc_id,
        created_at=r.created_at, resolved_at=r.resolved_at,
    )


class ConvertPayload(BaseModel):
    action: str                      # add_plan_item / retest
    # Seed text for add_plan_item. When empty it falls back to the task title, but for "media placement suggestion" tasks
    # the title is a platform name, not a topic, so it is required for those; see _do_convert.
    seed: str = ""
    queries: list[str] = []          # add_plan_item may specify the prompt group to cover


def _require_item_access(db: Session, item_id: int, user) -> GeoActionItemORM:
    """Action item write operations enforce unified enterprise authorization by the owning topic."""
    r = db.get(GeoActionItemORM, item_id)
    if r is None:
        raise resource_not_found()
    require_resource_access(db, user, r, ACTION_WRITE)
    return r


def _list_items(db: Session, topic_id: int, status: Optional[str], limit: int):
    if db.get(AiTelemetryTopicORM, topic_id) is None:
        raise HTTPException(404, "topic not found")
    q = db.query(GeoActionItemORM).filter(GeoActionItemORM.topic_id == topic_id)
    if status in ("open", "converted", "dismissed", "done"):
        q = q.filter(GeoActionItemORM.status == status)
    rows = (q.order_by(GeoActionItemORM.priority.asc(),
                       GeoActionItemORM.created_at.desc())
             .limit(min(limit, 300)).all())
    return [_to_out(r) for r in rows]


def _do_dismiss(db: Session, r: GeoActionItemORM) -> ActionItemOut:
    if r.status == "open":
        r.status = "dismissed"
        r.resolved_at = datetime.utcnow()
        db.commit()
    return _to_out(r)


def _is_media_rec(r: GeoActionItemORM) -> bool:
    """This task is a "media placement suggestion": it only says where to place, without a topic."""
    return r.source == "source_strategy" and (r.source_key or "").startswith("media:")


def _media_rec_platform(db: Session, ref: dict) -> str:
    """Media suggestion -> the plan row's platform. Refuse conversion if it cannot map to a placeable platform.

    The platform in a suggestion is natural language (e.g. 「博客园(cnblogs.com/博客园)」, "Cnblogs"); writing it directly as the platform name
    into a plan row can neither be published nor match the platform catalog; while dropping it and using the template's default platform
    would schedule a "place on Cnblogs" suggestion as "publish on Sohu"; both are wrong, so either parse out
    a real platform, or raise an explicit error and let a person choose.
    """
    from geo.services.source_plan import resolve_media_recommendation

    raw = str(ref.get("platform") or "").strip()
    if not raw:
        raise HTTPException(422, "这条媒体建议没有平台字段,无法转成发文行")
    hit = resolve_media_recommendation(db, raw)
    if not hit.get("platform"):
        raise HTTPException(422, (
            f"「{raw}」不是我们能直接投放的平台(百科/名录/泛指的行业门户都投不了),"
            "请在发文计划里手动加一行并自选平台。"))
    return str(hit["platform"])


def _do_convert(db: Session, r: GeoActionItemORM, payload: ConvertPayload, actor) -> ActionItemOut:
    if r.status != "open":
        raise HTTPException(409, "只有 open 状态的任务可转化")

    if payload.action == "add_plan_item":
        from geo.api.admin_review import (
            InsertPlanItemPayload, InsertPlanItemsPayload, insert_plan_items,
        )
        try:
            ref = json.loads(r.source_ref_json or "{}")
        except Exception:  # noqa: BLE001
            ref = {}
        if not isinstance(ref, dict):
            ref = {}
        # The task title can only be a seed when "the title itself is a topic". A media suggestion's title is
        # 「投放媒体建议:博客园(...)」 ("media placement suggestion: Cnblogs (...)"), and using it as a seed would produce an article about Cnblogs;
        # such tasks must have a topic filled in by a person.
        seed = payload.seed.strip()
        if not seed:
            if _is_media_rec(r):
                raise HTTPException(422, (
                    "这条是投放媒体建议,本身没有选题:请填写要写的选题(seed)再转发文计划。"))
            seed = r.title
        platform, platform_reason = "", ""
        if _is_media_rec(r):
            platform, platform_reason = _media_rec_platform(db, ref), "media_recommendation"
        queries = payload.queries or (
            [str(ref["query"])] if ref.get("query") else []
        )
        plan_out = insert_plan_items(
            r.topic_id,
            InsertPlanItemsPayload(items=[InsertPlanItemPayload(
                seed=seed, queries=queries, note=r.title,
                platform=platform, platform_reason=platform_reason,
                origin={"type": "action_item", "ref": r.id, "source": r.source},
            )]),
            admin=actor, db=db,
        )
        # insert_plan_items has already committed; take the last row's id (largest seq = the just-inserted row) to backfill provenance
        try:
            rows = list(plan_out.publishing_plan or [])
            if rows:
                r.linked_plan_item_id = str(getattr(rows[-1], "id", "") or "") or None
        except Exception:  # noqa: BLE001
            pass
    elif payload.action == "retest":
        if not r.linked_doc_id:
            raise HTTPException(422, "该任务没有关联稿件,无法转复测")
        doc = db.get(TopicGeneratedDocORM, r.linked_doc_id)
        topic = db.get(AiTelemetryTopicORM, r.topic_id)
        if doc is None or topic is None:
            raise HTTPException(404, "关联稿件或主题不存在")
        from geo.services.retest_service import schedule_for_doc
        schedule_for_doc(db, doc, topic)
    else:
        raise HTTPException(422, "action must be add_plan_item / retest")

    r.status = "converted"
    r.resolved_at = datetime.utcnow()
    db.commit()
    return _to_out(r)


# ── admin (workspace staff, platform-wide) ────────────────────────────────
@router.get("/topics/{topic_id}", response_model=list[ActionItemOut])
def list_action_items(
    topic_id: int, status: Optional[str] = None, limit: int = 100,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    return _list_items(db, topic_id, status, limit)


@router.post("/{item_id}/dismiss", response_model=ActionItemOut)
def dismiss_action_item(
    item_id: int,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    r = db.get(GeoActionItemORM, item_id)
    if r is None:
        raise HTTPException(404, "action item not found")
    return _do_dismiss(db, r)


@router.post("/{item_id}/convert", response_model=ActionItemOut)
def convert_action_item(
    item_id: int, payload: ConvertPayload,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    r = db.get(GeoActionItemORM, item_id)
    if r is None:
        raise HTTPException(404, "action item not found")
    return _do_convert(db, r, payload, admin)


# ── User self-service (customers, only their own topics' tasks) ──────────────────────
@self_router.get("/topics/{topic_id}", response_model=list[ActionItemOut])
def list_my_action_items(
    topic_id: int, status: Optional[str] = None, limit: int = 100,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise resource_not_found()
    require_resource_access(db, current_user, t, ACTION_READ)
    return _list_items(db, topic_id, status, limit)


@self_router.post("/{item_id}/dismiss", response_model=ActionItemOut)
def dismiss_my_action_item(
    item_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _require_item_access(db, item_id, current_user)
    return _do_dismiss(db, r)


@self_router.post("/{item_id}/convert", response_model=ActionItemOut)
def convert_my_action_item(
    item_id: int, payload: ConvertPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = _require_item_access(db, item_id, current_user)
    return _do_convert(db, r, payload, current_user)
