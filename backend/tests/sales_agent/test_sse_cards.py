"""Card delivery over the SSE channel.

After the workbench switched from one-shot POST to `/chat` (SSE), cards come down the stream. This chain has two
pitfalls that only surface when "both sides are connected"; each side looks fine on its own:

1. Key name -- the backend once sent `tool_cards` while the client read `obj.cards`, and cards were silently dropped
2. Value -- recommendation tools use `ToolReturn` to split the return into two; cards must read `metadata`
   not `content` (`content` is the summary for the model and has no company list)

Run:
    venv/bin/python -m pytest tests/sales_agent/test_sse_cards.py -q
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.sales_agent import api as sales_api
from agents.sales_agent.api import _card_from_tool_return_part, _card_key, router
from geo.database import Base
from geo.models.user import UserORM

USER = 1000001
FRONTEND = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "salesAgentApi.ts"


# --- Value: cards read metadata, not the summary for the model ---

def _part(tool_name: str, content, metadata=None):
    from pydantic_ai.messages import ToolReturnPart
    return ToolReturnPart(tool_name=tool_name, content=content, tool_call_id="c1", metadata=metadata)


def test_card_prefers_the_full_metadata_view():
    keyed = _card_from_tool_return_part(_part(
        "recommend_companies",
        {"status": "ok", "actual_count": 2},                       # summary for the model
        {"status": "ok", "leads": [{"company": {"name": "某某科技"}}]},  # full data for the card
    ))
    assert keyed is not None
    assert keyed[1]["data"]["leads"][0]["company"]["name"] == "某某科技"


def test_card_falls_back_to_content_when_not_split():
    keyed = _card_from_tool_return_part(_part("update_lead_status", {"status": "ok", "lead_id": 7}))
    assert keyed is not None and keyed[1]["data"]["lead_id"] == 7


@pytest.mark.parametrize("tool,content", [
    ("remember_preference", {"ok": True}),   # not in CARD_TOOLS
    ("recommend_companies", "纯文本"),        # content isn't a dict
])
def test_non_card_returns_are_skipped(tool, content):
    assert _card_from_tool_return_part(_part(tool, content)) is None


def test_key_is_stable_for_the_same_card():
    a = _card_key("recommend_companies", {"b": 1, "a": 2})
    b = _card_key("recommend_companies", {"a": 2, "b": 1})
    assert a == b, "键随字典顺序变的话,收尾补发会把同一张卡再发一遍"


# --- Key name: what the backend sends and the frontend reads must match ---

def test_frontend_reads_the_key_backend_sends():
    ts = FRONTEND.read_text(encoding="utf-8")
    assert "obj.cards" in ts, "前端不再读 cards 了,后端那边要一起改"
    src = pathlib.Path(sales_api.__file__).read_text(encoding="utf-8")
    assert "{'cards':" in src, "SSE 不发 cards 帧了"
    assert "{'tool_cards':" not in src, "SSE 又发回 tool_cards —— 客户端读的是 obj.cards,会被静默丢掉"


# --- Actually run a stream ---

@pytest.fixture()
def client(monkeypatch):
    # TestClient sends requests from another thread; in-memory sqlite defaults to one DB per connection, so without pinning the pool
    # that side sees an empty DB (error: "no such table: users")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = UserORM(id=USER, email="sse@test.local", password_hash="x", is_active=True)
    db.add(user)
    db.commit()

    # The intent classifier would really call the model; this only tests the transport layer, so disable it and use keywords
    monkeypatch.setenv("SALES_AGENT_INTENT_LLM", "0")
    monkeypatch.setattr(sales_api, "resolve_sales_agent_model_config", lambda scene="chat": None)
    monkeypatch.setattr(sales_api, "model_config_fields", lambda cfg: {})

    tool_args = {
        "limit": 3, "service_scope": ["AI-GEO优化"], "industries": ["企业服务"],
        "regions": ["全国"], "company_size": "11-100 名员工", "typical_customers": ["国内企业"],
        "seed_companies": [
            {"name": "甲公司", "website": "https://jia.example.com", "industry": "企业服务", "region": "北京", "source": "manual"},
            {"name": "乙公司", "website": "https://yi.example.com", "industry": "企业服务", "region": "上海", "source": "manual"},
            {"name": "丙公司", "website": "https://bing.example.com", "industry": "企业服务", "region": "广州", "source": "manual"},
        ],
    }

    def fake_agent(scene: str = "chat"):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import DeltaToolCall, FunctionModel

        from agents.sales_agent.deps import SalesAgentDeps
        from agents.sales_agent.tools import recommend_companies

        # The SSE entry uses agent.iter() + node.stream(), so the model must support streaming: first round emits a tool call,
        # second round emits body deltas.
        async def stream_steps(messages, info):
            if len(messages) == 1:
                yield {0: DeltaToolCall(name="recommend_companies", json_args=json.dumps(tool_args), tool_call_id="c1")}
                return
            yield "已按当前筛选条件推荐 "
            yield "3 家企业。"

        return Agent(FunctionModel(stream_function=stream_steps), deps_type=SalesAgentDeps, tools=[recommend_companies])

    monkeypatch.setattr(sales_api, "build_sales_agent", fake_agent)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[sales_api.get_current_user] = lambda: user
    app.dependency_overrides[sales_api.get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        db.close()


def _frames(resp) -> list[dict]:
    out = []
    for chunk in resp.text.split("\n\n"):
        line = chunk.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


def test_model_path_streams_cards_before_done(client):
    """Cards must arrive before done, carrying the full company list."""
    resp = client.post("/api/sales-agent/chat", json={"message": "你们怎么收费", "new_session": True})
    assert resp.status_code == 200
    assert resp.headers.get("x-accel-buffering") == "no", "少了这个头,网关会把整条流缓冲成一次性返回"

    frames = _frames(resp)
    kinds = [next(iter(f)) for f in frames]
    assert "cards" in kinds, f"整条流里没有卡片帧:{kinds}"
    assert kinds.index("cards") < kinds.index("done")

    cards = [c for f in frames if "cards" in f for c in f["cards"]]
    assert len(cards) == 1
    assert cards[0]["tool"] == "recommend_companies"
    assert len(cards[0]["data"]["leads"]) == 3, "卡片拿到的是给模型的摘要,不是全量"
    assert [f["delta"] for f in frames if "delta" in f], "正文没有增量帧"


def test_deterministic_path_streams_cards_too(client):
    """The deterministic flow (no model call) uses the same frames: the find-customers entry emits the profile card."""
    resp = client.post("/api/sales-agent/chat", json={"message": "帮我找客户", "new_session": True})
    assert resp.status_code == 200
    frames = _frames(resp)
    cards = [c for f in frames if "cards" in f for c in f["cards"]]
    assert cards, "确定性流程没下发卡片"
    assert "seller_profile" in json.dumps(cards, ensure_ascii=False)
