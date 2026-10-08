"""Login session auto-renewal daemon - automatic re-login for password-type accounts, no manual work needed.

Periodically (ENGINE_REFRESH_INTERVAL_SEC, default 900s) pulls refresh-candidates from the backend (logged-out
quarantined **or** active accounts whose login session is near/past expiry + auth_type=password + encrypted credentials present),
decrypts {identifier,password}, runs the authorizer re-login through the account's original egress IP (candidates carry proxy_url) ->
re-upload to renew (upsert renews by account_id, status back to active, expires_at extended, automatically unquarantined).

Requires ENGINE_SESSION_POOL_URL + service token + ENGINE_CRED_KEY all in place to enable; and it may only be enabled on
**one** worker (ENGINE_REFRESH_ENABLED=1); enabling on several makes multiple machines re-login the same account -> off-site -> banned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

from . import crypto
from .authorizer import authorize_password
from .session_store import _POOL_URL, _POOL_TOKEN

_log = logging.getLogger("refresh-daemon")
_INTERVAL = int(os.environ.get("ENGINE_REFRESH_INTERVAL_SEC", "900"))
_BATCH = int(os.environ.get("ENGINE_REFRESH_BATCH", "5"))


def _enabled() -> bool:
    return bool(_POOL_URL and _POOL_TOKEN and crypto.enabled())


def _fetch_candidates() -> list:
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{_POOL_URL}/api/engine-sessions/refresh-candidates",
                  params={"limit": _BATCH},
                  headers={"X-Service-Token": _POOL_TOKEN})
    return r.json() if r.status_code == 200 else []


def _proxy_dict(url: str | None) -> dict | None:
    """Parse the account's full proxy string into cloakbrowser's {server,username,password}.
    Consistent with the proxy_override parsing in browser.py:_engine_proxy - ensures re-login uses the same egress IP
    as runtime (1 account 1 IP), avoiding "off-site login" risk control. Empty/parse failure -> None (fall back to env auto-pick)."""
    url = (url or "").strip()
    if not url:
        return None
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        if not u.hostname:
            return None
        server = f"{u.scheme or 'http'}://{u.hostname}" + (f":{u.port}" if u.port else "")
        d = {"server": server}
        if u.username:
            d["username"] = u.username
        if u.password:
            d["password"] = u.password
        return d
    except Exception:  # noqa: BLE001
        return None


async def _tick() -> None:
    cands = await asyncio.to_thread(_fetch_candidates)
    if not cands:
        return
    _log.info("[refresh] %d logged-out accounts pending renewal", len(cands))
    for cand in cands:
        plain = crypto.decrypt(cand.get("credentials") or "")
        if not plain:
            continue
        try:
            d = json.loads(plain)
            proxy = _proxy_dict(cand.get("proxy_url"))
            res = await authorize_password(cand["engine"], d["identifier"], d["password"],
                                           proxy=proxy)
            _log.info("[refresh] %s id=%s %s → %s", cand["engine"], cand["id"],
                      cand.get("account_handle"), "OK 已续期" if res.get("ok") else res.get("error"))
        except Exception as e:  # noqa: BLE001
            _log.warning("[refresh] id=%s renewal error: %s", cand.get("id"), e)


async def refresh_loop(stop: asyncio.Event) -> None:
    if not _enabled():
        _log.info("[refresh] not enabled (POOL_URL / service token / ENGINE_CRED_KEY not all configured)")
        return
    _log.info("[refresh] daemon started, interval=%ds batch=%d", _INTERVAL, _BATCH)
    while not stop.is_set():
        try:
            await _tick()
        except Exception as e:  # noqa: BLE001
            _log.warning("[refresh] tick error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_INTERVAL)
        except asyncio.TimeoutError:
            pass
