"""Scheduler process for scheduled sending (design §4.2) — **there is exactly one in the whole system**.

    python -m agents.sales_agent.scheduler

It doesn't live inside any web process: no startup hook, no changes to `agent.Dockerfile`, and geo-agent's
8 workers are untouched (§7.2). The cost is one more deployment unit — **if `start_send_scheduler()`
isn't launched by systemd / compose, this feature simply doesn't exist**, and it shows up as "nothing sent when due, no
errors, no logs". The heartbeat table (§3.4) is the only place this can be detected, so it isn't optional.

Division of labor:

    Scheduler main thread (one round per 60s)   scans tables + grabs locks + writes heartbeats; sends no emails, generates no drafts itself
    Sending threads × 4                          pre-generate -> wait until due -> send one plan serially to completion

**The main thread must not run plans itself.** A 12-email plan takes 6 minutes (30-second throttle × 12); running it on the main thread
stalls the 60-second beat for 6 minutes and delays every later plan — while the main thread only needs to dispatch.
Likewise head pre-generation can't run on the main thread: 5 companies ÷ concurrency 3 × 30 seconds ≈ 1 minute, eating a whole round.

**Sending threads are a fixed long-lived pool, not "one thread per plan".** Design §4.2's pseudocode says
`spawn_worker(sched)`, which reads more plainly, but every new thread creates a new event loop and an httpx
connection pool (`service/send_schedule._thread_event_loop` / `model.get_sales_agent_model`
both bucket by `threading.get_ident()`), and nobody closes either when the thread exits — after a day of hundreds of plans
that's hundreds of leaked fds. A fixed 4 threads makes "at most 4 httpx connection pools" in §7.3's resource table
actually hold, not just in the first round. Parallelism is unchanged: the semaphore is capped at `MAX_CONCURRENT_SCHEDULES`,
the same thing as `live_workers >= MAX` in the pseudocode.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import socket
import threading
import time
import traceback
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from geo.database import SessionLocal, engine as _db_engine, settings

from agents.sales_agent.models import (
    SalesAgentSchedulerHeartbeatORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.service import (
    PREGEN_HEAD_COUNT,
    due_send_schedules,
    expire_overdue_schedules,
    mark_schedule_running,
    pregenerate_schedule_drafts,
    reclaim_stale_items,
    run_one_schedule,
    session_has_table,
    wait_until_scheduled,
)

logger: logging.Logger = logging.getLogger(__name__)


TICK_SECONDS = 60
# ⚠️ **Never set this to 1.** Under L2 the whole system has only this one process, no 8 workers to stack parallelism,
# so this constant is the only source of parallelism — setting 1 means 10 users who all booked 15:00 must queue up,
# and the 10th person's "15:00" becomes 15:54. Before raising it, check whether the heartbeat table's `live_workers` is
# pegged at the max long-term (§7.4).
MAX_CONCURRENT_SCHEDULES = 4
# Heartbeat rows not updated for longer than this are cleared at startup. instance_key includes the pid, so every process change leaves a row;
# without clearing, a year accumulates hundreds of rows of garbage nobody reads. **It clears others' old rows, not its own**.
HEARTBEAT_RETENTION = timedelta(days=7)

# Two advisory lock namespaces. Uses PG's two-int4 form `pg_try_advisory_lock(ns, id)`,
# following `geo/services/publish_locks.py` (which uses "puba"); here it's ASCII "sasc"/"sase".
_NS_SCHED = 0x73617363    # "sasc" — plan lock, granularity = a single plan
_NS_SENDER = 0x73617365   # "sase" — sender lock, granularity = a single email_account
# Lock id used when a plan has no pinned sender. **This is not "no lock"**: unpinned means execution falls back to the platform-level
# account (global scope of `_effective_email_config`), and that account is shared by everyone —
# exactly the kind most in need of mutual exclusion. Let them all crowd onto the id=0 lock serially; better slow than simultaneous.
_SENDER_KEY_UNPINNED = 0


class AdvisoryLocks:
    """Session-level advisory locks, all on **one dedicated connection outside the pool** (§4.2.2).

    `pg_try_advisory_lock` locks are bound to the **connection**, not to transactions or Session objects. And the plan lock
    must span the whole sending round (12 emails × 30 seconds ≈ 6 minutes), which doesn't fit in a transaction — so `publish_locks.py`'s
    `pg_advisory_xact_lock` approach can't be copied here; its premise is "claim is a sub-millisecond DB operation".

    Using a pooled connection doesn't work either; the consequence is **duplicate sending**: once the lock-holding Session calls `close()`, the connection returns to the pool,
    and when reused by other code the lock is still on it unbeknownst to anyone; or the pool recycles and rebuilds the connection and the lock simply vanishes -> another
    worker grabs the same plan -> the same batch of emails is sent twice. So this has its own small `NullPool` engine,
    opening only one connection, used only for locks, never for business queries, and not counted in §7.3's pool budget.

    A killed process leaves no deadlock — PG releases session-level locks automatically when the connection drops. This is the only place in the whole design that needs no
    extra compensation; together with reclaiming detail rows whose `claimed_at` exceeds 5 minutes, the recovery path is complete.
    """

    def __init__(self, url: str = "") -> None:
        self._engine = create_engine(url or settings.DATABASE_URL, poolclass=NullPool)
        self._conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        # The 4 sending threads unlock in their own finally blocks, and a DBAPI connection can't be used by several threads at once.
        # Every statement is sub-millisecond, so the serialization overhead is negligible.
        # AUTOCOMMIT is also required: otherwise this connection would keep an open transaction hanging,
        # which on the PG side is an idle-in-transaction connection holding a snapshot for minutes.
        self._guard = threading.Lock()

    def try_lock(self, namespace: int, key: int) -> bool:
        with self._guard:
            got = self._conn.execute(
                text("SELECT pg_try_advisory_lock(:ns, :id)"),
                {"ns": int(namespace), "id": int(key)},
            ).scalar()
        return bool(got)

    def unlock(self, namespace: int, key: int) -> None:
        """Unlock. **Must run on the connection that took the lock**, so it shares `self._conn` with `try_lock`."""
        with self._guard:
            try:
                self._conn.execute(
                    text("SELECT pg_advisory_unlock(:ns, :id)"),
                    {"ns": int(namespace), "id": int(key)},
                )
            except Exception:  # noqa: BLE001
                # This connection broke. The lock on the PG side actually went away with the connection, so there's no deadlock;
                # but the next try_lock takes the lock on a **new connection**, and the locks of other plans that were held
                # on this connection also vanished — those plans lose mutual-exclusion protection this round.
                # Logged as ERROR so that "sent twice" can be traced back to its source in the logs.
                logger.error("advisory unlock failed; the lock connection may have dropped: ns=%s id=%s", namespace, key, exc_info=True)

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            self._engine.dispose()


class SendScheduler:
    """Scheduler main thread + a fixed pool of sending threads. One instance per process."""

    def __init__(
        self,
        *,
        session_factory=SessionLocal,
        locks: AdvisoryLocks | None = None,
        tick_seconds: float = TICK_SECONDS,
        workers: int = MAX_CONCURRENT_SCHEDULES,
    ) -> None:
        self._session_factory = session_factory
        self._locks = locks if locks is not None else AdvisoryLocks()
        self._tick_seconds = float(tick_seconds)
        self._worker_count = max(1, int(workers))
        self.instance_key = f"{socket.gethostname()}:{os.getpid()}"

        self._jobs: queue.Queue = queue.Queue()
        # Take a slot before dispatching; if none is available, stop for this round — the `live_workers >= MAX` of the pseudocode.
        # Take the slot before grabbing the lock: if grabbing the lock fails, return the slot, so the round isn't
        # filled up by plans that couldn't get their locks.
        self._slots = threading.Semaphore(self._worker_count)
        self._live = 0
        self._live_guard = threading.Lock()
        self._last_error = ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ Lifecycle

    def start(self) -> None:
        for index in range(self._worker_count):
            thread = threading.Thread(target=self._worker_loop, name=f"sales-sender-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("send scheduler started: %s, %s sending threads", self.instance_key, self._worker_count)

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        """Main loop. **Must not raise here** — a failed round only logs, and the next round runs as usual (§4.2 constraint 4)."""
        self._cleanup_stale_heartbeats()
        while not self._stop.is_set():
            started = time.time()
            try:
                self._tick()
                self._last_error = ""
            except Exception as exc:  # noqa: BLE001
                logger.exception("send scheduler: this round failed")
                self._record_error(exc)
            self._write_heartbeat()
            # The heartbeat is written after tick: it must carry this round's live_workers and last_error.
            # But it itself **must not** stall the main loop, so `_write_heartbeat` catches its own exceptions internally.
            self._stop.wait(max(1.0, self._tick_seconds - (time.time() - started)))
        self._shutdown_workers()
        self._locks.close()
        logger.info("send scheduler exited: %s", self.instance_key)

    def _shutdown_workers(self) -> None:
        for _ in self._threads:
            self._jobs.put(None)

    # ------------------------------------------------------------ One main-thread round

    def _tick(self) -> None:
        db = self._session_factory()
        try:
            expire_overdue_schedules(db)
            reclaim_stale_items(db)
            # Fetch PREGEN_LEAD_TIME early, not only when due — head drafts need time.
            # After dispatch the sending thread waits until due itself; meanwhile the plan is still armed, so cancellation costs nothing.
            schedule_ids = due_send_schedules(db)
            senders = self._sender_keys(db, schedule_ids)
        finally:
            db.close()

        for schedule_id in schedule_ids:
            if not self._slots.acquire(blocking=False):
                logger.info("send scheduler: all %s sending threads busy; remaining plans deferred to the next round", self._worker_count)
                break
            sender_key = senders.get(schedule_id, _SENDER_KEY_UNPINNED)
            if not self._locks.try_lock(_NS_SCHED, schedule_id):
                self._slots.release()
                continue
            if not self._locks.try_lock(_NS_SENDER, sender_key):
                # This sender is sending another plan. **Release the plan lock too**, don't hold it — leave it to the next round,
                # by which time the previous plan has most likely finished.
                self._locks.unlock(_NS_SCHED, schedule_id)
                self._slots.release()
                continue
            self._bump_live(1)
            self._jobs.put((int(schedule_id), int(sender_key)))

    def _sender_keys(self, db, schedule_ids: list[int]) -> dict[int, int]:
        """Plan -> sender id. **Use the one pinned in the plan table; don't call `_effective_email_config` on the fly**.

        Under the `round_robin` strategy an on-the-fly call may switch accounts per email, so what's locked and what actually sends wouldn't match,
        which is the same as no lock (§4.2).
        """
        if not schedule_ids:
            return {}
        rows = (
            db.query(SalesAgentSendScheduleORM.id, SalesAgentSendScheduleORM.email_account_id)
            .filter(SalesAgentSendScheduleORM.id.in_([int(x) for x in schedule_ids]))
            .all()
        )
        return {int(row[0]): int(row[1] or _SENDER_KEY_UNPINNED) for row in rows}

    # ------------------------------------------------------------ Sending threads

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            schedule_id, sender_key = job
            try:
                self._run_schedule(schedule_id)
            except BaseException as exc:  # noqa: BLE001
                # **Exceptions must not escape the thread.** If they do, this thread dies silently with its lock still held on the lock connection,
                # and that plan won't be picked up by anyone until the process restarts. The main thread's try can't catch what's
                # raised here (different stack), so every sending thread must catch it itself.
                logger.exception("send scheduler: plan %s execution failed", schedule_id)
                self._record_error(exc)
            finally:
                # Unlock before releasing the slot: in the reverse order the main thread might redispatch before the unlock lands,
                # wasting a lock grab.
                self._locks.unlock(_NS_SENDER, sender_key)
                self._locks.unlock(_NS_SCHED, schedule_id)
                self._bump_live(-1)
                self._slots.release()

    def _run_schedule(self, schedule_id: int) -> None:
        """The full lifecycle of one plan, entirely within this one sending thread."""
        pregenerate_schedule_drafts(self._session_factory, schedule_id, PREGEN_HEAD_COUNT)
        if not wait_until_scheduled(self._session_factory, schedule_id):
            logger.info("send scheduler: plan %s was cancelled or rescheduled while waiting; not sending", schedule_id)
            return
        # armed -> running flips here, **before any sending action**. "Cancel during sending" hinges entirely
        # on this step: cancellation takes effect at "re-read status before each email", which presupposes someone flipped it.
        if not mark_schedule_running(self._session_factory, schedule_id):
            logger.info("send scheduler: plan %s is no longer armed; not sending", schedule_id)
            return
        stats = run_one_schedule(self._session_factory, schedule_id)
        logger.info("send scheduler: plan %s finished %s", schedule_id, stats)

    # ------------------------------------------------------------ Heartbeat

    def _write_heartbeat(self) -> None:
        """One row per round, updated in place by `instance_key`.

        This table has no indexes except `instance_key` — this row is written every 60 seconds, 520k times a year,
        and only with no index on updated columns does PG use HOT updates, reclaiming dead tuples in-page (§3.5).
        Adding an index on `last_beat_at` would cause index bloat on every heartbeat.
        """
        # Opening the session is itself inside the try: when the DB is down / the pool is full, that's where it raises,
        # and this method is called outside the main loop's try (it must carry this round's last_error),
        # so leaking it would mean "heartbeat can't be written -> the whole scheduler stops", exactly backwards.
        db = None
        try:
            db = self._session_factory()
            if not session_has_table(db, SalesAgentSchedulerHeartbeatORM.__tablename__):
                return
            row = (
                db.query(SalesAgentSchedulerHeartbeatORM)
                .filter(SalesAgentSchedulerHeartbeatORM.instance_key == self.instance_key)
                .first()
            )
            if row is None:
                row = SalesAgentSchedulerHeartbeatORM(
                    instance_key=self.instance_key,
                    hostname=socket.gethostname()[:255],
                    pid=os.getpid(),
                    created_at=datetime.utcnow(),
                )
                db.add(row)
            row.last_beat_at = datetime.utcnow()
            row.live_workers = self._live
            row.last_error = self._last_error
            db.commit()
        except Exception:  # noqa: BLE001
            # Failing to write the heartbeat shouldn't stop scheduling — that would be "can't be seen, so stop working altogether".
            _rollback(db)
            logger.exception("send scheduler: heartbeat write failed")
        finally:
            _close(db)

    def _cleanup_stale_heartbeats(self) -> None:
        """At startup clear old rows with no activity for 7 days. Every pid change leaves a row; without clearing it keeps growing."""
        db = None
        try:
            db = self._session_factory()
            if not session_has_table(db, SalesAgentSchedulerHeartbeatORM.__tablename__):
                return
            cutoff = datetime.utcnow() - HEARTBEAT_RETENTION
            removed = (
                db.query(SalesAgentSchedulerHeartbeatORM)
                .filter(
                    SalesAgentSchedulerHeartbeatORM.last_beat_at < cutoff,
                    SalesAgentSchedulerHeartbeatORM.instance_key != self.instance_key,
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            if removed:
                logger.info("send scheduler: cleared %s expired heartbeat rows", removed)
        except Exception:  # noqa: BLE001
            _rollback(db)
            logger.exception("send scheduler: failed to clear expired heartbeats")
        finally:
            _close(db)

    # ------------------------------------------------------------ Small helpers

    def _bump_live(self, delta: int) -> None:
        with self._live_guard:
            self._live = max(0, self._live + delta)

    def _record_error(self, exc: BaseException) -> None:
        """Record the exception summary on the heartbeat row. Exceptions don't escape the thread, but must leave a trace — otherwise "silent failure" merely
        changes from "no logs" to "in the logs, but nobody reads them"."""
        text_ = f"{type(exc).__name__}: {exc}".strip()
        if not text_ or text_.endswith(":"):
            text_ = traceback.format_exc().strip().splitlines()[-1]
        self._last_error = text_[:2000]


def _rollback(db) -> None:
    """`db` may be None — opening the session is itself where it raises (DB down / pool full)."""
    if db is None:
        return
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("send scheduler: rollback failed", exc_info=True)


def _close(db) -> None:
    if db is None:
        return
    try:
        db.close()
    except Exception:  # noqa: BLE001
        logger.debug("send scheduler: close failed", exc_info=True)


def scheduler_enabled() -> tuple[bool, str]:
    """Whether it can start. Returns `(can, why not if not)`."""
    dialect = _db_engine.dialect.name
    if dialect != "postgresql":
        return False, (
            f"当前 DATABASE_URL 的方言是 {dialect},不是 postgresql,定时发送调度器不启动。\n"
            "不降级成无锁运行是故意的:pg_try_advisory_lock 在 SQLite 上根本不存在,\n"
            "with_for_update(skip_locked=True) 被 SQLAlchemy 静默忽略 —— 跑起来会「绿」,\n"
            "但并发正确性从未被验证过,正是 CLAUDE.md 点名的那个陷阱。\n"
            "要在本地验并发,照 scripts/verify_publish_claim.py 的做法把 DATABASE_URL 指到本机 PG。"
        )
    return True, ""


def start_send_scheduler(block: bool = False) -> SendScheduler | None:
    """Start the scheduler. **Doesn't start unless on PG**, returning None.

    With `block=True` it occupies the calling thread to run the main loop (for the `python -m` entry); with `block=False` it starts
    another thread and returns the instance for the caller to `stop()`.
    """
    ok, why = scheduler_enabled()
    if not ok:
        logger.info(why)
        return None
    scheduler = SendScheduler()
    scheduler.start()
    if not block:
        threading.Thread(target=scheduler.run_forever, name="sales-scheduler", daemon=True).start()
        return scheduler

    def _on_signal(signum, _frame):
        logger.info("send scheduler: received signal %s; wrapping up and exiting", signum)
        scheduler.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:
            # Signal handlers can't be installed outside the main thread; a direct systemd SIGKILL isn't any worse —
            # session-level locks are released automatically when the connection drops.
            pass
    scheduler.run_forever()
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("SALES_AGENT_SCHEDULER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )
    start_send_scheduler(block=True)
