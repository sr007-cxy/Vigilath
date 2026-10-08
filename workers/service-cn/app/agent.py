"""Worker agent loop - pull model.

When browser-service has DISPATCH_CENTER_URL configured, lifespan starts this background loop:
  detect egress IP -> register -> loop { heartbeat + claim a task -> run_hot -> POST the result }

The worker only needs outbound access to the center (works behind NAT/proxy). Concurrency is capped by max_concurrency; engines with an open circuit breaker are not claimed.
When the center disables/drains this worker, claim returns empty and the loop idles.

ENV:
- DISPATCH_CENTER_URL     dispatch center address (required to enable), e.g. https://dispatch.vigilath.com
- DISPATCH_TOKEN          shared auth token (same as the center)
- WORKER_UID              optional stable id (if omitted the center generates one; a restart becomes a new worker)
- WORKER_LABEL            optional display name
- WORKER_EXIT_IP          optional manually set egress IP (auto-detected if omitted)
- WORKER_HEARTBEAT_SEC    heartbeat interval in seconds (default 30)
- WORKER_POLL_SEC         claim polling interval in seconds (default 3)
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("browser-service.agent")

_IP_PROBES = ["https://api.ipify.org", "https://ifconfig.me/ip", "https://ipinfo.io/ip"]


async def _detect_exit_ip() -> str:
    override = os.environ.get("WORKER_EXIT_IP", "").strip()
    if override:
        return override
    async with httpx.AsyncClient(timeout=8) as client:
        for url in _IP_PROBES:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.text.strip():
                    return r.text.strip()
            except Exception:  # noqa: BLE001
                continue
    return ""


async def agent_loop(
    *,
    run_hot: Callable[[str, str], Awaitable[dict]],
    engines: list[str],
    max_concurrency: int,
    breaker_engines: Callable[[], set[str]],
    stop: asyncio.Event,
    hot_snapshots: Callable[[], list[dict]] | None = None,
) -> None:
    center = os.environ.get("DISPATCH_CENTER_URL", "").rstrip("/")
    if not center:
        return
    token = os.environ.get("DISPATCH_TOKEN", "").strip()
    headers = {"X-Dispatch-Token": token} if token else {}
    label = os.environ.get("WORKER_LABEL", "")
    region = os.environ.get("REGION", "cn")
    hb_sec = int(os.environ.get("WORKER_HEARTBEAT_SEC", "30"))
    poll_sec = int(os.environ.get("WORKER_POLL_SEC", "3"))
    hostname = socket.gethostname()
    exit_ip = await _detect_exit_ip()
    uid = os.environ.get("WORKER_UID", "").strip()

    sem = asyncio.Semaphore(max_concurrency)
    inflight = 0
    inflight_by_engine: dict[str, int] = {}

    async def _register(client: httpx.AsyncClient) -> str:
        body = {"worker_uid": uid or None, "hostname": hostname, "label": label,
                "region": region, "exit_ip": exit_ip, "engines": engines,
                "max_concurrency": max_concurrency, "version": "1"}
        r = await client.post(f"{center}/dispatch/register", json=body, headers=headers)
        r.raise_for_status()
        return r.json()["worker_uid"]

    async def _run_and_report(client: httpx.AsyncClient, task: dict, my_uid: str) -> None:
        nonlocal inflight
        eng = task["engine"]
        try:
            async with sem:
                res = await run_hot(eng, task["query"])
            payload = {"worker_uid": my_uid, "task_id": task["task_id"],
                       "kind": task.get("kind", "telemetry"),   # returned as-is; the center routes persistence by it
                       "answer": res.get("answer", ""), "citations": res.get("citations", []),
                       "source_url": res.get("source_url"), "video_url": res.get("video_url"),
                       "error": res.get("error"), "reasoning": res.get("reasoning"),
                       # Which account was used: the center stores it in response.session_id for per-account troubleshooting. Old centers ignore unknown fields.
                       "session_id": res.get("session_id"),
                       # Engine-side collection params/state (kimi's thinking / search_triggered / inline_refs ...)
                       "meta": res.get("meta")}
            await client.post(f"{center}/dispatch/result", json=payload, headers=headers)
        except Exception as e:  # noqa: BLE001
            log.warning("[agent] task %s run/report failed: %s", task.get("task_id"), e)
        finally:
            inflight -= 1
            inflight_by_engine[eng] = max(0, inflight_by_engine.get(eng, 0) - 1)

    # Register (retry until success / stop)
    async with httpx.AsyncClient(timeout=30) as client:
        while not stop.is_set():
            try:
                uid = await _register(client)
                log.info("[agent] registered uid=%s host=%s exit_ip=%s engines=%s",
                         uid, hostname, exit_ip, engines)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("[agent] register failed, retry in 10s: %s", e)
                await asyncio.sleep(10)

        last_hb = 0.0
        loop = asyncio.get_event_loop()
        while not stop.is_set():
            now = loop.time()
            # Heartbeat
            if now - last_hb >= hb_sec:
                try:
                    # hot_sessions: login session/rebuild/circuit-breaker state of hot sessions, so the center can see whether this machine's
                    # sessions are actually alive - previously this was only in local logs, and incidents required logging into the machine.
                    body = {
                        "worker_uid": uid, "in_flight": inflight, "exit_ip": exit_ip,
                        "breaker": {e: 1 for e in breaker_engines()},
                    }
                    if hot_snapshots is not None:
                        try:
                            body["hot_sessions"] = hot_snapshots()
                        except Exception:  # noqa: BLE001 - reporting is extra info and must not take down the heartbeat
                            pass
                    await client.post(f"{center}/dispatch/heartbeat", headers=headers, json=body)
                    last_hb = now
                except Exception as e:  # noqa: BLE001
                    log.warning("[agent] heartbeat failed: %s", e)
            # Claim tasks: each engine has only 1 serial hot session on the worker, so at most 1 task per engine
            # in flight - otherwise a slow/broken engine (e.g. yuanbao with an expired login) would fill the slots and starve the others.
            broken = breaker_engines()
            idle_engines = [e for e in engines
                            if e not in broken and inflight_by_engine.get(e, 0) == 0]
            for eng in idle_engines:
                if inflight >= max_concurrency:
                    break
                try:
                    r = await client.post(f"{center}/dispatch/claim", headers=headers, json={
                        "worker_uid": uid, "engines": [eng], "free_slots": 1,
                    })
                    r.raise_for_status()
                    tasks = r.json()
                except Exception as e:  # noqa: BLE001
                    log.warning("[agent] claim %s failed: %s", eng, e)
                    tasks = []
                for task in tasks:
                    inflight += 1
                    inflight_by_engine[eng] = inflight_by_engine.get(eng, 0) + 1
                    asyncio.create_task(_run_and_report(client, task, uid))
            await asyncio.sleep(poll_sec)
    log.info("[agent] loop stopped")
