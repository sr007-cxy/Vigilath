"""/health must really verify the agent -- no LLM, no DB, milliseconds.

CN production on 2026-08-24: container healthy, /health 200, yet every /api/agent/chat was 503.
The old health was a hardcoded dict that never touched pydantic-ai, so the docker healthcheck passed,
and the deploy script's smoke test (only checking healthy + /health 200) passed too -- **a broken image didn't trigger auto-rollback**.

Run: venv/bin/python -m pytest tests/geo_agent/test_health_contract.py -q   # cwd = backend/
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from agents.geo.service import app
    return TestClient(app, raise_server_exceptions=False)


def test_health_ok_when_agent_builds(client, monkeypatch):
    import agents.geo.agent as ag
    monkeypatch.setattr(ag, "build_agent", lambda *a, **k: object())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["agent"] == "ok"


def test_health_503_when_agent_cannot_build(client, monkeypatch):
    """★ This is exactly the shape of the CN production incident: process alive, routes present, but the agent can't be constructed."""
    import agents.geo.agent as ag

    def boom(*a, **k):
        raise AttributeError("module aiohttp has no attribute SocketTimeoutError")

    monkeypatch.setattr(ag, "build_agent", boom)
    r = client.get("/health")
    assert r.status_code == 503, "构造不出 agent 还回 200,冒烟就拦不住坏镜像"
    body = r.json()
    assert body["status"] == "degraded"
    assert "AttributeError" in body["agent"] and "aiohttp" in body["agent"]


def test_health_actually_calls_build_agent(client, monkeypatch):
    """Guard against regressing to a hardcoded dict -- that was exactly the root cause this time."""
    calls = []
    import agents.geo.agent as ag
    monkeypatch.setattr(ag, "build_agent", lambda *a, **k: calls.append(a) or object())
    client.get("/health")
    assert calls, "/health 没有调用 build_agent"


def test_health_does_not_leak_long_internals(client, monkeypatch):
    import agents.geo.agent as ag

    def boom(*a, **k):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr(ag, "build_agent", boom)
    assert len(client.get("/health").json()["agent"]) < 220
