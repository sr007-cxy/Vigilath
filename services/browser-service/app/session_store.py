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

# 身份绑定:check-out 时带本 worker 的稳定 id(由 WORKER_UID crc32),池子据此
# 把账号"粘"在固定 worker/出口 IP 上(sticky lease),降低 deepseek 等按 IP 风控。
# 未设 WORKER_UID(如手动调用)→ None,池子退回"最少被用"老策略。
import zlib as _zlib
_uid = (os.environ.get("WORKER_UID") or "").strip()
_WORKER_POOL_ID = (_zlib.crc32(_uid.encode("utf-8")) & 0x7FFFFFFF) if _uid else None

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


def _normalize_storage_state(state: Optional[dict]) -> Optional[dict]:
    """把 origins[].localStorage / sessionStorage 从对象 {k:v} 归一化成 Playwright 要求的
    数组 [{name,value}]。扩展导出的登录态常是 JS 原生对象格式,不归一化时 new_context 会报
    `storageState.origins[0].localStorage: expected array, got object`,导致整条引擎热会话起不来。
    防御性:已坏的会话加载即修,无需重新上传账号。"""
    if not isinstance(state, dict):
        return state
    for origin in state.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        for key in ("localStorage", "sessionStorage"):
            v = origin.get(key)
            if isinstance(v, dict):
                origin[key] = [{"name": str(k), "value": "" if val is None else str(val)}
                               for k, val in v.items()]
    return state


def _pool_checkout(engine_name: str) -> Optional[dict]:
    """Try to fetch a session from central pool. Returns storage_state dict or None.

    On success, stashes (engine, id) in thread-local so report_session_outcome
    knows which row to update.
    """
    if not _pool_enabled():
        return None
    try:
        params = {"engine": engine_name}
        if _WORKER_POOL_ID is not None:
            params["worker_id"] = _WORKER_POOL_ID
        with httpx.Client(timeout=_POOL_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_POOL_URL}/api/engine-sessions/check-out",
                params=params,
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
        # 出口标记(egress):None=按引擎默认 / 'proxy'=走代理 / 'host'=本机IP
        egslots = getattr(_last_checkout, "egress_by_engine", None)
        if egslots is None:
            egslots = {}
            _last_checkout.egress_by_engine = egslots
        egslots[engine_name] = data.get("egress")
        _log.info("[session-pool] engine=%s: checked out id=%s use_count=%s egress=%s source=%r",
                  engine_name, sid, data.get("use_count"), data.get("egress"), data.get("source_label"))
        return _normalize_storage_state(state)
    except Exception as e:
        _log.warning("[session-pool] engine=%s: check-out failed: %s", engine_name, e)
        return None


# D2(2026-05-18):check-in 升级为 enum FailureType 上报,后端按类型应用不同
# quarantine 政策。旧调用点 `report_session_outcome(engine, captcha_triggered=bool)`
# 被翻译成 SUCCESS / CAPTCHA。新调用建议直接传 result 字符串。
_VALID_RESULTS = {
    "success", "captcha", "empty_answer", "login_lost",
    "dom_not_found", "timeout", "crash", "rate_limited",
}


def report_session_outcome(
    engine_name: str,
    captcha_triggered: bool | None = None,
    *,
    result: str | None = None,
    error_msg: str | None = None,
    rate_limited_until: float | None = None,
    exit_ip: str | None = None,
) -> None:
    """Notify pool that the session we just checked out was used.

    Two equivalent calling styles:
      - 旧: report_session_outcome("doubao", captcha_triggered=True)
            → 翻译成 result="captcha"
      - 新: report_session_outcome("doubao", result="empty_answer",
                                    error_msg="answer was empty after 60s")

    No-op if pool isn't configured or no check-out was recorded for this engine
    in the current thread (e.g. we fell back to file). Safe to call always.
    """
    if not _pool_enabled():
        return
    slots = getattr(_last_checkout, "by_engine", None) or {}
    sid = slots.pop(engine_name, None)
    if sid is None:
        return

    # 调用点签名翻译
    if result is None:
        if captcha_triggered is True:
            result = "captcha"
        else:
            result = "success"
    if result not in _VALID_RESULTS:
        _log.warning("[session-pool] engine=%s: invalid result=%r, downgrading to 'crash'",
                     engine_name, result)
        result = "crash"

    body = {"id": sid, "result": result}
    if error_msg:
        body["error_msg"] = error_msg[:500]  # 截断防爆 log
    if rate_limited_until is not None:
        body["rate_limited_until"] = rate_limited_until  # 风控冷却到期(如 deepseek mute_until)
    if exit_ip:
        body["exit_ip"] = exit_ip  # 实际出口 IP(按 egress 代理解析)
    try:
        with httpx.Client(timeout=_POOL_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_POOL_URL}/api/engine-sessions/check-in",
                headers={"X-Service-Token": _POOL_TOKEN, "Content-Type": "application/json"},
                json=body,
            )
        if r.status_code != 200:
            _log.warning("[session-pool] engine=%s: check-in HTTP %d: %s",
                         engine_name, r.status_code, r.text[:200])
        else:
            _log.info("[session-pool] engine=%s id=%s checked in (result=%s) → %s",
                      engine_name, sid, result, r.json())
    except Exception as e:
        _log.warning("[session-pool] engine=%s: check-in failed: %s", engine_name, e)


def get_last_checkout_id(engine_name: str):
    """返回当前线程上次 check-out 的 session id;按账号粘定代理(sticky)时用作 session 标识。
    没从池子 check-out(走本地文件兜底)时返回 None。"""
    slots = getattr(_last_checkout, "by_engine", None) or {}
    return slots.get(engine_name)


def get_last_egress(engine_name: str):
    """返回当前线程上次 check-out 的出口标记:None=按引擎默认 / 'proxy' / 'host'。"""
    slots = getattr(_last_checkout, "egress_by_engine", None) or {}
    return slots.get(engine_name)


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
            return _normalize_storage_state(data)
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
