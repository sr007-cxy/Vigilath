"""Phase D.1 — 内容自动生成定时任务.

每小时整点跑一次,扫所有 auto_generate_enabled=True 的 topic,看本小时是否匹配
其 auto_generate_time(只比较 HH 部分,粒度小时;时区 Asia/Shanghai)。匹配且
当天还没生成过就触发 content_generator.schedule_generation()。

幂等保护:用 auto_generate_last_run_at 当 "今天已跑标记"。今天比上一次跑批日期晚就触发;
所以同一天即便 cron 触发 24 次也只跑一次。

由 sentiment_scheduler 共用的 APScheduler 进程托管 — 也就是说只有 GEO_SCHEDULER_LEADER=1
的实例才会跑这条 job.

环境变量:
    GEO_CONTENT_SCHED_TZ       默认 Asia/Shanghai
    GEO_CONTENT_SCHED_MINUTE   每小时第几分钟触发,默认 7(避开 sentinel 的 :05)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.services.content_generator import schedule_generation

log = logging.getLogger(__name__)

TIMEZONE = os.environ.get("GEO_CONTENT_SCHED_TZ", "Asia/Shanghai")
START_MINUTE = int(os.environ.get("GEO_CONTENT_SCHED_MINUTE", "7"))


def _now_local() -> datetime:
    """返回 Asia/Shanghai 当前时间(naive)。

    SQLite 把 datetime 存 naive,这里也用 naive 时间和库里 last_run_at 比较;
    但 last_run_at 存的是 UTC,所以转一次。
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:  # noqa: BLE001
        # 兜底:UTC+8
        return datetime.utcnow() + timedelta(hours=8)


def _utc_today_start_in_tz() -> datetime:
    """返回今天本地 0 点对应的 UTC naive datetime,用于判断 last_run_at 是不是"今天跑过"."""
    local = _now_local()
    # 本地 0 点
    if local.tzinfo:
        local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_midnight = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        utc_midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8))
    return utc_midnight


def hourly_tick() -> None:
    """APScheduler 每小时触发的入口."""
    db: Session = SessionLocal()
    try:
        now = _now_local()
        today_start_utc = _utc_today_start_in_tz()
        log.info("[content-cron] tick hour=%02d", now.hour)
        rows = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.auto_generate_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        triggered = 0
        for t in rows:
            try:
                hh, _, _ = (t.auto_generate_time or "09:00").partition(":")
                target_hour = int(hh)
            except ValueError:
                continue
            if target_hour != now.hour:
                continue
            # 今天跑过就跳过
            if t.auto_generate_last_run_at and t.auto_generate_last_run_at >= today_start_utc:
                continue
            count = max(1, min(20, int(t.auto_generate_count or 3)))
            log.info("[content-cron] firing topic=%d count=%d", t.id, count)
            schedule_generation(
                topic_id=t.id, max_docs=count, mark_auto_run=True,
            )
            triggered += 1
        log.info("[content-cron] %d topics triggered", triggered)
    finally:
        db.close()


_scheduler = None


def attach(scheduler) -> None:
    """挂载到外部 APScheduler 实例(供其它进程内 scheduler 共用)."""
    scheduler.add_job(
        hourly_tick,
        trigger="cron", minute=START_MINUTE,
        id="content_auto_generate_hourly",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=1800,
        max_instances=1,
    )
    log.info("[content-cron] attached: every hour at :%02d", START_MINUTE)


def start() -> None:
    """独立启动一个 APScheduler 实例,只挂 content cron.

    跟 sentiment_scheduler 解耦 — 测试 / 生产环境里舆情已经有独立的
    geo-sentinel.service 跑,backend 进程不能再叠加 sentiment hourly job,
    否则任务会重复执行。所以内容生成走自己的 leader env。
    """
    global _scheduler
    if _scheduler is not None:
        log.warning("[content-scheduler] already started")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.error("[content-scheduler] APScheduler not installed; "
                  "run: pip install 'apscheduler[sqlalchemy]'")
        return
    _scheduler = BackgroundScheduler(timezone=TIMEZONE)
    attach(_scheduler)
    _scheduler.start()
    log.info("[content-scheduler] started independently (tz=%s)", TIMEZONE)


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("[content-scheduler] stopped")


def maybe_start_from_env() -> None:
    """在 main.py 的 startup hook 里调.GEO_CONTENT_SCHEDULER_LEADER=1 才起."""
    if os.environ.get("GEO_CONTENT_SCHEDULER_LEADER", "0") == "1":
        log.info("[content-scheduler] leader mode, starting…")
        start()
    else:
        log.info("[content-scheduler] not leader, skip")
