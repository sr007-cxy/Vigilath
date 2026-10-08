"""GEO optimization Agent — standalone service (Option B).

Why standalone: pydantic-ai needs pydantic>=2.11, while the main backend pins fastapi==0.104.1 + pydantic==2.5.0
(incompatible). This service runs in a **separate venv** (fastapi>=0.115 + pydantic>=2.11 + pydantic-ai,
see requirements-agent.txt), isolated from the main backend; it shares the same DB / SECRET_KEY / OPENROUTER_API_KEY.

Run:
    uvicorn agents.geo.service:app --host 127.0.0.1 --port 8010
nginx reverse-proxies /api/agent/* to this service (not the main backend).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.geo.api import router as agent_router
from agents.geo.embed.api import router as embed_router
from agents.geo.embed.device import router as device_router
from agents.geo.embed.wallet import router as wallet_router
from geo.api.skill_install import router as skill_install_router
from agents.geo.embed.im_feishu import router as im_feishu_router
from agents.geo.embed.feishu_isv import router as feishu_isv_router
from agents.geo.embed.im_wecom import router as im_wecom_router
from agents.geo.embed.im_wecom_isv import router as im_wecom_isv_router
from agents.geo.embed.onboard import router as onboard_router
from agents.geo.embed.wecom_oauth import router as wecom_oauth_router
from agents.geo.embed.im_dingtalk import router as im_dingtalk_router
from agents.sales_agent.api import router as sales_agent_router

app = FastAPI(title="Vigilath GEO Agent Service", version="0.1")

# External /api/agent/v1/* uses Bearer tokens (not cookies), so CORS with "*" and no credentials is safe.
# A fine-grained Origin allowlist for direct browser use is checked separately at the token layer (agent_tokens.origins).
_origins = [o.strip() for o in (os.environ.get("AGENT_EMBED_ORIGINS") or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router, prefix="/api", tags=["agent"])          # Internal: /api/agent/* (login JWT)
app.include_router(sales_agent_router, prefix="/api", tags=["sales-agent"])  # Internal: /api/sales-agent/* (login JWT)
app.include_router(embed_router, prefix="/api", tags=["agent-embed"])    # External: /api/agent/v1/* (embed token)
app.include_router(device_router, prefix="/api", tags=["agent-device"])
app.include_router(wallet_router, prefix="/api", tags=["agent-wallet"])  # Balance + agent direct-pay top-up  # External, no auth: /api/agent/v1/device/* (device code authorization)
# No prefix: GET /skill (content negotiation). Mounted here rather than only on the main backend — the agent service port is 8010 on both sites,
# while the main backend is test host 8000 / prod 8070, so one nginx snippet can't serve both sites.
app.include_router(skill_install_router, tags=["skill-install"])
app.include_router(im_feishu_router, prefix="/api", tags=["agent-im"])   # IM self-built: /api/agent/im/feishu/callback
app.include_router(feishu_isv_router, prefix="/api", tags=["agent-im-isv"])  # IM ISV:/api/agent/im/feishu-isv/callback
app.include_router(im_wecom_router, prefix="/api", tags=["agent-im-wecom"])  # IM WeCom self-built: /api/agent/im/wecom/callback
app.include_router(im_wecom_isv_router, prefix="/api", tags=["agent-im-wecom-isv"])  # IM WeCom third-party: /api/agent/im/wecom-isv/{cmd,data}
app.include_router(onboard_router, prefix="/api", tags=["agent-im-onboard"])  # IM acquisition: /api/agent/im/onboard/bind
app.include_router(wecom_oauth_router, prefix="/api", tags=["agent-im-wecom-oauth"])  # WeCom silent login: /api/agent/im/wecom-oauth/*
app.include_router(im_dingtalk_router, prefix="/api", tags=["agent-im-dingtalk"])  # IM DingTalk: /api/agent/im/dingtalk/callback


@app.get("/health")
def health():
    """Health check — **must really verify once that the agent can be built**, not just reply ok.

    Learned in CN production on 2026-08-24: container healthy, /health 200, yet every /api/agent/chat returned 503.
    The real cause was openai in the image drifting to 3.x (on import it references aiohttp.SocketTimeoutError,
    which aiohttp 3.9.1 lacks), so build_agent raised outright. The old /health was a hard-coded dict
    that never touched pydantic-ai, so:
      · docker healthcheck passed -> container marked healthy
      · the deploy script's smoke test only checks "healthy + /health 200" -> passed -> **no auto-rollback**
    A completely unusable image went live that way, looking perfectly normal.

    build_agent has lru_cache, so only the first call really builds; later health calls cost almost nothing.
    Return 503 when it can't be built: both the healthcheck and the smoke test catch it, and the bad image rolls back on the spot.
    """
    from fastapi.responses import JSONResponse

    try:
        from agents.geo.agent import build_agent
        build_agent("chat", "geo")
    except Exception as e:  # noqa: BLE001 — any build failure counts as unavailable
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": "geo-agent",
                     # Only the class name + a short message: this endpoint binds only to 127.0.0.1, but there's no need to echo the full config
                     "agent": f"{type(e).__name__}: {str(e)[:160]}"},
        )
    return {"status": "ok", "service": "geo-agent", "agent": "ok"}
