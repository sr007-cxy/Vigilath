"""Addressing capability of admin_list_plan_items — pure-function level, no DB, no LLM.

Background: the sample finance & tax customer (topic 20) has 433 plan rows, 133 of them past due. The old implementation hard-coded the window
`today <= d <= today+days`, so past-due rows were always outside it; all plan write tools address by item_id,
and `admin_get_plan` returns only a summary without items — the agent had no way at all to get the ids of those 133 rows,
i.e. they were "out of reach". This guards the four new addressing capabilities, plus "behavior unchanged when no new params are passed".

Run: venv/bin/python -m pytest tests/geo_agent/test_plan_item_addressing.py -q   # cwd = backend/
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from agents.geo import admin_tools as at


def _run(coro):
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


TODAY = date.today()


def _row(i, d, seed, platform, seq=0):
    return {"id": f"item-{i}", "publish_date": d.isoformat(), "seed": seed,
            "platform": platform, "queries": [], "media": "", "template_id": 1, "seq": seq}


def _plan_rows():
    """3 past-due + 3 today + 3 future rows, with varied platforms/seeds."""
    rows = []
    for i, off in enumerate([-30, -10, -1]):
        rows.append(_row(f"past{i}", TODAY + timedelta(days=off), "税务风险检测", "知乎", i))
    for i in range(3):
        rows.append(_row(f"today{i}", TODAY, "律所财税优化", "软文匠", i))
    for i, off in enumerate([1, 5, 40]):
        rows.append(_row(f"fut{i}", TODAY + timedelta(days=off), "财税外包", "知乎", i))
    return rows


@pytest.fixture()
def ctx(monkeypatch):
    rows = _plan_rows()
    plan = SimpleNamespace(id=1, status="confirmed", publishing_plan_json=json.dumps(rows))
    topic = SimpleNamespace(id=20, name="北京示例财税管理有限公司", user_id=1000031)
    monkeypatch.setattr(at, "admin_topic", lambda c: topic)
    monkeypatch.setattr(at, "own_plan", lambda c: plan)
    monkeypatch.setattr(at, "_plan_rows", lambda p: rows)

    class _Q:
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self): return []

    return SimpleNamespace(deps=SimpleNamespace(db=_Q()))


def _ids(out):
    return [i["item_id"] for i in out["items"]]


# ── 1. Backward compatibility: without new params, behavior matches pre-change ───────
def test_default_window_still_starts_today(ctx):
    out = _run(at.admin_list_plan_items(ctx))
    assert all(i["日期"] >= TODAY.isoformat() for i in out["items"])
    assert not any(x.startswith("item-past") for x in _ids(out))


def test_legacy_keys_are_preserved(ctx):
    out = _run(at.admin_list_plan_items(ctx))
    for k in ("topic_name", "窗口天数", "只看未出稿", "返回行数", "窗口内总行数", "items"):
        assert k in out, f"旧返回体字段 {k} 丢了,前端卡片/既有断言会崩"


def test_new_params_are_all_optional():
    sig = inspect.signature(at.admin_list_plan_items)
    for name in ("date_from", "date_to", "platform", "seed_contains", "offset"):
        assert sig.parameters[name].default in ("", 0), f"{name} 必须有默认值,否则旧调用全挂"


# ── 2. Past-due rows: the core of this change ───────────────────────────
def test_past_rows_are_unreachable_by_default(ctx):
    """Counter-proof: without date_from they're out of reach — exactly the pre-change state."""
    out = _run(at.admin_list_plan_items(ctx, days=90))
    assert not [x for x in _ids(out) if x.startswith("item-past")]


def test_date_from_reaches_past_rows(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat()))
    got = [x for x in _ids(out) if x.startswith("item-past")]
    assert len(got) == 3, f"过期行还是够不到:{_ids(out)}"


def test_date_range_is_inclusive_both_ends(ctx):
    d = (TODAY - timedelta(days=10)).isoformat()
    out = _run(at.admin_list_plan_items(ctx, date_from=d, date_to=d))
    assert _ids(out) == ["item-past1"]


def test_date_to_alone_leaves_start_unbounded(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_to=(TODAY - timedelta(days=5)).isoformat()))
    assert set(_ids(out)) == {"item-past0", "item-past1"}


# ── 3. Filtering ────────────────────────────────────────────────────────
def test_platform_filter(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(),
                                        platform="知乎"))
    assert set(_ids(out)) == {"item-past0", "item-past1", "item-past2", "item-fut0", "item-fut1", "item-fut2"}


def test_seed_filter(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(),
                                        seed_contains="律所"))
    assert all(x.startswith("item-today") for x in _ids(out))


def test_filters_compose(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(),
                                        platform="知乎", seed_contains="财税外包"))
    assert set(_ids(out)) == {"item-fut0", "item-fut1", "item-fut2"}


# ── 4. Paging and "don't treat this page as everything" ─────────────────
def test_matched_total_counts_beyond_the_page(ctx):
    """★ Must keep counting after a page is full, otherwise 「匹配总数」 ("total matched") equals limit and the model thinks it has seen everything."""
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(), limit=2))
    assert out["返回行数"] == 2
    assert out["匹配总数"] == 9, f"匹配总数被 limit 截断了:{out['匹配总数']}"


def test_pagination_walks_the_whole_set(ctx):
    seen, off = [], 0
    while True:
        out = _run(at.admin_list_plan_items(
            ctx, date_from=(TODAY - timedelta(days=60)).isoformat(), limit=2, offset=off))
        seen += _ids(out)
        if not out.get("还有更多"):
            break
        off = out["下一页offset"]
    assert len(seen) == 9 and len(set(seen)) == 9


def test_truncated_page_says_so_loudly(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(), limit=2))
    assert out.get("还有更多") is True
    assert "别拿这一页当全部" in out.get("_note", "")


def test_last_page_has_no_more_flag(ctx):
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(), limit=40))
    assert "还有更多" not in out


def test_limit_still_capped_at_40(ctx):
    """The trim cap stays — it prevents context blowup; paging solves reachability, not loosening volume."""
    out = _run(at.admin_list_plan_items(ctx, date_from=(TODAY - timedelta(days=60)).isoformat(), limit=999))
    assert out["返回行数"] <= 40


# ── 5. Bad input ────────────────────────────────────────────────────────
def test_bad_date_raises_instead_of_silently_falling_back(ctx):
    """A silent fallback yields a result that "looks normal but has a totally wrong range", much harder to debug than an error."""
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry):
        _run(at.admin_list_plan_items(ctx, date_from="2026/07/01"))


def test_reversed_range_raises(ctx):
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry):
        _run(at.admin_list_plan_items(ctx, date_from=TODAY.isoformat(),
                                      date_to=(TODAY - timedelta(days=5)).isoformat()))


# ── 6. Dirty dates: shapes that really exist in production ──────────────
def _ctx_with(rows, monkeypatch):
    plan = SimpleNamespace(id=1, status="confirmed", publishing_plan_json=json.dumps(rows))
    topic = SimpleNamespace(id=20, name="T", user_id=1)
    monkeypatch.setattr(at, "admin_topic", lambda c: topic)
    monkeypatch.setattr(at, "own_plan", lambda c: plan)
    monkeypatch.setattr(at, "_plan_rows", lambda p: rows)

    class _Q:
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self): return []

    return SimpleNamespace(deps=SimpleNamespace(db=_Q()))


def test_iso_datetime_rows_are_recovered(monkeypatch):
    """★ Live topic 20 has 5 rows of '2026-08-10T11:00'. A strict strptime raises ValueError,
    and the old implementation just `continue`d — those rows were permanently invisible to the agent with no hint."""
    d = (TODAY - timedelta(days=3))
    rows = [_row("iso", d, "种子", "知乎")]
    rows[0]["publish_date"] = d.isoformat() + "T11:00"
    ctx2 = _ctx_with(rows, monkeypatch)
    out = _run(at.admin_list_plan_items(ctx2, date_from="2020-01-01"))
    assert _ids(out) == ["item-iso"], "带时间的 ISO 日期没救回来"
    assert "日期无法解析的行" not in out


def test_truly_broken_dates_are_reported_not_swallowed(monkeypatch):
    """Rows that can't be rescued must be reported truthfully — the silent continue was the original disease."""
    rows = [_row("ok", TODAY, "种子", "知乎")]
    bad = _row("bad", TODAY, "种子", "知乎")
    bad["publish_date"] = "下周三"
    rows.append(bad)
    ctx2 = _ctx_with(rows, monkeypatch)
    out = _run(at.admin_list_plan_items(ctx2, date_from="2020-01-01"))
    assert out["日期无法解析的行"] == 1
    assert "人工" in out.get("_warn", ""), "得告诉用户这些行要去页面上手工修"
    assert _ids(out) == ["item-ok"]


def test_empty_date_counts_as_unparsable(monkeypatch):
    rows = [_row("blank", TODAY, "种子", "知乎")]
    rows[0]["publish_date"] = ""
    ctx2 = _ctx_with(rows, monkeypatch)
    out = _run(at.admin_list_plan_items(ctx2, date_from="2020-01-01"))
    assert out["返回行数"] == 0 and out["日期无法解析的行"] == 1


def test_row_date_helper():
    from agents.geo.admin_tools import _row_date
    assert _row_date("2026-08-10") == date(2026, 8, 10)
    assert _row_date("2026-08-10T11:00") == date(2026, 8, 10)
    assert _row_date("2026-08-10 11:00:00") == date(2026, 8, 10)
    assert _row_date("下周三") is None
    assert _row_date("") is None
    assert _row_date(None) is None


# ── 7. One row publishing to multiple platforms ─────────────────────────
def test_extra_platforms_are_shown(monkeypatch):
    """Live topic 20 has 8 rows of "WeChat OA + official blog". Showing only the primary platform = confidently wrong about "where does this row publish"."""
    r = _row("multi", TODAY, "种子", "公众号")
    r["extra_platforms"] = ["官网博客"]
    out = _run(at.admin_list_plan_items(_ctx_with([r], monkeypatch)))
    assert out["items"][0]["平台"] == "公众号"
    assert out["items"][0]["另发"] == ["官网博客"]


def test_extra_platforms_are_filterable(monkeypatch):
    r = _row("multi", TODAY, "种子", "公众号")
    r["extra_platforms"] = ["官网博客"]
    plain = _row("plain", TODAY, "种子", "软文匠")
    ctx2 = _ctx_with([r, plain], monkeypatch)
    assert _ids(_run(at.admin_list_plan_items(ctx2, platform="官网博客"))) == ["item-multi"]
    assert _ids(_run(at.admin_list_plan_items(ctx2, platform="公众号"))) == ["item-multi"]


def test_no_extras_means_no_extra_key(monkeypatch):
    """The vast majority of rows have no extra platforms; don't add an always-empty field to the return body."""
    out = _run(at.admin_list_plan_items(_ctx_with([_row("p", TODAY, "种子", "知乎")], monkeypatch)))
    assert "另发" not in out["items"][0]


def test_row_platform_unchanged_so_counts_do_not_shift():
    """★ _row_platform must not merge extras in — the by_platform stats still use it;
    merging would make per-platform counts sum to more than the total plan rows, and report metric definitions would diverge on the spot."""
    from agents.geo.admin_tools import _row_extra_platforms, _row_platform
    r = {"platform": "公众号", "extra_platforms": ["官网博客"]}
    assert _row_platform(r) == "公众号"
    assert _row_extra_platforms(r) == ["官网博客"]


@pytest.mark.parametrize("raw,want", [
    (None, []), ([], []), ("官网博客", ["官网博客"]),
    (["官网博客", " "], ["官网博客"]), (["A", "B"], ["A", "B"]),
])
def test_extra_platforms_parsing(raw, want):
    from agents.geo.admin_tools import _row_extra_platforms
    assert _row_extra_platforms({"extra_platforms": raw}) == want


def test_dirty_date_is_normalised_but_flagged(monkeypatch):
    """Normalize for display (otherwise the model thinks "time-of-day scheduling" exists), but attach the raw value when different — never flatten silently."""
    d = TODAY - timedelta(days=2)
    r = _row("iso", d, "种子", "知乎")
    r["publish_date"] = d.isoformat() + "T11:00"
    out = _run(at.admin_list_plan_items(_ctx_with([r], monkeypatch), date_from="2020-01-01"))
    it = out["items"][0]
    assert it["日期"] == d.isoformat()
    assert it["日期原文"] == d.isoformat() + "T11:00"


def test_clean_date_has_no_raw_key(monkeypatch):
    out = _run(at.admin_list_plan_items(_ctx_with([_row("p", TODAY, "种子", "知乎")], monkeypatch)))
    assert "日期原文" not in out["items"][0]
