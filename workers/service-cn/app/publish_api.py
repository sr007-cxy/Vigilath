"""HTTP entry for the publish channel - browser-service goes from "read only" to "can also write".

Three routes:
    POST /publish/verify-identity   read the login identity once (used by both the registration flow and pre-publish checks)
    POST /publish                   run one publish (this round only supports dry_run)
    GET  /publish/screenshot/...    fetch dry_run / failure screenshots

Step 7 (keep-alive/renewal/health check) added two more:
    POST /publish/keepalive         one keep-alive pass: navigate + read identity + export the refreshed storage_state
    GET  /publish/worker-info       local worker_id, for the backend healer's ownership check

A separate module rather than piling more onto the 815-line main.py - main.py only adds one include_router line.

## Authentication

**None** of browser-service's existing routes have authentication (relying on binding 127.0.0.1 + nginx, see main.py).
That is acceptable for read endpoints, but `/publish` is a write endpoint that can publish externally with company accounts, and any internal process could
call it to publish under false identity. So an **optional** gate is added here:

    PUBLISH_SERVICE_TOKEN set -> verify X-Publish-Service-Token (hmac.compare_digest)
    not set                    -> allow (does not break existing deployments)

Production should set it. compare_digest instead of == avoids leaking timing information, written the same way as the backend's
`_tok_eq` in api/engine_sessions.py:60.

## Concurrency

Publishing uses a **separate** semaphore, not shared with /search:
  · /search's semaphore timeout is 300s, and a queue of searches would stall publishing until it expires - while scheduled publishing
    has timing requirements;
  · but both sets of Chromium instances share the same machine's memory (300-500 MB per instance), so
    MAX_CONCURRENT_QUERIES + MAX_CONCURRENT_PUBLISH should stay <= 4 (design §3.6).
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .publishers import PublishRequest, get_publisher, supported_platforms
from .publishers.context import account_page
from .publishers.paths import resolve_screenshot
from .core_publisher import (fetch_core_screenshot, publish_with_core,
                             CORE_PLATFORM_ALIASES, core_claimable_platforms,
                             verify_identity_with_core)
from .publishers.core_backed import CoreBackedPublisher

_log = logging.getLogger(__name__)

router = APIRouter()

MAX_CONCURRENT_PUBLISH = int(os.environ.get("MAX_CONCURRENT_PUBLISH", "2"))
PUBLISH_SEMAPHORE_TIMEOUT = int(os.environ.get("PUBLISH_SEMAPHORE_TIMEOUT", "120"))
# Total timeout for one publish. Shorter than /search's 300s: publishing is "fill a form + click a button"; running to 4 minutes basically
# means it is stuck, and reclaiming it early as a timeout is more useful than waiting.
PUBLISH_TIMEOUT = int(os.environ.get("PUBLISH_TIMEOUT", "240"))
VERIFY_TIMEOUT = int(os.environ.get("PUBLISH_VERIFY_TIMEOUT", "90"))
# Total timeout for one keep-alive pass. A bit wider than verify-identity (it adds one real page navigation + one
# storage_state export), but far shorter than publishing - keep-alive fills no form, uploads nothing, clicks no submit.
KEEPALIVE_TIMEOUT = int(os.environ.get("PUBLISH_KEEPALIVE_TIMEOUT", "120"))
MANUAL_LOGIN_TTL = int(os.environ.get("PUBLISH_MANUAL_LOGIN_TTL_SEC", "600"))
MANUAL_LOGIN_MAX_SESSIONS = int(os.environ.get("PUBLISH_MANUAL_LOGIN_MAX_SESSIONS", "3"))
MANUAL_LOGIN_HEADED = os.environ.get("PUBLISH_MANUAL_LOGIN_HEADED", "0") == "1"
ACCOUNT_LOGIN_CHANNELS_ENABLED = os.environ.get("PUBLISH_ACCOUNT_LOGIN_CHANNELS_ENABLED", "0") == "1"

_sem: Optional[asyncio.Semaphore] = None
# session_id -> browser context. In memory only; no account or password is saved; invalidated on service restart.
_manual_login_sessions: dict[str, dict] = {}

_PASSWORD_LOGIN_PLATFORMS = {"zhihu", "sohu", "cnblogs", "douyin", "xhs"}
_NO_QR_PLATFORMS = {"sohu", "cnblogs", "douyin", "xhs"}

_PUBLISH_QR_CONFIG = {
    "__default__": {
        "entry": ("text=扫码登录", "text=二维码登录", "text=微信登录", "text=扫码"),
        "qr": ("[class*='qrcode']", "[class*='QRCode']", "[class*='qr-code']",
               "[class*='qrCode']", "iframe[src*='passport']", "iframe[src*='wx']",
               "img[src*='qr']", "canvas"),
    },
    "zhihu": {
        "entry": ("text=扫码登录",),
        "qr": ("canvas", "[class*='QRCode']", "[class*='qrcode']"),
    },
    "baijia": {
        "entry": ("text=扫码登录", "text=百度App扫码"),
        "qr": ("iframe[src*='passport.baidu.com']", "img[src*='qr']", "canvas"),
    },
    "toutiao": {
        "entry": ("text=扫码登录",),
        "qr": ("canvas", "[class*='qrcode']", "iframe[src*='passport']"),
    },
    "sohu": {
        "entry": ("text=扫码登录", "text=微信登录"),
        "qr": ("img[src*='qr']", "canvas", "[class*='qrcode']"),
    },
    "csdn": {
        "entry": ("text=扫码登录", "text=微信登录"),
        "qr": ("iframe[src*='passport.csdn']", "img[src*='qr']", "canvas"),
    },
    "smzdm": {
        "entry": ("text=扫码登录", "text=微信登录"),
        "qr": ("img[src*='qr']", "[class*='qrimg']", "canvas"),
    },
    "wxgzh": {
        "entry": ("text=扫码登录", "text=微信登录"),
        "qr": ("img.qrcode", "img[src*='qrcode']", "[class*='qrcode'] img"),
    },
    "xhs": {
        "entry": ("text=扫码登录", "text=小红书扫码登录"),
        "qr": ("canvas", "[class*='qrcode']", "img[src*='qr']"),
    },
    "douyin": {
        "entry": ("text=扫码登录",),
        "qr": ("canvas", "[class*='qrcode']", "iframe[src*='passport']"),
    },
    "cnblogs": {
        "entry": ("text=扫码登录", "text=微信登录"),
        "qr": ("img[src*='qr']", "canvas", "[class*='qrcode']"),
    },
}

_PASSWORD_LOGIN_CONFIG = {
    "__default__": {
        "tab": ("button:text-is('密码登录')", "[role='button']:text-is('密码登录')",
                "text=账号密码登录", "text=密码登录", "text=手机号登录"),
        "account": ("input[name='username'], input[name='account'], input[name='email'], "
                    "input[name='phone'], input[placeholder*='手机号'], "
                    "input[placeholder*='邮箱'], input[placeholder*='账号'], "
                    "input:not([type='password']):not([type='hidden'])"),
        "password": "input[name='password'], input[type='password']",
        "submit": "button[type='submit'], button:text-is('登录'), text=登录",
    },
    "zhihu": {
        "tab": ("button:text-is('密码登录')", "[role='button']:text-is('密码登录')"),
    },
    "sohu": {
        "tab": ("text=密码登录", "text=账号密码登录", "text=邮箱登录"),
    },
    "cnblogs": {
        "tab": ("text=密码登录", "text=账号密码登录"),
    },
    "douyin": {
        "tab": ("text=密码登录", "text=手机号登录", "text=账号登录"),
    },
    "xhs": {
        "tab": ("text=密码登录", "text=手机号登录", "text=账号登录"),
    },
}


def _semaphore() -> asyncio.Semaphore:
    """Created lazily: there is no event loop yet at module import time."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONCURRENT_PUBLISH)
    return _sem


def _require_token(got: Optional[str]) -> None:
    expected = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if not expected:
        return  # not configured = keep this service's existing "trust the internal network" stance
    if not got or not hmac.compare_digest(str(got), expected):
        raise HTTPException(status_code=401, detail="invalid publish service token")


async def _read_account_handle(adapter, page) -> Optional[str]:
    """Reading the platform nickname only affects display; on failure, do not take down the main login/identity check chain."""
    try:
        value = await adapter.account_handle(page)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[publish] account_handle probe failed platform=%s: %s",
                     getattr(adapter, "platform", "unknown"), exc)
        return None
    text = str(value or "").strip()
    return text[:64] if text else None


async def _core_identity_from_page(platform: str, page, *,
                                   proxy_url: Optional[str] = None,
                                   ua_platform: Optional[str] = None,
                                   expected_account_id: Optional[str] = None) -> dict:
    state = await page.context.storage_state()
    user_agent = await page.evaluate("navigator.userAgent")
    return await verify_identity_with_core(
        platform=platform, storage_state=state, proxy_url=proxy_url,
        user_agent=user_agent, ua_platform=ua_platform,
        expected_account_id=expected_account_id)


# ── Request bodies ─────────────────────────────────────────────────────────


class VerifyIdentityIn(BaseModel):
    platform: str = Field(..., min_length=1)
    storage_state: dict
    proxy_url: Optional[str] = None
    user_agent: Optional[str] = None
    ua_platform: Optional[str] = None
    # Compared if provided. The registration flow does not pass it (it is there to obtain account_id); pre-publish checks always pass it.
    expected_account_id: Optional[str] = None


class PublishIn(BaseModel):
    platform: str = Field(..., min_length=1)
    # Expected identity. May be empty right after registration before an authoritative id is resolved - but the backend never dispatches work to such accounts.
    account_id: str = ""
    title: str = ""
    body: str = ""
    # html / plain / markdown. Defaulting to plain is the **conservative** default: the plain path treats the body as inert text,
    # so a wrong guess only makes the layout plain; defaulting to html would do pointless parsing on plain bodies.
    body_format: str = "plain"
    source_markdown: str = ""
    images: list[str] = Field(default_factory=list)
    location: Optional[dict] = None
    topics: Optional[str] = None
    dry_run: bool = True
    storage_state: dict
    official_api: Optional[dict] = None
    proxy_url: Optional[str] = None
    ua_platform: Optional[str] = None
    user_agent: Optional[str] = None
    dispatch_id: Optional[int] = None
    attempt: Optional[int] = None
    account_db_id: Optional[int] = None
    worker_id: Optional[int] = None


class KeepaliveIn(BaseModel):
    """One keep-alive / health check / renewal pass (the only call body of the backend publish_pool_healer).

    `expected_account_id` is **required**: during keep-alive we always know who this account should be, unlike the registration flow
    which comes to obtain the identity. Not comparing would let through the case "this cookie now belongs to someone else".
    """
    platform: str = Field(..., min_length=1)
    expected_account_id: str = Field(..., min_length=1)
    storage_state: dict
    proxy_url: Optional[str] = None
    user_agent: Optional[str] = None
    ua_platform: Optional[str] = None


class PasswordLoginIn(BaseModel):
    """Account/password login for publish accounts. The password exists only in this request's memory."""
    platform: str = Field(..., min_length=1)
    identifier: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=512)
    # Local low-value test accounts may use host egress, in which case the backend explicitly passes null.
    # Fixed-proxy mode is still validated by the backend to require a bound, usable proxy_id.
    proxy_url: Optional[str] = None
    expected_account_id: Optional[str] = None
    ua_platform: Optional[str] = None


class WebLoginIn(BaseModel):
    """Interactive web login; account, password, SMS and QR-code info are only entered on the platform page."""
    platform: str = Field(..., min_length=1)
    proxy_url: Optional[str] = None
    expected_account_id: Optional[str] = None
    ua_platform: Optional[str] = None


class ManualLoginSessionIn(BaseModel):
    session_id: str = Field(..., min_length=16, max_length=128)


# ── Routes ───────────────────────────────────────────────────────────


@router.get("/publish/platforms")
async def publish_platforms():
    """Platforms for which this service actually implements adapters. **Different** from the backend's publish_platforms config table -
    that table is "which platforms the business wants to publish to", this is "which ones the code can already publish to". When they disagree, this one wins."""
    web_login_platforms = []
    if ACCOUNT_LOGIN_CHANNELS_ENABLED:
        for platform in supported_platforms():
            adapter = get_publisher(platform)
            if adapter and adapter.login_url:
                web_login_platforms.append(platform)
    claimable = core_claimable_platforms()
    platforms = supported_platforms()
    return {"platforms": supported_platforms(),
            "password_login_platforms": (
                sorted(p for p in platforms if p in _PASSWORD_LOGIN_PLATFORMS)
                if ACCOUNT_LOGIN_CHANNELS_ENABLED else []
            ),
            "web_login_platforms": web_login_platforms,
            "core_supported_platforms": supported_platforms(),
            "core_platform_aliases": CORE_PLATFORM_ALIASES,
            "claimable_platforms": sorted(claimable)}


@router.post("/publish/login-web")
async def login_web(req: WebLoginIn,
                    x_publish_service_token: Optional[str] = Header(None)):
    """Open the platform login page and keep the browser; the user can log in via password, SMS or QR code."""
    _require_token(x_publish_service_token)
    if not ACCOUNT_LOGIN_CHANNELS_ENABLED:
        raise HTTPException(status_code=400, detail="account login channels disabled")
    adapter = get_publisher(req.platform)
    if adapter is None or not adapter.login_url:
        raise HTTPException(status_code=400,
                            detail=f"web login unsupported for platform={req.platform}")
    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=PUBLISH_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="too many concurrent publish operations")
    try:
        return await asyncio.wait_for(_login_web_inner(adapter, req), timeout=VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "login_page_timeout", "detail": "打开登录页超时"}
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] web login failed platform=%s", req.platform)
        return {"ok": False, "error": "login_page_failed",
                "detail": f"{type(e).__name__}: {e}"[:200]}
    finally:
        _semaphore().release()


async def _login_web_inner(adapter, req: WebLoginIn) -> dict:
    seed = req.expected_account_id or secrets.token_hex(16)
    cm = account_page(req.platform, {}, proxy_url=req.proxy_url,
                      ua_platform=req.ua_platform, fingerprint_seed=seed,
                      headless=False if MANUAL_LOGIN_HEADED else None)
    page = await cm.__aenter__()
    handed_off = False
    try:
        await page.goto(adapter.login_url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(1)
        session = await _hold_manual_login(cm, page, adapter, req)
        handed_off = True
        return {"ok": True, "manual_required": True,
                "detail": "请在平台登录页完成登录", **session}
    finally:
        if not handed_off:
            await cm.__aexit__(None, None, None)


@router.post("/publish/login-password")
async def login_password(req: PasswordLoginIn,
                         x_publish_service_token: Optional[str] = Header(None)):
    """Complete account/password login through the fixed proxy and return the login session; account and password are not recorded."""
    _require_token(x_publish_service_token)
    if not ACCOUNT_LOGIN_CHANNELS_ENABLED:
        raise HTTPException(status_code=400, detail="account login channels disabled")
    if (req.platform or "").strip().lower() not in _PASSWORD_LOGIN_PLATFORMS:
        raise HTTPException(status_code=400,
                            detail=f"password login unsupported for platform={req.platform}")
    adapter = get_publisher(req.platform)
    if adapter is None:
        raise HTTPException(status_code=400,
                            detail=f"no publisher adapter for platform={req.platform}")
    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=PUBLISH_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="too many concurrent publish operations")
    try:
        return await asyncio.wait_for(_login_password_inner(adapter, req), timeout=VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "login_timeout", "detail": "登录超时"}
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] password login failed platform=%s", req.platform)
        return {"ok": False, "error": "login_failed",
                "detail": f"{type(e).__name__}: {e}"[:200]}
    finally:
        _semaphore().release()


async def _login_password_inner(adapter, req: PasswordLoginIn) -> dict:
    """Account/password login. Selectors are lightly configured per platform; exits conservatively when the page requires verification."""
    seed = req.expected_account_id or hashlib.sha256(
        f"{req.platform}:{req.identifier}".encode("utf-8")).hexdigest()
    cm = account_page(req.platform, {}, proxy_url=req.proxy_url,
                      ua_platform=req.ua_platform, fingerprint_seed=seed,
                      headless=False if MANUAL_LOGIN_HEADED else None)
    page = await cm.__aenter__()
    handed_off = False
    try:
        await page.goto(adapter.login_url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(2)
        cfg = _password_login_config(req.platform)
        for sel in cfg["tab"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1200):
                    await el.click()
                    break
            except Exception:  # noqa: BLE001
                pass

        account = page.locator(cfg["account"]).first
        password = page.locator(cfg["password"]).first
        try:
            await account.wait_for(state="visible", timeout=10_000)
            await password.wait_for(state="visible", timeout=10_000)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "login_form_not_found",
                    "detail": "未找到账号密码登录表单，平台可能要求扫码或验证"}
        await account.fill(req.identifier)
        await password.fill(req.password)
        submit = page.locator(cfg["submit"]).first
        try:
            await submit.click(timeout=5_000)
        except Exception:  # noqa: BLE001
            await password.press("Enter")

        account_id = None
        for _ in range(15):
            await asyncio.sleep(2)
            if isinstance(adapter, CoreBackedPublisher):
                probe = await _core_identity_from_page(
                    req.platform, page, proxy_url=req.proxy_url,
                    ua_platform=req.ua_platform,
                    expected_account_id=req.expected_account_id)
                account_id = probe.get("account_id") if probe.get("ok") else None
            else:
                account_id = await adapter.verify_identity(page)
            if account_id:
                break
            try:
                body = (await page.locator("body").inner_text()).lower()
            except Exception:  # noqa: BLE001
                body = ""
            if any(x in body for x in ("密码错误", "账号或密码错误", "帐号或密码错误")):
                return {"ok": False, "error": "bad_credentials", "detail": "账号或密码错误"}
            if any(x in body for x in ("安全验证", "验证码", "完成验证", "扫码登录")):
                session = await _hold_manual_login(cm, page, adapter, req)
                handed_off = True
                return {"ok": False, "error": "challenge_required",
                        "detail": "平台要求验证码、滑块或扫码，请在人工接管窗口完成验证",
                        **session}
        if not account_id:
            return {"ok": False, "error": "login_failed", "detail": "未检测到登录成功"}
        if req.expected_account_id and account_id != req.expected_account_id:
            return {"ok": False, "error": "identity_mismatch",
                    "detail": "登录身份与原账号不一致"}
        if isinstance(adapter, CoreBackedPublisher):
            probe = await _core_identity_from_page(
                req.platform, page, proxy_url=req.proxy_url,
                ua_platform=req.ua_platform,
                expected_account_id=req.expected_account_id)
            account_handle = probe.get("account_handle")
            exit_ip = probe.get("exit_ip")
        else:
            account_handle = await _read_account_handle(adapter, page)
            exit_ip = await adapter.resolve_exit_ip(page)
        state = await page.context.storage_state()
        user_agent = await page.evaluate("navigator.userAgent")
        ua_platform = await page.evaluate("navigator.platform")
        from .publish_loop import worker_id
        return {"ok": True, "account_id": account_id, "exit_ip": exit_ip,
                "account_handle": account_handle,
                "storage_state": state, "user_agent": user_agent,
                "ua_platform": ua_platform, "worker_id": worker_id(), "error": None}
    finally:
        if not handed_off:
            await cm.__aexit__(None, None, None)


async def _manual_preview(page) -> Optional[str]:
    """Return a screenshot of the current window. Returned only briefly within admin-initiated time-limited sessions, never written to disk."""
    try:
        png = await page.screenshot(full_page=False, timeout=5_000)
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _publish_qr_selectors(platform: str, key: str) -> tuple[str, ...]:
    cfg = _PUBLISH_QR_CONFIG.get((platform or "").strip().lower(), {})
    specific = tuple(cfg.get(key, ()))
    default = tuple(_PUBLISH_QR_CONFIG["__default__"][key])
    return specific + tuple(sel for sel in default if sel not in specific)


def _password_login_config(platform: str) -> dict:
    cfg = _PASSWORD_LOGIN_CONFIG.get((platform or "").strip().lower(), {})
    default = _PASSWORD_LOGIN_CONFIG["__default__"]
    return {**default, **cfg}


async def _manual_qr_image(platform: str, page, *, click_entry: bool = False) -> Optional[str]:
    """Best-effort QR element screenshot; failure falls back to full preview."""
    if (platform or "").strip().lower() in _NO_QR_PLATFORMS:
        return None
    if not hasattr(page, "locator"):
        return None
    found = await _grab_manual_qr_image(platform, page)
    if found or not click_entry:
        return found
    for sel in _publish_qr_selectors(platform, "entry"):
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=250):
                await el.click(timeout=1_000)
                await asyncio.sleep(0.3)
                break
        except Exception:  # noqa: BLE001
            continue
    return await _grab_manual_qr_image(platform, page)


async def _grab_manual_qr_image(platform: str, page) -> Optional[str]:
    for sel in _publish_qr_selectors(platform, "qr"):
        try:
            el = page.locator(sel).first
            if not await el.is_visible(timeout=250):
                continue
            box = await el.bounding_box()
            if box and (box.get("width", 0) < 80 or box.get("height", 0) < 80):
                continue
            png = await el.screenshot(timeout=3_000)
            return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        except Exception:  # noqa: BLE001
            continue
    return None


async def _manual_qr_visible(platform: str, page) -> bool:
    if (platform or "").strip().lower() in _NO_QR_PLATFORMS:
        return False
    if not hasattr(page, "locator"):
        return False
    for sel in _publish_qr_selectors(platform, "qr"):
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=120):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _close_manual_login(session: dict) -> None:
    try:
        await session["cm"].__aexit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


async def _gc_manual_logins() -> None:
    now = time.monotonic()
    expired = [sid for sid, s in _manual_login_sessions.items()
               if now - s["created"] > MANUAL_LOGIN_TTL]
    for sid in expired:
        session = _manual_login_sessions.pop(sid, None)
        if session:
            await _close_manual_login(session)


async def _hold_manual_login(cm, page, adapter, req: PasswordLoginIn | WebLoginIn) -> dict:
    await _gc_manual_logins()
    if len(_manual_login_sessions) >= MANUAL_LOGIN_MAX_SESSIONS:
        raise RuntimeError("人工登录会话已满，请稍后重试")
    sid = secrets.token_urlsafe(24)
    qr_image = await _manual_qr_image(req.platform, page, click_entry=True)
    _manual_login_sessions[sid] = {
        "cm": cm, "page": page, "adapter": adapter,
        "platform": req.platform, "expected_account_id": req.expected_account_id,
        "proxy_url": req.proxy_url, "ua_platform": req.ua_platform,
        "created": time.monotonic(), "qr_image": qr_image,
    }
    return {"manual_session_id": sid,
            "qr_image": qr_image,
            "preview_image": None if qr_image else await _manual_preview(page),
            "interactive": MANUAL_LOGIN_HEADED,
            "expires_in": MANUAL_LOGIN_TTL}


@router.post("/publish/login-web/poll")
@router.post("/publish/login-password/manual-poll")
async def manual_login_poll(req: ManualLoginSessionIn,
                            x_publish_service_token: Optional[str] = Header(None)):
    """Poll the manual takeover result; on success, export the login session and destroy the browser session."""
    _require_token(x_publish_service_token)
    await _gc_manual_logins()
    session = _manual_login_sessions.get(req.session_id)
    if not session:
        return {"status": "expired"}
    page, adapter = session["page"], session["adapter"]
    try:
        probe = None
        if isinstance(adapter, CoreBackedPublisher):
            qr_still_visible = bool(session.get("qr_image")) and \
                await _manual_qr_visible(session["platform"], page)
            account_id = None if qr_still_visible else await adapter.verify_identity(page)
            if account_id:
                state = await page.context.storage_state()
                probe = await verify_identity_with_core(
                    platform=session["platform"], storage_state=state,
                    proxy_url=session.get("proxy_url"),
                    ua_platform=session.get("ua_platform"),
                    expected_account_id=session.get("expected_account_id"))
                if not probe.get("ok") and probe.get("fail_type") != "login_lost":
                    _manual_login_sessions.pop(req.session_id, None)
                    await _close_manual_login(session)
                    return {"status": "error", "error": probe.get("fail_type") or "core_auth_failed",
                            "detail": probe.get("error") or "WechatSync sidecar 身份探测失败"}
                account_id = probe.get("account_id") if probe.get("ok") else None
        else:
            account_id = await adapter.verify_identity(page)
        if not account_id:
            left = max(0, int(MANUAL_LOGIN_TTL -
                              (time.monotonic() - session["created"])))
            qr_image = session.get("qr_image")
            if not qr_image:
                qr_image = await _manual_qr_image(session["platform"], page)
                if qr_image:
                    session["qr_image"] = qr_image
            return {"status": "pending",
                    "qr_image": qr_image,
                    "preview_image": None if qr_image else await _manual_preview(page),
                    "interactive": MANUAL_LOGIN_HEADED, "expires_in": left}
        expected = session.get("expected_account_id")
        if expected and account_id != expected:
            _manual_login_sessions.pop(req.session_id, None)
            await _close_manual_login(session)
            return {"status": "error", "error": "identity_mismatch",
                    "detail": "登录身份与原账号不一致"}
        account_handle = (probe or {}).get("account_handle") if probe else \
            await _read_account_handle(adapter, page)
        exit_ip = (probe or {}).get("exit_ip") if probe else await adapter.resolve_exit_ip(page)
        state = await page.context.storage_state()
        user_agent = await page.evaluate("navigator.userAgent")
        ua_platform = await page.evaluate("navigator.platform")
        from .publish_loop import worker_id
        result = {"status": "done", "ok": True, "account_id": account_id,
                  "exit_ip": exit_ip, "account_handle": account_handle,
                  "storage_state": state,
                  "user_agent": user_agent, "ua_platform": ua_platform,
                  "worker_id": worker_id(), "error": None}
        _manual_login_sessions.pop(req.session_id, None)
        await _close_manual_login(session)
        return result
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] manual login poll failed platform=%s", session["platform"])
        _manual_login_sessions.pop(req.session_id, None)
        await _close_manual_login(session)
        return {"status": "error", "error": "manual_login_failed",
                "detail": f"{type(e).__name__}: {e}"[:200]}


@router.post("/publish/login-web/cancel")
@router.post("/publish/login-password/manual-cancel")
async def manual_login_cancel(req: ManualLoginSessionIn,
                              x_publish_service_token: Optional[str] = Header(None)):
    _require_token(x_publish_service_token)
    session = _manual_login_sessions.pop(req.session_id, None)
    if session:
        await _close_manual_login(session)
    return {"ok": True}


@router.post("/publish/verify-identity")
async def verify_identity(req: VerifyIdentityIn,
                          x_publish_service_token: Optional[str] = Header(None)):
    """Read the current login identity -> {ok, account_id, exit_ip, fail_type, error}.

    Two callers:
      1. Backend account registration (POST /api/publish-accounts/register) - the account_id resolved in this step
         is the **authoritative identity definition** of the account, written to publish_accounts.account_id;
      2. Troubleshooting / health checks - confirming an account is still itself.

    🔴 Must be called with the account's bound proxy_url. The proxy used at registration must exactly equal the runtime proxy,
    otherwise the first publish is an off-site login (design §4 P0 item 3).
    """
    _require_token(x_publish_service_token)
    adapter = get_publisher(req.platform)
    if adapter is None:
        raise HTTPException(status_code=400,
                            detail=f"no publisher adapter for platform={req.platform}")
    if not (req.storage_state or {}).get("cookies"):
        # Same check as engine_sessions.upload rejecting a storage_state without cookies:
        # no cookies basically means "not logged in"; do not waste a browser launch.
        raise HTTPException(status_code=400, detail="storage_state 没有 cookies,大概未登录")

    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=PUBLISH_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="too many concurrent publish operations")
    try:
        return await asyncio.wait_for(
            _verify_inner(adapter, req), timeout=VERIFY_TIMEOUT)
    except asyncio.TimeoutError:
        return {"ok": False, "account_id": None, "account_handle": None, "exit_ip": None,
                "fail_type": "timeout", "error": "verify-identity timed out"}
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] verify-identity failed platform=%s", req.platform)
        return {"ok": False, "account_id": None, "account_handle": None, "exit_ip": None,
                "fail_type": "crash", "error": f"{type(e).__name__}: {e}"}
    finally:
        _semaphore().release()


async def _verify_inner(adapter, req: VerifyIdentityIn) -> dict:
    from .publish_loop import worker_id

    if isinstance(adapter, CoreBackedPublisher):
        result = await verify_identity_with_core(
            platform=req.platform, storage_state=req.storage_state,
            proxy_url=req.proxy_url, user_agent=req.user_agent,
            ua_platform=req.ua_platform,
            expected_account_id=req.expected_account_id)
        if result.get("storage_state"):
            # verify-identity callers only need identity, nickname and egress; login sessions are saved by the login flow.
            result = {k: v for k, v in result.items() if k != "storage_state"}
        return {**result, "worker_id": worker_id()}

    async with account_page(req.platform, req.storage_state,
                            proxy_url=req.proxy_url, ua_platform=req.ua_platform,
                            fingerprint_seed=req.expected_account_id) as page:
        account_id = await adapter.verify_identity(page)
        account_handle = await _read_account_handle(adapter, page) if account_id else None
        exit_ip = await adapter.resolve_exit_ip(page)
    if account_id is None:
        return {"ok": False, "account_id": None, "account_handle": None, "exit_ip": exit_ip,
                "fail_type": "login_lost", "error": "读不到当前登录身份(票据可能已失效)"}
    if req.expected_account_id and account_id != req.expected_account_id:
        return {"ok": False, "account_id": account_id, "account_handle": account_handle,
                "exit_ip": exit_ip,
                "fail_type": "identity_mismatch",
                "error": "当前登录身份与期望账号不一致"}
    return {"ok": True, "account_id": account_id, "account_handle": account_handle,
            "exit_ip": exit_ip,
            "worker_id": worker_id(), "fail_type": None, "error": None}


@router.get("/publish/worker-info")
async def publish_worker_info():
    """Numeric id of the local worker - for the backend healer's **ownership check**.

    A publish account's `owner_worker_id` is a permanent hard binding, because the same account must never appear on two machines:
    different machines differ in TLS fingerprint, font list, time zone and kernel features, a stealthier and harder
    ban signal than IP drift (design §F4). Keep-alive is likewise "opening a browser with this account", so **the same ban applies** -
    the backend healer compares this value with `owner_worker_id` before probing, and on mismatch skips and names it,
    rather than having whatever browser-service is at hand run it.

    worker_id directly reuses publish_loop's implementation (crc32(WORKER_UID) & 0x7FFFFFFF, same as the backend
    claim), rather than copying those 4 lines here - if the two algorithms drift, every account becomes "ownership mismatch".
    """
    from .publish_loop import worker_id
    wid = worker_id()
    return {"worker_id": wid, "worker_uid_set": wid is not None,
            "platforms": supported_platforms()}


@router.post("/publish/keepalive")
async def keepalive(req: KeepaliveIn,
                    x_publish_service_token: Optional[str] = Header(None)):
    """Keep-alive + health check + renewal, all done in one browser pass -> consumed by the backend publish_pool_healer.

    The three are inherently three sides of the same visit (design §F6):

      keep-alive    one real logged-in home page visit = "periodic low-frequency active use"
      health check  read /me once after the visit = live ticket probe (expiry is judged **only** by this, not by the fail_type
            reported by workers - both wrongful kills, Wenxin's 6/8 accounts and qwen's 9 accounts, came from trusting the reporter)
      renewal       export storage_state after the visit: the platform rotated tickets in its responses, so this copy is the new one

    ## fail_type has only three levels, and the levels are the reason this endpoint exists

        None          account alive
        login_lost    **reached the platform**, and the platform does not recognize you = authoritative login loss
        unreachable   🔴 did not reach the platform (proxy down/network down/site down) - **not the account's fault**,
                      the backend must not change a single field

    `/publish/verify-identity` cannot make this distinction: it maps every None from `verify_identity()`
    to login_lost, so network down = login loss. See the docstring of publishers/base.py::keepalive.

    ## 🔴 The response contains a plaintext login session

    It is the same thing delivered by claim, over the same 127.0.0.1 private link, so it adds no new exposure.
    But **not a single character may appear in logs** - every log below records only ok/fail_type/account id.
    """
    _require_token(x_publish_service_token)
    adapter = get_publisher(req.platform)
    if adapter is None:
        raise HTTPException(status_code=400,
                            detail=f"no publisher adapter for platform={req.platform}")
    has_official_api = req.platform == "wxgzh" and bool(req.official_api)
    if not (req.storage_state or {}).get("cookies") and not has_official_api:
        raise HTTPException(status_code=400, detail="storage_state 没有 cookies,大概未登录")

    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=PUBLISH_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="too many concurrent publish operations")
    try:
        out = await asyncio.wait_for(_keepalive_inner(adapter, req), timeout=KEEPALIVE_TIMEOUT)
    except asyncio.TimeoutError:
        # 🔴 Timeouts count as unreachable, not login_lost. Keep-alive has no irreversible consequence like "it may already have gone out"
        # (it clicks no submit button), so the worst conclusion is "this pass did not see clearly" - and treating not seeing clearly as
        # login loss is exactly how qwen's 9 accounts died.
        out = _keepalive_fail("unreachable", f"keepalive timed out ({KEEPALIVE_TIMEOUT}s)")
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] keepalive failed platform=%s", req.platform)
        out = _keepalive_fail("unreachable", f"{type(e).__name__}: {e}")
    finally:
        _semaphore().release()
    _log.info("[publish] keepalive platform=%s ok=%s reachable=%s fail_type=%s",
              req.platform, out["ok"], out["reachable"], out["fail_type"])
    return out


def _keepalive_fail(fail_type: str, error: str, *, reachable: bool = False,
                    account_id: Optional[str] = None,
                    exit_ip: Optional[str] = None,
                    screenshot_path: Optional[str] = None) -> dict:
    """Failure response. **storage_state is always None** - if we did not see clearly, do not write back the login session."""
    return {"ok": False, "reachable": reachable, "account_id": account_id,
            "account_handle": None, "exit_ip": exit_ip, "storage_state": None,
            "fail_type": fail_type, "error": error,
            "screenshot_path": screenshot_path}


async def _keepalive_inner(adapter, req: KeepaliveIn) -> dict:
    if isinstance(adapter, CoreBackedPublisher):
        result = await verify_identity_with_core(
            platform=req.platform, storage_state=req.storage_state,
            proxy_url=req.proxy_url, user_agent=req.user_agent,
            ua_platform=req.ua_platform,
            expected_account_id=req.expected_account_id)
        fail_type = result.get("fail_type")
        if not result.get("ok"):
            if fail_type == "login_lost":
                return _keepalive_fail(
                    "login_lost", result.get("error") or "已到达平台但读不到登录身份",
                    reachable=True, exit_ip=result.get("exit_ip"))
            if fail_type == "identity_mismatch":
                return _keepalive_fail(
                    "identity_mismatch", result.get("error") or "当前登录身份与期望账号不一致",
                    reachable=True, account_id=result.get("account_id"),
                    exit_ip=result.get("exit_ip"))
            return _keepalive_fail(
                "unreachable", result.get("error") or "Core sidecar 身份探测失败")
        return {"ok": True, "reachable": True, "account_id": result.get("account_id"),
                "account_handle": result.get("account_handle"),
                "exit_ip": result.get("exit_ip"),
                "storage_state": result.get("storage_state") or req.storage_state,
                "fail_type": None, "error": None, "screenshot_path": None}

    async with account_page(req.platform, req.storage_state,
                            proxy_url=req.proxy_url, ua_platform=req.ua_platform,
                            fingerprint_seed=req.expected_account_id) as page:
        reachable, account_id = await adapter.keepalive(page)
        if not reachable:
            # **No** screenshot when unreachable: the page never loaded, so it would be a blank image that only wastes disk.
            return _keepalive_fail("unreachable", "导航失败(代理/网络/站点不可达)")
        exit_ip = await adapter.resolve_exit_ip(page)
        if account_id is None:
            return _keepalive_fail(
                "login_lost", "已到达平台但读不到登录身份(票据已失效,需重新授权)",
                reachable=True, exit_ip=exit_ip,
                screenshot_path=await adapter.screenshot(page, "keepalive-login-lost"))
        if account_id != req.expected_account_id:
            return _keepalive_fail(
                "identity_mismatch", "当前登录身份与期望账号不一致",
                reachable=True, account_id=account_id, exit_ip=exit_ip,
                screenshot_path=await adapter.screenshot(page, "keepalive-mismatch"))
        account_handle = await _read_account_handle(adapter, page)
        # The new login session for renewal. Taken last: the earlier navigation/API calls may all make the platform rotate tickets,
        # so taking it early would get the old copy.
        state = await page.context.storage_state()
    return {"ok": True, "reachable": True, "account_id": account_id,
            "account_handle": account_handle,
            "exit_ip": exit_ip, "storage_state": state,
            "fail_type": None, "error": None, "screenshot_path": None}


@router.post("/publish")
async def publish(req: PublishIn, x_publish_service_token: Optional[str] = Header(None)):
    """Run one publish. Returns PublishResult.as_dict().

    HTTP semantics are deliberately the same as /search: **business failures also return 200**, with the failure info in the body's
    fail_type / error. The backend relies on fail_type for precise classification (submit_unknown must never be
    retried as a "network error"); using 5xx would erase that distinction.
    Only "the request itself is invalid" (unknown platform / missing cookie / bad token / concurrency full) uses 4xx.
    """
    _require_token(x_publish_service_token)
    adapter = get_publisher(req.platform)
    if adapter is None:
        raise HTTPException(status_code=400,
                            detail=f"no publisher adapter for platform={req.platform}")
    has_official_api = req.platform == "wxgzh" and bool(req.official_api)
    if not (req.storage_state or {}).get("cookies") and not has_official_api:
        raise HTTPException(status_code=400, detail="storage_state 没有 cookies,大概未登录")
    if not (req.title or "").strip():
        raise HTTPException(status_code=400, detail="title required")

    try:
        await asyncio.wait_for(_semaphore().acquire(), timeout=PUBLISH_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="too many concurrent publish operations")
    # Container for submit traces. **Held here**, not by the PublishRequest inside _publish_inner -
    # a wait_for timeout cancels the whole _publish_inner and everything on the coroutine stack is gone; only this
    # dict remains. The adapter writes an entry into it before clicking the publish button (PublishRequest.mark_committed).
    commit: dict = {}
    try:
        if isinstance(adapter, CoreBackedPublisher):
            # Core internally decides "whether the request may have been delivered" via its operation journal + commit.
            # It must not be cut off by an outer wait_for, otherwise when commit was already delivered, not seeing the sidecar's
            # operation log here would misjudge it as a retryable timeout.
            return await publish_with_core(req)
        return await asyncio.wait_for(_publish_inner(adapter, req, commit),
                                      timeout=PUBLISH_TIMEOUT)
    except asyncio.TimeoutError:
        # Whether the timeout happened **before the click** or **after the click** are two completely different conclusions - and
        # `asyncio.wait_for` cuts off at an arbitrary point in the flow and cannot tell by itself. So we can only ask the submit traces.
        #
        # When unclear, it must not default to retryable: the tens of seconds waiting for the article URL are exactly the slowest part of the whole flow,
        # i.e. where a timeout is most likely to land - and that is right after the click.
        return _unknown_or_timeout(
            commit, "publish timed out",
            f"发布流程超时({PUBLISH_TIMEOUT}s)且已点击过发布按钮")
    except Exception as e:  # noqa: BLE001
        _log.exception("[publish] failed platform=%s", req.platform)
        # Likewise: crashing after the click, the platform may already have received it. crash is the retryable level, so it cannot be used.
        return _unknown_or_timeout(
            commit, f"{type(e).__name__}: {e}",
            f"发布流程异常({type(e).__name__}: {e})且已点击过发布按钮",
            clean_fail_type="crash")
    finally:
        _semaphore().release()


def _unknown_or_timeout(commit: dict, clean_error: str, dirty_error: str,
                        clean_fail_type: str = "timeout") -> dict:
    """Without an adapter conclusion, decide fail_type by "was publish clicked or not".

    🔴 Only taken when **no PublishResult is available** (cancelled by timeout / uncaught exception). The adapter's
    own conclusion always takes precedence - it knows more than this dict: e.g. when "the click action itself threw" it
    judges dom_not_found (retryable), while commit already has a trace by then and should not be overridden as submit_unknown.

    No trace -> the platform was never touched -> clean_fail_type (timeout / crash, both retryable).
    Trace present -> **result unknown** -> submit_unknown: never retried, reconciled via draft_id.
    """
    if not commit.get("clicked"):
        return {"ok": False, "fail_type": clean_fail_type, "error": clean_error,
                "failure_phase": "pre_commit", "confirmed_not_published": True,
                "published_url": None, "draft_id": commit.get("draft_id"),
                "draft_url": commit.get("draft_url"),
                "screenshot_path": commit.get("screenshot_path"),
                "exit_ip": None, "observed_account_id": None}
    draft_id = commit.get("draft_id")
    _log.error("[publish] 🔴 Result unknown: publish was clicked but no conclusion was obtained - %s. "
               "draft_id=%s, judged submit_unknown (retry forbidden)", dirty_error, draft_id)
    return {"ok": False, "fail_type": "submit_unknown",
            "failure_phase": "commit", "confirmed_not_published": False,
            "error": f"{dirty_error};请凭 draft_id={draft_id} 对账,不要重试",
            "published_url": None, "draft_id": draft_id,
            "draft_url": commit.get("draft_url"),
            "screenshot_path": commit.get("screenshot_path"),
            "exit_ip": None, "observed_account_id": None}


async def _publish_inner(adapter, req: PublishIn, commit: dict) -> dict:
    async with account_page(req.platform, req.storage_state,
                            proxy_url=req.proxy_url, ua_platform=req.ua_platform,
                            fingerprint_seed=req.account_id or None) as page:
        result = await adapter.publish(page, PublishRequest(
            platform=req.platform,
            account_id=req.account_id,
            title=req.title,
            body=req.body,
            body_format=req.body_format,
            images=list(req.images or []),
            dry_run=req.dry_run,
            # Pass the same dict object down - the adapter writes it, the except branches above read it.
            commit=commit,
        ))
        if result.draft_id and not result.draft_url:
            result.draft_url = adapter.observed_draft_url(page, result.draft_id)
    out = result.as_dict()
    if result.ok:
        return out
    clicked = bool(commit.get("clicked"))
    if result.fail_type == "submit_unknown":
        out.update(failure_phase="commit", confirmed_not_published=False)
    elif result.fail_type == "content_rejected":
        # content_rejected is the adapter's structured conclusion for an explicit platform rejection; there is no public success URL.
        out.update(failure_phase="platform_rejected" if clicked else "prepare",
                   confirmed_not_published=True)
    elif not clicked:
        out.update(failure_phase="pre_commit", confirmed_not_published=True)
    else:
        # A commit trace was written before clicking submit; if the adapter then gives no evidence of "confirmed not published",
        # always treat it as possibly delivered, and forbid automatically retrying engineering errors like dom/crash into duplicate articles.
        out.update(fail_type="submit_unknown", failure_phase="commit",
                   confirmed_not_published=False,
                   error=f"提交可能已送达平台:{result.error or result.fail_type or 'unknown'}")
    return out


@router.get("/publish/screenshot/{platform}/{filename}")
async def publish_screenshot(platform: str, filename: str):
    """Fetch a publish screenshot (retrieval entry for F10 audit trails).

    The path parameter deliberately does **not** use `{filename:path}` - main.py's `/snapshot/{engine}/{filename:path}`
    is written that way and allows `../` traversal. resolve_screenshot() additionally does a basename + resolve
    double check, returning 404 when out of bounds.
    """
    p = resolve_screenshot(platform, filename)
    if p is None:
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(str(p), media_type="image/png")


@router.get("/publish/screenshot/core/{platform}/{filename}")
async def core_publish_screenshot(platform: str, filename: str):
    """Proxy the sidecar screenshot; the path stays a single segment and the sidecar does another basename check."""
    if "/" in filename or "\\" in filename or platform not in set(supported_platforms()):
        raise HTTPException(status_code=404, detail="screenshot not found")
    try:
        content, media_type = await fetch_core_screenshot(platform, filename)
        return Response(content=content, media_type=media_type)
    except Exception:
        raise HTTPException(status_code=404, detail="screenshot not found")
