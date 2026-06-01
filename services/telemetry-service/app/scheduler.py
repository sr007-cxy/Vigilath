"""Asyncio cron loop — 7 天滚动 drip 跑批 + 每周周报.

旧行为(2026-06 前):每周一把梭,一个 tick 把所有 enabled topic 的 queries×engines
全量并发跑完。对 browser 抓取的引擎站点是集中击穿,且没有"每引擎每天最多 N 条"的闸。

新行为(drip):
- 一个**周期 = 7 天**,锚点是 SCHEDULER_CRON_WEEKDAY/HOUR/MINUTE(默认北京周一 02:05)。
- scheduler 每 TELEMETRY_DRIP_TICK_MINUTES 分钟(默认 60)tick 一次,每次只跑一小批,
  靠 tick 频率天然把负载摊到 24 小时。
- 每个引擎有**每日上限**(TELEMETRY_ENGINE_DAILY_CAP,默认 50;可按引擎覆盖),
  跨所有 topic 全局计数(瓶颈是引擎账号池,不是单 topic)。豆包走 API 不限流。
- 本周期已成功跑过的 (engine,query) 不再重跑;一个周期内覆盖完整题库后**跑完即停**,
  等到下一个锚点(满 7 天)再开新周期。
- 一个周期内**复用同一个 RunORM**(报表 / 周报语义保持"1 周期 1 run")。

env:
- SCHEDULER_CRON_WEEKDAY/HOUR/MINUTE/TIMEZONE_OFFSET  周期锚点(默认 周一 02:05 UTC+8)
- TELEMETRY_DRIP_TICK_MINUTES        drip tick 间隔分钟,默认 60
- TELEMETRY_ENGINE_DAILY_CAP         每引擎每日上限(全局),默认 50
- TELEMETRY_ENGINE_DAILY_CAP_<ENGINE> 单引擎覆盖,如 TELEMETRY_ENGINE_DAILY_CAP_YUANBAO=27
- TELEMETRY_UNCAPPED_ENGINES         不限流的引擎(逗号分隔),默认 doubao
- BRIEFING_CRON_*                    周报 cron(不变)
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .runner import run_topic_once
from .briefings import generate_all_briefings_for_week
from .storage import (
    db_session, list_enabled_topics, parse_topic,
    TopicORM, RunORM, start_run, finish_run,
)

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 60

# 周期锚点(沿用旧 env 名)
CRON_WEEKDAY = int(os.environ.get("SCHEDULER_CRON_WEEKDAY", "0"))  # Mon=0..Sun=6
CRON_HOUR_LOCAL = int(os.environ.get("SCHEDULER_CRON_HOUR", "2"))
CRON_MINUTE = int(os.environ.get("SCHEDULER_CRON_MINUTE", "5"))
TZ_OFFSET = int(os.environ.get("SCHEDULER_TIMEZONE_OFFSET", "8"))

# drip 配置
DRIP_TICK_MINUTES = int(os.environ.get("TELEMETRY_DRIP_TICK_MINUTES", "60"))
ENGINE_DAILY_CAP = int(os.environ.get("TELEMETRY_ENGINE_DAILY_CAP", "50"))
UNCAPPED_ENGINES = {
    e.strip() for e in os.environ.get("TELEMETRY_UNCAPPED_ENGINES", "doubao").split(",") if e.strip()
}

# v1.2 周报 cron — 默认本地周一 09:00
BRIEFING_CRON_WEEKDAY = int(os.environ.get("BRIEFING_CRON_WEEKDAY", "0"))  # Mon=0
BRIEFING_CRON_HOUR_LOCAL = int(os.environ.get("BRIEFING_CRON_HOUR", "9"))
BRIEFING_CRON_MINUTE = int(os.environ.get("BRIEFING_CRON_MINUTE", "0"))
TRIGGER_WINDOW_MIN = 2   # 周报触发窗口 2 分钟,容忍 poll 抖动

_LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET))

# 周报每周只跑一次 — 记上次触发的本地周一日期,避免 2 分钟窗口期重复触发
_last_briefing_local_monday: object | None = None
# 上次 drip tick 的 UTC 时间,用来按 DRIP_TICK_MINUTES 节流
_last_drip_at: datetime | None = None


# ── 时间换算(DB 存的是 naive UTC)──────────────────────────────────

def _to_utc_naive(dt_local_aware: datetime) -> datetime:
    return dt_local_aware.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle_start_utc(now_utc: datetime) -> datetime:
    """不晚于当前时间的最近一个锚点(weekday hh:mm),作为本周期起点(UTC naive)."""
    local = now_utc.astimezone(_LOCAL_TZ)
    anchor = local.replace(hour=CRON_HOUR_LOCAL, minute=CRON_MINUTE, second=0, microsecond=0)
    days_since = (local.weekday() - CRON_WEEKDAY) % 7
    anchor = anchor - timedelta(days=days_since)
    if anchor > local:          # 今天是锚点 weekday 但还没到锚点时刻 → 回退一周
        anchor -= timedelta(days=7)
    return _to_utc_naive(anchor)


def _day_start_utc(now_utc: datetime) -> datetime:
    """本地今天 0 点对应的 UTC naive,用于"今日已用额度"计数."""
    local = now_utc.astimezone(_LOCAL_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return _to_utc_naive(midnight)


def _ticks_left_today(now_utc: datetime) -> int:
    """到本地午夜还剩几个 drip tick(>=1),用于把当日剩余额度平摊到剩余 tick."""
    local = now_utc.astimezone(_LOCAL_TZ)
    next_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    mins_left = (next_midnight - local).total_seconds() / 60.0
    return max(1, math.ceil(mins_left / DRIP_TICK_MINUTES))


def _engine_daily_cap(engine: str) -> float:
    """该引擎每日上限;不限流引擎返回 inf."""
    if engine in UNCAPPED_ENGINES:
        return math.inf
    ov = os.environ.get(f"TELEMETRY_ENGINE_DAILY_CAP_{engine.upper()}")
    return int(ov) if ov else ENGINE_DAILY_CAP


# ── drip 主逻辑 ──────────────────────────────────────────────────

def _drip_due(now_utc: datetime) -> bool:
    global _last_drip_at
    if _last_drip_at is None:
        return True
    elapsed = (now_utc - _last_drip_at).total_seconds()
    # 留半个 poll 周期的余量,避免错过整点 tick
    return elapsed >= DRIP_TICK_MINUTES * 60 - POLL_INTERVAL_SEC / 2


async def _run_drip(now_utc: datetime) -> None:
    cycle_start = _cycle_start_utc(now_utc)
    day_start = _day_start_utc(now_utc)
    ticks_left = _ticks_left_today(now_utc)

    # 0) 关掉上个周期遗留的 running run(起点早于本周期锚点)
    with db_session() as s:
        for r in (s.query(RunORM)
                   .filter(RunORM.status == "running", RunORM.started_at < cycle_start).all()):
            finish_run(s, r.id, "success", error="cycle ended")

    # 1) 今日每引擎全局已用条数(success+fail 都占额度 —— "每天最多问 N 条"是指发起数)
    with db_session() as s:
        rows = s.execute(text(
            "SELECT engine, COUNT(*) n FROM ai_telemetry_responses "
            "WHERE created_at >= :ds GROUP BY engine"
        ), {"ds": day_start}).all()
    used_today = {r.engine: r.n for r in rows}

    # 2) 收集本轮要考虑的 topic + 引擎集合
    with db_session() as s:
        topic_snaps = []
        for t in list_enabled_topics(s):
            queries, engines = parse_topic(t)
            if queries and engines:
                topic_snaps.append((t.id, t.name, queries, engines))

    # 3) 本 tick 每引擎全局预算 = ceil(今日剩余额度 / 今日剩余 tick 数)
    tick_budget: dict[str, float] = {}
    for _, _, _, engines in topic_snaps:
        for e in engines:
            if e in tick_budget:
                continue
            cap = _engine_daily_cap(e)
            if cap == math.inf:
                tick_budget[e] = math.inf
            else:
                remaining = max(0, cap - used_today.get(e, 0))
                tick_budget[e] = math.ceil(remaining / ticks_left) if remaining > 0 else 0

    log.info("[drip] cycle_start=%s day_start=%s ticks_left=%d budget=%s",
             cycle_start, day_start, ticks_left,
             {k: ("inf" if v == math.inf else v) for k, v in tick_budget.items()})

    # 4) 逐 topic 切片跑批
    for tid, name, queries, engines in topic_snaps:
        with db_session() as s:
            succeeded = {(r.engine, r.query) for r in s.execute(text(
                "SELECT DISTINCT engine, query FROM ai_telemetry_responses "
                "WHERE topic_id=:tid AND (error IS NULL OR error='') AND created_at >= :cs"
            ), {"tid": tid, "cs": cycle_start}).all()}
            attempted_today = {(r.engine, r.query) for r in s.execute(text(
                "SELECT DISTINCT engine, query FROM ai_telemetry_responses "
                "WHERE topic_id=:tid AND created_at >= :ds"
            ), {"tid": tid, "ds": day_start}).all()}
            open_run = (s.query(RunORM)
                        .filter(RunORM.topic_id == tid, RunORM.status == "running",
                                RunORM.started_at >= cycle_start)
                        .order_by(RunORM.id.desc()).first())
            open_run_id = open_run.id if open_run else None

        pending = [(e, q) for q in queries for e in engines if (e, q) not in succeeded]
        if not pending:
            # 本周期题库已覆盖完 → 收尾,跑完即停
            if open_run_id is not None:
                with db_session() as s:
                    finish_run(s, open_run_id, "success")
                log.info("[drip] topic %d (%s) cycle complete, run %d finished", tid, name, open_run_id)
            continue

        # 本周期还没建 run → 建一个,整周期复用
        run_id = open_run_id
        if run_id is None:
            with db_session() as s:
                run_id = start_run(s, tid).id

        # 优先跑"今日还没试过"的 (未试过 -> False=0 排前面),再轮到今日已试过的(重试)
        pending.sort(key=lambda eq: eq in attempted_today)

        slice_pairs: list[tuple[str, str]] = []
        for e, q in pending:
            b = tick_budget.get(e, 0)
            if b == math.inf:
                slice_pairs.append((e, q))
            elif b > 0:
                slice_pairs.append((e, q))
                tick_budget[e] = b - 1

        if not slice_pairs:
            continue

        with db_session() as s:
            t = s.get(TopicORM, tid)
            if t is None or not t.enabled:
                continue
            s.expunge(t)
        try:
            await run_topic_once(t, existing_run_id=run_id, pairs=set(slice_pairs), finalize=False)
            log.info("[drip] topic %d (%s) run %d: dispatched %d pairs (pending was %d)",
                     tid, name, run_id, len(slice_pairs), len(pending))
        except Exception as e:  # noqa: BLE001
            log.exception("[drip] topic %d run failed: %s", tid, e)


# ── loop ─────────────────────────────────────────────────────────

async def scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info(
        "scheduler started: drip every %dmin, cycle anchor weekday=%d %02d:%02d UTC+%d, "
        "daily_cap=%d uncapped=%s poll=%ds",
        DRIP_TICK_MINUTES, CRON_WEEKDAY, CRON_HOUR_LOCAL, CRON_MINUTE, TZ_OFFSET,
        ENGINE_DAILY_CAP, sorted(UNCAPPED_ENGINES), POLL_INTERVAL_SEC,
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
        import asyncio as _a
        await _a.to_thread(generate_all_briefings_for_week)
    except Exception as e:  # noqa: BLE001
        log.exception("weekly briefings failed: %s", e)


async def _tick() -> None:
    global _last_drip_at
    now = datetime.now(timezone.utc)
    # 周报判定每个 poll 都跑(便宜)
    await _maybe_run_briefings(now)
    # drip 按 DRIP_TICK_MINUTES 节流
    if _drip_due(now):
        _last_drip_at = now
        await _run_drip(now)
