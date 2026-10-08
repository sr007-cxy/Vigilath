"""Scheduler process (A5, design §4.2).

**Concurrency correctness can't be tested here, and the way it can't is easily misleading.** Advisory locks don't exist
on SQLite at all, so everything below uses a fake lock object -- it verifies the **orchestration** of "who should take the lock, how to back off when it can't,
whether any unlock is missed", not PG's mutual exclusion semantics. Real mutual exclusion must be verified on real PG
(acceptance 2b), the same situation as `verify_publish_claim.py`.

So this file guards four other things, all of the "no error, but the feature silently disappears" kind:

1. **No missed unlocks.** When a schedule finishes/raises/is cancelled, both locks must be released -- miss one, and that schedule
   won't be picked up by anyone until the process restarts;
2. **Exceptions must not escape the send thread.** If they do, the thread dies, showing up as "not sent on time, no error";
3. **No slot leaks.** Failing to get the lock must return the slot, or after a few rounds the scheduler is "full"
   and never dispatches any schedule again;
4. **The heartbeat keeps writing.** Under L2 the scheduler is a single point, and the heartbeat table is the only place to notice it died.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_scheduler.py -q
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.sales_agent import scheduler as scheduler_mod
from agents.sales_agent.models import (
    SalesAgentSchedulerHeartbeatORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.scheduler import (
    HEARTBEAT_RETENTION,
    MAX_CONCURRENT_SCHEDULES,
    SendScheduler,
    _NS_SCHED,
    _NS_SENDER,
    _SENDER_KEY_UNPINNED,
    scheduler_enabled,
    start_send_scheduler,
)
from geo.database import Base

USER = 1


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    yield maker
    engine.dispose()


class FakeLocks:
    """Fake lock. Records every lock/unlock and can make specified keys fail to lock.

    **It doesn't simulate mutual exclusion** -- try_lock twice on the same key both succeed. This is deliberate: mutual exclusion is
    PG's job; what's verified here is the caller's orchestration (whether to take it, how to back off, whether any unlock is missed).
    """

    def __init__(self, refuse: set[tuple[int, int]] | None = None) -> None:
        self.refuse = refuse or set()
        self.held: set[tuple[int, int]] = set()
        self.log: list[tuple[str, int, int]] = []
        self.closed = False
        self._guard = threading.Lock()

    def try_lock(self, namespace: int, key: int) -> bool:
        with self._guard:
            if (namespace, key) in self.refuse:
                self.log.append(("refused", namespace, key))
                return False
            self.held.add((namespace, key))
            self.log.append(("lock", namespace, key))
            return True

    def unlock(self, namespace: int, key: int) -> None:
        with self._guard:
            self.held.discard((namespace, key))
            self.log.append(("unlock", namespace, key))

    def close(self) -> None:
        self.closed = True


def _seed_schedule(db, schedule_id: int, *, account_id: int | None = 7, minutes: int = 1) -> int:
    """An armed schedule due in `minutes` minutes. By default it falls within the 5-minute lead time and will be picked up."""
    db.add(SalesAgentSendScheduleORM(
        id=schedule_id,
        user_id=USER,
        channel="email",
        email_account_id=account_id,
        scheduled_at=datetime.utcnow() + timedelta(minutes=minutes),
        status="armed",
    ))
    db.commit()
    return schedule_id


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture()
def scheduler(factory):
    """Start real send threads, but leave `_run_schedule` for each test to replace."""
    made: list[SendScheduler] = []

    def build(*, locks: FakeLocks | None = None, workers: int = 2) -> SendScheduler:
        sched = SendScheduler(
            session_factory=factory, locks=locks or FakeLocks(), workers=workers, tick_seconds=0.05
        )
        sched.start()
        made.append(sched)
        return sched

    yield build
    for sched in made:
        sched.stop()
        sched._shutdown_workers()


# ---------------------------------------------------------------- does it start


@pytest.fixture()
def sqlite_dialect(monkeypatch):
    """Pin the engine read by `scheduler_enabled()` to SQLite.

    **Can't rely on `.env` happening to be SQLite.** `Settings.DATABASE_URL` defaults to sqlite, but dev machines'
    `.env` usually points at a local PG, so on those machines these two cases aren't merely "failing" --
    `test_start_returns_none_and_spawns_nothing_on_sqlite` would actually start the scheduler,
    connected to the DB in `.env`, and with no cleanup fixture the thread just leaks. Right now
    `send_schedules` is empty so it looks fine; once the table has pending schedules, running
    pytest once on such a machine could actually send mail. Tests must bring their own dialect, not borrow it from the environment.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(scheduler_mod, "_db_engine", engine)
    yield engine
    engine.dispose()


def test_sqlite_refuses_to_start(sqlite_dialect):
    """Local default is SQLite, and the scheduler must refuse to start; **it may not degrade to running without locks**.

    Green from a degraded run is false green -- `pg_try_advisory_lock` doesn't exist on SQLite,
    `skip_locked` is silently ignored, and concurrency bugs never reproduce locally. CLAUDE.md names this trap.
    """
    ok, why = scheduler_enabled()
    assert ok is False
    assert "postgresql" in why
    # The refusal reason must explain how to verify concurrency locally, not just say "unsupported".
    assert "DATABASE_URL" in why


def test_start_returns_none_and_spawns_nothing_on_sqlite(sqlite_dialect):
    before = threading.active_count()
    assert start_send_scheduler(block=False) is None
    assert threading.active_count() == before


def test_max_concurrent_schedules_is_never_one():
    """Under L2 this constant is the **only** source of parallelism in the whole system.

    Setting it to 1 means 10 users who all scheduled 15:00 queue up in a line, and the 10th person's "15:00" becomes
    15:54 -- acceptance 2b (multiple users at the same time don't queue) fails outright. Pinned here so nobody sets it to 1
    "to play it safe first".
    """
    assert MAX_CONCURRENT_SCHEDULES >= 2


# ---------------------------------------------------------------- dispatch and locks


def test_due_schedule_is_locked_dispatched_and_unlocked(scheduler, factory):
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    locks = FakeLocks()
    sched = scheduler(locks=locks)
    ran: list[int] = []
    sched._run_schedule = lambda schedule_id: ran.append(schedule_id)

    sched._tick()

    assert _wait(lambda: ran == [1])
    # Both locks must be released. Miss one = this schedule won't be picked up again until the process restarts.
    assert _wait(lambda: not locks.held), f"锁没解干净:{locks.held}"
    assert ("lock", _NS_SCHED, 1) in locks.log
    assert ("lock", _NS_SENDER, 7) in locks.log


def test_sender_lock_taken_before_dispatch_not_after(scheduler, factory):
    """The mailbox lock must be taken **before** dispatch.

    Failing to get it means this mailbox is sending another schedule -- and "different users = different mailboxes" doesn't hold:
    users without their own mailbox all fall back to the same platform-level account. Taking it after dispatch, two schedules are already
    running in parallel and the 30-second throttle is bypassed entirely.
    """
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    locks = FakeLocks(refuse={(_NS_SENDER, 7)})
    sched = scheduler(locks=locks)
    ran: list[int] = []
    sched._run_schedule = lambda schedule_id: ran.append(schedule_id)

    sched._tick()

    time.sleep(0.1)
    assert ran == []
    # Release the schedule lock too, don't hold it -- leave it for the next round, by which time the previous schedule has most likely finished.
    assert not locks.held
    assert ("unlock", _NS_SCHED, 1) in locks.log


def test_refused_lock_gives_the_slot_back(scheduler, factory):
    """Failing to get the lock must return the slot.

    Otherwise after a few rounds the scheduler is "full" -- it never dispatches any schedule again, and with no error.
    """
    db = factory()
    for index in range(1, 4):
        _seed_schedule(db, index, account_id=index)
    db.close()

    locks = FakeLocks(refuse={(_NS_SCHED, 1), (_NS_SCHED, 2), (_NS_SCHED, 3)})
    sched = scheduler(locks=locks, workers=2)
    sched._run_schedule = lambda schedule_id: None

    for _ in range(5):
        sched._tick()

    # 5 rounds x 3 schedules all fail to lock; not a single slot may be lost.
    acquired = [sched._slots.acquire(blocking=False) for _ in range(2)]
    assert acquired == [True, True], "槽位漏了,调度器已经不会再派发任何计划"


def test_only_max_concurrent_schedules_run_at_once(scheduler, factory):
    """Concurrent running schedules are capped at `workers`; the 3rd waits for the next round."""
    db = factory()
    for index in range(1, 4):
        _seed_schedule(db, index, account_id=index)
    db.close()

    locks = FakeLocks()
    sched = scheduler(locks=locks, workers=2)
    gate = threading.Event()
    started: list[int] = []

    def _block(schedule_id: int) -> None:
        started.append(schedule_id)
        gate.wait(3)

    sched._run_schedule = _block
    sched._tick()

    assert _wait(lambda: len(started) == 2)
    time.sleep(0.15)
    assert len(started) == 2, "槽位没卡住,第 3 个计划被一起派发了"
    # The 3rd schedule **didn't take the lock**, rather than "took the lock but is queued" -- queuing means the lock is held for nothing,
    # and no other process/next round can touch it.
    assert (_NS_SCHED, 3) not in locks.held
    gate.set()


def test_unpinned_sender_all_share_one_lock(scheduler, factory):
    """Schedules without a pinned mailbox all crowd onto the id=0 lock, **serialized**.

    Not pinned means falling back to the platform-level account at execution, which is shared by everyone -- exactly the kind that
    most needs mutual exclusion. Better slow than concurrent.
    """
    db = factory()
    _seed_schedule(db, 1, account_id=None)
    db.close()

    sched = scheduler(locks=FakeLocks())
    keys = sched._sender_keys(sched._session_factory(), [1])
    assert keys == {1: _SENDER_KEY_UNPINNED}


def test_sender_key_comes_from_the_pinned_column_not_a_live_lookup(scheduler, factory):
    """The lock is taken by the `email_account_id` pinned in the schedule table.

    Under the `round_robin` policy, calling `_effective_email_config` on the fly may switch account per email, so what's locked and
    what's actually used to send don't match, which is no lock at all.
    """
    db = factory()
    _seed_schedule(db, 1, account_id=42)
    db.close()

    sched = scheduler(locks=FakeLocks())
    assert sched._sender_keys(sched._session_factory(), [1]) == {1: 42}


# ---------------------------------------------------------------- exceptions don't escape the thread


def test_worker_survives_a_failing_schedule(scheduler, factory):
    """When a schedule raises, the send thread must survive and carry on with the next one.

    If it escapes, the thread silently dies, showing up as "not sent on time, no error, no log" -- the worst failure mode for this
    feature, whose whole selling point is "set it and forget it".
    """
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    locks = FakeLocks()
    # One send thread -- the second run must land on the **same** thread for "it survived" to mean anything.
    sched = scheduler(locks=locks, workers=1)
    seen: list[int] = []

    def _boom(schedule_id: int) -> None:
        seen.append(schedule_id)
        if len(seen) == 1:
            raise RuntimeError("发信时炸了")

    sched._run_schedule = _boom
    sched._tick()
    assert _wait(lambda: seen == [1])
    assert _wait(lambda: not locks.held)
    sched._tick()

    assert _wait(lambda: seen == [1, 1]), f"线程没活下来:{seen}"
    assert _wait(lambda: not locks.held), f"抛异常那次的锁没解:{locks.held}"


def test_a_failing_schedule_is_recorded_on_the_heartbeat(scheduler, factory):
    """Exceptions don't escape the thread, **but must leave a trace** -- otherwise it just goes from "no log" to "nobody knows"."""
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    sched = scheduler(locks=FakeLocks(), workers=1)

    def _boom(schedule_id: int) -> None:
        raise RuntimeError("发信时炸了")

    sched._run_schedule = _boom
    sched._tick()
    assert _wait(lambda: "发信时炸了" in sched._last_error)

    sched._write_heartbeat()
    db = factory()
    row = db.query(SalesAgentSchedulerHeartbeatORM).one()
    assert "发信时炸了" in row.last_error
    db.close()


def test_a_failing_tick_does_not_stop_the_main_loop(scheduler, factory, monkeypatch):
    """An error in one main-thread round is only logged; the next round runs as usual."""
    sched = scheduler(locks=FakeLocks())
    ticks: list[int] = []

    def _boom():
        ticks.append(1)
        if len(ticks) <= 2:
            raise RuntimeError("扫表炸了")
        sched.stop()

    sched._tick = _boom
    sched.run_forever()

    assert len(ticks) >= 3, "第一轮出错就把主循环带垮了"


def test_locks_are_released_when_the_schedule_was_cancelled(scheduler, factory):
    """Cancelled while waiting for the time -> `_run_schedule` returns immediately, and the locks must still be released."""
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    locks = FakeLocks()
    sched = scheduler(locks=locks)
    sched._run_schedule = lambda schedule_id: None
    sched._tick()

    assert _wait(lambda: not locks.held)
    assert sched._live == 0


# ---------------------------------------------------------------- heartbeat


def test_heartbeat_updates_one_row_in_place(scheduler, factory):
    """One row per process, updated in place.

    Inserting a row per round would add 520K rows a year; more importantly, troubleshooting looks at "when was this process's last
    heartbeat", which can only be read from a single row.
    """
    sched = scheduler(locks=FakeLocks())
    sched._write_heartbeat()
    first = _heartbeat(factory).last_beat_at
    time.sleep(0.01)
    sched._write_heartbeat()

    db = factory()
    rows = db.query(SalesAgentSchedulerHeartbeatORM).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].last_beat_at >= first
    assert rows[0].instance_key == sched.instance_key


def test_heartbeat_carries_live_workers(scheduler, factory):
    """`live_workers` staying at the cap long-term is the signal to "raise MAX_CONCURRENT_SCHEDULES" (§7.4),
    so it must really reflect the number running, not always be 0."""
    db = factory()
    _seed_schedule(db, 1, account_id=7)
    db.close()

    sched = scheduler(locks=FakeLocks(), workers=2)
    gate = threading.Event()
    sched._run_schedule = lambda schedule_id: gate.wait(3)
    sched._tick()

    assert _wait(lambda: sched._live == 1)
    sched._write_heartbeat()
    assert _heartbeat(factory).live_workers == 1
    gate.set()
    assert _wait(lambda: sched._live == 0)


def test_heartbeat_failure_does_not_stop_the_scheduler(scheduler, factory, monkeypatch):
    """Failing to write the heartbeat must not stop scheduling -- that would be "can't be seen, so stop working altogether"."""
    sched = scheduler(locks=FakeLocks())

    def _broken():
        raise RuntimeError("库挂了")

    monkeypatch.setattr(sched, "_session_factory", _broken)
    sched._write_heartbeat()  # passes as long as it doesn't raise


def test_missing_tables_are_not_an_error_every_60_seconds(scheduler):
    """Before the migration runs, a heartbeat stack trace every minute would flood the logs."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    empty = sessionmaker(bind=engine)
    sched = scheduler(locks=FakeLocks())
    sched._session_factory = empty
    sched._write_heartbeat()
    sched._cleanup_stale_heartbeats()
    engine.dispose()


def test_stale_heartbeats_are_cleaned_but_not_my_own(scheduler, factory):
    """Each pid change leaves a row and grows forever if not cleaned; but **at the moment of cleanup, its own row must stay**."""
    db = factory()
    old = datetime.utcnow() - HEARTBEAT_RETENTION - timedelta(days=1)
    db.add(SalesAgentSchedulerHeartbeatORM(instance_key="old-host:1", hostname="old-host", pid=1, last_beat_at=old))
    db.add(SalesAgentSchedulerHeartbeatORM(instance_key="live-host:2", hostname="live-host", pid=2, last_beat_at=datetime.utcnow()))
    db.commit()
    db.close()

    sched = scheduler(locks=FakeLocks())
    sched.instance_key = "old-host:1"     # pretend this process happens to reuse that key
    sched._cleanup_stale_heartbeats()

    db = factory()
    keys = {row.instance_key for row in db.query(SalesAgentSchedulerHeartbeatORM).all()}
    db.close()
    assert keys == {"old-host:1", "live-host:2"}


def _heartbeat(factory) -> SalesAgentSchedulerHeartbeatORM:
    db = factory()
    try:
        return db.query(SalesAgentSchedulerHeartbeatORM).one()
    finally:
        db.close()


# ---------------------------------------------------------------- thread pool


def test_the_worker_pool_is_fixed_not_one_thread_per_schedule(scheduler, factory):
    """Send threads are a fixed, long-lived pool.

    The pseudocode in design §4.2 says `spawn_worker(sched)`, but every new thread creates a new event loop
    and an httpx connection pool (`_thread_event_loop` / `get_sales_agent_model` are both bucketed per thread),
    and nobody closes them when the thread exits -- hundreds of schedules a day means hundreds of leaked fds, while the §7.3
    resource table says "httpx connection pool at most 4".
    """
    db = factory()
    for index in range(1, 7):
        _seed_schedule(db, index, account_id=index)
    db.close()

    sched = scheduler(locks=FakeLocks(), workers=2)
    done = threading.Semaphore(0)
    threads: set[int] = set()

    def _note(schedule_id: int) -> None:
        threads.add(threading.get_ident())
        done.release()

    sched._run_schedule = _note
    for _ in range(3):
        sched._tick()
        for _ in range(2):
            assert done.acquire(timeout=3)

    assert len(threads) <= 2, f"起了 {len(threads)} 条发送线程,应该只有 2 条长驻的"


def test_stopping_does_not_leave_worker_threads_behind(factory):
    sched = SendScheduler(session_factory=factory, locks=FakeLocks(), workers=2, tick_seconds=0.01)
    sched.start()
    sched.stop()
    sched.run_forever()
    assert _wait(lambda: all(not t.is_alive() for t in sched._threads))
