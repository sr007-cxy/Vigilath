"""Session persistence for browser engine adapters.

Stores and retrieves full Playwright storage_state (cookies + localStorage)
per-engine so sessions survive across runs without requiring re-login.

Playwright storage_state format:
{
  "cookies": [...],
  "origins": [
    {
      "origin": "https://chat.deepseek.com",
      "localStorage": [{"name": "key", "value": "val"}, ...]
    }
  ]
}

**Pool mode (2026-05-18+)**:
  当配置了 ENGINE_SESSION_POOL_URL + ENGINE_SESSION_SERVICE_TOKEN 时,
  `load_storage_state(engine)` 优先从中央 pool check-out 一条 active session
  (`POST {url}/api/engine-sessions/check-out?engine=<engine>`),失败再退化到
  本地文件。check-out 返回的 session_id 由 `report_session_outcome(engine, ok)`
  通过 `POST .../check-in` 报回去 — 没挑 CAPTCHA → noop;挑了 → captcha_count
  +1, 达阈值自动 quarantine。

  对外接口不变(load_storage_state 还是返回 dict),所以 engine adapters
  不用改;但每个 adapter 应该在 search 结束时调一次
  `report_session_outcome(engine_name, captcha_triggered)` 让 pool 学习。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import local as _thread_local
from typing import Optional

import httpx


_log = logging.getLogger(__name__)


_SESSION_DIR = Path(os.environ.get(
    "BROWSER_SESSION_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "browser_sessions"),
))

# Pool config — 未设置就关闭 pool 模式,行为跟以前 100% 一致(只读本地文件)
_POOL_URL = (os.environ.get("ENGINE_SESSION_POOL_URL") or "").rstrip("/")
_POOL_TOKEN = (os.environ.get("ENGINE_SESSION_SERVICE_TOKEN") or "").strip()
_POOL_HTTP_TIMEOUT = float(os.environ.get("ENGINE_SESSION_POOL_TIMEOUT", "5"))

# 记录最近一次 check-out 的 session_id,供 report_session_outcome 用。
# 用 thread-local 是因为 browser-service 是 thread-pool 模型,每个并发 search
# 在自己的 thread 里。AsyncIO 单进程下也成立 — Playwright async 不会在
# load_storage_state ↔ report_session_outcome 之间切线程。
_last_checkout: _thread_local = _thread_local()


def _ensure_dir() -> None:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)


# ── Pool client ─────────────────────────────────────────────────────


def _pool_enabled() -> bool:
    return bool(_POOL_URL and _POOL_TOKEN)


def _pool_checkout(engine_name: str) -> Optional[dict]:
    """Try to fetch a session from central pool. Returns storage_state dict or None.

    On success, stashes (engine, id) in thread-local so report_session_outcome
    knows which row to update.
    """
    if not _pool_enabled():
        return None
    try:
        with httpx.Client(timeout=_POOL_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_POOL_URL}/api/engine-sessions/check-out",
                params={"engine": engine_name},
                headers={"X-Service-Token": _POOL_TOKEN},
            )
        if r.status_code == 404:
            _log.info("[session-pool] engine=%s: pool empty, fallback to local file", engine_name)
            return None
        if r.status_code != 200:
            _log.warning("[session-pool] engine=%s: check-out HTTP %d: %s",
                         engine_name, r.status_code, r.text[:200])
            return None
        data = r.json()
        sid = data.get("id")
        state = data.get("storage_state") or {}
        # Stash for later check-in (one slot per engine in thread-local)
        slots = getattr(_last_checkout, "by_engine", None)
        if slots is None:
            slots = {}
            _last_checkout.by_engine = slots
        slots[engine_name] = sid
        _log.info("[session-pool] engine=%s: checked out id=%s use_count=%s source=%r",
                  engine_name, sid, data.get("use_count"), data.get("source_label"))
        return state
    except Exception as e:
        _log.warning("[session-pool] engine=%s: check-out failed: %s", engine_name, e)
        return None


def report_session_outcome(engine_name: str, captcha_triggered: bool) -> None:
    """Notify pool that the session we just checked out was used.

    No-op if pool isn't configured or no check-out was recorded for this engine
    in the current thread (e.g. we fell back to file). Safe to call always.
    """
    if not _pool_enabled():
        return
    slots = getattr(_last_checkout, "by_engine", None) or {}
    sid = slots.pop(engine_name, None)
    if sid is None:
        return
    try:
        with httpx.Client(timeout=_POOL_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_POOL_URL}/api/engine-sessions/check-in",
                headers={"X-Service-Token": _POOL_TOKEN, "Content-Type": "application/json"},
                json={"id": sid, "captcha_triggered": captcha_triggered},
            )
        if r.status_code != 200:
            _log.warning("[session-pool] engine=%s: check-in HTTP %d: %s",
                         engine_name, r.status_code, r.text[:200])
        else:
            _log.info("[session-pool] engine=%s id=%s checked in (captcha=%s) → %s",
                      engine_name, sid, captcha_triggered, r.json())
    except Exception as e:
        _log.warning("[session-pool] engine=%s: check-in failed: %s", engine_name, e)


def save_storage_state(engine_name: str, storage_state: dict) -> None:
    """Persist full storage_state (cookies + localStorage) to disk."""
    _ensure_dir()
    path = _SESSION_DIR / f"{engine_name}.json"
    path.write_text(json.dumps(storage_state, ensure_ascii=False, indent=2))


def load_storage_state(engine_name: str) -> Optional[dict]:
    """Load storage_state.

    优先级:
      1. 中央 pool(配了 ENGINE_SESSION_POOL_URL + token 才启用)
      2. 本地文件(legacy / pool 空 / pool 不可达 时兜底)

    Handles both new format (full storage_state with origins) and legacy
    format (plain cookie list) for backward compatibility.
    """
    # Path 1: central pool
    pooled = _pool_checkout(engine_name)
    if pooled is not None:
        return pooled

    # Path 2: legacy local file
    path = _SESSION_DIR / f"{engine_name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        # New format: {"cookies": [...], "origins": [...]}
        if isinstance(data, dict) and "cookies" in data:
            return data
        # Legacy format: plain list of cookies
        if isinstance(data, list):
            return {"cookies": data, "origins": []}
        return None
    except (json.JSONDecodeError, OSError):
        return None


def clear_session(engine_name: str) -> None:
    """Remove saved session for the given engine."""
    path = _SESSION_DIR / f"{engine_name}.json"
    path.unlink(missing_ok=True)


# Backward-compatible aliases
def load_cookies(engine_name: str) -> Optional[list]:
    """Load cookies only (backward compat). Returns cookie list or None."""
    state = load_storage_state(engine_name)
    if state:
        return state.get("cookies")
    return None


def save_cookies(engine_name: str, cookies: list) -> None:
    """Save cookies only (backward compat). Preserves existing localStorage."""
    existing = load_storage_state(engine_name) or {}
    existing["cookies"] = cookies
    if "origins" not in existing:
        existing["origins"] = []
    save_storage_state(engine_name, existing)


# ── Profile persistence (for cross-machine fingerprint consistency) ──


def save_engine_profile(engine_name: str, profile: dict) -> None:
    """Save fingerprint profile alongside storage_state for cross-machine reuse.

    CF-gated engines (ChatGPT / Gemini / Grok) require the same UA/platform
    profile at login time and runtime. The profile dict is saved next to the
    storage_state JSON so they can be uploaded together.
    """
    _ensure_dir()
    path = _SESSION_DIR / f"{engine_name}_profile.json"
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


def load_engine_profile(engine_name: str) -> Optional[dict]:
    """Load previously saved fingerprint profile, or None if not found."""
    path = _SESSION_DIR / f"{engine_name}_profile.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
