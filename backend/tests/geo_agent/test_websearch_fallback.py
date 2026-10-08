"""Degradation chain -- after the main pipeline fails, give **degraded results with sources**, not a bare failed, and certainly not made-up numbers.

One premise that must be pinned: **Yuanbao WebSearch can't be the fallback**. It goes through telemetry's /run-now,
so when the batch run center is down it goes down too -- using it to back up batch run failures is a circular dependency, yielding nothing when it matters.
The fallback can only be the in-process direct public web path (websearch.py).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from agents.geo import degrade, tools, websearch


def _run(coro):
    """Run a coroutine and put a usable event loop back afterwards (same approach as test_sentiment_once)."""
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


class _Deps:
    db = None
    user_id = 1
    account_id = 1
    topic_id = None
    channel = "web"
    caps = ("read", "write")
    scope = "self"


class _Ctx:
    deps = _Deps()


def _fake_search(rows):
    def _go(queries, **kw):
        return {
            "available": True, "queried": list(queries), "reason": "",
            "evidence": [{"title": t, "url": u, "snippet": sn,
                          "source_domain": "example.com", "published_at": "2026-08-01",
                          "provider": "tencent", "query": queries[0]}
                         for t, u, sn in rows],
        }
    return _go


def _diag(ctx=None, brand="威吉力", terms=None, site="", reason="后端不可达"):
    return _run(tools._diagnosis_fallback(ctx or _Ctx(), brand,
                                          terms or ["AI可见性检测"], site, reason=reason))


def test_diagnosis_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.setattr(websearch, "search_many",
                        _fake_search([("威吉力怎么样", "https://a.com/1", "还行")]))
    out = _diag()
    assert out["status"] == "degraded"
    assert out["data_quality"] == degrade.DEGRADED
    assert out["degraded_missing"], "必须说清哪几项没测成"
    assert out["公开信息证据"], "降级也要给可下钻的证据"


def test_diagnosis_fallback_gives_the_negative_finding(monkeypatch):
    """Negative-form gaps -- the conclusion "you can't be found in searches for 'which XXX is best'" holds without engine measurement,
    and it's exactly the sentence users most need to see."""
    monkeypatch.setattr(websearch, "search_many",
                        _fake_search([("别家很好", "https://a.com/1", "推荐别家")]))
    out = _diag(terms=["老楼加装电梯"])
    assert any("检索不到" in g for g in out["缺口"])


@pytest.mark.parametrize("key", ["露出率", "score", "总分", "文章引用率"])
def test_diagnosis_fallback_never_invents_scores(monkeypatch, key):
    monkeypatch.setattr(websearch, "search_many",
                        _fake_search([("t", "https://a.com/1", "s")]))
    assert _diag().get(key) is None, f"降级结果里竟然有 {key}"


def test_when_even_search_is_down_it_says_so(monkeypatch):
    """When search is unavailable too, it must say "can't measure", and **must not** say "no negative news" just because nothing was found."""
    monkeypatch.setattr(websearch, "search_many",
                        lambda q, **kw: {"available": False, "evidence": [],
                                         "queried": q, "reason": "没配 key"})
    out = _diag()
    assert out["status"] == "unavailable"
    assert out["data_quality"] == degrade.UNAVAILABLE
    assert "不要" in out["note"]


def test_sentiment_fallback_keeps_the_same_shape(monkeypatch):
    """Degraded result keys must be **exactly identical** to normal results (values may be null).

    A different set of key names means one more branch in the display layer, and that branch will sooner or later go unmaintained.
    """
    monkeypatch.setattr(websearch, "search_many",
                        _fake_search([("威吉力 投诉", "https://a.com/1", "有人吐槽")]))
    out = _run(tools._sentiment_fallback(_Ctx(), "威吉力", [], reason="抓取挂了"))
    normal_keys = {"mode", "brand", "条目数", "中高风险条数", "平均情感分",
                   "信源数", "风险分布", "负面明细", "证据时间范围", "note"}
    assert normal_keys <= set(out), f"降级结果缺了正常形状里的键:{normal_keys - set(out)}"
    assert out["平均情感分"] is None, "检索结果推不出情感分,不能给"
    assert out["负面明细"][0]["原帖链接"], "每条都要能下钻"
    assert "降级" in out["note"]


@pytest.mark.parametrize("fn", [tools._diagnosis_fallback, tools._sentiment_fallback],
                         ids=lambda f: f.__name__)
def test_yuanbao_is_not_used_as_the_fallback_source(fn):
    """The fallback source can't be Yuanbao -- it goes through telemetry's /run-now and goes down with the batch run center."""
    src = inspect.getsource(fn)
    assert "yuanbao" not in src.lower(), f"{fn.__name__} 把元宝当兜底了(循环依赖)"
    assert "websearch.search_many" in src
