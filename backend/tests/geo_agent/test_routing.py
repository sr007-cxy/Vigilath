"""Component-level evaluation -- no LLM, deterministic, used as a CI regression gate.

Tests only the **mechanism layer** (route_line + business line tool isolation), no model calls, no DB, milliseconds:
1. whether route_line(question) routes to the expected business line;
2. whether the routed line's toolset **contains** expect_tools and **excludes** forbid_tools.

Item 2 is the hard guarantee of the isolation mechanism: even if the model wants to cross lines and call GEO write tools, the line's toolset doesn't have them -> out of reach.
Regressions from changing route_line keywords or _line_tools grouping turn red here immediately.

Run (with the agent venv, since the import chain pulls in pydantic-ai):
    venv/bin/python -m pytest tests/geo_agent/test_routing.py -q   # cwd = backend/
"""
from __future__ import annotations

import pytest

from agents.geo.eval.dataset import CASES
from agents.geo.agent import (
    route_line, _line_tools, _GEO_READ, _GEO_WRITE, _MEDIA_WRITE, _PAID, _SHARED,
)


def _toolset_names(line: str, channel: str = "embed") -> set[str]:
    """The set of tool names this business line can actually load under the given channel.

    Defaults to embed -- these assertions ask "can the external channel reach it", and since 2026-08-25
    toolsets vary by channel; passing web would make the assertions test an unrelated set.
    """
    return {getattr(t, "__name__", str(t))
            for t in _line_tools(line, can_write=True, include_publish=False,
                                 channel=channel)}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_route_line(case):
    assert route_line(case.utterance) == case.line, (
        f"[{case.id}] route_line 判错:期望 {case.line},得到 {route_line(case.utterance)} —— {case.utterance!r}"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_expected_tools_reachable(case):
    """Expected tools must be in the routed line's toolset, otherwise the model can't reach them and can't possibly call them successfully."""
    names = _toolset_names(case.line)
    missing = [t for t in case.expect_tools if t not in names]
    assert not missing, f"[{case.id}] 期望工具不在 {case.line} 线工具集内(机制上够不到):{missing}"


# All GEO tool names (the sentiment line must reach none of them -- the hard guarantee of cross-line isolation)
# _SHARED (websearch_evidence) is deliberately given to both lines -- degraded evidence gathering is unrelated to business lines;
# both GEO asking "are we being cited" and sentiment asking "is anyone attacking us" rely on it when the main pipeline is down.
# So it doesn't count as a "GEO tool" and is explicitly excluded from the mutual-exclusion assertion.
_SHARED_NAMES = {getattr(t, "__name__", str(t)) for t in _SHARED}
_GEO_ALL = {getattr(t, "__name__", str(t))
            for t in (_GEO_READ + _GEO_WRITE)} - _SHARED_NAMES


@pytest.mark.parametrize("case", [c for c in CASES if c.line == "sentiment"], ids=lambda c: c.id)
def test_sentiment_line_has_no_geo_tools(case):
    """Hard guarantee of cross-line isolation: the sentiment line's toolset **contains no GEO tools**, so the model mechanically can't reach GEO write actions.

    Note: per-case forbid_tools are **behavior-level** (the model shouldn't "call" them, e.g. viewing questions shouldn't trigger draft_articles),
    while draft_articles/create_topic legitimately exist on the GEO line -- such assertions can only be done in run_eval (end-to-end).
    """
    leaked = _toolset_names("sentiment") & _GEO_ALL
    assert not leaked, f"[{case.id}] 跨线隔离失效:舆情线竟加载到 GEO 工具 {leaked}"


# A whole group never reachable from external channels: real outbound sends + media writes + paid orders.
_EXTERNAL_BANNED = ({"publish_drafts"}
                    | {getattr(t, "__name__", str(t)) for t in (_MEDIA_WRITE + _PAID)})


@pytest.mark.parametrize("channel", ["embed", "im", "skill", "api"])
def test_external_channels_never_get_write_or_paid_tools(channel):
    """External channels can't reach real outbound sends, media writes or paid orders -- a whole group, not a single tool.

    Anyone in an IM group can send messages, while the account is bound to the installer; in the skill marketplace the caller is someone else's
    host assistant. If these channels got money-spending tools, it would be "someone else spending your money".
    """
    for line in ("geo", "sentiment", "both"):
        leaked = _toolset_names(line, channel) & _EXTERNAL_BANNED
        assert not leaked, f"{line} 线在 {channel} 通道竟暴露 {leaked}"


@pytest.mark.parametrize("channel", ["embed", "im", "skill", "api"])
def test_external_ban_is_immune_to_env(monkeypatch, channel):
    """With every capability switch and channel allowlist opened to the max, external channels **still** can't get them.

    This is a mechanism gate, not a config gate -- env can only tighten, never loosen. A misconfiguration shouldn't become a security incident.
    """
    from agents.geo import gates

    for env_name, _ in gates.CAPS.values():
        monkeypatch.setenv(env_name, "web,admin,embed,im,skill,api")
    monkeypatch.setenv("AGENT_ALLOW_EXTERNAL_PUBLISH", "1")
    monkeypatch.setenv("AGENT_ALLOW_PAID_PUBLISH", "1")
    for line in ("geo", "sentiment", "both"):
        leaked = _toolset_names(line, channel) & _EXTERNAL_BANNED
        assert not leaked, f"{line} 线在 {channel} 通道被 env 放开了 {leaked}"


def test_web_channel_does_get_them():
    """Conversely: these tools must exist on our own web page, otherwise "users can complete the whole flow on their own" is empty talk."""
    names = _toolset_names("both", "web")
    assert {"push_media_draft", "pay_and_publish", "get_wallet"} <= names
