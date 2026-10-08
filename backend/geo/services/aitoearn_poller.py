"""aitoearn publish flow reconciliation poller -- scans "submitted, awaiting link" distribution tasks and completes the backfill.

After a flow is created each platform publishes asynchronously, and the post link (workLink) is fetched asynchronously by aitoearn,
with no callback -- poll GET /flows/{flowId} (our external flow ID; details include each
item's status / workLink / linkStatus / errorMsg). Tasks have two in-flight states:
  - publishing: create_flow timed out/result unknown (the endpoint does not blindly retry). Flow found -> move to
    pending_url; confirmed not found (after cooldown) -> roll back to planned to allow re-submission.
  - pending_url: flow landed, waiting for the link. Once workLink arrives ->
    publish_task_service.mark_published completes link backfill, attribution and re-test registration directly.
    linkStatus=failed but already sent (has platformWorkId) -> stay in pending_url and prompt
    manual backfill; never set published without a URL.

Mounting: content_scheduler.attach runs it hourly at :25; on prod without a scheduler leader,
crontab calls `python -m geo.services.aitoearn_poller` (following the rwj_order_poller precedent).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.services import compliance_audit, publish_task_service
from geo.services.aitoearn_client import (
    AitoearnApiError, AitoearnConfigError, configured, get_client,
)
from geo.services.aitoearn_publisher import CHANNEL, map_flow_task

log = logging.getLogger(__name__)

# Tasks without a link after this many days are no longer polled (normal platform publishing takes minutes to hours)
MAX_AGE_DAYS = 14
# Rollback cooldown when a flow is not found in publishing state: wait at least this long after submission before asserting "not submitted"
REVERT_COOLDOWN_MIN = 10


def _channel_meta(task: TopicDocPublishTaskORM) -> dict:
    try:
        meta = json.loads(task.channel_meta_json or "{}")
        return meta if isinstance(meta, dict) else {}
    except ValueError:
        return {}


def is_aitoearn_task(task: TopicDocPublishTaskORM) -> bool:
    return _channel_meta(task).get("channel") == CHANNEL


def _audit(db: Session, task: TopicDocPublishTaskORM, *, actor_id: Optional[int],
           detail: dict) -> None:
    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="channel_publish",
        object_type="publish_task", object_id=task.id,
        actor_type="system" if actor_id is None else "admin", actor_id=actor_id,
        actor_name="aitoearn对账",
        detail=detail,
    )


def _past_cooldown(meta: dict) -> bool:
    started = meta.get("publish_started_at")
    if not started:
        return True
    try:
        t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.utcnow() - t0.replace(tzinfo=None) \
        > timedelta(minutes=REVERT_COOLDOWN_MIN)


async def reconcile_task(db: Session, task: TopicDocPublishTaskORM, *,
                         actor_id: Optional[int] = None) -> dict:
    """Reconcile one task: fetch flow details -> advance by status. Returns {action, detail} for the endpoint/logs.

    Does not commit; follows the caller's transaction.
    """
    meta = _channel_meta(task)
    flow_id = str(meta.get("flow_id") or "")
    if not flow_id:
        if task.status == "publishing":
            task.status = "planned"
            task.last_error = "任务缺少 flow_id(发布未真正发起),已回退待发布"
            return {"action": "revert_planned", "detail": task.last_error}
        return {"action": "skip", "detail": "任务缺少 flow_id"}

    try:
        flow = await get_client().get_flow(flow_id)
    except AitoearnApiError as e:
        # Handle conservatively when "flow does not exist" (create_flow never landed) cannot be told apart from other business errors:
        # only roll back when publishing + past the cooldown, otherwise wait for the next round
        if task.status == "publishing" and _past_cooldown(meta):
            task.status = "planned"
            task.last_error = f"发布结果未知且 flow 查询失败({e}),已回退待发布"
            _audit(db, task, actor_id=actor_id,
                   detail={"action": "revert_planned", "flow_id": flow_id,
                           "reason": str(e)})
            return {"action": "revert_planned", "detail": task.last_error}
        return {"action": "none", "detail": f"flow 查询失败:{e}"}

    rows = flow.get("tasks") if isinstance(flow, dict) else None
    rec = rows[0] if isinstance(rows, list) and rows else None
    if rec is None:
        if task.status == "publishing" and _past_cooldown(meta):
            task.status = "planned"
            task.last_error = "flow 详情为空(发布未落地),已回退待发布"
            _audit(db, task, actor_id=actor_id,
                   detail={"action": "revert_planned", "flow_id": flow_id,
                           "reason": "flow_empty"})
            return {"action": "revert_planned", "detail": task.last_error}
        return {"action": "none", "detail": "flow 详情为空,等待下一轮"}

    # Flow landed: publishing -> pending_url, and record the platform post ID along the way
    changed = False
    if rec.get("platformWorkId") and meta.get("platform_work_id") != rec["platformWorkId"]:
        meta["platform_work_id"] = str(rec["platformWorkId"])
        changed = True
    if task.status == "publishing":
        task.status = "pending_url"
        task.last_error = None
        changed = True
    if changed:
        task.channel_meta_json = json.dumps(meta, ensure_ascii=False)

    action, url, detail = map_flow_task(rec)
    if action == "publish":
        try:
            publish_task_service.mark_published(
                db, task, url,
                proof={"source": CHANNEL, "flow_id": flow_id,
                       "platform_work_id": rec.get("platformWorkId")},
                actor_id=actor_id,
            )
        except ValueError as e:
            task.last_error = f"作品链接落库失败:{e}"
            return {"action": "url_rejected", "detail": str(e)}
        _audit(db, task, actor_id=actor_id,
               detail={"action": "published", "url": url, "flow_id": flow_id})
        return {"action": "published", "detail": url}
    if action == "failed":
        task.status = "failed"
        task.last_error = detail
        _audit(db, task, actor_id=actor_id,
               detail={"action": "publish_failed", "flow_id": flow_id,
                       "reason": detail})
        return {"action": "failed", "detail": detail}
    if action == "link_failed":
        task.last_error = detail    # Stay in pending_url; manual backfill goes through submit_url
        return {"action": "link_failed", "detail": detail}
    return {"action": "none", "detail": detail}


def tick() -> None:
    """Periodic job entry: scan all in-flight aitoearn tasks."""
    if not configured():
        return
    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)
    db = SessionLocal()
    try:
        tasks = (
            db.query(TopicDocPublishTaskORM)
              .filter(
                  TopicDocPublishTaskORM.status.in_(("publishing", "pending_url")),
                  TopicDocPublishTaskORM.channel_meta_json.like("%aitoearn%"),
                  TopicDocPublishTaskORM.created_at >= cutoff,
              ).all()
        )
        tasks = [t for t in tasks if is_aitoearn_task(t)]
        if not tasks:
            return
        log.info("[aitoearn-poller] %d task(s) in flight", len(tasks))
        for task in tasks:
            try:
                out = asyncio.run(reconcile_task(db, task))
                db.commit()
                if out["action"] != "none":
                    log.info("[aitoearn-poller] task=%s %s: %s",
                             task.id, out["action"], out["detail"])
            except AitoearnConfigError:
                db.rollback()
                return
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("[aitoearn-poller] reconcile failed task=%s", task.id)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tick()
