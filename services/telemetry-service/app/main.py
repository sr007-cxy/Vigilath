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
from .insights import get_or_generate_cell_insight, update_feedback
from .briefings import generate_briefing_for_topic

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
    """触发一次正式跑批(写库),fire-and-forget — 立刻返回,后台跑.

    单 topic 真实耗时 5-40 分钟,HTTP 不能同步等;FE 调完后轮询 GET /topics/{id}/runs.
    """
    with db_session() as s:
        t = s.get(TopicORM, body.topic_id)
        if t is None:
            return {"status": "not_found"}
        s.expunge(t)

    async def _bg(topic):
        try:
            await run_topic_once(topic)
        except Exception as e:  # noqa: BLE001
            log.exception("background run failed for topic %d: %s", topic.id, e)

    asyncio.create_task(_bg(t))
    return {"status": "started", "topic_id": body.topic_id}


# ─── v1.1 cell insight (按需触发) ────────────────────────────


class CellInsightBody(BaseModel):
    topic_id: int
    query: str
    engine: str
    force: bool = False


@app.post("/cell-insight")
async def http_cell_insight(body: CellInsightBody):
    """drawer 首次打开 / 重新分析 — 同步返回(LLM 调用 3-8s)."""
    result = await asyncio.to_thread(
        get_or_generate_cell_insight,
        topic_id=body.topic_id, query=body.query, engine=body.engine, force=body.force,
    )
    if result is None:
        return {"status": "not_found"}
    return result


class FeedbackBody(BaseModel):
    insight_id: int
    feedback: str  # helpful / not_helpful / wrong


@app.post("/cell-insight/feedback")
async def http_cell_insight_feedback(body: FeedbackBody):
    ok = update_feedback(body.insight_id, body.feedback)
    return {"status": "ok" if ok else "failed"}


# ─── v1.2 briefing (按需 + scheduler 自动) ──────────────────


class BriefingTriggerBody(BaseModel):
    topic_id: int


@app.post("/briefing/generate")
async def http_generate_briefing(body: BriefingTriggerBody):
    """手动触发某 topic 上一周的周报生成(管理用 / scheduler 之外的补刀)."""
    result = await asyncio.to_thread(generate_briefing_for_topic, body.topic_id)
    if result is None:
        return {"status": "not_found"}
    return result
