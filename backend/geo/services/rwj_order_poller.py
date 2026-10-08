"""Meijieku-family receipt polling -- scan paid placement tasks "ordered, awaiting receipt"; close the loop once the article URL arrives.

Covers both Ruanwenjiang + Meijieku: which site each task queries is decided by its own platform
(ruanwenjiang_publisher.provider_of), not global config.

After ordering via Article/{Type}/add the URL is produced asynchronously by media editors and the platform has no callback -- only polling
the article list works. Tasks have two in-flight states:
  - publishing: the order call timed out / result unknown (the endpoint doesn't blindly retry). Order found -> move to pending_url
    or directly published; confirmed not found -> revert to planned so it can be re-initiated.
  - pending_url: ordered successfully, awaiting URL. URL found -> publish_task_service.mark_published
    goes through the shared loop (normalize -> dedupe -> doc set published -> register re-test); media rejection (status=4)
    will never yield a URL, so set failed directly with the rejection reason in last_error so ops can place elsewhere.

Receipt field parsing is centralized in ruanwenjiang_publisher._extract_receipt (measured 2026-08-19 against the real
article list: one row = one outlet; one article placed with multiple outlets = multiple rows with the same title; rows must be matched by outlet).
Mounting: content_scheduler.attach runs it hourly; in prod with no scheduler leader,
crontab calls `python -m geo.services.rwj_order_poller`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import TopicDocPublishTaskORM, TopicGeneratedDocORM
from geo.services import compliance_audit, publish_refund, publish_task_service
from geo.services.ruanwenjiang_publisher import (
    RwjNotConfigured, find_article, provider_of,
)
from geo.services.rwj_client import DEFAULT_PROVIDER, SITES, get_client, site

log = logging.getLogger(__name__)

# Tasks without a receipt after this many days are no longer polled (normal media turnaround is hours to a few days)
MAX_AGE_DAYS = 14


def _channel_meta(task: TopicDocPublishTaskORM) -> dict:
    try:
        meta = json.loads(task.channel_meta_json or "{}")
        return meta if isinstance(meta, dict) else {}
    except ValueError:
        return {}


def reconcile_task(db: Session, task: TopicDocPublishTaskORM, *,
                   actor_id: Optional[int] = None) -> dict:
    """Single-task reconciliation: query the article list -> advance by status. Returns {action, detail} for endpoint/logs.

    Does not commit; follows the caller's transaction.
    """
    doc = db.get(TopicGeneratedDocORM, task.doc_id)
    if doc is None:
        return {"action": "skip", "detail": "稿件不存在"}
    meta = _channel_meta(task)
    type_ = str(meta.get("type") or "Website")
    # Which site to query is set by the task's own platform -- old tasks have platform 「软文匠」 ("Ruanwenjiang"), falling back to rwj
    provider = provider_of(task.platform) or DEFAULT_PROVIDER
    actor_label = f"{site(provider).label}对账"
    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()

    # One article may be placed with multiple outlets at once (same title); this task's outlet must be passed to avoid cross-matching
    res_id = meta.get("resource_id")
    receipt = find_article(
        type_, title, provider=provider,
        resource_id=int(res_id) if res_id else None,
        resource_title=str(meta.get("resource_title") or task.media_name or "") or None,
        # Got the order number at order time -> exact lookup of one row, no more paging and guessing by title
        order_no=str(meta.get("order_no") or "") or None,
        out_order_no=str(meta.get("out_order_no") or "") or None,
    )
    if receipt is None:
        if task.status == "publishing":
            # Order result unknown and not in the list -> treat as not submitted, revert to allow resubmission
            task.status = "planned"
            task.last_error = "下单结果未知,稿件列表中未查到订单,已回退待发布"
            # ★ Must refund. Reverting to planned means the user can publish again -- without a refund the same article is charged twice.
            #   Refund and status change land in the same commit (publish_refund doesn't commit itself).
            refunded = publish_refund.refund_task(db, task, "order_not_found",
                                                  actor_id=actor_id)
            compliance_audit.record(
                db, topic_id=task.topic_id, event_type="paid_publish",
                object_type="publish_task", object_id=task.id,
                actor_type="system" if actor_id is None else "admin", actor_id=actor_id,
                actor_name=actor_label,
                detail={"action": "revert_planned", "reason": "order_not_found",
                        "refunded_cents": refunded},
            )
            return {"action": "revert_planned", "refunded_cents": refunded,
                    "detail": "列表未查到订单,已回退待发布"
                              + (f",已退款 ¥{refunded / 100:.2f}" if refunded else "")}
        return {"action": "none", "detail": "尚未查到回执"}

    # Order found: first persist the order identifier and latest status
    status_text = receipt.get("status_text")
    if (receipt.get("order_no") and meta.get("order_no") != receipt["order_no"]) or \
            meta.get("receipt_status") != status_text:
        if receipt.get("order_no"):
            meta["order_no"] = receipt["order_no"]
        if receipt.get("article_id"):
            meta["article_id"] = receipt["article_id"]   # liveness checks / after-sales appeals query the order by it directly
        meta["receipt_status"] = status_text
        meta["receipt_row_keys"] = receipt.get("raw_keys")
        task.channel_meta_json = json.dumps(meta, ensure_ascii=False)

    # Media rejection (status=4, with rejection_info): no URL will come; set failed so ops can place elsewhere
    if receipt.get("rejected") and not receipt.get("url"):
        task.status = "failed"
        task.last_error = f"媒体拒稿:{status_text or '未说明原因'}"
        # The article won't go out, so no charge
        refunded = publish_refund.refund_task(db, task, "rejected", actor_id=actor_id)
        compliance_audit.record(
            db, topic_id=task.topic_id, event_type="paid_publish",
            object_type="publish_task", object_id=task.id,
            actor_type="system" if actor_id is None else "admin", actor_id=actor_id,
            actor_name=actor_label,
            detail={"action": "rejected", "reason": status_text,
                    "order_no": receipt.get("order_no"), "refunded_cents": refunded},
        )
        return {"action": "rejected", "refunded_cents": refunded,
                "detail": task.last_error
                          + (f",已退款 ¥{refunded / 100:.2f}" if refunded else "")}

    if task.status == "publishing":
        task.status = "pending_url"
        task.last_error = None

    url = receipt.get("url")
    if not url:
        return {"action": "order_found", "detail": f"订单已确认(状态:{status_text or '未知'}),URL 未产出"}

    try:
        publish_task_service.mark_published(
            db, task, url,
            proof={"source": provider, "order_no": receipt.get("order_no"),
                   "status_text": status_text},
            actor_id=actor_id,
        )
    except ValueError as e:
        task.last_error = f"回执 URL 落库失败:{e}"
        return {"action": "url_rejected", "detail": str(e)}
    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="paid_publish",
        object_type="publish_task", object_id=task.id,
        actor_type="system" if actor_id is None else "admin", actor_id=actor_id,
        actor_name=actor_label,
        detail={"action": "published", "url": url, "order_no": receipt.get("order_no")},
    )
    # Tell the user it's published -- paid placement takes hours to days from order to publish; users won't keep watching the page
    try:
        from agents.geo import notify
        from geo.models.ai_telemetry import AiTelemetryTopicORM

        notify.publish_settled(db, task, db.get(AiTelemetryTopicORM, task.topic_id), url=url)
    except Exception:  # noqa: BLE001 — notification is an enhancement; must not break receipt persistence
        log.debug("[poller] published notification failed task=%s", task.id, exc_info=True)
    return {"action": "published", "detail": url}


def tick() -> None:
    """Periodic job entry: scan all in-flight paid placement tasks on both sites."""
    # Only poll sites with configured credentials; one unconfigured site shouldn't drag down the other
    live = [p for p in SITES if get_client(p).configured]
    if not live:
        return
    platforms = [site(p).label for p in live]
    cutoff = datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)
    db = SessionLocal()
    try:
        tasks = (
            db.query(TopicDocPublishTaskORM)
              .filter(
                  TopicDocPublishTaskORM.platform.in_(platforms),
                  TopicDocPublishTaskORM.status.in_(("publishing", "pending_url")),
                  TopicDocPublishTaskORM.created_at >= cutoff,
              ).all()
        )
        if not tasks:
            return
        log.info("[rwj-poller] %d task(s) in flight (%s)", len(tasks), "/".join(platforms))
        for task in tasks:
            try:
                out = reconcile_task(db, task)
                db.commit()
                if out["action"] != "none":
                    log.info("[rwj-poller] task=%s %s: %s", task.id, out["action"], out["detail"])
            except RwjNotConfigured:
                # A single site going offline only skips that site; don't stop the other site's in-flight tasks
                db.rollback()
                log.warning("[rwj-poller] task=%s site not configured, skipping", task.id)
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("[rwj-poller] reconcile failed task=%s", task.id)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tick()
