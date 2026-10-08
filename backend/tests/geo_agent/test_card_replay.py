"""Structured events may only come from **this turn** -- the mechanism gate for the 2026-08-27 "pile of junk" incident.

`result.all_messages()` includes message_history (the self line feeds up to 50); extracting events from it means:
  - cards      cards from dozens of past turns are re-pushed every turn
  - ui_actions **control_page commands from history are re-executed by the frontend every turn**; the page navigates / submits by itself
  - confirms   past two-step confirmation cards pop up again every turn
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from agents.geo import api as api_mod
from agents.geo.cards import CARD_TOOLS, cards_from, confirms_from, ui_actions_from
from agents.geo.embed import api as embed_api


# ── Fake run result: new_messages gives only this turn, all_messages gives history + this turn ──────────
@dataclass
class _Part:
    part_kind: str
    tool_name: str = ""
    content: Any = None


@dataclass
class _Msg:
    parts: list


class _Result:
    def __init__(self, history, fresh):
        self._h, self._f = history, fresh

    def all_messages(self):
        return self._h + self._f

    def new_messages(self):
        return self._f


def _ret(tool, content):
    return _Msg([_Part("tool-return", tool, content)])


HISTORY = [
    _ret("get_query_coverage", {"hit_queries": 3, "monitored_queries": 8}),
    _ret("control_page", {"ui_actions": [{"action": "navigate", "args": {"path": "/reports"}}]}),
    _ret("admin_approve_doc", {"needs_confirm": True, "action_label": "通过这篇稿",
                               "confirm_token": "v1.x.y.z"}),
]
FRESH = [_ret("run_full_diagnosis", {"status": "need_input", "brand": "<brand-a>"})]


def test_cards_only_from_this_turn():
    cards = cards_from(_Result(HISTORY, FRESH))
    assert [c["tool"] for c in cards] == ["run_full_diagnosis"]


def test_ui_actions_from_history_are_never_replayed():
    """The most serious one: the frontend **executes as instructed**; replaying means the page navigates by itself."""
    assert ui_actions_from(_Result(HISTORY, FRESH)) == []


def test_confirms_from_history_are_never_replayed():
    assert confirms_from(_Result(HISTORY, FRESH)) == []


def test_confirm_token_never_leaves_the_backend():
    fresh = [_ret("admin_approve_doc", {"needs_confirm": True, "action_label": "通过这篇稿",
                                        "confirm_token": "v1.x.y.z"})]
    got = confirms_from(_Result([], fresh))
    assert got and "confirm_token" not in got[0]


def test_falls_back_when_new_messages_is_absent():
    """On version drift fall back to the old behavior instead of crashing."""
    class _Old:
        def all_messages(self):
            return FRESH
    assert [c["tool"] for c in cards_from(_Old())] == ["run_full_diagnosis"]


# ── Both channels share the same card contract ────────────────────────────────────────────

def test_both_channels_share_one_card_tools():
    """embed once kept its own copy and missed run_full_diagnosis -- so when the external channel lacked industry words
    it got no to-fill card and fell back to "the model restates the dict in text", exactly what to-fill cards are meant to eliminate.
    """
    assert api_mod.CARD_TOOLS is CARD_TOOLS
    assert not hasattr(embed_api, "CARD_TOOLS"), "embed 又抄了一份 CARD_TOOLS"
    assert "run_full_diagnosis" in CARD_TOOLS


def test_no_channel_extracts_from_all_messages():
    """Source-level gate: neither chat path may extract events from result.all_messages() again.
    (all_messages_json() is the full history persisted to DB; that one is correct and not covered here.)
    """
    for mod in (api_mod, embed_api):
        src = inspect.getsource(mod)
        assert "result.all_messages()" not in src, f"{mod.__name__} 又用回了 all_messages()"
