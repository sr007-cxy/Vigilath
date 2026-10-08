"""When industry words are missing: **don't start the diagnosis**; return a to-fill card.

Reproducing the 2026-08-27 <brand-a> incident: the account had no topic -> couldn't derive industry words -> it used to raise ModelRetry,
so the model had to write a paragraph asking on its own, and not knowing what the user does it made up a guess list like "if you sell shoes and apparel -> canvas shoes...
if you do catering -> snacks...". The user was never really asked, and had to pick from a pile of wild guesses.

Industry words decide what questions all eight sub-audits of the run ask AI; guess wrong and the whole report is wrong, and the run really costs money
(4000 credits). So the user must decide them, and **not a single byte may go out before the user replies**.
"""
from __future__ import annotations

import inspect

from agents.geo import agent as agent_mod
from agents.geo import tools as tools_mod
from agents.geo.api import CARD_TOOLS


def test_missing_terms_returns_card_not_modelretry():
    """Source-level assertion: that branch must return a to-fill card, not raise ModelRetry anymore."""
    src = inspect.getsource(tools_mod.run_full_diagnosis)
    head = src.split("running = _fd_latest")[0]      # only look at the part before starting
    assert '"status": "need_input"' in head
    assert '"field": "industry_terms"' in head
    assert "raise ModelRetry(\"缺行业词" not in head, "缺行业词不该再抛 ModelRetry"


def test_card_is_returned_before_any_http_call():
    """The to-fill card must come before building the token / sending HTTP -- otherwise it's "run first, ask later"."""
    src = inspect.getsource(tools_mod.run_full_diagnosis)
    i_card = src.index('"status": "need_input"')
    for marker in ("full/generate", "_jwt.encode", "AsyncClient"):
        assert i_card < src.index(marker), f"待填卡出现在 {marker} 之后,等于先发起再问"


def test_run_full_diagnosis_emits_cards():
    """Without being in CARD_TOOLS the backend doesn't push cards events, the card can't render, and it falls back to restating in text."""
    assert "run_full_diagnosis" in CARD_TOOLS


def test_prompt_forbids_inventing_industry_terms():
    """The prompt must explicitly forbid drafting industry words on its own, and forbid listing guesses across industries."""
    p = agent_mod.SYSTEM_PROMPT
    assert "绝不许你自己拟" in p
    assert "猜测清单" in p


def test_hint_tells_model_not_to_guess():
    """The card's own _hint is the only on-the-spot instruction the model will read; it must state both things."""
    src = inspect.getsource(tools_mod.run_full_diagnosis)
    seg = src[src.index('"_hint"'):src.index('"_hint"') + 400]
    assert "还没有发起" in seg
    assert "不要自己猜" in seg
