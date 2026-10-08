"""Service layer for scheduled sending (design §4.1).

This layer provides only **pure functions**: create plans, arm/cancel, pre-generate drafts, run a plan. Threads,
advisory locks, heartbeats and the 60-second tick all live in the separate process `agents/sales_agent/scheduler.py` (§4.2);
none are touched here, and with that separation this layer can be fully tested on SQLite.

Three rules that must not be broken:

1. **Never bypass `confirm_outreach_send`** (§1.1). The compliance gate, suppression list, delivery records, receipts and
   `lead.status` transitions all hang off it; building a parallel path for automatic sending would diverge sooner or later;
2. **Send serially, generate concurrently** (§4.2.1). The 30-second throttle protects the sender mailbox; it is a business limit, not a technical
   one; generation sends nothing and does not touch sender quotas, so concurrency is spent there;
3. **Re-read the plan status before each email** (§4.1 "status flip point"). Once the executor dives into its sleep loop,
   a cancel the user clicks on the page only changes a DB field; without actively re-checking it is as if they never clicked.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyORM,
    SalesAgentEmailAccountORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentOutreachLogORM,
    SalesAgentSendScheduleItemORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.service.common import (
    SalesAgentError,
    _dumps,
    _iso,
    _loads,
    public_error_text,
    record_compliance_event,
    session_has_table,
)
from agents.sales_agent.service.email_config import _effective_email_config
from agents.sales_agent.service.email_send import (
    MAX_RECIPIENT_FALLBACK,
    _bj,
    _delivery_recipient_candidates,
    delivery_failure_side,
)
from agents.sales_agent.service.outreach import (
    _usable_email_contact_for_lead,
    confirm_outreach_send,
)
from agents.sales_agent.service.suppression import suppressed_emails, suppression_skip_reason
from agents.sales_agent.time_parse import bj_day_start_utc, describe_utc, to_local

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- constants
#
# **These are not independent knobs** (§4.2.1). There is a quantitative relationship between them that must hold:
#
#     generation rate PREGEN_CONCURRENCY / 30s  >  send rate 1 / throttle_seconds
#
# There is currently 3x headroom, which is why "prepare only the first 5 before the start time and fill the rest while sending" works: sending can never catch up with
# generation. Anyone who sets throttle to 10 s/email or PREGEN_CONCURRENCY to 1 must come back and recompute
# PREGEN_HEAD_COUNT, otherwise the symptom is "sending gradually slows down" rather than a clear error.

PREGEN_HEAD_COUNT = 5           # How many to prepare before the start time. A pacing constraint, not a resource constraint
PREGEN_CONCURRENCY = 3          # Generation concurrency. It sends nothing and does not touch sender quotas, so it can run concurrently
PREGEN_LEAD_TIME = timedelta(minutes=5)     # How early to pick up a plan to prepare the head
SCHEDULE_EXPIRY = timedelta(hours=4)        # Anything not finished past this expires as a whole batch; no overnight catch-up sends
CLAIM_STALE = timedelta(minutes=5)          # claimed_at timeout reclaim (recovery path when the process is killed)
STATUS_RECHECK_SECONDS = 10                 # Cancellation re-check interval while waiting for the start time

SCHEDULE_STATUSES = ("draft", "armed", "running", "done", "cancelled", "expired")
ITEM_STATUSES = ("pending_draft", "pending", "sending", "sent", "failed", "skipped", "cancelled")
# Not yet sent. Cancellation, reclaim and expiry all scope their effect by this set.
ITEM_OPEN_STATUSES = ("pending_draft", "pending", "sending")


def _schedule_tables_ready(db: Session) -> bool:
    return session_has_table(db, SalesAgentSendScheduleORM.__tablename__) and session_has_table(
        db, SalesAgentSendScheduleItemORM.__tablename__
    )


# Send time has **only one rule left: not earlier than now** (see the `<= now` checks in `confirm_scheduled_send` /
# `reschedule_send_schedule`). There used to be a hard-coded 09:00-18:00
# send window; it has been removed entirely: whatever time the user says is the time, the system does not impose business hours.
# The `send_schedule.send_window` column remains in the DB (it has a default and does not affect writes), but no
# code reads it anymore, and it is no longer exposed in the API or UI.


# ---------------------------------------------------------------- daily quota


def sender_daily_sent_count(db: Session, email_account_id: int | None, now_utc: datetime | None = None) -> int:
    """How many emails this **sender mailbox** has successfully sent today (Beijing calendar day).

    The aggregation key is `email_account_id`, **without user_id** (§4.2): platform-level sender mailboxes are shared across users
    (`_effective_email_config` falls back to global when the user has no account); including user_id would let 5
    users each send 200, so that account actually sends 1000 in a day, and it is exactly what we must protect.

    No schedule_id either: if tied to the plan, a user creating 3 plans could send 600 a day.
    """
    if not email_account_id:
        return 0
    return (
        db.query(SalesAgentEmailDeliveryORM)
        .filter(
            SalesAgentEmailDeliveryORM.email_account_id == int(email_account_id),
            SalesAgentEmailDeliveryORM.status == "sent",
            SalesAgentEmailDeliveryORM.sent_at >= bj_day_start_utc(0, now_utc=now_utc),
        )
        .count()
    )


def sender_daily_remaining(db: Session, schedule: SalesAgentSendScheduleORM, now_utc: datetime | None = None) -> int:
    cap = max(0, int(schedule.daily_cap or 0))
    if not cap:
        return 0
    return max(0, cap - sender_daily_sent_count(db, schedule.email_account_id, now_utc=now_utc))


# ---------------------------------------------------------------- plan creation


def _item_counts(db: Session, schedule_id: int) -> dict[str, int]:
    rows = (
        db.query(SalesAgentSendScheduleItemORM.status)
        .filter(SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id))
        .all()
    )
    out: dict[str, int] = {}
    for (status,) in rows:
        out[status] = out.get(status, 0) + 1
    return out


def evaluate_send_candidates(
    db: Session,
    user_id: int,
    lead_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Which leads in this batch can be sent and which cannot (with a reason for each). **Read-only, not persisted.**

    Extracted from `plan_send_schedule` because the dialog must report "will send 8 · 2 cannot be sent"
    **before** creating the plan. That number and the number at actual scheduling must come from the same code:
    computing them separately will drift sooner or later, and the symptom is the user seeing 8 while 10 were actually sent.

    `included` carries two ORM objects, `lead`/`draft`, for `plan_send_schedule` to build items;
    the dry-run only looks at `company`/`email`/`lead_id` and never touches the two ORM fields.
    """
    ids = [int(x) for x in (lead_ids or []) if int(x or 0) > 0]
    leads = (
        db.query(SalesAgentLeadRecommendationORM)
        .filter(
            SalesAgentLeadRecommendationORM.id.in_(ids),
            SalesAgentLeadRecommendationORM.user_id == int(user_id),
        )
        .all()
    ) if ids else []
    by_id = {row.id: row for row in leads}

    # Query the suppression list for the whole batch at once, not company by company in the loop: 100 companies would be 100 round trips.
    # The separate check right before sending cannot be omitted; hours may pass between plan creation and actual sending.
    candidate_emails: dict[int, str] = {}
    for lead_id in ids:
        lead = by_id.get(lead_id)
        if lead is not None:
            contact = _usable_email_contact_for_lead(db, lead)
            if contact is not None:
                candidate_emails[lead_id] = contact.email
    blocked = suppressed_emails(db, user_id, candidate_emails.values())

    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for lead_id in ids:
        lead = by_id.get(lead_id)
        if lead is None:
            skipped.append({"lead_id": lead_id, "company": "", "reason": "这条推荐已经不在你的列表里了(可能被删除或撤回)"})
            continue
        company = db.get(SalesAgentCompanyORM, lead.company_id) if lead.company_id else None
        name = company.name if company is not None else ""
        email = candidate_emails.get(lead_id, "")
        if not email:
            skipped.append({"lead_id": lead_id, "company": name, "reason": "这家企业还没有可用的公开收件邮箱"})
            continue
        hit = blocked.get(email.strip().lower())
        if hit:
            skipped.append({"lead_id": lead_id, "company": name, "reason": suppression_skip_reason(hit)})
            continue
        draft = _latest_email_draft(db, lead.id)
        included.append({
            "lead_id": lead.id,
            "company": name,
            "email": email,
            "has_draft": draft is not None,
            "_lead": lead,
            "_draft": draft,
        })
    return included, skipped


def plan_send_schedule(
    db: Session,
    user_id: int,
    lead_ids: list[int],
    scheduled_at: datetime,
    *,
    throttle_seconds: int = 30,
    daily_cap: int = 200,
    session_id: int | None = None,
    campaign_id: int | None = None,
    user_text: str = "",
) -> dict[str, Any]:
    """Only builds the list, **does not generate drafts in this request**; status stays at `draft`.

    Cannot generate synchronously here: the wall-clock limit for one email is 45 seconds (`_INTRO_BLOCKING_MODEL_TIMEOUT`),
    and that 45 is itself squeezed by nginx's 60-second read timeout: **one company eats the whole request budget**,
    so 100 in series is impossible. Drafts are filled in by the scheduler process after arm (§4.1 1b).

    Returns the full list (including skipped items and per-item reasons) for user preview. Skip reasons must be specific to each company:
    "some companies cannot be sent" does not count.
    """
    if not _schedule_tables_ready(db):
        raise SalesAgentError("定时发送的数据表还没建好,请先跑数据库迁移。", status_code=503)
    ids = [int(x) for x in (lead_ids or []) if int(x or 0) > 0]
    if not ids:
        raise SalesAgentError("没有选中任何企业,无法建立发送计划。", status_code=400, public=True)
    if not isinstance(scheduled_at, datetime):
        raise SalesAgentError("发送时间没有解析出来,请说一个具体的时刻,比如「明天上午十点」。", status_code=400, public=True)

    included, skipped = evaluate_send_candidates(db, user_id, ids)

    schedule = SalesAgentSendScheduleORM(
        user_id=int(user_id),
        campaign_id=campaign_id,
        channel="email",
        scheduled_at=scheduled_at.replace(second=0, microsecond=0),
        status="draft",
        throttle_seconds=max(1, int(throttle_seconds or 30)),
        daily_cap=max(1, int(daily_cap or 200)),
        created_by_session_id=session_id,
        metadata_json=_dumps({"user_text": str(user_text or "")[:500], "requested_lead_ids": ids}),
    )
    db.add(schedule)
    db.flush()

    for row in included:
        lead = row.pop("_lead")
        draft = row.pop("_draft")
        db.add(SalesAgentSendScheduleItemORM(
            schedule_id=schedule.id,
            lead_id=lead.id,
            company_id=lead.company_id,
            outreach_log_id=draft.id if draft is not None else None,
            # Attach existing drafts directly; mark the rest pending_draft, which is invisible to users:
            # in the drawer it shows as "to be sent" just like pending.
            status="pending" if draft is not None else "pending_draft",
        ))
    # When not a single company qualifies, do not leave an empty plan in the DB: it cannot be armed and nobody will delete it.
    if not included:
        db.query(SalesAgentSendScheduleItemORM).filter(
            SalesAgentSendScheduleItemORM.schedule_id == schedule.id
        ).delete(synchronize_session=False)
        db.delete(schedule)
        db.commit()
        reasons = "；".join(dict.fromkeys(s["reason"] for s in skipped)) or "选中的企业都不能发送"
        # ^ In this branch the whole batch cannot be sent and the plan itself is deleted, so the record below has nowhere to live.
        # The interpolated messages in this module are **deliberately not marked public**. Not because users must not see them; quite the opposite:
        # every word is our own, and we have run them through `_public_error_message`; they go out verbatim unmarked
        # (the word list blocks model/vendor/secret-type wording, and none of these touch it). Marking them would require punching a hole in
        # the `test_nothing_marked_public_interpolates_anything` gate, and that gate
        # guards the shape rule "interpolation = external content coming in"; loosening it for a few messages that pass anyway
        # is not worth it.
        raise SalesAgentError(f"这批企业一家都发不出去:{reasons}。", status_code=400)
    # Skipped companies **never enter the item table** (they were never scheduled), so this list exists only in this one return value.
    # Without keeping a copy, when the user closes the page and comes back via the plan list, "per-item reasons in the skipped area" (third point of §4.5)
    # would be reduced to a number, and that is the only place they can discover "I thought it would be sent, but it wasn't".
    meta = _loads(schedule.metadata_json, {})
    meta["skipped"] = skipped[:200]
    schedule.metadata_json = _dumps(meta)
    db.commit()
    db.refresh(schedule)
    return {
        "schedule": serialize_send_schedule(db, schedule),
        "included": included,
        "skipped": skipped,
        "hint": _plan_hint(schedule, len(included)),
    }


def _latest_email_draft(db: Session, lead_id: int) -> SalesAgentOutreachLogORM | None:
    """The latest not-yet-sent email draft for this lead.

    `sent` ones must not be attached: at send time they would hit `_guard_resend`'s cooldown and the whole run would be wasted.
    """
    return (
        db.query(SalesAgentOutreachLogORM)
        .filter(
            SalesAgentOutreachLogORM.lead_id == int(lead_id),
            SalesAgentOutreachLogORM.channel == "email",
            SalesAgentOutreachLogORM.send_status.in_(("draft", "pending_approval", "rejected")),
        )
        .order_by(SalesAgentOutreachLogORM.id.desc())
        .first()
    )


def batch_finish_text(schedule: SalesAgentSendScheduleORM, count: int) -> str:
    """Roughly when the whole batch finishes sending. **This sentence must always be echoed** (§4.1 arm step 3).

    "Send at 15:00" easily means "done by 15:00" in the user's mind, but the 30-second throttle means it can only be
    "start sending at 15:00": 12 companies take 6 minutes, 100 take 50 minutes. This is required by the throttle and unavoidable.
    """
    if count <= 1:
        return ""
    span = timedelta(seconds=max(1, int(schedule.throttle_seconds or 30)) * (count - 1))
    minutes = max(1, round(span.total_seconds() / 60))
    return f"{count} 家按每封间隔 {schedule.throttle_seconds} 秒发,约 {minutes} 分钟发完,大概 {_bj(schedule.scheduled_at + span)} 结束"


def _plan_hint(schedule: SalesAgentSendScheduleORM, count: int) -> str:
    parts = [f"已排好 {count} 家,{describe_utc(schedule.scheduled_at)}开始发"]
    tail = batch_finish_text(schedule, count)
    if tail:
        parts.append(tail)
    parts.append("确认之后才会真的发出去")
    return "；".join(parts)


# ---------------------------------------------------------------- arm / cancel


def arm_send_schedule(
    db: Session,
    user_id: int,
    schedule_id: int,
    *,
    actor: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """`draft -> armed`. **This is the compliance authorization point** and must be triggered by explicit user confirmation.

    Manual per-email sending needs no record, because each click is itself a record; only after switching to "authorize N emails at once"
    is one needed. arm does three things (§4.1 arm steps 1, 2, 3):

    1. **Resolve the sender mailbox, validate it is usable, and pin `email_account_id` into the plan.** Without pinning: platform-level
       mailboxes are shared across users, and under the `round_robin` strategy each email switches accounts; the sender mailbox lock has nothing to lock
       and the daily quota has no aggregation key. This is also the precondition for "no circuit breaking at runtime" (§4.1.1): the main scenarios
       of broken config are blocked at this moment, so runtime can stay simple;
    1b. **Validate that `scheduled_at` falls within the window.** Otherwise it **silently expires**: the executor skips it every round as
       "outside the window" without changing status, until `scheduled_at + 4h` expires the whole batch, with not a single email sent in four hours
       and no notice at all. We must not accept a plan doomed to expire;
    2. Write a compliance record (with a list snapshot);
    3. Echo when the whole batch will finish, adding a note when the head cannot be ready in time.

    **Does not require all drafts to be ready**: the user authorizes the scope "email these 12 companies", not 12 bodies.
    """
    now = now_utc or datetime.utcnow()
    schedule = _owned_schedule(db, user_id, schedule_id)
    if schedule.status == "armed":
        return {"schedule": serialize_send_schedule(db, schedule), "hint": "这个计划已经确认过了,到点会自动发。"}
    if schedule.status != "draft":
        raise SalesAgentError(f"这个计划当前是「{schedule_status_text(schedule.status)}」,不能再确认。", status_code=400)

    counts = _item_counts(db, schedule.id)
    pending = sum(counts.get(s, 0) for s in ("pending_draft", "pending"))
    if not pending:
        raise SalesAgentError("这个计划里已经没有待发送的企业了。", status_code=400, public=True)

    if schedule.scheduled_at <= now:
        raise SalesAgentError(
            f"这个计划的发送时间({describe_utc(schedule.scheduled_at, now_utc=now)})已经过去了,请重新定一个时间。",
            status_code=400,
        )

    cfg = _effective_email_config(db, user_id)
    account: SalesAgentEmailAccountORM | None = cfg.get("account")
    if not cfg.get("sending_enabled") or account is None:
        reasons = "；".join(cfg.get("disabled_reasons") or ["未配置可用发件箱"])
        raise SalesAgentError(f"现在还发不了:{reasons}。配好之后再确认这个计划。", status_code=400)
    schedule.email_account_id = account.id
    schedule.status = "armed"
    schedule.updated_at = now

    remaining = sender_daily_remaining(db, schedule, now_utc=now)
    meta = _loads(schedule.metadata_json, {})
    meta["armed_at"] = _iso(now)
    meta["armed_by"] = actor or f"user:{user_id}"
    meta["armed_account_id"] = account.id
    schedule.metadata_json = _dumps(meta)
    db.commit()
    db.refresh(schedule)

    record_compliance_event(
        db, user_id, "arm_send_schedule", "high", "allowed",
        f"用户一次性授权 {pending} 家企业在 {_bj(schedule.scheduled_at)} 自动发送",
        {
            "schedule_id": schedule.id,
            "lead_count": pending,
            "scheduled_at": _iso(schedule.scheduled_at),
            "email_account_id": account.id,
            "user_text": str(meta.get("user_text") or "")[:500],
            "recipients": _recipient_snapshot(db, schedule.id),
        },
    )
    return {"schedule": serialize_send_schedule(db, schedule), "hint": _arm_hint(db, schedule, pending, remaining, now)}


def _arm_hint(db: Session, schedule: SalesAgentSendScheduleORM, count: int, remaining: int, now: datetime) -> str:
    parts = [f"好,{describe_utc(schedule.scheduled_at, now_utc=now)}开始给这 {count} 家发"]
    tail = batch_finish_text(schedule, count)
    if tail:
        parts.append(tail)
    # First PREGEN_HEAD_COUNT companies / concurrency 3 x 30 seconds per email ~= 1 minute. Only arming at the very last moment
    # leaves it short: not an error (the executor waits for the head to be filled before sending, nothing is missed), but it must be stated on the spot.
    head_need = timedelta(seconds=int(PREGEN_HEAD_COUNT / PREGEN_CONCURRENCY * 30) + 30)
    if _item_counts(db, schedule.id).get("pending_draft") and schedule.scheduled_at - now < head_need:
        parts.append("离发送只剩一两分钟,头几封还在写,开始时间可能晚个一两分钟")
    if remaining < count:
        parts.append(f"注意:这个发件箱今天还剩 {remaining} 封额度,超出的会留到明天再说")
    return "；".join(parts)


def _recipient_snapshot(db: Session, schedule_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """List snapshot for the compliance record. The authorized scope is "these companies", so we keep the list rather than bodies."""
    rows = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(
            SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
            SalesAgentSendScheduleItemORM.status.in_(ITEM_OPEN_STATUSES),
        )
        .order_by(SalesAgentSendScheduleItemORM.id.asc())
        .limit(limit)
        .all()
    )
    out = []
    for row in rows:
        company = db.get(SalesAgentCompanyORM, row.company_id) if row.company_id else None
        out.append({"lead_id": row.lead_id, "company": company.name if company is not None else ""})
    return out


def cancel_send_schedule(
    db: Session,
    user_id: int,
    schedule_id: int,
    *,
    reason: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Cancel. **Only affects items not yet sent; sent ones are not rolled back** (§4.5).

    Cancelling during sending does not take effect here: this only changes DB fields, and actually stopping relies on the executor re-reading
    the status before each email. Without both, the user's cancel click is as if never clicked.
    """
    now = now_utc or datetime.utcnow()
    schedule = _owned_schedule(db, user_id, schedule_id)
    if schedule.status in ("done", "cancelled", "expired"):
        return {
            "schedule": serialize_send_schedule(db, schedule),
            "cancelled_items": 0,
            "hint": f"这个计划已经是「{schedule_status_text(schedule.status)}」,没有可取消的了。",
        }
    was_running = schedule.status == "running"
    cancelled = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(
            SalesAgentSendScheduleItemORM.schedule_id == schedule.id,
            SalesAgentSendScheduleItemORM.status.in_(("pending_draft", "pending")),
        )
        .update(
            {"status": "cancelled", "finished_at": now, "updated_at": now},
            synchronize_session=False,
        )
    )
    schedule.status = "cancelled"
    schedule.updated_at = now
    meta = _loads(schedule.metadata_json, {})
    meta["cancelled_at"] = _iso(now)
    meta["cancel_reason"] = str(reason or "")[:200]
    schedule.metadata_json = _dumps(meta)
    db.commit()
    db.refresh(schedule)
    counts = _item_counts(db, schedule.id)
    sent = counts.get("sent", 0)
    if was_running and sent:
        # Emails already sent cannot be recalled; the receipt must say so truthfully, not blur it into "cancelled".
        hint = f"已停下,剩余 {cancelled} 家不再发。注意已经发出去的 {sent} 封收不回来了。"
    elif was_running:
        hint = f"已停下,剩余 {cancelled} 家不再发。当前这封发完就停。"
    else:
        hint = f"已取消,{cancelled} 家都不会发了。"
    record_compliance_event(
        db, user_id, "cancel_send_schedule", "medium", "blocked",
        reason or "用户取消定时发送计划",
        {"schedule_id": schedule.id, "cancelled_items": cancelled, "already_sent": sent},
    )
    return {"schedule": serialize_send_schedule(db, schedule), "cancelled_items": cancelled, "hint": hint}


def reschedule_send_schedule(
    db: Session,
    user_id: int,
    schedule_id: int,
    scheduled_at: datetime,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Change the time. Not allowed once started or finished: that requires creating a new plan.

    There is a dedicated reschedule entry because "cancel and recreate" would discard all already generated drafts,
    and a user saying "move it to four o'clock" obviously does not mean "delete the 12 emails just written and rewrite them".
    """
    now = now_utc or datetime.utcnow()
    schedule = _owned_schedule(db, user_id, schedule_id)
    if schedule.status not in ("draft", "armed"):
        raise SalesAgentError(
            f"这个计划当前是「{schedule_status_text(schedule.status)}」,改不了时间了,需要的话重新建一个。",
            status_code=400,
        )
    if not isinstance(scheduled_at, datetime):
        raise SalesAgentError("新的发送时间没有解析出来,请说一个具体的时刻。", status_code=400, public=True)
    target = scheduled_at.replace(second=0, microsecond=0)
    if target <= now:
        raise SalesAgentError(
            f"{describe_utc(target, now_utc=now)}已经过去了,请重新定一个时间。", status_code=400
        )
    old = schedule.scheduled_at
    schedule.scheduled_at = target
    schedule.updated_at = now
    meta = _loads(schedule.metadata_json, {})
    meta.setdefault("reschedules", []).append({"from": _iso(old), "to": _iso(target), "at": _iso(now)})
    schedule.metadata_json = _dumps(meta)
    db.commit()
    db.refresh(schedule)
    count = sum(_item_counts(db, schedule.id).get(s, 0) for s in ("pending_draft", "pending"))
    return {
        "schedule": serialize_send_schedule(db, schedule),
        "hint": f"改好了,{describe_utc(target, now_utc=now)}开始给这 {count} 家发(原定 {_bj(old)})。",
    }


def drop_send_schedule_items(
    db: Session,
    user_id: int,
    schedule_id: int,
    item_ids: list[int],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Remove some companies from the list (§4.5 "removable").

    Why this exists instead of having users "cancel the whole batch and recreate": **what the user confirms is the list after removal**.
    Without per-item removal, batch authorization degrades to all-or-nothing: if 1 of 12 should not be sent, the user must give up
    all 12, along with the already generated drafts.

    Three boundaries:

    1. **Only effective in `draft` / `armed`.** After `running` the list is being consumed email by email; what needs stopping then
       is the whole batch, which is `cancel_send_schedule`'s job; this aligns with the 4.5 panel rule "after `running` only
       cancel remains", so the frontend must not grey it out while the backend still accepts it;
    2. **Removing down to empty is not allowed.** An empty plan cannot be armed and nobody will delete it (same reason as
       the "no empty plan when no company qualifies" rule in `plan_send_schedule`). To send none at all, just cancel;
    3. Set `cancelled` rather than `skipped`. `skipped` means "we cannot send" (no email, on the suppression
       list); `cancelled` means "the user does not want it sent", shown as "cancelled" on the panel; mixing the two
       makes the per-item reasons in the skipped area unclear about whose decision it was.
    """
    now = now_utc or datetime.utcnow()
    schedule = _owned_schedule(db, user_id, schedule_id)
    ids = [int(x) for x in (item_ids or []) if int(x or 0) > 0]
    if not ids:
        raise SalesAgentError("没有指定要剔除哪几家。", status_code=400, public=True)
    if schedule.status not in ("draft", "armed"):
        raise SalesAgentError(
            f"这个计划当前是「{schedule_status_text(schedule.status)}」,清单改不动了。"
            "要停下来的话用取消。",
            status_code=400,
        )
    open_items = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(
            SalesAgentSendScheduleItemORM.schedule_id == schedule.id,
            SalesAgentSendScheduleItemORM.status.in_(("pending_draft", "pending")),
        )
        .all()
    )
    targets = [row for row in open_items if row.id in set(ids)]
    if not targets:
        raise SalesAgentError("要剔除的这几家不在待发清单里,可能已经被剔除过了。", status_code=404)
    if len(targets) >= len(open_items):
        raise SalesAgentError(
            f"这样会把 {len(open_items)} 家全剔掉,剩下一个空计划。一家都不发的话直接取消这个计划。",
            status_code=400,
        )
    for row in targets:
        row.status = "cancelled"
        row.skip_reason = "用户在清单里剔除"
        row.finished_at = now
        row.updated_at = now
    schedule.updated_at = now
    meta = _loads(schedule.metadata_json, {})
    meta.setdefault("dropped", []).append({"item_ids": [row.id for row in targets], "at": _iso(now)})
    schedule.metadata_json = _dumps(meta)
    db.commit()
    db.refresh(schedule)
    left = len(open_items) - len(targets)
    return {
        "schedule": serialize_send_schedule(db, schedule),
        "dropped": len(targets),
        "remaining": left,
        "hint": f"已剔除 {len(targets)} 家,还剩 {left} 家会发。",
    }


def _owned_schedule(db: Session, user_id: int, schedule_id: int) -> SalesAgentSendScheduleORM:
    if not _schedule_tables_ready(db):
        raise SalesAgentError("定时发送的数据表还没建好,请先跑数据库迁移。", status_code=503)
    row = db.get(SalesAgentSendScheduleORM, int(schedule_id or 0))
    if row is None or row.user_id != int(user_id):
        raise SalesAgentError("这个发送计划不存在,或者不属于当前账号。", status_code=404, public=True)
    return row


# ---------------------------------------------------------------- serialization

_SCHEDULE_STATUS_TEXT = {
    "draft": "待确认",
    "armed": "已确认,等到点",
    "running": "正在发送",
    "done": "已发完",
    "cancelled": "已取消",
    "expired": "已过期",
}
_ITEM_STATUS_TEXT = {
    # pending_draft is invisible to users: whether a draft is ready is our implementation detail,
    # and users just see "to be sent".
    "pending_draft": "待发送",
    "pending": "待发送",
    "sending": "发送中",
    "sent": "已发送",
    "failed": "发送失败",
    "skipped": "已跳过",
    "cancelled": "已取消",
}


def schedule_status_text(status: str) -> str:
    return _SCHEDULE_STATUS_TEXT.get(str(status or ""), str(status or ""))


def item_status_text(status: str) -> str:
    return _ITEM_STATUS_TEXT.get(str(status or ""), str(status or ""))


def serialize_send_schedule(db: Session, row: SalesAgentSendScheduleORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    counts = _item_counts(db, row.id)
    total = sum(counts.values())
    return {
        "id": row.id,
        "user_id": row.user_id,
        "channel": row.channel,
        "status": row.status,
        "status_text": schedule_status_text(row.status),
        "scheduled_at": _iso(row.scheduled_at),
        "scheduled_at_text": _bj(row.scheduled_at),
        "timezone": row.timezone,
        "throttle_seconds": row.throttle_seconds,
        "daily_cap": row.daily_cap,
        "email_account_id": row.email_account_id,
        "campaign_id": row.campaign_id,
        "session_id": row.created_by_session_id,
        "total": total,
        "counts": counts,
        "sent": counts.get("sent", 0),
        "pending": counts.get("pending", 0) + counts.get("pending_draft", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
        "metadata": {k: v for k, v in _loads(row.metadata_json, {}).items() if k != "requested_lead_ids"},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def serialize_send_schedule_item(db: Session, row: SalesAgentSendScheduleItemORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    company = db.get(SalesAgentCompanyORM, row.company_id) if row.company_id else None
    return {
        "id": row.id,
        "schedule_id": row.schedule_id,
        "lead_id": row.lead_id,
        "company_id": row.company_id,
        "company_name": company.name if company is not None else "",
        "contact_id": row.contact_id,
        "outreach_log_id": row.outreach_log_id,
        "status": row.status,
        "status_text": item_status_text(row.status),
        "skip_reason": row.skip_reason,
        "email_delivery_id": row.email_delivery_id,
        # Fall-through trail: "the first mailbox bounced, the second succeeded" must be explainable on the panel;
        # keeping only the successful result cannot explain why it went to this address.
        "attempted": _loads(row.attempted_json, []),
        "attempt_count": row.attempt_count,
        "finished_at": _iso(row.finished_at),
        "updated_at": _iso(row.updated_at),
    }


def list_send_schedules(
    db: Session, user_id: int, *, status: str = "", limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    if not _schedule_tables_ready(db):
        return {"schedules": [], "total": 0, "limit": limit, "offset": offset}
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    query = db.query(SalesAgentSendScheduleORM).filter(SalesAgentSendScheduleORM.user_id == int(user_id))
    if status:
        query = query.filter(SalesAgentSendScheduleORM.status == str(status))
    total = query.count()
    rows = query.order_by(SalesAgentSendScheduleORM.id.desc()).offset(offset).limit(limit).all()
    return {
        "schedules": [serialize_send_schedule(db, row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_send_schedule_detail(db: Session, user_id: int, schedule_id: int, *, item_limit: int = 200) -> dict[str, Any]:
    schedule = _owned_schedule(db, user_id, schedule_id)
    items = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(SalesAgentSendScheduleItemORM.schedule_id == schedule.id)
        .order_by(SalesAgentSendScheduleItemORM.id.asc())
        .limit(max(1, min(int(item_limit or 200), 500)))
        .all()
    )
    return {
        "schedule": serialize_send_schedule(db, schedule),
        "items": [serialize_send_schedule_item(db, row) for row in items],
    }


def agent_send_schedule_status(db: Session, user_id: int, schedule_id: int | None = None) -> dict[str, Any]:
    """Trimmed version for the model to read. Times always go through `_bj`; do not hand it a bare UTC string to guess the timezone.

    Besides the numbers it provides two things, both directly required by the §4.6 three-moments metric definition:

    - **"Expected finish time"**: when the user asks "has the three o'clock batch gone out", answering "sending" says nothing;
      what they really want is how much longer to wait. Not started counts the whole batch; sending counts the remaining companies;
    - **"Who was not sent and why"**: given only "1 skipped", the model can only repeat
      "1 skipped", exactly the dead-end phrasing the established rules forbid. Only with the reason fed to the model
      can it say "Suzhou YY Intelligent unsubscribed after scheduling".
    """
    if not _schedule_tables_ready(db):
        return {"schedules": [], "hint": "定时发送还没开通。"}
    query = db.query(SalesAgentSendScheduleORM).filter(SalesAgentSendScheduleORM.user_id == int(user_id))
    if schedule_id:
        query = query.filter(SalesAgentSendScheduleORM.id == int(schedule_id))
    rows = query.order_by(SalesAgentSendScheduleORM.id.desc()).limit(5).all()
    out = []
    for row in rows:
        counts = _item_counts(db, row.id)
        pending = counts.get("pending", 0) + counts.get("pending_draft", 0)
        record = {
            "id": row.id,
            "状态": schedule_status_text(row.status),
            "发送时间": _bj(row.scheduled_at),
            "共几家": sum(counts.values()),
            "已发": counts.get("sent", 0),
            "待发": pending,
            "失败": counts.get("failed", 0),
            "跳过": counts.get("skipped", 0),
        }
        if row.status in ("armed", "running") and pending > 1:
            throttle = max(1, int(row.throttle_seconds or 30))
            minutes = max(1, round(throttle * (pending - 1) / 60))
            record["预计还要"] = f"约 {minutes} 分钟发完剩下的 {pending} 家"
        unsent = _unsent_with_reasons(db, row.id)
        if unsent:
            record["没发出去的"] = unsent
        out.append(record)
    return {"schedules": out, "hint": "" if out else "现在没有定时发送计划。"}


def _unsent_with_reasons(db: Session, schedule_id: int, limit: int = 8) -> list[dict[str, str]]:
    """Per-company reasons for failures/skips. **This list must not be collapsed into a number**: users never ask
    "how many were skipped", they ask "why wasn't the one I expected sent"."""
    rows = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(
            SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
            SalesAgentSendScheduleItemORM.status.in_(("failed", "skipped")),
        )
        .order_by(SalesAgentSendScheduleItemORM.id.asc())
        .limit(max(1, int(limit or 8)))
        .all()
    )
    return [
        {
            "企业": _receipt_company_name(db, row),
            "原因": str(row.skip_reason or "").strip() or "没有留下原因,在计划详情里能看到这一条",
        }
        for row in rows
    ]


# ---------------------------------------------------------------- pre-generation
#
# Generation needs "one lead -> one email draft", and that logic (report email / no-report intro email /
# email type branches / persistence shaping) lives entirely in `api.py`, which `service/` cannot import (the layering would be reversed).
#
# So we leave an **injection point** here instead of copying those two hundred lines: the caller provides a `generate_draft`,
# defaulting to a lazily imported api adapter. The cost of copying is that the two generation paths will diverge sooner or later,
# and "scheduled emails differ from manually sent ones" is the hardest kind of bug to notice.

DraftGenerator = Callable[[int, int], "asyncio.Future[int] | int"]


async def _default_generate_draft(user_id: int, lead_id: int) -> int:
    """Default generator: delegates to the api layer adapter and returns `outreach_log_id`.

    Lazy import: a module-level import would cement the `service -> api -> service` cycle.
    """
    from agents.sales_agent.api import generate_scheduled_outreach_draft  # noqa: PLC0415

    return await generate_scheduled_outreach_draft(user_id, lead_id)


async def pregenerate_schedule_drafts_async(
    session_factory: Callable[[], Session],
    schedule_id: int,
    limit: int = PREGEN_HEAD_COUNT,
    *,
    generate_draft: DraftGenerator | None = None,
) -> dict[str, int]:
    """Fill the first `limit` `pending_draft` items of this plan into `pending`. **Reentrant and idempotent.**

    It is called many times (once before the start time, once between each email during sending), so it re-queries
    `pending_draft` each time and does not cache the list.

    Concurrency is safe because this part sends nothing, does not touch sender quotas, and has no HTTP request waiting on a timeout;
    that is separate from sending having to be serial. **Concurrency is spent on generation, serial execution is reserved for sending.**

    **Each email holds the connection in three phases**: query context (held) -> release -> call the model (tens of seconds, not held) -> reopen
    a session to persist (held). Holding a connection while waiting on the model is the fastest way to break "never hold a DB connection long".

    Leads whose generation fails are marked `skipped` with the reason, **never silently dropped**.
    """
    generate = generate_draft or _default_generate_draft
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        if schedule is None or schedule.status not in ("armed", "running"):
            return {"generated": 0, "failed": 0}
        user_id = schedule.user_id
        todo = [
            (row.id, row.lead_id)
            for row in db.query(SalesAgentSendScheduleItemORM)
            .filter(
                SalesAgentSendScheduleItemORM.schedule_id == schedule.id,
                SalesAgentSendScheduleItemORM.status == "pending_draft",
            )
            .order_by(SalesAgentSendScheduleItemORM.id.asc())
            .limit(max(0, int(limit or 0)))
            .all()
        ]
    finally:
        db.close()  # <- release the connection; none is held during the tens of seconds waiting on the model below
    if not todo:
        return {"generated": 0, "failed": 0}

    semaphore = asyncio.Semaphore(max(1, PREGEN_CONCURRENCY))

    async def _one(item_id: int, lead_id: int) -> bool:
        async with semaphore:
            try:
                draft_id = int(await generate(user_id, lead_id) or 0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pregenerate draft failed: schedule=%s item=%s", schedule_id, item_id, exc_info=True)
                _finish_item(session_factory, item_id, "skipped", skip_reason=_generation_failure_text(exc))
                return False
        if draft_id <= 0:
            _finish_item(session_factory, item_id, "skipped", skip_reason="这家企业的邮件没能写出来,资料太少了;补一下企业信息再试")
            return False
        _attach_draft(session_factory, item_id, draft_id)
        return True

    results = await asyncio.gather(*(_one(i, l) for i, l in todo), return_exceptions=False)
    return {"generated": sum(1 for ok in results if ok), "failed": sum(1 for ok in results if not ok)}


_GENERIC_GENERATION_FAILURE = "这家企业的邮件没能写出来(生成时出错),已跳过;可以在企业详情里单独重写一封"


def _generation_failure_text(exc: Exception) -> str:
    """A generation failure must say clearly which step failed; a generic "generation failed" is not allowed.

    Uses `public_error_text` rather than only accepting `public=True`: most business errors with interpolation are not marked
    public (marking them would hit the interpolation gate), yet a message like "this company's follow-up email failed the compliance check: lead status is
    Unsubscribed" is exactly what most belongs in skip_reason. One pass through the word list is enough; only those that fail it
    fall back to the generic message below.
    """
    if isinstance(exc, SalesAgentError):
        text = public_error_text(exc, _GENERIC_GENERATION_FAILURE)
        if text != _GENERIC_GENERATION_FAILURE:
            return text[:200]
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "写这封信超时了,模型这会儿太慢;这家先跳过,回头单独点一下重写"
    return _GENERIC_GENERATION_FAILURE


def pregenerate_schedule_drafts(
    session_factory: Callable[[], Session],
    schedule_id: int,
    limit: int = PREGEN_HEAD_COUNT,
    *,
    generate_draft: DraftGenerator | None = None,
) -> dict[str, int]:
    """Sync wrapper of the above. The send thread is purely synchronous and runs it on this thread's own loop.

    **The loop is one per thread and long-lived, not created per call.** This is not an optimization:
    `get_sales_agent_model()` holds an httpx connection pool underneath, and an httpx client is bound to the loop that created
    it; creating and closing one each time means the next call gets the same model whose loop is already closed,
    straight into `Event loop is closed`. A send thread fills a dozen drafts per round, so this error would blow up on the second.

    Correspondingly, `model.py`'s cache key includes the thread id: one thread = one loop = one connection pool.
    The scheduler process's send threads are a fixed few long-lived ones (the worker pool in `scheduler.py`), so neither
    cache grows with the number of plans.
    """
    loop = _thread_event_loop()
    return loop.run_until_complete(
        pregenerate_schedule_drafts_async(session_factory, schedule_id, limit, generate_draft=generate_draft)
    )


_THREAD_STATE = threading.local()


def _thread_event_loop() -> asyncio.AbstractEventLoop:
    """This thread's long-lived event loop. Closing it invalidates the httpx pool on top of it too; see the section above."""
    loop = getattr(_THREAD_STATE, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _THREAD_STATE.loop = loop
    return loop


def _attach_draft(session_factory: Callable[[], Session], item_id: int, draft_id: int) -> None:
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is None or item.status != "pending_draft":
            return  # Cancelled in the meantime; do not push it back to pending
        item.outreach_log_id = int(draft_id)
        item.status = "pending"
        item.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _finish_item(
    session_factory: Callable[[], Session],
    item_id: int,
    status: str,
    *,
    skip_reason: str = "",
    delivery_id: int | None = None,
    contact_id: int | None = None,
) -> None:
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is None:
            return
        item.status = status
        if skip_reason:
            item.skip_reason = skip_reason[:255]
        if delivery_id:
            item.email_delivery_id = int(delivery_id)
        if contact_id:
            item.contact_id = int(contact_id)
        item.finished_at = datetime.utcnow()
        item.updated_at = item.finished_at
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- execution


def due_send_schedules(db: Session, now_utc: datetime | None = None, limit: int = 20) -> list[int]:
    """Plan ids due for dispatch. **Picked up `PREGEN_LEAD_TIME` early, not at the start time**: head drafts need time."""
    if not _schedule_tables_ready(db):
        return []
    now = now_utc or datetime.utcnow()
    rows = (
        db.query(SalesAgentSendScheduleORM.id)
        .filter(
            SalesAgentSendScheduleORM.status == "armed",
            SalesAgentSendScheduleORM.scheduled_at <= now + PREGEN_LEAD_TIME,
            # Do not pick up and send ones that expired long ago: that is expire's job
            SalesAgentSendScheduleORM.scheduled_at > now - SCHEDULE_EXPIRY,
        )
        .order_by(SalesAgentSendScheduleORM.scheduled_at.asc())
        .limit(max(1, int(limit or 20)))
        .all()
    )
    return [row[0] for row in rows]


def expire_overdue_schedules(db: Session, now_utc: datetime | None = None) -> int:
    """Mark whole batches not finished past `scheduled_at + 4h` as `expired`. **No overnight catch-up sends.**

    Suddenly receiving yesterday afternoon's batch the next morning is worse than sending nothing: the user no longer remembers
    scheduling it, and recipients see an obviously delayed time.
    """
    if not _schedule_tables_ready(db):
        return 0
    now = now_utc or datetime.utcnow()
    rows = (
        db.query(SalesAgentSendScheduleORM)
        .filter(
            SalesAgentSendScheduleORM.status.in_(("armed", "running")),
            SalesAgentSendScheduleORM.scheduled_at <= now - SCHEDULE_EXPIRY,
        )
        .all()
    )
    for row in rows:
        row.status = "expired"
        row.updated_at = now
        db.query(SalesAgentSendScheduleItemORM).filter(
            SalesAgentSendScheduleItemORM.schedule_id == row.id,
            SalesAgentSendScheduleItemORM.status.in_(ITEM_OPEN_STATUSES),
        ).update(
            {
                "status": "skipped",
                "skip_reason": "计划超过 4 小时没发完,已过期;不做隔夜补发,需要的话重新排一次",
                "finished_at": now,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    db.commit()
    return len(rows)


def reclaim_stale_items(db: Session, now_utc: datetime | None = None) -> int:
    """Reclaim items whose `claimed_at` has had no progress for over 5 minutes back to `pending`. Recovery path when the process is killed."""
    if not _schedule_tables_ready(db):
        return 0
    now = now_utc or datetime.utcnow()
    count = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(
            SalesAgentSendScheduleItemORM.status == "sending",
            SalesAgentSendScheduleItemORM.claimed_at.isnot(None),
            SalesAgentSendScheduleItemORM.claimed_at <= now - CLAIM_STALE,
        )
        .update({"status": "pending", "claimed_at": None, "updated_at": now}, synchronize_session=False)
    )
    db.commit()
    return count


def mark_schedule_running(session_factory: Callable[[], Session], schedule_id: int) -> bool:
    """`armed -> running`, **before any sending action**, not as soon as the lock is acquired.

    §4.5's "cancel during sending" depends entirely on this step: cancellation takes effect by "re-read status before each email,
    stop if not running", and that presupposes someone flipped the status to running.

    Head pre-generation and waiting before the start time all stay in `armed`: nothing is sent during that time, so cancelling costs nothing.
    """
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        if schedule is None or schedule.status != "armed":
            return False
        schedule.status = "running"
        schedule.updated_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def _schedule_status(session_factory: Callable[[], Session], schedule_id: int) -> str:
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        return schedule.status if schedule is not None else ""
    finally:
        db.close()


def wait_until_scheduled(
    session_factory: Callable[[], Session],
    schedule_id: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = datetime.utcnow,
) -> bool:
    """Sleep until `scheduled_at`, re-checking status every 10 seconds. Returns "whether to continue sending".

    **This re-check cannot be omitted**: without it, if the user cancels a 15:00 plan at 14:56, the thread would still
    sleep until the time and start sending.
    """
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        target = schedule.scheduled_at if schedule is not None else None
    finally:
        db.close()
    if target is None:
        return False
    while True:
        remaining = (target - now_fn()).total_seconds()
        if remaining <= 0:
            return _schedule_status(session_factory, schedule_id) == "armed"
        if _schedule_status(session_factory, schedule_id) != "armed":
            return False
        sleep(min(STATUS_RECHECK_SECONDS, remaining))


def run_one_schedule(
    session_factory: Callable[[], Session],
    schedule_id: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    generate_draft: DraftGenerator | None = None,
    now_fn: Callable[[], datetime] = datetime.utcnow,
) -> dict[str, int]:
    """Send a plan already flipped to `running` serially to completion.

    Serial is not untuned performance but a business requirement: the 30-second throttle keeps the sender mailbox clear of provider risk control, and passing the compliance gate per email
    is where the rule "scheduled sending is not mass mailing" lands (§1.5).
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0, "cancelled": 0}
    throttle, last_sent_at = _schedule_throttle(session_factory, schedule_id), None
    while True:
        # 1. Re-read the plan status before each email. Once the executor dives into its sleep loop,
        #    the user's cancel click only changes a DB field; without actively re-checking it is as if never clicked.
        if _schedule_status(session_factory, schedule_id) != "running":
            _cancel_remaining(session_factory, schedule_id)
            break
        item_id = _claim_next_item(session_factory, schedule_id)
        if item_id is None:
            break
        if last_sent_at is not None:
            # 2. Use the throttle gap to fill later drafts first, then sleep out the remaining time. Generation is 3x faster than sending,
            #    so this step almost always ends early; but **the next email's send time must still not be earlier than the previous +
            #    throttle**: the throttle is an external promise, and filling drafts is no reason to send early.
            _pregen_quietly(session_factory, schedule_id, generate_draft)
            rest = throttle - (now_fn() - last_sent_at).total_seconds()
            if rest > 0:
                sleep(rest)
            # 3. Re-check **after waking**, not before sleeping: draft filling plus throttle adds up to tens of seconds,
            #    and checking before would entirely miss a cancel clicked during those seconds, sending one extra email for nothing. This line
            #    sits right next to the send line below; no other wait may be inserted between them.
            if _schedule_status(session_factory, schedule_id) != "running":
                _release_item(session_factory, item_id)
                _cancel_remaining(session_factory, schedule_id)
                break
        outcome = _run_one_item(session_factory, schedule_id, item_id, generate_draft=generate_draft, sleep=sleep)
        stats[outcome] = stats.get(outcome, 0) + 1
        if outcome in ("sent", "failed"):
            last_sent_at = now_fn()   # Throttle starts only after a real send; skipped ones do not take a beat
    _finalize_schedule(session_factory, schedule_id)
    # The cancelled count is **re-queried from the DB, not accumulated**: the persistence of a cancellation has two sources: the user's
    # `cancel_send_schedule` call already changed the pending items itself, and counting again in the loop would only count
    # the leftover remainder. "N companies not sent" in the receipt must match the list the user sees, so only the DB is authoritative.
    stats["cancelled"] = _count_items(session_factory, schedule_id, "cancelled")
    # Results always go through the conversation (§4.6): the drawer only shows plan-state numbers; who was and was not sent after completion
    # can only be explained by this session message. When the user is not on the page, it is already waiting in the session.
    write_schedule_receipt(session_factory, schedule_id)
    return stats


# ---------------------------------------------------------------- receipts (§4.6)

_RECEIPT_DETAIL_LIMIT = 8


def _receipt_company_name(db: Session, item: SalesAgentSendScheduleItemORM) -> str:
    company = db.get(SalesAgentCompanyORM, item.company_id) if item.company_id else None
    return (company.name if company is not None else "") or "(未命名企业)"


def _last_attempt(item: SalesAgentSendScheduleItemORM) -> dict[str, Any]:
    attempts = _loads(item.attempted_json, [])
    return attempts[-1] if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict) else {}


def build_schedule_receipt(db: Session, schedule: SalesAgentSendScheduleORM) -> str:
    """A finished plan -> one receipt sentence for the user.

    All three requirements are implemented here and must not be left to the model to phrase:

    1. **When "12 scheduled" and "11 actually sent" do not match, explain who is missing and why.** The difference
       has only one source: time passes between confirmation and sending, during which compliance status can change (each email is re-gated
       right before sending, §4.1). Not the norm, but when it happens it must never be silent: the user thinks 12 were sent,
       actually 11, without knowing which one is missing; worse than never scheduling the plan;
    2. **Skipped items always carry a reason**, never a dead-end phrase like "1 skipped";
    3. **A broken sender mailbox is reported separately.** Sender-side failures (authorization code changed, cannot connect) and "recipient rejected" are two
       completely different things, and the user's next action differs completely: one is fixing their own config, the other is
       not retrying this company. Lumping them into "12 failed to send" says nothing.
    """
    items = (
        db.query(SalesAgentSendScheduleItemORM)
        .filter(SalesAgentSendScheduleItemORM.schedule_id == schedule.id)
        .order_by(SalesAgentSendScheduleItemORM.id.asc())
        .all()
    )
    if not items:
        return ""
    when = _bj(schedule.scheduled_at)
    sent = [i for i in items if i.status == "sent"]
    failed = [i for i in items if i.status == "failed"]
    skipped = [i for i in items if i.status == "skipped"]
    cancelled = [i for i in items if i.status == "cancelled"]

    # Criterion for a broken sender mailbox: **not a single email sent, and the last attempt of every failure was sender side**.
    # If even one was sent, the problem is not the sender mailbox, so use the generic wording below.
    sender_side = [i for i in failed if str(_last_attempt(i).get("side") or "") == "sender"]
    if failed and not sent and len(sender_side) == len(failed):
        account = db.get(SalesAgentEmailAccountORM, schedule.email_account_id) if schedule.email_account_id else None
        addr = (account.from_email if account is not None else "") or "当前发件箱"
        reason = str(_last_attempt(sender_side[0]).get("reason") or "").strip() or "发信失败"
        return (
            f"{when} 的计划没能发出:发件箱 {addr} {reason},{len(failed)} 家都没发。"
            "请到邮件设置里检查这个发件箱(授权码、服务器地址、端口),改好之后可以重新排期 —— "
            "这批企业一封都没收到,重排不会重复打扰。"
        )

    counts = [f"发出 {len(sent)} 封"]
    if failed:
        counts.append(f"{len(failed)} 家没发成")
    if skipped:
        counts.append(f"{len(skipped)} 家跳过")
    if cancelled:
        counts.append(f"{len(cancelled)} 家没有发")
    head = f"{when} 的计划" + ("已取消" if schedule.status == "cancelled" else "已完成")
    text = f"{head}:{'、'.join(counts)}。"

    # 1 and 2: write per company. Name and reason together; "who is missing" and "why" must not be split into two sentences.
    detail = [
        f"{_receipt_company_name(db, item)}:{str(item.skip_reason or '').strip() or '没有留下原因,可以在计划详情里看这一条'}"
        for item in (failed + skipped)
    ]
    if detail:
        shown = detail[:_RECEIPT_DETAIL_LIMIT]
        text += "没发出去的:" + "；".join(shown) + "。"
        if len(detail) > len(shown):
            text += f"另外还有 {len(detail) - len(shown)} 家,在计划详情里能逐条看到原因。"
    if cancelled:
        text += f"计划被取消时还剩 {len(cancelled)} 家没轮到,它们一封都没发。"
    return text


def write_schedule_receipt(session_factory: Callable[[], Session], schedule_id: int) -> str:
    """Write the receipt into **the session that created this plan**, returning the written text (empty string if not written).

    Phase 1 has no push: nothing notifies the frontend after writing (§4.6 "polling, not push"), and users only see it
    next time they come in, so this message must carry the complete conclusion, not "see the panel for details".

    **Failing to write must not raise.** The emails are already sent; the receipt is merely unseen; raising here would make the caller
    treat a batch of successful sends as failed, trading a visibility problem for a correctness problem.
    """
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        if schedule is None or not schedule.created_by_session_id:
            return ""
        text = build_schedule_receipt(db, schedule)
        if not text:
            return ""
        db.add(
            SalesAgentChatMessageORM(
                session_id=int(schedule.created_by_session_id),
                role="assistant",
                content=text,
                intent="send_schedule_receipt",
                metadata_json=_dumps({"kind": "send_schedule_receipt", "schedule_id": schedule.id}),
            )
        )
        session = db.get(SalesAgentChatSessionORM, int(schedule.created_by_session_id))
        if session is not None:
            # The session list is sorted by updated_at; without bumping it, this receipt sits in the seventh session, as good as unwritten.
            session.updated_at = datetime.utcnow()
        db.commit()
        return text
    except Exception:  # noqa: BLE001
        logger.warning("send schedule receipt failed: schedule=%s", schedule_id, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return ""
    finally:
        _close(db)


def _count_items(session_factory: Callable[[], Session], schedule_id: int, status: str) -> int:
    db = session_factory()
    try:
        return (
            db.query(SalesAgentSendScheduleItemORM)
            .filter(
                SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
                SalesAgentSendScheduleItemORM.status == status,
            )
            .count()
        )
    finally:
        db.close()


def _schedule_throttle(session_factory: Callable[[], Session], schedule_id: int) -> float:
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        return float(max(1, int(schedule.throttle_seconds or 30))) if schedule is not None else 30.0
    finally:
        db.close()


def _claim_next_item(session_factory: Callable[[], Session], schedule_id: int) -> int | None:
    """Claim the next item. On PG uses `skip_locked`; on SQLite SQLAlchemy silently ignores it,
    so concurrency correctness can only be verified on real PG (CLAUDE.md notes this).
    """
    db = session_factory()
    try:
        query = (
            db.query(SalesAgentSendScheduleItemORM)
            .filter(
                SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
                SalesAgentSendScheduleItemORM.status.in_(("pending", "pending_draft")),
            )
            .order_by(SalesAgentSendScheduleItemORM.id.asc())
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        item = query.first()
        if item is None:
            return None
        item.status = "sending"
        item.claimed_at = datetime.utcnow()
        item.updated_at = item.claimed_at
        db.commit()
        return item.id
    finally:
        db.close()


def _release_item(session_factory: Callable[[], Session], item_id: int) -> None:
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is not None and item.status == "sending":
            item.status = "cancelled"
            item.claimed_at = None
            item.finished_at = datetime.utcnow()
            item.updated_at = item.finished_at
            db.commit()
    finally:
        db.close()


def _cancel_remaining(session_factory: Callable[[], Session], schedule_id: int) -> int:
    db = session_factory()
    try:
        now = datetime.utcnow()
        count = (
            db.query(SalesAgentSendScheduleItemORM)
            .filter(
                SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
                SalesAgentSendScheduleItemORM.status.in_(ITEM_OPEN_STATUSES),
            )
            .update({"status": "cancelled", "finished_at": now, "updated_at": now}, synchronize_session=False)
        )
        db.commit()
        return count
    finally:
        db.close()


def _pregen_quietly(
    session_factory: Callable[[], Session], schedule_id: int, generate_draft: DraftGenerator | None
) -> None:
    """Fill drafts during the throttle gap. **Exceptions in this step are always swallowed**: the companies that cannot be filled mark themselves skipped,
    and interrupting the whole batch because "some later company could not be written" would be disproportionate."""
    try:
        pregenerate_schedule_drafts(session_factory, schedule_id, PREGEN_HEAD_COUNT, generate_draft=generate_draft)
    except Exception:  # noqa: BLE001
        logger.warning("inline pregenerate failed: schedule=%s", schedule_id, exc_info=True)


def _finalize_schedule(session_factory: Callable[[], Session], schedule_id: int) -> None:
    db = session_factory()
    try:
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        if schedule is None or schedule.status != "running":
            return
        left = (
            db.query(SalesAgentSendScheduleItemORM)
            .filter(
                SalesAgentSendScheduleItemORM.schedule_id == schedule.id,
                SalesAgentSendScheduleItemORM.status.in_(ITEM_OPEN_STATUSES),
            )
            .count()
        )
        if left:
            return
        schedule.status = "done"
        schedule.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_one_item(
    session_factory: Callable[[], Session],
    schedule_id: int,
    item_id: int,
    *,
    generate_draft: DraftGenerator | None,
    sleep: Callable[[float], None],
) -> str:
    """Send one company. Returns `sent` / `failed` / `skipped` / `cancelled`.

    Scheduling and sending may be a dozen hours apart, so everything below must be **re-checked on the spot**, never sending from a snapshot taken
    at scheduling time (§4.1.2): whether the company still exists, whether the contact email changed, the suppression list, the daily quota.
    """
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        if item is None or schedule is None:
            return "skipped"
        user_id = schedule.user_id
        lead = db.get(SalesAgentLeadRecommendationORM, item.lead_id) if item.lead_id else None
        company = db.get(SalesAgentCompanyORM, item.company_id) if item.company_id else None
        # Company deleted or recommendation withdrawn: mark skipped with the reason; do not raise and interrupt the whole batch.
        if lead is None or lead.user_id != user_id or company is None:
            _close(db)
            _finish_item(session_factory, item_id, "skipped", skip_reason="这家企业的推荐已经不在了(被删除或撤回),没有发")
            return "skipped"
        if sender_daily_remaining(db, schedule) <= 0:
            _close(db)
            _finish_item(
                session_factory, item_id, "skipped",
                skip_reason=f"发件箱今天已经发满 {schedule.daily_cap} 封,剩下的没有发",
            )
            _skip_rest_for_cap(session_factory, schedule_id, schedule.daily_cap)
            return "skipped"
        needs_draft = item.status == "sending" and not item.outreach_log_id
        lead_id = lead.id
    finally:
        _close(db)

    if needs_draft:
        # Its turn has come but the draft is not ready (user armed at the last moment, or the model is slow): **fill it in place and then send,
        # no skipping, no reordering**. Only if it cannot be produced is it marked skipped as a generation failure, moving on to the next.
        _pregen_one(session_factory, schedule_id, item_id, lead_id, generate_draft)

    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is None:
            return "skipped"
        if item.status != "sending":
            # The draft-filling step marked it (skipped), or the user cancelled in the meantime
            return "cancelled" if item.status == "cancelled" else "skipped"
        if not item.outreach_log_id:
            _close(db)
            _finish_item(session_factory, item_id, "skipped", skip_reason="这家企业的邮件没写出来,已跳过")
            return "skipped"
        lead = db.get(SalesAgentLeadRecommendationORM, item.lead_id)
        schedule = db.get(SalesAgentSendScheduleORM, int(schedule_id))
        user_id = schedule.user_id
        draft_id = item.outreach_log_id
        # The recipient is **re-fetched on the spot**: the email at scheduling time may have been changed. Also check the suppression list;
        # fall-through backup addresses must pass the gate too, otherwise "first choice bounced -> fall through" fishes out an unsubscribed address.
        candidates = [
            (row.id, email)
            for row, email, _ in _delivery_recipient_candidates(
                db, lead, limit=MAX_RECIPIENT_FALLBACK, preferred_contact_id=item.contact_id, user_id=user_id
            )
        ]
        throttle = float(max(1, int(schedule.throttle_seconds or 30)))
    finally:
        _close(db)

    if not candidates:
        _finish_item(
            session_factory, item_id, "skipped",
            skip_reason="这家企业现在没有可用的收件邮箱(可能被改掉、退订或加入了不再联系名单)",
        )
        return "skipped"

    attempts: list[dict[str, Any]] = []
    for index, (contact_id, email) in enumerate(candidates):
        if index:
            # Throttle between fall-throughs too: switching addresses does not mean switching sender mailboxes.
            sleep(throttle)
        result = _attempt_send(session_factory, user_id, draft_id, contact_id, email)
        attempts.append(result)
        _record_attempts(session_factory, item_id, attempts)
        if result["result"] == "sent":
            _finish_item(
                session_factory, item_id, "sent",
                delivery_id=result.get("delivery_id"), contact_id=contact_id,
            )
            return "sent"
        # **Only recipient-side failures fall through.** For sender-side failures (wrong authorization code, cannot connect, timeout) changing recipients is
        # useless, and would only burn through all of this company's mailboxes in one config error.
        if result["side"] != "recipient":
            break
    reason = attempts[-1]["reason"] if attempts else "发送失败"
    tried = "、".join(a["email"] for a in attempts)
    _finish_item(
        session_factory, item_id, "failed",
        skip_reason=f"{reason}(试过:{tried})"[:255],
        delivery_id=attempts[-1].get("delivery_id") if attempts else None,
    )
    return "failed"


def _close(db: Session) -> None:
    try:
        db.close()
    except Exception:  # noqa: BLE001
        pass


def _pregen_one(
    session_factory: Callable[[], Session],
    schedule_id: int,
    item_id: int,
    lead_id: int,
    generate_draft: DraftGenerator | None,
) -> None:
    """Fill this item's draft in place. Put it back to `pending_draft` first so pre-generation recognizes it."""
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is None or item.status != "sending":
            return
        item.status = "pending_draft"
        db.commit()
    finally:
        _close(db)
    try:
        pregenerate_schedule_drafts(session_factory, schedule_id, 1, generate_draft=generate_draft)
    except Exception:  # noqa: BLE001
        logger.warning("inline draft for item %s failed", item_id, exc_info=True)
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is not None and item.status == "pending":
            item.status = "sending"   # Filled; claim it back and continue sending
            db.commit()
    finally:
        _close(db)


def _attempt_send(
    session_factory: Callable[[], Session],
    user_id: int,
    draft_id: int,
    contact_id: int,
    email: str,
) -> dict[str, Any]:
    """Send once. **Goes through `confirm_outreach_send`, never bypasses it** (§1.1).

    The compliance gate, suppression list, delivery records, receipts and `lead.status` transitions all hang off it; building
    a parallel path for automatic sending would diverge sooner or later, and the divergence ("scheduled emails skipped the compliance gate")
    is exactly the kind that must never go wrong.

    Specifying the recipient is done by writing `contact_id` into the draft's metadata, an entry `confirm_outreach_send` already
    recognizes, not a new back door.
    """
    db = session_factory()
    try:
        draft = db.get(SalesAgentOutreachLogORM, int(draft_id))
        if draft is None:
            return {"email": email, "result": "failed", "side": "sender", "reason": "草稿已经不在了"}
        meta = _loads(draft.metadata_json, {})
        meta["contact_id"] = int(contact_id)
        draft.metadata_json = _dumps(meta)
        db.commit()
    finally:
        _close(db)

    db = session_factory()
    try:
        payload = confirm_outreach_send(db, user_id, int(draft_id), True, actor=f"schedule:user:{user_id}")
    except SalesAgentError as exc:
        return {
            "email": email,
            "result": "failed",
            # When unsure, count it as sender side: not falling through is safer than wrongly burning a string of addresses.
            "side": getattr(exc, "failure_side", "sender"),
            "reason": str(exc)[:200] if getattr(exc, "public", False) else "这封没发出去,请到邮件面板看具体原因",
        }
    except Exception:  # noqa: BLE001
        logger.warning("scheduled send raised: draft=%s", draft_id, exc_info=True)
        return {"email": email, "result": "failed", "side": "sender", "reason": "这封没发出去(发送时出错),请到邮件面板看具体原因"}
    finally:
        _close(db)

    delivery = payload.get("delivery") or {}
    delivery_id = delivery.get("id")
    status = str(delivery.get("status") or "")
    if status == "sent":
        return {"email": email, "result": "sent", "side": "", "reason": "", "delivery_id": delivery_id}
    side = "sender"
    if delivery_id:
        db = session_factory()
        try:
            side = delivery_failure_side(db.get(SalesAgentEmailDeliveryORM, int(delivery_id)))
        finally:
            _close(db)
    return {
        "email": email,
        "result": "failed",
        "side": side,
        "reason": str(delivery.get("failure_reason") or payload.get("hint") or "这封没发出去")[:200],
        "delivery_id": delivery_id,
    }


def _record_attempts(session_factory: Callable[[], Session], item_id: int, attempts: list[dict[str, Any]]) -> None:
    db = session_factory()
    try:
        item = db.get(SalesAgentSendScheduleItemORM, int(item_id))
        if item is None:
            return
        item.attempted_json = _dumps(attempts)
        item.attempt_count = len(attempts)
        item.updated_at = datetime.utcnow()
        db.commit()
    finally:
        _close(db)


def _skip_rest_for_cap(session_factory: Callable[[], Session], schedule_id: int, cap: int) -> None:
    db = session_factory()
    try:
        now = datetime.utcnow()
        db.query(SalesAgentSendScheduleItemORM).filter(
            SalesAgentSendScheduleItemORM.schedule_id == int(schedule_id),
            SalesAgentSendScheduleItemORM.status.in_(("pending", "pending_draft")),
        ).update(
            {
                "status": "skipped",
                "skip_reason": f"发件箱今天已经发满 {cap} 封,剩下的没有发",
                "finished_at": now,
                "updated_at": now,
            },
            synchronize_session=False,
        )
        db.commit()
    finally:
        _close(db)


def run_due_send_schedules(
    session_factory: Callable[[], Session],
    now_utc: datetime | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    generate_draft: DraftGenerator | None = None,
    now_fn: Callable[[], datetime] = datetime.utcnow,
) -> dict[str, Any]:
    """Serial driver: pick up due plans and run them one by one to completion.

    **The scheduler process does not use it**: it needs "main thread scans the table and grabs locks, send threads do the work" (§4.2),
    and running a 6-minute plan on the main thread would stall the 60-second tick entirely. This function is for tests
    and for cases that "just want one synchronous run in a single process", putting the orchestration logic somewhere without threads and locks
    so it can be tested as a whole.
    """
    db = session_factory()
    try:
        expire_overdue_schedules(db, now_utc)
        reclaim_stale_items(db, now_utc)
        ids = due_send_schedules(db, now_utc)
    finally:
        _close(db)
    out: dict[str, Any] = {"schedules": len(ids), "sent": 0, "failed": 0, "skipped": 0, "cancelled": 0}
    for schedule_id in ids:
        try:
            pregenerate_schedule_drafts(session_factory, schedule_id, PREGEN_HEAD_COUNT, generate_draft=generate_draft)
            if not wait_until_scheduled(session_factory, schedule_id, sleep=sleep, now_fn=now_fn):
                continue
            if not mark_schedule_running(session_factory, schedule_id):
                continue
            stats = run_one_schedule(
                session_factory, schedule_id, sleep=sleep, generate_draft=generate_draft, now_fn=now_fn
            )
        except Exception:  # noqa: BLE001
            # One plan's error must not bring down the other plans in this round. A dead thread means the feature silently stops working,
            # and silent failure is the worst failure mode for this feature: its whole selling point is "say it once and forget about it".
            logger.exception("run schedule %s failed", schedule_id)
            continue
        for key, value in stats.items():
            out[key] = out.get(key, 0) + value
    return out
