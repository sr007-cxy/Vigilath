"""GEO 优化 Agent —— 独立 service(方案 B)。

为什么独立:pydantic-ai 需 pydantic>=2.11,而主后端钉死 fastapi==0.104.1 + pydantic==2.5.0
(不兼容)。本 service 跑在**独立 venv**(fastapi>=0.115 + pydantic>=2.11 + pydantic-ai,
见 requirements-agent.txt),与主后端隔离;共用同一 DB / SECRET_KEY / OPENROUTER_API_KEY。

运行:
    uvicorn geo.agent.service:app --host 127.0.0.1 --port 8010
nginx 把 /api/agent/* 反代到本 service(而非主后端)。
"""
from __future__ import annotations

from fastapi import FastAPI

from geo.agent.api import router as agent_router

app = FastAPI(title="Vigilath GEO Agent Service", version="0.1")
app.include_router(agent_router, prefix="/api", tags=["agent"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "geo-agent"}
