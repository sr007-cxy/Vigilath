"""Context budget and trimming.

On the test host the largest session's messages_json is already 131KB, fed back to the model verbatim every turn -- we pay for that volume
and will sooner or later hit the provider input limit. This pins down three things:

1. Under budget, messages are **returned verbatim one by one** (the change must not affect existing behavior);
2. Over budget, they are actually trimmed below the threshold;
3. The summary is built only from structured state in the DB: no conversation text, no model calls.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_context_budget.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.runtime.budget import (
    ContextBudget,
    budget_from_env,
    build_state_digest,
    estimate_chars,
    trim_history,
)


def _msgs(n: int, filler: int = 200):
    """Build n real Pydantic AI messages."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    return [ModelRequest(parts=[UserPromptPart(content="话" * filler)]) for _ in range(n)]


# --- Budget config ---

def test_budget_defaults():
    b = budget_from_env()
    assert b.max_chars == 60_000 and b.keep_recent_turns == 6 and b.enabled


def test_budget_disabled_by_zero(monkeypatch):
    """Disabling the threshold restores the pre-change behavior -- this is the rollback lever and must work."""
    monkeypatch.setenv("SALES_AGENT_CTX_MAX_CHARS", "0")
    assert budget_from_env().enabled is False


@pytest.mark.parametrize("bad", ["abc", ""])
def test_budget_env_garbage_falls_back(monkeypatch, bad):
    monkeypatch.setenv("SALES_AGENT_CTX_MAX_CHARS", bad)
    assert budget_from_env().max_chars == 60_000


# --- Estimation ---

def test_estimate_grows_with_history():
    assert estimate_chars([]) == 0
    assert estimate_chars(_msgs(20)) > estimate_chars(_msgs(2))


# --- Trimming ---

def test_under_budget_returns_identical_object():
    msgs = _msgs(4)
    out, trimmed = trim_history(msgs, "摘要", ContextBudget(max_chars=1_000_000))
    assert trimmed is False and out is msgs


def test_disabled_budget_never_trims():
    msgs = _msgs(200)
    out, trimmed = trim_history(msgs, "摘要", ContextBudget(max_chars=0))
    assert trimmed is False and out is msgs


def test_over_budget_gets_trimmed_within_limit():
    msgs = _msgs(200)
    budget = ContextBudget(max_chars=8_000, keep_recent_turns=3)
    out, trimmed = trim_history(msgs, "【此前对话摘要】我方专业能力:GEO", budget)
    assert trimmed is True
    assert len(out) < len(msgs)
    assert estimate_chars(out) <= budget.max_chars


def test_trim_keeps_first_and_last():
    """The first message (with first-turn intent) and the most recent turns must be kept."""
    msgs = _msgs(60)
    out, _ = trim_history(msgs, "", ContextBudget(max_chars=6_000, keep_recent_turns=2))
    assert out[0] is msgs[0]
    assert out[-1] is msgs[-1]


def test_digest_injected_when_trimming():
    msgs = _msgs(60)
    digest = "【此前对话摘要】本次目标行业:工业制造"
    out, _ = trim_history(msgs, digest, ContextBudget(max_chars=6_000, keep_recent_turns=2))
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    assert digest in ModelMessagesTypeAdapter.dump_json(out).decode()


def test_empty_history_is_safe():
    out, trimmed = trim_history([], "摘要", ContextBudget(max_chars=10))
    assert out == [] and trimmed is False


# --- Summary content ---

class _FakeFlow(dict):
    pass


def test_digest_from_flow_only(monkeypatch):
    """When no profile is available, a summary must still be produced from this run's filters alone."""
    monkeypatch.setattr("agents.sales_agent.service.seller_profile_for_lead_entry",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    flow = {"target_industries": ["工业制造"], "target_regions": ["长三角"], "requested_limit": 100}
    d = build_state_digest(None, 1, flow)
    assert "工业制造" in d and "长三角" in d and "100" in d
    assert "不要重复追问" in d


def test_digest_empty_when_nothing_known(monkeypatch):
    monkeypatch.setattr("agents.sales_agent.service.seller_profile_for_lead_entry",
                        lambda *a, **k: {})
    assert build_state_digest(None, 1, {}) == ""


def test_digest_carries_no_conversation_text(monkeypatch):
    """The summary holds only structured state -- conversation text must not leak into it."""
    monkeypatch.setattr("agents.sales_agent.service.seller_profile_for_lead_entry",
                        lambda *a, **k: {"service_capabilities": ["GEO 优化"], "company_size": "11-100 名员工"})
    d = build_state_digest(None, 1, {"target_regions": ["北京"]})
    assert "GEO 优化" in d and "11-100 名员工" in d and "北京" in d
    assert "用户说" not in d and "assistant" not in d.lower()
