"""The diagnosis narrative layer must know the category -- no guessing from the brand name.

Report #99: the brand "<brand-a>" sells beef and mutton, and all 15 queries actually sent were about mutton,
but the narrative layer only got the brand name (that report didn't even have a website), so the model guessed goat milk powder from 「羊」 ("sheep/goat"),
and the draft mentioned "milk powder" 19 times and "mutton" only 3 times. The data was right; the narrative layer just lacked the category.

This pins three things that must appear in the facts block:
  1. the industry words / brand words the user filled in
  2. the rule "don't infer the category from the brand name"
  3. measured queries -- industry words themselves can be ambiguous (#99 filled in 「<brand-d>羊肉哪家好」 ("which <brand-d> mutton is best"),
     easily read as the dairy brand "<brand-d>"); queries are unambiguous fallback evidence
"""
from __future__ import annotations

from geo.services.full_diagnosis_generator import _sample_queries, _summarize_for_llm

SCORES = {"total": 42, "grade": "C", "dimensions": []}
SUB = {"visibility": {"status": "ok", "data": {"engines": [
    {"answers": [{"query": "口碑好的羊肉品牌推荐"},
                 {"query": "哪个牌子的羊肉好吃"},
                 {"query": "口碑好的羊肉品牌推荐"}]}]}}}


def test_industry_terms_reach_the_prompt():
    out = _summarize_for_llm("<brand-a>", "", SCORES, {},
                             industry_terms=["牛羊肉"], brand_terms=["<brand-a>"])
    assert "牛羊肉" in out


def test_forbids_guessing_from_brand_name():
    out = _summarize_for_llm("<brand-a>", "", SCORES, {}, industry_terms=["牛羊肉"])
    assert "不要从品牌名去推断品类" in out


def test_no_industry_still_forbids_guessing():
    """Forbid it even more when no industry words are filled in -- #99 was exactly that case."""
    out = _summarize_for_llm("<brand-a>", "", SCORES, {})
    assert "不要猜品类" in out


def test_real_queries_are_included_as_evidence():
    out = _summarize_for_llm("<brand-a>", "", SCORES, SUB, industry_terms=["<brand-d>羊肉哪家好"])
    assert "口碑好的羊肉品牌推荐" in out
    assert "哪个牌子的羊肉好吃" in out


def test_sample_queries_dedupes_and_caps():
    assert _sample_queries(SUB) == ["口碑好的羊肉品牌推荐", "哪个牌子的羊肉好吃"]
    many = {"a": [{"query": f"q{i}"} for i in range(30)]}
    assert len(_sample_queries(many, limit=8)) == 8


def test_sample_queries_survives_junk():
    """Sub-result structures vary; if nothing can be extracted return empty, never raise."""
    assert _sample_queries({}) == []
    assert _sample_queries({"x": None, "y": [1, "s", {"query": ""}]}) == []
