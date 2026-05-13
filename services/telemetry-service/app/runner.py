"""Runner — 对单个 topic 跑一遍 (engine × query),写库 + 维护 QueryHit + 异步 LLM 抽取.

外部依赖:browser-service `/search` (POST {engine, query})
失败粒度:engine × query 级别,单条挂了不影响其它,error 落到 response 行.

流程(v1 + v1.1):
  1. start_run() / initialize_pending_cells() / mark_cells_running()
  2. 并发跑 N×M 个 (engine, query)
  3. 单条结果 → detect_hit() → save_response(hit=...) → update_query_hit_after_response()
  4. finish_run()
  5. fire-and-forget 异步 LLM 抽取:对本次所有 Response 抽 competitors / citation_domains / answer_format
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from .storage import (
    db_session, start_run, finish_run, save_response, parse_topic, parse_target,
    TopicORM, ResponseORM,
)
from .tracking import (
    detect_hit, initialize_pending_cells, mark_cells_running,
    update_query_hit_after_response,
)
from .llm import extract_response_insights
from .openrouter import call_openrouter, is_openrouter_engine

log = logging.getLogger(__name__)

BROWSER_SERVICE_CN = os.environ.get("BROWSER_SERVICE_CN", "http://localhost:8091")
BROWSER_SERVICE_GLOBAL = os.environ.get("BROWSER_SERVICE_GLOBAL", "http://localhost:8092")
PER_QUERY_TIMEOUT = int(os.environ.get("TELEMETRY_PER_QUERY_TIMEOUT", "180"))
MAX_CONCURRENT = int(os.environ.get("TELEMETRY_MAX_CONCURRENT", "3"))
EXTRACT_AFTER_RUN = os.environ.get("TELEMETRY_EXTRACT_AFTER_RUN", "1") == "1"

CN_ENGINES = {"deepseek", "doubao", "qwen", "wenxin", "yuanbao"}


def _browser_url(engine: str) -> str:
    return BROWSER_SERVICE_CN if engine in CN_ENGINES else BROWSER_SERVICE_GLOBAL


async def _call_browser(client: httpx.AsyncClient, engine: str, query: str) -> dict[str, Any]:
    """统一引擎调度入口:海外 5 引擎走 OpenRouter API,国内 5 引擎走 browser-service /search.

    返回 shape:{engine, query, answer, citations, video_url, source_url, error}
    历史命名保留(原只支持 browser),内部分发新增 API 路径.
    """
    if is_openrouter_engine(engine):
        return await call_openrouter(client, engine, query, timeout=PER_QUERY_TIMEOUT)
    url = f"{_browser_url(engine)}/search"
    try:
        r = await client.post(url, json={"engine": engine, "query": query}, timeout=PER_QUERY_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "video_url": None, "source_url": None, "error": str(e)}


async def run_topic_once(topic: TopicORM) -> None:
    """对一个 topic 把 (queries × engines) 全部跑完并落库.

    单 Response 落库时:detect_hit + update_query_hit_after_response 都在同一 session.
    Run 整体结束后:fire-and-forget LLM 抽取(EXTRACT_AFTER_RUN=1 时).
    """
    queries, engines = parse_topic(topic)
    target, aliases = parse_target(topic)
    topic_id = topic.id
    if not queries or not engines:
        log.warning("topic %d empty, skip", topic_id)
        return

    with db_session() as s:
        run = start_run(s, topic_id)
        run_id = run.id
        # 矩阵初始化(对新 query / engine 也要建 cell)
        # 这里 topic 是从外部 expunge 进来的副本,先 merge 再操作
        t_managed = s.merge(topic)
        initialize_pending_cells(s, t_managed)
        mark_cells_running(s, t_managed)

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    failures = 0
    response_ids: list[int] = []

    async with httpx.AsyncClient() as client:
        async def _one(engine: str, query: str) -> None:
            nonlocal failures
            async with sem:
                result = await _call_browser(client, engine, query)
            answer = result.get("answer", "") or ""
            error = result.get("error")

            # 命中判定 — 失败的 response 不判定(answer 是空)
            hit, excerpt = (False, None)
            if not error:
                hit, excerpt = detect_hit(answer, target, aliases)

            with db_session() as s:
                r = save_response(
                    s, run_id=run_id, topic_id=topic_id, engine=engine, query=query,
                    answer=answer,
                    citations=result.get("citations") or [],
                    video_url=result.get("video_url"),
                    source_url=result.get("source_url"),
                    error=error,
                    hit=hit,
                    hit_excerpt=excerpt,
                )
                rid = r.id
                # 维护矩阵 cell
                if not error:
                    t_managed = s.get(TopicORM, topic_id)
                    if t_managed is not None:
                        update_query_hit_after_response(s, response=r, topic=t_managed)

            response_ids.append(rid)
            if error:
                failures += 1

        tasks = [_one(e, q) for e in engines for q in queries]
        await asyncio.gather(*tasks)

    total = len(engines) * len(queries)
    status = "success" if failures < total else "failed"
    with db_session() as s:
        finish_run(s, run_id, status, error=None if failures == 0 else f"{failures}/{total} failed")
    log.info("topic %d run %d done: %d/%d ok", topic_id, run_id, total - failures, total)

    # v1.1 fire-and-forget LLM 抽取(不阻塞返回)
    if EXTRACT_AFTER_RUN and response_ids:
        asyncio.create_task(_extract_responses_bg(topic_id, target, aliases, response_ids))


async def _extract_responses_bg(
    topic_id: int, target: str, aliases: list[str], response_ids: list[int],
) -> None:
    """v1.1 — 异步给每条新 Response 调 LLM 抽 competitors / answer_format / citation_domains."""
    try:
        await asyncio.sleep(0.1)
        for rid in response_ids:
            try:
                await asyncio.to_thread(_extract_one, target, rid)
            except Exception as e:  # noqa: BLE001
                log.warning("extract failed for response %d: %s", rid, e)
    except Exception as e:  # noqa: BLE001
        log.exception("extract bg task crashed: %s", e)


def _extract_one(target: str, response_id: int) -> None:
    """同步抽一条 — 在 thread 里跑,避免阻塞 asyncio loop."""
    with db_session() as s:
        r = s.get(ResponseORM, response_id)
        if r is None or r.error:
            return
        if r.competitors_json:
            return  # 已抽过
        citations: list[dict] = []
        try:
            citations = json.loads(r.citations_json or "[]")
        except Exception:  # noqa: BLE001
            pass
        result = extract_response_insights(
            target=target, query=r.query, engine=r.engine,
            answer=r.answer or "", citations=citations,
        )
        if not result:
            return
        comps = result.get("competitors") or []
        # citation_domains 兜底:LLM 没给 → 从 citations 自抽
        cd = result.get("citation_domains") or _domains_from_citations(citations)
        r.competitors_json = json.dumps(comps, ensure_ascii=False)
        r.citation_domains_json = json.dumps(cd, ensure_ascii=False)
        r.answer_format = result.get("answer_format") or None
        # mention_position 兜底:LLM 没给 → 用 hit_excerpt 在原文里的位置算 lead/body/tail
        r.mention_position = result.get("mention_position") or _fallback_position(r.answer, r.hit_excerpt)


def _fallback_position(answer: str | None, hit_excerpt: str | None) -> str | None:
    """LLM 不可用时,按 hit_excerpt 在答复全文里的字符位置粗判 lead/body/tail.

    用 excerpt 去 strip 两端「…」,在 answer 里 find 首字符位置;前 30% lead,
    后 30% tail,中间 body.无法匹配返回 None.
    """
    if not answer or not hit_excerpt:
        return None
    needle = hit_excerpt.strip("…").strip()
    if not needle or not answer:
        return None
    i = answer.find(needle[:20]) if len(needle) >= 20 else answer.find(needle)
    if i < 0:
        return None
    pct = i / max(1, len(answer))
    if pct < 0.30:
        return "lead"
    if pct > 0.70:
        return "tail"
    return "body"


def _domains_from_citations(citations: list[dict]) -> list[str]:
    """从 citations 抽顶级域名 — LLM 没拿到时的兜底."""
    out = []
    seen = set()
    for c in citations or []:
        d = (c.get("domain") or "").strip().lower()
        if not d and c.get("url"):
            try:
                d = (urlparse(c["url"]).hostname or "").lower()
            except Exception:  # noqa: BLE001
                pass
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


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
