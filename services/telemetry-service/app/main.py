"""telemetry-service — AI 遥测的 daily cron + run-now 转发.

ENV:
- DATABASE_URL              backend 同库连接串
- BROWSER_SERVICE_CN        国内 5 引擎的 browser-service
- BROWSER_SERVICE_GLOBAL    海外 5 引擎的 browser-service
- TELEMETRY_PER_QUERY_TIMEOUT
- TELEMETRY_MAX_CONCURRENT

部署:uvicorn app.main:app --host 0.0.0.0 --port 8095 --workers 1
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .runner import run_preview, run_topic_once
from .scheduler import scheduler_loop
from .storage import db_session, TopicORM

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("telemetry-service")

_stop_event: Optional[asyncio.Event] = None
_scheduler_task: Optional[asyncio.Task] = None
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stop_event, _scheduler_task
    _stop_event = asyncio.Event()
    if SCHEDULER_ENABLED:
        _scheduler_task = asyncio.create_task(scheduler_loop(_stop_event))
        log.info("[start] scheduler launched")
    else:
        log.info("[start] scheduler disabled by env")
    yield
    if _stop_event:
        _stop_event.set()
    if _scheduler_task:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except asyncio.TimeoutError:
            _scheduler_task.cancel()
    log.info("[stop] shut down")


app = FastAPI(title="Telemetry Service", version="0.1.0", lifespan=lifespan)


# ── Schemas ────────────────────────────────────────────────────


class RunNowBody(BaseModel):
    name: str = ""
    queries: list[str] = Field(..., min_length=1, max_length=10)
    engines: list[str] = Field(..., min_length=1, max_length=10)
    user_id: Optional[int] = None


class RunNowItem(BaseModel):
    engine: str
    query: str
    answer: str = ""
    citations: list[dict] = []
    error: Optional[str] = None


class RunTopicBody(BaseModel):
    topic_id: int


# ── Endpoints ──────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "healthy", "scheduler": SCHEDULER_ENABLED}


@app.post("/run-now", response_model=list[RunNowItem])
async def http_run_now(body: RunNowBody):
    """同步跑批 — 不写库,直接把每条结果返给调用方(backend → FE modal)."""
    results = await run_preview(body.queries, body.engines)
    return results


@app.post("/run-topic")
async def http_run_topic(body: RunTopicBody):
    """运维 / 调度兜底入口 — 按 topic_id 立刻拉起一次正式跑批(写库)."""
    with db_session() as s:
        t = s.get(TopicORM, body.topic_id)
        if t is None:
            return {"status": "not_found"}
        s.expunge(t)
    await run_topic_once(t)
    return {"status": "ok", "topic_id": body.topic_id}
