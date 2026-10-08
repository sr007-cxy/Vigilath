"""Degradation contract -- the opposite of a fallback isn't an error, it's **fabricating data**.

After a pipeline fails, the most dangerous output isn't "nothing came out", it's "a report that looks complete with every number guessed".
So these four are mechanisms, not reminders (prompts can't stop the model):

  1. Conclusions without a source are discarded, and how many were dropped is reported honestly
  2. Degraded results produce no numeric metrics
  3. The three fields have the same names along the whole pipeline
  4. When it can't search it returns available=False, **never an empty list posing as "searched, found nothing"**
"""
from __future__ import annotations

import httpx
import pytest

from agents.geo import degrade, websearch


def test_three_fields_are_always_injected():
    out = degrade.wrap({"a": 1}, degrade.Degraded(missing=["AI可见性实测"], reason="服务挂了"))
    assert out["data_quality"] == degrade.DEGRADED
    assert out["degraded_missing"] == ["AI可见性实测"]
    assert "服务挂了" in out["degraded_notice"]


def test_full_result_is_marked_full():
    assert degrade.wrap({"a": 1}, None)["data_quality"] == degrade.FULL


@pytest.mark.parametrize("key", ["露出率", "TOP1露出率", "文章引用率", "score", "hit_rate"])
def test_numeric_metrics_are_nulled(key):
    """Never back-derive ratios from search results -- that's the textbook way to fabricate data."""
    out = degrade.wrap({key: 42, "nested": {key: 7}}, degrade.Degraded())
    assert out[key] is None
    assert out["nested"][key] is None


def test_unsourced_conclusions_are_dropped_and_counted():
    out = degrade.wrap({
        "conclusions": [
            {"text": "有出处", "evidence_ref": 0},
            {"text": "没出处,凭感觉"},
            {"text": "也没出处"},
        ],
    }, degrade.Degraded())
    assert [c["text"] for c in out["conclusions"]] == ["有出处"]
    assert out["dropped_unsourced"] == 2, "丢了几条要说出来,不能悄悄少给"


def test_non_metric_fields_survive():
    out = degrade.wrap({"品牌": "威吉力", "证据": [{"url": "x"}]}, degrade.Degraded())
    assert out["品牌"] == "威吉力"
    assert out["证据"] == [{"url": "x"}]


@pytest.mark.parametrize("bad", [
    None,
    httpx.ConnectError("boom"),
    RuntimeError("boom"),
    {"error": "上游炸了"},
    {"valid_results": 0},
    [],
])
def test_upstream_down_recognises_every_failure_shape(bad):
    """The criterion is **mechanism**, not exception text -- text changes with upstream revisions, mechanisms don't.
    Especially {'error': ...}, "wrapping a failure into a normal response body", which is more common than half the exceptions."""
    assert degrade.upstream_down(bad) is not None


@pytest.mark.parametrize("good", [{"ok": True}, [{"a": 1}], {"valid_results": 3}])
def test_healthy_results_are_not_flagged(good):
    assert degrade.upstream_down(good) is None


def test_websearch_never_fakes_an_empty_result(monkeypatch):
    """Without a configured key it must be available=False.

    An empty list would be read by upper layers as "searched, found nothing", and the model would start concluding based on "no negative news"
    -- the first step of fabricating data.
    """
    monkeypatch.delenv("TENCENT_SEARCH_API_KEY", raising=False)
    out = websearch.search("威吉力 负面")
    assert out["available"] is False
    assert out["evidence"] == []
    assert out["reason"], "说不了为什么查不了,上层就没法如实转述"


def test_websearch_request_failure_is_not_an_empty_result(monkeypatch):
    monkeypatch.setenv("TENCENT_SEARCH_API_KEY", "fake")

    def _boom(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(websearch.requests, "post", _boom)
    out = websearch.search("威吉力")
    assert out["available"] is False and out["evidence"] == []


def test_search_many_dedupes_by_url(monkeypatch):
    monkeypatch.setenv("TENCENT_SEARCH_API_KEY", "fake")
    page = {"url": "https://a.com/1", "title": "标题", "passage": "摘要"}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"Response": {"Pages": [page, page]}}

    monkeypatch.setattr(websearch.requests, "post", lambda *a, **k: _Resp())
    out = websearch.search_many(["q1", "q2"])
    assert out["available"] is True
    assert len(out["evidence"]) == 1, "同一条链接在多个查询里出现应该只留一条"
