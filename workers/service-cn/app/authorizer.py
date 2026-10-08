"""Account authorizer - server-side automatic login that captures the login session into the pool (core of fully automatic authorization).

Supports "automatic password login" (verified for deepseek): the admin console enters account+password -> the server here
runs a login -> captures storage_state -> POSTs to the account pool upload endpoint. When the login drops, the daemon
calls this function again with the stored password to log back in automatically.

QR-code assisted login and API keys go through other paths (not in this module). qwen/Wenxin keep a browser because of anti-bot measures, and qwen
has no password login, so this module currently mainly serves deepseek (and any engine with password login).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import httpx

_POOL_URL = os.environ.get("ENGINE_SESSION_POOL_URL", "").rstrip("/")
# upload uses the service token (browser-service already has it for check-out/in); the backend upload already accepts it
_SERVICE_TOKEN = os.environ.get("ENGINE_SESSION_SERVICE_TOKEN", "")

# Per-engine login flow config. Only engines "with password login" are configured; QR-code/passwordless ones are not here.
_LOGIN_CONFIG = {
    "deepseek": {
        "url": "https://chat.deepseek.com/sign_in",
        "pw_login_btn": ["text=Login with password", "text=密码登录"],
        "identifier_sel": "input:not([type=password])",
        "password_sel": "input[type=password]",
        "submit": ["button:has-text('登录')", "button:has-text('Log in')"],
        "success": [":text-is('New chat')", "text=开启新对话"],
        "bad_cred_text": "密码错误或账户不存在",
        "profile_platform": "Linux x86_64",
    },
    # Wenxin/Baidu: on chat.baidu.com click "登录" ("Log in") to open the Baidu passport login layer, switch to the "账号密码登录" ("account password login") tab, then fill in.
    # Baidu password login often hits slider/SMS captchas, especially from data center IPs; log in if possible, fall back to manual/QR code on captcha.
    "wenxin": {
        "url": "https://chat.baidu.com/",
        "pre_click": ["text=登录", "text=请登录", "text=立即登录"],  # open the login layer first
        "pw_login_btn": ["text=账号密码登录", "text=用户名登录", "text=密码登录"],  # switch from QR code to the password tab
        "identifier_sel": "input[name='userName'], #TANGRAM__PSP_11__userName, "
                          "input[placeholder*='手机'], input[placeholder*='用户名'], input[placeholder*='邮箱']",
        "password_sel": "input[type=password]",
        "submit": ["#TANGRAM__PSP_11__submit", "input[value='登录']",
                   "button:has-text('登录')", "text=登 录"],
        "success": ["text=开启新对话", "textarea"],
        "success_cookies": ["BDUSS"],   # .baidu.com only has BDUSS after a successful QR/password login (most reliable)
        "bad_cred_text": "您输入的帐号或密码有误",
        "profile_platform": "Linux x86_64",
    },
}


def supports_password(engine: str) -> bool:
    return engine in _LOGIN_CONFIG


async def _click_any(pg, selectors, timeout: int = 3000) -> bool:
    """Find the first visible selector in the main page + all iframes and click it (the Baidu login layer is often in an iframe).

    Must filter by `visible=true` before taking .first: the deepseek login page has two copies of 「密码登录」 ("password login") in the DOM,
    the first is a hidden width-measuring span (ds-sign-in-form__social-link-measure); a bare .first gets it,
    is_visible is always False, and from a CN IP the password form never opens -> login_form_not_found.
    """
    frames = [pg] + [f for f in getattr(pg, "frames", [])]
    for sel in selectors:
        for fr in frames:
            try:
                el = fr.locator(sel).locator("visible=true").first
                if await el.is_visible(timeout=timeout):
                    await el.click()
                    return True
            except Exception:  # noqa: BLE001
                pass
    return False


async def _fill_any(pg, sel, value, timeout: int = 3000) -> bool:
    """Find an input box in the main page + all iframes and fill it."""
    frames = [pg] + [f for f in getattr(pg, "frames", [])]
    for fr in frames:
        try:
            el = fr.locator(sel).locator("visible=true").first
            if await el.is_visible(timeout=timeout):
                await el.fill(value)
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _mask(identifier: str) -> str:
    """For masked display: foo@bar.com -> fo***@bar.com."""
    if "@" in identifier:
        u, d = identifier.split("@", 1)
        return (u[:2] + "***") + "@" + d
    return identifier[:3] + "***" + identifier[-2:] if len(identifier) > 5 else identifier


async def authorize_password(
    engine: str,
    identifier: str,
    password: str,
    *,
    proxy: Optional[dict] = None,
    upload: bool = True,
    ttl_days: int = 14,
) -> dict:
    """Log in automatically on the server with account+password and capture the login session (optionally into the pool).

    Returns {ok, error, account_handle, uploaded}.
    error: 'unsupported'|'bad_credentials'|'login_failed'|'<exception>'.
    """
    cfg = _LOGIN_CONFIG.get(engine)
    if not cfg:
        return {"ok": False, "error": "unsupported"}

    from cloakbrowser import launch_context_async
    from .browser import get_context_options, _pick_profile

    # Login-only proxy: when the caller passes no proxy, ENGINE_AUTHZ_PROXY takes precedence over the PROXY_ENGINES
    # auto proxy below. We used to think CN egress only got the "phone number + verification code" page and the email+password form required an overseas egress;
    # tested 2026-09-20, that is wrong: the CN page defaults to phone verification, but after clicking 「密码登录」 ("password login") it is "phone/email + password";
    # it failed to open before because _click_any's bare .first hit the hidden width-measuring span (see its docstring). So direct CN connection works,
    # and ENGINE_AUTHZ_PROXY is only needed when a fixed egress is really required. Runtime egress has its own mechanism
    # (proxy_override/PROXY_ENGINES), unaffected by this.
    # Value looks like http://host:port or http://user:pass@host:port (local gost SS has no auth, a bare string is fine).
    if proxy is None:
        authz_proxy = os.environ.get("ENGINE_AUTHZ_PROXY", "").strip()
        if authz_proxy:
            from urllib.parse import urlparse
            try:
                u = urlparse(authz_proxy)
                if u.hostname:
                    d = {"server": f"{u.scheme or 'http'}://{u.hostname}" + (f":{u.port}" if u.port else "")}
                    if u.username:
                        d["username"] = u.username
                    if u.password:
                        d["password"] = u.password
                    proxy = d
            except Exception:  # noqa: BLE001
                pass

    # Fallback: only when ENGINE_AUTHZ_PROXY is not set, use the runtime egress proxy of engines in the PROXY_ENGINES allowlist.
    # Note - if the runtime proxy session lands on a CN egress (e.g. Bright Data ISP without a country lock), deepseek login will still
    # hit the phone verification page; in that case set ENGINE_AUTHZ_PROXY to a definite overseas egress.
    if proxy is None:
        proxy_engines = [e.strip() for e in os.environ.get("PROXY_ENGINES", "").split(",") if e.strip()]
        server = os.environ.get("ENGINE_PROXY_SERVER", "").strip()
        user = os.environ.get("ENGINE_PROXY_USER", "").strip()
        pw = os.environ.get("ENGINE_PROXY_PASS", "").strip()
        if engine in proxy_engines and server and user and pw:
            proxy = {"server": server, "username": f"{user}-session-authz", "password": pw}

    opts = get_context_options(profile=_pick_profile(platform_filter=cfg["profile_platform"]))
    if proxy:
        opts["proxy"] = proxy
    ctx = await launch_context_async(headless=False, **opts)
    pg = await ctx.new_page()
    try:
        await pg.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)
        # The login form is rendered on the frontend and through a proxy sometimes is not there after 5s (2026-09-20 deepseek #57 got
        # login_form_not_found twice; reopening with the same proxy it appeared at 6s). Wait up to 30s more; if absent, continue with the checks below.
        try:
            await pg.wait_for_selector(cfg["identifier_sel"], state="visible", timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        # 0) Open the login layer first (chat.baidu.com needs a click on "登录" ("Log in") first; deepseek has no pre_click, skipped)
        if cfg.get("pre_click"):
            await _click_any(pg, cfg["pre_click"], timeout=4000)
            await asyncio.sleep(2)
        # 1) Switch to the "账号密码登录" ("account password login") tab (Baidu defaults to QR code)
        await _click_any(pg, cfg["pw_login_btn"], timeout=3000)
        await asyncio.sleep(2)
        # 2) Fill in account and password (search across iframes; the Baidu login layer is often in an iframe)
        filled_id = await _fill_any(pg, cfg["identifier_sel"], identifier)
        filled_pw = await _fill_any(pg, cfg["password_sel"], password)
        if not (filled_id and filled_pw):
            # Login box not found: iframe structure changed, page redesigned, or QR code forced -> hand back to the upper layer (manual/QR code)
            return {"ok": False, "error": "login_form_not_found"}
        await asyncio.sleep(1)
        # 3) Submit: try the submit button first, otherwise press Enter in the password box
        if not await _click_any(pg, cfg["submit"], timeout=2000):
            frames = [pg] + [f for f in getattr(pg, "frames", [])]
            for fr in frames:
                try:
                    el = fr.locator(cfg["password_sel"]).first
                    if await el.is_visible(timeout=800):
                        await el.press("Enter")
                        break
                except Exception:  # noqa: BLE001
                    pass
        # 4) Poll for login success (cookie first, ~24s; if a slider/SMS pops up meanwhile, a human passing it via VNC is also captured)
        succ_cookies = cfg.get("success_cookies") or []
        ok = False
        for _ in range(12):
            await asyncio.sleep(2)
            if succ_cookies:
                try:
                    names = {c.get("name") for c in await ctx.cookies()}
                    if any(a in names for a in succ_cookies):
                        ok = True
                        break
                except Exception:  # noqa: BLE001
                    pass
            else:
                cnt = 0
                for s in cfg["success"]:
                    try:
                        cnt += await pg.locator(s).count()
                    except Exception:  # noqa: BLE001
                        pass
                if cnt > 0:
                    ok = True
                    break
        if not ok:
            try:
                body = (await pg.inner_text("body"))[:200]
            except Exception:  # noqa: BLE001
                body = ""
            if cfg["bad_cred_text"] in body:
                return {"ok": False, "error": "bad_credentials"}
            return {"ok": False, "error": "login_failed"}

        state = await ctx.storage_state()
        handle = _mask(identifier)
        result = {"ok": True, "error": None, "account_handle": handle, "uploaded": False}
        # Store credentials encrypted (identifier+password) for automatic renewal re-login after disconnects
        from . import crypto
        enc_cred = crypto.encrypt(json.dumps({"identifier": identifier, "password": password}))
        if upload and _POOL_URL and _SERVICE_TOKEN:
            try:
                with httpx.Client(timeout=20) as c:
                    r = c.post(
                        f"{_POOL_URL}/api/engine-sessions/upload",
                        headers={"X-Service-Token": _SERVICE_TOKEN, "Content-Type": "application/json"},
                        json={"engine": engine, "storage_state": state,
                              # unique source_label per account (= identifier), so upsert does not merge multiple accounts into one row
                              "source_label": f"authz:{identifier}", "account_handle": handle,
                              "ttl_days": ttl_days,
                              "auth_type": "password", "credentials": enc_cred},
                    )
                result["uploaded"] = (r.status_code == 201)
                if r.status_code != 201:
                    result["error"] = f"upload HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                result["error"] = f"upload failed: {e!r}"[:200]
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)[:200]}
    finally:
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
