"""Regression gates for the 2026-09-03 product test feedback -- each assertion corresponds to one item of feedback.

The original feedback is in `docs/07-问题-ISSUE/`. This locks only the **mechanism layer** (no model calls, no network),
milliseconds; any regression turns red immediately.

Run:
    venv/bin/python -m pytest tests/geo_agent/test_qa_2026_09_03.py -q   # cwd = backend/
"""
from __future__ import annotations

import json

import pytest


# ── (1) Official website reading: target is the brand short name; the website is in profile.website ──────────────────
class _FakeTopic:
    def __init__(self, name="", target="", profile=None):
        self.id = 1
        self.name = name
        self.target = target
        self.profile_json = json.dumps(profile or {}, ensure_ascii=False)


def test_website_comes_from_profile_not_target():
    """Feedback: "in the read API, the official website field actually points to 'company/brand short name'"."""
    from geo.services.topic_site import topic_brand_term, topic_website

    t = _FakeTopic(name="威吉力", target="威吉力", profile={"website": "vigilath.com"})
    assert topic_website(t) == "https://vigilath.com"
    assert topic_brand_term(t) == "威吉力"


def test_website_is_empty_when_only_short_name_is_set():
    """When no website is filled in it's **honestly empty** -- never substitute the brand short name as the website."""
    from geo.services.topic_site import topic_website

    assert topic_website(_FakeTopic(name="威吉力", target="威吉力")) == ""


def test_legacy_url_in_target_still_reads_as_website():
    """Old data: create_topic once wrote url into target. Such rows must still yield the website."""
    from geo.services.topic_site import topic_brand_term, topic_website

    t = _FakeTopic(name="威吉力", target="https://vigilath.com")
    assert topic_website(t) == "https://vigilath.com"
    # And as a search term it's useless -- fall back to the topic name; don't ask AI with a URL
    assert topic_brand_term(t) == "威吉力"


def test_set_website_keeps_other_profile_fields():
    from geo.services.topic_site import set_topic_website

    t = _FakeTopic(profile={"company_short_name": "威吉力", "_admin": {"x": 1}})
    set_topic_website(t, "vigilath.com")
    prof = json.loads(t.profile_json)
    assert prof["website"] == "https://vigilath.com"
    assert prof["company_short_name"] == "威吉力"
    assert prof["_admin"] == {"x": 1}          # the admin section must not be clobbered


# ── (2) Credit categories: diagnosis must not be recorded as "monitoring batch run" ──────────────────────────────────
def test_full_diagnosis_has_its_own_ledger_kind():
    """Feedback: "the credit statement has several monitoring batch run charges, but no matching batch run records in task monitoring".

    Real cause: diagnosis charged at the batch_cell unit price but recorded kind=batch; while batch runs themselves were still in shadow mode by default,
    charging nothing -- so the "monitoring batch run" category only contained diagnosis charges.
    """
    import inspect

    from geo.models.balance import DEBIT_KINDS, KIND_BATCH, KIND_FULL_DIAGNOSIS
    from geo.services import full_diagnosis_generator as g

    assert KIND_FULL_DIAGNOSIS != KIND_BATCH
    assert KIND_FULL_DIAGNOSIS in DEBIT_KINDS
    src = inspect.getsource(g._charge_full_diagnosis)
    assert "KIND_FULL_DIAGNOSIS" in src
    assert "kind=KIND_BATCH" not in src


def test_ledger_labels_cover_every_debit_kind():
    """Every charge category needs a Chinese label, otherwise users see English constants in the statement."""
    import inspect

    from geo.models.balance import ALL_KINDS
    from agents.geo import tools_billing

    src = inspect.getsource(tools_billing.list_ledger)
    for kind in ALL_KINDS:
        assert f'"{kind}"' in src, f"list_ledger 的标签表漏了 {kind}"


def test_frontend_and_backend_ledger_labels_agree():
    """The frontend BalanceTab and the agent's label table share one metric definition; changing one requires syncing the other."""
    import pathlib

    from geo.models.balance import ALL_KINDS

    tsx = (pathlib.Path(__file__).resolve().parents[3]
           / "frontend/src/pages/Account/BalanceTab.tsx").read_text(encoding="utf-8")
    for kind in ALL_KINDS:
        assert f"{kind}:" in tsx, f"前端 KIND_LABELS 漏了 {kind}"


def test_diagnosis_confirm_card_no_longer_claims_it_is_free():
    """Feedback: the confirm card said "the diagnosis itself isn't charged in credits", while the generator really charged per cell."""
    import inspect

    from agents.geo import tools

    src = inspect.getsource(tools.run_full_diagnosis)
    assert "诊断本身不按积分计" not in src
    assert "_fd_cost_estimate" in src


def test_diagnosis_402_distinguishes_points_from_tier():
    """Feedback: the second diagnosis run was told "this feature isn't enabled for the account", when actually credits were insufficient."""
    import inspect

    from agents.geo import tools

    src = inspect.getsource(tools.run_full_diagnosis)
    assert "insufficient_points" in src
    assert "required_tier" in src


# ── (3) Citation re-test: no charge if it didn't run ────────────────────────────────────────────
def test_retest_is_not_charged_at_schedule_time():
    """Feedback: "after manually publishing an article and filling in the link... it was deducted automatically, and no real re-test was done".

    When due, telemetry marks probes of disabled topics cancelled, while the money was collected at registration.
    """
    import inspect

    from geo.services import retest_service

    src = inspect.getsource(retest_service.schedule_for_doc)
    assert "topic_billing.charge" not in src, "登记复测时又开始预扣费了"


def test_retest_billing_only_charges_probes_that_actually_ran():
    from geo.services import retest_billing

    assert set(retest_billing.BILLABLE_STATUS) == {"enqueued", "done"}
    assert "cancelled" not in retest_billing.BILLABLE_STATUS
    assert "scheduled" not in retest_billing.BILLABLE_STATUS


def test_retest_billing_reuses_the_old_idem_key():
    """The idempotency key must equal the old pre-charge key -- otherwise probes already charged historically would be charged again."""
    import inspect

    from geo.services import retest_billing

    assert 'f"retest:{p.id}"' in inspect.getsource(retest_billing.settle_probes)


def test_submit_publish_url_discloses_the_retest_fee():
    import inspect

    from agents.geo import tools_publish

    src = inspect.getsource(tools_publish.submit_publish_url)
    assert "probe_fee_cents" in src and "复测" in src


# ── (4) Monitoring switch: the frontend must really have an entry ────────────────────────────────────
def test_self_schedule_endpoint_exists():
    """Feedback: "enabling monitoring requires turning on a switch on the web first, but the switch entry can't be found".

    Previously the only write entry for schedule_tier was PATCH /admin/topics/{id}/schedule (requiring workbench permission).
    """
    from geo.api import ai_telemetry as at

    assert hasattr(at, "patch_own_topic_schedule")
    assert hasattr(at, "SelfSchedulePatch")


def test_agent_can_change_the_schedule_in_conversation():
    from agents.geo.agent import _line_tools

    names = {t.__name__ for t in _line_tools("geo", can_write=True, channel="web")}
    assert "set_monitor_schedule" in names
    assert "set_topic_website" in names


def test_prompt_no_longer_tells_users_to_find_a_switch_on_the_web():
    from agents.geo.agent import SYSTEM_PROMPT

    assert "set_monitor_schedule" in SYSTEM_PROMPT
    assert "set_topic_website" in SYSTEM_PROMPT


def test_frontend_monitor_tab_has_the_switch():
    import pathlib

    tsx = (pathlib.Path(__file__).resolve().parents[3]
           / "frontend/src/pages/User/TopicProfile.tsx").read_text(encoding="utf-8")
    assert "ScheduleCard" in tsx
    assert "patchSchedule" in tsx


# ── (5) Routing of the sentiment monitoring quick check ────────────────────────────────────────────────────
@pytest.mark.parametrize("utterance", [
    "帮我跑个快检",
    "做一次快检看看",
    "有没有投诉",
    "我们的风评怎么样",
])
def test_quick_check_wording_routes_to_sentiment(utterance):
    """Feedback: "the quick check works on workbuddy but not in the web Agent".

    「快检」 ("quick check") is the product's official name, yet it wasn't in the sentiment keyword list -- the sentence had neither sentiment words nor
    GEO words, so it landed on the default geo line, which doesn't load run_sentiment_once.
    """
    from agents.geo.agent import _line_tools, route_line

    line = route_line(utterance)
    assert line in ("sentiment", "both"), f"{utterance!r} 被路由到 {line}"
    names = {t.__name__ for t in _line_tools(line, can_write=True, channel="web")}
    assert "run_sentiment_once" in names


def test_geo_line_can_still_read_a_quick_check_result():
    """It starts on the sentiment line, while "is it done" lands on the geo line -- fetching results must be reachable across lines."""
    from agents.geo.agent import _line_tools

    names = {t.__name__ for t in _line_tools("geo", can_write=True, channel="web")}
    assert "get_sentiment_once" in names


# ── (6) Brand disambiguation for quick checks: don't attribute a whole industry's negatives to one brand ────────────────
def test_quick_check_threads_the_category_all_the_way_down():
    """Feedback: "running a sentiment quick check for GEO Analytics, negative sentiment about the GEO industry was also attributed to GEO Analytics".

    `_account_snapshot` has long supported disambiguation by category (report #99 <brand-a> vs <namesake-company>),
    but the quick-check entry never passed it -- industry_terms was always None.
    """
    import inspect

    from geo.services import sentiment_once

    for fn in (sentiment_once.start, sentiment_once.run_sync,
               sentiment_once.status, sentiment_once.peek):
        assert "industry" in inspect.signature(fn).parameters, fn.__name__
    assert "industry_terms=industry" in inspect.getsource(sentiment_once._worker)


def test_quick_check_tool_takes_industry_terms():
    import inspect

    from agents.geo import tools

    assert "industry_terms" in inspect.signature(tools.run_sentiment_once).parameters


def test_category_changes_the_cache_key():
    """The category must be in the key -- otherwise changing the category still hits the previous wrong result."""
    from geo.services.full_diagnosis_sentiment import cache_key_for

    a = cache_key_for("GEO Analytics", ["GEO Analytics"], 1, None)
    b = cache_key_for("GEO Analytics", ["GEO Analytics"], 1, ["AI搜索优化"])
    assert a != b


# ── (7) Money-spending actions must not guess the topic ────────────────────────────────────────────────
def test_money_tools_take_an_explicit_topic():
    """Feedback: "draft articles can be generated, but for the wrong topic"."""
    import inspect

    from agents.geo import tools, tools_monitor

    assert "topic_id" in inspect.signature(tools.draft_articles).parameters
    for fn in (tools.draft_articles, tools_monitor.trigger_batch_run):
        assert "_explicit_topic" in inspect.getsource(fn), f"{fn.__name__} 还在猜主题"
