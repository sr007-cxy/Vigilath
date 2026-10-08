"""SMS code assisted authorization - a stateful SMS code flow (for engines like qwen whose only login is "phone number + code").

Same shape as qr_authorize, just replacing "scan the QR code" with "a person types the SMS code into the admin console":
  start(engine, phone) -> open a browser to the login page -> switch to phone login -> fill in the phone number -> click "获取验证码" ("Get code")
                         -> return {session_id, status: code_sent, screenshot}
  submit(session_id, code) -> fill in the code -> tick the agreement -> click log in -> wait for the login cookie
                         -> present: capture storage_state into the pool (auth_type='sms'), close the page, done
                         -> absent: return failed + screenshot (common: wrong code / slider)
  poll(session_id)      -> logged in? (e.g. the user clicked elsewhere) -> done; otherwise pending

Why screenshots: the login layer is in passport's iframe, and whether selectors hit is invisible remotely; sending the code
often hits a slider, and only by giving the admin console the page as it looked can a person decide between "click again" and "switch egress".
The browser page stays open between start..submit (held by the module-level _sessions) and is cleared on expiry (SMS_TTL).
No credentials stored (SMS codes are one-time), auth_type='sms'; after disconnects, relies on dashboard alerts + manual re-login.

kimi (2026-09-18): the login layer is on the main page (not in an iframe), and sending the code **always** triggers a NetEase Yidun slider, which we do not solve automatically
(that would be bypassing risk control). start returns status=captcha + a screenshot, a person drags the slider on the server display via VNC
(browser-service's headed browser runs on Xvfb :99, exposed by scripts/setup_vnc.sh), and once the code arrives,
submit; poll also accepts the case where "a person completed the whole login in VNC". The login criterion is not a cookie but
access_token / refresh_token appearing in localStorage (auth_localstorage); ctx.storage_state() already includes
localStorage, so upload needs no change.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import time
from typing import Optional

import httpx

_POOL_URL = os.environ.get("ENGINE_SESSION_POOL_URL", "").rstrip("/")
_SERVICE_TOKEN = os.environ.get("ENGINE_SESSION_SERVICE_TOKEN", "")
_SMS_TTL = int(os.environ.get("SMS_SESSION_TTL_SEC", "300"))

# session_id -> {ctx, page, engine, phone, created, code_sent_at}
_sessions: dict = {}

# Per engine: login page, opening the login layer, switching to the phone tab, phone/code inputs, send-code/submit buttons,
# agreement checkbox, login success cookie. Selectors are all "multiple candidates tried in order", so when page copy changes just add candidates.
_SMS_CONFIG = {
    "qwen": {
        "url": "https://www.qianwen.com/",
        "pre_click": ["text=登录", "text=立即登录"],
        # The login layer defaults to phone login; in case it stays on the QR/password tab, switch back
        "sms_tab": ["text=手机号登录", "text=验证码登录", "text=短信登录", "text=手机登录"],
        # Country code selection: Qianwen's login page has a "+86" dropdown left of the phone box; click it first, then click "+<code>" in the popup list.
        # Candidates are tried in order: the element showing the current country code / containers with country or area class names.
        "country_trigger": ["text=+86", "[class*='countryCode']", "[class*='country']", "[class*='areaCode']",
                            "[class*='area-code']", "[class*='prefix']"],
        "country_item": ["text=+{cc}", "li:has-text('+{cc}')", "[class*='item']:has-text('+{cc}')",
                         "div:has-text('+{cc}') >> nth=-1"],
        "phone_sel": ["input[placeholder*='手机']", "input[type='tel']", "input[name*='phone']",
                      "input[name*='mobile']", "input[placeholder*='请输入']"],
        "send_btn": ["text=获取验证码", "text=发送验证码", "text=获取短信验证码", "button:has-text('验证码')"],
        "code_sel": ["input[placeholder*='验证码']", "input[name*='code']", "input[autocomplete='one-time-code']",
                     "input[maxlength='6']"],
        "agree": ["input[type='checkbox']", "[class*='checkbox']", "[class*='agree']"],
        "submit": ["button:has-text('登录')", "button:has-text('登 录')", "text=登录/注册", "button[type='submit']"],
        "auth_cookies": ["tongyi_sso_ticket"],
        # These words appearing on the page after sending the code = hit a slider/human verification; a person must switch egress or try later
        "captcha_hint": ["滑动", "拖动", "拼图", "安全验证", "验证失败"],
    },
    # Selectors follow the login layer DOM captured headless on 2026-09-18: phone-form__* class names, km-button buttons,
    # Yidun hidden field input.yidun_input[name=NECaptchaValidate]. The top bar "登录" ("Log in") must be clicked by class name - the sidebar
    # has two more buttons whose copy contains "登录" outside the viewport, and text= would hit them first.
    "kimi": {
        "url": "https://www.kimi.com/",
        "pre_click": ["button.km-button-secondary:has-text('登录')", "button.user-area__main",
                      "button:has-text('登录'):visible"],
        "sms_tab": ["text=手机号码登录", "text=手机号登录"],
        "country_trigger": ["button.phone-form__region-trigger", "text=+86"],
        "country_item": ["text=+{cc}", "li:has-text('+{cc}')", "[class*='item']:has-text('+{cc}')"],
        "phone_sel": ["input.phone-form__phone-input", "input[placeholder='手机号']", "input[type='tel']"],
        "send_btn": ["button.phone-form__send", "text=发送验证码"],
        "code_sel": ["input.phone-form__code-input", "input[placeholder='验证码']", "input[maxlength='6']"],
        "agree": ["input[type='checkbox']"],
        "submit": ["button.km-button-primary:has-text('登录')", "button:has-text('登录'):visible"],
        # Not cookie-based: only when the three keys land in localStorage does it count as logged in (same metric definition as kimi_http / the pool gate)
        "auth_cookies": [],
        "auth_localstorage": ["access_token", "refresh_token"],
        # Copy must be narrow: the kimi home page permanently shows "探索灵感 / 滑动探索" ("Explore ideas / Swipe to explore"), so a generic "滑动" ("swipe") would be misjudged as a slider
        "captcha_hint": ["向右拖动", "拼图", "安全验证", "验证失败", "请完成验证", "请依次点击"],
        # DOM features of the Yidun overlay (more stable than copy)
        "captcha_sel": [".yidun_popup", ".yidun_modal", "[class*='yidun'][class*='popup']"],
        # Clicking send code without ticking the agreement -> pops a "同意 Kimi 的协议" ("Agree to Kimi's terms") dialog; clicking 「同意」 ("Agree") equals ticking it and continuing to send the code
        "consent_btn": ["button.km-button-primary:has-text('同意')"],
    },
}


def supports_sms(engine: str) -> bool:
    return engine in _SMS_CONFIG


def mask_phone(phone: str, cc: str = "86") -> str:
    p = re.sub(r"\D", "", phone or "")
    masked = f"{p[:3]}****{p[-4:]}" if len(p) >= 7 else "***"
    cc = re.sub(r"\D", "", cc or "86") or "86"
    return masked if cc == "86" else f"+{cc} {masked}"


def _frames(page):
    return [page] + [f for f in getattr(page, "frames", [])]


async def _click_any(page, selectors, timeout: int = 2500) -> bool:
    """Find the first visible selector in the main page + all iframes and click it (the passport login layer is in an iframe)."""
    for sel in selectors:
        for fr in _frames(page):
            try:
                el = fr.locator(sel).first
                if await el.is_visible(timeout=timeout):
                    await el.click()
                    return True
            except Exception:  # noqa: BLE001
                pass
    return False


async def _fill_any(page, selectors, value: str, timeout: int = 2500) -> bool:
    for sel in selectors:
        for fr in _frames(page):
            try:
                el = fr.locator(sel).first
                if await el.is_visible(timeout=timeout):
                    await el.click()
                    await el.fill(value)
                    return True
            except Exception:  # noqa: BLE001
                pass
    return False


async def _page_text(page) -> str:
    out = []
    for fr in _frames(page):
        try:
            out.append(await fr.locator("body").inner_text(timeout=1500))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(out)


async def _screenshot(page) -> Optional[str]:
    try:
        raw = await page.screenshot(type="jpeg", quality=60)
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    except Exception:  # noqa: BLE001
        return None


async def _is_logged_in(page, cfg) -> bool:
    """Login success criterion: a login cookie appears in the context (qwen=tongyi_sso_ticket),
    or (localStorage auth engines, kimi) the auth keys are all present in the page's localStorage."""
    ls_keys = cfg.get("auth_localstorage") or []
    if ls_keys:
        try:
            got = await page.evaluate(
                "(keys) => keys.filter(k => !!localStorage.getItem(k))", ls_keys)
            if len(got) == len(ls_keys):
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        names = {c.get("name", "") for c in await page.context.cookies()}
    except Exception:  # noqa: BLE001
        return False
    return any(a in names for a in cfg.get("auth_cookies") or [])


async def _captcha_showing(page, cfg, text: Optional[str] = None) -> bool:
    """Whether a slider/human verification is on the page: check DOM features first (captcha_sel), then copy (captcha_hint)."""
    for sel in cfg.get("captcha_sel") or []:
        for fr in _frames(page):
            try:
                if await fr.locator(sel).first.is_visible(timeout=300):
                    return True
            except Exception:  # noqa: BLE001
                pass
    if text is None:
        text = await _page_text(page)
    return any(h in text for h in cfg.get("captcha_hint", []))


async def _ensure_checked(page, selectors, timeout: int = 800) -> bool:
    """Agreement checkbox: use check() for checkboxes (an already ticked one will not be unticked), fall back to clicking for other elements."""
    for sel in selectors:
        for fr in _frames(page):
            try:
                el = fr.locator(sel).first
                if not await el.is_visible(timeout=timeout):
                    continue
                if await el.evaluate("e => e.tagName === 'INPUT' && e.type === 'checkbox'"):
                    await el.check(timeout=timeout)
                else:
                    await el.click()
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def _gc():
    now = time.time()
    for sid in list(_sessions.keys()):
        if now - _sessions[sid]["created"] > _SMS_TTL:
            s = _sessions.pop(sid, None)
            if s:
                asyncio.create_task(_close(s))


async def _close(s):
    try:
        await s["ctx"].close()
    except Exception:  # noqa: BLE001
        pass


async def _upload(engine: str, ctx, phone: str, session_id: str, cc: str = "86") -> tuple[bool, Optional[str]]:
    state = await ctx.storage_state()
    if not (_POOL_URL and _SERVICE_TOKEN):
        return False, "pool url/token not configured"
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(
                f"{_POOL_URL}/api/engine-sessions/upload",
                headers={"X-Service-Token": _SERVICE_TOKEN, "Content-Type": "application/json"},
                json={"engine": engine, "storage_state": state,
                      "source_label": f"sms:{session_id}", "auth_type": "sms",
                      "account_handle": mask_phone(phone, cc), "ttl_days": 14},
            )
        return (r.status_code == 201), (None if r.status_code == 201 else f"upload HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return False, f"upload failed: {e!r}"[:150]


async def _select_country(page, cfg, cc: str) -> bool:
    """Switch the country code to +cc in the login layer. The default +86 needs no switch. Returns whether the switch was confirmed (failing to switch does not block;
    the screenshot is sent back for a person to look at)."""
    cc = re.sub(r"\D", "", cc or "86") or "86"
    if cc == "86":
        return True
    if not await _click_any(page, cfg.get("country_trigger", []), timeout=1500):
        return False
    await asyncio.sleep(1)
    items = [sel.replace("{cc}", cc) for sel in cfg.get("country_item", [])]
    ok = await _click_any(page, items, timeout=1500)
    await asyncio.sleep(0.8)
    return ok


async def sms_start(engine: str, phone: str, country_code: str = "86") -> dict:
    """Open a browser to the login page, switch country code, fill in the phone number, click send code. Returns {session_id, status, screenshot, error}.
    phone is the **local number** (without country code); country_code is the country code digits (default 86)."""
    cfg = _SMS_CONFIG.get(engine)
    if not cfg:
        return {"error": "unsupported"}
    digits = re.sub(r"\D", "", phone or "")
    cc = re.sub(r"\D", "", country_code or "86") or "86"
    if len(digits) < 6:
        return {"error": "bad_phone"}
    _gc()
    from cloakbrowser import launch_context_async
    from .browser import get_context_options, _pick_profile
    opts = get_context_options(profile=_pick_profile(platform_filter="Linux x86_64"))
    ctx = await launch_context_async(headless=False, **opts)
    page = await ctx.new_page()
    try:
        await page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)
        await _click_any(page, cfg["pre_click"])
        await asyncio.sleep(2)
        await _click_any(page, cfg.get("sms_tab", []), timeout=1500)
        await asyncio.sleep(1)
        country_ok = await _select_country(page, cfg, cc)
        if not await _fill_any(page, cfg["phone_sel"], digits):
            shot = await _screenshot(page)
            await ctx.close()
            return {"error": "phone_input_not_found", "screenshot": shot}
        await asyncio.sleep(0.5)
        # kimi: the agreement must be ticked before sending the code, otherwise sending is intercepted by the "agree to terms" dialog; qwen's agree is ticked at submit,
        # ticking once here is harmless too (check() does not untick an already ticked one)
        if cfg.get("consent_btn"):
            await _ensure_checked(page, cfg.get("agree", []))
        sent = await _click_any(page, cfg["send_btn"])
        await asyncio.sleep(2.5)
        if sent and cfg.get("consent_btn") and await _click_any(page, cfg["consent_btn"], timeout=800):
            await asyncio.sleep(2.5)     # after agreeing the site continues the send-code flow itself (the Yidun slider pops up at this step)
        text = await _page_text(page)
        shot = await _screenshot(page)
        sid = secrets.token_urlsafe(12)
        _sessions[sid] = {"ctx": ctx, "page": page, "engine": engine, "phone": digits, "cc": cc,
                          "created": time.time(), "code_sent_at": time.time() if sent else None}
        if not sent:
            return {"session_id": sid, "status": "send_button_not_found", "screenshot": shot,
                    "error": "send_button_not_found", "country_ok": country_ok}
        if await _captcha_showing(page, cfg, text):
            # The page stays open: qwen occasionally hits a slider, and a person decides from the screenshot whether to switch egress or retry; kimi always has a Yidun slider,
            # which a person finishes dragging on the server display via VNC, then the code is sent, and they enter it in the admin console to submit.
            return {"session_id": sid, "status": "captcha", "screenshot": shot,
                    "error": "captcha_required", "country_ok": country_ok,
                    "captcha_manual": bool(cfg.get("auth_localstorage"))}
        # Even if the country code switch failed, return code_sent anyway (the code may have gone to the +86 number) - pass country_ok to
        # the admin console to prompt "please confirm the country code from the screenshot", and a person decides whether to continue or restart.
        return {"session_id": sid, "status": "code_sent", "screenshot": shot, "error": None,
                "country_ok": country_ok}
    except Exception as e:  # noqa: BLE001
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
        return {"error": repr(e)[:200]}


async def sms_submit(session_id: str, code: str) -> dict:
    """Fill in the code and click log in. done (in the pool) / failed (with screenshot) / expired."""
    s = _sessions.get(session_id)
    if not s:
        return {"status": "expired"}
    cfg = _SMS_CONFIG[s["engine"]]
    page = s["page"]
    code = re.sub(r"\D", "", code or "")
    if not code:
        return {"status": "failed", "error": "bad_code"}
    try:
        if not await _fill_any(page, cfg["code_sel"], code):
            return {"status": "failed", "error": "code_input_not_found", "screenshot": await _screenshot(page)}
        await _ensure_checked(page, cfg.get("agree", []))
        await _click_any(page, cfg["submit"])
        # The login cookie / localStorage usually lands within 1-5s; wait up to 15s
        for _ in range(15):
            await asyncio.sleep(1)
            if await _is_logged_in(page, cfg):
                break
        if not await _is_logged_in(page, cfg):
            hint = "captcha_required" if await _captcha_showing(page, cfg) else "not_logged_in"
            return {"status": "failed", "error": hint, "screenshot": await _screenshot(page)}
        uploaded, err = await _upload(s["engine"], s["ctx"], s["phone"], session_id, s.get("cc", "86"))
        _sessions.pop(session_id, None)
        await _close(s)
        return {"status": "done", "uploaded": uploaded, "error": err,
                "account_handle": mask_phone(s["phone"], s.get("cc", "86"))}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": repr(e)[:200]}


async def sms_poll(session_id: str) -> dict:
    """Poll: done / pending (with screenshot) / expired."""
    s = _sessions.get(session_id)
    if not s:
        return {"status": "expired"}
    cfg = _SMS_CONFIG[s["engine"]]
    try:
        if await _is_logged_in(s["page"], cfg):
            uploaded, err = await _upload(s["engine"], s["ctx"], s["phone"], session_id, s.get("cc", "86"))
            _sessions.pop(session_id, None)
            await _close(s)
            return {"status": "done", "uploaded": uploaded, "error": err,
                    "account_handle": mask_phone(s["phone"], s.get("cc", "86"))}
        # Not logged in: return the current screenshot + whether the slider is still there (kimi: after a person finishes dragging in VNC, refreshing the admin console shows it)
        return {"status": "pending", "screenshot": await _screenshot(s["page"]),
                "captcha": await _captcha_showing(s["page"], cfg)}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": repr(e)[:200]}


async def sms_cancel(session_id: str) -> dict:
    s = _sessions.pop(session_id, None)
    if s:
        await _close(s)
    return {"status": "closed"}
