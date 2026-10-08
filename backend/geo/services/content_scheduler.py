"""Phase D.1 -- scheduled job for automatic content generation.

Runs every GEN_INTERVAL_MIN minutes, scanning all topics with auto_generate_enabled=True to see whether
auto_generate_time (**full HH:MM comparison**; timezone Asia/Shanghai) has arrived. If it has and nothing was generated
today yet, trigger content_generator.schedule_generation().

2026-09-11 -- it used to "scan at minute 7 of every hour, comparing only HH": topics specify HH:MM, but minutes were
dropped entirely, so 09:30 actually ran at 09:07. Now it compares by minute. Accordingly the criterion cannot be "equal" --
with a 5-minute scan interval, 09:30 lands exactly on a tick while 09:32 never would; so it is "if not yet run within
GEN_CATCHUP_MIN minutes after the time, run once to catch up", and only past that window does today count as missed.
This window also prevents something else: at the moment the change ships, all topics already past their time today and not yet run
would catch up together -- a sudden, real-money expense.

Idempotency: auto_generate_last_run_at serves as the "already ran today" marker. Triggers when today is later than the last batch run date;
so even scanning 288 times a day only runs once.

Hosted by main.py's startup hook, gated by **GEO_CONTENT_SCHEDULER_LEADER=1**
(see maybe_start_from_env; earlier this said GEO_SCHEDULER_LEADER, which is the switch for sentiment monitoring
and unrelated to this module).

Environment variables:
    GEO_CONTENT_SCHED_TZ            default Asia/Shanghai
    GEO_CONTENT_SCHED_INTERVAL_MIN  scan interval in minutes, default 5
    GEO_CONTENT_SCHED_CATCHUP_MIN   how long after the time catch-up is still allowed, default 120
    GEO_CONTENT_SCHED_MINUTE        deprecated (old "minute of the hour"), kept only so old .env files do not break
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.services.content_generator import schedule_generation

log = logging.getLogger(__name__)

TIMEZONE = os.environ.get("GEO_CONTENT_SCHED_TZ", "Asia/Shanghai")
# Scan interval for automatic generation. To compare the minutes of auto_generate_time, scanning hourly is no longer possible.
GEN_INTERVAL_MIN = int(os.environ.get("GEO_CONTENT_SCHED_INTERVAL_MIN", "5"))
# Window in which catch-up is still allowed after the time. Ensures "the process happened to be restarting" does not lose a whole day of drafts,
# without igniting all topics already past their time when the change ships (that would be a one-off bulk charge).
GEN_CATCHUP_MIN = int(os.environ.get("GEO_CONTENT_SCHED_CATCHUP_MIN", "120"))
# How many days ahead automatic generation prepares drafts. 0 = only write rows scheduled up to today (including overdue ones).
# Why cap it: without a cap it would keep writing further along the plan every day, while later plan rows may be rescheduled /
# get a different template / platform at any time -- writing them early just burns that LLM money, and any human change requires regenerating.
GEN_LOOKAHEAD_DAYS = int(os.environ.get("GEO_CONTENT_GEN_LOOKAHEAD_DAYS", "7"))
# Publish plan "rolling" renewal -- scanned once a day at this hour; plans with insufficient runway get the next month scheduled.
REFILL_HOUR = int(os.environ.get("GEO_PLAN_REFILL_HOUR", "3"))
# Automatic publishing by publish_date. Minute-level since 2026-07-30: scanned every PUBLISH_INTERVAL_MIN minutes;
# rows with a time (YYYY-MM-DDTHH:MM) publish when due; date-only rows (YYYY-MM-DD) keep the old semantics --
# due only after a certain hour of the day, to avoid all old plans publishing early at midnight.
# Since 2026-09-11 this is only the **fallback default**: the topic's own auto_publish_time takes priority (it also defaults to 11:00,
# so existing behavior is unchanged). Three-level priority: see _due_plan_item_ids.
PUBLISH_HOUR = int(os.environ.get("GEO_AUTO_PUBLISH_HOUR", "11"))
PUBLISH_INTERVAL_MIN = int(os.environ.get("GEO_AUTO_PUBLISH_INTERVAL_MIN", "5"))
# Master switch: on by default (=1). Production can set 0 to stop all scheduled auto-publishing at once (for emergencies, DB untouched).
AUTO_PUBLISH_ENABLED = os.environ.get("GEO_AUTO_PUBLISH_ENABLED", "1") == "1"
# Vigilath-Siyuan closed-loop scheduling (P0): off by default (=0); widen rollout after validating on a single topic.
SIYUAN_ENABLED = os.environ.get("GEO_SIYUAN_ENABLED", "0") == "1"
# siyuan_tick runs once a day at this hour: after refill, before publish, so drafts generated from this round's arm selection
# have a chance to enter today's/tomorrow's publish window. Defaults to refill + 1 hour.
SIYUAN_HOUR = int(os.environ.get("GEO_SIYUAN_HOUR", str(REFILL_HOUR + 1)))
# Max number of actions a single topic may trigger generation for per round (budget protection); <=0 uses run_tick's built-in TICK_K.
SIYUAN_DAILY_MAX = int(os.environ.get("GEO_SIYUAN_DAILY_MAX", "0"))
# The only real exit of the auto-publish pipeline: the self-hosted site on Mediumsly (= "官网博客" ("website blog") in the platform catalog).
# The publish destination must be recorded as that, not as the "planned platform" in the publish plan (see _mark_platform_target).
OWN_BLOG_PLATFORM = "官网博客"
MEDIUMSLY_MEDIA = "Mediumsly"


def _now_local() -> datetime:
    """Return the current Asia/Shanghai time (naive).

    SQLite stores datetimes naive, so naive time is used here too for comparison with last_run_at in the DB;
    but last_run_at is stored in UTC, so convert once.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:  # noqa: BLE001
        # Fallback: UTC+8
        return datetime.utcnow() + timedelta(hours=8)


def parse_hhmm(value: str | None, default_min: int) -> int:
    """`"HH:MM"` -> minute of the day. Falls back to default_min if unparseable.

    Tolerates hand-entered forms like `"9:0"` / `"09"` / with spaces -- the value is persisted straight from the API,
    and a missing zero must not stop a whole topic from producing drafts that day.
    """
    raw = (value or "").strip()
    if not raw:
        return default_min
    hh, _, mm = raw.partition(":")
    try:
        h = int(hh)
        m = int(mm) if mm.strip() else 0
    except ValueError:
        return default_min
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return default_min
    return h * 60 + m


def _due_now(target_min: int, now: datetime, catchup_min: int) -> bool:
    """Is it time yet.

    Not "equal": with a scan interval of GEN_INTERVAL_MIN minutes, 09:30 lands exactly on a tick while 09:32
    never would. So it is a [due, due+catchup) window -- if not yet run within the window, catch up;
    outside the window today counts as missed (running only once per day is guaranteed separately by last_run_at).
    """
    now_min = now.hour * 60 + now.minute
    return target_min <= now_min < target_min + catchup_min


def pending_plan_item_ids(
    db, topic_id: int, items, today: date, lookahead_days: int, limit: int,
) -> list[str]:
    """Ids of plan rows with no draft yet and scheduled within the [past, today+lookahead] window, first `limit` by schedule ascending.

    Why rows "already drafted" must be excluded: _run_per_item has **overwrite** semantics for the same plan_item
    (body rewritten, status back to pending_review). Dispatching already-drafted rows again would overturn and rewrite
    drafts humans already reviewed every day -- a risk the old path before 2026-09-11 did not have, since it never
    looked at plan rows and only wrote by prompts.

    The criterion for "already drafted" is **a non-soft-deleted doc exists**, regardless of its status:
      · generating -- the last round is still writing; dispatching again would have two tasks fighting over one row
      · rejected   -- manually rejected; whether to rewrite is a human decision, not cron's
      · published  -- rewriting would make the live article diverge from the DB (same reason whole-day regeneration skips published ones)

    Schedule ascending: naturally fills the most urgent first (overdue, due today), then prepares drafts further ahead.
    """
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    if limit <= 0:
        return []
    horizon = today + timedelta(days=max(0, lookahead_days))
    cand: list[tuple[str, int, str]] = []      # (day, seq, id) -- sorted by day, seq orders within the same day
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rid = str(it.get("id") or "")
        raw = str(it.get("publish_date") or "")
        if not rid or not raw:
            continue
        try:
            pd = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        if pd > horizon:
            continue
        cand.append((raw[:10], int(it.get("seq") or 0), rid))
    if not cand:
        return []
    cand.sort()

    written = {
        r[0] for r in db.query(TopicGeneratedDocORM.plan_item_id)
                        .filter(TopicGeneratedDocORM.topic_id == topic_id)
                        .filter(TopicGeneratedDocORM.plan_item_id.in_([c[2] for c in cand]))
                        .filter(TopicGeneratedDocORM.deleted_at.is_(None))
                        .all()
        if r[0]
    }
    return [c[2] for c in cand if c[2] not in written][:limit]


def _utc_today_start_in_tz() -> datetime:
    """Return the UTC naive datetime for today's local midnight, used to check whether last_run_at means "already ran today"."""
    local = _now_local()
    # Local midnight
    if local.tzinfo:
        local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_midnight = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        utc_midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8))
    return utc_midnight


def gen_tick() -> None:
    """Scan entry point for automatic generation (every GEN_INTERVAL_MIN minutes).

    Renamed from hourly_tick -- it no longer runs hourly, so keeping the name would mislead.
    The old name is kept as an alias so `python -m geo.services.content_scheduler hourly` in crontab still works.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM

    db: Session = SessionLocal()
    try:
        now = _now_local()
        today_start_utc = _utc_today_start_in_tz()
        log.info("[content-cron] tick %02d:%02d", now.hour, now.minute)
        rows = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.auto_generate_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        triggered = 0
        for t in rows:
            # Full HH:MM comparison (previously only HH was used, and the topic's minutes were dropped entirely)
            target_min = parse_hhmm(t.auto_generate_time, 9 * 60)
            if not _due_now(target_min, now, GEN_CATCHUP_MIN):
                continue
            # Skip if already ran today
            if t.auto_generate_last_run_at and t.auto_generate_last_run_at >= today_start_utc:
                continue
            count = max(1, min(20, int(t.auto_generate_count or 3)))

            # 2026-09-11 -- write by **publish plan rows**, no longer by prompts.
            #
            # Previously schedule_generation was called here without plan_id, so it fell into the old path of
            # "write the whole batch from topic.queries_json prompts" -- the drafts did not match the rows in the publish
            # plan: the plan said today should publish "Zhihu / long-form review / some seed", while auto generation
            # wrote something else, ignoring schedule, template, platform and creation preferences. What people saw was
            # "the plan is well laid out, but the auto-generated drafts are not those".
            #
            # Now first pick rows "scheduled within the window with no draft yet" and dispatch with plan_id + plan_item_ids,
            # via _run_per_item -- the same metric definition as the full-table generation on "confirm plan".
            # If no plan rows are available (project not started / everything in the plan already written), fall back to the old path,
            # same behavior as before the rework, so topics without plans do not stop producing because of this change.
            plan = (
                db.query(AiTelemetryTopicExecutionPlanORM)
                  .filter(AiTelemetryTopicExecutionPlanORM.topic_id == t.id)
                  .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
                  .first()
            )
            item_ids: list[str] = []
            plan_id = None
            if plan and plan.status in ("draft", "confirmed"):
                try:
                    items = json.loads(plan.publishing_plan_json or "[]")
                except Exception:  # noqa: BLE001
                    items = []
                item_ids = pending_plan_item_ids(
                    db, t.id, items, now.date(), GEN_LOOKAHEAD_DAYS, count)
                if item_ids:
                    plan_id = plan.id

            log.info("[content-cron] firing topic=%d count=%d via=%s",
                     t.id, count, "plan" if plan_id else "queries")
            schedule_generation(
                topic_id=t.id, plan_id=plan_id,
                plan_item_ids=item_ids or None,
                max_docs=count, mark_auto_run=True,
                trigger_source="cron",      # No human trigger -> batch ledger actor recorded as empty
            )
            triggered += 1
        log.info("[content-cron] %d topics triggered", triggered)
    finally:
        db.close()


# Old-name alias: external callers (crontab / docs / other modules) may still reference hourly_tick.
hourly_tick = gen_tick


def refill_tick() -> None:
    """Daily trigger: scan approved + auto-generate topics, and schedule the next month for publish plans with insufficient runway.

    Idempotency is guaranteed by the PLAN_REFILL_LEAD_DAYS threshold inside extend_plan_one_month (skips if runway is sufficient),
    so scanning daily results in each topic actually being renewed about once a month.
    """
    from geo.api.admin_review import extend_plan_one_month
    from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM

    db: Session = SessionLocal()
    try:
        topics = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.auto_generate_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        refilled = 0
        for t in topics:
            plan = (
                db.query(AiTelemetryTopicExecutionPlanORM)
                  .filter(AiTelemetryTopicExecutionPlanORM.topic_id == t.id)
                  .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
                  .first()
            )
            if not plan or plan.status not in ("draft", "confirmed"):
                continue
            try:
                n = extend_plan_one_month(db, plan)
            except Exception:  # noqa: BLE001
                log.exception("[plan-refill] topic=%d failed", t.id)
                db.rollback()
                continue
            if n:
                refilled += 1
                log.info("[plan-refill] topic=%d +%d rows", t.id, n)
        log.info("[plan-refill] %d plans extended", refilled)
    finally:
        db.close()


def siyuan_tick() -> None:
    """Vigilath-Siyuan closed-loop scheduling (P0): runs once a day, connecting VSCL-RL from "arm selection" to "generation".

    For each approved topic with siyuan_enabled:
      1. vscl_rl.run_tick -> link generated drafts / settle due actions / select the next batch of planned actions;
      2. for each new planned action, trigger content generation for its query and bind the drafts to that action
         (topic_generated_docs.vscl_action_id), for linking and reward settlement tracing in the next round.

    After generation they enter the existing review/publish pipeline (gen_tick does not participate; generation is dispatched directly here);
    publishing is still done by publish_tick per publish_date. run_tick is idempotent and safe to repeat.
    """
    if not SIYUAN_ENABLED:
        log.info("[siyuan-cron] GEO_SIYUAN_ENABLED!=1, skip")
        return
    from geo.services import vscl_rl

    db: Session = SessionLocal()
    try:
        topics = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.siyuan_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        fired = 0
        for t in topics:
            try:
                result = vscl_rl.run_tick(db, t.id)
                db.commit()
            except Exception:  # noqa: BLE001
                log.exception("[siyuan-cron] run_tick failed topic=%d", t.id)
                db.rollback()
                continue
            planned = result.get("planned") or []
            if SIYUAN_DAILY_MAX > 0:
                planned = planned[:SIYUAN_DAILY_MAX]
            for a in planned:
                q = (a.get("query") or "").strip()
                action_id = a.get("action_id")
                if not q or action_id is None:
                    continue
                # fire-and-forget thread; single query, generated drafts bound to this action.
                schedule_generation(
                    topic_id=t.id, queries_override=[q],
                    trigger_source="siyuan",    # No human trigger -> batch ledger actor recorded as empty
                    vscl_action_id=int(action_id), max_docs=1,
                )
                fired += 1
            t.siyuan_last_tick_at = datetime.utcnow()
            db.commit()
            log.info("[siyuan-cron] topic=%d attached=%s settled=%s planned=%d",
                     t.id, result.get("attached"), result.get("settled"), len(planned))
        log.info("[siyuan-cron] %d topics ticked, %d generations fired", len(topics), fired)
    finally:
        db.close()


def _mark_platform_target(d, url: str, *, platform: str, media: str = "") -> None:
    """Append a **real** placement record to publish_targets_json.

    platform must be the channel actually published to, **not doc.platform** -- the latter is the publish plan's
    "planned platform" (Toutiao / Zhihu / official account...), only used to choose writing rules and the Mediumsly tag.
    Fixed 2026-08-06: this used to write d.platform, while the auto-publish pipeline actually only publishes to Mediumsly,
    so the frontend "publish destination" showed "今日头条 ↗" ("Toutiao") with a link pointing to mediumsly.com -- a fake destination.

    marked_by=0 means automatic by the system (not a manual click by some admin). Same field as admin manual publish,
    so the frontend need not distinguish the source when rendering published platforms.
    Idempotent: if the same (platform, url) is already recorded, do not append -- prevents repeated ticks / republishing from duplicating one entry.
    """
    url = (url or "").strip()
    try:
        targets = json.loads(d.publish_targets_json or "[]")
    except Exception:  # noqa: BLE001
        targets = []
    if not isinstance(targets, list):
        targets = []
    for t in targets:
        if isinstance(t, dict) and (t.get("url") or "").strip() == url \
                and (t.get("platform") or "") == platform:
            return
    targets.append({
        "platform": platform,
        "media": media,
        "url": url,
        # Record the planned platform: when where it was actually published != where it was planned, operators can trace whom this draft was scheduled for.
        "planned_platform": d.platform or "",
        "marked_at": datetime.utcnow().isoformat(),
        "marked_by": 0,
    })
    d.publish_targets_json = json.dumps(targets, ensure_ascii=False)


async def _run_publish() -> None:
    """Scan due drafts and publish. Awaited serially in a single event loop (the Mediumsly publisher has its own concurrency gate)."""
    from geo.models.ai_telemetry import (
        AiTelemetryTopicExecutionPlanORM,
        AiTelemetryTopicORM,
        TopicGeneratedDocORM,
    )
    from geo.models.user import UserORM
    from geo.services import mediumsly_publisher

    now_local = _now_local().replace(tzinfo=None)
    today = now_local.date()
    db: Session = SessionLocal()
    published = 0
    try:
        topics = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.auto_publish_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        for t in topics:
            plan = (
                db.query(AiTelemetryTopicExecutionPlanORM)
                  .filter(AiTelemetryTopicExecutionPlanORM.topic_id == t.id)
                  .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
                  .first()
            )
            if not plan or plan.status not in ("draft", "confirmed"):
                continue
            try:
                items = json.loads(plan.publishing_plan_json or "[]")
            except Exception:  # noqa: BLE001
                items = []
            due_ids = _due_plan_item_ids(
                items, now_local, today, parse_hhmm(t.auto_publish_time, PUBLISH_HOUR * 60))
            if not due_ids:
                continue
            docs = (
                db.query(TopicGeneratedDocORM)
                  .filter(TopicGeneratedDocORM.topic_id == t.id)
                  .filter(TopicGeneratedDocORM.plan_item_id.in_(list(due_ids)))
                  .filter(TopicGeneratedDocORM.status.notin_(("published", "rejected")))
                  .all()
            )
            if not docs:
                continue
            user = db.get(UserORM, t.user_id)
            for d in docs:
                if not (d.body_markdown or "").strip():
                    continue  # No body (generation failed / placeholder), do not publish
                # Compliance gate hook 3: forced rescan at the auto-publish exit + gate 0 qualification check (design doc 5.2).
                # Exceptions in the gate itself count as blocking (fail-closed): better to publish one round less than let unscanned drafts through.
                try:
                    from geo.services import compliance_scanner
                    gate_ok, gate_reason = compliance_scanner.publish_gate(
                        db, d, t, hook="publish_auto")
                except Exception:  # noqa: BLE001
                    log.exception("[publish-cron] compliance gate error doc=%s", d.id)
                    gate_ok, gate_reason = False, "合规闸门异常,本轮跳过"
                if not gate_ok:
                    log.warning("[publish-cron] doc=%s blocked by compliance: %s",
                                d.id, gate_reason)
                    continue
                # Actually publish to Mediumsly; no token configured -> degrade to marking only, non-blocking.
                publish_url = ""
                if user and user.email:
                    try:
                        result = await mediumsly_publisher.push(d, user, t)
                        d.mediumsly_post_id = result.post_id
                        d.mediumsly_url = result.url
                        d.mediumsly_pushed_at = datetime.utcnow()
                        d.mediumsly_last_error = None
                        publish_url = result.url or ""
                    except mediumsly_publisher.MediumslyNotConfigured:
                        pass  # No token configured, only mark publish_targets
                    except mediumsly_publisher.MediumslyPostGone:
                        d.mediumsly_post_id = None
                        d.mediumsly_last_error = (
                            "Mediumsly post deleted upstream — id cleared, retry"
                        )
                    except mediumsly_publisher.MediumslyError as e:
                        d.mediumsly_last_error = f"{e.code or 'ERROR'}: {e}"
                        log.warning("[publish-cron] doc=%s push failed: %s",
                                    d.id, d.mediumsly_last_error)
                # Only an actual Mediumsly article URL counts as "published", and only then is the destination written --
                # writing a destination with an empty URL on push failure / no token would misreport placement to the user.
                if (publish_url or "").strip():
                    _mark_platform_target(d, publish_url,
                                          platform=OWN_BLOG_PLATFORM,
                                          media=MEDIUMSLY_MEDIA)
                # Same semantics as admin manual publish: set published even if external posting failed, error kept in mediumsly_last_error.
                d.status = "published"
                published += 1
                # Flywheel P1-C: register a targeted re-test probe after placement (failure does not block publishing).
                # Siyuan P0 (design doc sections 5.2/6): no real URL means no real placement -- only register the re-test once
                # a publish URL exists, otherwise the reward window would include content that is not live, polluting the RL reward.
                if (publish_url or "").strip():
                    try:
                        from geo.services.retest_service import schedule_for_doc
                        schedule_for_doc(db, d, t)
                    except Exception:  # noqa: BLE001
                        log.exception("[publish-cron] retest probe failed doc=%s", d.id)
                else:
                    log.info("[publish-cron] doc=%s has no real URL, marked published but no re-test registered",
                             d.id)
            db.commit()
            if docs:
                log.info("[publish-cron] topic=%d published %d docs", t.id, len(docs))
        log.info("[publish-cron] %d docs published total", published)
    finally:
        db.close()



def _due_plan_item_ids(items, now_local, today, publish_min: int = None) -> set[str]:
    """Set of plan_item ids in the publish plan that are already due -- including overdue backfills.

    Three-level priority for the due time:
      1. Row level -- rows whose publish_date is `YYYY-MM-DDTHH:MM`, compared by minute, published when due;
      2. Topic level -- date-only rows (`YYYY-MM-DD`) use publish_min (the topic's auto_publish_time);
      3. Fallback -- when publish_min is not passed, use the global PUBLISH_HOUR (old semantics, default 11:00).

    Why date-only rows cannot publish right at midnight: that would push all historical plans to publish early in the small hours.

    The free-platform and paid-media auto-publish paths **share this one decision** -- writing it twice would eventually drift,
    with the same row being "due" on one path and "not yet" on the other.
    """
    if publish_min is None:
        publish_min = PUBLISH_HOUR * 60
    now_min = now_local.hour * 60 + now_local.minute
    due_ids: set[str] = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        raw = str(it.get("publish_date") or "")
        if not raw or not it.get("id"):
            continue
        try:
            if "T" in raw:
                due = datetime.fromisoformat(raw) <= now_local
            else:
                pd = date.fromisoformat(raw[:10])
                due = pd < today or (pd == today and now_min >= publish_min)
            if due:
                due_ids.add(str(it["id"]))
        except ValueError:
            continue
    return due_ids


# -- Paid media auto-order on the scheduled date (2026-09-10) -------------------------------
# Previously paid placements **could only be clicked manually**: even with the plan laid out and drafts approved, someone had to go to the publish center
# and click "publish · deduct N credits" one by one. For topics with topic.auto_publish_paid_enabled,
# this step is delegated to publish_tick.
#
# ★ This is the only path in the repo that **spends money unattended**, so not a single gate can be skipped:
#   reuse every step of the manual order chain (ownership -> live price check -> per-order/daily caps -> optimistic lock -> /add),
#   only replacing current_user with the topic owner -- the money is charged to them, not to "the system".
#   On account-level faults (insufficient balance / login session invalid / unknown result), immediately **stop this topic for this round**,
#   leaving the rest in planned as-is: better to publish less than keep charging blindly.
def _auto_publish_paid_for_topic(db, topic, due_ids: set[str]) -> tuple[int, int]:
    """Publish this topic's due paid tasks. Returns (success count, skipped/failed count)."""
    from geo.api.rwj_channel import (
        _preview_one, _publish_one, _task_meta,
    )
    from geo.models.ai_telemetry import TopicDocPublishTaskORM, TopicGeneratedDocORM
    from geo.models.user import UserORM
    from geo.services import publish_task_service

    owner = db.get(UserORM, topic.user_id)
    if owner is None:
        return 0, 0
    paid_names = publish_task_service._paid_platform_names()
    tasks = (
        db.query(TopicDocPublishTaskORM)
          .filter(TopicDocPublishTaskORM.topic_id == topic.id)
          .filter(TopicDocPublishTaskORM.status == "planned")
          .filter(TopicDocPublishTaskORM.platform.in_(paid_names))
          .all()
    )
    ok = bad = 0
    gate_cache: dict = {}
    for task in tasks:
        doc = db.get(TopicGeneratedDocORM, task.doc_id) if task.doc_id else None
        if doc is None or doc.status != "approved" or not (doc.body_markdown or "").strip():
            continue                      # Draft not approved / no body, not for this round
        if str(getattr(doc, "plan_item_id", "") or "") not in due_ids:
            continue                      # Scheduled date not reached yet
        one = _preview_one(db, task, owner)
        if not one.get("ok"):
            db.commit()
            bad += 1
            log.warning("[publish-cron] paid auto-publish price check failed topic=%s task=%s %s",
                        topic.id, task.id, one.get("code"))
            if one.get("fatal"):
                break
            continue
        out = _publish_one(db, task, doc, topic, _task_meta(task), one["provider"],
                           one["type"], one["resource_id"], one["resource_title"],
                           one["price"], owner, gate_cache)
        if out.get("ok"):
            ok += 1
            log.info("[publish-cron] paid auto-publish succeeded topic=%s task=%s %s",
                     topic.id, task.id, task.media_name)
        else:
            bad += 1
            log.warning("[publish-cron] paid auto-publish failed topic=%s task=%s %s",
                        topic.id, task.id, out.get("code"))
            if out.get("fatal"):
                break                     # Account-level fault: this topic stops here for this round
    return ok, bad



async def _run_publish_paid() -> None:
    """The paid media auto-order pass. **Scanned separately** from the free platform pass: the two switches are independent;
    a topic can perfectly well "not auto-publish on free platforms, but auto-order paid media when due", and vice versa.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicORM
    from geo.database import settings as _s

    if not getattr(_s, "RWJ_PUBLISH_ENABLED", False):
        log.info("[publish-cron] RWJ_PUBLISH_ENABLED=0, skipping the whole paid auto-publish pass")
        return
    now_local = _now_local().replace(tzinfo=None)
    today = now_local.date()
    db: Session = SessionLocal()
    ok = bad = 0
    try:
        topics = (
            db.query(AiTelemetryTopicORM)
              .filter(AiTelemetryTopicORM.auto_publish_paid_enabled.is_(True))
              .filter(AiTelemetryTopicORM.submission_status == "approved")
              .all()
        )
        for t in topics:
            plan = (
                db.query(AiTelemetryTopicExecutionPlanORM)
                  .filter(AiTelemetryTopicExecutionPlanORM.topic_id == t.id)
                  .order_by(AiTelemetryTopicExecutionPlanORM.id.desc())
                  .first()
            )
            if not plan or plan.status not in ("draft", "confirmed"):
                continue
            try:
                items = json.loads(plan.publishing_plan_json or "[]")
            except Exception:  # noqa: BLE001
                items = []
            due_ids = _due_plan_item_ids(
                items, now_local, today, parse_hhmm(t.auto_publish_time, PUBLISH_HOUR * 60))
            if not due_ids:
                continue
            try:
                a, b = _auto_publish_paid_for_topic(db, t, due_ids)
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("[publish-cron] paid auto-publish crashed topic=%s", t.id)
                continue
            ok += a
            bad += b
        if ok or bad:
            log.info("[publish-cron] paid auto-publish: %d succeeded, %d skipped/failed", ok, bad)
    finally:
        db.close()


def publish_tick() -> None:
    """Triggered every PUBLISH_INTERVAL_MIN minutes: publish due, unpublished drafts in the publish plan per schedule.

    Selection: approved topics with auto_publish_enabled -> latest execution plan ->
          due plan_items (timed ones compared by minute; date-only ones due at the topic's auto_publish_time that day)
          -> linked docs with status not in (published, rejected) and a body.
    Publish: actually publish to Mediumsly (missing token degrades to marking only) + write publish_targets marking the platform, set published.
    Idempotent: published docs are never selected again (status filter), naturally preventing republishing.
    """
    if not AUTO_PUBLISH_ENABLED:
        log.info("[publish-cron] GEO_AUTO_PUBLISH_ENABLED!=1, skip")
        return
    try:
        asyncio.run(_run_publish())
    except Exception:  # noqa: BLE001
        log.exception("[publish-cron] crashed")
    # The paid pass runs independently: a free platform failure should not drag it down, and vice versa
    try:
        asyncio.run(_run_publish_paid())
    except Exception:  # noqa: BLE001
        log.exception("[publish-cron] paid crashed")


_scheduler = None


def attach(scheduler) -> None:
    """Attach to an external APScheduler instance (shared by other in-process schedulers)."""
    scheduler.add_job(
        gen_tick,
        trigger="cron", minute=f"*/{GEN_INTERVAL_MIN}",
        # Do not change the job id: a new id would leave an orphaned old job in the same APScheduler,
        # and both would run (replace_existing only matches the same id).
        id="content_auto_generate_hourly",
        replace_existing=True,
        coalesce=True,
        # The catch-up window is handled by _due_now itself; grace only needs to cover one scan interval
        misfire_grace_time=GEN_INTERVAL_MIN * 60,
        max_instances=1,
    )
    scheduler.add_job(
        refill_tick,
        trigger="cron", hour=REFILL_HOUR, minute=0,
        id="plan_monthly_refill",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )
    scheduler.add_job(
        publish_tick,
        trigger="cron", minute=f"*/{PUBLISH_INTERVAL_MIN}",
        id="content_auto_publish",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=300,
        max_instances=1,
    )
    # Vigilath-Siyuan closed loop (P0): runs once a day after refill, arm selection -> trigger generation. Off by default.
    # When prod has no scheduler leader, use crontab to call python -m geo.services.content_scheduler siyuan.
    if SIYUAN_ENABLED:
        scheduler.add_job(
            siyuan_tick,
            trigger="cron", hour=SIYUAN_HOUR, minute=0,
            id="siyuan_tick",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
            max_instances=1,
        )

    # Ruanwenjiang paid placement receipt polling: scan in-flight tasks hourly to fetch URLs. The tick returns immediately when credentials are not configured.
    # When prod has no scheduler leader, use crontab to call python -m geo.services.rwj_order_poller.
    from geo.services.rwj_order_poller import tick as rwj_poll_tick

    scheduler.add_job(
        rwj_poll_tick,
        trigger="cron", minute=15,
        id="rwj_order_poll",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=1800,
        max_instances=1,
    )

    # Paid placement link liveness check: crawl published links once a day; those judged dead consecutively and within the 30-day after-sales window
    # request a refund per the gate (RWJ_APPEAL_ENABLED, off by default). prod uses crontab to call
    # python -m geo.services.rwj_liveness.
    from geo.services.rwj_liveness import tick as rwj_liveness_tick

    scheduler.add_job(
        rwj_liveness_tick,
        trigger="cron", hour=4, minute=40,
        id="rwj_link_liveness",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    # aitoearn distribution flow reconciliation polling: scan in-flight tasks hourly to fetch post links. Returns immediately when the key is not configured.
    # When prod has no scheduler leader, use crontab to call python -m geo.services.aitoearn_poller.
    from geo.services.aitoearn_poller import tick as aitoearn_poll_tick

    scheduler.add_job(
        aitoearn_poll_tick,
        trigger="cron", minute=25,
        id="aitoearn_flow_poll",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=1800,
        max_instances=1,
    )

    # Public probing of publish candidate URLs; on by default, can be set to 0 to disable in an emergency.
    # Only probes url_submitted; never enters platform backends.
    if os.environ.get("GEO_PUBLISH_URL_RECONCILE_ENABLED", "1") == "1":
        from geo.services.publish_url_reconciler import tick as publish_url_reconcile_tick
        scheduler.add_job(
            publish_url_reconcile_tick,
            trigger="cron", minute="*",
            id="publish_url_reconcile",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=300,
            max_instances=1,
        )

    # Compliance in-flight monitoring (P1): one sweep every 10 minutes + re-test + timeout; idempotent (watermark + dedup);
    # when prod has no scheduler leader, use crontab to call python -m geo.services.compliance_monitor.
    if os.environ.get("GEO_COMPLIANCE_MONITOR_ENABLED", "1") == "1":
        from geo.services.compliance_monitor import sweep_tick as compliance_sweep_tick

        scheduler.add_job(
            compliance_sweep_tick,
            trigger="cron", minute="*/10",
            id="compliance_monitor_sweep",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=300,
            max_instances=1,
        )
    # Requeue generation failures (2026-08-04): regenerate due failed drafts every 10 minutes. Terminal causes are not queued,
    # so idle cost is low. When prod has no scheduler leader, use crontab to call python -m geo.services.gen_retry.
    if os.environ.get("GEO_GEN_RETRY_ENABLED", "1") == "1":
        from geo.services.gen_retry import retry_tick as gen_retry_tick

        scheduler.add_job(
            gen_retry_tick,
            trigger="cron", minute="*/10",
            id="content_gen_retry",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=600,
            max_instances=1,
        )

    # Automatic platform rule adaptation (2026-08-04): once a day, auto-fill AI first drafts for platforms in use without rules,
    # and distill rejection reasons of existing platforms into increments pending adoption. When prod has no leader, run via crontab
    # python -m geo.services.platform_rules_auto.
    if os.environ.get("GEO_PLATFORM_RULES_AUTO", "1") == "1":
        from geo.services.platform_rules_auto import sync_tick as platform_rules_tick

        scheduler.add_job(
            platform_rules_tick,
            trigger="cron", hour=REFILL_HOUR, minute=40,
            id="platform_rules_auto_sync",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
            max_instances=1,
        )

    log.info("[content-cron] attached: content-gen */%d min (catchup %d min), "
             "plan-refill @%02d:00, "
             "auto-publish */%d min, siyuan @%02d:00 (%s), compliance-sweep */10 (%s)",
             GEN_INTERVAL_MIN, GEN_CATCHUP_MIN, REFILL_HOUR, PUBLISH_INTERVAL_MIN, SIYUAN_HOUR,
             "on" if SIYUAN_ENABLED else "off",
             "on" if os.environ.get("GEO_COMPLIANCE_MONITOR_ENABLED", "1") == "1" else "off")
    log.info("[content-cron] gen-retry */10 (%s), platform-rules-auto @%02d:40 (%s)",
             "on" if os.environ.get("GEO_GEN_RETRY_ENABLED", "1") == "1" else "off",
             REFILL_HOUR,
             "on" if os.environ.get("GEO_PLATFORM_RULES_AUTO", "1") == "1" else "off")


def start() -> None:
    """Start a standalone APScheduler instance that only hosts the content cron.

    Decoupled from sentiment_scheduler -- in test / production sentiment monitoring already runs in the separate
    geo-sentinel.service, so the backend process must not add the sentiment hourly job on top,
    otherwise tasks would run twice. So content generation uses its own leader env.
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
    """Called from main.py's startup hook. Only starts when GEO_CONTENT_SCHEDULER_LEADER=1."""
    if os.environ.get("GEO_CONTENT_SCHEDULER_LEADER", "0") == "1":
        log.info("[content-scheduler] leader mode, starting…")
        start()
    else:
        log.info("[content-scheduler] not leader, skip")


if __name__ == "__main__":
    # When prod has no scheduler leader, run a single tick via crontab (see compliance_monitor).
    # Usage: python -m geo.services.content_scheduler [siyuan|publish|refill|hourly|rwj|aitoearn|publish-url]
    import sys

    logging.basicConfig(level=logging.INFO)
    _job = sys.argv[1] if len(sys.argv) > 1 else "siyuan"
    _dispatch = {
        "siyuan": siyuan_tick,
        "publish": publish_tick,
        "refill": refill_tick,
        "hourly": gen_tick,      # Old name, still used in crontab
        "gen": gen_tick,
    }
    if _job == "rwj":
        from geo.services.rwj_order_poller import tick as _rwj_tick
        _dispatch["rwj"] = _rwj_tick
    if _job == "aitoearn":
        from geo.services.aitoearn_poller import tick as _aitoearn_tick
        _dispatch["aitoearn"] = _aitoearn_tick
    if _job == "liveness":
        from geo.services.rwj_liveness import tick as _liveness_tick
        _dispatch["liveness"] = _liveness_tick
    if _job == "publish-url":
        from geo.services.publish_url_reconciler import tick as _publish_url_tick
        _dispatch["publish-url"] = _publish_url_tick
    _fn = _dispatch.get(_job)
    if _fn is None:
        print(f"unknown job: {_job}; choose one of {sorted(_dispatch)}")
        raise SystemExit(2)
    _fn()
