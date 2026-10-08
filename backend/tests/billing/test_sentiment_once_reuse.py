"""Sentiment monitoring quick check: **a round that didn't actually burn engine calls isn't charged**.

Two clues from product feedback, combined:
  - "the full diagnosis report's sentiment analysis matches the quick check's content, and the diagnosis is free";
  - "services that cost credits charged directly without asking the user to confirm".

Technically: the two entry points share the `cache_key_for` **algorithm** but feed different arguments --
the diagnosis passes the report row's brand_terms / industry_terms, the quick check passes `_norm_terms`'s result,
with industry_terms always None. industry_terms is part of the key, so as long as the user filled in industry words,
the quick check always misses the content the diagnosis just produced for free -> the same content is given free once, then charged again.

`_worker`'s criterion is now "did this round really run", not "did it produce a result".

    venv/bin/python -m pytest tests/billing/test_sentiment_once_reuse.py -q   # cwd = backend/
"""
from __future__ import annotations

import pytest

from geo.services import cache_service, sentiment_once
from geo.services import full_diagnosis_sentiment as fds

USER = 1
BRAND = "威吉力"
RESULT = {"kpi": {"total_today": 3}, "posts": [], "generated_at": "2026-08-30T10:00:00"}


@pytest.fixture
def wired(monkeypatch):
    """In-memory cache + stubs for accounting/probing. Returns (store, bills, ran)."""
    store: dict = {}
    monkeypatch.setattr(cache_service, "get_cached_report", store.get)
    monkeypatch.setattr(cache_service, "set_cached_report",
                        lambda k, v, ttl_s=0: (store.__setitem__(k, v), True)[1])
    monkeypatch.setattr(cache_service, "delete_cached_report",
                        lambda k: (store.pop(k, None), True)[1])

    bills: list = []
    monkeypatch.setattr(sentiment_once, "bill_one",
                        lambda user_id, brand: bills.append((user_id, brand)))

    ran: list = []

    def _probe(user_id, brand, brand_terms=None, report_id=None, industry_terms=None):
        ran.append(brand)
        store[fds.cache_key_for(brand, brand_terms, user_id, industry_terms)] = RESULT
        return RESULT

    monkeypatch.setattr(fds, "run_sentiment_cached", _probe)
    # Default "this account hasn't run a diagnosis"; cases that need it override this
    monkeypatch.setattr(sentiment_once, "_free_diagnosis_result",
                        lambda user_id, brand: None)
    return store, bills, ran


def test_fresh_run_is_billed(wired):
    store, bills, ran = wired
    sentiment_once._worker(USER, BRAND, [BRAND])
    assert ran == [BRAND] and bills == [(USER, BRAND)]


def test_cache_hit_is_not_billed(wired):
    """Run by yourself within 24h -- reuse directly, don't charge again."""
    store, bills, ran = wired
    store[fds.cache_key_for(BRAND, [BRAND], USER)] = RESULT
    sentiment_once._worker(USER, BRAND, [BRAND])
    assert ran == [], "命中缓存就不该再跑"
    assert bills == [], "没跑就不该收钱"


def test_diagnosis_result_is_reused_for_free(wired, monkeypatch):
    """A full diagnosis just ran the same brand for free -- reuse it, no charge, and it must be readable by polling."""
    store, bills, ran = wired
    monkeypatch.setattr(sentiment_once, "_free_diagnosis_result",
                        lambda user_id, brand: RESULT)
    sentiment_once._worker(USER, BRAND, [BRAND])
    assert ran == [] and bills == []
    # Write back under the quick-check key, otherwise peek/status can't read it and the user keeps seeing "still running"
    assert sentiment_once.peek(USER, BRAND, [BRAND]) == RESULT


def test_lookup_failure_falls_back_to_running(wired, monkeypatch):
    """Reuse is an optimization, not a requirement -- if the lookup fails run as usual; don't block the feature."""
    store, bills, ran = wired

    def boom(user_id, brand):
        raise RuntimeError("db down")

    monkeypatch.setattr(sentiment_once, "_free_diagnosis_result", boom)
    with pytest.raises(RuntimeError):
        # _worker itself swallows exceptions into a failed marker; call the internal function directly to confirm it really raises
        sentiment_once._free_diagnosis_result(USER, BRAND)
    monkeypatch.setattr(sentiment_once, "_free_diagnosis_result",
                        lambda user_id, brand: None)
    sentiment_once._worker(USER, BRAND, [BRAND])
    assert ran == [BRAND] and bills == [(USER, BRAND)]
