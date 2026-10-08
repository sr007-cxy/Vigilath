"""Content template CRUD — for the execution plan editor.

scope:
- system: global presets, editable by workbench staff (admin | operator)
- tenant: ops' own templates; only the creator (created_by_id) or the same tenant_id can edit
- topic: temporary templates attached to a topic

After locking (locked=True), the prompt_template / kind fields become read-only;
other metadata (name / target_platforms / length_*) can still be changed.
Locked templates can still be used in publishing plans; each LLM call renders with the "current prompt".
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from geo.api.auth import get_current_user, is_staff, require_workbench
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import (
    ContentTemplateCreate, ContentTemplateORM, ContentTemplateOut,
    ContentTemplateUpdate, TopicGeneratedDocORM,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/content-templates")


def _to_out(t: ContentTemplateORM) -> ContentTemplateOut:
    try:
        platforms = json.loads(t.target_platforms_json or "[]")
    except Exception:  # noqa: BLE001
        platforms = []
    if not isinstance(platforms, list):
        platforms = []
    return ContentTemplateOut(
        id=t.id, scope=t.scope, tenant_id=t.tenant_id, topic_id=t.topic_id,
        name=t.name, kind=t.kind, prompt_template=t.prompt_template,
        target_platforms=[str(p) for p in platforms if p],
        length_min=t.length_min, length_max=t.length_max,
        locked=t.locked, locked_at=t.locked_at, locked_by_id=t.locked_by_id,
        created_by_id=t.created_by_id,
        created_at=t.created_at, updated_at=t.updated_at,
    )



def _owned_topic_or_403(db: Session, topic_id: int, actor):
    """Is this topic the caller's? If not, reject as "not found" (does not leak existence)."""
    from geo.models.ai_telemetry import AiTelemetryTopicORM
    from geo.services.access_control import (
        ACTION_READ, require_resource_access, resource_not_found,
    )
    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise resource_not_found()
    require_resource_access(db, actor, t, ACTION_READ)
    return t


def _strip_internal(out: ContentTemplateOut) -> ContentTemplateOut:
    """Template for customers: strip the prompt body.

    The launch dialog only uses id / name / kind (+ scorecard); prompt_template is our prompt engineering,
    an internal asset, with no reason to be sent to customers with the list.
    """
    return out.model_copy(update={"prompt_template": ""})


@router.get("", response_model=list[ContentTemplateOut])
def list_templates(
    scope: Optional[str] = Query(None, description="system / tenant / topic;空则全部"),
    topic_id: Optional[int] = Query(None, description="scope=topic 时按 topic 过滤"),
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Template list. **Customers can read it too** (the launch-project dialog lists selectable templates from it), but narrowed in two ways:

    · Only system (global) and tenant (generic templates written by ops) scopes, plus topic templates of **their own topics**
      — without topic_id the topic scope is omitted entirely, or other customers' topic templates would be listed;
    · prompt_template is stripped (the prompt body is an internal asset; the dialog only uses id/name/kind).
    Write operations (create/edit/delete/lock) remain admin-only and are not opened up.
    """
    staff = is_staff(actor)
    if scope and scope not in ("system", "tenant", "topic"):
        raise HTTPException(422, {"code": "INVALID_SCOPE", "message": "scope 必须是 system/tenant/topic"})
    if not staff:
        if scope == "topic" or topic_id is not None:
            if topic_id is None:
                raise HTTPException(422, {
                    "code": "TOPIC_REQUIRED", "message": "按主题查模板必须带 topic_id"})
            _owned_topic_or_403(db, topic_id, actor)
        elif scope is None:
            # When no scope is given, customers only see global and generic templates — otherwise other customers' topic templates would be pulled in
            scope = None
    q = db.query(ContentTemplateORM)
    if scope:
        q = q.filter(ContentTemplateORM.scope == scope)
    elif not staff:
        q = q.filter(ContentTemplateORM.scope.in_(("system", "tenant")))
    if topic_id is not None:
        q = q.filter(ContentTemplateORM.topic_id == topic_id)
    rows = q.order_by(ContentTemplateORM.scope.asc(), ContentTemplateORM.id.asc()).all()
    out = [_to_out(t) for t in rows]
    return out if staff else [_strip_internal(o) for o in out]


@router.get("/scorecard")
def template_scorecard(
    topic_id: Optional[int] = Query(None, description="只统计该主题的稿;空=全量"),
    days: Optional[int] = Query(None, ge=1, le=730, description="只看这些天内生成的稿"),
    actor = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Template scorecard — how drafts produced by each template actually turned out.

    Citation rate (share with non-empty cited_by) dominates; compliance rejections / manual approval / platform approval are secondary;
    metric definitions and weights in `geo/services/template_scorecard.py`. Read-only aggregation, no data changed.

    **Customers can read it too, but must pass their own topic_id** — an empty topic_id is the site-wide aggregate, an ops-only view.
    """
    from geo.services import template_scorecard

    if not is_staff(actor):
        if topic_id is None:
            raise HTTPException(422, {
                "code": "TOPIC_REQUIRED", "message": "查模板成绩单必须带 topic_id"})
        _owned_topic_or_403(db, topic_id, actor)

    return {
        "mature_days": template_scorecard.MATURE_DAYS,
        "min_sample": template_scorecard.MIN_SAMPLE,
        "weights": template_scorecard.WEIGHTS,
        "items": template_scorecard.build(db, topic_id=topic_id, days=days),
    }


@router.post("", response_model=ContentTemplateOut)
def create_template(
    payload: ContentTemplateCreate,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    if payload.scope == "topic" and not payload.topic_id:
        raise HTTPException(422, {
            "code": "MISSING_TOPIC", "field": "topic_id",
            "message": "scope=topic 时必须带 topic_id",
        })
    if payload.length_min < 100 or payload.length_max > 5000 or payload.length_min > payload.length_max:
        raise HTTPException(422, {
            "code": "INVALID_LENGTH", "field": "length_min/max",
            "message": "长度范围非法(100 ≤ min ≤ max ≤ 5000)",
        })
    t = ContentTemplateORM(
        scope=payload.scope,
        tenant_id=getattr(admin, "id", None) if payload.scope == "tenant" else None,
        topic_id=payload.topic_id if payload.scope == "topic" else None,
        name=payload.name.strip()[:64],
        kind=payload.kind.strip()[:16],
        prompt_template=payload.prompt_template,
        target_platforms_json=json.dumps(payload.target_platforms or [], ensure_ascii=False),
        length_min=payload.length_min,
        length_max=payload.length_max,
        locked=False,
        created_by_id=admin.id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.patch("/{template_id}", response_model=ContentTemplateOut)
def update_template(
    template_id: int,
    payload: ContentTemplateUpdate,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    t = db.get(ContentTemplateORM, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    locked_fields = ("prompt_template", "kind")
    if t.locked:
        for f in locked_fields:
            if getattr(payload, f, None) is not None:
                raise HTTPException(409, {
                    "code": "TEMPLATE_LOCKED", "field": f,
                    "message": "模板已锁定,prompt_template / kind 不可改",
                })
    if payload.name is not None:
        t.name = payload.name.strip()[:64]
    if payload.kind is not None:
        t.kind = payload.kind.strip()[:16]
    if payload.prompt_template is not None:
        t.prompt_template = payload.prompt_template
    if payload.target_platforms is not None:
        t.target_platforms_json = json.dumps(payload.target_platforms, ensure_ascii=False)
    if payload.length_min is not None:
        t.length_min = payload.length_min
    if payload.length_max is not None:
        t.length_max = payload.length_max
    if t.length_min > t.length_max:
        raise HTTPException(422, {
            "code": "INVALID_LENGTH", "message": "length_min 不能大于 length_max",
        })
    t.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Delete a template — locked templates must be unlocked first, to avoid deleting a frozen prompt by mistake.

    Drafts referencing the template are not deleted; only template_id is cleared (the leftover id in publishing plan rows
    shows as "not selected" in the editor; just pick another template).
    """
    t = db.get(ContentTemplateORM, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    if t.locked:
        raise HTTPException(409, {
            "code": "TEMPLATE_LOCKED",
            "message": "模板已锁定,请先解除锁定再删除",
        })
    n = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.template_id == template_id)
          .update({TopicGeneratedDocORM.template_id: None}, synchronize_session=False)
    )
    db.delete(t)
    db.commit()
    log.info("content template %d deleted (detached %s docs)", template_id, n)
    return None


@router.post("/{template_id}/lock", response_model=ContentTemplateOut)
def lock_template(
    template_id: int,
    admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    t = db.get(ContentTemplateORM, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    if not t.locked:
        t.locked = True
        t.locked_at = datetime.utcnow()
        t.locked_by_id = admin.id
        t.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(t)
    return _to_out(t)


@router.post("/{template_id}/unlock", response_model=ContentTemplateOut)
def unlock_template(
    template_id: int,
    _admin = Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Unlock — prompt_template / kind become editable again."""
    t = db.get(ContentTemplateORM, template_id)
    if not t:
        raise HTTPException(404, "template not found")
    if t.locked:
        t.locked = False
        t.locked_at = None
        t.locked_by_id = None
        t.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(t)
    return _to_out(t)
