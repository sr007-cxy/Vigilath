"""One-off sentiment quick check — start / poll / result / runaway / no-cache fallback. No LLM, no DB, no network.

Guards three things:
  ① a one-off quick check **requires creating nothing** (no sentiment account, no topic) — that's why it exists;
  ② a runaway is judged stale and may be restarted, **never left hanging forever** (same family as reports permanently stuck in generating);
  ③ when the cache is unavailable, **don't pretend to queue** — queued but unable to store the result, the user would wait for something that never comes.

Run: venv/bin/python -m pytest tests/geo_agent/test_sentiment_once.py -q   # cwd = backend/
"""
from __future__ import annotations

import asyncio
import time

import pytest

import agents.geo.tools as T
from agents.geo.deps import AgentDeps
from geo.services import cache_service, sentiment_once
from geo.services import full_diagnosis_sentiment as fds

FAKE_RESULT = {
    "kpi": {"total_today": 3, "high_risk": 1, "avg_sentiment": -0.31, "active_sources": 2},
    "risk_dist": [{"level": "high", "count": 1}],
    "posts": [{"title": "某某被投诉", "source": "微博", "url": "https://weibo.com/x",
               "publish_time": "2026-08-20", "sentiment_label": "负面",
               "sentiment_score": -0.7, "risk_level": "high", "summary": "摘要"}],
    "negative_posts": [],
    "evidence_range": {"from": "2015-01-01", "to": "2026-08-20"},
    "recent_count": 1, "historical_count": 2, "undated_count": 0,
    "generated_at": "2026-08-25T10:00:00",
}


def _run(coro):
    """Run a coroutine and put back a usable current event loop (reason: see test_geo_runtime_limits)."""
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def fake_cache(monkeypatch):
    """In-memory fake cache (Redis in production). Returns (store, switch); switch['on']=False simulates Redis being down."""
    store: dict = {}
    switch = {"on": True}

    monkeypatch.setattr(cache_service, "get_cached_report",
                        lambda k: store.get(k) if switch["on"] else None)
    monkeypatch.setattr(cache_service, "set_cached_report",
                        lambda k, v, ttl_s=0: switch["on"] and (store.__setitem__(k, v) or True))
    monkeypatch.setattr(cache_service, "delete_cached_report",
                        lambda k: (store.pop(k, None), True)[1])
    return store, switch


@pytest.fixture
def ctx(monkeypatch):
    """A minimal ctx sufficient for the tool: the probe is replaced with a fake and the gate passes (the gate itself has its own tests)."""
    def _cached(user_id, brand, brand_terms=None, report_id=None):
        time.sleep(0.2)                                  # Simulate background work time
        cache_service.set_cached_report(fds.cache_key_for(brand, brand_terms), FAKE_RESULT)
        return FAKE_RESULT

    monkeypatch.setattr(fds, "run_sentiment_cached", _cached)
    monkeypatch.setattr(fds, "run_sentiment_probe",
                        lambda user_id, brand, brand_terms=None, report_id=None: FAKE_RESULT)
    monkeypatch.setattr(T, "usage_guardrail_check", lambda deps, action: None)

    class _Ctx:
        deps = AgentDeps(account_id=1, user_id=1, db=None, channel="skill")

    return _Ctx()


def _wait_ready(ctx, brand: str, timeout_s: float = 3.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = _run(T.get_sentiment_once(ctx, brand=brand))
        if out["status"] == "ready":
            return out
        time.sleep(0.1)
    raise AssertionError("后台快检没有在限时内落结果")


def test_once_needs_no_account_or_topic(fake_cache, ctx):
    """No sentiment account, no topic, just give a brand name to start — that's why the one-off quick check exists."""
    out = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    assert out["status"] == "running"
    assert "创建" not in out["note"] and "开通" not in out["note"]


def test_once_polls_to_result_with_links(fake_cache, ctx):
    _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    out = _wait_ready(ctx, "<brand-b>")
    row = out["负面明细"][0]
    assert row["原帖链接"] and row["发布时间"], "每条负面必须带原帖链接和发布时间"
    # The evidence time range must be passed through truthfully so the copy layer doesn't hard-code "last 30 days"
    assert out["证据时间范围"]["from"] == "2015-01-01"


def test_once_reuses_24h_result(fake_cache, ctx):
    """Starting again for the same brand within 24h = return the previous result directly, without burning another engine call."""
    _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    _wait_ready(ctx, "<brand-b>")
    again = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    assert again["status"] == "ready"


def test_stale_run_does_not_hang_forever(fake_cache, ctx):
    """After a process restart kills the background thread, the flag remains but the result never comes — it must be judged dead and allow a restart."""
    store, _ = fake_cache
    store[sentiment_once._flag_key("<brand-b>", ["<brand-b>"])] = {"started_at": time.time() - 3600}
    out = _run(T.get_sentiment_once(ctx, brand="<brand-b>"))
    assert out["status"] == "failed"
    assert "重新发起" in out["note"]


def test_no_cache_falls_back_to_sync(fake_cache, ctx):
    """Don't queue when the cache is unavailable — even if queued it can't be stored, and the user would wait for a result that never comes."""
    _, switch = fake_cache
    switch["on"] = False
    out = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    assert out["status"] == "ready"


def test_daily_health_dead_end_points_at_once(fake_cache, ctx, monkeypatch):
    """When daily monitoring isn't enabled, don't dead-end the user; must point to the one-off quick check."""
    monkeypatch.setattr(T, "_sentiment_account", lambda ctx_, active_only=False: None)
    out = _run(T.get_sentiment_today(ctx))
    assert out["configured"] is False
    assert "run_sentiment_once" in out["next"]
