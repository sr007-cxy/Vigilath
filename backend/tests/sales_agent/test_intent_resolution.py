"""Intent resolution: the Router Agent after the Rule Guard does structured routing only.

The Router is not a business executor: no DB queries, no cards, no tool calls. It only narrows user input into a fine-grained intent
when the keyword result is uncertain; timeouts/exceptions/invalid returns all fall back to the rule result and must not block the turn.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_intent_resolution.py -q
"""
from __future__ import annotations

import asyncio

import pytest

from agents.sales_agent.flow.intent import (
    INTENTS,
    READ_ONLY_INTENTS,
    ROUTER_INTENTS,
    coarse_intent_for,
    intent_is_read_only,
    intent_needs_business_flow,
    needs_model_classification,
    resolve_intent,
)


class _Decision:
    def __init__(self, intent, read_only=True, confidence=0.7):
        self.intent = intent
        self.read_only = read_only
        self.confidence = confidence


def _run(message, keyword, classify=None):
    # Don't use get_event_loop(): on Python 3.12, once asyncio.run has been called elsewhere this raises
    # "There is no current event loop" -- green when run alone, failing in the full suite.
    return asyncio.run(resolve_intent(message, keyword, classify))


async def _says(intent, confidence=0.7):
    return _Decision(intent, confidence=confidence)


# --- Router taxonomy ---


def test_router_intents_are_supported():
    assert set(ROUTER_INTENTS).issubset(set(INTENTS))


@pytest.mark.parametrize("intent,expected", [
    ("lead_recommendation", "lead"),
    ("delivery_lookup", "lookup"),
    ("recommendation_lookup", "lookup"),
    ("seller_profile_lookup", "lookup"),
    ("seller_profile_update", "profile"),
    ("report_detection", "report"),
    ("outreach_draft", "outreach"),
    ("unclear", "chat"),
    ("faq", "faq"),
    ("chat", "chat"),
])
def test_fine_intents_map_to_existing_flow_scenes(intent, expected):
    assert coarse_intent_for(intent) == expected


# --- When to ask the model ---


@pytest.mark.parametrize("kw", ["lead", "chat", "lead_recommendation", "outreach", "outreach_draft"])
def test_ambiguous_keywords_go_to_model(kw):
    """lead's word list is too broad and chat is the fallback; neither counts.

    `outreach` / `outreach_draft` were added on 2026-09-13 (scheduled send design (2)): 「三点把这批发一下」 ("send this batch at three")
    and 「帮我改改措辞」 ("help me reword this") both look like outreach to keywords; the difference is
    **one actually sends mail**. Rule Guard (1) only takes high-confidence phrases; the rest is left to the Router Agent.

    This and `_AMBIGUOUS` are two sides of the same thing: set (2)'s `final` to False without adding
    outreach_draft to `_AMBIGUOUS`, and `resolve_intent` returns the keyword result unchanged --
    the Router is never asked once, and every case stays green.
    """
    assert needs_model_classification(kw)


@pytest.mark.parametrize("kw", [
    "lookup", "faq", "compliance", "profile", "report",
    "delivery_lookup", "recommendation_lookup", "seller_profile_lookup",
    "seller_profile_update", "report_detection", "unclear",
])
def test_specific_keywords_skip_the_model(kw):
    """Specific categories are used directly, saving a call."""
    assert not needs_model_classification(kw)
    assert _run("随便什么", kw)[1] == "keyword"


# --- Model overrides ---


def test_model_can_correct_a_lead_misroute():
    """The production message: keywords said lead, the model saw it was asking about history."""
    intent, source = _run("现在搜索出来过多少企业？", "lead", lambda t: _says("recommendation_lookup"))
    assert (intent, source) == ("recommendation_lookup", "model")


def test_model_can_keep_a_real_lead_request():
    assert _run("帮我找客户", "lead", lambda t: _says("lead_recommendation")) == ("lead_recommendation", "model")


# --- (2b) post-check: the model says send, but no time can be extracted from the sentence ---


@pytest.mark.parametrize("message", [
    "给这家写封邮件",
    "改一下刚才那封",
    "帮我把措辞改得客气点",
    "邮件写好了",
])
def test_send_schedule_without_a_time_is_downgraded(message):
    """The judgment is in the model's hands and out of our control -- so a hard gate that only moves toward safety follows it.

    Regression cases only prove "these sentences are right now", not "a different phrasing next time won't be misjudged",
    and the cost of a misjudgment here is actually sending a batch of mail, not a wrong reply.
    """
    assert _run(message, "outreach_draft", lambda t: _says("send_schedule_create")) == (
        "outreach_draft", "post_check_downgrade",
    )


@pytest.mark.parametrize("message", [
    "下午三点把这批发一下",
    "明早九点发",
    "一小时后发给他们",
    "晚点发吧",  # a vague time still counts as "has time" -- ask what time, not downgrade to a draft
])
def test_send_schedule_with_a_time_survives(message):
    assert _run(message, "outreach_draft", lambda t: _says("send_schedule_create")) == (
        "send_schedule_create", "model",
    )


def test_post_check_only_ever_downgrades():
    """Downgrade only, never upgrade: a missed detection costs the user one more sentence, a false one costs sent mail; not symmetric."""
    assert _run("下午三点把这批发一下", "outreach_draft", lambda t: _says("outreach_draft")) == (
        "outreach_draft", "model",
    )


def test_send_schedule_lookup_is_never_downgraded():
    """Lookup and cancel are read-only; no time word shouldn't touch them -- 「别发了」 ("don't send") has no time anyway."""
    assert _run("别发了", "outreach_draft", lambda t: _says("send_schedule_lookup")) == (
        "send_schedule_lookup", "model",
    )


@pytest.mark.parametrize("alias,expected", [
    ("schedule_send", "send_schedule_create"),
    ("send_schedule", "send_schedule_create"),
    ("scheduled_send", "send_schedule_create"),
    ("schedule_lookup", "send_schedule_lookup"),
    ("send_schedule_status", "send_schedule_lookup"),
])
def test_model_shorthand_intents_are_normalized(alias, expected):
    """Models love abbreviations. If not normalized, it falls into the `picked not in INTENTS` keyword_fallback --
    showing up as "the model judged right but it had no effect", with nothing in the logs showing where it was lost."""
    intent, source = _run("下午三点把这批发一下", "outreach_draft", lambda t: _says(alias))
    assert intent == expected
    assert source == "model"


# --- Intent resolution must not block the turn ---


def test_model_failure_falls_back_to_keyword():
    async def boom(_):
        raise RuntimeError("provider 挂了")
    assert _run("随便什么", "lead", boom) == ("lead", "keyword_fallback")


def test_model_timeout_falls_back(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_INTENT_TIMEOUT", "1")

    async def slow(_):
        await asyncio.sleep(3)
        return _Decision("lookup")

    assert _run("随便什么", "lead", slow) == ("lead", "keyword_fallback")


def test_garbage_intent_falls_back():
    """When the model returns a value outside the set, it must not be used."""
    assert _run("随便什么", "lead", lambda t: _says("我不知道")) == ("lead", "keyword_fallback")


def test_low_confidence_intent_falls_back():
    assert _run("随便什么", "lead", lambda t: _says("delivery_lookup", confidence=0.2)) == ("lead", "keyword_fallback")


def test_no_classifier_means_keyword():
    assert _run("随便什么", "lead", None) == ("lead", "keyword")


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_INTENT_LLM", "0")
    assert _run("随便什么", "lead", lambda t: _says("lookup")) == ("lead", "keyword")


# --- Read/write split ---


@pytest.mark.parametrize("intent,expected", [
    ("lookup", True),
    ("faq", True),
    ("chat", True),
    ("seller_profile_lookup", True),
    ("delivery_lookup", True),
    ("recommendation_lookup", True),
    ("unclear", True),
    ("lead", False),
    ("lead_recommendation", False),
    ("profile", False),
    ("seller_profile_update", False),
    ("report", False),
    ("report_detection", False),
    ("outreach", False),
    ("outreach_draft", False),
    ("compliance", False),
])
def test_read_only_classification(intent, expected):
    """A query is cheap, a run is expensive; this property is part of the decision, not something to leave to word-list luck."""
    assert intent_is_read_only(intent) is expected
    assert (intent in READ_ONLY_INTENTS) is expected


@pytest.mark.parametrize("intent,expected", [
    ("chat", False),
    ("faq", False),
    ("lookup", True),
    ("seller_profile_lookup", True),
    ("delivery_lookup", True),
    ("recommendation_lookup", True),
    ("lead_recommendation", True),
    ("seller_profile_update", True),
    ("report_detection", True),
    ("outreach_draft", True),
    ("compliance", True),
    ("unclear", True),
])
def test_business_flow_classification(intent, expected):
    assert intent_needs_business_flow(intent) is expected
