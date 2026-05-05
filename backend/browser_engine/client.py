"""Unified HTTP client for browser engine microservice.

Routes engine queries to the correct browser-service instance
(cn on 103, global on 101) based on engine name.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

# ── Service URLs ───────────────────────────────────────────────

BROWSER_CN_URL = os.environ.get("BROWSER_CN_URL", "http://localhost:8092")
BROWSER_GLOBAL_URL = os.environ.get("BROWSER_GLOBAL_URL", "http://172.80.40.101:8091")

ENGINE_ROUTING = {
    # CN — domestic engines
    "deepseek": "cn",
    "doubao":   "cn",
    "qwen":     "cn",
    "wenxin":   "cn",
    "yuanbao":  "cn",
    # Global — international engines
    "chatgpt":  "global",
    "claude":   "global",
    "gemini":   "global",
    "grok":     "global",
    "copilot":  "global",
}

_TIMEOUT = 180.0
_SHORT_TIMEOUT = 10.0


def _base_url(engine: str) -> str:
    region = ENGINE_ROUTING.get(engine, "cn")
    return BROWSER_GLOBAL_URL if region == "global" else BROWSER_CN_URL


# ── Search ─────────────────────────────────────────────────────

def search(engine: str, query: str) -> dict:
    """Sync search — call from thread pool."""
    base = _base_url(engine)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{base}/search", json={"engine": engine, "query": query})
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": "browser service unavailable"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return {"engine": engine, "query": query, "answer": "", "citations": [], "error": "browser service busy (429)"}
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": f"browser service error: {e}"}
    except Exception as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": str(e)}


async def search_async(engine: str, query: str) -> dict:
    """Async search — call from async context."""
    base = _base_url(engine)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{base}/search", json={"engine": engine, "query": query})
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": "browser service unavailable"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return {"engine": engine, "query": query, "answer": "", "citations": [], "error": "browser service busy (429)"}
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": f"browser service error: {e}"}
    except Exception as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [], "error": str(e)}


# ── Session status ─────────────────────────────────────────────

def has_session(engine: str) -> bool:
    """Check if a single engine has a valid session."""
    base = _base_url(engine)
    try:
        with httpx.Client(timeout=_SHORT_TIMEOUT) as client:
            resp = client.get(f"{base}/sessions/{engine}")
            if resp.status_code == 200:
                return resp.json().get("has_session", False)
    except Exception:
        pass
    return False


async def has_session_async(engine: str) -> bool:
    base = _base_url(engine)
    try:
        async with httpx.AsyncClient(timeout=_SHORT_TIMEOUT) as client:
            resp = await client.get(f"{base}/sessions/{engine}")
            if resp.status_code == 200:
                return resp.json().get("has_session", False)
    except Exception:
        pass
    return False


def get_sessions() -> list[dict]:
    """Get all sessions from both service instances."""
    result = []
    for url in [BROWSER_CN_URL, BROWSER_GLOBAL_URL]:
        try:
            with httpx.Client(timeout=_SHORT_TIMEOUT) as client:
                resp = client.get(f"{url}/sessions")
                if resp.status_code == 200:
                    result.extend(resp.json())
        except Exception:
            pass
    return result


async def get_sessions_async() -> list[dict]:
    """Async version — fetches from both instances concurrently."""
    result = []
    async with httpx.AsyncClient(timeout=_SHORT_TIMEOUT) as client:
        for url in [BROWSER_CN_URL, BROWSER_GLOBAL_URL]:
            try:
                resp = await client.get(f"{url}/sessions")
                if resp.status_code == 200:
                    result.extend(resp.json())
            except Exception:
                pass
    return result


# ── Snapshot proxy helper ──────────────────────────────────────

def get_snapshot_url(engine: str, filename: str) -> str:
    """Return the full URL for a snapshot file on the browser service."""
    base = _base_url(engine)
    return f"{base}/snapshot/{engine}/{filename}"
