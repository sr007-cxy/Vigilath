"""Degradation contract for the page version of one-click diagnosis.

The chat version has long had a public web search fallback (tools._diagnosis_fallback), but those two chapters on the page version were blank --
for the same failure, the two entry points gave users completely different things. This pins the contract on the backend side.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.geo import degrade
import geo.services.full_diagnosis_generator as g


@pytest.fixture()
def quiet():
    with patch.object(g, "_update_step") as step, \
         patch.object(g, "full_diag_log_event"):
        yield step


def test_probe_failure_produces_degraded_not_blank(quiet):
    out = g._degraded_probe(1, "威吉力", RuntimeError("跑批中心不可达"),
                            "skipped", "跑批中心检测跳过")
    assert out["data_quality"] == degrade.DEGRADED
    assert out["degraded_notice"]
    assert out["degraded_missing"] == ["AI 可见性实测", "竞品对位"]


def test_step_status_stays_a_known_terminal_state(quiet):
    """**Must not** introduce a degraded step status.

    `_update_step`'s progress["done"] only recognizes ok/failed/skipped; an extra status means
    the report can never complete done and gets stuck at generating. This pipeline has already tripped on that once.
    """
    g._degraded_probe(1, "x", RuntimeError("e"), "skipped", "m")
    g._degraded_probe(1, "x", ValueError("e"), "failed", "m")
    for call in quiet.call_args_list:
        assert call.args[2] in ("ok", "failed", "skipped"), \
            f"step 状态 {call.args[2]!r} 不在 progress.done 认的三个终态里"


def test_numeric_metrics_are_nulled_out(quiet):
    """Degraded results never produce numeric metrics -- back-deriving scores from search evidence is the textbook way to fabricate data."""
    wrapped = degrade.wrap(
        {"整体露出率": 42, "nested": {"TOP1露出率": 7, "文字": "保留"}},
        degrade.Degraded(reason="x"),
    )
    assert wrapped["整体露出率"] is None
    assert wrapped["nested"]["TOP1露出率"] is None
    assert wrapped["nested"]["文字"] == "保留"


def test_conclusions_without_evidence_are_dropped_and_reported(quiet):
    wrapped = degrade.wrap(
        {"conclusions": [{"text": "有据的", "evidence_ref": 0}, {"text": "凭空的"}]},
        degrade.Degraded(reason="x"),
    )
    assert len(wrapped["conclusions"]) == 1
    assert wrapped["dropped_unsourced"] == 1, "丢了几条要说出来,不能悄悄少给"


def test_no_websearch_key_still_degrades_cleanly(quiet, monkeypatch):
    """Even without a search key configured, give a degraded label instead of raising or pretending it ran."""
    monkeypatch.delenv("TENCENT_SEARCH_API_KEY", raising=False)
    out = g._degraded_probe(1, "威吉力", RuntimeError("e"), "skipped", "m")
    assert out["data_quality"] == degrade.DEGRADED
    assert out["evidence"] == []
    assert out["conclusions"] == [], "没证据就不给结论"
