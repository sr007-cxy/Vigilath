"""Runner — 对单个 topic 跑一遍 (engine × query),写库.

外部依赖:browser-service `/search` (POST {engine, query})
失败粒度:engine × query 级别,单条挂了不影响其它,error 落到 response 行.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from .storage import (
    db_session, start_run, finish_run, save_response, parse_topic, TopicORM,
)

log = logging.getLogger(__name__)

BROWSER_SERVICE_CN = os.environ.get("BROWSER_SERVICE_CN", "http://localhost:8091")
BROWSER_SERVICE_GLOBAL = os.environ.get("BROWSER_SERVICE_GLOBAL", "http://localhost:8092")
PER_QUERY_TIMEOUT = int(os.environ.get("TELEMETRY_PER_QUERY_TIMEOUT", "180"))
MAX_CONCURRENT = int(os.environ.get("TELEMETRY_MAX_CONCURRENT", "3"))

CN_ENGINES = {"deepseek", "doubao", "qwen", "wenxin", "yuanbao"}


def _browser_url(engine: str) -> str:
    return BROWSER_SERVICE_CN if engine in CN_ENGINES else BROWSER_SERVICE_GLOBAL


async def _call_browser(client: httpx.AsyncClient, engine: str, query: str) -> dict[str, Any]:
    """调 browser-service /search,返回 {answer, citations, video_url, error}."""
    url = f"{_browser_url(engine)}/search"
    try:
        r = await client.post(url, json={"engine": engine, "query": query}, timeout=PER_QUERY_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "video_url": None, "error": str(e)}


async def run_topic_once(topic: TopicORM) -> None:
    """对一个 topic 把 (queries × engines) 全部跑完并落库.

    并发上限由 MAX_CONCURRENT 控制,失败单条记录到 response.error.
    """
    queries, engines = parse_topic(topic)
    topic_id = topic.id
    if not queries or not engines:
        log.warning("topic %d empty, skip", topic_id)
        return

    with db_session() as s:
        run = start_run(s, topic_id)
        run_id = run.id

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    failures = 0

    async with httpx.AsyncClient() as client:
        async def _one(engine: str, query: str) -> None:
            nonlocal failures
            async with sem:
                result = await _call_browser(client, engine, query)
            with db_session() as s:
                save_response(
                    s, run_id=run_id, topic_id=topic_id, engine=engine, query=query,
                    answer=result.get("answer", "") or "",
                    citations=result.get("citations") or [],
                    video_url=result.get("video_url"),
                    error=result.get("error"),
                )
            if result.get("error"):
                failures += 1

        tasks = [_one(e, q) for e in engines for q in queries]
        await asyncio.gather(*tasks)

    total = len(engines) * len(queries)
    status = "success" if failures == 0 else ("failed" if failures == total else "success")
    with db_session() as s:
        finish_run(s, run_id, status, error=None if failures == 0 else f"{failures}/{total} failed")
    log.info("topic %d run %d done: %d/%d ok", topic_id, run_id, total - failures, total)


async def run_preview(queries: list[str], engines: list[str]) -> list[dict[str, Any]]:
    """/run-now 同步预览 — 不写库,直接返回结果给前端 modal."""
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        async def _one(engine: str, query: str) -> None:
            async with sem:
                r = await _call_browser(client, engine, query)
            results.append({
                "engine": engine,
                "query": query,
                "answer": r.get("answer", "") or "",
                "citations": r.get("citations") or [],
                "error": r.get("error"),
            })

        tasks = [_one(e, q) for e in engines for q in queries]
        await asyncio.gather(*tasks)

    return results
