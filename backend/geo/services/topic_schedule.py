"""Topic batch run tiers -- the single deciding function.

"Should this topic run now, and on what cycle" has exactly one implementation here; scheduler / API responses /
admin badges share the same semantics. This is an institutional constraint: the root problem with TELEMETRY_DRIP_ONLY_TOPICS wasn't
"one more switch", but that **the switch was invisible in the admin**. So any new scheduling filter must be expressible
by effective_state here, or it may not be added.

telemetry-service has an isomorphic copy (app/schedule.py); both sides must be changed together.
Why not share code: the two services are deployed independently with different dependency trees (same trade-off as
the three same-source geo_checker copies described in CLAUDE.md).

Field semantics:
    schedule_tier   daily one round per day / weekly one round per week / off don't run
    paused_until    non-empty and not yet expired -> skip this round, auto-resume when due (tier kept, config not lost)
    enabled         derived cache of tier: off <-> False, daily/weekly <-> True
    cycle_anchor_weekday / cycle_anchor_hour
                    topic-level override of the cycle anchor (0=Monday..6=Sunday / 0-23 local hour);
                    NULL = use global env (SCHEDULER_CRON_WEEKDAY / _HOUR).
                    Actual scheduling by anchor is telemetry's cycle_start_for();
                    this side only validates the valid range and displays.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

TIER_DAILY = "daily"
TIER_WEEKLY = "weekly"
TIER_OFF = "off"
VALID_TIERS = (TIER_DAILY, TIER_WEEKLY, TIER_OFF)


@dataclass
class ScheduleDecision:
    should_run: bool
    tier: str                       # daily / weekly / off (normalized)
    reason: str                     # "" / off / paused / disabled
    effective_state: str            # daily / weekly / off / paused -- rendered directly by admin badges
    resumes_at: Optional[datetime]  # resume time when paused, otherwise None


def normalize_tier(tier: Optional[str]) -> str:
    t = (tier or "").strip().lower()
    return t if t in VALID_TIERS else TIER_WEEKLY


def resolve_schedule(
    tier: Optional[str], enabled: bool, paused_until: Optional[datetime],
    now: Optional[datetime] = None,
) -> ScheduleDecision:
    """Determine a topic's current scheduling state. Pass now as UTC naive (same as DB storage)."""
    now = now or datetime.utcnow()
    t = normalize_tier(tier)

    # off takes precedence over everything: an explicitly configured "don't run"
    if t == TIER_OFF:
        return ScheduleDecision(False, TIER_OFF, "off", "off", None)
    # enabled=False but tier isn't off -- inconsistent data (old data / direct DB edits),
    # treated as not running and exposed in effective_state, not silently swallowed
    if not enabled:
        return ScheduleDecision(False, t, "disabled", "off", None)
    # Temporary pause (auto-resumes when due)
    if paused_until is not None and paused_until > now:
        return ScheduleDecision(False, t, "paused", "paused", paused_until)
    return ScheduleDecision(True, t, "", t, None)


def enabled_for_tier(tier: str) -> bool:
    """Derivation rule tier -> enabled (used by the single write point)."""
    return normalize_tier(tier) != TIER_OFF


def normalize_anchor_weekday(value: Optional[int]) -> Optional[int]:
    """Topic-level batch run day: 0=Monday..6=Sunday; None / out of range = use the global env default.

    Isomorphic to the same-named function in telemetry app/schedule.py -- the valid range of the cycle anchor is defined only here.
    """
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 6 else None


def normalize_anchor_hour(value: Optional[int]) -> Optional[int]:
    """Topic-level start hour (local hour 0-23); None / out of range = use the global env default."""
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 23 else None


# ─────────────── User-facing cadence description ───────────────
#
# Why this section exists: after persisting (ticking prompts), everything that happens on the platform side is implicit -- topics default to
# weekly, the anchor defaults to Monday 02:05 Beijing, and the batch run center drips hourly. A user submitting at 10:30 am sees nothing
# happen that day; the next sign of life is a scheduled job in the small hours. From their view it's "I clicked submit and got no feedback,
# and the next day a job showed up at 2 am". The cadence itself is fine; **not telling them** is the problem.
#
# So that sentence is extracted into a function, shared by the persist endpoint / GET /topics/{id}/schedule / the agent's persist tool,
# instead of each writing its own. **Describes only, doesn't schedule**: actual scheduling is telemetry's cycle_start_for.

_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")
_TIER_CN = {TIER_DAILY: "每天一轮", TIER_WEEKLY: "每周一轮",
            TIER_OFF: "手动(不自动跑,只在「监测与跑批」面板点按钮时跑)"}


def _env_int(name: str, default: int) -> int:
    import os

    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def selected_query_count(topic) -> int:
    """Number of prompts that will actually run (ticked, not soft-deleted)."""
    import json

    try:
        arr = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(arr, list):
        return 0
    n = 0
    for q in arr:
        if isinstance(q, str):
            n += 1
        elif isinstance(q, dict) and q.get("text") and q.get("selected") \
                and not q.get("deleted_at"):
            n += 1
    return n


def describe(topic, db=None) -> dict:
    """A cadence description that can be read straight to the user: how often / when it starts / credits per round / how to stop.

    db is only used to get the batch run unit price; if not passed or unavailable the money part is omitted, so pricing doesn't sink the whole description.
    """
    import json

    decision = resolve_schedule(
        getattr(topic, "schedule_tier", None),
        bool(getattr(topic, "enabled", True)),
        getattr(topic, "paused_until", None),
    )
    weekday = normalize_anchor_weekday(getattr(topic, "cycle_anchor_weekday", None))
    hour = normalize_anchor_hour(getattr(topic, "cycle_anchor_hour", None))
    # Both sides' defaults must match -- when changing this, sync telemetry-service's app/scheduler.py
    if weekday is None:
        weekday = _env_int("SCHEDULER_CRON_WEEKDAY", 0)
    if hour is None:
        hour = _env_int("SCHEDULER_CRON_HOUR", 2)
    minute = _env_int("SCHEDULER_CRON_MINUTE", 5)

    if decision.effective_state == "paused":
        cadence = f"临时暂停中,{decision.resumes_at} 之后自动恢复"
    elif decision.tier == TIER_DAILY:
        cadence = f"每天 {hour:02d}:{minute:02d}(北京时间)开一轮"
    elif not decision.should_run:
        cadence = "不自动跑;在「监测与跑批」面板点「跑新增」(只跑没成功过的格子)或「全部跑一轮」"
    else:
        cadence = (f"每周{_WEEKDAY_CN[weekday % 7]} {hour:02d}:{minute:02d}"
                   f"(北京时间)开一轮")

    try:
        engines = len(json.loads(getattr(topic, "engines_json", "") or "[]"))
    except Exception:  # noqa: BLE001
        engines = 0
    queries = selected_query_count(topic)
    cells = queries * engines

    try:
        engine_keys = [str(e) for e in json.loads(getattr(topic, "engines_json", "") or "[]") if e]
    except Exception:  # noqa: BLE001
        engine_keys = []

    out: dict = {
        "在跑": decision.should_run,
        "状态": decision.effective_state,
        "节奏": _TIER_CN.get(decision.tier, decision.tier),
        "开跑时间": cadence,
        "提示词数": queries,
        "引擎数": engines,
        # For rendering controls on the "continuous monitoring" card: which models run (raw keys, including channel variants switched by ops),
        # cycle anchor (effective value + whether user/ops customized it). Changed via PATCH /topics/{id}/schedule.
        "引擎": engine_keys,
        "锚点": {
            "weekday": weekday, "hour": hour, "minute": minute,
            "自定义": (getattr(topic, "cycle_anchor_weekday", None) is not None
                     or getattr(topic, "cycle_anchor_hour", None) is not None),
        },
        "每轮问答数": cells,      # one Q&A = one prompt x one engine asked once; billing and progress are both counted by it
        # This used to say "go to the topic settings page and turn off scheduled audits", but that switch only existed in the ops console back then --
        # users searched the frontend and couldn't find it. Now both paths really work: there's a card on the page and a tool in chat.
        "怎么停": "主题设置页的「提示词」标签页顶部(向导第 4 步顶部也有)是「监测与跑批」"
                  "面板:切手动 / 每天 / 每周、选周几几点开跑、勾跑哪些模型、暂停几天、"
                  "手动跑一轮;在对话里直接说也行(set_monitor_schedule)。",
        "note": (
            ("**新主题默认不自动跑**(2026-09-21 起):保存提示词不会触发跑批,"
             "要在「监测与跑批」面板点「全部跑一轮」;之后加了词,点「跑新增」"
             "只补跑新词。想持续监测要把节奏切成每天/每周。"
             if decision.tier == TIER_OFF else
             "提交之后**不会立刻开跑**,要等跑批中心下一个整点 tick —— 这句必须主动告诉用户,"
             "别让他刷着页面等。跑批中心每小时滴一次,所以某个引擎这一轮失败,"
             "最快也要下一小时才会再试。")
            + "**跑哪些模型、节奏(每天/每周/手动/暂停)、周几几点开跑,用户自己都能改**,"
            "对话里用 set_monitor_schedule,页面上在「提示词」标签页顶部的面板上 —— "
            "绝不要说「这个改不了」或「去网页找某个开关」。"
            "想现在就跑一轮用 trigger_batch_run。"
        ),
    }
    if db is not None:
        try:
            from geo.models.balance import PRICE_BATCH_CELL
            from geo.services import (
                balance_service, batch_billing, batch_prepay, topic_billing,
            )

            unit = int(balance_service.get_price(db, PRICE_BATCH_CELL))
            out["每次问答单价"] = f"{unit} 积分"
            # Since 2026-09-09 it's **prepaid**: the whole round's money is charged once before starting; if the balance is insufficient
            # it doesn't start at all (manual runs return 402, drip skips). That's the exact opposite feel from "settled next day",
            # and saying it wrong makes users think they can run first and pay later.
            if batch_prepay.enabled() and topic_billing.enabled() and unit > 0:
                out["每轮约扣"] = f"{unit * cells} 积分"
                out["计费口径"] = ("监测跑批**开跑前先扣**整轮的积分,"
                                   "余额不足则本轮不开跑;周期内没跑掉的问答在收口后自动退回。")
                return out
            # The "settled next day" sentence used to be hardcoded unconditionally, while batch run settlement was still in shadow mode by default
            # (GEO_BATCH_CHARGE_MODE=shadow) -- charging nothing. Users waited for the next-day charge per that sentence,
            # it never came, and they could only suspect the books were wrong. So check the **real effective state** before deciding what to say.
            enforcing = (topic_billing.enabled()
                         and batch_billing.mode() == "enforce"
                         and bool(batch_billing.billing_start_date()))
            if enforcing and unit > 0:
                out["每轮约扣"] = f"{unit * cells} 积分"
                out["计费口径"] = ("监测跑批次日结算,按实际跑成的问答数计;"
                                   f"计费起始日 {batch_billing.billing_start_date()}。")
            else:
                out["每轮约扣"] = "0 积分(当前不计费)"
                out["计费口径"] = ("监测跑批目前**不扣积分**(预览期,只记账)。"
                                   f"按价目表本应 {unit * cells} 积分。"
                                   "如实这么告诉用户,别让他等一笔不会发生的扣费。")
        except Exception:  # noqa: BLE001
            pass
    return out
