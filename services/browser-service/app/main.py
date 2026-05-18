"""Browser Engine Microservice.

ENV-driven: REGION, ENGINE_LIST, MAX_CONCURRENT_QUERIES control behavior.
Two instances (cn / global) run the same code with different ENV.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .engines.base import EngineAdapter, EngineResult, Citation
from .models import (
    SearchRequest, SearchResponse, CitationOut,
    SessionInfo, EnginesResponse, HealthResponse,
)
from .session_store import load_storage_state, save_storage_state, clear_session, _SESSION_DIR

# ── ENV configuration ──────────────────────────────────────────

REGION = os.environ.get("REGION", "cn")
ENGINE_LIST = [e.strip() for e in os.environ.get("ENGINE_LIST", "").split(",") if e.strip()]
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_QUERIES", "3"))
SEMAPHORE_TIMEOUT = int(os.environ.get("SEMAPHORE_TIMEOUT", "300"))

ENGINE_MODULE_MAP = {
    "deepseek": "app.engines.deepseek_browser:DeepSeekBrowserAdapter",
    "doubao":   "app.engines.doubao_browser:DoubaoBrowserAdapter",
    "qwen":     "app.engines.qwen_browser:QwenBrowserAdapter",
    "wenxin":   "app.engines.wenxin_browser:WenxinBrowserAdapter",
    "yuanbao":  "app.engines.yuanbao_browser:YuanbaoBrowserAdapter",
    "chatgpt":  "app.engines.chatgpt_browser:ChatGPTBrowserAdapter",
    "claude":   "app.engines.claude_browser:ClaudeBrowserAdapter",
    "gemini":   "app.engines.gemini_browser:GeminiBrowserAdapter",
    "grok":     "app.engines.grok_browser:GrokBrowserAdapter",
    "copilot":  "app.engines.copilot_browser:CopilotBrowserAdapter",
}

ENGINE_DISPLAY_NAMES = {
    "deepseek": "DeepSeek", "doubao": "豆包", "qwen": "通义千问",
    "wenxin": "文心一言", "yuanbao": "元宝", "chatgpt": "ChatGPT",
    "claude": "Claude", "gemini": "Gemini", "grok": "Grok",
    "copilot": "Copilot",
}

# ── Load adapters ──────────────────────────────────────────────

_adapters: dict[str, EngineAdapter] = {}
_semaphore: Optional[asyncio.Semaphore] = None


def _load_adapters() -> None:
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    for name in ENGINE_LIST:
        spec = ENGINE_MODULE_MAP.get(name)
        if not spec:
            print(f"[WARN] Unknown engine: {name}, skipping")
            continue
        module_path, class_name = spec.rsplit(":", 1)
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            _adapters[name] = cls()
            print(f"[OK] Loaded engine: {name} ({class_name})")
        except Exception as e:
            print(f"[ERROR] Failed to load {name}: {e}")


# ── Snapshot paths ─────────────────────────────────────────────

_SNAPSHOT_BASE = Path(os.environ.get(
    "SNAPSHOT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "snapshots"),
))


# ── App lifecycle ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_adapters()
    print(f"[START] region={REGION} engines={list(_adapters.keys())} max_concurrent={MAX_CONCURRENT}")
    yield
    # Cleanup: close browser if needed
    from .browser import close_browser
    await close_browser()
    print("[STOP] Browser service shut down")


app = FastAPI(title="Browser Service", version="0.1.0", lifespan=lifespan)


# ── API endpoints ──────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    from .browser import get_browser
    connected = False
    try:
        b = await get_browser()
        connected = b.is_connected()
    except Exception:
        pass
    return HealthResponse(
        status="healthy",
        browser_connected=connected,
        region=REGION,
        engine_count=len(_adapters),
    )


@app.get("/debug/env")
async def debug_env():
    """Diagnostic: return DISPLAY, Xvfb status, and key env vars."""
    import subprocess
    display = os.environ.get("DISPLAY", "")
    xvfb_running = False
    xvfb_details = ""
    if display:
        try:
            r = subprocess.run(["xdpyinfo", "-display", display],
                               capture_output=True, text=True, timeout=5)
            xvfb_running = r.returncode == 0
            xvfb_details = r.stdout[:200] if xvfb_running else r.stderr[:200]
        except Exception as e:
            xvfb_details = str(e)
    return {
        "DISPLAY": display or "(not set)",
        "xvfb_running": xvfb_running,
        "xvfb_details": xvfb_details.strip() or "(no xdpyinfo)",
        "MAX_CONCURRENT_QUERIES": MAX_CONCURRENT,
        "REGION": REGION,
        "ENGINE_LIST": ENGINE_LIST,
    }


@app.get("/engines", response_model=EnginesResponse)
async def engines():
    return EnginesResponse(region=REGION, engines=list(_adapters.keys()))


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    adapter = _adapters.get(req.engine)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {req.engine}")

    # Acquire semaphore with timeout
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="Too many concurrent queries")

    # 全局 search timeout — 必须 > 各 engine 内部 wait timeout(目前 wenxin 内部
     # 180s typing wait + 30s stable double-check,加上 goto/输入/extract = 总耗
    # 可能到 240s)。默认 300s,通过 ENGINE_SEARCH_TIMEOUT 可调。
    search_timeout = int(os.environ.get("ENGINE_SEARCH_TIMEOUT", "300"))
    start = time.time()
    try:
        result: EngineResult = await asyncio.wait_for(
            adapter.search(req.query),
            timeout=search_timeout,
        )
        elapsed = time.time() - start
        print(
            f"[SEARCH] engine={req.engine} query={req.query[:30]!r} "
            f"ans_len={len(result.answer or '')} cites={len(result.citations or [])} "
            f"err={result.error!r} {elapsed:.1f}s"
        )
        # Build video URL if recording was captured
        video_url = None
        if hasattr(result, "video_path") and result.video_path:
            from pathlib import PurePosixPath
            vpath = PurePosixPath(result.video_path)
            video_url = f"/snapshot/{req.engine}/{vpath.name}"
        return SearchResponse(
            engine=result.engine,
            query=result.query,
            answer=result.answer or "",
            citations=[
                CitationOut(
                    url=c.url, domain=c.domain, title=c.title,
                    snippet=c.snippet, position=c.position,
                )
                for c in (result.citations or [])
            ],
            error=result.error,
            video_url=video_url,
        )
    except asyncio.TimeoutError:
        return SearchResponse(engine=req.engine, query=req.query, error=f"timeout ({search_timeout}s)")
    except Exception as e:
        return SearchResponse(engine=req.engine, query=req.query, error=str(e))
    finally:
        _semaphore.release()


# ── Session management ─────────────────────────────────────────

@app.get("/sessions", response_model=list[SessionInfo])
async def list_sessions():
    results = []
    for name in _adapters:
        state = load_storage_state(name)
        results.append(SessionInfo(
            engine=name,
            has_session=bool(state),
            name=ENGINE_DISPLAY_NAMES.get(name, name),
        ))
    return results


@app.get("/sessions/{engine}", response_model=SessionInfo)
async def get_session(engine: str):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    state = load_storage_state(engine)
    return SessionInfo(
        engine=engine,
        has_session=bool(state),
        name=ENGINE_DISPLAY_NAMES.get(engine, engine),
    )


class SessionUpload(BaseModel):
    storage_state: dict
    profile: dict | None = None  # Fingerprint profile for CF-gated engines


@app.put("/sessions/{engine}")
async def upload_session(engine: str, body: SessionUpload):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    save_storage_state(engine, body.storage_state)
    if body.profile:
        from .session_store import save_engine_profile
        save_engine_profile(engine, body.profile)
    return {"status": "ok", "engine": engine, "profile_saved": bool(body.profile)}


@app.delete("/sessions/{engine}")
async def delete_session(engine: str):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    clear_session(engine)
    return {"status": "ok", "engine": engine}


# ── Snapshot download ──────────────────────────────────────────

@app.get("/snapshot/{engine}/{filename:path}")
async def download_snapshot(engine: str, filename: str):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    filepath = _SNAPSHOT_BASE / engine / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(
        path=str(filepath),
        media_type="video/webm",
        filename=filename,
    )
