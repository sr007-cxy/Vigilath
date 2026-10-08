"""Slot extraction -- the sentence agreed on by frontend and backend must be recognized.

Clicking 「用这些词开始诊断」 ("start diagnosis with these words") in IndustryTermsAsk in frontend/src/components/AgentChat/cards.tsx
sends exactly `行业词:X Y Z` ("industry words: X Y Z"). **Both sides must recognize this sentence**; changing either side requires changing the other.
"""
from __future__ import annotations

import pathlib

from agents.geo.flow import extract


def test_recognises_the_exact_string_the_card_sends():
    assert extract("行业词:烤羊肉串 清真牛羊肉") == {"industry_terms": ["烤羊肉串", "清真牛羊肉"]}


def test_frontend_still_sends_that_exact_shape():
    """Contract gate: if the shape of the frontend sentence changes, this must change with it."""
    card = pathlib.Path(__file__).resolve().parents[3] / \
        "frontend/src/components/AgentChat/cards.tsx"
    src = card.read_text(encoding="utf-8")
    assert "`行业词:${terms.join(' ')}`" in src, "前端回填格式变了,agents/geo/flow/slots.py 要同步"


def test_accepts_the_common_separators():
    for raw in ("行业词:牛羊肉,火锅食材", "行业词:牛羊肉、火锅食材", "行业词: 牛羊肉/火锅食材"):
        assert extract(raw) == {"industry_terms": ["牛羊肉", "火锅食材"]}, raw


def test_accepts_halfwidth_colon_and_aliases():
    assert extract("品类词: 牛羊肉") == {"industry_terms": ["牛羊肉"]}
    assert extract("行业:牛羊肉") == {"industry_terms": ["牛羊肉"]}


def test_dedupes_and_caps_at_five():
    got = extract("行业词:a a b c d e f g")["industry_terms"]
    assert got == ["a", "b", "c", "d", "e"]


def test_brand_slot():
    assert extract("品牌:<brand-a>") == {"brand": "<brand-a>"}


def test_returns_nothing_when_unsure():
    """If unrecognized, don't fill -- better to let the model proceed as before than guess a wrong slot."""
    for raw in ("", "   ", "帮我跑一下,<brand-a>的一键全面诊断", "好慢啊", "跑完了吗?"):
        assert extract(raw) == {}, raw


def test_free_text_after_the_marker_is_taken_as_is():
    """What the user types in the card input box ends up spliced into that sentence too, and shouldn't be reinterpreted."""
    assert extract("行业词:老楼加装电梯")["industry_terms"] == ["老楼加装电梯"]
