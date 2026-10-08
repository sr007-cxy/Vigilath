"""Publishing-plan preset placement -- when a draft is approved, auto-create placement tasks from the plan row's (platform, media).

New persisted fields on plan rows (publishing_plan_json):
  media            account/media name (free text)
  rwj_resource_id  Ruanwenjiang media resource id (used to auto-push a draft when platform=软文匠 ("Ruanwenjiang"))
  extra_platforms  extra placement platforms (one draft, many outlets): one task each, no media, not via Ruanwenjiang

Approval hooks (admin approve / user self-review approve) call apply_preset_on_approve:
  - platform=软文匠 ("Ruanwenjiang") with a resource id -> pass publish_gate -> auto-push Ruanwenjiang draft (free)
    -> create planned task (with quote/draft_id); a failed draft push doesn't block, the task records last_error
  - other platforms -> create default task (pending publish/backfill); auto channels like the official blog publish via existing pipeline
  - no plan row / no preset platform -> no action
Idempotent: create_tasks reuses by (doc, platform, media); repeated approve doesn't create duplicates.
This function commits on its own (called after approve has committed; failure only rolls back the preset, not the review result).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import (
    AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicORM, TopicGeneratedDocORM,
)
from geo.services import publish_task_service

log = logging.getLogger(__name__)


def _plan_item_of(db: Session, doc: TopicGeneratedDocORM) -> Optional[dict]:
    if not (doc.plan_item_id and doc.execution_plan_id):
        return None
    plan = db.get(AiTelemetryTopicExecutionPlanORM, doc.execution_plan_id)
    if plan is None:
        return None
    try:
        items = json.loads(plan.publishing_plan_json or "[]")
    except ValueError:
        return None
    for it in items:
        if isinstance(it, dict) and str(it.get("id") or "") == doc.plan_item_id:
            return it
    return None


def apply_preset_on_approve(db: Session, doc: TopicGeneratedDocORM,
                            topic: AiTelemetryTopicORM,
                            *, actor_id: Optional[int] = None) -> Optional[dict]:
    """Create tasks from the plan row preset. Returns {platform, media, task_id, action} or None (no preset).

    All exceptions are swallowed (logged); never let the review action fail.
    """
    try:
        return _apply(db, doc, topic, actor_id=actor_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("[publish-preset] apply failed doc=%s", doc.id)
        return None


def _apply(db: Session, doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM,
           *, actor_id: Optional[int]) -> Optional[dict]:
    item = _plan_item_of(db, doc)
    platform = str((item or {}).get("platform") or doc.platform or "").strip()
    channel = str((item or {}).get("channel") or "").strip()
    media = str((item or {}).get("media") or "").strip()
    rwj_resource_id = (item or {}).get("rwj_resource_id")

    from geo.services import ruanwenjiang_publisher as rp
    from geo.services.media_select import schedule_auto_select

    # 2026-08-04 -- one draft, many outlets: one task per extra platform (no media; Ruanwenjiang only accepts the main platform's resource id).
    # Whichever branch the main platform takes (paid Ruanwenjiang / normal task) doesn't affect this, so create extra platforms first.
    extras = [
        p for p in dict.fromkeys(
            str(x).strip() for x in ((item or {}).get("extra_platforms") or [])
        )
        if p and p != platform and p not in rp.PLATFORM_NAMES
    ]
    for extra in extras:
        try:
            tasks = publish_task_service.create_tasks(
                db, doc, topic, [{"platform": extra, "media_name": ""}],
                assigned_to_user_id=actor_id,
            )
            db.commit()
            log.info("[publish-preset] doc=%s extra platform %s -> task %s", doc.id, extra, tasks[0].id)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.warning("[publish-preset] doc=%s extra platform %s task creation failed", doc.id, extra, exc_info=True)

    # Whether to use a paid publishing channel: new plan rows check channel (rwj=Ruanwenjiang / mjk=Meijieku);
    # old rows have no channel, so keep the "platform is the channel name" rule.
    from geo.services.rwj_client import SITES
    preset_provider = channel if channel in SITES else rp.provider_of(platform)
    # Explicit preset (plan row already picked media) wins; AI stays out
    if preset_provider and rwj_resource_id:
        return _apply_rwj(db, doc, topic, int(rwj_resource_id), media,
                          provider=preset_provider,
                          target_platform=platform, actor_id=actor_id)

    out: Optional[dict] = None
    if platform and media:
        # Manual preset with both platform and media specified
        tasks = publish_task_service.create_tasks(
            db, doc, topic, [{"platform": platform, "media_name": media}],
            assigned_to_user_id=actor_id,
        )
        db.commit()
        log.info("[publish-preset] doc=%s -> task %s (%s/%s)", doc.id, tasks[0].id, platform, media)
        out = {"platform": platform, "media": media, "task_id": tasks[0].id, "action": "task"}
    elif platform:
        # Platform but no media: create a placeholder platform task
        tasks = publish_task_service.create_tasks(
            db, doc, topic, [{"platform": platform, "media_name": ""}],
            assigned_to_user_id=actor_id,
        )
        db.commit()
        out = {"platform": platform, "media": "", "task_id": tasks[0].id, "action": "task"}

    # If the plan row says anything, AI stays out -- otherwise a free plan row that explicitly says "WeChat official account / official blog"
    # would, after approval, get a background thread picking a Ruanwenjiang media, pushing a draft and creating a planned task, conjuring
    # a paid intent out of nothing. Only loose drafts with no placement preset go through AI auto media selection.
    if not channel and not platform and not extras:
        schedule_auto_select(doc.id, actor_id=actor_id)
    return out


def _rwj_resource_meta(db: Session, resource_id: int,
                       provider: Optional[str] = None) -> tuple[str, str, str]:
    """Resource id -> (site, resource type, media name). Falls back to (rwj, Website, "") when the resource library hasn't synced.

    The two sites number resource_id independently; when a plan row has no channel, only the mirror lookup can tell which
    site it belongs to -- a wrong guess places an order on site B with site A's id.
    """
    from geo.services.rwj_client import DEFAULT_PROVIDER

    try:
        from geo.models.rwj_media import RwjMediaResourceORM as R
        q = db.query(R).filter(R.resource_id == int(resource_id))
        if provider:
            q = q.filter(R.provider == provider)
        row = q.order_by(R.is_active.desc()).first()
        if row is not None:
            return (row.provider or DEFAULT_PROVIDER,
                    row.res_type or "Website", row.title or "")
    except Exception:  # noqa: BLE001
        log.warning("[publish-preset] resource type lookup failed resource_id=%s", resource_id, exc_info=True)
    return provider or DEFAULT_PROVIDER, "Website", ""


def _apply_rwj(db: Session, doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM,
               resource_id: int, resource_title: str, *,
               provider: Optional[str] = None,
               target_platform: str = "", actor_id: Optional[int]) -> dict:
    """Ruanwenjiang preset: compliance gate -> (optional) push draft -> planned task.

    The task table's platform must stay "软文匠" ("Ruanwenjiang") -- the paid second-confirmation endpoint (rwj_channel's
    _rwj_task_or_404), receipt polling and publish-center badges all key off this value. Changing it to "搜狐号" ("Sohu account") makes
    the confirm endpoint 404 and receipts never get picked up -- "charged but no URL back". The real platform name goes into
    channel_meta.target_platform.

    Draft push is off by default (RWJ_PLAN_BIND_MODE=select): a month of scheduling is 150 articles, and Ruanwenjiang
    draft creation is not idempotent and has no cleanup; each reschedule piles up another batch. To reserve early, push manually in the publish center.
    """
    from geo.database import settings
    from geo.services import ruanwenjiang_publisher as rp
    from geo.services.compliance_scanner import publish_gate

    ok, reason = publish_gate(db, doc, topic, hook="publish_paid_media",
                              actor_id=actor_id, actor_name="发文计划预设")
    if not ok:
        db.commit()   # keep a record of the block
        log.warning("[publish-preset] doc=%s blocked by compliance: %s", doc.id, reason)
        return {"platform": rp.platform_name(provider or rp.DEFAULT_PROVIDER),
                "media": resource_title,
                "task_id": None, "action": "compliance_blocked", "reason": reason}

    # Resource type must come from the resource library -- Sohu/Toutiao/Zhihu accounts are Wemedia; hardcoding Website
    # makes verify_resource search the portal media list, miss this resource_id, and fail to publish.
    provider, res_type, res_title_db = _rwj_resource_meta(db, resource_id, provider)
    platform_label = rp.platform_name(provider)
    resource_title = resource_title or res_title_db

    meta: dict[str, Any] = {
        "channel": provider, "provider": provider, "type": res_type,
        "resource_id": resource_id, "resource_title": resource_title,
        "target_platform": target_platform or "",
        "preset": True, "quoted_at": datetime.utcnow().isoformat(),
    }
    last_error = None
    if str(getattr(settings, "RWJ_PLAN_BIND_MODE", "select")).lower() == "draft":
        try:
            result = rp.push_draft(doc, type_=res_type, provider=provider,
                                   resource_id=resource_id, resource_title=resource_title)
            meta.update({
                "resource_id": result.resource_id,
                "resource_title": result.resource_title, "draft_id": result.draft_id,
                "delivery": result.delivery, "quoted_price": result.price,
                "quoted_level": result.level, "dropped_images": result.dropped_images,
            })
            resource_title = result.resource_title
        except Exception as e:  # noqa: BLE001 — network / resource delisted etc.; task keeps the error, ops re-pushes in publish center
            last_error = f"预设自动推草稿失败:{e}"
            log.warning("[publish-preset] doc=%s %s draft push failed: %s", doc.id, provider, e)

    tasks = publish_task_service.create_tasks(
        db, doc, topic,
        [{"platform": platform_label, "media_name": resource_title,
          "channel_type": "external_media", "publish_mode": "paid_media"}],
        assigned_to_user_id=actor_id,
        initial_status="planned", channel_meta=meta,
    )
    if last_error:
        tasks[0].last_error = last_error
    db.commit()
    log.info("[publish-preset] doc=%s -> %s task %s (%s)%s",
             doc.id, platform_label, tasks[0].id, resource_title,
             " [草稿失败]" if last_error else "")
    return {"platform": platform_label, "media": resource_title,
            "task_id": tasks[0].id, "action": "rwj_draft" if not last_error else "rwj_task_only"}
