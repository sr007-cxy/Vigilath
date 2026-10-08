"""Shared Playwright browser lifecycle management.

Provides a singleton browser instance that all browser adapters share, with
anti-detection and session support.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Optional

from .anti_detect import (
    build_stealth_js_for_profile,
    _pick_profile,
    get_context_options,
    get_context_options_international,
    get_launch_options,
)
from .session_store import load_storage_state, load_engine_profile, save_storage_state


async def apply_stealth_to_context(
    context, *, international: bool = False, profile: dict | None = None
) -> None:
    """Inject stealth JS at context level so it runs on every page navigation.

    The profile passed here MUST match the UA used for the context — otherwise
    navigator.userAgent (set by Playwright from context UA) will contradict
    navigator.platform / WebGL renderer (set by this stealth JS), which is an
    instant tell for advanced anti-bot systems (e.g. ByteDance / Doubao).
    """
    if profile is None:
        profile = _pick_profile()
    js = build_stealth_js_for_profile(profile, international=international)
    await context.add_init_script(js)

# Note: we previously layered playwright-stealth on top of our own stealth JS,
# but its randomized profile selection was reintroducing UA/platform/WebGL
# mismatches (the same fingerprint inconsistency we now guard against
# explicitly). All stealth is applied via apply_stealth_to_context() bound to
# a single per-session profile.

_browser = None
_lock = None
_pw_instance = None  # Keep reference to prevent GC


async def get_browser():
    """Get or create the shared Playwright browser instance.

    The launch UA arg is mostly cosmetic — actual UA is set per-context in
    create_stealth_page(). What matters is that the browser is launched once
    and reused across contexts, so per-context profile selection drives the
    visible fingerprint.
    """
    global _browser, _lock, _pw_instance
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _browser is None or not _browser.is_connected():
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise RuntimeError(
                    "playwright is not installed. Run: pip install playwright && playwright install chromium"
                )
            _pw_instance = await async_playwright().start()

            # Set PLAYWRIGHT_HEADLESS=0 for headed mode (e.g. for debugging)
            use_headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0"
            _browser = await _pw_instance.chromium.launch(**get_launch_options(headless=use_headless))
        return _browser


async def create_stealth_page(
    engine_name: str,
    *,
    locale: str | None = None,
    timezone_id: str | None = None,
    record_video: bool = False,
    profile: dict | None = None,
    storage_state: dict | None = None,
    proxy_url: str | None = None,
    headless: bool | None = None,
):
    """Create a new browser page with anti-detection and full session state.

    Args:
        engine_name: Engine identifier for session loading (e.g. "deepseek", "gemini").
        locale: Override locale (e.g. "en-US"). Defaults to "zh-CN".
        timezone_id: Override timezone (e.g. "America/New_York"). Defaults to "Asia/Shanghai".
        record_video: If True, record the session as an MP4 video.
        profile: Pre-picked UA/platform/WebGL profile dict. Pass the same
            profile across multiple page creations within one query lifecycle
            (e.g. headless → headed CAPTCHA retry) so the device identity
            stays consistent. If omitted, a random profile is picked.
        storage_state: explicit login session. For publish channels (app/publishers/) - their login sessions
            come from backend publish_accounts (decrypted and delivered over HTTP), **not** from session_store's
            engine slots, so they cannot go through load_storage_state. When omitted, behavior is exactly as before.
        proxy_url: explicit proxy string http://user:pass@host:port. As above, publish accounts' proxies are resolved by the backend
            from proxy_id and delivered. 🔴 Must be passed explicitly for publishing: the login proxy must exactly equal
            the runtime proxy and cannot fall back to the PROXY_ENGINES allowlist/environment default like the engine pool -
            login proxy != runtime proxy = an off-site login on the very first publish.
        headless: when given explicitly, opens a dedicated browser and overrides the global mode. Manual login takeover passes False so the window can
            be operated on the publishing machine's desktop; None keeps the shared browser/PLAYWRIGHT_HEADLESS.

    These two new parameters are **purely additive**: when neither is passed, the original path runs line by line (load_storage_state + _engine_proxy),
    with zero change to the engine query chain. Added here rather than in a separate copy to reuse for free the lifecycle of "open a dedicated browser
    instance when a proxy is needed + hang the handle on the context" - rewriting it would certainly miss pw.stop(),
    leaking one chromium process per publish. Cleanup always uses close_stealth_context().
    """
    international = locale is not None and locale != "zh-CN"

    # Pick ONE profile up-front and use it everywhere: context UA, stealth JS
    # platform/vendor, and WebGL renderer. Mismatching these is the #1 way
    # ByteDance-style anti-bot systems detect Playwright.
    if profile is None:
        profile = _pick_profile()

    if locale and timezone_id:
        ctx_opts = get_context_options(
            locale=locale, timezone_id=timezone_id, profile=profile
        )
    elif international:
        ctx_opts = get_context_options_international(profile=profile)
    else:
        ctx_opts = get_context_options(profile=profile)

    # Restore full storage_state (cookies + localStorage) if available
    # (must happen before getting the browser: the proxy is bound to the account just checked out, so get the account first)
    state = storage_state if storage_state is not None else load_storage_state(engine_name)
    if state:
        ctx_opts["storage_state"] = state

    # Video recording
    if record_video:
        from .video_store import get_snapshot_dir
        video_dir = str(get_snapshot_dir(engine_name))
        ctx_opts["record_video_dir"] = video_dir
        ctx_opts["record_video_size"] = {"width": 1920, "height": 1080}

    # ── Per-account bound egress proxy (fixed 2026-08) ────────────────────────────
    # Previously only create_headed_page applied the proxy and this function did not - so engines going through this function
    # (qwen subprocess, hot sessions like wenxin, heal_probe) **ran bare from the worker's own IP even with a proxy bound**.
    # Measured: 12 qwen accounts with bound proxies reported the worker's own IP, and the same account claimed by different workers
    # switched egress each time - that is the real "off-site login", the top cause of account bans.
    #
    # Chromium's per-context proxy requires the browser to also be launched with a proxy (official limitation), so
    # when a proxy is needed **open a dedicated browser instance with that proxy** instead of reusing the shared instance; the shared instance
    # keeps serving accounts that need no proxy, behavior unchanged. The caller closes it when done (see _proxy_browser below).
    proxy = parse_proxy_url(proxy_url) if proxy_url else _engine_proxy(engine_name, state)
    # An explicit headless override also requires a dedicated instance: the shared browser's headed/headless mode is
    # fixed at launch, and reusing it under host egress would silently void headless=False, ending with
    # only an unclickable screenshot for the manual captcha.
    dedicated_browser = bool(proxy) or headless is not None
    if dedicated_browser:
        ctx_opts["proxy"] = proxy
        if not proxy:
            ctx_opts.pop("proxy", None)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright is not installed")
        pw = await async_playwright().start()
        use_headless = (os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0"
                        if headless is None else headless)
        launch_opts = get_launch_options(headless=use_headless, profile=profile)
        if proxy:
            launch_opts["proxy"] = proxy
        browser = await pw.chromium.launch(**launch_opts)
        if proxy:
            _log_proxy(engine_name, proxy)
    else:
        pw = None
        browser = await get_browser()

    context = await browser.new_context(**ctx_opts)
    # The dedicated instance's handle is hung on the context; after the caller does close(context) the browser must be closed too,
    # otherwise each query leaks a chromium process. close_stealth_context() encapsulates this.
    context._proxy_browser = browser if dedicated_browser else None  # noqa: SLF001
    context._proxy_pw = pw                               # noqa: SLF001

    # Apply stealth at context level BEFORE creating the page so init scripts
    # run on the very first navigation. Always apply our profile-bound stealth
    # JS — playwright-stealth's randomized injection would re-pick a different
    # platform and reintroduce the very mismatch we're guarding against.
    await apply_stealth_to_context(
        context, international=international, profile=profile
    )

    page = await context.new_page()
    return page, context


def _log_proxy(engine_name: str, proxy: dict) -> None:
    """Log one egress line (only the session suffix is shown, never the full credentials)."""
    import sys
    tag = (proxy.get("username") or "").rsplit("-session-", 1)[-1] or proxy.get("server", "?")
    sys.__stdout__.write(f"[proxy] {engine_name} via {tag}\n")
    sys.__stdout__.flush()


async def close_stealth_context(context) -> None:
    """Close a context created by create_stealth_page; if it carries a dedicated browser instance (proxied account),
    close the browser + playwright too, otherwise each query leaks a chromium process."""
    if context is None:
        return
    br = getattr(context, "_proxy_browser", None)
    pw = getattr(context, "_proxy_pw", None)
    try:
        await context.close()
    except Exception:  # noqa: BLE001
        pass
    if br is not None:
        try:
            await br.close()
        except Exception:  # noqa: BLE001
            pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:  # noqa: BLE001
            pass


def _sticky_id_from_state(state: dict | None) -> str | None:
    """Compute a stable account identifier from storage_state (for proxy sticky sessions).
    Hash the longest value in the cookie set (usually the login token) - stable across reloads,
    independent of check-out timing."""
    if not state:
        return None
    cookies = state.get("cookies") or []
    if not cookies:
        return None
    longest = max((c.get("value", "") or "") for c in cookies)
    if not longest:
        return None
    import hashlib
    return hashlib.md5(longest.encode("utf-8")).hexdigest()[:10]


def parse_proxy_url(proxy_url: str | None) -> dict | None:
    """http://user:pass@host:port -> Playwright's {server, username?, password?}.

    Returns None on parse failure (the caller decides whether to fall back or error). This logic used to be inline in _engine_proxy
    handling proxy_override; publish channels need the same parsing, so it was extracted for sharing.
    """
    s = (proxy_url or "").strip()
    if not s:
        return None
    from urllib.parse import urlparse
    try:
        u = urlparse(s)
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


def _engine_proxy(engine_name: str, state: dict | None = None) -> dict | None:
    """Per-account sticky egress proxy. Only enabled for engines listed in PROXY_ENGINES (e.g. deepseek).

    sticky: append `-session-acct<id>` to the proxy username so the same account always uses the same egress IP, avoiding
    "off-site login" risk control. The id prefers the just-checked-out session id, otherwise a stable hash computed from storage_state.

    ENV: PROXY_ENGINES (comma-separated) / ENGINE_PROXY_SERVER / ENGINE_PROXY_USER / ENGINE_PROXY_PASS
    """
    # 5) egress takes precedence over proxy_override (changed 2026-08-24).
    # Previously proxy_override had top priority and returned directly, so the admin setting an account to
    # `egress='host'` (use the local IP) **had no effect at all** - as long as it still had a proxy_id bound, check-out
    # delivered the proxy string and overrode it. That day all 8 wenxin accounts were egress=host and still all went through the proxy;
    # while the proxy was down for non-payment, every check-out timed out after 60-120s, and the failures were charged to the accounts.
    # Now: if host is explicitly set, use the local IP, period.
    egress_pref = None
    try:
        from .session_store import get_last_egress
        egress_pref = get_last_egress(engine_name)
    except Exception:  # noqa: BLE001
        egress_pref = None
    if egress_pref == "host":
        return None

    # Next: the full per-account bound proxy string (proxy_override, resolved by check-out from proxy_id and delivered),
    # implementing "this IP bound to this account", 1 account per IP.
    try:
        from .session_store import get_last_proxy_override
        override = (get_last_proxy_override(engine_name) or "").strip()
    except Exception:  # noqa: BLE001
        override = ""
    if override:
        parsed = parse_proxy_url(override)
        if parsed:
            return parsed
        # Parse failure -> fall back to egress/global logic

    # Per-account egress marker takes precedence: 'host' forces the local IP (no proxy), 'proxy' forces the proxy;
    # None falls back to the engine-level default (PROXY_ENGINES allowlist). This lets some accounts of one engine use proxy IPs
    # and others the local IP, achieving the precise topology of "different platforms per IP + 1 account/IP".
    egress = None
    try:
        from .session_store import get_last_egress
        egress = get_last_egress(engine_name)
    except Exception:  # noqa: BLE001
        egress = None
    if egress == "host":
        return None
    if egress != "proxy":
        engines = [e.strip() for e in os.environ.get("PROXY_ENGINES", "").split(",") if e.strip()]
        if engine_name not in engines:
            return None
    server = os.environ.get("ENGINE_PROXY_SERVER", "").strip()
    user = os.environ.get("ENGINE_PROXY_USER", "").strip()
    pw = os.environ.get("ENGINE_PROXY_PASS", "").strip()
    if not (server and user and pw):
        return None
    sid = None
    try:
        from .session_store import get_last_checkout_id
        sid = get_last_checkout_id(engine_name)
    except Exception:  # noqa: BLE001
        sid = None
    if sid is None:
        sid = _sticky_id_from_state(state)
    username = f"{user}-session-acct{sid}" if sid else user
    return {"server": server, "username": username, "password": pw}


async def create_headed_page(
    engine_name: str,
    *,
    locale: str | None = None,
    timezone_id: str | None = None,
    profile: dict | None = None,
    record_video: bool = False,
    channel: str | None = None,
):
    """Create a headed (visible) browser page for manual CAPTCHA handling.

    Launches a separate browser instance (not the shared headless one) with
    a visible window. When the adapter detects CAPTCHA, it can call this to
    let the user manually complete verification in the browser window.

    Pass `profile` to keep the device fingerprint identical to the headless
    context that just hit the CAPTCHA — switching profiles mid-flow looks
    like "different device", which escalates ByteDance's challenge.

    When `record_video` is True, Playwright records the session to
    data/snapshots/{engine}/*.webm (file is finalized only after the caller
    closes the context).

    Backend (since 2026-05-13): defaults to **CloakBrowser** (github.com/CloakHQ/CloakBrowser),
    49+ source-level C++ patches, more thorough than patchright's runtime CDP patch.
    Set `DOUBAO_BACKEND=patchright` to switch back to patchright as a fallback.

    Neither backend may additionally apply `apply_stealth_to_context` (our ~13KB stealth
    JS) - that makes the Chrome network service blow up with ERR_NAME_NOT_RESOLVED (confirmed by bisect on
    2026-05-12). Both stealth backends already spoof navigator/webgl/
    permissions etc. at the CDP/compile layer; our handwritten one only interferes.
    """
    backend = os.environ.get("DOUBAO_BACKEND", "cloakbrowser").lower()
    international = locale is not None and locale != "zh-CN"

    if profile is None:
        profile = _pick_profile()

    if locale and timezone_id:
        ctx_opts = get_context_options(
            locale=locale, timezone_id=timezone_id, profile=profile
        )
    elif international:
        ctx_opts = get_context_options_international(profile=profile)
    else:
        ctx_opts = get_context_options(profile=profile)

    state = load_storage_state(engine_name)
    if state:
        ctx_opts["storage_state"] = state

    # Per-account sticky egress proxy (only PROXY_ENGINES, e.g. deepseek). Must be obtained after load_storage_state,
    # since the sticky session id comes from the just-checked-out session.
    proxy = _engine_proxy(engine_name, state)
    if proxy:
        ctx_opts["proxy"] = proxy
        import sys
        sys.__stdout__.write(f"[proxy] {engine_name} via {proxy['username'].rsplit('-session-',1)[-1]}\n")
        sys.__stdout__.flush()

    if record_video:
        from .video_store import get_snapshot_dir
        ctx_opts["record_video_dir"] = str(get_snapshot_dir(engine_name))
        ctx_opts["record_video_size"] = {"width": 1920, "height": 1080}

    pw = None
    browser = None

    if backend == "cloakbrowser":
        try:
            from cloakbrowser import launch_context_async
        except ImportError:
            raise RuntimeError("cloakbrowser is not installed (pip install cloakbrowser)")
        # CloakBrowser ships its own stealth chromium binary; there is no channel concept.
        # launch_context_async returns the context directly (no separate browser handle),
        # so closing only needs close context; pw_ref/headed_browser are set to None so the close compatibility path passes through.
        context = await launch_context_async(headless=False, **ctx_opts)
    else:
        # patchright fallback path
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("patchright is not installed (pip install patchright)")
        pw = await async_playwright().start()
        last_err = None
        for ch in (channel, None) if channel else (None,):
            try:
                launch_opts = get_launch_options(headless=False, profile=profile, channel=ch)
                browser = await pw.chromium.launch(**launch_opts)
                break
            except Exception as e:
                last_err = e
                continue
        if browser is None:
            await pw.stop()
            raise RuntimeError(f"create_headed_page: launch failed (channel={channel}): {last_err}")
        context = await browser.new_context(**ctx_opts)

    page = await context.new_page()

    # Attach pw + browser references so caller can close them later (both None
    # for cloakbrowser since it manages lifecycle internally).
    page._pw_ref = pw
    page._headed_browser = browser
    return page, context


async def create_persistent_chrome_context(
    engine_name: str,
    *,
    profile: dict,
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
):
    """Create a persistent Chrome context for Cloudflare-gated engines.

    Uses launch_persistent_context + channel="chrome" so that:
    1. Real Chrome binary → Sec-CH-UA brand includes "Google Chrome"
    2. Persistent user_data_dir → CF sees a "mature profile" with cache/history
    3. Saved profile dict → fingerprint matches the login-time identity

    Each engine gets its own user_data_dir so cookies/cache/history persist
    across runs. storage_state is injected via add_cookies() + localStorage
    eval (launch_persistent_context doesn't accept storage_state directly).
    """
    from playwright.async_api import async_playwright
    from pathlib import Path

    pw = await async_playwright().start()

    use_headless = os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0"

    # Try real Chrome first (Sec-CH-UA brand "Google Chrome"); on hosts
    # without it (notably Linux ARM64 — Google publishes no Chrome ARM64
    # binary), fall back to bundled chromium. Our extra_http_headers in
    # get_context_options already forces the Sec-CH-UA brand list to
    # include "Google Chrome" so HTTP-layer brand check still passes.
    #
    # NOTE: we deliberately use launch + new_context, NOT
    # launch_persistent_context. Empirically the persistent-context
    # variant trips a Cloudflare deep challenge ("Just a moment...")
    # even on a clean user_data_dir with no cookies — likely because
    # Chromium writes default Preferences / Local State that look like
    # a "half-aged" profile to CF's heuristics. Plain new_context with
    # storage_state replay is what verify_chatgpt_fingerprint already
    # proved passes CF on this server's IP.
    browser = None
    last_err = None
    for ch in ("chrome", None):
        try:
            launch_opts = get_launch_options(
                headless=use_headless, profile=profile, channel=ch,
            )
            browser = await pw.chromium.launch(**launch_opts)
            break
        except Exception as e:
            last_err = e
            continue
    if browser is None:
        await pw.stop()
        raise RuntimeError(
            f"Could not launch Chrome or bundled Chromium: {last_err}"
        )

    ctx_opts = get_context_options(
        locale=locale, timezone_id=timezone_id, profile=profile,
    )
    context = await browser.new_context(**ctx_opts)

    # Order matters here. Cloudflare flags any browser whose first request
    # already carries a session-token cookie ("cookie replay" pattern) and
    # serves a deep "Just a moment..." challenge that we cannot pass without
    # interactive Turnstile. To avoid this:
    #
    #   1. Apply stealth init script first.
    #   2. Open a fresh page and goto each origin from state — this lets CF
    #      see a clean browser, issue cf_clearance + infra cookies normally.
    #   3. Only THEN inject the saved session-token + other auth cookies.
    #   4. Reload so the server-side sees the now-authenticated request.
    #
    # localStorage is injected during step 2 (we are already on the origin).
    state = load_storage_state(engine_name)

    await apply_stealth_to_context(context, international=True, profile=profile)
    page = await context.new_page()

    if state and state.get("origins"):
        for origin in state["origins"]:
            origin_url = origin.get("origin")
            if not origin_url:
                continue
            try:
                # Clean first visit — no auth cookies yet — so CF treats us
                # as a normal first-time visitor. CF serves a transparent
                # challenge page; the embedded JS auto-solves and redirects
                # to the real origin in a few seconds. wait_until="load" +
                # explicit URL polling ensures we don't stop on the middle
                # challenge page.
                await page.goto(origin_url, wait_until="load", timeout=45000)
                for _ in range(60):
                    cur_url = page.url or ""
                    try:
                        cur_title = await page.title() or ""
                    except Exception:
                        cur_title = ""
                    if (
                        "__cf_chl_rt_tk" not in cur_url
                        and "/cdn-cgi/challenge-platform" not in cur_url
                        and "Just a moment" not in cur_title
                        and "Verifying" not in cur_title
                    ):
                        break
                    await asyncio.sleep(0.5)
                for item in origin.get("localStorage", []):
                    await page.evaluate(
                        "([k, v]) => localStorage.setItem(k, v)",
                        [item["name"], item["value"]],
                    )
            except Exception:
                pass

    if state and state.get("cookies"):
        # Strip Cloudflare's own session cookies before injecting. They are
        # machine-specific (CF binds them to TLS fingerprint + IP); replaying
        # CF cookies from another host triggers a deep "Just a moment..."
        # challenge on the new host. Let CF re-issue these naturally — our
        # stealth + Linux profile already proved able to clear CF on a clean
        # browser via verify_chatgpt_fingerprint.
        _CF_OWNED = {"__cf_bm", "__cflb", "_cfuvid", "cf_clearance"}
        safe_cookies = [
            c for c in state["cookies"]
            if c.get("name") not in _CF_OWNED
        ]
        if safe_cookies:
            await context.add_cookies(safe_cookies)
            try:
                await page.reload(wait_until="load", timeout=45000)
                # Same CF challenge wait as the first goto.
                for _ in range(60):
                    cur_url = page.url or ""
                    try:
                        cur_title = await page.title() or ""
                    except Exception:
                        cur_title = ""
                    if (
                        "__cf_chl_rt_tk" not in cur_url
                        and "/cdn-cgi/challenge-platform" not in cur_url
                        and "Just a moment" not in cur_title
                        and "Verifying" not in cur_title
                    ):
                        break
                    await asyncio.sleep(0.5)
            except Exception:
                pass

    # Caller must close in reverse order: ctx.close() → browser.close() → pw.stop()
    page._pw_ref = pw
    page._headed_browser = browser
    return page, context


async def save_page_session(engine_name: str, context) -> None:
    """Save full storage_state (cookies + localStorage) for next run."""
    state = await context.storage_state()
    save_storage_state(engine_name, state)


_HUMAN_DELAY_SCALE = float(os.environ.get("BROWSER_DELAY_SCALE", "1.0"))


async def human_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Random delay to simulate human timing.

    The global scale factor BROWSER_DELAY_SCALE (env) allows speeding everything up without touching 100+ call sites.
    Default 1.0 (original values); production recommends 0.25-0.4 (daily batch run speed
    improves 1.5-2x; anti-detection signals are slightly reduced but still nonzero). If captchas increase, just
    turn it back. Setting 0 skips entirely (for tests, not production).
    """
    if _HUMAN_DELAY_SCALE <= 0:
        return
    await asyncio.sleep(random.uniform(min_s * _HUMAN_DELAY_SCALE, max_s * _HUMAN_DELAY_SCALE))


async def human_type(page, selector: str, text: str, delay_ms: int = 0) -> None:
    """Type text with random per-keystroke delay to simulate human typing."""
    el = page.locator(selector)
    await el.click()
    for ch in text:
        await el.type(ch, delay=random.randint(50, 200))
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)


async def human_move_mouse(page, x: int, y: int, steps: int = 10) -> None:
    """Move mouse to (x,y) with intermediate steps simulating human movement."""
    await page.mouse.move(x, y, steps=steps)


async def human_scroll(page, direction: str = "down", amount: int = 3) -> None:
    """Simulate human scrolling with random delta and pauses."""
    delta = 300 if direction == "down" else -300
    for _ in range(amount):
        await page.mouse.wheel(0, delta + random.randint(-50, 50))
        await asyncio.sleep(random.uniform(0.2, 0.5))


async def human_click(page, x: int, y: int) -> None:
    """Simulate a human click: move mouse, small pause, then click."""
    await human_move_mouse(page, x, y, steps=random.randint(5, 15))
    await asyncio.sleep(random.uniform(0.05, 0.15))
    await page.mouse.click(x, y)


async def simulate_browsing(page, duration: float = 3.0) -> None:
    """Simulate natural browsing behavior: random mouse moves, scrolls, pauses.

    Call this after page load to establish a human-like interaction history
    before submitting any queries.
    """
    import time
    start = time.monotonic()

    viewport = page.viewport_size or {"width": 1920, "height": 1080}
    w, h = viewport["width"], viewport["height"]

    # Random mouse movements across the page
    moves = random.randint(3, 6)
    for _ in range(moves):
        if time.monotonic() - start > duration:
            break
        x = random.randint(100, w - 100)
        y = random.randint(100, h - 200)
        await human_move_mouse(page, x, y, steps=random.randint(8, 20))
        await asyncio.sleep(random.uniform(0.3, 0.8))

    # Natural scrolling — scan the page like a real user
    if time.monotonic() - start < duration:
        scroll_times = random.randint(1, 3)
        for _ in range(scroll_times):
            if time.monotonic() - start > duration:
                break
            await human_scroll(page, "down", amount=random.randint(1, 3))
            await asyncio.sleep(random.uniform(0.3, 0.6))

    # Occasional scroll back up
    if random.random() < 0.3 and time.monotonic() - start < duration:
        await human_scroll(page, "up", amount=random.randint(1, 2))

    # Final pause
    remaining = duration - (time.monotonic() - start)
    if remaining > 0:
        await asyncio.sleep(min(remaining, 1.0))


async def close_browser() -> None:
    """Shut down the shared browser instance."""
    global _browser
    if _browser and _browser.is_connected():
        await _browser.close()
    _browser = None
