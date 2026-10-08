"""**Functional-level** self-tests for the same batch of feedback -- real sqlite, real tool function calls, real DB reads and writes.

The `test_qa_2026_09_03.py` above locks mechanisms (signatures, constants, loading); this one locks
"what the DB actually looks like after running". Both are needed: the former guards against refactor drift, the latter against "renamed correctly
but never took effect".

Run:
    venv/bin/python -m pytest tests/geo_agent/test_qa_2026_09_03_live.py -q
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from geo.database import Base
from geo.models.ai_telemetry import AiTelemetryTopicORM, GeoRetestProbeORM
from geo.models.balance import (
    BalanceLedgerORM, BalancePriceORM, KIND_RETEST, UserBalanceORM,
)
from geo.models.user import UserORM
from geo.services import balance_service


@pytest.fixture()
def ctx():
    # Create **all** tables: the schedule endpoint goes through access_control (needs organization_grants);
    # picking tables means replicating the dependency graph in the test, which turns red with every extra layer of indirection.
    # check_same_thread=False + StaticPool: the code under test uses asyncio.to_thread / background threads,
    # and the default sqlite connection refuses with "not the same thread" -- a test environment limitation, not product behavior.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=1, email="u@x.z", password_hash="x", role="user"))
    db.commit()
    balance_service.get_or_create_account(db, 1)
    balance_service.credit(db, user_id=1, kind="recharge", amount_cents=100000,
                           idem_key="topup")
    db.commit()
    deps = SimpleNamespace(db=db, user_id=1, account_id=1, topic_id=None,
                           channel="web", caps=["read", "write"], tid="",
                           tenant_id=None, policy_key="", role="user", scope="self",
                           turn_id="t1", locked_topic_id=None)
    yield SimpleNamespace(deps=deps)
    db.close()


@pytest.fixture(autouse=True)
def _no_policy(monkeypatch):
    monkeypatch.setattr("agents.geo.methods.usage_guardrail_check", lambda d, a: None)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ── Official website ────────────────────────────────────────────────────────────────
def test_create_topic_puts_the_url_in_website_not_in_target(ctx):
    """Creating a topic with a website: target must stay the brand name, the URL goes into profile.website.

    It used to be `target=url` -- the search term became a URL, and every batch run asked AI
    "how is https://vigilath.com".
    """
    from agents.geo.tools import create_topic

    info = _run(create_topic(ctx, name="威吉力", url="vigilath.com", industry="GEO"))
    assert info.website == "https://vigilath.com"
    assert info.target == "威吉力"

    row = ctx.deps.db.get(AiTelemetryTopicORM, info.topic_id)
    assert row.target == "威吉力"
    assert json.loads(row.profile_json)["website"] == "https://vigilath.com"


def test_get_topic_reports_empty_website_instead_of_the_short_name(ctx):
    from agents.geo.tools import get_topic

    ctx.deps.db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力"))
    ctx.deps.db.commit()
    ctx.deps.topic_id = 7

    info = _run(get_topic(ctx))
    assert info.website == ""            # not filled in means not filled in
    assert info.target == "威吉力"        # short name as before, just no longer called "official website"


def test_agent_can_actually_set_the_website(ctx):
    """Feedback: "the agent said it could modify it automatically, but in reality it still had to be changed manually"."""
    from agents.geo.tools import set_topic_website

    ctx.deps.db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力"))
    ctx.deps.db.commit()
    ctx.deps.topic_id = 7

    info = _run(set_topic_website(ctx, url="https://www.vigilath.com/"))
    assert info.website == "https://www.vigilath.com/"
    row = ctx.deps.db.get(AiTelemetryTopicORM, 7)
    assert json.loads(row.profile_json)["website"] == "https://www.vigilath.com/"


def test_set_website_rejects_garbage(ctx):
    from pydantic_ai import ModelRetry

    from agents.geo.tools import set_topic_website

    ctx.deps.db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力"))
    ctx.deps.db.commit()
    ctx.deps.topic_id = 7
    with pytest.raises(ModelRetry):
        _run(set_topic_website(ctx, url="这不是网址"))


# ── Money-spending actions don't guess the topic ────────────────────────────────────────────────────
def test_draft_articles_asks_which_topic_when_there_are_several(ctx):
    from agents.geo.tools import draft_articles

    ctx.deps.db.add_all([
        AiTelemetryTopicORM(id=1, user_id=1, name="品牌甲", target="品牌甲",
                            queries_json='[{"text":"a","selected":true}]'),
        AiTelemetryTopicORM(id=2, user_id=1, name="品牌乙", target="品牌乙",
                            queries_json='[{"text":"b","selected":true}]'),
    ])
    ctx.deps.db.commit()

    out = _run(draft_articles(ctx, max_docs=1))
    assert out["status"] == "need_input"
    assert out["field"] == "topic_id"
    assert {c["名称"] for c in out["候选"]} == {"品牌甲", "品牌乙"}


def test_draft_articles_uses_the_topic_it_was_given(ctx, monkeypatch):
    from agents.geo import tools

    ctx.deps.db.add_all([
        AiTelemetryTopicORM(id=1, user_id=1, name="品牌甲", target="品牌甲",
                            queries_json='[{"text":"a","selected":true}]'),
        AiTelemetryTopicORM(id=2, user_id=1, name="品牌乙", target="品牌乙",
                            queries_json='[{"text":"b","selected":true}]'),
    ])
    ctx.deps.db.commit()

    out = _run(tools.draft_articles(ctx, max_docs=1, topic_id=2))
    assert out.get("needs_confirm") is True          # reached two-step confirmation
    assert out["preview"]["主题"] == "品牌乙"


# ── Re-test billing ────────────────────────────────────────────────────────────
def _probe(db, pid, status):
    db.add(GeoRetestProbeORM(id=pid, topic_id=9, doc_id=pid, status=status,
                             scheduled_for=datetime.utcnow() - timedelta(days=1),
                             created_at=datetime.utcnow() - timedelta(days=2)))


@pytest.fixture()
def billing_on(monkeypatch):
    """Enable re-test billing: push the start date very early so the "no back-charging" gate doesn't take part in these assertions."""
    monkeypatch.setenv("GEO_RETEST_BILLING_START_DATE", "2000-01-01")


def test_only_probes_that_ran_get_charged(ctx, monkeypatch, billing_on):
    """Feedback: "credits were deducted without a real re-test being done".

    When due, telemetry marks probes of disabled topics cancelled -- that run shouldn't be charged a cent.
    """
    from geo.services import retest_billing

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=9, user_id=1, name="品牌", target="品牌"))
    _probe(db, 1, "done")
    _probe(db, 2, "enqueued")
    _probe(db, 3, "cancelled")
    _probe(db, 4, "scheduled")
    db.commit()
    monkeypatch.setattr(balance_service, "get_price", lambda *_a, **_k: 50)

    stat = retest_billing.settle_probes(db)
    assert stat["charged"] == 2, stat

    charged = {r.ref_id for r in db.query(BalanceLedgerORM)
               .filter(BalanceLedgerORM.kind == KIND_RETEST).all()}
    assert charged == {"1", "2"}


def test_retest_settlement_is_idempotent(ctx, monkeypatch, billing_on):
    from geo.services import retest_billing

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=9, user_id=1, name="品牌", target="品牌"))
    _probe(db, 1, "done")
    db.commit()
    monkeypatch.setattr(balance_service, "get_price", lambda *_a, **_k: 50)

    retest_billing.settle_probes(db)
    before = balance_service.get_balance(db, 1)
    retest_billing.settle_probes(db)
    assert balance_service.get_balance(db, 1) == before


# ── Monitoring cadence switch ────────────────────────────────────────────────────────
def test_agent_turns_monitoring_on_and_the_row_actually_changes(ctx):
    """Feedback: "enabling monitoring requires turning on a switch on the web first, but the switch entry can't be found"."""
    from agents.geo.tools_monitor import set_monitor_schedule

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力",
                               schedule_tier="off", enabled=False))
    db.commit()
    ctx.deps.topic_id = 7

    out = _run(set_monitor_schedule(ctx, cadence="daily"))
    assert out["已修改"] is True
    row = db.get(AiTelemetryTopicORM, 7)
    assert row.schedule_tier == "daily"
    assert row.enabled is True           # enabled is a derived cache of tier and must stay in sync

    _run(set_monitor_schedule(ctx, cadence="off"))
    row = db.get(AiTelemetryTopicORM, 7)
    assert row.schedule_tier == "off" and row.enabled is False


def test_pause_and_resume(ctx):
    from agents.geo.tools_monitor import set_monitor_schedule

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力",
                               schedule_tier="weekly", enabled=True))
    db.commit()
    ctx.deps.topic_id = 7

    _run(set_monitor_schedule(ctx, pause_days=7))
    row = db.get(AiTelemetryTopicORM, 7)
    assert row.paused_until is not None
    assert row.schedule_tier == "weekly"     # pausing doesn't lose config

    _run(set_monitor_schedule(ctx, resume=True))
    assert db.get(AiTelemetryTopicORM, 7).paused_until is None


def test_trigger_batch_run_says_what_is_missing_instead_of_inventing_a_button(ctx):
    from agents.geo.tools_monitor import trigger_batch_run

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力",
                               queries_json="[]", engines_json='["deepseek"]'))
    db.commit()
    ctx.deps.topic_id = 7

    out = _run(trigger_batch_run(ctx))
    assert out["ok"] is False
    assert out["缺什么"] and "提示词" in out["缺什么"][0]


# ── Rescheduling failed drafts ──────────────────────────────────────────────────────────
def test_retry_failed_articles_previews_the_real_count(ctx, monkeypatch):
    """Feedback: "generation failed: the client can't regenerate" -- the customer side previously had no entry to try again."""
    from datetime import datetime as _dt

    from agents.geo import tools
    from geo.models.ai_telemetry import TopicGeneratedDocORM

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力"))
    for i in (1, 2):
        db.add(TopicGeneratedDocORM(
            id=i, topic_id=7, source_query_text=f"t{i}", title="[生成失败] x",
            status="pending_review", generation_error="failed to parse JSON",
            gen_next_retry_at=None, created_at=_dt.utcnow()))
    db.commit()
    ctx.deps.topic_id = 7
    monkeypatch.setattr("geo.services.gen_retry.SessionLocal", lambda: db)

    out = _run(tools.retry_failed_articles(ctx))
    assert out.get("needs_confirm") is True
    assert out["preview"]["篇数"] == 2


def test_retry_says_so_when_there_is_nothing_stuck(ctx, monkeypatch):
    from agents.geo import tools

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=7, user_id=1, name="威吉力", target="威吉力"))
    db.commit()
    ctx.deps.topic_id = 7
    monkeypatch.setattr("geo.services.gen_retry.SessionLocal", lambda: db)

    out = _run(tools.retry_failed_articles(ctx))
    assert out["ok"] is False and "没有卡住的失败稿" in out["note"]


# ── Safety valve for re-test post-pay: no back-charging ────────────────────────────────────────────
def test_no_retro_charge_without_a_start_date(ctx, monkeypatch):
    """At the moment of switching to post-paid, the DB holds a batch of existing probes "that should have been charged at registration but weren't charged a cent".

    Measured before the test host launch: 187 done probes within 30 days, not one with a ledger entry. Without this gate,
    the first tick after switching would back-charge them all at once -- and users never agreed to a billing start date.
    """
    from geo.services import retest_billing

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=9, user_id=1, name="品牌", target="品牌"))
    _probe(db, 1, "done")
    db.commit()
    monkeypatch.setattr(balance_service, "get_price", lambda *_a, **_k: 50)
    monkeypatch.delenv("GEO_RETEST_BILLING_START_DATE", raising=False)

    before = balance_service.get_balance(db, 1)
    stat = retest_billing.settle_probes(db)
    assert stat["charged"] == 0 and stat["before_start"] == 1
    assert balance_service.get_balance(db, 1) == before


def test_probes_before_the_start_date_are_never_charged(ctx, monkeypatch):
    from geo.services import retest_billing

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=9, user_id=1, name="品牌", target="品牌"))
    _probe(db, 1, "done")            # created_at = 2 days ago
    db.commit()
    monkeypatch.setattr(balance_service, "get_price", lambda *_a, **_k: 50)
    # Start date set to today -> the one registered two days ago isn't back-charged
    monkeypatch.setenv("GEO_RETEST_BILLING_START_DATE",
                       retest_billing._bj_date(datetime.utcnow()))

    stat = retest_billing.settle_probes(db)
    assert stat["charged"] == 0 and stat["before_start"] == 1


def test_probes_after_the_start_date_are_charged(ctx, monkeypatch):
    from geo.services import retest_billing

    db = ctx.deps.db
    db.add(AiTelemetryTopicORM(id=9, user_id=1, name="品牌", target="品牌"))
    db.add(GeoRetestProbeORM(id=1, topic_id=9, doc_id=1, status="done",
                             scheduled_for=datetime.utcnow(),
                             created_at=datetime.utcnow()))
    db.commit()
    monkeypatch.setattr(balance_service, "get_price", lambda *_a, **_k: 50)
    monkeypatch.setenv("GEO_RETEST_BILLING_START_DATE",
                       retest_billing._bj_date(datetime.utcnow() - timedelta(days=1)))

    stat = retest_billing.settle_probes(db)
    assert stat["charged"] == 1, stat
