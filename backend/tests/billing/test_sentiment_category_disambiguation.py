"""Sentiment searches must carry the category, otherwise they find a same-named company in another industry.

Report #99: the brand "<brand-a>" sells beef and mutton, but the search query only had the brand name --
`<brand-a> 最近一周 舆情 负面 争议 新闻` ("<brand-a> last week sentiment negative controversy news") -- and all 12 pieces of evidence fetched were about **<namesake-company>**
(a goat milk company): milk supply disruption in March 2024, pasteurized goat milk going sour, goat milk delivery breach.
The whole sentiment chapter was about another company, and the narrative layer faithfully relayed it, so the whole report talked about milk powder.

The narrative layer's category anchor (test_diag_narrative_category_anchor) fixes the "relaying" layer;
this fixes the further upstream "fetched the wrong entity".
"""
from __future__ import annotations

from geo.services.full_diagnosis_sentiment import _account_snapshot, cache_key_for
from geo.services.sentiment_yuanbao_websearch import _build_queries

TERMS = {"aliases": [], "keywords": ["蒙羊"], "competitors": [], "excludes": []}


def test_category_lands_in_every_query():
    acc = _account_snapshot(None, "<brand-a>", ["蒙羊"], ["牛羊肉"])
    qs = _build_queries(acc, TERMS)
    assert qs, "至少要有检索词"
    # The category must appear in the subject position, not just in one focus query
    assert sum("牛羊肉" in q for q in qs) >= 4, qs


def test_no_industry_keeps_old_behaviour():
    """Behavior unchanged when no industry words are filled in -- don't stuff things into the search query out of nowhere."""
    acc = _account_snapshot(None, "<brand-a>", ["蒙羊"], None)
    assert _build_queries(acc, TERMS)[0].startswith("<brand-a> 最近一周")


def test_official_account_intent_not_overwritten():
    """A regular account's intent is filled in by the user; the category must use a separate field and may not overwrite it."""
    class _Acc:
        id = 7
        target = "<brand-a>"
        ticker = "<brand-a>"
        aliases_json = "[]"
        intent = "只看食品安全"
        keywords_json = "[]"
        keyword_groups_json = "[]"
        excludes_json = "[]"

    snap = _account_snapshot(_Acc(), "<brand-a>", ["蒙羊"], ["牛羊肉"])
    assert snap.intent == "只看食品安全"
    assert snap.category_hint == "牛羊肉"


def test_cache_key_includes_category():
    """The category changes the search query -- if it's not in the cache key, changing the category still hits the old wrong result."""
    a = cache_key_for("<brand-a>", ["蒙羊"], 1, ["牛羊肉"])
    b = cache_key_for("<brand-a>", ["蒙羊"], 1, ["羊奶粉"])
    c = cache_key_for("<brand-a>", ["蒙羊"], 1, None)
    assert a != b and a != c and b != c


def test_category_hint_caps_at_three_terms():
    """Industry words may be a long list; stuffing them all into the query dilutes it."""
    acc = _account_snapshot(None, "X", [], ["a", "b", "c", "d", "e"])
    assert acc.category_hint.split() == ["a", "b", "c"]
