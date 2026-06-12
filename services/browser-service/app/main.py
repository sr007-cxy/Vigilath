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
    FetchTitleRequest, FetchTitleResponse,
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

# D4 hot browser:engine_name → EngineSession.lazy 创建,首次 /search-hot 触发.
from .engine_session import EngineSession  # noqa: E402
_hot_sessions: dict[str, EngineSession] = {}
_hot_lock = asyncio.Lock()  # 保护 _hot_sessions dict


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

_agent_stop: Optional[asyncio.Event] = None
_agent_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_stop, _agent_task
    _load_adapters()
    print(f"[START] region={REGION} engines={list(_adapters.keys())} max_concurrent={MAX_CONCURRENT}")
    # 配了 DISPATCH_CENTER_URL → 启动 worker agent loop(pull 模型:自注册 + 领任务)
    if os.environ.get("DISPATCH_CENTER_URL", "").strip():
        from .agent import agent_loop
        _agent_stop = asyncio.Event()
        _agent_task = asyncio.create_task(agent_loop(
            run_hot=run_hot_query,
            engines=list(_adapters.keys()),
            max_concurrency=MAX_CONCURRENT,
            breaker_engines=_breaker_engines,
            stop=_agent_stop,
        ))
        print(f"[START] dispatch worker agent → {os.environ['DISPATCH_CENTER_URL']}")
    yield
    # 停 agent loop
    if _agent_stop:
        _agent_stop.set()
    if _agent_task:
        try:
            await asyncio.wait_for(_agent_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _agent_task.cancel()
    # D4:关掉所有 hot sessions
    for engine, sess in list(_hot_sessions.items()):
        try:
            await sess.close()
            print(f"[STOP] hot session {engine} closed")
        except Exception as e:
            print(f"[STOP] hot session {engine} close failed: {e}")
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


# fetch-title ─────────────────────────────────────────────────
# 给定任意 URL,用真实浏览器(带反检测)导航过去取页面标题.知乎等站点对裸 HTTP
# 请求返回 403,只有真实浏览器能拿到 <title>,所以引用标题回填统一走这里.
# 不依赖任何引擎登录态 — 用一次性 clean context.best-effort,抓不到返回空.

@app.post("/fetch-title", response_model=FetchTitleResponse)
async def fetch_title(req: FetchTitleRequest):
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="Too many concurrent queries")

    from .browser import create_stealth_page
    page = context = None
    try:
        page, context = await create_stealth_page("_title_probe")
        await page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout_ms)
        title = ""
        try:
            title = (await page.title()) or ""
        except Exception:  # noqa: BLE001
            title = ""
        if not title.strip():
            # 退到 og:title / 首个 h1
            try:
                title = (await page.evaluate(
                    "() => {"
                    "  const m = document.querySelector('meta[property=\"og:title\"], meta[name=\"og:title\"]');"
                    "  if (m && m.getAttribute('content')) return m.getAttribute('content');"
                    "  const h = document.querySelector('h1');"
                    "  return (h && h.innerText) || '';"
                    "}"
                )) or ""
            except Exception:  # noqa: BLE001
                pass
        return FetchTitleResponse(url=req.url, title=(title or "").strip()[:300])
    except Exception as e:  # noqa: BLE001
        return FetchTitleResponse(url=req.url, title="", error=str(e))
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
        _semaphore.release()


# D4 hot-browser endpoint ─────────────────────────────────────
# 同 /search,但走 EngineSession 复用 browser context.engine 必须实现
# _prepare_page + _query_with_page(目前仅 deepseek).返回相同 SearchResponse.

async def _get_hot_session(engine: str) -> EngineSession:
    """lazy 创建 + init EngineSession(线程/任务 safe).未适配 hot protocol 抛 NotImplementedError."""
    adapter = _adapters.get(engine)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {engine}")
    async with _hot_lock:
        sess = _hot_sessions.get(engine)
        if sess is None:
            sess = EngineSession(engine, adapter)
            _hot_sessions[engine] = sess
    if not sess._initialized:
        await sess.init()
    return sess


def _deepseek_http_sync(query: str) -> dict:
    """deepseek 纯 HTTP 路径(无浏览器):check-out 拿 token → HTTP 查询 → check-in。
    check-out / check-in / HTTP 全在同一线程,保证 session_store 的 thread-local 一致。"""
    from .session_store import load_storage_state, report_session_outcome
    from .deepseek_http import run_deepseek_http
    empty = {"engine": "deepseek", "query": query, "answer": "", "citations": [],
             "source_url": None, "video_url": None, "error": None}
    state = load_storage_state("deepseek")  # check-out(thread-local 记下 session id)
    if not state:
        return {**empty, "error": "no session available"}
    res = run_deepseek_http(state, query)
    err = res.get("error")
    # 映射成池子的 check-in 结果:login_lost→隔离;pow_failed→crash(不怪账号);empty→empty_answer
    if err == "login_lost":
        report_session_outcome("deepseek", result="login_lost")
    elif err == "empty_answer":
        report_session_outcome("deepseek", result="empty_answer")
    elif err:
        report_session_outcome("deepseek", result="crash", error_msg=err)
    else:
        report_session_outcome("deepseek", result="success")
    return {"engine": "deepseek", "query": query, "answer": res.get("answer", ""),
            "citations": res.get("citations", []), "source_url": None, "video_url": None,
            "error": err}


async def run_hot_query(engine: str, query: str) -> dict:
    """跑一条 hot query,返回 dispatch /result 需要的 dict 形状(供 /search-hot 与 worker agent 共用)。"""
    # deepseek 走纯 HTTP(无浏览器):DEEPSEEK_HTTP_MODE=1 时启用,结果与网页一致(含联网引用)
    from .deepseek_http import deepseek_http_enabled
    if engine == "deepseek" and deepseek_http_enabled():
        return await asyncio.to_thread(_deepseek_http_sync, query)

    search_timeout = int(os.environ.get("ENGINE_SEARCH_TIMEOUT", "300"))
    try:
        sess = await _get_hot_session(engine)
    except NotImplementedError as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"not hot-adapted: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"hot-session init failed: {e}"}
    try:
        result: EngineResult = await asyncio.wait_for(sess.query(query), timeout=search_timeout)
    except asyncio.TimeoutError:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"timeout ({search_timeout}s)"}
    except Exception as e:  # noqa: BLE001
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": str(e)}
    return {
        "engine": result.engine, "query": result.query, "answer": result.answer or "",
        "citations": [
            {"url": c.url, "domain": c.domain, "title": c.title,
             "snippet": c.snippet, "position": c.position}
            for c in (result.citations or [])
        ],
        "source_url": None, "video_url": None, "error": result.error,
    }


def _breaker_engines() -> set[str]:
    """当前处于 IP 熔断中的引擎(claim 时跳过,让中心把任务留给别的 worker)。"""
    now = time.time()
    return {e for e, s in _hot_sessions.items() if getattr(s, "_breaker_until", 0) > now}


@app.post("/search-hot", response_model=SearchResponse)
async def search_hot(req: SearchRequest):
    adapter = _adapters.get(req.engine)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {req.engine}")

    # deepseek 纯 HTTP 分支(与 worker agent 的 run_hot_query 一致)
    from .deepseek_http import deepseek_http_enabled
    if req.engine == "deepseek" and deepseek_http_enabled():
        d = await asyncio.to_thread(_deepseek_http_sync, req.query)
        return SearchResponse(
            engine="deepseek", query=req.query, answer=d.get("answer", ""),
            citations=[CitationOut(**c) for c in d.get("citations", [])],
            error=d.get("error"),
        )

    # lazy 创建 + init EngineSession(线程/任务 safe)
    async with _hot_lock:
        sess = _hot_sessions.get(req.engine)
        if sess is None:
            sess = EngineSession(req.engine, adapter)
            _hot_sessions[req.engine] = sess

    try:
        if not sess._initialized:
            await sess.init()  # 首次:launch + prepare
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        return SearchResponse(engine=req.engine, query=req.query, error=f"hot-session init failed: {e}")

    search_timeout = int(os.environ.get("ENGINE_SEARCH_TIMEOUT", "300"))
    start = time.time()
    try:
        result: EngineResult = await asyncio.wait_for(
            sess.query(req.query),
            timeout=search_timeout,
        )
        elapsed = time.time() - start
        print(
            f"[SEARCH-HOT] engine={req.engine} query={req.query[:30]!r} "
            f"ans_len={len(result.answer or '')} cites={len(result.citations or [])} "
            f"err={result.error!r} {elapsed:.1f}s q#{sess.query_count}"
        )
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
            video_url=None,  # hot 模式下视频暂不支持
        )
    except asyncio.TimeoutError:
        return SearchResponse(engine=req.engine, query=req.query, error=f"timeout ({search_timeout}s)")
    except Exception as e:
        return SearchResponse(engine=req.engine, query=req.query, error=str(e))


@app.get("/hot-sessions")
async def list_hot_sessions():
    """Debug: list active hot sessions + stats."""
    return [
        {
            "engine": eng,
            "initialized": s._initialized,
            "query_count": s.query_count,
            "last_used_at": s.last_used_at,
        }
        for eng, s in _hot_sessions.items()
    ]


@app.post("/hot-sessions/{engine}/rotate")
async def rotate_hot_session(engine: str, reason: str = "manual"):
    """Force rotate (close + reopen with fresh session)."""
    sess = _hot_sessions.get(engine)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"no hot session for engine={engine}")
    await sess.rotate(reason=reason)
    return {"engine": engine, "rotated": True}


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
