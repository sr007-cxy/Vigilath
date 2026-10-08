"""One-off sentiment monitoring quick check -- confirm / start / poll / result / runaway / no-cache fallback. No LLM, no DB, no network.

Guards four things:
  (1) a one-off quick check **requires creating nothing** (no sentiment account, no topic) -- the reason it exists;
  (2) it **costs credits**, so the first call only returns a confirm card and doesn't start (2026-08-30 product feedback:
     "credit-consuming services run automatically with no notice");
  (3) a runaway is judged stale and can be restarted, **never hanging forever** (same class as the reports-stuck-in-generating pitfall);
  (4) when the cache is unavailable **don't pretend to queue** -- queued but results can't be stored means the user waits for something that never arrives.

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
    """Run a coroutine and put a usable current event loop back (see test_geo_runtime_limits for why)."""
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


# user_id in ctx -- the quick-check cache is partitioned by account; keys in tests must match what the tool computes
USER_ID = 1


@pytest.fixture
def ctx(monkeypatch):
    """A minimal ctx sufficient for the tool: the probe is replaced with a fake, the gate allows (the gate itself has its own tests)."""
    # industry_terms is the category word for **brand disambiguation**, passed all the way down since 2026-09-03 (see
    # the module comment of services/sentiment_once). The fake probe must accept it and write under the same key,
    # otherwise the key the tool computes differs from the one the fake writes, and the test fails looking like "the background never stored a result".
    def _cached(user_id, brand, brand_terms=None, report_id=None, industry_terms=None):
        time.sleep(0.2)                                  # simulate background latency
        cache_service.set_cached_report(
            fds.cache_key_for(brand, brand_terms, USER_ID, industry_terms), FAKE_RESULT)
        return FAKE_RESULT

    monkeypatch.setattr(fds, "run_sentiment_cached", _cached)
    monkeypatch.setattr(fds, "run_sentiment_probe",
                        lambda user_id, brand, brand_terms=None, report_id=None,
                        industry_terms=None: FAKE_RESULT)
    monkeypatch.setattr(T, "usage_guardrail_check", lambda deps, action: None)

    class _Ctx:
        deps = AgentDeps(account_id=1, user_id=1, db=None, channel="skill",
                         turn_id="turn-1")

    return _Ctx()


def _confirmed(ctx, **kwargs) -> dict:
    """Go through two-step confirmation before starting -- quick checks cost credits; the first call only returns a confirm card.

    The second step must use a different turn_id: the fulcrum of confirm.py is "reject if the ticket's turn equals this turn",
    simulating "the user really said something again".
    """
    preview = _run(T.run_sentiment_once(ctx, **kwargs))
    assert preview.get("needs_confirm") is True, f"第一次调用必须只出确认卡,拿到的是 {preview}"
    assert preview.get("modified") is False
    token = preview["confirm_token"]
    ctx.deps.turn_id = "turn-2"
    try:
        return _run(T.run_sentiment_once(ctx, confirm_token=token, **kwargs))
    finally:
        ctx.deps.turn_id = "turn-1"


def _wait_ready(ctx, brand: str, timeout_s: float = 3.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = _run(T.get_sentiment_once(ctx, brand=brand))
        if out["status"] == "ready":
            return out
        time.sleep(0.1)
    raise AssertionError("后台快检没有在限时内落结果")


def test_once_needs_no_account_or_topic(fake_cache, ctx):
    """No sentiment account, no topic; just a brand name is enough to start -- the reason one-off quick checks exist."""
    out = _confirmed(ctx, brand="<brand-b>")
    assert out["status"] == "running"
    assert "创建" not in out["note"] and "开通" not in out["note"]


def test_once_asks_before_spending(fake_cache, ctx):
    """The first call **only returns a confirm card and never starts**.

    The most concentrated point in product feedback: credit-consuming services ran automatically without asking the user.
    The confirm card must include "what it does / how much it costs / remaining balance", and explicitly modified=False.
    """
    preview = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    assert preview["needs_confirm"] is True
    assert preview["modified"] is False
    assert preview["preview"]["品牌"] == "<brand-b>"
    assert preview["cost"], "确认卡必须带报价"
    # Really not started: fetching the result now can only be "not started yet"
    assert _run(T.get_sentiment_once(ctx, brand="<brand-b>"))["status"] != "ready"


def test_once_rejects_same_turn_self_confirm(fake_cache, ctx):
    """The model confirming itself in **the same turn** -- must be rejected. This is the fulcrum of two-step confirmation."""
    preview = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    out = _run(T.run_sentiment_once(ctx, brand="<brand-b>",
                                    confirm_token=preview["confirm_token"]))
    assert out["ok"] is False and out["code"] == "SAME_TURN"
    assert _run(T.get_sentiment_once(ctx, brand="<brand-b>"))["status"] != "ready"


def test_once_cancelled_when_user_says_no(fake_cache, ctx):
    """The ticket is valid but the user says 「算了」 ("never mind") next turn -- treat as cancel, don't start."""
    preview = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    ctx.deps.turn_id = "turn-2"
    ctx.deps.user_message = "算了,先不跑了"
    try:
        out = _run(T.run_sentiment_once(ctx, brand="<brand-b>",
                                        confirm_token=preview["confirm_token"]))
    finally:
        ctx.deps.turn_id, ctx.deps.user_message = "turn-1", ""
    assert out["cancelled"] is True and out["modified"] is False


def test_once_polls_to_result_with_links(fake_cache, ctx):
    _confirmed(ctx, brand="<brand-b>")
    out = _wait_ready(ctx, "<brand-b>")
    row = out["负面明细"][0]
    assert row["原帖链接"] and row["发布时间"], "每条负面必须带原帖链接和发布时间"
    # The evidence time range must be passed through truthfully, so the copy layer doesn't hardcode "last 30 days"
    assert out["证据时间范围"]["from"] == "2015-01-01"


def test_once_reuses_24h_result(fake_cache, ctx):
    """Starting the same brand again within 24h = return the last result directly, without burning engine calls again.

    The confirm card must reflect this truthfully too: a cache hit **shouldn't quote a price again**.
    """
    _confirmed(ctx, brand="<brand-b>")
    _wait_ready(ctx, "<brand-b>")
    preview = _run(T.run_sentiment_once(ctx, brand="<brand-b>"))
    assert preview["preview"]["24h内已有结果"] is True
    assert "0 积分" in str(preview["cost"])
    again = _confirmed(ctx, brand="<brand-b>")
    assert again["status"] == "ready"


def test_stale_run_does_not_hang_forever(fake_cache, ctx):
    """After a process restart takes the background thread away, the marker remains but the result never comes -- must be judged dead and allow restart."""
    store, _ = fake_cache
    store[sentiment_once._flag_key(USER_ID, "<brand-b>", ["<brand-b>"])] = {"started_at": time.time() - 3600}
    out = _run(T.get_sentiment_once(ctx, brand="<brand-b>"))
    assert out["status"] == "failed"
    assert "重新发起" in out["note"]


def test_no_cache_falls_back_to_sync(fake_cache, ctx):
    """Don't queue when the cache is unavailable -- even if queued it can't be stored, and the user would wait for a result that never arrives."""
    _, switch = fake_cache
    switch["on"] = False
    out = _confirmed(ctx, brand="<brand-b>")
    assert out["status"] == "ready"


def test_daily_health_dead_end_points_at_once(fake_cache, ctx, monkeypatch):
    """Without daily monitoring enabled, don't dead-end the user; point them to the one-off quick check."""
    monkeypatch.setattr(T, "_sentiment_account", lambda ctx_, active_only=False: None)
    out = _run(T.get_sentiment_today(ctx))
    assert out["configured"] is False
    assert "run_sentiment_once" in out["next"]


def test_cache_is_scoped_per_user(fake_cache):
    """For a brand A has run, B must not hit A's result.

    This is an unauthorized read: quick-check results contain negative post bodies and links; sharing the cache across accounts hands A's
    negative sentiment concerns straight to B. Before 2026-08-25 cache_key_for was built only from (brand, terms, window).
    """
    a = fds.cache_key_for("<brand-b>", ["<brand-b>"], 1)
    b = fds.cache_key_for("<brand-b>", ["<brand-b>"], 2)
    assert a != b, "不同账号必须落在不同缓存键上"
    # Same for the anonymous side: different devices don't hit each other
    assert fds.cache_key_for("<brand-b>", ["<brand-b>"], "anon:dev-1") != \
           fds.cache_key_for("<brand-b>", ["<brand-b>"], "anon:dev-2")


def test_flag_key_is_scoped_too(fake_cache):
    """The "running" marker must be partitioned by account too -- otherwise B is blocked by A's in-progress marker,
    sees a "still running" they never started, and never gets a result."""
    assert sentiment_once._flag_key(1, "<brand-b>", ["<brand-b>"]) != \
           sentiment_once._flag_key(2, "<brand-b>", ["<brand-b>"])
