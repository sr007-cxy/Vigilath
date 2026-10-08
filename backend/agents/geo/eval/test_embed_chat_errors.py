"""Error surface of the external /api/agent/v1/chat — no exception text may ever be shown to the user.

Background: from 2026-08-24 the external chat was completely unusable due to a NameError, and the SSE carried
`{"error": "name 'line' is not defined"}`, which the skill client printed verbatim as a red line.
Users felt "walls everywhere, no hints, just errors" — the truth was nobody translated the walls and nobody caught the bug.

Guards two things:
  ① hitting a gate (CallDenied) is normal, not a fault -> output human text as the body, no error event;
  ② code bugs -> one human sentence + a reference id; the raw exception goes only to server logs.

Run: venv/bin/python -m pytest tests/geo_agent/test_embed_chat_errors.py -q   # cwd = backend/
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agents.geo.agent as agent_mod
import agents.geo.embed.api as eapi
from agents.geo.embed.tokens import EmbedClaims
from geo.services.call_policy import CallDenied


def _client(monkeypatch, exc: BaseException) -> TestClient:
    app = FastAPI()
    app.include_router(eapi.router, prefix="/api")

    class _Agent:
        async def run(self, *a, **kw):
            raise exc

    monkeypatch.setattr(agent_mod, "build_embed_agent", lambda *a, **kw: _Agent())
    monkeypatch.setattr(eapi, "load_message_history", lambda deps: [])
    monkeypatch.setattr(eapi, "save_message_history", lambda deps, m: None)

    app.dependency_overrides[eapi.embed_claims] = lambda: EmbedClaims(
        account_id=1, caps=["read", "write"], tid="t1", origins=[], policy_key="")
    app.dependency_overrides[eapi.get_db] = lambda: None
    return TestClient(app)


def _events(resp) -> list[dict]:
    return [json.loads(ln[5:].strip()) for ln in resp.text.splitlines() if ln.startswith("data:")]


@pytest.mark.parametrize("exc", [
    NameError("name 'line' is not defined"),          # Code bug
    CallDenied(429, "今日触发次数已达上限(50/50)"),     # Gate
    CallDenied(403, "该档未开放的动作:run_full_diagnosis"),
])
def test_never_leaks_raw_exception(monkeypatch, exc):
    resp = _client(monkeypatch, exc).post("/api/agent/v1/chat", json={"message": "今天舆情怎么样"})
    evts = _events(resp)
    text = "".join(e.get("delta", "") for e in evts)

    assert resp.status_code == 200
    assert not any("error" in e for e in evts), f"仍在往 SSE 里塞 error 事件:{evts}"
    assert any(e.get("done") for e in evts), "必须收口,否则客户端干等"
    assert text.strip(), "必须给用户一句话,不能静默"
    for leak in ("NameError", "CallDenied", "Traceback", "not defined", "channel:"):
        assert leak not in text, f"文案泄漏了内部信息:{leak}"


def test_gate_reads_as_a_wall_not_a_crash(monkeypatch):
    """Hitting a quota must say clearly "when you can come back / where to continue", otherwise it's no different from an outage."""
    resp = _client(monkeypatch, CallDenied(429, "今日触发次数已达上限(50/50)")) \
        .post("/api/agent/v1/chat", json={"message": "跑个诊断"})
    text = "".join(e.get("delta", "") for e in _events(resp))
    assert "上限" in text
    assert "网页版" in text or "再试" in text


def test_unexpected_error_carries_a_reference(monkeypatch):
    """Bugs must get a reference id — same source as call_audit.request_id / usage ref_id, so an incident report can find that exact call."""
    resp = _client(monkeypatch, NameError("boom")).post("/api/agent/v1/chat", json={"message": "hi"})
    text = "".join(e.get("delta", "") for e in _events(resp))
    assert "编号" in text
