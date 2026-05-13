"""Asyncio cron loop — 每天在固定本地时间跑一次所有 enabled topics.

默认 北京 02:05(UTC+8 → 即 UTC 18:05 前一天)。可通过 env 调:
- SCHEDULER_CRON_HOUR      (本地小时,默认 2)
- SCHEDULER_CRON_MINUTE    (本地分钟,默认 5)
- SCHEDULER_TIMEZONE_OFFSET (本地 vs UTC 的小时差,默认 8 = 北京)

策略:
- 每分钟扫一次,匹配「当前 UTC 时间 ∈ [target_utc, target_utc + 2min)」窗口
- 同一话题同一本地日期只跑一次(防止 2 分钟窗口期内重复触发)
- 单 worker(--workers 1)→ 不需要分布式锁
- 多个 topic 同时触发 → runner 内部有并发上限(TELEMETRY_MAX_CONCURRENT=3)兜底
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from .runner import run_topic_once
from .briefings import generate_all_briefings_for_week
from .storage import db_session, list_enabled_topics, TopicORM

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 60

CRON_HOUR_LOCAL = int(os.environ.get("SCHEDULER_CRON_HOUR", "2"))
CRON_MINUTE = int(os.environ.get("SCHEDULER_CRON_MINUTE", "5"))
TZ_OFFSET = int(os.environ.get("SCHEDULER_TIMEZONE_OFFSET", "8"))
TRIGGER_WINDOW_MIN = 2   # 触发窗口 2 分钟,容忍 poll 抖动

# v1.2 周报 cron — 默认本地周一 09:00
BRIEFING_CRON_WEEKDAY = int(os.environ.get("BRIEFING_CRON_WEEKDAY", "0"))  # Mon=0
BRIEFING_CRON_HOUR_LOCAL = int(os.environ.get("BRIEFING_CRON_HOUR", "9"))
BRIEFING_CRON_MINUTE = int(os.environ.get("BRIEFING_CRON_MINUTE", "0"))

# 转 UTC 等价时间(可能为负 → wrap)
_HOUR_UTC = (CRON_HOUR_LOCAL - TZ_OFFSET) % 24
_BRIEFING_HOUR_UTC = (BRIEFING_CRON_HOUR_LOCAL - TZ_OFFSET) % 24
_LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET))

# 周报每周只跑一次 — 记上次触发的本地周一日期,避免 2 分钟窗口期重复触发
_last_briefing_local_monday: object | None = None


def _in_trigger_window(now_utc: datetime) -> bool:
    """当前 UTC 时间是否在 [target, target + TRIGGER_WINDOW_MIN) 内."""
    if now_utc.hour != _HOUR_UTC:
        return False
    return CRON_MINUTE <= now_utc.minute < (CRON_MINUTE + TRIGGER_WINDOW_MIN)


def _local_date(dt_utc: datetime | None) -> object | None:
    if dt_utc is None:
        return None
    dt = dt_utc if dt_utc.tzinfo else dt_utc.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ).date()


def _should_run_now(topic: TopicORM, now_utc: datetime) -> bool:
    if not _in_trigger_window(now_utc):
        return False
    today_local = now_utc.astimezone(_LOCAL_TZ).date()
    last_local = _local_date(topic.last_run_at)
    return last_local != today_local


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info(
        "scheduler started: fire at %02d:%02d UTC+%d (= %02d:%02d UTC) daily, poll=%ds",
        CRON_HOUR_LOCAL, CRON_MINUTE, TZ_OFFSET, _HOUR_UTC, CRON_MINUTE, POLL_INTERVAL_SEC,
    )
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


def _in_briefing_window(now_utc: datetime) -> bool:
    """周报触发窗口 — 当前 UTC 时间是否在配置的周一 09:00 北京 ± 2min 内."""
    local = now_utc.astimezone(_LOCAL_TZ)
    if local.weekday() != BRIEFING_CRON_WEEKDAY:
        return False
    if local.hour != BRIEFING_CRON_HOUR_LOCAL:
        return False
    return BRIEFING_CRON_MINUTE <= local.minute < (BRIEFING_CRON_MINUTE + TRIGGER_WINDOW_MIN)


async def _maybe_run_briefings(now_utc: datetime) -> None:
    """v1.2 — 每周一 09:00 北京触发,生成所有 enabled topic 的上周周报."""
    global _last_briefing_local_monday
    if not _in_briefing_window(now_utc):
        return
    local = now_utc.astimezone(_LOCAL_TZ)
    this_monday = (local - timedelta(days=local.weekday())).date()
    if _last_briefing_local_monday == this_monday:
        return  # 本周已跑过
    _last_briefing_local_monday = this_monday
    log.info("scheduler firing weekly briefings (week starting %s)", this_monday)
    try:
        # 阻塞在 thread 里跑 — generate_all_briefings_for_week 是同步函数 + LLM 调用
        import asyncio as _a
        await _a.to_thread(generate_all_briefings_for_week)
    except Exception as e:  # noqa: BLE001
        log.exception("weekly briefings failed: %s", e)


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    with db_session() as s:
        topics = list_enabled_topics(s)
        due = [t for t in topics if _should_run_now(t, now)]
        due_snapshot = [(t.id, t.name) for t in due]

    for topic_id, name in due_snapshot:
        log.info("scheduler launching topic %d (%s)", topic_id, name)
        try:
            with db_session() as s:
                t = s.get(TopicORM, topic_id)
                if t is None or not t.enabled:
                    continue
                topic_copy = t
                s.expunge(topic_copy)
            await run_topic_once(topic_copy)
        except Exception as e:  # noqa: BLE001
            log.exception("topic %d run failed: %s", topic_id, e)

    # v1.2 周报触发(独立于日跑批 cron)
    await _maybe_run_briefings(now)
