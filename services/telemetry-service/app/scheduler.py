"""Asyncio cron loop — 每天跑一次所有 enabled topics.

策略:
- 每分钟扫一次,匹配「该 topic 的目标小时 = 当前 UTC 小时」且今天还没跑过
- topic 的目标小时 = hash(topic.id) % 24,自然错峰
- 单 worker(--workers 1)→ 不需要分布式锁
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .runner import run_topic_once
from .storage import db_session, list_enabled_topics, TopicORM

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 60


def _topic_target_hour(topic_id: int) -> int:
    return topic_id % 24


def _should_run_now(topic: TopicORM, now: datetime) -> bool:
    if _topic_target_hour(topic.id) != now.hour:
        return False
    # 同一天同一话题只跑一次
    last = topic.last_run_at
    if last is None:
        return True
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return last.date() != now.date()


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info("scheduler started, polling every %ds", POLL_INTERVAL_SEC)
    while not stop_event.is_set():
        try:
            await _tick()
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler tick failed: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler stopped")


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    with db_session() as s:
        topics = list_enabled_topics(s)
        due = [t for t in topics if _should_run_now(t, now)]
        # detach 出 session,避免在 async 跑批期间 session 已关
        due_snapshot = [(t.id, t.name) for t in due]

    for topic_id, name in due_snapshot:
        log.info("scheduler launching topic %d (%s)", topic_id, name)
        try:
            with db_session() as s:
                t = s.get(TopicORM, topic_id)
                if t is None or not t.enabled:
                    continue
                # 把 ORM 摘出来,避免 await 期间 session 失效
                topic_copy = t
                # detach: make_transient 也行,这里直接传 detached 实例
                s.expunge(topic_copy)
            await run_topic_once(topic_copy)
        except Exception as e:  # noqa: BLE001
            log.exception("topic %d run failed: %s", topic_id, e)
