"""Runtime guardrails (timeouts / usage caps) — no LLM, no DB, millisecond-level, runs in CI.

The most important one is test_admin_never_sets_tool_calls_limit: it guards not a value but the mechanism
"under exhaustive there must be no gate that can split a batch of tool_calls".

Run: venv/bin/python -m pytest tests/geo_agent/test_runtime_limits.py -q   # cwd = backend/
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from agents.geo.runtime.limits import (
    GeoAgentTimeout, deadline_for, profile_for, public_message, run_guarded,
    timeout_for, usage_limits_enabled, usage_limits_for,
)

# ★ Derive from **the module under test itself**, not from the test file's relative location — the test directory layout differs across
#   branches (develop has backend/tests/geo_agent/, the release branch still has agents/geo/eval/);
#   hard-coding parents[N] resolves to a nonexistent path under another layout, raising FileNotFoundError instead of an assertion failure.
import agents.geo.runtime.limits as _limits_mod

AGENT_DIR = pathlib.Path(_limits_mod.__file__).resolve().parents[1]


def _run(coro):
    """Run a coroutine and put back a usable current event loop.

    ★ Can't use asyncio.run directly: on exit it calls set_event_loop(None), and in Python 3.12
      asyncio.get_event_loop() raises RuntimeError when there is no current loop.
      This directory sorts alphabetically before sales_agent, so a bare asyncio.run makes all of that suite's
      get_event_loop().run_until_complete(...) tests fail together with
      "There is no current event loop" — green when run alone, red in the full run, the hardest kind to debug.
    """
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


# ── Tier determination ──────────────────────────────────────────────────
@pytest.mark.parametrize("scene,line,channel,want", [
    ("chat", "geo", "web", "chat"),
    ("chat", "sentiment", "web", "chat"),
    ("chat", "both", "web", "chat"),
    ("chat", "admin", "web", "admin"),
    ("pro", "geo", "web", "pro"),
    ("pro", "admin", "web", "pro"),      # scene=pro takes priority over line
    ("chat", "geo", "im", "im"),
    ("chat", "admin", "im", "admin"),    # admin takes priority over channel
])
def test_profile_for(scene, line, channel, want):
    assert profile_for(scene, line, channel) == want


# ── Timeouts ────────────────────────────────────────────────────────────
def test_default_timeouts_are_ordered():
    """admin/pro are wider than chat — more tools, larger return bodies, longer reasoning."""
    assert timeout_for("chat") < timeout_for("admin") < timeout_for("pro")


@pytest.mark.parametrize("profile", ["chat", "admin", "pro", "im"])
def test_timeout_lands_before_nginx_cuts(profile):
    """★ The cap serves only one purpose: wrap up before nginx's proxy_read_timeout of 300s,
    turning a 504 into a human-readable sentence. Anything over 300 is the same as not setting it."""
    assert 0 < timeout_for(profile) < 300, "超时必须早于 nginx 300s 的 SSE 读超时"


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_ADMIN", "7")
    assert timeout_for("admin") == 7


def test_timeout_zero_disables(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_CHAT", "0")
    assert timeout_for("chat") == 0
    assert deadline_for("chat") == float("inf")


def test_timeout_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_CHAT", "abc")
    assert timeout_for("chat") == 240


def test_unknown_profile_uses_chat_default():
    assert timeout_for("nope") == timeout_for("chat")


def test_run_guarded_times_out(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_CHAT", "1")
    with pytest.raises(GeoAgentTimeout):
        _run(run_guarded(asyncio.sleep(5), profile="chat"))


def test_run_guarded_passes_value_through(monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_CHAT", "5")

    async def ok():
        return "done"

    assert _run(run_guarded(ok(), profile="chat")) == "done"


def test_run_guarded_disabled_still_awaits(monkeypatch):
    """Turning off the timeout must not swallow the awaitable along with it."""
    monkeypatch.setenv("AGENT_TIMEOUT_CHAT", "0")

    async def ok():
        return 42

    assert _run(run_guarded(ok(), profile="chat")) == 42


# ── Usage caps ──────────────────────────────────────────────────────────
def test_admin_never_sets_tool_calls_limit():
    """★ A mechanism gate, not a value gate.

    Under end_strategy="exhaustive" the framework must run every tool_call in a response;
    a tool_calls_limit hit in the middle of the batch raises UsageLimitExceeded, with the first tools executed
    and the rest not — admin_approve_doc becomes "email sent, placement task not created, return body is an exception".
    Any value may land right in the middle, so the answer is **don't set it**, not raise it.
    """
    assert usage_limits_for("admin").tool_calls_limit is None


def test_admin_still_has_a_loop_brake():
    """Not setting tool_calls_limit doesn't mean no gate — request_limit is checked **before** the next model request,
    never splits a batch of tool_calls, and is what prevents infinite loops."""
    lim = usage_limits_for("admin")
    assert lim.request_limit and lim.request_limit > 0
    assert lim.total_tokens_limit and lim.total_tokens_limit > 0


@pytest.mark.parametrize("profile", ["chat", "pro", "im"])
def test_non_admin_profiles_have_all_three_brakes(profile):
    lim = usage_limits_for(profile)
    assert lim.request_limit and lim.tool_calls_limit and lim.total_tokens_limit


def test_usage_limits_kill_switch(monkeypatch):
    monkeypatch.setenv("AGENT_USAGE_LIMITS", "0")
    assert usage_limits_enabled() is False
    assert all(usage_limits_for(p) is None for p in ("chat", "admin", "pro", "im"))


# ── User-readable copy ──────────────────────────────────────────────────
def test_public_message_covers_both_guard_types():
    assert public_message(GeoAgentTimeout("chat 档超过 90s 未返回"))

    from pydantic_ai.exceptions import UsageLimitExceeded
    assert public_message(UsageLimitExceeded("request_limit exceeded"))


def test_public_message_is_empty_for_other_errors():
    """Ordinary faults shouldn't be disguised as "a few too many steps"; that hides the real problem."""
    assert public_message(ValueError("boom")) == ""


@pytest.mark.parametrize("exc", [GeoAgentTimeout("admin 档超过 150s 未返回")])
def test_public_message_leaks_nothing_internal(exc):
    """Never echo seconds, tier names or exception class names."""
    msg = public_message(exc)
    for leak in ("150", "admin", "Timeout", "GeoAgent", "profile"):
        assert leak not in msg, f"文案泄漏了内部信息:{leak}"


# ── Structural gate on integration points ───────────────────────────────
def _calls_with_kwarg(path: pathlib.Path, func_names: set[str], kwarg: str) -> tuple[int, int]:
    """Count the total .run()/.iter() calls and how many of them carry the given kwarg."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    total = withkw = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in func_names:
            continue
        total += 1
        if any(k.arg == kwarg for k in node.keywords):
            withkw += 1
    return total, withkw


AGENT_CALL_SITES = sorted(
    p for p in list(AGENT_DIR.glob("*.py")) + list(AGENT_DIR.glob("embed/*.py"))
    if _calls_with_kwarg(p, {"run", "iter"}, "deps")[1] > 0
)


def test_call_sites_are_discoverable():
    assert AGENT_CALL_SITES, "一个 agent 调用点都没扫到 —— 断言等于没写"


@pytest.mark.parametrize("path", AGENT_CALL_SITES, ids=lambda p: p.name)
def test_every_agent_call_passes_usage_limits(path):
    """Every agent.run()/iter() must pass usage_limits — missing one is an uncapped backdoor."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"run", "iter"}
                and any(k.arg == "deps" for k in node.keywords)
                and not any(k.arg == "usage_limits" for k in node.keywords)):
            missing.append(node.lineno)
    assert not missing, f"{path.name} 第 {missing} 行的 agent 调用没传 usage_limits"


@pytest.mark.parametrize("path", AGENT_CALL_SITES, ids=lambda p: p.name)
def test_every_call_site_has_a_wall_clock_guard(path):
    """Non-streaming uses run_guarded, streaming (api.py) uses deadline_for — one of the two is required."""
    src = path.read_text(encoding="utf-8")
    assert "run_guarded" in src or "deadline_for" in src, f"{path.name} 没有整轮超时保护"


def test_sse_path_does_not_save_history_on_timeout():
    """★ The timeout branch must never persist to DB: run.result is None and history has unanswered tool_calls;
    saving them makes pydantic-ai raise UserError(unprocessed tool calls) on the next turn."""
    lines = (AGENT_DIR / "api.py").read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if "if time.perf_counter() > deadline:" in ln)
    # Take up to that branch's return (search by line, don't use str.index — tool_return in comments would match)
    end = next(n for n in range(i + 1, len(lines)) if lines[n].strip() == "return")
    branch = "\n".join(lines[i:end + 1])
    assert "save_message_history" not in branch, "超时分支里出现了落库调用"
    assert 'status="timeout"' in branch, "超时没记进模型用量,会在用量页上隐形"
    assert "public_message" in branch, "超时该给用户一句可读的话,不是干等或抛原始异常"
