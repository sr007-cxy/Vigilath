"""Pairing gate for history trimming -- no LLM, no DB, milliseconds, runs in CI.

Guards a shape that really caused incidents: tool_call is in a ModelResponse and tool_return in the **next**
ModelRequest; slicing by index splits them. Once split, neither direction errors -- both are hard-to-debug failures:

  broken at the head -> the provider receives "starts with role=tool, no preceding assistant with tool_calls" -> 400
  broken at the tail -> pydantic-ai raises UserError: ...contains unprocessed tool calls

Run (with the agent venv; the import chain pulls in pydantic-ai):
    venv/bin/python -m pytest tests/geo_agent/test_history_pairing.py -q   # cwd = backend/
"""
from __future__ import annotations

import json

import pytest

from agents.geo.methods import _truncate, align_to_pair_boundary, strip_system_parts

pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import (  # noqa: E402
    ModelMessagesTypeAdapter, ModelRequest, ModelResponse, RetryPromptPart,
    SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)


def _turn(user: str, calls: list[str], *, with_returns: bool = True, text: str = "好的"):
    """Build one turn: user -> (text + N tool_calls) -> N tool_returns."""
    out = [ModelRequest(parts=[UserPromptPart(content=user)]),
           ModelResponse(parts=[TextPart(content=text)]
                         + [ToolCallPart(tool_name=f"t_{c}", args={}, tool_call_id=c) for c in calls])]
    if with_returns:
        out.append(ModelRequest(parts=[ToolReturnPart(tool_name=f"t_{c}", content={"ok": 1}, tool_call_id=c)
                                       for c in calls]))
    return out


def _ids(messages):
    calls = {p.tool_call_id for m in messages if m.kind == "response"
             for p in m.parts if getattr(p, "part_kind", "") == "tool-call"}
    rets = {getattr(p, "tool_call_id", None) for m in messages if m.kind == "request"
            for p in m.parts if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")
            and getattr(p, "tool_name", None)}
    return calls, rets


def assert_paired(messages):
    calls, rets = _ids(messages)
    assert not (rets - calls), f"孤儿 tool_return:{rets - calls}"
    assert not (calls - rets), f"没等到应答的 tool_call:{calls - rets}"


# ── 1. Head orphans ────────────────────────────────────────────────────────
@pytest.mark.parametrize("cut", range(1, 7))
def test_any_head_cut_leaves_no_orphan(cut):
    """Cut the head at any index; after repair there must be no orphans -- this is the shape the old _truncate broke sessions with."""
    msgs = _turn("q1", ["c1", "c2"]) + _turn("q2", ["c3"])
    assert_paired(align_to_pair_boundary(msgs[cut:]))


def test_head_cut_actually_had_orphans_before_fix():
    """Counter-proof: without repair it really is broken, otherwise these assertions test nothing."""
    msgs = _turn("q1", ["c1", "c2"])
    _, rets = _ids(msgs[2:])          # keep only the tool_return message
    assert rets == {"c1", "c2"}
    calls, _ = _ids(msgs[2:])
    assert not calls, "构造失败:切完不该还留着 tool_call"


# ── 2. Unanswered at the tail ──────────────────────────────────────────────────────
def test_trailing_unanswered_tool_calls_are_dropped():
    """The shape left by a timeout interruption: tool_call sent but no tool_return."""
    msgs = _turn("q1", ["c1"]) + _turn("q2", ["c9"], with_returns=False)
    fixed = align_to_pair_boundary(msgs)
    assert_paired(fixed)
    assert any(getattr(p, "content", "") == "q2" for m in fixed for p in m.parts), "用户消息不该被连坐"


def test_response_becomes_empty_is_dropped():
    """A ModelResponse containing only unanswered tool_calls is dropped entirely."""
    msgs = [ModelRequest(parts=[UserPromptPart(content="q")]),
            ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="x")])]
    fixed = align_to_pair_boundary(msgs)
    assert [m.kind for m in fixed] == ["request"]


# ── 3. Don't touch what shouldn't be touched ──────────────────────────────────────────────────
def test_healthy_history_is_returned_unchanged():
    msgs = _turn("q1", ["c1", "c2"]) + _turn("q2", ["c3"])
    fixed = align_to_pair_boundary(msgs)
    assert len(fixed) == len(msgs)
    assert all(a is b for a, b in zip(fixed, msgs)), "健康历史必须原对象返回,不做任何重建"


def test_plain_retry_without_tool_name_is_kept():
    """A RetryPromptPart with tool_name None is an ordinary retry, not bound to a tool, and must not be dropped as an orphan."""
    msgs = [ModelRequest(parts=[UserPromptPart(content="q")]),
            ModelResponse(parts=[TextPart(content="a")]),
            ModelRequest(parts=[RetryPromptPart(content="换个说法")])]
    fixed = align_to_pair_boundary(msgs)
    assert len(fixed) == 3


@pytest.mark.parametrize("bad", [None, [], "x", 0])
def test_degenerate_input_passthrough(bad):
    assert align_to_pair_boundary(bad) is bad


# ── 4. _truncate on the storage side ────────────────────────────────────────────────
def _dump(msgs) -> str:
    return ModelMessagesTypeAdapter.dump_json(msgs).decode()


def test_truncate_below_cap_is_byte_identical():
    raw = _dump(_turn("q", ["c1"]))
    assert _truncate(raw, 400) == raw


def test_truncate_zero_cap_disables():
    raw = _dump(_turn("q", ["c1"]) * 3)
    assert _truncate(raw, 0) == raw


def test_truncate_repairs_the_cut():
    """Cut at a position that would split a pair; what's stored must still be intact pairs."""
    msgs = _turn("q1", ["c1", "c2"]) + _turn("q2", ["c3"])
    out = _truncate(_dump(msgs), 2)
    assert_paired(ModelMessagesTypeAdapter.validate_json(out))


def test_truncate_keeps_the_tail_not_the_head():
    """Keep the tail -- the old SystemPromptPart at the start is exactly what we don't want to carry forward (architecture-current state section 13)."""
    msgs = [ModelRequest(parts=[SystemPromptPart(content="OLD_SYS"), UserPromptPart(content="q0")])]
    msgs += _turn("q1", ["c1"]) + _turn("q2", ["c2"])
    out = _truncate(_dump(msgs), 3)
    assert "OLD_SYS" not in out


def test_truncate_returns_original_on_garbage():
    assert _truncate("not json", 2) == "not json"


# ── 5. strip_system_parts ──────────────────────────────────────────────
def test_strip_system_parts_drops_only_system():
    msgs = [ModelRequest(parts=[SystemPromptPart(content="S"), UserPromptPart(content="q")])]
    msgs += _turn("q2", ["c1"])
    out = strip_system_parts(msgs)
    assert not [p for m in out for p in m.parts if getattr(p, "part_kind", "") == "system-prompt"]
    assert any(getattr(p, "content", "") == "q" for m in out for p in m.parts), "同条里的 user 消息不能连坐"
    assert_paired(out)


def test_strip_drops_message_that_was_only_system():
    msgs = [ModelRequest(parts=[SystemPromptPart(content="S")]),
            ModelRequest(parts=[UserPromptPart(content="q")])]
    out = strip_system_parts(msgs)
    assert len(out) == 1


def test_strip_preserves_tool_pairing():
    msgs = [ModelRequest(parts=[SystemPromptPart(content="S"), UserPromptPart(content="q")])]
    msgs += _turn("q2", ["c1", "c2"])
    before_calls, before_rets = _ids(msgs)
    after_calls, after_rets = _ids(strip_system_parts(msgs))
    assert (before_calls, before_rets) == (after_calls, after_rets)
