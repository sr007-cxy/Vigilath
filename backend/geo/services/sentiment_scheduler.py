"""Sentiment monitoring scheduled jobs -- APScheduler integration.

Runs hourly (adjustable via SENTINEL_CRON_INTERVAL_HOURS),
iterating all accounts with sentiment monitoring access and triggering the pipeline.

Deployment notes:
- With multiple workers only the leader starts the scheduler (reads the GEO_SCHEDULER_LEADER env var)
- jobs persisted with SQLAlchemyJobStore, survive container restarts
- Scan for "zombie jobs" every 10 minutes (running but last_run_at not updated for over 60 minutes)
- Job interval is 1 hour, so the zombie threshold is relaxed to 60 minutes, giving LLM calls ample time

Startup:
    In the startup hook of backend/geo/main.py, if GEO_SCHEDULER_LEADER=1 then call
    sentiment_scheduler.start()
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from geo.database import SessionLocal, engine
from geo.models.sentiment import SentimentAccountORM
from geo.services.sentiment_pipeline import (
    ZOMBIE_TIMEOUT_MIN, run_pipeline_for_account, zombie_started_at,
)

log = logging.getLogger(__name__)

# Scheduling interval (hours): default once per hour
INTERVAL_HOURS = int(os.environ.get("SENTINEL_CRON_INTERVAL_HOURS", "1"))
# Start offset (minutes): avoid the top-of-hour peak -- default 5 (runs at :05 every hour)
START_MINUTE = int(os.environ.get("SENTINEL_CRON_START_MINUTE", "5"))
# Run once daily at a fixed hour (0-23); when set, takes precedence over INTERVAL_HOURS.
# Example: SENTINEL_CRON_HOUR=10 + START_MINUTE=0 -> runs once a day at 10:00.
_cron_hour_raw = os.environ.get("SENTINEL_CRON_HOUR", "").strip()
CRON_HOUR = int(_cron_hour_raw) if _cron_hour_raw else None
TIMEZONE = os.environ.get("SENTINEL_CRON_TZ", "Asia/Shanghai")
# Zombie timeout (SENTINEL_ZOMBIE_TIMEOUT_MIN, default 60 minutes) is defined in sentiment_pipeline --
# the re-entry guard criterion and zombie cleanup criterion must be the same; writing it in two places will drift sooner or later.

_scheduler = None


def _list_eligible_accounts(db: Session) -> list[int]:
    """Return account ids due this round.

    Since sentiment monitoring became per-run billing, "eligible" means two things: the account is active and the balance covers one run.
    Tier no longer matters (there used to be a "JOIN memberships" TODO here, the same retired metric definition as the
    pro hard gate on the API side).

    The balance check lives in `sentiment_pipeline`, not here -- that side also has to check "was it already
    charged today", and for consistency the criterion must live in one place. Here we only filter out obvious non-runners.

    `run_hour` is the account's chosen "daily monitoring hour" (Beijing time). If set, the account is selected only at that hour;
    unset (NULL) keeps the old run-every-round behavior. **It only moves the time, not the money** -- the billing idempotency key pins
    "account x Beijing day", so it's still charged only once a day.
    """
    rows = (
        db.query(SentimentAccountORM.id, SentimentAccountORM.run_hour)
          .filter(SentimentAccountORM.active.is_(True))
          .all()
    )
    hour_now = _bj_hour()
    return [r[0] for r in rows if r[1] is None or int(r[1]) == hour_now]


def _bj_hour() -> int:
    """Current Beijing hour. Same metric definition as billing's bj_date (UTC+8), independent of the machine timezone."""
    return (datetime.utcnow() + timedelta(hours=8)).hour


def run_hourly_job():
    """cron trigger entry -- runs all eligible accounts hourly.

    Runs serially (avoids LLM rate limits); P1 can add concurrency + rate limiting.

    Logical fallback: if the previous round hasn't finished (running), APScheduler's max_instances=1
    automatically skips this trigger to avoid overlap.
    """
    log.info("[sentinel-cron] hourly run start")
    # **Reset zombies first**: a killed / crashed previous round leaves the account stuck at last_run_status='running' forever,
    # skipped every round after -> briefs stop updating permanently (2026-07-19/20 incident: account 1 stuck at running from 07-18).
    # cleanup_zombies used to run only inside geo-backend's APScheduler, unreachable by the standalone daily script,
    # so it's called explicitly here to guarantee "stuck runs self-heal".
    try:
        cleanup_zombies()
    except Exception as e:  # noqa: BLE001 — a failed reset shouldn't block this round
        log.warning("[sentinel-cron] zombie cleanup failed: %s", e)

    db = SessionLocal()
    try:
        ids = _list_eligible_accounts(db)
    finally:
        db.close()

    log.info("[sentinel-cron] %d eligible accounts", len(ids))
    success = failed = skipped = 0
    for aid in ids:
        try:
            r = run_pipeline_for_account(aid, trigger="cron")
            if r.get("status") == "success":
                success += 1
            elif r.get("status") == "skipped":
                skipped += 1
            else:
                failed += 1
        except Exception as e:  # noqa: BLE001
            log.exception("account %s pipeline crashed: %s", aid, e)
            failed += 1
    log.info("[sentinel-cron] done success=%d failed=%d skipped=%d", success, failed, skipped)


def cleanup_zombies():
    """Runs every 10 minutes, marking timed-out unfinished 'running' jobs as failed.

    Scenario: the service process is killed / OOM / docker stop; the job state machine never gets to write 'failed'
    and stays stuck at 'running'. The frontend keeps showing "job running".
    """
    db: Session = SessionLocal()
    try:
        # The criterion is delegated to zombie_started_at (reads the run log's started_at).
        # This used to be `last_run_at < cutoff` -- last_run_at records "last finished",
        # and _mark_running never writes it: an account that crashed on its first round has last_run_at NULL,
        # `NULL < cutoff` is NULL in SQL and the row isn't selected, so **the kind most likely to get stuck
        # was exactly the kind never cleaned up**. Now fetch all running first and judge each; there are only a handful of accounts, so it's cheap.
        running = (
            db.query(SentimentAccountORM)
              .filter(SentimentAccountORM.last_run_status == "running")
              .all()
        )
        hit = 0
        for acc in running:
            started = zombie_started_at(db, acc.id)
            if started is None:
                continue
            log.warning("[sentinel-cleanup] zombie account %s (started %s, over %d minutes), marking failed",
                        acc.id, started, ZOMBIE_TIMEOUT_MIN)
            acc.last_run_status = "failed"
            acc.last_run_error = "task interrupted (zombie cleanup)"
            hit += 1
        if hit:
            db.commit()
    finally:
        db.close()


def start() -> None:
    """Start APScheduler. Only the leader process should call this.

    In BackgroundScheduler mode APScheduler runs jobs in threads, in parallel with the FastAPI main process.
    """
    global _scheduler
    if _scheduler is not None:
        log.warning("[sentinel-scheduler] already started")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    except ImportError:
        log.error("[sentinel-scheduler] APScheduler not installed; run: pip install 'apscheduler[sqlalchemy]'")
        return

    _scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=engine)},
        timezone=TIMEZONE,
    )

    # Run every INTERVAL_HOURS hours, triggering at minute START_MINUTE of the hour
    # (default :05 every hour -- avoids the top-of-hour peak, and runs slightly after data updates)
    if CRON_HOUR is not None:
        # Run once daily at CRON_HOUR:START_MINUTE
        trigger_kwargs = dict(trigger="cron", hour=CRON_HOUR, minute=START_MINUTE)
    elif INTERVAL_HOURS == 1:
        # Trigger hourly at :MM
        trigger_kwargs = dict(trigger="cron", minute=START_MINUTE)
    else:
        # Once every N hours, starting from :MM
        trigger_kwargs = dict(
            trigger="cron",
            hour=f"*/{INTERVAL_HOURS}",
            minute=START_MINUTE,
        )

    _scheduler.add_job(
        run_hourly_job,
        **trigger_kwargs,
        id="sentinel_hourly",
        replace_existing=True,
        coalesce=True,                  # merge multiple missed runs into 1
        misfire_grace_time=1800,        # missed runs within 30 minutes can still catch up
        max_instances=1,                # key: avoid starting the next round before the previous finishes
    )
    _scheduler.add_job(
        cleanup_zombies,
        trigger="interval", minutes=10,
        id="sentinel_cleanup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # Post-paid batch run settlement (unrelated to sentiment monitoring, just riding this leader process -- in prod
    # this is the only APScheduler guaranteed to run as a single instance; running twice hits the idempotency key but wastes a full table scan)
    from geo.services.batch_billing import tick as _settle_batches

    _scheduler.add_job(
        _settle_batches,
        trigger="cron", minute=25,
        id="batch_billing_settle",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )
    # Close-out for batch run prepay: finished runs refund the overcharged part based on cells actually completed
    # (drip spreads one cycle over a week; words skipped mid-way by backlog protection / deterministic refusals can't be charged).
    # **Mutually exclusive** with the post-paid settlement above -- see the batch_prepay module docs.
    from geo.services.batch_prepay import tick as _refund_prepaid_runs

    _scheduler.add_job(
        _refund_prepaid_runs,
        trigger="cron", minute=40,
        id="batch_prepay_refund",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )

    _scheduler.start()
    _sched_desc = (f"daily at {CRON_HOUR:02d}:{START_MINUTE:02d}" if CRON_HOUR is not None
                   else f"every {INTERVAL_HOURS}h at :{START_MINUTE:02d}")
    log.info(
        "[sentinel-scheduler] started: %s %s, zombie_timeout=%dmin, cleanup=10min",
        _sched_desc, TIMEZONE, ZOMBIE_TIMEOUT_MIN,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("[sentinel-scheduler] stopped")


def maybe_start_from_env() -> None:
    """Called in the main.py startup hook. Decides whether to start based on env."""
    if os.environ.get("GEO_SCHEDULER_LEADER", "0") == "1":
        log.info("[sentinel-scheduler] leader mode, starting…")
        start()
    else:
        log.info("[sentinel-scheduler] not leader (GEO_SCHEDULER_LEADER!=1), skip")


# ── crontab entry point ─────────────────────────────────────────────────────────
# prod is multi-worker + containerized with no leader process (GEO_SCHEDULER_LEADER was never set,
# APScheduler never starts); scheduled jobs all go through the host crontab's `run.sh` one-shot containers:
#   5 * * * * .../run.sh sentiment_cron python -m geo.services.sentiment_scheduler
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_hourly_job()
