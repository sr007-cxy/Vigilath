"""Timeouts and usage limits -- the first runtime guardrail.

Without it: when the provider hangs, the request hangs until the gateway times out and the user only sees a spinner;
when the model keeps calling tools without converging, nothing stops it.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_runtime_limits.py -q
"""
from __future__ import annotations

import asyncio

import pytest

from agents.sales_agent.model import _model_request_timeout, get_sales_agent_model_settings
from agents.sales_agent.runtime.limits import (
    SalesAgentTimeout,
    public_message,
    run_guarded,
    timeout_for,
    usage_limits_for,
)


def test_timeout_defaults_differ_by_scene():
    """lead runs recommendation orchestration and must get more than chat."""
    assert timeout_for("chat") == 60
    assert timeout_for("lead") == 180
    assert timeout_for("lead") > timeout_for("chat")


def test_timeout_unknown_scene_falls_back():
    assert timeout_for("没见过的场景") == timeout_for("chat")


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_TIMEOUT_CHAT", "12")
    assert timeout_for("chat") == 12


def test_model_request_timeout_is_shorter_than_chat_wall_clock(monkeypatch):
    monkeypatch.delenv("SALES_AGENT_MODEL_TIMEOUT_CHAT", raising=False)
    assert _model_request_timeout("chat") < timeout_for("chat")


def test_model_request_timeout_env_override(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_MODEL_TIMEOUT_CHAT", "6.5")
    assert _model_request_timeout("chat") == 6.5


def test_pro_scene_has_a_timeout_too(monkeypatch):
    """Without it, the template downgrade on the intro email path never gets its chance to trigger -- a hang lasts until gateway timeout."""
    monkeypatch.delenv("SALES_AGENT_MODEL_TIMEOUT_PRO", raising=False)
    assert _model_request_timeout("pro") > 0
    # pro produces a whole email at once, longer than a Q&A turn, so it must get more time than chat.
    assert _model_request_timeout("pro") > _model_request_timeout("chat")


def test_pro_timeout_env_override(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_MODEL_TIMEOUT_PRO", "30")
    assert _model_request_timeout("pro") == 30


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc"])
def test_model_request_timeout_env_garbage_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("SALES_AGENT_MODEL_TIMEOUT_PRO", bad)
    assert _model_request_timeout("pro") == 90.0


def test_unlisted_scenes_stay_untimed(monkeypatch):
    """Only registered scenarios get timeouts. Other scenarios stay as-is and don't get timeouts introduced as a side effect of this change."""
    monkeypatch.setenv("SALES_AGENT_MODEL_TIMEOUT_LEAD", "20")
    assert _model_request_timeout("lead") == 0.0
    assert _model_request_timeout("没见过的场景") == 0.0


def test_explicit_timeout_overrides_the_scene_default(monkeypatch):
    """Intro email rewriting downgrades faster than the pro default, thanks to this parameter."""
    monkeypatch.setenv("SALES_AGENT_MODEL_API_KEY", "test-key")
    settings = get_sales_agent_model_settings("pro", timeout=45.0)
    assert settings is not None and settings["timeout"] == 45.0
    assert get_sales_agent_model_settings("pro")["timeout"] == 90.0


@pytest.mark.parametrize("bad", ["", "0", "-5", "abc"])
def test_timeout_env_garbage_ignored(monkeypatch, bad):
    monkeypatch.setenv("SALES_AGENT_TIMEOUT_CHAT", bad)
    assert timeout_for("chat") == 60


def test_usage_limits_lead_is_looser():
    chat, lead = usage_limits_for("chat"), usage_limits_for("lead")
    assert lead.request_limit > chat.request_limit
    assert lead.tool_calls_limit > chat.tool_calls_limit
    assert chat.request_limit >= 1 and chat.tool_calls_limit >= 1


def test_run_guarded_passes_through_fast_result():
    async def quick():
        return "ok"
    assert asyncio.run(run_guarded(quick(), scene="chat")) == "ok"


def test_run_guarded_times_out(monkeypatch):
    monkeypatch.setenv("SALES_AGENT_TIMEOUT_CHAT", "1")

    async def hang():
        await asyncio.sleep(30)

    with pytest.raises(SalesAgentTimeout):
        asyncio.run(run_guarded(hang(), scene="chat"))


def test_run_guarded_propagates_real_errors(monkeypatch):
    """Business exceptions must be re-raised as-is, not swallowed by the timeout wrapper."""
    async def boom():
        raise ValueError("业务错误")

    with pytest.raises(ValueError):
        asyncio.run(run_guarded(boom(), scene="chat"))


def test_public_message_is_user_facing():
    msg = public_message(SalesAgentTimeout("x"))
    assert msg and "超时" not in msg and "Timeout" not in msg   # don't expose internal terms
    assert "停下来" in msg


def test_public_message_empty_for_other_errors():
    assert public_message(ValueError("x")) == ""
