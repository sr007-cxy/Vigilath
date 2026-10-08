"""The six REST endpoints of scheduled send (A7, design §4.4).

The service layer is thoroughly tested in `test_send_schedule.py`; here we only watch **what the endpoint layer itself can break**:

1. **All six endpoints must be sync `def`.** Everything below is sync SQLAlchemy; writing `async def` runs
   blocking IO on the event loop thread -- stalling not just this request but the whole agent process (the 1.4 issue).
   This can only be checked via reflection, no test case can catch it, so it gets its own pin;
2. **`scheduled_at` without a timezone is read as Beijing time.** Read as UTC it's off by 8 hours across the board --
   10:00 picked on the panel becomes an actual 18:00, a difference that's not even conspicuous;
3. **Without a time, ask; don't pick for the user.** And the follow-up wording must be **the same sentence** as in chat --
   a different temperament per entry point is the opposite of Sales Agent never emitting generic errors;
4. **After `running`, the list can't be changed, only cancelled.** The 4.5 panel grays out "change time" based on this;
   inconsistent rules on the two sides give "frontend lets you click, backend then refuses";
5. **Other people's schedules always 404**, never 403 -- don't confirm whether the id exists.

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_send_schedule_api.py -q
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import agents.sales_agent.api as sales_api
from agents.sales_agent.api import router
from agents.sales_agent.models import (
    SalesAgentChatSessionORM,
    SalesAgentCompanyContactORM,
    SalesAgentCompanyORM,
    SalesAgentEmailAccountORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentSendScheduleItemORM,
    SalesAgentSendScheduleORM,
)
from agents.sales_agent.time_parse import bj_day_start_utc, to_local
from geo.database import Base
from geo.models.user import UserORM

USER = 7700123
OTHER_USER = 7700124
BASE = "/api/sales-agent/outreach/schedules"   # the router carries its own /sales-agent prefix


def _tomorrow_local(hour: int = 15, minute: int = 0) -> datetime:
    """Naive UTC for tomorrow at hour:minute Beijing time.

    Always use "tomorrow" rather than "today + N hours": the latter computes an already-past time during some hours of the day
    (the one time rule is "not earlier than now"), and the test turns red inexplicably.
    """
    return bj_day_start_utc(0) + timedelta(days=1, hours=hour, minutes=minute)


def _local_iso(utc_at: datetime) -> str:
    """Naive UTC -> the **timezone-less** local string the picker would send."""
    return to_local(utc_at).strftime("%Y-%m-%dT%H:%M")


@pytest.fixture()
def db():
    # TestClient sends requests from another thread; without pinning the pool, in-memory sqlite looks empty from there.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        UserORM(id=USER, email="schedule-api@example.invalid", password_hash="x", is_active=True),
        UserORM(id=OTHER_USER, email="other-api@example.invalid", password_hash="x", is_active=True),
        SalesAgentEmailAccountORM(
            id=1, scope="user", user_id=USER, provider_type="smtp", enabled=True, is_default=True,
            from_email="me@example.com", smtp_host="smtp.example.com",
            smtp_username="me@example.com", smtp_secret_encrypted="x",
        ),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[sales_api.get_current_user] = lambda: db.get(UserORM, USER)
    app.dependency_overrides[sales_api.get_db] = lambda: db
    return TestClient(app)


def _seed_leads(db, names: list[str], *, user_id: int = USER) -> list[int]:
    lead_ids = []
    start = db.query(SalesAgentCompanyORM).count() + 1
    for offset, name in enumerate(names):
        index = start + offset
        db.add_all([
            SalesAgentCompanyORM(id=index, name=name, source="manual", data_status="active"),
            SalesAgentLeadRecommendationORM(id=index, user_id=user_id, company_id=index),
            SalesAgentCompanyContactORM(company_id=index, email=f"c{index}@example.com",
                                        consent_status="unknown", is_reachable=True),
        ])
        lead_ids.append(index)
    db.commit()
    return lead_ids


def _create(client, db, names=("甲科技", "乙科技", "丙科技"), **body) -> dict:
    lead_ids = _seed_leads(db, list(names))
    payload = {"lead_ids": lead_ids, "when_text": "明天下午三点", **body}
    resp = client.post(BASE, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- sync def


SCHEDULE_ENDPOINTS = {
    ("POST", "/sales-agent/outreach/schedules"),
    ("GET", "/sales-agent/outreach/schedules"),
    ("GET", "/sales-agent/outreach/schedules/{schedule_id}"),
    ("POST", "/sales-agent/outreach/schedules/{schedule_id}/confirm"),
    ("PATCH", "/sales-agent/outreach/schedules/{schedule_id}"),
    ("DELETE", "/sales-agent/outreach/schedules/{schedule_id}"),
    # Estimate for the batch dialog (read-only, not persisted to DB)
    ("POST", "/sales-agent/outreach/schedules/preview"),
}


def test_all_schedule_endpoints_exist_and_are_sync():
    """All endpoints present, and **not a single `async def`** (a hard rule of 4.4).

    `async def` + sync SQLAlchemy doesn't error; it caps concurrency and lets slow queries drag down the whole process.
    Written as `def`, FastAPI throws it into the anyio thread pool, keeping the blocking contained in a thread.
    The big block of `async def` above in api.py is history, not a template -- copying it walks right back into the trap.
    """
    found = {
        (method, r.path)
        for r in router.routes
        for method in (getattr(r, "methods", None) or set())
        if "/outreach/schedules" in getattr(r, "path", "")
    }
    assert found == SCHEDULE_ENDPOINTS, (
        f"端点对不上:多出 {found - SCHEDULE_ENDPOINTS},缺少 {SCHEDULE_ENDPOINTS - found}"
    )
    bad = [
        r.path for r in router.routes
        if "/outreach/schedules" in getattr(r, "path", "") and inspect.iscoroutinefunction(r.endpoint)
    ]
    assert not bad, f"这几个写成了 async def:{bad}"


# ---------------------------------------------------------------- create schedule


def test_create_returns_the_preview_and_sends_nothing(client, db):
    data = _create(client, db)

    assert [x["company"] for x in data["included"]] == ["甲科技", "乙科技", "丙科技"]
    assert data["schedule"]["status"] == "draft"
    assert data["schedule"]["status_text"] == "待确认"
    assert data["schedule"]["sent"] == 0
    # The time the whole batch finishes must be given in the preview: users hearing "send at 15:00" assume "finished by 15:00".
    assert "发完" in data["hint"]
    items = db.query(SalesAgentSendScheduleItemORM).all()
    assert {row.status for row in items} == {"pending_draft"}


def test_skipped_companies_get_a_reason_each(client, db):
    """"Some companies can't be sent to" doesn't count -- give the reason company by company."""
    ok = _seed_leads(db, ["有邮箱科技"])
    company = SalesAgentCompanyORM(id=900, name="没邮箱科技", source="manual", data_status="active")
    db.add_all([company, SalesAgentLeadRecommendationORM(id=900, user_id=USER, company_id=900)])
    db.commit()

    resp = client.post(BASE, json={"lead_ids": [*ok, 900], "when_text": "明天下午三点"})
    assert resp.status_code == 200, resp.text
    skipped = resp.json()["skipped"]
    assert [x["company"] for x in skipped] == ["没邮箱科技"]
    assert skipped[0]["reason"].strip(), "跳过项必须带原因"


def test_naive_scheduled_at_is_read_as_beijing_time(client, db):
    """The `2026-09-14T10:00` sent by the picker is 10 a.m. in the user's eyes, not 10:00 UTC.

    Read as UTC it's off by 8 hours: 10:00 picked on the panel actually happens at 18:00 -- no error,
    just mail really sent at a time the user didn't expect.
    """
    target = _tomorrow_local(10, 30)
    data = _create(client, db, when_text="", scheduled_at=_local_iso(target))

    row = db.query(SalesAgentSendScheduleORM).one()
    assert row.scheduled_at == target
    assert "10:30" in data["schedule"]["scheduled_at_text"]


def test_scheduled_at_with_a_timezone_is_converted(client, db):
    """With an offset, convert by the offset; don't add another +8 on top."""
    target = _tomorrow_local(10, 30)
    data = _create(client, db, when_text="", scheduled_at=to_local(target).strftime("%Y-%m-%dT%H:%M:00+08:00"))

    assert db.query(SalesAgentSendScheduleORM).one().scheduled_at == target
    assert "10:30" in data["schedule"]["scheduled_at_text"]


@pytest.mark.parametrize("body,needle", [
    ({"when_text": "", "scheduled_at": ""}, "两个不同的授权"),
    ({"when_text": "晚点发", "scheduled_at": ""}, "我不会替你挑一个时间"),
    ({"when_text": "明天发", "scheduled_at": ""}, "有日子没有点"),
])
def test_a_missing_time_is_asked_about_not_guessed(client, db, body, needle):
    """The follow-up wording is **the same copy** as in chat (`when_followup_text`).

    A different temperament per entry point is the opposite of "Sales Agent never emits generic errors": if the panel replies
    "invalid time format", the user still only learns "I said it wrong", not "I need to say what time".
    """
    lead_ids = _seed_leads(db, ["甲科技"])
    resp = client.post(BASE, json={"lead_ids": lead_ids, **body})

    assert resp.status_code == 400
    assert needle in resp.json()["detail"]
    assert db.query(SalesAgentSendScheduleORM).count() == 0, "追问的时候不该已经建了计划"


def test_unparseable_scheduled_at_says_what_it_wants(client, db):
    lead_ids = _seed_leads(db, ["甲科技"])
    resp = client.post(BASE, json={"lead_ids": lead_ids, "scheduled_at": "下周随便哪天"})

    assert resp.status_code == 400
    assert "2026-09-14T10:00" in resp.json()["detail"], "要给出想要的格式,不能只说读不出来"


def test_create_accepts_any_future_hour(client, db):
    """Late night can be scheduled too -- the previously hardcoded 09:00-18:00 send window has been removed entirely,
    and the only remaining time rule is "not earlier than now"."""
    data = _create(client, db, when_text="", scheduled_at=_local_iso(_tomorrow_local(22)))

    assert not data.get("time_warning")
    assert data["schedule"]["status"] == "draft"


def test_empty_lead_ids_is_refused(client, db):
    resp = client.post(BASE, json={"lead_ids": [], "when_text": "明天下午三点"})
    assert resp.status_code == 400
    assert "没有选中任何企业" in resp.json()["detail"]


# ---------------------------------------------------------------- confirm


def test_confirm_arms_the_schedule(client, db):
    schedule_id = _create(client, db)["schedule"]["id"]
    resp = client.post(f"{BASE}/{schedule_id}/confirm", json={"confirmed": True})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule"]["status"] == "armed"
    # Not a single email has been sent yet, so the receipt must never say "sent".
    assert "已发送" not in body.get("hint", "")
    assert db.get(SalesAgentSendScheduleORM, schedule_id).email_account_id == 1, "arm 要把发件箱钉死"


def test_confirm_false_cancels_instead_of_doing_nothing(client, db):
    """The user reviewed the list and decided not to send; that's a cancel, not "nothing happened" --
    otherwise this draft hangs in the list forever and must be reviewed again next time.
    """
    schedule_id = _create(client, db)["schedule"]["id"]
    resp = client.post(f"{BASE}/{schedule_id}/confirm", json={"confirmed": False})

    assert resp.status_code == 200, resp.text
    assert resp.json()["schedule"]["status"] == "cancelled"


def test_confirm_accepts_a_late_night_time(client, db):
    """If the user says send at 10 p.m., it's 10 p.m. -- the system no longer imposes business hours."""
    schedule_id = _create(client, db, when_text="", scheduled_at=_local_iso(_tomorrow_local(22)))["schedule"]["id"]
    resp = client.post(f"{BASE}/{schedule_id}/confirm", json={"confirmed": True})

    assert resp.status_code == 200, resp.text
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "armed"


# ---------------------------------------------------------------- list / detail


def test_list_finds_the_schedule_after_the_page_is_closed(client, db):
    """Persistent entry (4.5): something authorized and about to send real outbound mail can't be visible only at the moment it's created."""
    schedule_id = _create(client, db)["schedule"]["id"]
    client.post(f"{BASE}/{schedule_id}/confirm", json={"confirmed": True})

    data = client.get(BASE, params={"status": "armed"}).json()
    assert [x["id"] for x in data["schedules"]] == [schedule_id]
    assert data["total"] == 1


def test_detail_carries_the_item_rows(client, db):
    """The list endpoint can't carry 100 rows of detail; only this endpoint can feed the detail panel and 4.6's polling."""
    schedule_id = _create(client, db)["schedule"]["id"]
    data = client.get(f"{BASE}/{schedule_id}").json()

    assert [x["company_name"] for x in data["items"]] == ["甲科技", "乙科技", "丙科技"]
    # pending_draft is invisible to users: whether a draft is ready is our implementation detail.
    assert {x["status_text"] for x in data["items"]} == {"待发送"}


def test_detail_of_someone_elses_schedule_is_404(client, db):
    other_lead = _seed_leads(db, ["别人家科技"], user_id=OTHER_USER)[0]
    stranger = SalesAgentSendScheduleORM(
        user_id=OTHER_USER, channel="email", status="draft", scheduled_at=_tomorrow_local(),
    )
    db.add(stranger)
    db.commit()
    db.add(SalesAgentSendScheduleItemORM(schedule_id=stranger.id, lead_id=other_lead, status="pending"))
    db.commit()

    for method, url in [
        ("get", f"{BASE}/{stranger.id}"),
        ("delete", f"{BASE}/{stranger.id}"),
    ]:
        resp = getattr(client, method)(url)
        assert resp.status_code == 404, f"{method} {url} 回了 {resp.status_code}"


# ---------------------------------------------------------------- PATCH


def test_patch_drops_items_and_keeps_the_rest(client, db):
    """What the user confirms is **the copy after removals**."""
    schedule_id = _create(client, db)["schedule"]["id"]
    items = client.get(f"{BASE}/{schedule_id}").json()["items"]
    resp = client.patch(f"{BASE}/{schedule_id}", json={"drop_item_ids": [items[1]["id"]]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["dropped"] == 1
    assert resp.json()["remaining"] == 2
    after = {x["company_name"]: x["status_text"] for x in client.get(f"{BASE}/{schedule_id}").json()["items"]}
    assert after == {"甲科技": "待发送", "乙科技": "已取消", "丙科技": "待发送"}


def test_dropping_everything_is_refused(client, db):
    """Removing everything would leave an empty schedule that can't be armed and nobody will delete. If no company is sent to, cancel outright."""
    schedule_id = _create(client, db)["schedule"]["id"]
    items = client.get(f"{BASE}/{schedule_id}").json()["items"]
    resp = client.patch(f"{BASE}/{schedule_id}", json={"drop_item_ids": [x["id"] for x in items]})

    assert resp.status_code == 400
    assert "直接取消" in resp.json()["detail"]


def test_patch_reschedules_and_counts_after_the_drop(client, db):
    """When one request both removes items and changes the time, the company count in the receipt must be **after removal**.

    Getting the order wrong doesn't error; the receipt just says one company too few/many -- and that sentence is exactly what users check against.
    """
    schedule_id = _create(client, db)["schedule"]["id"]
    items = client.get(f"{BASE}/{schedule_id}").json()["items"]
    resp = client.patch(f"{BASE}/{schedule_id}", json={
        "drop_item_ids": [items[0]["id"]],
        "scheduled_at": _local_iso(_tomorrow_local(16)),
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dropped"] == 1
    assert "这 2 家" in body["hint"], body["hint"]
    assert db.get(SalesAgentSendScheduleORM, schedule_id).scheduled_at == _tomorrow_local(16)


def test_patch_with_nothing_to_change_is_rejected(client, db):
    """A 200 that changed nothing makes the frontend think the change succeeded."""
    schedule_id = _create(client, db)["schedule"]["id"]
    resp = client.patch(f"{BASE}/{schedule_id}", json={})

    assert resp.status_code == 400
    assert "没有要改的东西" in resp.json()["detail"]


def test_patch_of_a_missing_time_asks_the_same_question(client, db):
    schedule_id = _create(client, db)["schedule"]["id"]
    resp = client.patch(f"{BASE}/{schedule_id}", json={"when_text": "晚点吧"})

    assert resp.status_code == 400
    assert "改到" in resp.json()["detail"], "改时间的追问要说「改到」,不是「发」"


def test_running_schedule_refuses_patch_but_accepts_delete(client, db):
    """Matches 4.5: after `running`, "change time" is grayed out on the panel, leaving only "cancel".

    Both sides must agree -- if the frontend lets you click and only the backend refuses, the user thinks the change succeeded.
    """
    schedule_id = _create(client, db)["schedule"]["id"]
    client.post(f"{BASE}/{schedule_id}/confirm", json={"confirmed": True})
    row = db.get(SalesAgentSendScheduleORM, schedule_id)
    row.status = "running"
    db.commit()
    items = client.get(f"{BASE}/{schedule_id}").json()["items"]

    moved = client.patch(f"{BASE}/{schedule_id}", json={"scheduled_at": _local_iso(_tomorrow_local(16))})
    assert moved.status_code == 400
    assert "改不了时间" in moved.json()["detail"]

    dropped = client.patch(f"{BASE}/{schedule_id}", json={"drop_item_ids": [items[0]["id"]]})
    assert dropped.status_code == 400
    assert "清单改不动了" in dropped.json()["detail"]

    cancelled = client.delete(f"{BASE}/{schedule_id}")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["schedule"]["status"] == "cancelled"


# ---------------------------------------------------------------- cancel


def test_delete_cancels_the_remaining_items(client, db):
    schedule_id = _create(client, db)["schedule"]["id"]
    resp = client.delete(f"{BASE}/{schedule_id}", params={"reason": "先不发了"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cancelled_items"] == 3
    assert body["schedule"]["status"] == "cancelled"
    assert {row.status for row in db.query(SalesAgentSendScheduleItemORM).all()} == {"cancelled"}


# ---------------------------------------------------------------- batches + one-click scheduling
#
# The three endpoints used by the "email this batch of companies at 3 p.m." dialog. Two things the endpoint layer can break here:
#
# 1. **The list is resolved server-side by batch**, not picked as lead_ids by the frontend -- the frontend's list is
#    a snapshot from the last render and doesn't know about companies withdrawn in the meantime;
# 2. **One click = create + schedule**, and times outside the window must be rejected **before creating**.


# `_session_key` runs it through `uuid.UUID()`; a non-UUID gets a 403 outright -- a made-up
# "sess-batch-0001" won't pass here.
SESSION_PUBLIC_ID = "7f1d4f7e-0000-4000-8000-000000000501"
OTHER_SESSION_PUBLIC_ID = "7f1d4f7e-0000-4000-8000-000000000502"


def _seed_session(db, session_id: int = 501, *, user_id: int = USER, public_id: str = SESSION_PUBLIC_ID):
    db.add(SalesAgentChatSessionORM(id=session_id, public_id=public_id, user_id=user_id))
    db.commit()
    return session_id


def _seed_batch(db, campaign_id: int, names: list[str], *, session_id: int | None = None,
                minutes_ago: int = 0, with_contact: bool = True, status: str = "New") -> list[int]:
    """One recommendation batch: a campaign + its leads. `with_contact=False` simulates "the ones that can't be sent to"."""
    if db.get(SalesAgentRecommendationCampaignORM, campaign_id) is None:
        db.add(SalesAgentRecommendationCampaignORM(
            id=campaign_id, user_id=USER, session_id=session_id, status="completed",
            created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
        ))
    lead_ids = []
    start = db.query(SalesAgentCompanyORM).count() + 1
    for offset, name in enumerate(names):
        index = start + offset
        db.add_all([
            SalesAgentCompanyORM(id=index, name=name, source="manual", data_status="active"),
            SalesAgentLeadRecommendationORM(id=index, user_id=USER, company_id=index,
                                            campaign_id=campaign_id, status=status),
        ])
        if with_contact:
            db.add(SalesAgentCompanyContactORM(company_id=index, email=f"c{index}@example.com",
                                               consent_status="unknown", is_reachable=True))
        lead_ids.append(index)
    db.commit()
    return lead_ids


def test_create_resolves_the_batch_server_side(client, db):
    """A schedule can be created from just a batch id -- the list is queried server-side, not taken from the frontend's snapshot."""
    lead_ids = _seed_batch(db, 31, ["甲科技", "乙科技"])
    resp = client.post(BASE, json={"lead_ids": [], "campaign_ids": [31], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    items = client.get(f"{BASE}/{resp.json()['schedule']['id']}").json()["items"]
    assert sorted(row["lead_id"] for row in items) == sorted(lead_ids)


def test_create_from_a_batch_skips_withdrawn_leads(client, db):
    """A company the user withdrew is not rescheduled, even if it's still in the frontend's list."""
    kept = _seed_batch(db, 31, ["甲科技"])
    _seed_batch(db, 31, ["撤回掉的"], status="Undone")
    resp = client.post(BASE, json={"lead_ids": [], "campaign_ids": [31], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    items = client.get(f"{BASE}/{resp.json()['schedule']['id']}").json()["items"]
    assert [row["lead_id"] for row in items] == kept


def test_explicit_lead_ids_still_win_over_campaign_ids(client, db):
    """The old entry (manual panel selection) passing lead_ids behaves the same -- the batch is only the source when it's empty."""
    _seed_batch(db, 31, ["甲科技", "乙科技"])
    picked = _seed_leads(db, ["手选的"])
    resp = client.post(BASE, json={"lead_ids": picked, "campaign_ids": [31], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    items = client.get(f"{BASE}/{resp.json()['schedule']['id']}").json()["items"]
    assert [row["lead_id"] for row in items] == picked


def test_confirm_true_arms_in_the_same_request(client, db):
    """One click on "OK" in the dialog should schedule it -- no leftover draft needing another confirmation."""
    _seed_batch(db, 31, ["甲科技", "乙科技"])
    resp = client.post(BASE, json={
        "lead_ids": [], "campaign_ids": [31],
        "scheduled_at": _local_iso(_tomorrow_local(15)), "confirm": True,
    })

    assert resp.status_code == 200, resp.text
    schedule_id = resp.json()["schedule"]["id"]
    assert db.get(SalesAgentSendScheduleORM, schedule_id).status == "armed"


def test_without_confirm_it_still_stops_at_draft(client, db):
    """Callers that don't pass confirm (the old panel) behave the same and still stop at pending confirmation."""
    _seed_batch(db, 31, ["甲科技"])
    resp = client.post(BASE, json={"lead_ids": [], "campaign_ids": [31], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    assert db.get(SalesAgentSendScheduleORM, resp.json()["schedule"]["id"]).status == "draft"


def test_past_time_with_confirm_leaves_nothing_behind(client, db):
    """Past time + one click -> 400, **and no schedule left in the DB**.

    `arm` already hard-rejects past times. Letting it create first and reject after, the user sees "clicked OK and then got an error",
    and the DB has an extra draft that can never be armed hanging in the list.
    """
    _seed_batch(db, 31, ["甲科技"])
    resp = client.post(BASE, json={
        "lead_ids": [], "campaign_ids": [31],
        "scheduled_at": _local_iso(datetime.utcnow() - timedelta(hours=2)), "confirm": True,
    })

    assert resp.status_code == 400
    assert "已经过去了" in resp.json()["detail"]
    assert db.query(SalesAgentSendScheduleORM).count() == 0
    assert db.query(SalesAgentSendScheduleItemORM).count() == 0


# ---------------------------------------------------------------- estimate


PREVIEW = "/api/sales-agent/outreach/schedules/preview"
BATCHES = "/api/sales-agent/outreach/recommendation-batches"


def test_preview_reports_counts_without_writing_anything(client, db):
    """Source of the dialog's "will send to 2 · 1 can't be sent". Not a single row may be persisted to DB."""
    _seed_batch(db, 31, ["甲科技", "乙科技"])
    _seed_batch(db, 31, ["没邮箱的"], with_contact=False)
    resp = client.post(PREVIEW, json={"campaign_ids": [31], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["total"], body["sendable_count"], body["blocked_count"]) == (3, 2, 1)
    assert not body["time_warning"]
    assert db.query(SalesAgentSendScheduleORM).count() == 0


def test_preview_counts_match_what_gets_created(client, db):
    """Estimate and real scheduling must share a source -- dialog says 2, creation yields 3 is the hardest kind of bug to notice."""
    _seed_batch(db, 31, ["甲科技", "乙科技"])
    _seed_batch(db, 31, ["没邮箱的"], with_contact=False)
    body = {"lead_ids": [], "campaign_ids": [31], "scheduled_at": _local_iso(_tomorrow_local(15))}
    preview = client.post(PREVIEW, json=body).json()
    created = client.post(BASE, json=body).json()

    assert preview["sendable_count"] == len(created["included"])
    assert preview["blocked_count"] == len(created["skipped"])


def test_preview_warns_about_a_past_time_instead_of_failing(client, db):
    """A time already past is only a warning -- the user is still in the dialog and should be able to fix the time from the hint, not eat an error."""
    _seed_batch(db, 31, ["甲科技"])
    resp = client.post(PREVIEW, json={
        "campaign_ids": [31], "scheduled_at": _local_iso(datetime.utcnow() - timedelta(hours=2)),
    })

    assert resp.status_code == 200, resp.text
    assert "已经过去了" in resp.json()["time_warning"]


def test_preview_of_someone_elses_batch_counts_nothing(client, db):
    """Unauthorized count lookups aren't allowed either -- batch ids are sequential and guessable."""
    db.add(SalesAgentRecommendationCampaignORM(id=99, user_id=OTHER_USER, status="completed"))
    db.add(SalesAgentCompanyORM(id=900, name="别人的", source="manual", data_status="active"))
    db.add(SalesAgentLeadRecommendationORM(id=900, user_id=OTHER_USER, company_id=900, campaign_id=99))
    db.commit()
    resp = client.post(PREVIEW, json={"campaign_ids": [99], "when_text": "明天下午三点"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------- batch list


def test_batches_endpoint_numbers_them_oldest_first(client, db):
    """Options in the dialog. "Batch 1" is the earliest batch; the latest is checked by default."""
    _seed_session(db)
    _seed_batch(db, 31, ["甲科技", "乙科技"], session_id=501, minutes_ago=30)
    _seed_batch(db, 32, ["丙科技"], session_id=501, minutes_ago=5)
    resp = client.get(BATCHES, params={"session_id": SESSION_PUBLIC_ID})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(b["index"], b["campaign_id"], b["lead_count"]) for b in body["batches"]] == [
        (1, 31, 2), (2, 32, 1),
    ]
    assert body["default_campaign_id"] == 32


def test_batches_never_include_another_users_session(client, db):
    """Batches recommended in someone else's session must not appear in my "which batch to send"."""
    _seed_session(db, 502, user_id=OTHER_USER, public_id=OTHER_SESSION_PUBLIC_ID)
    _seed_batch(db, 33, ["别人会话里的"], session_id=502)
    resp = client.get(BATCHES, params={"session_id": OTHER_SESSION_PUBLIC_ID})

    # `_find_user_session` returns None for sessions not owned by the user (no error, and no confirmation of existence),
    # so this degrades to "no session". What must hold is **that batch must not appear among the options** --
    # if it can be selected, it can be sent.
    assert resp.status_code == 200, resp.text
    assert 33 not in [b["campaign_id"] for b in resp.json()["batches"]]
