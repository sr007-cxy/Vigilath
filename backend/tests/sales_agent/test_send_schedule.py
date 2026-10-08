"""Service layer of scheduled send (A4, design §4.1).

This layer can be fully tested because threads, advisory locks, heartbeats and the 60-second tick all stay in
`scheduler.py`. **But concurrency correctness can't be tested** -- `skip_locked` is silently ignored by SQLAlchemy
on SQLite, so that part must be verified on real PG (see CLAUDE.md and acceptance 2b).

Four things to guard:

1. **Cancel really stops it** -- the user's cancel only changes a DB field; if the executor doesn't actively re-check, it's as if
   never clicked. While waiting for the time, before each email, after filling drafts: none of the three re-check points may be dropped;
2. **Only recipient-side failures fall back** -- switching recipients on sender-side faults is useless and only burns through every mailbox
   at the company in one config error;
3. **Skips must say which company and why** -- Sales Agent never emits generic errors;
4. **Never bypass `confirm_outreach_send`** -- the compliance gate and delivery records hang off it.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_send_schedule.py -q
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import agents.sales_agent.service.email_send as email_send_mod
from agents.sales_agent.models import (
    SalesAgentComplianceLogORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailAccountORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSendScheduleItemORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.service.common import SalesAgentError
from agents.sales_agent.service.send_schedule import (
    PREGEN_HEAD_COUNT,
    SCHEDULE_EXPIRY,
    arm_send_schedule,
    batch_finish_text,
    cancel_send_schedule,
    due_send_schedules,
    expire_overdue_schedules,
    mark_schedule_running,
    plan_send_schedule,
    pregenerate_schedule_drafts,
    reclaim_stale_items,
    reschedule_send_schedule,
    run_one_schedule,
    sender_daily_sent_count,
    wait_until_scheduled,
)
from agents.sales_agent.service.companies import (
    LEGACY_BATCH_ID,
    resolve_batch_lead_ids,
    session_recommendation_batches,
)
from agents.sales_agent.service.suppression import (
    SUPPRESSION_REASON_UNSUBSCRIBED,
    add_email_suppression,
)
from agents.sales_agent.time_parse import bj_day_start_utc
from geo.database import Base

USER = 1
# Tomorrow 15:00 Beijing time -- safely in the future, so it won't drift in/out of the window
# depending on when the tests run.
TOMORROW_3PM = None


def _tomorrow_local_hour(hour: int = 15) -> datetime:
    """Naive UTC for tomorrow at `hour` o'clock Beijing time."""
    return bj_day_start_utc(0) + timedelta(days=1, hours=hour)


@pytest.fixture()
def factory():
    """All sessions share one connection -- the service layer "opens a session each time and closes it after use",
    and the default SQLite in-memory DB would give each connection its own empty DB."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    yield maker
    engine.dispose()


@pytest.fixture()
def db(factory):
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _seed(db, companies: list[tuple[str, list[str]]], *, with_draft: bool = False) -> list[int]:
    """Create N companies + their contacts; return the list of lead_ids."""
    lead_ids = []
    for index, (name, emails) in enumerate(companies, start=1):
        company = SalesAgentCompanyORM(id=index, name=name, source="manual", data_status="active")
        lead = SalesAgentLeadRecommendationORM(id=index, user_id=USER, company_id=index)
        db.add_all([company, lead])
        for slot, email in enumerate(emails):
            db.add(SalesAgentCompanyContactORM(
                company_id=index, email=email, consent_status="unknown",
                # is_reachable desc + id desc decide the first choice; here make the one written first come first
                is_reachable=(slot == 0),
            ))
        if with_draft:
            db.add(SalesAgentOutreachLogORM(
                lead_id=index, channel="email", subject=f"给{name}", content="正文", send_status="draft",
            ))
        lead_ids.append(index)
    db.commit()
    return lead_ids


def _seed_account(db, *, account_id: int = 1) -> SalesAgentEmailAccountORM:
    account = SalesAgentEmailAccountORM(
        id=account_id, scope="user", user_id=USER, provider_type="smtp", enabled=True,
        is_default=True, from_email="me@example.com", smtp_host="smtp.example.com",
        smtp_username="me@example.com", smtp_secret_encrypted="x",
    )
    db.add(account)
    db.commit()
    return account


# ---------------------------------------------------------------- create schedule


def test_plan_includes_and_skips_with_specific_reasons(db, factory):
    lead_ids = _seed(db, [
        ("有邮箱科技", ["a@example.com"]),
        ("没邮箱科技", []),
        ("退订科技", ["gone@example.com"]),
    ])
    add_email_suppression(db, USER, "gone@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    result = plan_send_schedule(db, USER, [*lead_ids, 999], _tomorrow_local_hour())

    assert [x["company"] for x in result["included"]] == ["有邮箱科技"]
    reasons = {x["company"]: x["reason"] for x in result["skipped"]}
    assert "没有可用的公开收件邮箱" in reasons["没邮箱科技"]
    assert "退订" in reasons["退订科技"]
    # Recommendations that no longer exist also get their own row; they must not be silently dropped
    assert any(x["lead_id"] == 999 for x in result["skipped"])
    assert result["schedule"]["status"] == "draft"
    assert result["schedule"]["total"] == 1


def test_plan_attaches_an_existing_draft(db):
    lead_ids = _seed(db, [("甲", ["a@example.com"])], with_draft=True)
    result = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())
    assert result["included"][0]["has_draft"] is True
    item = db.query(SalesAgentSendScheduleItemORM).one()
    assert item.status == "pending" and item.outreach_log_id


def test_plan_marks_missing_drafts_pending_draft(db):
    """Drafts can't be fully generated within the create-schedule request (45 seconds per email vs nginx 60 seconds)."""
    lead_ids = _seed(db, [("甲", ["a@example.com"])])
    plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())
    assert db.query(SalesAgentSendScheduleItemORM).one().status == "pending_draft"


def test_plan_does_not_attach_an_already_sent_draft(db):
    """Attaching already-sent drafts would hit `_guard_resend`'s cooldown at send time, wasting the whole run."""
    lead_ids = _seed(db, [("甲", ["a@example.com"])])
    db.add(SalesAgentOutreachLogORM(lead_id=1, channel="email", content="正文", send_status="sent"))
    db.commit()
    plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())
    assert db.query(SalesAgentSendScheduleItemORM).one().status == "pending_draft"


def test_plan_refuses_an_all_skipped_batch_and_leaves_nothing_behind(db):
    _seed(db, [("没邮箱科技", [])])
    with pytest.raises(SalesAgentError) as excinfo:
        plan_send_schedule(db, USER, [1], _tomorrow_local_hour())
    assert "收件邮箱" in str(excinfo.value)   # say why, not just "creation failed"
    # Empty schedules must not stay in the DB -- they can't be armed, and nobody will delete them
    assert db.query(SalesAgentSendScheduleORM).count() == 0
    assert db.query(SalesAgentSendScheduleItemORM).count() == 0


def test_plan_rejects_an_empty_selection(db):
    with pytest.raises(SalesAgentError):
        plan_send_schedule(db, USER, [], _tomorrow_local_hour())


def test_plan_hint_says_it_starts_then_not_finishes(db):
    """In the user's mind "send at 15:00" easily means "finished by 15:00", while throttling means it can only be
    "start sending at 15:00". This must be echoed back."""
    lead_ids = _seed(db, [(f"第{i}家", [f"a{i}@example.com"]) for i in range(1, 13)])
    result = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour(), throttle_seconds=30)
    assert "开始发" in result["hint"]
    assert "分钟发完" in result["hint"]


def test_batch_finish_text_is_empty_for_a_single_company(db):
    schedule = SalesAgentSendScheduleORM(scheduled_at=_tomorrow_local_hour(), throttle_seconds=30)
    assert batch_finish_text(schedule, 1) == ""
    assert "6 分钟" in batch_finish_text(schedule, 13)   # 12 intervals x 30 seconds


# ---------------------------------------------------------------- arm


def _planned(db, count: int = 2, **kwargs) -> int:
    lead_ids = _seed(db, [(f"第{i}家", [f"a{i}@example.com"]) for i in range(1, count + 1)], with_draft=True)
    return plan_send_schedule(db, USER, lead_ids, kwargs.pop("scheduled_at", _tomorrow_local_hour()), **kwargs)["schedule"]["id"]


def test_arm_pins_the_sender_account(db):
    """If not pinned: the platform-level mailbox is shared across users, and round_robin switches accounts per email --
    the mailbox lock has no object to lock and the daily quota has no aggregation key."""
    account = _seed_account(db)
    schedule_id = _planned(db)
    result = arm_send_schedule(db, USER, schedule_id)
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    assert row.status == "armed"
    assert row.email_account_id == account.id
    assert "开始给这 2 家发" in result["hint"]


def test_arm_refuses_without_a_usable_sender(db):
    """Scheduled at 14:00 to send at 15:00, the user deletes the mailbox in between, and the whole batch fails at send time without them knowing.
    It must be blocked at arm time -- this is the premise of "no circuit breaking at runtime"."""
    schedule_id = _planned(db)
    with pytest.raises(SalesAgentError) as excinfo:
        arm_send_schedule(db, USER, schedule_id)
    assert "发件箱" in str(excinfo.value)
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "draft"


def test_arm_accepts_any_future_hour(db):
    """Only one time rule remains: not earlier than now. The previously hardcoded 09:00-18:00 send window has been removed entirely --
    if the user says send at 2 a.m., it's 2 a.m.; the system doesn't impose business hours on them."""
    _seed_account(db)
    schedule_id = _planned(db, scheduled_at=_tomorrow_local_hour(2))
    arm_send_schedule(db, USER, schedule_id)
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "armed"


def test_arm_refuses_a_past_time(db):
    _seed_account(db)
    schedule_id = _planned(db)
    db.get(SalesAgentSendScheduleORM, schedule_id).scheduled_at = datetime.utcnow() - timedelta(hours=1)
    db.commit()
    with pytest.raises(SalesAgentError) as excinfo:
        arm_send_schedule(db, USER, schedule_id)
    assert "已经过去" in str(excinfo.value)


def test_arm_is_idempotent(db):
    _seed_account(db)
    schedule_id = _planned(db)
    arm_send_schedule(db, USER, schedule_id)
    again = arm_send_schedule(db, USER, schedule_id)
    assert "已经确认过" in again["hint"]


def test_arm_does_not_require_every_draft_to_be_ready(db):
    """What the user authorizes is the scope "send to these 12 companies", not 12 bodies."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["a@example.com"])])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    assert db.query(SalesAgentSendScheduleItemORM).one().status == "pending_draft"
    assert arm_send_schedule(db, USER, schedule_id)["schedule"]["status"] == "armed"


def test_arm_writes_a_compliance_event(db):
    """Manual per-email sending needs no evidence (each click is itself the record); it's only needed once it becomes "one authorization for N emails"."""
    _seed_account(db)
    arm_send_schedule(db, USER, _planned(db))
    row = (
        db.query(SalesAgentComplianceLogORM)
        .filter(SalesAgentComplianceLogORM.action == "arm_send_schedule")
        .one()
    )
    assert row.decision == "allowed" and row.risk_level == "high"


def test_arm_warns_when_the_daily_cap_is_nearly_used_up(db):
    account = _seed_account(db)
    schedule_id = _planned(db, count=3)
    db.get(SalesAgentSendScheduleORM, schedule_id).daily_cap = 2
    db.add(SalesAgentEmailDeliveryORM(
        user_id=USER, email_account_id=account.id, status="sent",
        sent_at=bj_day_start_utc(0) + timedelta(hours=10), recipient_email="x@example.com",
    ))
    db.commit()
    assert "还剩 1 封额度" in arm_send_schedule(db, USER, schedule_id)["hint"]


# ---------------------------------------------------------------- daily quota


def test_daily_cap_counts_per_sender_not_per_user(db):
    """The platform-level mailbox is shared across users -- an aggregation key including user_id would let 5 users send 200 each,
    while that account is exactly what's being protected."""
    account = _seed_account(db)
    today = bj_day_start_utc(0) + timedelta(hours=10)
    for uid in (1, 2, 3):
        db.add(SalesAgentEmailDeliveryORM(
            user_id=uid, email_account_id=account.id, status="sent", sent_at=today, recipient_email="x@example.com",
        ))
    db.commit()
    assert sender_daily_sent_count(db, account.id) == 3


def test_daily_cap_day_boundary_is_beijing_midnight(db):
    """The UTC day boundary = 08:00 Beijing time, just before the default window -- splitting by UTC gives the inconsistent
    "already sent 200 yesterday, yet can send more at 9 this morning"."""
    account = _seed_account(db)
    db.add_all([
        # Today 00:30 Beijing time (UTC is yesterday 16:30) -- counts as today
        SalesAgentEmailDeliveryORM(user_id=USER, email_account_id=account.id, status="sent",
                                  sent_at=bj_day_start_utc(0) + timedelta(minutes=30), recipient_email="a@x.com"),
        # Yesterday 23:30 Beijing time -- not today
        SalesAgentEmailDeliveryORM(user_id=USER, email_account_id=account.id, status="sent",
                                  sent_at=bj_day_start_utc(0) - timedelta(minutes=30), recipient_email="b@x.com"),
        # Failed sends don't consume quota
        SalesAgentEmailDeliveryORM(user_id=USER, email_account_id=account.id, status="failed",
                                  sent_at=bj_day_start_utc(0) + timedelta(hours=9), recipient_email="c@x.com"),
    ])
    db.commit()
    assert sender_daily_sent_count(db, account.id) == 1


# ---------------------------------------------------------------- cancel / reschedule


def test_cancel_stops_pending_items_only(db):
    _seed_account(db)
    schedule_id = _planned(db, count=3)
    arm_send_schedule(db, USER, schedule_id)
    items = db.query(SalesAgentSendScheduleItemORM).order_by(SalesAgentSendScheduleItemORM.id).all()
    items[0].status = "sent"
    db.get(SalesAgentSendScheduleORM, schedule_id).status = "running"
    db.commit()

    result = cancel_send_schedule(db, USER, schedule_id)
    assert result["cancelled_items"] == 2
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "cancelled"
    # What's already sent can't be recalled; the receipt must say so truthfully, not blur it into "cancelled"
    assert "收不回来" in result["hint"]
    assert db.get(SalesAgentSendScheduleItemORM, items[0].id).status == "sent"


def test_cancel_a_finished_schedule_is_a_noop(db):
    _seed_account(db)
    schedule_id = _planned(db)
    db.get(SalesAgentSendScheduleORM, schedule_id).status = "done"
    db.commit()
    assert cancel_send_schedule(db, USER, schedule_id)["cancelled_items"] == 0


def test_reschedule_keeps_the_drafts(db):
    """"Move it to four" obviously doesn't include "delete the 12 freshly written emails and rewrite them"."""
    _seed_account(db)
    schedule_id = _planned(db)
    arm_send_schedule(db, USER, schedule_id)
    drafts = [i.outreach_log_id for i in db.query(SalesAgentSendScheduleItemORM).all()]
    target = _tomorrow_local_hour(16)
    result = reschedule_send_schedule(db, USER, schedule_id, target)
    assert db.get(SalesAgentSendScheduleORM, schedule_id).scheduled_at == target
    assert [i.outreach_log_id for i in db.query(SalesAgentSendScheduleItemORM).all()] == drafts
    assert "原定" in result["hint"]


def test_reschedule_accepts_any_future_hour(db):
    """Moving it to 3 a.m. is fine too -- the only remaining time rule is "not earlier than now"."""
    _seed_account(db)
    schedule_id = _planned(db)
    target = _tomorrow_local_hour(3)
    reschedule_send_schedule(db, USER, schedule_id, target)
    assert db.get(SalesAgentSendScheduleORM, schedule_id).scheduled_at == target


def test_reschedule_refuses_once_running(db):
    _seed_account(db)
    schedule_id = _planned(db)
    db.get(SalesAgentSendScheduleORM, schedule_id).status = "running"
    db.commit()
    with pytest.raises(SalesAgentError) as excinfo:
        reschedule_send_schedule(db, USER, schedule_id, _tomorrow_local_hour(16))
    assert "重新建一个" in str(excinfo.value)


def test_other_users_cannot_touch_a_schedule(db):
    _seed_account(db)
    schedule_id = _planned(db)
    for call in (arm_send_schedule, cancel_send_schedule):
        with pytest.raises(SalesAgentError):
            call(db, 999, schedule_id)


# ---------------------------------------------------------------- pickup / expiry / reclaim


def test_due_picks_up_five_minutes_early(db):
    """Pick up early, not at the due time -- the leading drafts need time."""
    _seed_account(db)
    now = datetime.utcnow()
    schedule_id = _planned(db)
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    row.status, row.scheduled_at = "armed", now + timedelta(minutes=4)
    db.commit()
    assert due_send_schedules(db, now) == [schedule_id]

    row.scheduled_at = now + timedelta(minutes=30)
    db.commit()
    assert due_send_schedules(db, now) == []


def test_due_ignores_long_overdue_schedules(db):
    """Anything over 4 hours is expire's job and must not be picked up to send."""
    _seed_account(db)
    now = datetime.utcnow()
    schedule_id = _planned(db)
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    row.status, row.scheduled_at = "armed", now - SCHEDULE_EXPIRY - timedelta(minutes=1)
    db.commit()
    assert due_send_schedules(db, now) == []


def test_due_ignores_drafts(db):
    """Not armed means not authorized; not a single email may be sent."""
    _seed_account(db)
    _planned(db)
    assert due_send_schedules(db, datetime.utcnow() + timedelta(days=2)) == []


def test_expire_marks_the_whole_batch_and_says_why(db):
    """Suddenly receiving yesterday afternoon's batch the next morning is worse than nothing being sent."""
    _seed_account(db)
    now = datetime.utcnow()
    schedule_id = _planned(db, count=2)
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    row.status, row.scheduled_at = "armed", now - SCHEDULE_EXPIRY - timedelta(minutes=1)
    db.commit()

    assert expire_overdue_schedules(db, now) == 1
    db.expire_all()
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "expired"
    for item in db.query(SalesAgentSendScheduleItemORM).all():
        assert item.status == "skipped"
        assert "隔夜补发" in item.skip_reason


def test_reclaim_returns_stale_claims_to_pending(db):
    """Recovery path after the process is killed."""
    _seed_account(db)
    schedule_id = _planned(db)
    now = datetime.utcnow()
    items = db.query(SalesAgentSendScheduleItemORM).order_by(SalesAgentSendScheduleItemORM.id).all()
    items[0].status, items[0].claimed_at = "sending", now - timedelta(minutes=6)
    items[1].status, items[1].claimed_at = "sending", now - timedelta(seconds=30)
    db.commit()

    assert reclaim_stale_items(db, now) == 1
    db.expire_all()
    assert db.get(SalesAgentSendScheduleItemORM, items[0].id).status == "pending"
    assert db.get(SalesAgentSendScheduleItemORM, items[1].id).status == "sending"


# ---------------------------------------------------------------- state transitions and waiting


def test_mark_running_only_flips_from_armed(db, factory):
    _seed_account(db)
    schedule_id = _planned(db)
    assert mark_schedule_running(factory, schedule_id) is False   # still draft
    arm_send_schedule(db, USER, schedule_id)
    assert mark_schedule_running(factory, schedule_id) is True
    assert mark_schedule_running(factory, schedule_id) is False   # already running


def test_wait_returns_immediately_when_the_time_has_come(db, factory):
    _seed_account(db)
    schedule_id = _planned(db)
    arm_send_schedule(db, USER, schedule_id)
    later = datetime.utcnow() + timedelta(days=2)
    assert wait_until_scheduled(factory, schedule_id, sleep=lambda _: None, now_fn=lambda: later) is True


def test_wait_bails_out_when_cancelled_mid_wait(db, factory):
    """Skip this re-check point, and a user cancelling a 15:00 schedule at 14:56 still has the thread sleep until due and start sending."""
    _seed_account(db)
    schedule_id = _planned(db)
    arm_send_schedule(db, USER, schedule_id)
    slept: list[float] = []

    def _sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            cancel_send_schedule(db, USER, schedule_id)

    assert wait_until_scheduled(factory, schedule_id, sleep=_sleep) is False
    assert len(slept) == 2   # stops on seeing the cancel, doesn't sleep all the way to the due time


# ---------------------------------------------------------------- pre-generation


def _generator(mapping: dict[int, int], *, fail: set[int] | None = None):
    """Fake generator: lead_id -> draft_id. Leads in `fail` raise."""
    calls: list[int] = []

    async def _generate(user_id: int, lead_id: int) -> int:
        calls.append(lead_id)
        if fail and lead_id in fail:
            raise SalesAgentError("这家企业资料太少,写不出跟进邮件。", public=True)
        return mapping[lead_id]

    _generate.calls = calls  # type: ignore[attr-defined]
    return _generate


def _make_drafts(db, lead_ids: list[int]) -> dict[int, int]:
    out = {}
    for lead_id in lead_ids:
        draft = SalesAgentOutreachLogORM(lead_id=lead_id, channel="email", content="正文", send_status="draft")
        db.add(draft)
        db.flush()
        out[lead_id] = draft.id
    db.commit()
    return out


def test_pregenerate_only_fills_the_head(db, factory):
    _seed_account(db)
    lead_ids = _seed(db, [(f"第{i}家", [f"a{i}@example.com"]) for i in range(1, 9)])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids))

    result = pregenerate_schedule_drafts(factory, schedule_id, PREGEN_HEAD_COUNT, generate_draft=gen)
    assert result == {"generated": 5, "failed": 0}
    db.expire_all()
    counts = {}
    for item in db.query(SalesAgentSendScheduleItemORM).all():
        counts[item.status] = counts.get(item.status, 0) + 1
    assert counts == {"pending": 5, "pending_draft": 3}


def test_pregenerate_is_reentrant(db, factory):
    """It's called many times (once before due, once per email during sending), re-querying each time without caching the list."""
    _seed_account(db)
    lead_ids = _seed(db, [(f"第{i}家", [f"a{i}@example.com"]) for i in range(1, 4)])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids))

    assert pregenerate_schedule_drafts(factory, schedule_id, 2, generate_draft=gen)["generated"] == 2
    assert pregenerate_schedule_drafts(factory, schedule_id, 2, generate_draft=gen)["generated"] == 1
    assert pregenerate_schedule_drafts(factory, schedule_id, 2, generate_draft=gen)["generated"] == 0
    # The same company is never written twice
    assert sorted(gen.calls) == lead_ids


def test_pregenerate_failure_is_skipped_with_a_specific_reason(db, factory):
    """Leads that fail generation are marked skipped with the reason stated, **never silently dropped**."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["a@example.com"]), ("乙", ["b@example.com"])])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids), fail={2})

    assert pregenerate_schedule_drafts(factory, schedule_id, 5, generate_draft=gen) == {"generated": 1, "failed": 1}
    db.expire_all()
    failed = db.query(SalesAgentSendScheduleItemORM).filter(SalesAgentSendScheduleItemORM.lead_id == 2).one()
    assert failed.status == "skipped"
    assert "资料太少" in failed.skip_reason   # specific to this company, not "generation failed"


def test_pregenerate_ignores_a_cancelled_schedule(db, factory):
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["a@example.com"])])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    cancel_send_schedule(db, USER, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids))
    assert pregenerate_schedule_drafts(factory, schedule_id, 5, generate_draft=gen) == {"generated": 0, "failed": 0}
    assert gen.calls == []


# ---------------------------------------------------------------- sending


@pytest.fixture()
def smtp(monkeypatch):
    """Replace real outbound sending and record each email. Addresses in `fail_for` fail with the given smtplib exception.

    Before raising, it goes through the real `_smtp_failure` -- the recipient/sender-side decision lives there, and if the test of
    "only recipient-side failures fall back" bypassed it and injected its own `failure_side`, it would only verify the value
    I injected myself.
    """

    class _Recorder:
        def __init__(self):
            self.sent: list[str] = []
            self.fail_for: dict[str, Exception] = {}

        def __call__(self, row, recipient_email, subject, text, html_body):
            exc = self.fail_for.get(recipient_email)
            if exc is not None:
                raise email_send_mod._smtp_failure(exc) from exc
            self.sent.append(recipient_email)
            return "message-id"

    recorder = _Recorder()
    monkeypatch.setattr(email_send_mod, "_send_email_account_message", recorder)
    return recorder


def _armed_running(db, factory, count: int, **kwargs) -> int:
    _seed_account(db)
    schedule_id = _planned(db, count=count, **kwargs)
    arm_send_schedule(db, USER, schedule_id)
    assert mark_schedule_running(factory, schedule_id) is True
    return schedule_id


def test_run_sends_every_company_once(db, factory, smtp):
    schedule_id = _armed_running(db, factory, 3)
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert stats["sent"] == 3
    assert sorted(smtp.sent) == ["a1@example.com", "a2@example.com", "a3@example.com"]
    db.expire_all()
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "done"
    assert all(i.status == "sent" and i.email_delivery_id for i in db.query(SalesAgentSendScheduleItemORM).all())


def test_run_throttles_between_sends(db, factory, smtp):
    """The 30-second throttle protects the mailbox from provider risk controls -- a business limit, not untuned performance."""
    schedule_id = _armed_running(db, factory, 3)
    slept: list[float] = []
    run_one_schedule(factory, schedule_id, sleep=slept.append, now_fn=lambda: datetime(2026, 1, 1))
    # 3 emails = 2 intervals; time is pinned so each sleep is full length
    assert slept == [30.0, 30.0]


def test_run_stops_when_cancelled_midway(db, factory, smtp):
    """Once the executor dives into the sleep loop, the user's cancel only changes a DB field."""
    schedule_id = _armed_running(db, factory, 4)

    def _sleep(_seconds):
        if len(smtp.sent) == 2:
            cancel_send_schedule(db, USER, schedule_id)

    stats = run_one_schedule(factory, schedule_id, sleep=_sleep)
    assert stats["sent"] == 2
    assert len(smtp.sent) == 2      # stops after the current email, never sends extra
    assert stats["cancelled"] == 2
    db.expire_all()
    assert {i.status for i in db.query(SalesAgentSendScheduleItemORM).all()} == {"sent", "cancelled"}


def test_run_falls_back_to_the_next_email_on_a_recipient_side_failure(db, factory, smtp):
    """When a company has multiple mailboxes, a failed first choice may fall back -- externally it's still "1 company = 1 email"."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["first@example.com", "second@example.com"])], with_draft=True)
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    mark_schedule_running(factory, schedule_id)
    smtp.fail_for["first@example.com"] = smtplib.SMTPRecipientsRefused(
        {"first@example.com": (550, b"no such user")}
    )

    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert stats["sent"] == 1
    assert smtp.sent == ["second@example.com"]
    db.expire_all()
    item = db.query(SalesAgentSendScheduleItemORM).one()
    # "First mailbox bounced, second succeeded" must be stated clearly, not leave only a success result
    assert item.attempt_count == 2
    assert [a["email"] for a in __import__("json").loads(item.attempted_json)] == [
        "first@example.com", "second@example.com",
    ]


def test_run_does_not_fall_back_on_a_sender_side_failure(db, factory, smtp):
    """With a wrong auth code, switching recipients is useless and only burns through every mailbox at the company in one config error."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["first@example.com", "second@example.com"])], with_draft=True)
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    mark_schedule_running(factory, schedule_id)
    smtp.fail_for["first@example.com"] = smtplib.SMTPAuthenticationError(535, b"auth failed")

    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert stats["failed"] == 1
    assert smtp.sent == []
    db.expire_all()
    item = db.query(SalesAgentSendScheduleItemORM).one()
    assert item.status == "failed" and item.attempt_count == 1
    assert "second@example.com" not in item.skip_reason


def test_a_failure_does_not_stop_the_batch(db, factory, smtp):
    """Wrong addresses and recipient rejections are normal; stopping on the first failure would make normal batches halt midway often."""
    schedule_id = _armed_running(db, factory, 3)
    smtp.fail_for["a2@example.com"] = smtplib.SMTPAuthenticationError(535, b"auth failed")
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert (stats["sent"], stats["failed"]) == (2, 1)
    db.expire_all()
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "done"


def test_run_skips_a_company_whose_lead_vanished(db, factory, smtp):
    """Company deleted, recommendation withdrawn -- mark skipped with the reason, don't raise and abort the whole batch."""
    schedule_id = _armed_running(db, factory, 2)
    db.delete(db.get(SalesAgentLeadRecommendationORM, 1))
    db.commit()
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert (stats["sent"], stats["skipped"]) == (1, 1)
    db.expire_all()
    skipped = db.query(SalesAgentSendScheduleItemORM).filter(
        SalesAgentSendScheduleItemORM.status == "skipped"
    ).one()
    assert "已经不在了" in skipped.skip_reason


def test_run_rereads_the_recipient_at_send_time(db, factory, smtp):
    """A dozen-plus hours pass between scheduling and sending; the mailbox may have been changed."""
    schedule_id = _armed_running(db, factory, 1)
    contact = db.query(SalesAgentCompanyContactORM).filter(
        SalesAgentCompanyContactORM.company_id == 1
    ).one()
    contact.email = "changed@example.com"
    db.commit()
    run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert smtp.sent == ["changed@example.com"]


def test_run_skips_an_address_suppressed_after_planning(db, factory, smtp):
    """Someone may well unsubscribe between creating the schedule and actually sending."""
    schedule_id = _armed_running(db, factory, 2)
    add_email_suppression(db, USER, "a1@example.com", reason=SUPPRESSION_REASON_UNSUBSCRIBED)
    db.commit()   # listing only flushes; committing is the caller's job
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert smtp.sent == ["a2@example.com"]
    assert stats["skipped"] == 1
    db.expire_all()
    skipped = db.query(SalesAgentSendScheduleItemORM).filter(
        SalesAgentSendScheduleItemORM.status == "skipped"
    ).one()
    assert "收件邮箱" in skipped.skip_reason


def test_run_stops_at_the_daily_cap_and_says_so(db, factory, smtp):
    schedule_id = _armed_running(db, factory, 3)
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    row.daily_cap = 2
    db.commit()
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert stats["sent"] == 2
    db.expire_all()
    for item in db.query(SalesAgentSendScheduleItemORM).all():
        if item.status == "skipped":
            assert "发满 2 封" in item.skip_reason


def test_run_generates_a_missing_draft_in_place(db, factory, smtp):
    """If the draft isn't ready when its turn comes, **fill it in place and send, no skipping, no reordering**."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["a@example.com"])])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    mark_schedule_running(factory, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids))

    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None, generate_draft=gen)
    assert stats["sent"] == 1
    assert gen.calls == [1]
    assert smtp.sent == ["a@example.com"]


def test_run_skips_a_company_whose_draft_never_arrives(db, factory, smtp):
    """If it can't be produced, mark skipped with the reason and move to the next company, without stalling the batch."""
    _seed_account(db)
    lead_ids = _seed(db, [("甲", ["a@example.com"]), ("乙", ["b@example.com"])])
    schedule_id = plan_send_schedule(db, USER, lead_ids, _tomorrow_local_hour())["schedule"]["id"]
    arm_send_schedule(db, USER, schedule_id)
    mark_schedule_running(factory, schedule_id)
    gen = _generator(_make_drafts(db, lead_ids), fail={1})

    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None, generate_draft=gen)
    assert (stats["sent"], stats["skipped"]) == (1, 1)
    assert smtp.sent == ["b@example.com"]
    db.expire_all()
    skipped = db.query(SalesAgentSendScheduleItemORM).filter(
        SalesAgentSendScheduleItemORM.lead_id == 1
    ).one()
    assert skipped.status == "skipped" and "资料太少" in skipped.skip_reason


def test_run_goes_through_confirm_outreach_send(db, factory, smtp):
    """Never bypass it -- the compliance gate, delivery records, receipts and lead.status transitions all hang off it."""
    schedule_id = _armed_running(db, factory, 1)
    run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    db.expire_all()
    delivery = db.query(SalesAgentEmailDeliveryORM).one()
    assert delivery.status == "sent"
    assert db.query(SalesAgentOutreachLogORM).one().send_status == "sent"
    assert db.get(SalesAgentLeadRecommendationORM, 1).status == "Contacted"


def test_run_does_nothing_for_a_schedule_that_is_not_running(db, factory, smtp):
    """If not flipped to running, not a single email may be sent -- cancelling in the armed stage must cost nothing."""
    _seed_account(db)
    schedule_id = _planned(db, count=2)
    arm_send_schedule(db, USER, schedule_id)
    stats = run_one_schedule(factory, schedule_id, sleep=lambda _: None)
    assert stats["sent"] == 0
    assert smtp.sent == []


# ---------------------------------------------------------------- batches ("this batch")
#
# The 「这批」 ("this batch") in 「给这批企业下午三点发邮件」 ("email this batch of companies at 3 p.m.") used to be guessed by the model: the model has no
# campaign_id (cut by the whitelist in `runtime/payload.py`), and the recommendation list is flat, not batched.
# The group below guards **the single interpretation from batch to lead_ids**, and the numbering of the dialog options.


def _seed_campaign(db, campaign_id: int, session_id: int | None, *, minutes_ago: int = 0, status: str = "completed"):
    db.add(SalesAgentRecommendationCampaignORM(
        id=campaign_id, user_id=USER, session_id=session_id, status=status,
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    ))
    db.commit()


def _seed_leads_in_campaign(db, campaign_id: int | None, lead_ids: list[int], *, status: str = "New"):
    for lead_id in lead_ids:
        db.add(SalesAgentCompanyORM(id=lead_id, name=f"企业{lead_id}", source="manual", data_status="active"))
        db.add(SalesAgentLeadRecommendationORM(
            id=lead_id, user_id=USER, company_id=lead_id, campaign_id=campaign_id, status=status,
        ))
    db.commit()


def test_batch_resolves_to_every_lead_in_it(db):
    """Batch id -> lead_ids. This is the only authoritative interpretation of "this batch", given to neither the model nor the frontend."""
    _seed_campaign(db, 7, session_id=None)
    _seed_leads_in_campaign(db, 7, [1, 2, 3])
    assert resolve_batch_lead_ids(db, USER, [7]) == [1, 2, 3]


def test_batch_excludes_undone_leads(db):
    """Withdrawn recommendations must not be picked back up by "send again" -- withdrawing means "don't touch this company again"."""
    _seed_campaign(db, 7, session_id=None)
    _seed_leads_in_campaign(db, 7, [1, 2])
    _seed_leads_in_campaign(db, 7, [3], status="Undone")
    assert resolve_batch_lead_ids(db, USER, [7]) == [1, 2]


def test_batch_merges_and_dedups_across_batches(db):
    """Choosing "send both batches" merges and dedups, ordered by lead id ascending -- send order must be stable and predictable."""
    _seed_campaign(db, 7, session_id=None)
    _seed_campaign(db, 8, session_id=None)
    _seed_leads_in_campaign(db, 7, [1, 3])
    _seed_leads_in_campaign(db, 8, [2, 4])
    assert resolve_batch_lead_ids(db, USER, [8, 7, 8]) == [1, 2, 3, 4]


def test_batch_never_leaks_across_users(db):
    """Passing someone else's batch id yields only empty -- unauthorized list access comes one step before unauthorized sending."""
    _seed_campaign(db, 7, session_id=None)
    _seed_leads_in_campaign(db, 7, [1, 2])
    assert resolve_batch_lead_ids(db, USER + 1, [7]) == []


def test_empty_batch_list_resolves_to_nothing(db):
    """Nothing selected means nothing selected. Return empty here and let `plan_send_schedule` say "no companies selected"."""
    assert resolve_batch_lead_ids(db, USER, []) == []


def test_session_batches_are_numbered_oldest_first(db):
    """When the user says "the first batch" they mean the **earliest** batch, not the latest.

    The default selection is still the latest batch -- in Chinese 「这批」 ("this batch") defaults to the one just recommended.
    """
    _seed_campaign(db, 7, session_id=9, minutes_ago=30)
    _seed_campaign(db, 8, session_id=9, minutes_ago=5)
    _seed_leads_in_campaign(db, 7, [1, 2])
    _seed_leads_in_campaign(db, 8, [3, 4, 5])

    data = session_recommendation_batches(db, USER, 9)
    assert [(b["index"], b["campaign_id"], b["lead_count"]) for b in data["batches"]] == [
        (1, 7, 2), (2, 8, 3),
    ]
    assert data["default_campaign_id"] == 8


def test_session_batches_ignore_other_sessions(db):
    """"This batch" is an in-session reference. Listing ones recommended in other sessions last week only leads to misclicks."""
    _seed_campaign(db, 7, session_id=9)
    _seed_campaign(db, 8, session_id=10)
    _seed_leads_in_campaign(db, 7, [1])
    _seed_leads_in_campaign(db, 8, [2])
    assert [b["campaign_id"] for b in session_recommendation_batches(db, USER, 9)["batches"]] == [7]


def test_session_batches_drop_empty_campaigns(db):
    """Batches with no recommended companies aren't options -- selecting them couldn't send anything."""
    _seed_campaign(db, 7, session_id=9, minutes_ago=30, status="collecting_requirements")
    _seed_campaign(db, 8, session_id=9, minutes_ago=5)
    _seed_leads_in_campaign(db, 8, [1])
    assert [b["campaign_id"] for b in session_recommendation_batches(db, USER, 9)["batches"]] == [8]


def test_legacy_leads_only_surface_when_the_session_has_no_batches(db):
    """Legacy data with `campaign_id IS NULL` gets its own bucket, offered **only when this session has no batch at all**.

    Mixing account-wide history into "which batch to send" when this session has batches means a misclick resends to a pile of companies
    already fully followed up.
    """
    _seed_leads_in_campaign(db, None, [1, 2])
    only_legacy = session_recommendation_batches(db, USER, 9)
    assert [b["campaign_id"] for b in only_legacy["batches"]] == [LEGACY_BATCH_ID]
    assert only_legacy["batches"][0]["lead_count"] == 2
    # This bucket must also resolve to lead_ids, or it's selectable in the dialog but can't send.
    assert resolve_batch_lead_ids(db, USER, [LEGACY_BATCH_ID]) == [1, 2]

    _seed_campaign(db, 7, session_id=9)
    _seed_leads_in_campaign(db, 7, [3])
    assert [b["campaign_id"] for b in session_recommendation_batches(db, USER, 9)["batches"]] == [7]
