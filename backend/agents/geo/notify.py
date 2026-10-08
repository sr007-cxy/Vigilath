"""Proactive notifications — "the article you published three weeks ago got cited by DeepSeek today".

**Why this matters more than it looks**: across the whole product chain, the one moment users go "oh" is a citation hit.
And users **won't come back on their own to ask** "did last month's article get cited" — without a push, this aha moment never happens,
and renewal has no trigger.

The `agent_notifications` table and read endpoint (`GET /api/agent/notifications`) existed long ago,
but **there was never a writer** — `methods.deliver_notification` was a `NotImplementedError`.
So this module fills in the only missing segment of the chain.

Three rules:

  1. **Dedup key pinned to (account, type, object, Beijing date)**: one draft cited by three engines shouldn't push three times,
     and a retest task running twice a day shouldn't push twice.
  2. **Do not disturb**: within `AGENT_NOTIFY_QUIET_HOURS` (default 22-8) nothing is delivered — but it's **still persisted to DB**,
     so the user sees it when opening the chat window the next day. "Muted" doesn't mean "dropped".
  3. **Push failures don't raise**: notifications are an enhancement; an undeliverable reminder must not crash the URL-backfill transaction.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_BJ = timezone(timedelta(hours=8))


def _quiet_hours() -> tuple[int, int]:
    raw = (os.environ.get("AGENT_NOTIFY_QUIET_HOURS", "22-8") or "").strip()
    try:
        a, b = raw.split("-")
        return int(a) % 24, int(b) % 24
    except (ValueError, AttributeError):
        return 22, 8


def in_quiet_hours(now: Optional[datetime] = None) -> bool:
    start, end = _quiet_hours()
    if start == end:
        return False
    h = (now or datetime.now(_BJ)).astimezone(_BJ).hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def enabled() -> bool:
    return (os.environ.get("AGENT_NOTIFY_ENABLED", "1") or "").strip().lower() \
        not in ("0", "false", "no")


def dedup_key(account_id: int, kind: str, object_id: str) -> str:
    day = datetime.now(_BJ).strftime("%Y-%m-%d")
    return f"{account_id}:{kind}:{object_id}:{day}"[:128]


def push(db: Session, account_id: int, *, title: str, body: str,
         kind: str, object_id: str) -> Optional[int]:
    """Persist a notification. Returns the notification id; returns None on a dedup hit. **Doesn't commit**; follows the caller's transaction.

    Persisting and "whether to push now" are separate: during quiet hours it's still persisted, just not delivered immediately —
    the user still sees it when opening the chat window the next day.
    """
    if not enabled() or not account_id:
        return None
    from geo.models.agent import AgentNotificationORM

    key = dedup_key(account_id, kind, object_id)
    try:
        hit = (db.query(AgentNotificationORM.id)
               .filter(AgentNotificationORM.dedup_key == key).first())
        if hit is not None:
            return None
        row = AgentNotificationORM(
            account_id=int(account_id), title=title[:255], body=body,
            dedup_key=key, created_at=datetime.utcnow())
        db.add(row)
        db.flush()
        log.info("[notify] account=%s %s object=%s%s",
                 account_id, kind, object_id,
                 "(免打扰时段,只落库)" if in_quiet_hours() else "")
        return row.id
    except Exception:  # noqa: BLE001 — notifications are an enhancement; never drag down the caller's transaction
        log.warning("[notify] failed to persist account=%s kind=%s", account_id, kind, exc_info=True)
        return None


# ── Specific events ─────────────────────────────────────────────────────
def cited_by_engine(db: Session, doc, topic, engines: list[str],
                    query: Optional[str] = None) -> Optional[int]:
    """Citation hit — the product's single aha moment, and the renewal trigger.

    The copy must state three things: which article, cited by whom, and while answering which question. Missing any one isn't convincing
    (a subjectless notification like "your article was cited" leaves users not knowing what to do).
    """
    if not engines:
        return None
    title = (doc.title or doc.source_query_text or f"稿件 #{doc.id}").strip()
    who = "、".join(dict.fromkeys(engines))[:60]
    body = f"你发的《{title}》被 {who} 引用了。"
    if query:
        body += f"引用发生在回答「{query}」的时候。"
    body += "可以在报告中心看这篇的被引明细。"
    return push(db, getattr(topic, "user_id", 0), title="有文章被 AI 引用了",
                body=body, kind="cited", object_id=str(doc.id))


def publish_settled(db: Session, task, topic, *, url: str) -> Optional[int]:
    """Placement published: got the real link."""
    return push(db, getattr(topic, "user_id", 0), title="投放已出稿",
                body=f"《{task.media_name or '付费媒体'}》那一篇已经出稿:{url}",
                kind="published", object_id=str(task.id))


def refunded(db: Session, task, topic, *, cents: int, reason_text: str) -> Optional[int]:
    """Refund received — when money comes back the user must be told, otherwise seeing only "placement failed" they'd think the money was wasted."""
    if cents <= 0:
        return None
    return push(db, getattr(topic, "user_id", 0), title="投放退款已到账",
                body=f"「{task.media_name or '付费媒体'}」这一单{reason_text},"
                     f"已退回 ¥{cents / 100:.2f} 到你的账户余额。",
                kind="refund", object_id=str(task.id))
