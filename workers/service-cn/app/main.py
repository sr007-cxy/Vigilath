"""Browser Engine Microservice.

ENV-driven: REGION, ENGINE_LIST, MAX_CONCURRENT_QUERIES control behavior.
Two instances (cn / global) run the same code with different ENV.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import signal
import sys
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
from .session_store import load_storage_state, save_storage_state, clear_session, peek_session_exists, _SESSION_DIR

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
    # Doubao mobile (external gateway only): reuses the Doubao adapter; search() hard-codes create_stealth_page("doubao")
    # -> automatically uses the Doubao phone-number account pool; same origin as doubao, just a separate engine id to force the browser path.
    "doubao_mobile": "app.engines.doubao_browser:DoubaoBrowserAdapter",
    # kimi: pure HTTP (Connect API), no browser but uses pool check-out/check-in, same as deepseek_http.
    # The adapter's search() delegates to the same HTTP path, so /search and /search-hot are equivalent for it.
    "kimi":     "app.kimi_http:KimiHttpAdapter",
}

ENGINE_DISPLAY_NAMES = {
    "deepseek": "DeepSeek", "doubao": "豆包", "qwen": "通义千问",
    "wenxin": "文心一言", "yuanbao": "元宝", "chatgpt": "ChatGPT",
    "claude": "Claude", "gemini": "Gemini", "kimi": "Kimi",
}

# ── Load adapters ──────────────────────────────────────────────

_adapters: dict[str, EngineAdapter] = {}
_semaphore: Optional[asyncio.Semaphore] = None

# D4 hot browser: engine_name -> EngineSession, created lazily, triggered by the first /search-hot.
from .engine_session import EngineSession  # noqa: E402
_hot_sessions: dict[str, EngineSession] = {}
_hot_lock = asyncio.Lock()  # protects the _hot_sessions dict


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
# Publish pool worker (server_pool channel). Fully independent of the engine agent: they claim from different queues
# with different concurrency units (engines per engine, publishing per account), and start/stop independently.
_publish_stop: Optional[asyncio.Event] = None
_publish_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_stop, _agent_task, _publish_stop, _publish_task
    _load_adapters()
    print(f"[START] region={REGION} engines={list(_adapters.keys())} max_concurrent={MAX_CONCURRENT}")
    # DISPATCH_CENTER_URL configured -> start the worker agent loop (pull model: self-register + claim tasks)
    if os.environ.get("DISPATCH_CENTER_URL", "").strip():
        from .agent import agent_loop
        _agent_stop = asyncio.Event()
        _agent_task = asyncio.create_task(agent_loop(
            run_hot=run_hot_query,
            engines=list(_adapters.keys()),
            max_concurrency=MAX_CONCURRENT,
            breaker_engines=_breaker_engines,
            stop=_agent_stop,
            hot_snapshots=lambda: [s.snapshot() for s in _hot_sessions.values()],
        ))
        print(f"[START] dispatch worker agent → {os.environ['DISPATCH_CENTER_URL']}")
    # PUBLISH_DISPATCH_URL configured -> start the publish pool worker (claim -> /publish -> report).
    # 🔴 Not started by default: this loop makes this process **publish externally**. To run it, the backend address must be configured explicitly,
    # and real publishing must also pass the ZHIHU_ALLOW_REAL_PUBLISH gate (publishers/zhihu.py).
    if os.environ.get("PUBLISH_DISPATCH_URL", "").strip():
        from .publish_loop import publish_loop, worker_id
        _publish_stop = asyncio.Event()
        _publish_task = asyncio.create_task(publish_loop(_publish_stop,
                                                         worker=worker_id()))
        print(f"[START] publish pool worker → {os.environ['PUBLISH_DISPATCH_URL']} "
              f"worker_id={worker_id()}")
    # Login session auto-renewal daemon (runs only on one designated worker to avoid multiple machines renewing the same account)
    _refresh_stop = None
    _refresh_task = None
    if os.environ.get("ENGINE_REFRESH_ENABLED", "").strip() == "1":
        from .refresh_daemon import refresh_loop
        _refresh_stop = asyncio.Event()
        _refresh_task = asyncio.create_task(refresh_loop(_refresh_stop))
        print("[START] 登录态自动续期守护已启动")
    app.state._refresh_stop = _refresh_stop
    app.state._refresh_task = _refresh_task
    yield
    # Stop the renewal daemon
    if getattr(app.state, "_refresh_stop", None):
        app.state._refresh_stop.set()
    # Stop the agent loop
    if _agent_stop:
        _agent_stop.set()
    if _agent_task:
        try:
            await asyncio.wait_for(_agent_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _agent_task.cancel()
    # Stop the publish pool worker. 🔴 The grace period is much longer than the engine agent's 5s: it may be stuck in the middle of an
    # **irreversible** publish (already clicked publish, waiting for the article URL). Cancelling after 5s would leave this article with neither
    # a published_url nor a receipt - the task stays claimed until the 30-minute lease expires and a fallback picks it up,
    # while the article may actually have gone out. Allow the full PUBLISH_TIMEOUT (default 240s) + margin for the receipt.
    if _publish_stop:
        _publish_stop.set()
    if _publish_task:
        grace = float(os.environ.get("PUBLISH_SHUTDOWN_GRACE_SEC", "300"))
        try:
            await asyncio.wait_for(_publish_task, timeout=grace)
        except asyncio.TimeoutError:
            print(f"[STOP] publish worker 超过 {grace}s 未收尾,强制取消 —— "
                  f"如有在途发布,去后端查 claimed 状态的 dispatch 并凭 draft_id 对账")
            _publish_task.cancel()
        except asyncio.CancelledError:
            _publish_task.cancel()
    # D4: close all hot sessions
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

# Publish channel (self-built account pool server_pool): POST /publish, /publish/verify-identity,
# GET /publish/screenshot/.... A separate module; see the module docstring of app/publish_api.py
# (which explains why it has its own token gate and semaphore while the engine routes do not).
from .publish_api import router as publish_router  # noqa: E402
app.include_router(publish_router, tags=["publish"])


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


def _file_md5(path: str) -> str:
    """First 12 chars of the file md5; returns "(unreadable)" if unreadable. For troubleshooting only, not used in any decision logic."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return "(unreadable)"


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
    # Routing switches. Added because "which code path does this machine actually take" could not be read from outside, yet it is exactly
    # the first question when troubleshooting: on 2026-09-08 while investigating widespread Tongyi failures, five machines had the same accounts and the same questions,
    # the test host worked and the other four did not - and QWEN_HOT_HTTP is **off** by default (see qwen_hot_http.py);
    # on, it takes the semi-browser HTTP path, off, it falls back to the old DOM flow - two completely different paths. The last three Tongyi fixes
    # were all on the hot http path, and machines without the switch got none of them. Back then the only way was logging into five machines to ps the env vars,
    # and the copy running under /opt was not even isomorphic with the repo copy (see dispatch center robustness plan P2-10).
    #
    # **This endpoint is publicly reachable via nginx** (/api/browser-cn/debug/env), so it only returns "on or off"
    # and never any credentials: proxy strings are redacted to scheme://host, usernames and passwords never appear - same
    # metric definition as 8fc9d3a9 "proxy credentials no longer go into logs".
    def _mask_proxy(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return "(not set)"
        try:
            from urllib.parse import urlsplit
            u = urlsplit(raw)
            if not u.scheme or not u.hostname:
                # urlsplit does not raise on malformed strings, it just returns empty fields - without this guard it would return
                # something like "://? (no credentials)", which is uninformative and looks like a bug.
                return "(set, 无法解析)"
            port = f":{u.port}" if u.port else ""
            auth = "有凭据" if (u.username or u.password) else "无凭据"
            return f"{u.scheme}://{u.hostname}{port}({auth})"
        except Exception:  # noqa: BLE001
            return "(set, 解析失败)"

    return {
        "DISPLAY": display or "(not set)",
        "xvfb_running": xvfb_running,
        "xvfb_details": xvfb_details.strip() or "(no xdpyinfo)",
        "MAX_CONCURRENT_QUERIES": MAX_CONCURRENT,
        "REGION": REGION,
        "ENGINE_LIST": ENGINE_LIST,
        # Routing switches (the values themselves are not sensitive, returned directly)
        "QWEN_HOT_HTTP": os.environ.get("QWEN_HOT_HTTP", "").strip() or "(not set)",
        "DEEPSEEK_HTTP_MODE": os.environ.get("DEEPSEEK_HTTP_MODE", "").strip() or "(not set)",
        "PROXY_ENGINES": os.environ.get("PROXY_ENGINES", "").strip() or "(not set)",
        "PROXY_URL": _mask_proxy(os.environ.get("PROXY_URL", "")),
        # Fingerprint of this code - answers "is this machine running the latest adapter version".
        # The running copy under /opt has diverged from the repo copy before (P2-10); the file md5 is the only reliable criterion.
        "qwen_hot_http_md5": _file_md5(os.path.join(os.path.dirname(__file__),
                                                    "qwen_hot_http.py")),
    }


@app.get("/engines", response_model=EnginesResponse)
async def engines():
    return EnginesResponse(region=REGION, engines=list(_adapters.keys()))


@app.post("/authorize")
async def authorize(req: dict):
    """Account authorization: automatic password login -> capture the login session into the pool. Internal call (admin console via backend proxy).
    body: {engine, identifier, password}. Returns {ok, error, account_handle, uploaded}."""
    engine = (req or {}).get("engine")
    identifier = (req or {}).get("identifier")
    password = (req or {}).get("password")
    if not (engine and identifier and password):
        raise HTTPException(status_code=400, detail="engine/identifier/password required")
    from .authorizer import authorize_password, supports_password
    if not supports_password(engine):
        raise HTTPException(status_code=400, detail=f"engine {engine} 不支持密码自动登录(走扫码/APIKey)")
    return await authorize_password(engine, identifier, password)


@app.post("/authorize/qr/start")
async def authorize_qr_start(req: dict):
    """QR-code assisted authorization - start: open the browser to the login page and capture the QR code. body {engine} -> {session_id, qr_image}."""
    engine = (req or {}).get("engine")
    from .qr_authorize import qr_start, supports_qr
    if not engine or not supports_qr(engine):
        raise HTTPException(status_code=400, detail=f"engine {engine} 不支持扫码授权")
    return await qr_start(engine)


@app.post("/authorize/qr/poll")
async def authorize_qr_poll(req: dict):
    """QR-code assisted authorization - poll: done (logged in, in the pool) / pending (may carry a new QR code) / expired."""
    sid = (req or {}).get("session_id")
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")
    from .qr_authorize import qr_poll
    return await qr_poll(sid)


@app.post("/authorize/sms/start")
async def authorize_sms_start(req: dict):
    """SMS code authorization - start: open the login page, fill in the phone number, click send code. body {engine, phone} -> {session_id, status, screenshot}."""
    engine = (req or {}).get("engine")
    phone = (req or {}).get("phone") or ""
    cc = str((req or {}).get("country_code") or "86")
    from .sms_authorize import sms_start, supports_sms
    if not engine or not supports_sms(engine):
        raise HTTPException(status_code=400, detail=f"engine {engine} 不支持手机验证码授权")
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    return await sms_start(engine, phone, cc)


@app.post("/authorize/sms/submit")
async def authorize_sms_submit(req: dict):
    """SMS code authorization - submit: fill in the code and click log in. body {session_id, code} -> done (in the pool) / failed (with screenshot) / expired."""
    sid = (req or {}).get("session_id")
    code = (req or {}).get("code") or ""
    if not sid or not code:
        raise HTTPException(status_code=400, detail="session_id/code required")
    from .sms_authorize import sms_submit
    return await sms_submit(sid, code)


@app.post("/authorize/sms/poll")
async def authorize_sms_poll(req: dict):
    """SMS code authorization - poll/cancel: body {session_id, cancel?}."""
    sid = (req or {}).get("session_id")
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")
    from .sms_authorize import sms_poll, sms_cancel
    if (req or {}).get("cancel"):
        return await sms_cancel(sid)
    return await sms_poll(sid)


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

    # Per-request overrides (workbench "engine live test"): recording switch + account pool; the ContextVar propagates with this coroutine
    # (and tasks/threads spawned by wait_for/to_thread), not bleeding between requests; None = old behavior.
    from .session_store import set_session_pool, reset_session_pool
    from .video_store import set_record_override, reset_record_override
    _tok_record = set_record_override(req.record_video)
    _tok_pool = set_session_pool(req.session_pool)

    # Global search timeout - must be > each engine's internal wait timeout (currently wenxin's internal
     # 180s typing wait + 30s stable double-check, plus goto/input/extract = total
    # may reach 240s). Default 300s, tunable via ENGINE_SEARCH_TIMEOUT.
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
            reasoning=getattr(result, "reasoning", None),
            citations=[
                CitationOut(
                    url=c.url, domain=c.domain, title=c.title,
                    snippet=c.snippet, position=c.position,
                )
                for c in (result.citations or [])
            ],
            error=result.error,
            video_url=video_url,
            meta=getattr(result, "meta", None),
        )
    except asyncio.TimeoutError:
        return SearchResponse(engine=req.engine, query=req.query, error=f"timeout ({search_timeout}s)")
    except Exception as e:
        return SearchResponse(engine=req.engine, query=req.query, error=str(e))
    finally:
        reset_record_override(_tok_record)
        reset_session_pool(_tok_pool)
        _semaphore.release()


# fetch-title ─────────────────────────────────────────────────
# Given any URL, navigate to it with a real browser (with anti-detection) to get the page title. Sites like Zhihu return 403 to bare HTTP
# requests; only a real browser gets the <title>, so citation title backfill always goes through here.
# Independent of any engine login session - uses a one-off clean context. Best-effort; returns empty if nothing is captured.

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
            # Fall back to og:title / the first h1
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
# Same as /search, but reuses the browser context via EngineSession. The engine must implement
# _prepare_page + _query_with_page (currently only deepseek). Returns the same SearchResponse.

async def _get_hot_session(engine: str) -> EngineSession:
    """Lazily create + init the EngineSession (thread/task safe). Raises NotImplementedError if the hot protocol is not supported."""
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


_exit_ip_cache: dict = {}          # proxy_key -> (ip, ts); egress IP cached per account sticky proxy key
_EXIT_IP_TTL_SEC = 3600


def _proxy_dict_to_url(p: dict | None) -> str | None:
    """_engine_proxy()'s {server,username,password} -> httpx proxy URL."""
    if not p:
        return None
    server = p.get("server", "")
    scheme, sep, hostport = server.partition("://")
    if not sep:
        scheme, hostport = "http", server
    return f"{scheme}://{p['username']}:{p['password']}@{hostport}"


# Egress IP detection endpoints (multi-endpoint fallback): ipify is reachable via overseas/JP proxies; ipip.net is reachable from China.
_IP_ECHO_URLS = ["https://api.ipify.org", "https://ipinfo.io/ip", "https://myip.ipip.net"]
_IP_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")


def _resolve_exit_ip(proxy_url: str | None) -> str | None:
    """Resolve the current egress IP (via proxy or local), cached for 1h per proxy_url. Best-effort.
    Multi-endpoint fallback: when ipify is unreachable from host (CN), fall back to ipip.net."""
    key = proxy_url or "__host__"
    now = time.time()
    hit = _exit_ip_cache.get(key)
    if hit and now - hit[1] < _EXIT_IP_TTL_SEC:
        return hit[0]
    import httpx
    ip = None
    for url in _IP_ECHO_URLS:
        try:
            with httpx.Client(timeout=8, proxy=proxy_url) as c:
                m = _IP_RE.search(c.get(url).text)
            if m:
                ip = m.group(1)
                break
        except Exception:  # noqa: BLE001
            continue
    if not ip:
        ip = hit[0] if hit else None
    if ip:
        _exit_ip_cache[key] = (ip, now)
    return ip


def _deepseek_http_sync(query: str, max_attempts: int = 6) -> dict:
    """deepseek pure HTTP path (no browser): check-out to get a token -> HTTP query -> check-in.
    check-out / check-in / HTTP all run in the same thread, keeping session_store's thread-local consistent.

    Failover: on login_lost / pow_failed, quarantine the bad account and retry with a new one (up to max_attempts times),
    instead of failing the whole query - one account losing its login does not affect this query (switch to another in the pool).
    """
    from .session_store import load_storage_state, report_session_outcome, get_last_checkout_id
    from .deepseek_http import run_deepseek_http
    from .browser import _engine_proxy
    empty = {"engine": "deepseek", "query": query, "answer": "", "citations": [],
             "source_url": None, "video_url": None, "error": None}
    err = None
    res: dict = {}
    session_id = None
    for attempt in range(max_attempts):
        state = load_storage_state("deepseek")  # check out again each time -> switch to a non-quarantined account
        if not state:
            return {**empty, "error": "no session available"}
        # Per-account sticky egress proxy (honors PROXY_ENGINES); the HTTP path previously did not pass the proxy,
        # so all accounts went out directly from the local single IP -> muted en masse by deepseek risk control. Now pinned per account.
        proxy_url = _proxy_dict_to_url(_engine_proxy("deepseek", state))
        from .session_store import session_user_agent
        # check-in pops the check-out slot, so record which account was used before that, to send back to the center with the result for persistence
        session_id = get_last_checkout_id("deepseek")
        res = run_deepseek_http(state, query, proxy=proxy_url, user_agent=session_user_agent("deepseek"))
        err = res.get("error")
        exit_ip = _resolve_exit_ip(proxy_url)   # the account's actual egress IP, reported with check-in and persisted
        # Map to the pool's check-in result: login_lost -> quarantine; pow_failed -> crash (not the account's fault); empty -> empty_answer
        if err == "login_lost":
            report_session_outcome("deepseek", result="login_lost", exit_ip=exit_ip)
        elif err == "rate_limited":
            # Account temporarily muted (deepseek risk control) -> cool the account until mute_until (no quarantine, no penalty), switch to the next
            report_session_outcome("deepseek", result="rate_limited",
                                   error_msg="muted", rate_limited_until=res.get("mute_until"),
                                   exit_ip=exit_ip)
        elif err == "empty_answer":
            report_session_outcome("deepseek", result="empty_answer", exit_ip=exit_ip)
        elif err:
            report_session_outcome("deepseek", result="crash", error_msg=err, exit_ip=exit_ip)
        else:
            report_session_outcome("deepseek", result="success", exit_ip=exit_ip)
        # Only switch and retry for "account/transient" errors. **rate_limited is not retried**: one account muted by deepseek
        # means this IP/moment is bad, and switching would only feed good accounts in to get muted too (one job wiping out several accounts).
        # When muted, break -> return rate_limited -> dispatch backs off per visible_after and retries later (spread over time).
        if err not in ("login_lost", "pow_failed"):
            break
    return {"engine": "deepseek", "query": query, "answer": res.get("answer", ""),
            "reasoning": res.get("reasoning"),
            "citations": res.get("citations", []), "source_url": None, "video_url": None,
            "error": err, "session_id": session_id}


_QWEN_QUERY_SCRIPT = os.path.join(os.path.dirname(__file__), "qwen_query.py")
_HEAL_PROBE_SCRIPT = os.path.join(os.path.dirname(__file__), "heal_probe.py")


class HealValidateIn(BaseModel):
    engine: str
    override: dict = {}
    query: str = "2026年最新国产手机推荐"


@app.post("/heal-validate")
async def heal_validate(req: HealValidateIn):
    """Run one canary with the proposed selectors as a temporary override, verifying it really yields an answer + citations (not persisted).
    Currently supports qwen (subprocess + QWEN_SELECTOR_OVERRIDE env). Returns {ans_len, cites, error}."""
    if req.engine != "qwen":
        return {"engine": req.engine, "error": "validate: only qwen supported", "ans_len": 0, "cites": 0}
    env = dict(os.environ)
    env["QWEN_SELECTOR_OVERRIDE"] = json.dumps(req.override, ensure_ascii=False)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, _QWEN_QUERY_SCRIPT, req.query,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=130)
        lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
        d = json.loads(lines[-1]) if lines else {}
        return {"engine": "qwen", "ans_len": len(d.get("answer") or ""),
                "cites": len(d.get("citations") or []), "error": d.get("error")}
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return {"engine": "qwen", "error": "validate timeout", "ans_len": 0, "cites": 0}
    except Exception as e:  # noqa: BLE001
        return {"engine": "qwen", "error": f"validate: {e}"[:200], "ans_len": 0, "cites": 0}


@app.post("/heal-probe")
async def heal_probe(req: SearchRequest):
    """Self-healing structure probe (browser-agent Phase 2): spawn heal_probe.py to dump the engine page structure
    (interactive elements/answer containers/network APIs) for the backend LLM to propose selectors. Read-only probe."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, _HEAL_PROBE_SCRIPT, req.engine,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ),
            start_new_session=True,   # same as above: kill by group on timeout, leaving no orphan browsers
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
        if not lines:
            return {"engine": req.engine, "error": "heal-probe: no output"}
        return json.loads(lines[-1])
    except asyncio.TimeoutError:
        _kill_process_tree(proc)
        return {"engine": req.engine, "error": "heal-probe timeout"}
    except Exception as e:  # noqa: BLE001
        return {"engine": req.engine, "error": f"heal-probe: {e}"[:200]}


def _kill_process_tree(proc) -> None:
    """Kill the browsers launched by the subprocess along with it.

    `proc.kill()` only kills the python wrapper process; the Chromium it spawned gets adopted by init and
    stays alive. With repeated qwen timeouts these orphans pile up - that is how the test host was driven to OOM on 2026-08-24
    (the kernel first killed the CI runner's node, then hours later chrome-headless), with
    4 chromium_headless_shell processes alive for two hours on the machine at the time.
    Together with start_new_session=True at spawn (the subprocess forms its own process group), killing by group here
    takes the browsers down too.
    """
    if proc is None or proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 - if the process group is gone/permission denied, fall back to killing only itself
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


async def _run_qwen_subprocess(query: str) -> dict:
    """Spawn a clean python process to run app/qwen_query.py (an in-process create_stealth_page conflicts with the resident
    hot browser context; a separate process is stable). Inherits this process's env and takes the last JSON line."""
    empty = {"engine": "qwen", "query": query, "answer": "", "citations": [],
             "source_url": None, "video_url": None, "error": None}
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, _QWEN_QUERY_SCRIPT, query,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ),
            start_new_session=True,   # its own process group, so on timeout the browsers it launched can be killed too
        )
        # 175s: on an empty answer qwen_query follows up in the same conversation (QWEN_TOTAL_BUDGET_SEC=165), so let it finish;
        # further up, telemetry uses 180s, and this must not exceed it.
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=int(os.environ.get("QWEN_SUBPROCESS_TIMEOUT", "175")))
        lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
        if not lines:
            return {**empty, "error": "qwen subprocess: no output"}
        d = json.loads(lines[-1])
        return {"engine": "qwen", "query": query, "answer": d.get("answer", ""),
                "reasoning": d.get("reasoning"),
                "citations": d.get("citations", []), "source_url": None,
                "video_url": None, "error": d.get("error"),
                "session_id": d.get("session_id")}
    except asyncio.TimeoutError:
        _kill_process_tree(proc)
        return {**empty, "error": "qwen subprocess timeout"}
    except Exception as e:  # noqa: BLE001
        return {**empty, "error": f"qwen subprocess: {e}"[:200]}


async def run_qwen_browser(query: str, max_attempts: int = 3) -> dict:
    """qwen browser query (run in a subprocess + failover). Empty answer/disconnect -> retry with another account (each subprocess checks out
    an account again), avoiding disconnected qwen accounts (QR-code login sessions often expire); same idea as deepseek failover."""
    d = {"engine": "qwen", "query": query, "answer": "", "citations": [],
         "source_url": None, "video_url": None, "error": "no attempt"}
    for _ in range(max_attempts):
        d = await _run_qwen_subprocess(query)
        if d.get("answer"):
            return d
        # Empty/disconnected/timeout -> switch accounts and retry
    return d


async def _run_doubao_oneshot(query: str) -> dict:
    """doubao_mobile one-off query: each query runs adapter.search() on a brand-new page (no hot session reuse)."""
    empty = {"engine": "doubao_mobile", "query": query, "answer": "", "citations": [],
             "source_url": None, "video_url": None, "error": None}
    adapter = _adapters.get("doubao_mobile") or _adapters.get("doubao")
    if not adapter:
        return {**empty, "error": "doubao_mobile adapter not loaded"}
    timeout = int(os.environ.get("ENGINE_SEARCH_TIMEOUT", "300"))
    try:
        r = await asyncio.wait_for(adapter.search(query), timeout=timeout)
    except asyncio.TimeoutError:
        return {**empty, "error": f"timeout ({timeout}s)"}
    except Exception as e:  # noqa: BLE001
        return {**empty, "error": str(e)[:200]}
    answer = r.answer or ""
    err = r.error
    # An empty answer is not success: otherwise the caller gets a "dirty done" with status=done but entirely empty content
    # (common when blanked by verify/risk control). Mark empty_answer -> dispatch judges it failed.
    if not answer and not err:
        err = "empty_answer"
    return {"engine": "doubao_mobile", "query": query, "answer": answer,
            "reasoning": getattr(r, "reasoning", None),
            "citations": [{"url": c.url, "domain": c.domain, "title": c.title,
                           "snippet": c.snippet, "position": c.position}
                          for c in (r.citations or [])],
            "source_url": None, "video_url": None, "error": err}


async def run_hot_query(engine: str, query: str) -> dict:
    """Run one hot query and return the dict shape needed by dispatch /result (shared by /search-hot and the worker agent)."""
    # deepseek uses pure HTTP (no browser): enabled with DEEPSEEK_HTTP_MODE=1, results match the web UI (including web citations)
    from .deepseek_http import deepseek_http_enabled
    if engine == "deepseek" and deepseek_http_enabled():
        return await asyncio.to_thread(_deepseek_http_sync, query)
    # kimi has only the pure HTTP path (Connect API + pool token), no browser fallback
    if engine == "kimi":
        from .kimi_http import run_kimi_pool_query
        return await asyncio.to_thread(run_kimi_pool_query, query)
    # qwen: with QWEN_HOT_HTTP=1 it uses semi-browser HTTP (fetch inside the hot page, signed by the site itself),
    # getting structured sources without relying on selectors; on failure it automatically falls back to the DOM flow below, guaranteeing no regression.
    if engine == "qwen":
        from .qwen_hot_http import hot_http_enabled, run_qwen_hot_http
        if hot_http_enabled():
            d = await run_qwen_hot_http(query)
            if d.get("answer"):
                return d
            print(f"[qwen] hot-http 未出答案({d.get('error')}),回退 DOM 流程", flush=True)
        return await run_qwen_browser(query)
    # doubao_mobile (the external gateway's Doubao mobile channel) uses one-off search(): a brand-new page per query,
    # no hot session reuse - when reusing the page, doubao's 「新对话」 ("New chat") reset often cannot find the button (page state changes after
    # one use / possible verify overlay), causing cascading "new-chat reset failed" crashes; a one-off
    # brand-new page is a new conversation every time, no reset needed, and as stable as the standalone tests.
    if engine == "doubao_mobile":
        return await _run_doubao_oneshot(query)

    search_timeout = int(os.environ.get("ENGINE_SEARCH_TIMEOUT", "300"))
    try:
        sess = await _get_hot_session(engine)
    except NotImplementedError as e:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"not hot-adapted: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"hot-session init failed: {e}"}
    # Which account the hot session used is sent back with the result (success or failure) and stored on the response at the center. After a failed query the session may
    # already have rotated (account switched), so it must be taken before the query to record the account actually used for this one.
    session_id = sess.session_id
    try:
        result: EngineResult = await asyncio.wait_for(sess.query(query), timeout=search_timeout)
    except asyncio.TimeoutError:
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": f"timeout ({search_timeout}s)",
                "session_id": session_id}
    except Exception as e:  # noqa: BLE001
        return {"engine": engine, "query": query, "answer": "", "citations": [],
                "source_url": None, "video_url": None, "error": str(e),
                "session_id": session_id}
    return {
        "engine": result.engine, "query": result.query, "answer": result.answer or "",
        "reasoning": getattr(result, "reasoning", None),
        "citations": [
            {"url": c.url, "domain": c.domain, "title": c.title,
             "snippet": c.snippet, "position": c.position}
            for c in (result.citations or [])
        ],
        "source_url": None, "video_url": None, "error": result.error,
        "session_id": session_id,
    }


def _breaker_engines() -> set[str]:
    """Engines currently under the IP circuit breaker (skipped at claim time so the center leaves the tasks to other workers)."""
    now = time.time()
    return {e for e, s in _hot_sessions.items() if getattr(s, "_breaker_until", 0) > now}


@app.post("/search-hot", response_model=SearchResponse)
async def search_hot(req: SearchRequest):
    adapter = _adapters.get(req.engine)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {req.engine}")

    # deepseek pure HTTP branch (same as the worker agent's run_hot_query)
    from .deepseek_http import deepseek_http_enabled
    if req.engine == "deepseek" and deepseek_http_enabled():
        d = await asyncio.to_thread(_deepseek_http_sync, req.query)
        return SearchResponse(
            engine="deepseek", query=req.query, answer=d.get("answer", ""),
            reasoning=d.get("reasoning"),
            citations=[CitationOut(**c) for c in d.get("citations", [])],
            error=d.get("error"),
        )
    # kimi pure HTTP branch (same as run_hot_query); meta carries collection params and state, stored by the center in meta_json
    if req.engine == "kimi":
        from .kimi_http import run_kimi_pool_query
        d = await asyncio.to_thread(run_kimi_pool_query, req.query)
        return SearchResponse(
            engine="kimi", query=req.query, answer=d.get("answer", ""),
            reasoning=d.get("reasoning"),
            citations=[CitationOut(**c) for c in d.get("citations", [])],
            error=d.get("error"), meta=d.get("meta"), session_id=d.get("session_id"),
        )
    # qwen uses the clean browser flow proven by the probe (bypassing the mismatched EngineSession path)
    if req.engine == "qwen":
        d = await run_qwen_browser(req.query)
        return SearchResponse(
            engine="qwen", query=req.query, answer=d.get("answer", ""),
            reasoning=d.get("reasoning"),
            citations=[CitationOut(**c) for c in d.get("citations", [])],
            error=d.get("error"),
        )

    # Lazily create + init the EngineSession (thread/task safe)
    async with _hot_lock:
        sess = _hot_sessions.get(req.engine)
        if sess is None:
            sess = EngineSession(req.engine, adapter)
            _hot_sessions[req.engine] = sess

    try:
        if not sess._initialized:
            await sess.init()  # first time: launch + prepare
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
            reasoning=getattr(result, "reasoning", None),
            citations=[
                CitationOut(
                    url=c.url, domain=c.domain, title=c.title,
                    snippet=c.snippet, position=c.position,
                )
                for c in (result.citations or [])
            ],
            error=result.error,
            video_url=None,  # video not supported in hot mode yet
        )
    except asyncio.TimeoutError:
        return SearchResponse(engine=req.engine, query=req.query, error=f"timeout ({search_timeout}s)")
    except Exception as e:
        return SearchResponse(engine=req.engine, query=req.query, error=str(e))


@app.get("/hot-sessions")
async def list_hot_sessions():
    """Debug: list active hot sessions + stats."""
    # snapshot() includes login session, rebuild count and breaker time remaining - the worker agent's heartbeat carries it to the center,
    # and the dashboard shows "whether this machine's hot sessions are actually alive" without logging into the machine for logs.
    return [s.snapshot() for s in _hot_sessions.values()]


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
        results.append(SessionInfo(
            engine=name,
            has_session=peek_session_exists(name),  # read-only, never check-out (status queries must not burn quota/leak leases)
            name=ENGINE_DISPLAY_NAMES.get(name, name),
        ))
    return results


@app.get("/sessions/{engine}", response_model=SessionInfo)
async def get_session(engine: str):
    if engine not in _adapters:
        raise HTTPException(status_code=404, detail=f"Unknown engine: {engine}")
    return SessionInfo(
        engine=engine,
        has_session=peek_session_exists(engine),  # read-only, never check-out
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
