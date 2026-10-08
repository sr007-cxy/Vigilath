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
  When ENGINE_SESSION_POOL_URL + ENGINE_SESSION_SERVICE_TOKEN are configured,
  `load_storage_state(engine)` first checks out an active session from the central pool
  (`POST {url}/api/engine-sessions/check-out?engine=<engine>`), degrading to the
  local file on failure. The session_id returned by check-out is reported back by `report_session_outcome(engine, ok)`
  via `POST .../check-in` - no CAPTCHA -> noop; CAPTCHA -> captcha_count
  +1, automatically quarantined at the threshold.

  The public interface is unchanged (load_storage_state still returns a dict), so engine adapters
  need no changes; but each adapter should call
  `report_session_outcome(engine_name, captcha_triggered)` once at the end of search so the pool learns.
"""

from __future__ import annotations

import json
import logging
import os
import contextvars
from pathlib import Path
from typing import Optional

import httpx


_log = logging.getLogger(__name__)


_SESSION_DIR = Path(os.environ.get(
    "BROWSER_SESSION_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "browser_sessions"),
))

# Pool config - when unset, pool mode is off and behavior is 100% the same as before (local files only)
_POOL_URL = (os.environ.get("ENGINE_SESSION_POOL_URL") or "").rstrip("/")
_POOL_TOKEN = (os.environ.get("ENGINE_SESSION_SERVICE_TOKEN") or "").strip()
_POOL_HTTP_TIMEOUT = float(os.environ.get("ENGINE_SESSION_POOL_TIMEOUT", "5"))
# Explicit pool mode (2026-07, for machines like the browser host where "old flows use local files"): when set to 1, only requests
# that explicitly specify session_pool (workbench engine live test) use central pool check-out; old calls without it
# keep reading local files - layering pool capability without changing existing /search behavior.
_POOL_EXPLICIT_ONLY = (os.environ.get("ENGINE_SESSION_POOL_EXPLICIT_ONLY") or "").strip() in ("1", "true")

# Identity binding: check-out carries this worker's stable id (crc32 of WORKER_UID), which the pool uses
# to "stick" accounts to a fixed worker/egress IP (sticky lease), reducing per-IP risk control by deepseek etc.
# WORKER_UID unset (e.g. manual calls) -> None, and the pool falls back to the old "least used" strategy.
import zlib as _zlib
_uid = (os.environ.get("WORKER_UID") or "").strip()
_WORKER_POOL_ID = (_zlib.crc32(_uid.encode("utf-8")) & 0x7FFFFFFF) if _uid else None

# Records the session_id / egress / proxy_override of the latest check-out, for _engine_proxy and
# report_session_outcome. **Must use contextvars, not threading.local**: browser-service
# runs concurrent coroutines on a single event loop (asyncio.Semaphore), with multiple search coroutines running concurrently in one thread,
# and threading.local is shared between them -> accounts get mixed up (account A's proxy/report misassigned to account B, leaked leases,
# and broken 1-account-1-IP for proxy_override). ContextVar is isolated per coroutine/task; the asyncio.to_thread used by deepseek
# also copies the context, so each thread is independent.
_checkout_ctx: contextvars.ContextVar = contextvars.ContextVar("engine_checkout", default=None)

# Per-request account pool selection (2026-07, workbench "engine live test"): when /search carries a session_pool field
# this variable is set, and _pool_checkout sends it as the pool parameter to backend check-out, claiming only from that pool;
# None = no parameter sent, the backend defaults to the default pool (sentiment monitoring), old callers' behavior completely unchanged.
_pool_name_ctx: contextvars.ContextVar = contextvars.ContextVar("session_pool_name", default=None)


def set_session_pool(name: Optional[str]):
    """Set the account pool for the current request; returns a token for reset in finally."""
    return _pool_name_ctx.set(name)


def reset_session_pool(token) -> None:
    _pool_name_ctx.reset(token)


def _ctx_slots() -> dict:
    """Get the check-out slots private to the current context (coroutine/thread); created on first access, isolated per context."""
    d = _checkout_ctx.get()
    if d is None:
        d = {"id": {}, "egress": {}, "proxy": {}}
        _checkout_ctx.set(d)
    return d


def _ensure_dir() -> None:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)


# ── Pool client ─────────────────────────────────────────────────────


def _pool_enabled() -> bool:
    return bool(_POOL_URL and _POOL_TOKEN)


# Engines whose login session lives in localStorage rather than cookie auth (kimi: the three keys access_token / refresh_token).
# The two write-back gates below originally only required non-empty cookies - meant to stop browser engines from "a post-logout state with empty cookies
# overwriting a good session"; for these engines check localStorage instead; gates for other engines are unchanged.
# Kept in sync with _LOCALSTORAGE_AUTH_ENGINES in backend geo/api/engine_sessions.py.
_LOCALSTORAGE_AUTH_ENGINES = {"kimi"}


def _state_has_auth(engine_name: str, storage_state: dict | None) -> bool:
    """Before write-back, judge whether this state looks "still logged in": non-empty cookies, or (localStorage auth engines) all three keys present."""
    if not storage_state:
        return False
    if storage_state.get("cookies"):
        return True
    if engine_name in _LOCALSTORAGE_AUTH_ENGINES:
        from .kimi_http import state_has_kimi_auth
        return state_has_kimi_auth(storage_state)
    return False


def _normalize_storage_state(state: Optional[dict]) -> Optional[dict]:
    """Normalize origins[].localStorage / sessionStorage from object {k:v} into the array [{name,value}]
    Playwright requires. Login sessions exported by the extension are often native JS objects; without normalization new_context reports
    `storageState.origins[0].localStorage: expected array, got object`, and the engine's whole hot session fails to start.
    Defensive: already broken sessions are fixed on load, no need to re-upload the account."""
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
    pool_name = _pool_name_ctx.get()
    if _POOL_EXPLICIT_ONLY and not pool_name:
        return None   # explicit pool mode: old calls without a specified pool do not touch the pool, use local files
    try:
        params = {"engine": engine_name}
        if _WORKER_POOL_ID is not None:
            params["worker_id"] = _WORKER_POOL_ID
        if pool_name:
            params["pool"] = pool_name
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
        # Stash for later check-in / _engine_proxy (isolated per context, one slot per engine)
        slots = _ctx_slots()
        slots["id"][engine_name] = sid
        # Egress marker: None = engine default / 'proxy' = use proxy / 'host' = local IP
        slots["egress"][engine_name] = data.get("egress")
        # Per-account proxy binding (proxy_override): when non-empty _engine_proxy prefers it
        slots["proxy"][engine_name] = data.get("proxy_override")
        slots.setdefault("user_agent", {})[engine_name] = data.get("user_agent")   # the account's UA at login (optionally returned by the backend)
        _log.info("[session-pool] engine=%s: checked out id=%s use_count=%s egress=%s proxy_bound=%s source=%r",
                  engine_name, sid, data.get("use_count"), data.get("egress"),
                  bool(data.get("proxy_override")), data.get("source_label"))
        return _normalize_storage_state(state)
    except Exception as e:
        _log.warning("[session-pool] engine=%s: check-out failed: %s", engine_name, e)
        return None


# D2 (2026-05-18): check-in upgraded to report an enum FailureType; the backend applies different
# quarantine policies per type. Old call sites `report_session_outcome(engine, captcha_triggered=bool)`
# are translated into SUCCESS / CAPTCHA. New calls should pass the result string directly.
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
    storage_state: dict | None = None,
) -> None:
    """Notify pool that the session we just checked out was used.

    Two equivalent calling styles:
      - old: report_session_outcome("doubao", captcha_triggered=True)
            -> translated to result="captcha"
      - new: report_session_outcome("doubao", result="empty_answer",
                                    error_msg="answer was empty after 60s")

    No-op if pool isn't configured or no check-out was recorded for this engine
    in the current thread (e.g. we fell back to file). Safe to call always.
    """
    if not _pool_enabled():
        return
    slots = (_checkout_ctx.get() or {}).get("id") or {}
    sid = slots.pop(engine_name, None)
    if sid is None:
        return

    # Call site signature translation
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
        body["error_msg"] = error_msg[:500]  # truncated to avoid blowing up logs
    if rate_limited_until is not None:
        body["rate_limited_until"] = rate_limited_until  # risk-control cooldown expiry (e.g. deepseek mute_until)
    if exit_ip:
        body["exit_ip"] = exit_ip  # actual egress IP (resolved via the egress proxy)
    # Rolling renewal: on successful wrap-up attach the refreshed storage_state; the backend writes it back + extends expires_at.
    if result == "success" and _state_has_auth(engine_name, storage_state):
        body["storage_state"] = storage_state
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


def renew_checked_out_session(engine_name: str, storage_state: dict | None) -> None:
    """Rolling renewal: write the storage_state refreshed by the current checked-out session back to the pool and extend expires_at.

    Unlike report_session_outcome: **does not pop** the check-out slot (the session is still in hot use and will be checked in
    normally later); it just renews cookies during active use, so resident hot sessions are not wrongly killed by the 7-day hard TTL.
    Goes through the check-in (result=success) path, where the backend SUCCESS branch handles write-back + renewal. Best-effort.
    """
    if not _pool_enabled():
        return
    if not _state_has_auth(engine_name, storage_state):
        return
    sid = get_last_checkout_id(engine_name)   # read-only, no pop
    if sid is None:
        return
    try:
        with httpx.Client(timeout=_POOL_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_POOL_URL}/api/engine-sessions/check-in",
                headers={"X-Service-Token": _POOL_TOKEN, "Content-Type": "application/json"},
                json={"id": sid, "result": "success", "storage_state": storage_state},
            )
        if r.status_code != 200:
            _log.warning("[session-pool] engine=%s id=%s renew HTTP %d: %s",
                         engine_name, sid, r.status_code, r.text[:200])
        else:
            _log.info("[session-pool] engine=%s id=%s storage_state renewed", engine_name, sid)
    except Exception as e:
        _log.warning("[session-pool] engine=%s id=%s renew failed: %s", engine_name, sid, e)


def get_last_checkout_id(engine_name: str):
    """Return the session id of the current context's last check-out; used as the session identifier for per-account sticky proxies.
    Returns None when nothing was checked out from the pool (local file fallback)."""
    slots = (_checkout_ctx.get() or {}).get("id") or {}
    return slots.get(engine_name)


def get_last_egress(engine_name: str):
    """Return the egress marker of the current context's last check-out: None = engine default / 'proxy' / 'host'."""
    slots = (_checkout_ctx.get() or {}).get("egress") or {}
    return slots.get(engine_name)


def get_last_proxy_override(engine_name: str):
    """Return the account-bound proxy string of the current context's last check-out (http://user:pass@host:port); None if unbound."""
    slots = (_checkout_ctx.get() or {}).get("proxy") or {}
    return slots.get(engine_name)


def session_user_agent(engine_name: str) -> Optional[str]:
    """The UA used when the current checked-out account logged in (only if the pool has it recorded and the backend returned it), used by pure HTTP paths to impersonate the account owner."""
    try:
        return ((_checkout_ctx.get() or {}).get("user_agent") or {}).get(engine_name) or None
    except Exception:  # noqa: BLE001
        return None


def save_storage_state(engine_name: str, storage_state: dict) -> None:
    """Persist full storage_state (cookies + localStorage) to disk."""
    _ensure_dir()
    path = _SESSION_DIR / f"{engine_name}.json"
    path.write_text(json.dumps(storage_state, ensure_ascii=False, indent=2))


def load_storage_state(engine_name: str) -> Optional[dict]:
    """Load storage_state.

    Priority:
      1. Central pool (enabled only when ENGINE_SESSION_POOL_URL + token are configured)
      2. Local file (fallback for legacy / empty pool / unreachable pool)

    Handles both new format (full storage_state with origins) and legacy
    format (plain cookie list) for backward compatibility.
    """
    # Path 1: central pool
    pooled = _pool_checkout(engine_name)
    if pooled is not None:
        return pooled

    # In pool mode, when check-out gets no account (all quarantined/rate-limited/leased by other workers) -> **do not fall back to the shared local file**:
    # that would make all workers hit the same (and stale) account, bypassing pool/lease/cap, the exact opposite of 1-account-1-IP,
    # and the density would trigger risk control. Return None so the upper layer treats it as "no session available": deepseek backs off transiently;
    # browser engines start a logged-out session that then fails the login/hit checks, going through reaper + retries to a terminal state, no more account sharing.
    # In explicit pool mode (_POOL_EXPLICIT_ONLY), old calls without a specified pool naturally skip the pool and keep using local files.
    if _pool_enabled() and not (_POOL_EXPLICIT_ONLY and not _pool_name_ctx.get()):
        return None

    # Path 2: legacy local file (fallback only in local / legacy mode without the pool enabled)
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


def peek_session_exists(engine_name: str) -> bool:
    """Read-only probe of whether an engine has a local login session file - **never checks out**.

    For status/health queries like `/sessions`: in pool mode `load_storage_state`'s first step
    is `_pool_checkout` (leases an account + use_count++), and status queries never check in ->
    every poll would leak a lease and waste the daily quota. Status queries should only read, not lease, hence this function.

    It returns the side-effect-free liveness signal "does this machine have a login session file"; in pool mode the
    authoritative view of account availability is the center's /admin/engine-sessions (only that reflects the pool's real state).
    """
    return (_SESSION_DIR / f"{engine_name}.json").exists()


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
