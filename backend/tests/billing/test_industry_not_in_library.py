"""Industry library: industries without a library must **say so and give the list**, not get away with a bare 404.

Product feedback: a user asked about "industrial cleaning robot industry trends" and got one brand's data.
There are two root causes -- the agent side had no industry ranking tool (now added), and this layer: the 404 body only said
"industry doesn't exist or isn't public", so callers got nothing usable and had to find some number to fill in.

**Fuzzy matching is deliberately not done**: landing "industrial cleaning robots" on "commercial cleaning robots" data,
the caller gets perfectly normal-looking industry data with no signal that it answers a different industry than the user asked about.
One character off is a different set of brands; users can't spot this kind of error, yet they'd base decisions on it.

    venv/bin/python -m pytest tests/billing/test_industry_not_in_library.py -q   # cwd = backend/
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from geo.api import industry_library as il


def test_404_carries_the_available_list(monkeypatch):
    monkeypatch.setattr(il, "_industry_topics", lambda db: {"商用清洁机器人": []})
    with pytest.raises(HTTPException) as e:
        il.industry_data(key="工业清洁机器人", db=None)
    assert e.value.status_code == 404
    d = e.value.detail
    assert isinstance(d, dict), "404 正文必须是结构化的,一句话字符串没法用"
    assert d["code"] == "INDUSTRY_NOT_IN_LIBRARY"
    assert d["available"] == ["商用清洁机器人"]
    assert "工业清洁机器人" in d["message"]


def test_no_fuzzy_match(monkeypatch):
    """Similar names must not match either -- that's exactly the bug being fixed."""
    called: list = []
    monkeypatch.setattr(il, "_industry_topics", lambda db: {"商用清洁机器人": []})
    monkeypatch.setattr(il, "_industry_data",
                        lambda db, name, topics: called.append(name))
    for near in ("工业清洁机器人", "清洁机器人", "商用清洁机器人 "):
        with pytest.raises(HTTPException):
            il.industry_data(key=near, db=None)
    assert called == [], "只认精确 key,近似名一律 404"


def test_sample_note_states_the_coverage():
    """The sample metric definition must be readable straight to the user -- without it, someone given 6 brands will judge the data untrustworthy."""
    assert "已建库" in il._SAMPLE_NOTE
    assert str(il.RANKING_CAP) in il._SAMPLE_NOTE
