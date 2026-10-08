"""Scheduled send: once intent is judged right, the flow must also go right.

These are two separate things. Adding the intent branch on 2026-09-13 hit exactly this:
`_rule_guard_intent` already classified 「下午三点把这批邮件发一下」 ("send this batch of emails at 3 p.m.") as `send_schedule_create`,
but in the deterministic flow `_explicit_stats_domain()` saw 「邮件」 ("email") in the sentence, classified it as `"delivery"`,
and intercepted it a second time, returning a send-volume stats card -- the intent was right, what the user got was still wrong.

This file watches three things:

1. `_run_deterministic_flow` **yields** (returns False) for the two send_schedule intents,
   instead of being grabbed by the delivery / recommendation query branches below;
2. After yielding, the model really has those five tools -- a tool written but not registered in `TOOLS` shows up as
   "no error, feature doesn't exist", with every test case still green;
3. When mass sending is refused, a usable phrasing is offered (design 4.7 boundary table), not just a block without guidance.

Run:
    ../agent-venv/bin/python -m pytest tests/sales_agent/test_send_schedule_intent.py -q
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.api import _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
    SalesAgentCompanyORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentRecommendationCampaignORM,
)
from agents.sales_agent.time_parse import describe_utc, parse_when_text, to_local
from geo.database import Base
from geo.models.user import UserORM

USER = 3300777


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="send-schedule@example.invalid", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="send-1", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id="send-1")
    try:
        yield d
    finally:
        db.close()


def _run(deps, message: str, intent: str) -> bool:
    return asyncio.run(_run_deterministic_flow(deps, message, intent))


def _replies(deps) -> list[str]:
    return [
        m.content for m in deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant").all()
    ]


# --- (3) The deterministic flow must yield ---


@pytest.mark.parametrize("message", [
    "下午三点把这批邮件发一下",     # contains 「邮件」 ("email") -- used to be classified as delivery by _explicit_stats_domain
    "明早九点发",
    "定时把这批发了",
])
def test_send_requests_are_handed_to_the_model(deps, message):
    """Scheduling must first compute how many can be sent, explain each skip reason, echo the start and finish times, then wait for confirmation.

    All of that lives in the tool and can't be assembled by the deterministic branch -- so here it only yields, without replying itself.

    Note this fixture's session has **no recommendation batch at all**. With batches, "this batch" needs no guessing
    and goes to the batch selection card below instead of yielding.
    """
    assert _run(deps, message, "send_schedule_create") is False
    assert _replies(deps) == [], "让路的分支不该自己写一条回复"


@pytest.mark.parametrize("message", ["三点那批发了吗", "我有哪些定时任务", "别发了"])
def test_schedule_lookups_are_handed_to_the_model(deps, message):
    assert _run(deps, message, "send_schedule_lookup") is False
    assert _replies(deps) == []


def test_delivery_lookup_is_not_affected(deps):
    """Yielding only applies to the two send_schedule intents; sent stats still take the deterministic branch."""
    assert _run(deps, "今天发了几封邮件", "delivery_lookup") is True
    assert _replies(deps), "delivery_lookup 仍然由确定性流程回答"


# --- Mass sending: a refusal must offer a way out ---


def test_mass_send_refusal_offers_the_scheduled_alternative(deps):
    """Blocking without guidance traps the user on a word they don't know how to get around."""
    assert _run(deps, "帮我把这批群发一下", "compliance") is True
    reply = _replies(deps)[-1]
    assert "群发我不能做" in reply
    assert "逐封" in reply and "间隔" in reply, "要给出可用说法,不是只说不行"
    assert "已发送" not in reply


def test_other_compliance_refusals_keep_the_old_reply(deps):
    """「帮我爬手机号」 ("scrape phone numbers for me") is not the same as mass sending; don't push scheduling at it as a universal way out."""
    assert _run(deps, "能不能帮我爬手机号", "compliance") is True
    reply = _replies(deps)[-1]
    assert "群发我不能做" not in reply
    assert "不能帮你执行" in reply


# --- The tools are really registered ---


SEND_TOOLS = (
    "schedule_outreach_send",
    "confirm_scheduled_send",
    "reschedule_send",
    "cancel_scheduled_send",
    "get_send_schedule_status",
)


def test_send_schedule_tools_are_registered():
    """Written but not added to `TOOLS` shows up as: no error, no log, feature doesn't exist.

    It really happened on 2026-09-13 -- all five tools were written and none were in `TOOLS`;
    syntax fine, imports fine, whole test suite green, and the model couldn't see them at all.
    """
    from agents.sales_agent.tools import TOOLS

    names = {getattr(fn, "__name__", "") for fn in TOOLS}
    missing = [name for name in SEND_TOOLS if name not in names]
    assert not missing, f"这几个工具模型看不见:{missing}"


@pytest.mark.parametrize("name", SEND_TOOLS)
def test_tool_schemas_build(name):
    """pydantic-ai generates the JSON schema from function signatures at the moment the Agent is built.

    That means a broken type annotation **only blows up at runtime** and otherwise lies dormant -- so build one explicitly here.
    """
    from pydantic_ai import Tool

    import agents.sales_agent.tools as tools_module

    schema = Tool(getattr(tools_module, name)).function_schema.json_schema
    assert schema["type"] == "object"
    assert "properties" in schema


def test_schedule_outreach_send_requires_only_when_text():
    """`when_text` must be the only required parameter: all others have defaults, so the model can schedule in one sentence.

    Conversely, once `when_text` becomes optional, the model can schedule without a time -- scheduled and immediate send
    are two different authorizations, and this signature itself is that gate.
    """
    from pydantic_ai import Tool

    from agents.sales_agent.tools import schedule_outreach_send

    schema = Tool(schedule_outreach_send).function_schema.json_schema
    assert schema.get("required") == ["when_text"]


# --- "This batch": deterministically pop the batch selection card ---
#
# The yield above assumes only the model can guess which batch "this batch" means. With recommendation batches in the session there's no guessing --
# batch ids are in the DB. This section guards: with batches, emit the card ourselves without entering the model.
#
# Below we always say "tomorrow 3 p.m." rather than "3 p.m.": for a time already past `parse_when_text`
# returns `is_past` (no silent rollover to tomorrow; ask instead), and the branch yields as before -- writing cases with "3 p.m."
# would turn the whole set red when run after 15:00.


def _seed_batch(deps, campaign_id: int, names: list[str], *,
                session_id: int | None, minutes_ago: int = 0) -> list[int]:
    db = deps.db
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
                                            campaign_id=campaign_id, status="New"),
        ])
        lead_ids.append(index)
    db.commit()
    return lead_ids


def _last_card(deps) -> dict:
    row = (
        deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.role == "assistant")
        .order_by(SalesAgentChatMessageORM.id.desc()).first()
    )
    cards = json.loads(row.metadata_json or "{}").get("tool_cards") or []
    assert cards, "这条回复没带卡"
    return cards[0]


def test_this_batch_emits_the_picker_without_calling_the_model(deps):
    """When companies were recommended in the session, "email this batch of companies at 3 p.m." must not go to the model to guess the list."""
    _seed_batch(deps, 41, ["甲科技", "乙科技"], session_id=deps.session_id)

    assert _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create") is True
    card = _last_card(deps)
    assert card["tool"] == "send_schedule_batch_picker"
    assert [b["campaign_id"] for b in card["data"]["batches"]] == [41]
    assert card["data"]["default_campaign_id"] == 41


def test_the_picker_lists_every_batch_of_this_session(deps):
    """Two batches recommended give two options, with the latest checked by default -- "this batch" defaults to the one just recommended."""
    _seed_batch(deps, 41, ["甲科技", "乙科技"], session_id=deps.session_id, minutes_ago=30)
    _seed_batch(deps, 42, ["丙科技"], session_id=deps.session_id, minutes_ago=5)

    assert _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create") is True
    data = _last_card(deps)["data"]
    assert [b["campaign_id"] for b in data["batches"]] == [41, 42]
    assert data["default_campaign_id"] == 42
    assert data["total_leads"] == 3


def test_the_picker_carries_no_company_names(deps):
    """The dialog reports only counts. Putting the list in the card means the product decision wasn't implemented, and the card would overflow."""
    _seed_batch(deps, 41, ["甲科技", "乙科技"], session_id=deps.session_id)

    _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create")
    blob = json.dumps(_last_card(deps), ensure_ascii=False)
    assert "甲科技" not in blob and "乙科技" not in blob


def test_the_reply_echoes_the_resolved_moment(deps):
    """"Send **before** 3 p.m." is parsed as 15:00 -- a semantic gap the user must see before they can fix it."""
    _seed_batch(deps, 41, ["甲科技"], session_id=deps.session_id)

    assert _run(deps, "明天下午三点之前给这批企业发送邮件", "send_schedule_create") is True
    when = parse_when_text("明天下午三点之前给这批企业发送邮件")
    assert when.ok and when.utc_at is not None
    assert describe_utc(when.utc_at) in _replies(deps)[-1]


def test_the_picker_hands_back_beijing_time_not_utc(deps):
    """`scheduled_at` is a **bare Beijing-time string** -- the backend's convention reads bare strings as Beijing time.

    Putting UTC here would shift everything by 8 hours when the dialog POSTs it back verbatim, a difference that's not even conspicuous:
    the user's 15:00 would become 23:00.
    """
    _seed_batch(deps, 41, ["甲科技"], session_id=deps.session_id)

    _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create")
    data = _last_card(deps)["data"]
    when = parse_when_text("明天下午三点给这批企业发送邮件")
    assert data["scheduled_at"] == to_local(when.utc_at).strftime("%Y-%m-%dT%H:%M")
    assert data["scheduled_at"].endswith("15:00")


def test_a_late_night_time_is_scheduled_without_complaint(deps):
    """3 a.m. is scheduled as well -- the previously hardcoded 09:00-18:00 send window has been removed entirely,
    and the only remaining time rule is "not earlier than now"."""
    _seed_batch(deps, 41, ["甲科技"], session_id=deps.session_id)

    assert _run(deps, "明天凌晨三点给这批企业发送邮件", "send_schedule_create") is True
    data = _last_card(deps)["data"]
    assert not data["time_warning"]
    assert "03:00" in data["scheduled_at"]


def test_a_session_without_batches_still_goes_to_the_model(deps):
    """Without recommended companies, "this batch" might refer to a named company; that case still goes to the model."""
    assert _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create") is False
    assert _replies(deps) == []


def test_batches_from_another_session_do_not_count(deps):
    """"This batch" is an in-session reference. Batches from a previous session don't count."""
    _seed_batch(deps, 41, ["甲科技"], session_id=deps.session_id + 999)

    assert _run(deps, "明天下午三点给这批企业发送邮件", "send_schedule_create") is False
    assert _replies(deps) == []


def test_a_send_request_without_a_time_still_goes_to_the_model(deps):
    """No time, no scheduling. This needs a follow-up question, whose wording lives in the tool; don't take it over here."""
    _seed_batch(deps, 41, ["甲科技"], session_id=deps.session_id)

    assert _run(deps, "定时把这批发了", "send_schedule_create") is False
    assert _replies(deps) == []
