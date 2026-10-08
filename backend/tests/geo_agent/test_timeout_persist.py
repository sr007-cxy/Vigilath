"""Persisting history on whole-turn timeouts -- only tail orphans should be lost, not the whole memory.

Originally the timeout branch **didn't persist the turn at all**, reasoning "run.result is None, and there are tool_calls without a tool_return;
stored, the next turn pydantic-ai raises UserError". The first half is right, but the conclusion overreached:
what was lost wasn't just this turn but **all history before it** -- users felt "it lost its memory mid-conversation".

Only the tail orphan tool_calls really need dropping, and `align_to_pair_boundary` does exactly that
(its comment says plainly "the timed-out turn has exactly this shape"; it just was never called).
"""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)

from agents.geo.methods import align_to_pair_boundary


def _history_with_orphan_tail():
    """A normal conversation + a trailing tool_call that never got its tool_return (= the shape of a timeout interruption)."""
    return [
        ModelRequest(parts=[UserPromptPart(content="我们品牌在 AI 里能被搜到吗")]),
        ModelResponse(parts=[ToolCallPart(tool_name="get_topic", args={},
                                          tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="get_topic", content={"n": 1},
                                           tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="你的主题是威吉力。")]),
        ModelRequest(parts=[UserPromptPart(content="那跑个诊断")]),
        # ↓ the timeout cuts here: the call went out, the result never came back
        ModelResponse(parts=[ToolCallPart(tool_name="run_full_diagnosis", args={},
                                          tool_call_id="c2")]),
    ]


def _tool_call_ids(msgs):
    return {p.tool_call_id for m in msgs for p in (getattr(m, "parts", None) or [])
            if getattr(p, "part_kind", "") == "tool-call"}


def _tool_return_ids(msgs):
    return {p.tool_call_id for m in msgs for p in (getattr(m, "parts", None) or [])
            if getattr(p, "part_kind", "") == "tool-return"}


def test_orphan_tool_call_is_dropped():
    """Tail orphans must be dropped -- left in, the next turn pydantic-ai raises UserError directly."""
    out = align_to_pair_boundary(_history_with_orphan_tail())
    assert "c2" not in _tool_call_ids(out), "没等到结果的 tool_call 还留着"


def test_earlier_conversation_survives():
    """The conversation before this turn must still be there -- that's the reason for fixing this bug."""
    out = align_to_pair_boundary(_history_with_orphan_tail())
    texts = [p.content for m in out for p in (getattr(m, "parts", None) or [])
             if getattr(p, "part_kind", "") in ("user-prompt", "text")]
    assert "我们品牌在 AI 里能被搜到吗" in texts
    assert "你的主题是威吉力。" in texts


def test_result_is_replayable():
    """In the repaired sequence every tool_call has a matching tool_return and can be safely fed back to the model."""
    out = align_to_pair_boundary(_history_with_orphan_tail())
    assert _tool_call_ids(out) <= _tool_return_ids(out), "还有配不上的 tool_call"


def test_empty_and_none_are_passthrough():
    assert align_to_pair_boundary(None) is None
    assert align_to_pair_boundary([]) == []
