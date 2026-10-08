"""QR-code assisted authorization - a stateful QR code flow (for engines like qwen/Wenxin/Yuanbao that only support QR login).

Flow:
  start(engine) -> open a browser to the login page -> capture the QR image -> return {session_id, qr_image}
  poll(session_id) -> logged in? -> yes: capture storage_state into the pool, close the page, done;
                                no: the QR code may have refreshed -> recapture and return pending+qr_image
The browser page stays open between start..poll (held by the module-level _sessions) and is cleared on expiry (QR_TTL).

Passwordless (QR code), so no credentials are stored; auth_type='qr'. After disconnects, relies on dashboard alerts + manual rescanning.
"""

from __future__ import annotations

import asyncio
import base64
import os
import secrets
import time
from typing import Optional

import httpx

_POOL_URL = os.environ.get("ENGINE_SESSION_POOL_URL", "").rstrip("/")
_SERVICE_TOKEN = os.environ.get("ENGINE_SESSION_SERVICE_TOKEN", "")
_QR_TTL = int(os.environ.get("QR_SESSION_TTL_SEC", "300"))

# session_id -> {ctx, page, engine, created}
_sessions: dict = {}

# Per engine: login page + buttons to click before entering QR mode + QR selector + login success criteria
_QR_CONFIG = {
    "qwen": {
        # The QR code is in an iframe on passport.qianwen.com; the "扫码登录" ("QR code login") tab must be clicked first
        "url": "https://www.qianwen.com/",
        "pre_click": ["text=登录", "text=立即登录"],
        "qr_tab": ["text=扫码登录", "text=扫码"],
        "qr_sel": ["[class*='QRCode']", "[class*='QRCodeWrapper']", "iframe[src*='passport']",
                   "canvas", "img[src*='qr']"],
        "success": ["textarea", "text=新建对话"],
        "login_hint": ["扫码", "二维码", "登录"],
        # Login cookie that only appears after a successful scan (the main login signal, most reliable)
        "auth_cookies": ["tongyi_sso_ticket"],
    },
    "wenxin": {
        "url": "https://chat.baidu.com/",
        "pre_click": ["text=登录", "text=请登录"],
        "qr_sel": ["img[src*='qr']", "canvas", "[class*='qrcode']", "[class*='qrimg']"],
        "success": ["text=开启新对话", "textarea"],
        "login_hint": ["扫码", "二维码"],
        "auth_cookies": ["BDUSS"],
    },
    "yuanbao": {
        "url": "https://yuanbao.tencent.com/",
        "pre_click": ["text=登录", "text=立即登录"],
        "qr_sel": ["iframe[src*='wx']", "img[src*='qr']", "canvas", "[class*='qrcode']"],
        "success": ["text=开启新对话", "textarea"],
        "login_hint": ["微信扫码", "二维码"],
        "auth_cookies": ["hy_token"],
    },
    "doubao": {
        # The Doubao login popup shows the QR code side by side by default (scan with the Doubao/Feishu app), no tab switch needed.
        # The class has a hash suffix (qrcode-DeN5Ny), so use [class*='qrcode'] substring matching.
        "url": "https://www.doubao.com/chat/",
        "pre_click": ["text=登录", "[class*='login-btn-header']", "text=登录/注册"],
        "qr_sel": ["[class*='qrcode']", "canvas", "img[src*='qr']"],
        "success": ["textarea"],
        "login_hint": ["扫码", "豆包", "飞书"],
        # ByteDance only sets sessionid (HttpOnly) after a successful login; the guest state only has sid_guard/msToken.
        "auth_cookies": ["sessionid", "sid_tt"],
    },
}


def supports_qr(engine: str) -> bool:
    return engine in _QR_CONFIG


async def _grab_qr(page, cfg) -> Optional[str]:
    """Screenshot the QR element -> base64 PNG data URL; None on failure."""
    for sel in cfg["qr_sel"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                png = await el.screenshot(timeout=3000)
                return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        except Exception:  # noqa: BLE001
            continue
    return None


async def _login_screen_visible(page, cfg) -> bool:
    """Is the login QR code still there? Only QR-specific selectors (containing qr/passport/wx) are used, excluding generic elements like canvas/img
    that may exist on both login and chat pages, to avoid misjudgment."""
    for sel in cfg.get("qr_sel", []):
        if not any(k in sel.lower() for k in ("qr", "passport", "wx")):
            continue
        try:
            if await page.locator(sel).first.is_visible(timeout=400):
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


async def _is_logged_in(page, cfg) -> bool:
    # Main criterion: the context only gets the login cookie after a successful scan (qwen=tongyi_sso_ticket /
    # wenxin=BDUSS / yuanbao=hy_token). Far more reliable than DOM checks - immune to false positives from "the login page has its own
    # textarea" and false negatives from "the QR iframe stays in the DOM after login" (the root cause of qwen scans not reacting).
    auth = cfg.get("auth_cookies") or []
    if auth:
        try:
            names = {c.get("name", "") for c in await page.context.cookies()}
        except Exception:  # noqa: BLE001
            names = set()
        # If auth_cookies is configured it is authoritative: present = logged in, absent = not yet (no more DOM checks, eliminating false positives/negatives).
        return any(a in names for a in auth)

    # Only engines without auth_cookies fall back to DOM checks: success hit and the QR code has disappeared.
    hit = False
    for sel in cfg["success"]:
        try:
            if await page.locator(sel).first.is_visible(timeout=800):
                hit = True
                break
        except Exception:  # noqa: BLE001
            pass
    if not hit:
        return False
    if await _login_screen_visible(page, cfg):
        return False
    return True


def _gc():
    now = time.time()
    for sid in list(_sessions.keys()):
        if now - _sessions[sid]["created"] > _QR_TTL:
            s = _sessions.pop(sid, None)
            if s:
                asyncio.create_task(_close(s))


async def _close(s):
    try:
        await s["ctx"].close()
    except Exception:  # noqa: BLE001
        pass


async def qr_start(engine: str) -> dict:
    """Open a browser to the login page and capture the QR code. Returns {session_id, qr_image, error}."""
    cfg = _QR_CONFIG.get(engine)
    if not cfg:
        return {"error": "unsupported"}
    _gc()
    from cloakbrowser import launch_context_async
    from .browser import get_context_options, _pick_profile
    opts = get_context_options(profile=_pick_profile(platform_filter="Linux x86_64"))
    ctx = await launch_context_async(headless=False, **opts)
    page = await ctx.new_page()
    try:
        await page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)
        for sel in cfg["pre_click"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    break
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(2)
        # Switch to the QR tab (some engines like qwen default to phone/password login and need switching)
        for sel in cfg.get("qr_tab", []):
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1500):
                    await el.click()
                    break
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(3)
        qr = await _grab_qr(page, cfg)
        if not qr:
            await ctx.close()
            return {"error": "qr_not_found"}
        sid = secrets.token_urlsafe(12)
        _sessions[sid] = {"ctx": ctx, "page": page, "engine": engine, "created": time.time()}
        return {"session_id": sid, "qr_image": qr, "error": None}
    except Exception as e:  # noqa: BLE001
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
        return {"error": repr(e)[:200]}


async def qr_poll(session_id: str) -> dict:
    """Poll: done (logged in, in the pool) / pending (may carry a new QR code) / expired / error."""
    s = _sessions.get(session_id)
    if not s:
        return {"status": "expired"}
    cfg = _QR_CONFIG[s["engine"]]
    page = s["page"]
    try:
        if await _is_logged_in(page, cfg):
            state = await s["ctx"].storage_state()
            uploaded = False
            err = None
            if _POOL_URL and _SERVICE_TOKEN:
                try:
                    with httpx.Client(timeout=20) as c:
                        r = c.post(
                            f"{_POOL_URL}/api/engine-sessions/upload",
                            headers={"X-Service-Token": _SERVICE_TOKEN, "Content-Type": "application/json"},
                            json={"engine": s["engine"], "storage_state": state,
                                  "source_label": f"qr:{session_id}", "auth_type": "qr",
                                  "ttl_days": 14},
                        )
                    uploaded = (r.status_code == 201)
                    if not uploaded:
                        err = f"upload HTTP {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    err = f"upload failed: {e!r}"[:150]
            _sessions.pop(session_id, None)
            await _close(s)
            return {"status": "done", "uploaded": uploaded, "error": err}
        # Not logged in -> recapture the QR code (it may have refreshed)
        qr = await _grab_qr(page, cfg)
        return {"status": "pending", "qr_image": qr}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": repr(e)[:200]}
