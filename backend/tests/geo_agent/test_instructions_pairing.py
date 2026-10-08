"""The two system prompt switches must take effect **in pairs**.

Background: `system_prompt=` gets serialized into message_history, and pydantic-ai only attaches it when history is empty
-- so existing sessions forever use the prompt from "the moment it was persisted", and later metric definition changes and added rules
never reach them. Switching to `instructions=` fixes it, but **switching only half is worse**:

  - switch to instructions without stripping history -> old sessions carry both the old system prompt and the new instructions,
    two metric definitions fighting
  - strip history without switching to instructions -> old sessions end up with **no system prompt at all**

So the criterion can live in only one place (`methods.use_instructions`), read by both sides.
"""
from __future__ import annotations

import inspect

from agents.geo import agent as A
from agents.geo import methods as M


def test_single_source_of_truth():
    """The env may only be read once, in use_instructions -- two sides each reading their own will drift."""
    for mod in (A, M):
        src = inspect.getsource(mod)
        occurrences = src.count("AGENT_USE_INSTRUCTIONS")
        if mod is M:
            # in methods: one env read + a mention in a comment
            assert occurrences >= 1
        else:
            assert "AGENT_USE_INSTRUCTIONS" not in src.replace("use_instructions", ""), \
                "agent.py 不该自己读 env,要走 methods.use_instructions"


def test_agent_passes_instructions_when_on(monkeypatch):
    monkeypatch.setenv("AGENT_USE_INSTRUCTIONS", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    A.build_agent.cache_clear()
    ag = A.build_agent("chat", "geo", "web")
    # When using instructions, the Agent's own _system_prompts should be empty
    assert not getattr(ag, "_system_prompts", ()) , \
        "切了 instructions 就不该再有 system_prompt,否则会被序列化进历史"


def test_agent_falls_back_to_system_prompt_when_off(monkeypatch):
    monkeypatch.setenv("AGENT_USE_INSTRUCTIONS", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    A.build_agent.cache_clear()
    ag = A.build_agent("chat", "geo", "web")
    assert getattr(ag, "_system_prompts", ()), "回退档必须还挂着 system_prompt"
    A.build_agent.cache_clear()


def test_history_is_stripped_only_when_on(monkeypatch):
    """Stripping and switching share a source: with the switch off, system parts in history must stay as-is."""
    src = inspect.getsource(M.load_message_history)
    assert "use_instructions()" in src and "strip_system_parts" in src, \
        "load_message_history 必须按同一个开关决定剥不剥"


def test_strip_drops_system_parts_and_keeps_the_rest():
    # Must use a dataclass: _replace_parts goes through dataclasses.replace,
    # and when it can't replace it returns as-is ("better keep a few extra than corrupt the structure") -- testing with a plain class can't test stripping
    from dataclasses import dataclass, field

    @dataclass
    class _P:
        part_kind: str

    @dataclass
    class _M:
        parts: list = field(default_factory=list)

    msgs = [_M([_P("system-prompt")]),
            _M([_P("system-prompt"), _P("user-prompt")]),
            _M([_P("text")])]
    out = M.strip_system_parts(msgs)
    assert len(out) == 2, "整条只有 system part 的要丢掉"
    assert all(p.part_kind != "system-prompt" for m in out for p in m.parts)
    assert [p.part_kind for p in out[0].parts] == ["user-prompt"], "同条里的其余 part 要留着"
