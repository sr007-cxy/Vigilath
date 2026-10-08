"""Doubao browser adapter — automates www.doubao.com.

Doubao is ByteDance's AI chat product. The 2026-04 UI exposes skills as
buttons in the chat action bar: 快速 (Fast), 超能模式 (Super mode), PPT 生成 (PPT generation), 图像生成 (Image generation),
帮我写作 (Help me write), and 更多 (More) (which opens 深入研究 (Deep research), 编程 (Coding), etc.).

DOM notes (2026-04, Semi Design):
  - Input: <textarea class="semi-input-textarea semi-input-textarea-autosize">
    with placeholder="发消息..." ("Send a message..."). A hidden textarea (height=0) must be avoided.
  - Skill buttons: div/button elements with Tailwind classes, text-based matching.
  - "深入研究" (deep research): behind the "更多" ("More") menu, provides web-cited answers.
  - Response: streaming markdown in the last assistant message bubble.
    Doubao renders references as superscript links [1][2] with <a href>.
  - Citations also appear in a "参考来源" ("Reference sources") section or collapsible source cards.

Network capture:
  The chat completion API streams SSE responses. Each chunk may contain
  structured citation data (url, title). We intercept all /api/ responses
  to extract these before falling back to DOM scraping.

Requires: a valid Doubao session (run scripts/doubao_login.py first).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from typing import List, Optional

from ..browser import (
    create_stealth_page,
    create_headed_page,
    human_delay,
    human_move_mouse,
    human_click,
    human_scroll,
    simulate_browsing,
    save_page_session,
)
from ..anti_detect import _pick_profile
from ..video_store import want_record


async def _close_headed(ctx, browser=None, pw=None) -> None:
    """Close headed browser context, browser, and playwright instance."""
    try:
        await ctx.close()
    except Exception:
        pass
    if browser:
        try:
            await browser.close()
        except Exception:
            pass
    if pw:
        try:
            await pw.stop()
        except Exception:
            pass
from .base import Citation, EngineAdapter, EngineResult, extract_urls_from_text
from ..reasoning import extract_reasoning
from ..session_store import report_session_outcome


def _is_xvfb_display() -> bool:
    """Check if current DISPLAY was set by our Xvfb module."""
    import os
    display = os.environ.get("DISPLAY", "")
    return display.startswith(":") and display.lstrip(":").isdigit()


# On CloakBrowser/some proxy paths, the title field in Doubao's SSE JSON encodes UTF-8 bytes as CP1252
# codepoints (\uXXXX), so json.loads yields mojibake like 'ä¸–çºªäº\x81'.
# Decode back with a hand-written CP1252 table + latin-1 fallback. Same implementation as yuanbao_browser.py,
# just localized to Doubao (both engines carry their own; can be moved to base.py later).
_CP1252_HIGH = {
    0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85,
    0x2020: 0x86, 0x2021: 0x87, 0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A,
    0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91, 0x2019: 0x92,
    0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97,
    0x02DC: 0x98, 0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B, 0x0153: 0x9C,
    0x017E: 0x9E, 0x0178: 0x9F,
}


def _fix_mojibake(s: str) -> str:
    """Restore 'UTF-8 bytes decoded as CP1252/Latin-1' mojibake ('世' -> 'ä¸–').

    Only changes strings that contain typical mojibake lead characters and round-trip cleanly; otherwise returns them unchanged to avoid damaging clean strings."""
    if not s or not any(c in s for c in "äÃâåæçèéêëìíîï"):
        return s
    try:
        bs = bytearray()
        for ch in s:
            cp = ord(ch)
            if cp < 0x100:
                bs.append(cp)
            elif cp in _CP1252_HIGH:
                bs.append(_CP1252_HIGH[cp])
            else:
                return s
        return bs.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return s


# Hosts to exclude from citation results - only exclude noise domains like "infrastructure / CDN / tracking".
#
# ⚠️ Never add **content platforms** here. ByteDance's own content sites (Douyin iesdouyin/douyin,
# Toutiao toutiao, Xigua ixigua, TikTok ...) are all real sources: customers want to know whether they
# are cited by content on these platforms. Fixed 2026-08: previously `iesdouyin.com` / `tiktok.com` were
# wrongly added as noise, so **all Douyin videos cited in Doubao answers were swallowed** (measured over 10 jobs, Douyin
# appeared 0 times, while Toutiao, also ByteDance, was captured normally) - a fatal source gap for GEO reports.
#
# Note: matching is a **substring match on the whole URL** (`any(b in url ...)`), so entries must be specific enough
# to avoid collateral damage (e.g. writing "douyin.com" would also kill www.douyin.com content pages).
_DOUBAO_BLOCK_HOSTS = (
    # Doubao itself / corporate sites / cloud services
    "doubao.com",
    "bytedance.com",
    "volcengine.com",
    "lf-flow-web-cdn.doubao.com",
    "feiliao.com",
    # CDN / static assets / image hosting / tracking and monitoring
    "ibytedapm.com",
    "bdurl.net",
    "byteimg.com",
    "bytednsdoc.com",
    "bytegoofa.com",
    "bytegecko.com",
    "byteoversea.com",
    "pstatp.com",
    "bytecss.cn",
    "bytetos.com",
)


# Marker words on the page when kicked back to the logged-out home page. Same metric definition as HOMEPAGE_MARKERS in V-networks/workers/desktop-cn/worker.py
# and telemetry dispatch._HOMEPAGE_MARKERS - those two check the "answer text",
# while this checks the "whole page body"; when the answer is empty, only this path can still detect a logged-out state.
_PAGE_HOMEPAGE_MARKERS = ("登录", "有什么我能帮你的吗", "为你推荐", "下载豆包电脑版")


class DoubaoBrowserAdapter(EngineAdapter):
    name = "豆包"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    CHAT_URL = "https://www.doubao.com/chat/"

    async def is_available(self) -> bool:
        try:
            page, ctx = await create_stealth_page("doubao")
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=15000)
            await human_delay(2, 3)
            logged_in = await page.locator(
                "textarea.semi-input-textarea, textarea[placeholder='发消息...'], "
                "textarea[placeholder]"
            ).count() > 0
            await ctx.close()
            return logged_in
        except Exception:
            return False

    # D4c (2026-05-18): hot browser protocol - EngineSession reuses the cloakbrowser ctx via _create_hot_page +
    # _prepare_page + _query_with_page + _close_hot_page.
    # search() is **untouched**, keeping one-shot with zero regression.
    async def _create_hot_page(self):
        """Doubao only: Xvfb + headed + cloakbrowser/patchright + channel=chrome.

        Parameters all match the search() section - profile pinned to Linux x86_64, channel=chrome.
        """
        import os
        # Start Xvfb (if DISPLAY is not set)
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        page, ctx = await create_headed_page(
            "doubao",
            profile=profile,
            record_video=want_record(self._record_video),
            channel="chrome",
        )
        return page, ctx

    async def _close_hot_page(self, page, ctx) -> None:
        """Doubao only: cloakbrowser's headed_browser + pw_ref must also be closed."""
        headed_browser = getattr(page, "_headed_browser", None)
        headed_pw = getattr(page, "_pw_ref", None)
        await _close_headed(ctx, headed_browser, headed_pw)


    #
    # Note Doubao's peculiarities (deploy doc section 9):
    #   - uses create_headed_page (cloakbrowser / patchright + Xvfb)
    #   - simulate_browsing warm-up is required before every query (ByteDance watches timing)
    #   - per-char keyboard.type (60-220ms jitter), no fill / insert_text
    #   - send via the send button first, Enter as fallback
    #   - the CAPTCHA path must dump a screenshot + report FailureType=captcha so the pool quarantines it
    #   - simulate_browsing cannot be done in _prepare_page (it is per-query behavior)
    async def _prepare_page(self, page, ctx) -> None:
        """Hot init: goto + network capture listener + popups + captcha precheck.

        Does not do _start_new_chat / simulate_browsing - those two are per-query.
        """
        # Network capture - listener attached to the page, list cleared before each query
        self._captured_bodies = []

        def _on_response(response):
            url_l = response.url.lower()
            if not any(kw in url_l for kw in ("/api/", "chat", "completion", "search")):
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if ctype and not any(
                t in ctype for t in ("json", "event-stream", "text/plain", "text/html")
            ):
                return
            try:
                asyncio.create_task(self._capture_body(response, self._captured_bodies))
            except Exception:
                pass

        page.on("response", _on_response)

        # ── For researching the pure HTTP path (option c): with DOUBAO_CAPTURE_REQUESTS=1, dump outgoing
        # completion requests in full (method/url/headers/post_data) for offline reproduction of
        # the request signature. Off by default, affects no behavior; log-only, printed to stdout (service log).
        import os as _os
        if _os.environ.get("DOUBAO_CAPTURE_REQUESTS", "").strip() in ("1", "true", "True"):
            def _on_request(request):
                try:
                    u = request.url.lower()
                    if request.method != "POST":
                        return
                    if not any(kw in u for kw in ("completion", "chat", "samantha")):
                        return
                    dump = {
                        "method": request.method,
                        "url": request.url,
                        "headers": dict(request.headers),
                        "post_data": request.post_data,
                    }
                    print("[doubao-capture] " + json.dumps(dump, ensure_ascii=False), flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[doubao-capture][err] {e!r}", flush=True)

            page.on("request", _on_request)

        await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
        await human_delay(3, 5)
        # Initial captcha precheck (very rare, but if it appears this session should be abandoned immediately)
        if await self._check_captcha(page):
            raise RuntimeError(
                "captcha on initial load — session needs rotate (login_lost/captcha state)"
            )
        await self._assert_logged_in(page)
        await self._dismiss_popups(page)

    async def _probe_login_lost(self, page) -> Optional[str]:
        """Probe the login session once: returns None if fine, or a readable reason if invalid (wording contains "login lost").

        Same criteria as _assert_logged_in, split out so the "empty extraction" path can reuse it -
        that path cannot raise (it must finish session saving/cleanup), so it can only put the reason into EngineResult.error.

        The "login lost" in the wording is recognized by engine_session._classify_error; do not change it:
        only when recognized is the account quarantined and switched immediately; falling into empty_answer only acts after reaching a threshold,
        and meanwhile dead accounts get dispatched repeatedly (measured the night of 2026-08-29: three accounts idled in turn all night).
        """
        info = None
        try:
            info = await page.evaluate("""() => {
                try {
                    const d = window._ROUTER_DATA || {};
                    const l = d.loaderData || {};
                    const c = (l.chat_layout || {}).chat_layout || {};
                    const a = c.accountInfo;
                    if (!a) return null;
                    const data = a.data || {};
                    return {code: data.error_code ?? a.error_code ?? null,
                            uid: data.user_id ?? null,
                            msg: data.description || a.message || ""};
                } catch (e) { return null; }
            }""")
        except Exception:  # noqa: BLE001
            info = None

        if info is not None:
            if info.get("code") or info.get("uid") == 0:
                return ("doubao login lost — 未登录/会话过期"
                        f"(error_code={info.get('code')} user_id={info.get('uid')} "
                        f"{info.get('msg')})")
            return None

        # accountInfo unavailable (page structure changed) -> fall back to text criteria, accepting only explicit wording
        try:
            html = await page.content()
        except Exception:  # noqa: BLE001
            return None
        if "会话过期" in html or "from_logout" in (page.url or ""):
            return "doubao login lost — 页面出现会话过期/已被登出"
        # Fallback: very short page body plus a home page marker hit = kicked back to the logged-out home page.
        # Measured 2026-08-29: 156KB of HTML had only 180 chars after stripping tags, the content being "登录 / 有什么我能
        # 帮你的吗 / 为你推荐" ("Log in / How can I help you / Recommended for you"), and accountInfo was no longer readable, so neither of the first two criteria hit.
        try:
            text = await page.evaluate(
                "() => (document.body && document.body.innerText || '').trim()")
        except Exception:  # noqa: BLE001
            return None
        t = (text or "")[:600]
        if t and len(t) < 600 and any(m in t for m in _PAGE_HOMEPAGE_MARKERS):
            return "doubao login lost — 页面回退未登录首页(正文极短且命中首页特征词)"
        return None

    async def _assert_logged_in(self, page) -> None:
        """Raise immediately if not logged in; do not ask questions with a dead session.

        2026-08-24: all 5 Doubao accounts in the pool were dead, yet the page **looked** logged in (the sidebar had the username and
        conversation history, rendered from the localStorage cache in storage_state), while the server had long since judged
        `session expired / user_id:0`. After asking, it was kicked to `?from_logout=1`, with an empty answer and error=None,
        and the upper layer recorded it as success - not a single account switch, running 122 seconds and returning empty-handed.
        Reproduced identically from two different CN IPs, so unrelated to the egress IP; the accounts were simply dead.

        The criterion is the accountInfo issued by the server (not how the page looks); if unavailable, fall back to text.
        """
        reason = await self._probe_login_lost(page)
        if reason:
            raise RuntimeError(reason)

    async def _query_with_page(self, page, ctx, query: str) -> EngineResult:
        """Hot query:reset chat → warm-up → input → submit → wait → extract.

        captcha mid-query -> returns error, and EngineSession calls rotate based on it.
        """
        # Clear the network bodies accumulated in the previous round
        self._captured_bodies = []

        sys.__stdout__.write("[Doubao-hot] start new chat\n"); sys.__stdout__.flush()
        await self._start_new_chat(page)

        sys.__stdout__.write("[Doubao-hot] simulate warm-up\n"); sys.__stdout__.flush()
        try:
            await simulate_browsing(page, duration=random.uniform(3.0, 6.0))
        except Exception as e:
            sys.__stdout__.write(f"[Doubao-hot] simulate warning: {e}\n"); sys.__stdout__.flush()

        input_el = await self._find_input(page)
        if input_el is None:
            return EngineResult(engine=self.name, query=query, error="input not found")

        try:
            box = await input_el.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await human_click(page, cx, cy)
            else:
                await input_el.click()
        except Exception:
            await input_el.click()
        await human_delay(1.2, 2.8)

        typed_query = f"{query},请展示引用来源"
        for ch in typed_query:
            await page.keyboard.type(ch, delay=random.randint(60, 220))
        await human_delay(0.8, 2.2)

        # Submit
        submitted = False
        try:
            send_btn = page.locator(
                "[data-testid='send-button'], "
                "button[aria-label='发送'], "
                "button[aria-label='Send'], "
                "button:has(svg) >> nth=-1"
            ).first
            if await send_btn.is_visible(timeout=1500):
                await send_btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            await page.keyboard.press("Enter")

        # Post-submit CAPTCHA (Doubao's key line of defense)
        await human_delay(3, 5)
        if await self._check_captcha(page):
            return EngineResult(
                engine=self.name, query=query,
                error="CAPTCHA: ByteDance challenged the request after submit",
            )

        await self._wait_for_stable_answer(page, max_wait=90, stable_secs=3.0)
        await human_delay(1.0, 2.0)

        answer = await self._extract_answer(page)
        citations = await self._extract_citations(page, answer)
        # Empty extraction -> probe the login session before concluding. The most common cause of an empty answer is being kicked back to the logged-out home page,
        # not "Doubao had nothing to say": the home page also has the input box/send button, so the flow can get all the way to
        # submitted with nobody answering. Without this probe we can only report empty_answer - which only quarantines after reaching
        # a threshold, while dead accounts are dispatched repeatedly (night of 2026-08-29: three accounts idled in turn all night).
        if not (answer or "").strip():
            reason = await self._probe_login_lost(page)
            if reason:
                return EngineResult(engine=self.name, query=query, error=reason)
        reasoning = await extract_reasoning(page, "doubao")
        return EngineResult(engine=self.name, query=query, answer=answer, citations=citations,
                            reasoning=reasoning)

    async def search(self, query: str) -> EngineResult:
        import os
        # Pin a single device profile for the whole query lifecycle.
        # Force "Linux x86_64" so what we CLAIM matches what browser host actually
        # IS — Linux + Mesa + freetype fonts. If we keep claiming "Mac M1"
        # but render on Linux/Xvfb, ByteDance compares canvas/font output
        # to the UA-declared platform and instantly flags the mismatch.
        # Linux Chrome users are uncommon but real; not a bot signal by
        # itself, while UA-vs-renderer mismatch IS one.
        profile = _pick_profile(platform_filter="Linux x86_64")

        # Doubao runs ByteDance's anti-bot stack which fingerprints headless
        # Chromium reliably (window.chrome shape, Permissions API behavior,
        # WebGL precision drift, etc.) regardless of stealth scripts. The only
        # reliable bypass is launching real headed Chromium against an Xvfb
        # virtual display. Auto-start Xvfb if no DISPLAY is set.
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            if start_xvfb():
                sys.__stdout__.write(
                    "[Doubao] Xvfb started for headed Chromium "
                    f"(DISPLAY={os.environ.get('DISPLAY')})\n"
                )
                sys.__stdout__.flush()

        use_headed = (
            bool(os.environ.get("DISPLAY"))
            and os.environ.get("DOUBAO_HEADED") != "0"
        )

        try:
            if use_headed:
                # channel="chrome" forces the real google-chrome binary —
                # only the real binary has the matching TLS JA3 + native
                # Sec-CH-UA full-version-list that ByteDance compares
                # against. Bundled Chromium has subtly different TLS
                # extension order, ALPN, and brand string that flag bot.
                page, ctx = await create_headed_page(
                    "doubao",
                    profile=profile,
                    record_video=want_record(self._record_video),
                    channel="chrome",
                )
                headed_browser = getattr(page, "_headed_browser", None)
                headed_pw = getattr(page, "_pw_ref", None)
            else:
                page, ctx = await create_stealth_page(
                    "doubao", record_video=want_record(self._record_video), profile=profile
                )
                headed_browser = None
                headed_pw = None

            # Check for captcha before proceeding
            await self._check_captcha(page)

            # Network capture: intercept chat API responses for citation URLs
            captured_bodies: List[str] = []
            self._captured_bodies = captured_bodies

            def _on_response(response):
                url_l = response.url.lower()
                if not any(kw in url_l for kw in ("/api/", "chat", "completion", "search")):
                    return
                ctype = (response.headers.get("content-type") or "").lower()
                if ctype and not any(
                    t in ctype
                    for t in ("json", "event-stream", "text/plain", "text/html")
                ):
                    return
                try:
                    asyncio.create_task(self._capture_body(response, captured_bodies))
                except Exception:
                    pass

            page.on("response", _on_response)

            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
            await human_delay(3, 5)

            # Confirm WHICH chrome binary we actually launched. fallback in
            # create_headed_page silently switches to bundled chromium if
            # channel="chrome" failed — we need to know.
            try:
                import subprocess as _sp
                # Match BOTH possible binaries: real google-chrome AND bundled chromium
                pids_raw = _sp.check_output(
                    ["pgrep", "-f", "(chrome-linux64/chrome|google-chrome)"],
                    text=True,
                ).strip().split("\n")[:5]
                bins = set()
                for p in pids_raw:
                    p = p.strip()
                    if not p:
                        continue
                    try:
                        import os as _os
                        bins.add(_os.readlink(f"/proc/{p}/exe"))
                    except Exception:
                        continue
                sys.__stdout__.write(f"[Doubao-binary] {sorted(bins)}\n")
                sys.__stdout__.flush()
            except Exception as e:
                sys.__stdout__.write(f"[Doubao-binary] probe failed: {e}\n")
                sys.__stdout__.flush()

            # Dump our own chromium process command-line so we can verify
            # --enable-automation is actually stripped (it's the canonical
            # "controlled browser" tell — must NOT be in args).
            try:
                import subprocess as _sp
                pids = _sp.check_output(
                    ["pgrep", "-f", "(chrome-linux64/chrome|google-chrome)"], text=True
                ).strip().split("\n")[:5]
                for pid in pids:
                    if not pid.strip():
                        continue
                    try:
                        with open(f"/proc/{pid.strip()}/cmdline", "rb") as f:
                            argv = f.read().split(b"\x00")
                        flags = sorted({
                            a.decode("utf-8", "ignore")
                            for a in argv
                            if a.startswith(b"--") and any(
                                k in a for k in (
                                    b"automation", b"webdriver", b"debugging",
                                    b"AutomationControlled", b"sandbox",
                                )
                            )
                        })
                        if flags:
                            sys.__stdout__.write(
                                f"[Doubao-procargs] pid={pid.strip()} flags={flags}\n"
                            )
                            sys.__stdout__.flush()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # Fingerprint diagnostic: log UA, platform, canvas to stdout.
            # canvas_hash is a djb2 hash of the full toDataURL (not the last 40 bytes) -
            # the base64 tail of a PNG is almost constant, so you cannot tell whether anti_detect.py's ^Math.random
            # perturbation really changed the pixels. Only hashing the whole string shows a different hash per run.
            try:
                fp = await page.evaluate("""() => {
                    const ua = navigator.userAgent;
                    const plat = navigator.platform;
                    let canvasLen = 0;
                    let canvasHash = '(no canvas)';
                    try {
                        const c = document.createElement('canvas');
                        c.width = 64; c.height = 64;
                        const ctx2 = c.getContext('2d');
                        ctx2.textBaseline = 'top';
                        ctx2.font = '14px Arial';
                        ctx2.fillText('fp_test', 2, 2);
                        const s = c.toDataURL();
                        canvasLen = s.length;
                        let h = 5381;
                        for (let i = 0; i < s.length; i++) {
                            h = ((h << 5) + h + s.charCodeAt(i)) | 0;
                        }
                        canvasHash = (h >>> 0).toString(16).padStart(8, '0');
                    } catch(e) { canvasHash = e.message; }
                    const wl = typeof navigator.webdriver !== 'undefined' ? navigator.webdriver : '(undefined)';
                    return { ua, plat, canvasHash, canvasLen, webdriver: wl };
                }""")
                sys.__stdout__.write(
                    f"[Doubao-fingerprint] UA={fp['ua']}  "
                    f"platform={fp['plat']}  "
                    f"canvas_hash={fp['canvasHash']} (len={fp['canvasLen']})  "
                    f"webdriver={fp['webdriver']}\n"
                )
                sys.__stdout__.flush()
            except Exception as e:
                sys.__stdout__.write(f"[Doubao-fingerprint] probe failed: {e}\n")
                sys.__stdout__.flush()

            # patchright already does stealth at the CDP layer, so there is no need to stack our mouse behavior
            # simulation (simulate_browsing/human_click) - those may actually hang the
            # mouse.move(steps=N) call under Xvfb. Input uses fill() in one go; patchright
            # uses isTrusted input events, which look to ByteDance as natural as a human paste.
            sys.__stdout__.write("[Doubao-step] dismiss popups\n"); sys.__stdout__.flush()
            await self._dismiss_popups(page)

            # Click "新对话" ("New chat") to start a fresh conversation. /chat/ auto-resumes
            # the last session, causing consecutive queries to pile up in the same conversation,
            # so _get_last_assistant_text always grabs the previous answer / sidebar title.
            sys.__stdout__.write("[Doubao-step] start new chat\n"); sys.__stdout__.flush()
            await self._start_new_chat(page)

            # Warm-up: ByteDance escalates from slider to image CAPTCHA when a
            # fresh context jumps straight from goto → focus input → submit.
            # 3-6 seconds of random mouse movement + small scrolls make the timing chain look like a person "glancing around the page".
            # An earlier DEV version removed this (reason: "patchright already does stealth"); in practice
            # even fresh sessions still got CAPTCHAs - patchright fixes CDP signals but not
            # behavioral timing signals.
            sys.__stdout__.write("[Doubao-step] simulate browsing warm-up\n"); sys.__stdout__.flush()
            try:
                await simulate_browsing(page, duration=random.uniform(3.0, 6.0))
            except Exception as e:
                sys.__stdout__.write(f"[Doubao-step] simulate_browsing warning: {e}\n"); sys.__stdout__.flush()

            sys.__stdout__.write("[Doubao-step] find input\n"); sys.__stdout__.flush()
            input_el = await self._find_input(page)
            if input_el is None:
                await ctx.close()
                return EngineResult(
                    engine=self.name, query=query, error="input not found"
                )

            # Click input with human-like mouse path, then a "thinking pause"
            # before typing (real users focus → think → type, not focus → type).
            sys.__stdout__.write("[Doubao-step] click input + thinking pause\n"); sys.__stdout__.flush()
            try:
                box = await input_el.bounding_box()
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    await human_click(page, cx, cy)
                else:
                    await input_el.click()
            except Exception:
                await input_el.click()
            await human_delay(1.2, 2.8)

            typed_query = f"{query}，请展示引用来源"
            # Per-char typing with random delay. fill() / insert_text pouring in the whole text at once
            # is the strongest bot signature - this alone is enough for ByteDance to escalate from a slider CAPTCHA
            # to an image CAPTCHA (which we cannot solve). 60-220ms jitter ≈ the rhythm of a real Chinese IME
            # (one Enter or one candidate pick per character).
            sys.__stdout__.write("[Doubao-step] per-char type query\n"); sys.__stdout__.flush()
            for ch in typed_query:
                await page.keyboard.type(ch, delay=random.randint(60, 220))

            # Post-typing review pause (humans scan their own message before sending).
            await human_delay(0.8, 2.2)

            # Submit ONCE: prefer the send button (what real users click), fall
            # back to Enter only if the button is missing. Double-submitting
            # (Enter + button click) is itself a bot signal.
            sys.__stdout__.write("[Doubao-step] submit\n"); sys.__stdout__.flush()
            submitted = False
            try:
                send_btn = page.locator(
                    "[data-testid='send-button'], "
                    "button[aria-label='发送'], "
                    "button[aria-label='Send'], "
                    "button:has(svg) >> nth=-1"
                ).first
                if await send_btn.is_visible(timeout=1500):
                    await send_btn.click()
                    submitted = True
            except Exception:
                pass
            if not submitted:
                await page.keyboard.press("Enter")
            sys.__stdout__.write(f"[Doubao-step] submitted via={'btn' if submitted else 'Enter'}\n"); sys.__stdout__.flush()

            # Check for CAPTCHA after submit. We do NOT do a close-and-reopen
            # "retry" here: the previous version closed the original context and
            # opened a brand-new one at /chat/, which has no CAPTCHA on the
            # fresh page (the CAPTCHA was on the closed page). _check_captcha
            # would then return False, the system would falsely report
            # "CAPTCHA cleared!", and the resubmit would trigger the
            # image-classification CAPTCHA we cannot solve. Cleaner to fail
            # fast on a single submission than to give ByteDance a second
            # automation signal.
            await human_delay(3, 5)
            sys.__stdout__.write("[Doubao-step] check captcha\n"); sys.__stdout__.flush()
            if await self._check_captcha(page):
                screenshot_dir = "/tmp/doubao_captcha"
                os.makedirs(screenshot_dir, exist_ok=True)
                ts = int(asyncio.get_event_loop().time())

                # Dump iframe geometry + page error indicators for diagnosis.
                try:
                    diag = await page.evaluate("""() => {
                        const iframes = Array.from(document.querySelectorAll('iframe')).map(f => {
                            const r = f.getBoundingClientRect();
                            const cs = getComputedStyle(f);
                            return {
                                src: f.src,
                                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                                visible: cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0,
                                z: cs.zIndex,
                            };
                        });
                        // Collect any visible error/toast text shown on the page
                        const errorTexts = [];
                        for (const sel of ['[class*="error"]', '[class*="Error"]',
                                            '[class*="toast"]', '[class*="Toast"]',
                                            '[class*="message-error"]', '[class*="failed"]']) {
                            for (const el of document.querySelectorAll(sel)) {
                                const cs = getComputedStyle(el);
                                if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                                const t = (el.textContent || '').trim();
                                if (t && t.length < 200) errorTexts.push(sel + ': ' + t);
                            }
                        }
                        return { iframes, errorTexts: errorTexts.slice(0, 10) };
                    }""")
                    sys.__stdout__.write(f"[Doubao-diag] iframes: {json.dumps(diag.get('iframes', []), ensure_ascii=False)}\n")
                    sys.__stdout__.write(f"[Doubao-diag] error_texts: {json.dumps(diag.get('errorTexts', []), ensure_ascii=False)}\n")
                except Exception as e:
                    sys.__stdout__.write(f"[Doubao-diag] probe failed: {e}\n")

                # Dump captured network bodies (the real chat API response is here)
                try:
                    bodies_path = f"{screenshot_dir}/bodies_{ts}.txt"
                    with open(bodies_path, "w") as f:
                        for i, b in enumerate(captured_bodies):
                            f.write(f"=== body {i} ({len(b)} bytes) ===\n")
                            f.write(b[:5000])
                            f.write("\n\n")
                    sys.__stdout__.write(f"[Doubao-diag] {len(captured_bodies)} api bodies → {bodies_path}\n")
                except Exception as e:
                    sys.__stdout__.write(f"[Doubao-diag] body dump failed: {e}\n")

                try:
                    await page.screenshot(path=f"{screenshot_dir}/captcha_{ts}.png")
                    html_dump = await page.content()
                    with open(f"{screenshot_dir}/page_{ts}.html", "w") as f:
                        f.write(html_dump)
                    sys.__stdout__.write(
                        f"[Doubao] CAPTCHA detected (mode={'headed' if use_headed else 'headless'}) — "
                        f"screenshot {screenshot_dir}/captcha_{ts}.png  html {screenshot_dir}/page_{ts}.html\n"
                    )
                    sys.__stdout__.flush()
                except Exception:
                    pass

                # The video path must be obtained while ctx is still alive, but the file is not fully flushed
                # until ctx.close() - so the order is: capture path -> save session -> close ctx
                # -> return. close was missing before, truncating video files under headed mode.
                video_path = await self._capture_video(page)
                await save_page_session("doubao", ctx)
                await _close_headed(ctx, headed_browser, headed_pw)
                hint = (
                    "Run scripts/doubao_login.py on a desktop and re-upload the session."
                    if use_headed
                    else "Install Xvfb and set DISPLAY so Doubao can run headed."
                )
                # Report to the pool: this session was hit by a CAPTCHA, calculate captcha_count + 1.
                # Once the threshold (currently 3) is reached, the backend automatically quarantines it and the next check-out skips it.
                report_session_outcome("doubao", captcha_triggered=True)
                return EngineResult(
                    engine=self.name, query=query,
                    error=(
                        "CAPTCHA: ByteDance challenged the request after submit. "
                        f"{hint}"
                    ),
                    video_path=video_path,
                )

            # Wait for response — longer timeout after CAPTCHA recovery
            max_wait = 90
            sys.__stdout__.write("[Doubao-step] wait_for_stable_answer\n"); sys.__stdout__.flush()
            await self._wait_for_stable_answer(page, max_wait=max_wait, stable_secs=3.0)
            sys.__stdout__.write("[Doubao-step] answer stabilized\n"); sys.__stdout__.flush()
            await human_delay(1.0, 2.0)

            # Debug: dump screenshot after answer wait (CAPTCHA path)
            if headed_browser:
                try:
                    import os
                    dbg = "/tmp/doubao_captcha"
                    os.makedirs(dbg, exist_ok=True)
                    await page.screenshot(path=f"{dbg}/after_answer_{int(asyncio.get_event_loop().time())}.png")
                    html_dump = await page.evaluate("() => document.body.innerHTML")
                    with open(f"{dbg}/after_answer.html", "w") as f:
                        f.write(html_dump)
                    sys.__stdout__.write(
                        f"[Doubao-debug] post-answer dump saved ({len(html_dump)} bytes)\n"
                    )
                    sys.__stdout__.flush()
                except Exception:
                    pass

            # Debug probe
            await self._probe_page(page)

            # Extract answer + citations
            answer = await self._extract_answer(page)
            citations = await self._extract_citations(page, answer)
            reasoning = await extract_reasoning(page, "doubao")

            # Same as the hot path: on empty extraction, probe the login session first. Cannot return early here -
            # session saving / recording / closing the context must still finish, so store the reason and merge it into the result at the end.
            login_lost_reason = None
            if not (answer or "").strip():
                login_lost_reason = await self._probe_login_lost(page)

            await save_page_session("doubao", ctx)

            video_path = None
            if want_record(self._record_video):
                try:
                    from ..video_store import get_video_path
                    video_path = await get_video_path(page)
                except Exception:
                    pass

            await _close_headed(ctx, headed_browser, headed_pw)

            # No captcha hit -> tell the pool not to touch captcha_count. Login loss is a different matter
            # (the account itself is dead, not briefly blocked by risk control); the two must be reported separately.
            report_session_outcome("doubao", captcha_triggered=False)
            if login_lost_reason:
                return EngineResult(
                    engine=self.name, query=query, error=login_lost_reason,
                    video_path=video_path,
                )
            return EngineResult(
                engine=self.name,
                query=query,
                answer=answer,
                citations=citations,
                video_path=video_path,
                reasoning=reasoning,
            )
        except Exception as e:
            # Exception: unknown whether it is a CAPTCHA or something else; conservatively treat as no-CAPTCHA (avoids wrongful quarantine).
            report_session_outcome("doubao", captcha_triggered=False)
            return EngineResult(engine=self.name, query=query, error=str(e))

    async def _capture_video(self, page) -> str | None:
        """Capture video path from page before context is closed."""
        if not want_record(self._record_video):
            return None
        try:
            from ..video_store import get_video_path
            return await get_video_path(page)
        except Exception:
            return None

    # ── Popup dismissal ────────────────────────────────────────

    async def _check_captcha(self, page) -> bool:
        """Check if ByteDance CAPTCHA is present/active. Returns True if blocked.

        Only matches HIGH-CONFIDENCE CAPTCHA signals. The previous version had
        broad heuristics ([class*='captcha'], [class*='overlay'], hidden input
        + any modal) that false-positive on Doubao's normal error states (e.g.
        the red ⚠️ that appears next to a rate-limited message has no CAPTCHA
        UI but used to trip the broad selector). Let real CAPTCHA flows be
        flagged by the SDK iframe / explicit container; everything else falls
        through and lets the answer-waiting path run.
        """
        try:
            which = await page.evaluate("""() => {
                // 1. ByteDance CAPTCHA SDK loads its widget in an iframe whose
                //    src points at verify.zijieapi.com or a /captcha/ path.
                for (const iframe of document.querySelectorAll('iframe')) {
                    const src = iframe.src || '';
                    if (src.includes('verify.zijieapi.com')
                        || src.includes('verifycenter')
                        || /\\/captcha\\//.test(src)) {
                        const r = iframe.getBoundingClientRect();
                        if (r.width > 100 && r.height > 100) return 'iframe:' + src.slice(0, 80);
                    }
                }
                // 2. Explicit slider container the SDK injects when active.
                const slider = document.getElementById('captcha_container')
                    || document.querySelector('#captcha-sdk, .captcha-sdk-container');
                if (slider) {
                    const style = getComputedStyle(slider);
                    if (style.display !== 'none' && style.visibility !== 'hidden'
                        && slider.offsetWidth > 50 && slider.innerHTML.length > 0) {
                        return 'slider';
                    }
                }
                // 3. Image-classification CAPTCHA: ByteDance renders a modal
                //    with clickable image tiles. Require BOTH a visible
                //    captcha-named container AND >= 4 clickable images.
                for (const ov of document.querySelectorAll(
                    '[class*="captcha"], [class*="Captcha"]'
                )) {
                    const s = getComputedStyle(ov);
                    if (s.display === 'none' || s.visibility === 'hidden') continue;
                    if (ov.offsetWidth < 200 || ov.offsetHeight < 150) continue;
                    if (ov.querySelectorAll('img').length >= 4) return 'image_modal';
                }
                return '';
            }""")
            if which:
                sys.__stdout__.write(f"[Doubao] _check_captcha matched: {which}\n")
                sys.__stdout__.flush()
                return True
            return False
        except Exception:
            return False

    async def _wait_for_captcha_clear(self, page, timeout: int = 120) -> bool:
        """Poll until CAPTCHA disappears or timeout. Returns True if cleared."""
        poll_interval = 2.0
        elapsed = 0.0
        while elapsed < timeout:
            if not await self._check_captcha(page):
                # Extra check: wait a bit and re-verify it stays gone
                await asyncio.sleep(1.5)
                if not await self._check_captcha(page):
                    return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            if int(elapsed) % 10 == 0:
                remaining = int(timeout - elapsed)
                sys.__stdout__.write(
                    f"[Doubao] Still waiting for CAPTCHA... ({remaining}s remaining)\n"
                )
                sys.__stdout__.flush()
        return False

    async def _dismiss_popups(self, page) -> None:
        for sel in [
            "text=我知道了",
            "text=同意",
            "text=Accept",
            "text=接受全部",
            "text=不再提示",
            "text=关闭",
            "text=下载电脑版",
            "[aria-label='close']",
            "[aria-label='Close']",
            "[class*='close'] >> visible=true",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click()
                    await human_delay(0.3, 0.6)
            except Exception:
                continue

    async def _start_new_chat(self, page) -> None:
        """Reset to a brand-new conversation: full-page navigation first, clicking the "新对话" ("New chat") button as fallback.

        D4c (2026-05-18): in hot mode, just clicking is not enough - if typing starts before the page transition
        finishes, the query goes into the **old conversation** (measured: running "你好" ("hello") 3 times in hot mode,
        the first 2 got 500+ char context-laden answers, only the 3rd went back to ~100 chars).

        2026-08-30 added full-page navigation as the first choice: the button path is increasingly unreliable server-side -
        after the page is used once the layout changes, or a verify overlay covers it, and if the button cannot be clicked the whole round errors
        (refusing to send query into a stale session). And Doubao now requires a human verification for **every newly opened
        incognito window**, so we cannot fall back to the one-shot mode of "a new window per query" either
        - the verification state is never kept, and every query gets bounced by a CAPTCHA (measured 2026-08-29: new accounts
        with intact login sessions, all 4 tasks failed on per-query verification).

        The physical device worker avoided this dilemma long ago (V-networks/workers/desktop-cn/doubao_collector.py
        prepare_page): reuse the same context to keep the verification state, and hard-reset each query with full-page navigation
        to /chat/. Navigation does not depend on any button, and cookies / storage within the same context
        are kept as-is, so human verification only needs to be passed once.

        Flow:
          1. Record the URL + message-list row count
          2. Navigate the full page to /chat/, wait for the SPA to mount and verify the rows are cleared -> return on success
          3. Navigation failed/residue remains -> fall back to the original button click + verification retry (up to 2 rounds)
          4. Both paths failed -> raise; never send the query into a residual conversation
        """
        candidates = [
            "[class*='nav-link-']:has-text('新对话')",
            "[class*='sidebar_nav_item']:has-text('新对话')",
            "div.font-semibold:text-is('新对话')",
            "button:text-is('新对话')",
            "[role='button']:text-is('新对话')",
            "text=新对话",
        ]

        async def _count_rows() -> int:
            try:
                return await page.evaluate("""() => {
                    const list = document.querySelector('[class*="message-list-"]');
                    if (!list) return 0;
                    return list.querySelectorAll('.v_list_row').length;
                }""")
            except Exception:
                return -1

        url_before = page.url
        rows_before = await _count_rows()

        # 1) Full-page navigation hard reset - first choice, independent of any button
        for nav_url in ("https://www.doubao.com/chat", "https://www.doubao.com/"):
            try:
                await page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:  # noqa: BLE001 - if one entry does not work, try the next
                sys.__stdout__.write(
                    f"[Doubao-new-chat] 导航 {nav_url} 失败: {str(e)[:80]}\n")
                sys.__stdout__.flush()
                continue
            # The SPA needs time to mount the message-list. Without waiting, "not rendered yet" would be misjudged as "cleared",
            # turning the reset check into an always-pass - worse than no check at all.
            await asyncio.sleep(2.0)
            rows_now = await _count_rows()
            # <=0 covers three cases: 0 = list exists but empty / 0 = list not mounted yet (brand-new conversation page, see _count_rows'
            # if (!list) return 0) / -1 = evaluate threw. Treating the exception as a pass is intentional:
            # this step is only a reset check; if unreadable, leave it to the downstream answer check rather than blocking the whole round here.
            if rows_now <= 0:
                sys.__stdout__.write(
                    f"[Doubao-new-chat] 整页导航重置成功 {nav_url} "
                    f"(rows {rows_before}->{rows_now})\n")
                sys.__stdout__.flush()
                await human_delay(0.5, 1.0)
                return
            sys.__stdout__.write(
                f"[Doubao-new-chat] 导航后仍有残留 rows={rows_now},换下一个入口\n")
            sys.__stdout__.flush()

        # 2) Fallback: click the "新对话" ("New chat") button (the original logic since 2026-05, can still help when navigation is blocked)
        for attempt in range(2):
            clicked = False
            for sel in candidates:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        clicked = True
                        sys.__stdout__.write(
                            f"[Doubao-new-chat] attempt {attempt+1} clicked via {sel!r}\n"
                        )
                        sys.__stdout__.flush()
                        break
                except Exception:
                    continue
            if not clicked:
                sys.__stdout__.write(
                    f"[Doubao-new-chat] attempt {attempt+1}: button not found\n"
                )
                sys.__stdout__.flush()
                await human_delay(0.5, 1.0)
                continue  # go to the next attempt instead of returning from the whole function - otherwise later queries go into the old conversation

            # Wait for the reset to take effect: URL changes to `/chat/` (no slug) OR the row count decreases
            reset_ok = False
            for _ in range(25):  # 25 × 0.2s = 5s
                await asyncio.sleep(0.2)
                url_now = page.url
                # URL check: no slug after the slash = /chat/ or /chat
                url_reset = (
                    url_now.endswith("/chat/") or url_now.endswith("/chat")
                ) and url_now != url_before
                rows_now = await _count_rows()
                rows_reset = rows_before > 0 and rows_now == 0
                if url_reset or rows_reset:
                    reset_ok = True
                    sys.__stdout__.write(
                        f"[Doubao-new-chat] verified reset (url_reset={url_reset} "
                        f"rows {rows_before}->{rows_now})\n"
                    )
                    sys.__stdout__.flush()
                    break

            if reset_ok:
                await human_delay(0.5, 1.0)  # give the UI a little more time to settle
                return

            sys.__stdout__.write(
                f"[Doubao-new-chat] attempt {attempt+1}: reset NOT verified after 5s "
                f"(url stayed={url_before == page.url}, rows {rows_before}->{await _count_rows()})\n"
            )
            sys.__stdout__.flush()
            await human_delay(0.5, 1.0)  # short pause before retrying

        # Reset failed in both rounds - raise so EngineSession records an error and the runner does not extract competitors, avoiding data pollution.
        # Lesson learned (2026-05-14 run_id=20): on reset failure it returned silently, later queries went into the residual conversation,
        # and doubao got a cross-talk reply about "泡泡玛特 vs MINISO LAND" ("Pop Mart vs MINISO LAND") that the LLM extracted as competitors, polluting brand-growth data.
        sys.__stdout__.write(
            "[Doubao-new-chat] ⚠ 整页导航与点按钮两条路都没能验证到 reset\n")
        sys.__stdout__.flush()
        raise RuntimeError(
            "doubao new-chat reset failed (navigation + button) — refusing to send query "
            "into a stale session (would contaminate answer with prior conversation)"
        )

    # ── Enable web search toggle ───────────────────────────────

    # ── Input detection ────────────────────────────────────────

    async def _find_input(self, page):
        """Find the chat input element. Returns the locator or None."""
        # 2026-04 update: Doubao switched from contenteditable to Semi Design textarea.
        # Must target the visible textarea (placeholder="发消息..." ("Send a message...")), not the hidden one (height=0).
        for sel in [
            "textarea.semi-input-textarea",
            "textarea[placeholder='发消息...']",
            "textarea[placeholder]",
            "[contenteditable='true'][class*='input']",
            "[contenteditable='true'][class*='editor']",
            "[contenteditable='true']",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    box = await el.bounding_box()
                    if box and box["height"] > 5:
                        return el
            except Exception:
                continue
        return None

    # ── Response waiting ───────────────────────────────────────

    async def _wait_for_stable_answer(
        self, page, max_wait: float = 120.0, stable_secs: float = 2.0
    ) -> None:
        """Poll the last assistant message's inner_text until stable.

        Uses the same engine-agnostic approach as Qwen: wait for the
        answer text to stop changing for `stable_secs` seconds.
        """
        poll_interval = 0.8
        last_text = ""
        stable_since = None
        elapsed = 0.0

        while elapsed < max_wait:
            try:
                cur = await self._get_last_assistant_text(page)
            except Exception:
                cur = last_text

            # Need substantive content (>8 chars) to consider stable
            if cur and len(cur.strip()) > 8 and cur == last_text:
                if stable_since is None:
                    stable_since = elapsed
                elif elapsed - stable_since >= stable_secs:
                    return
            else:
                last_text = cur
                stable_since = None

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    async def _get_last_assistant_text(self, page) -> str:
        """Get the text content of the last assistant message bubble.

        Must be scoped to the .message-list-* main message area. Doubao's 2026-04 sidebar
        conversation list uses chat-item-*, and its titles contain .content-* class title text (e.g.
        "brain disease biopharma companies and cited sources"); with a broad match like [class*='content-'],
        the last sidebar conversation title would be taken as the "latest answer", and every query
        would return the same sidebar title. Fix:
          1) only search within the .message-list- main area (or degrade to the page without a sidebar)
          2) reject chat-item-* / nav-link-* / sidebar-* hits
        """
        # Main path: in JS, "find the last receive row inside .message-list-".
        # Avoids the sidebar (chat-item-*) and navigation (nav-link-*).
        try:
            text = await page.evaluate("""() => {
                // 优先 .message-list-*(Doubao 2026-04 主消息区)。
                const list = document.querySelector('[class*="message-list-"]');
                const root = list || document.body;
                const rows = root.querySelectorAll('.v_list_row');
                for (let i = rows.length - 1; i >= 0; i--) {
                    const row = rows[i];
                    // 跳过 user side(send-msg)
                    if (row.querySelector('[class*="send-msg"], [data-send]')) continue;
                    // 跳过被 sidebar 包裹的(防御性 — message-list 内不该出现)
                    if (row.closest('[class*="chat-item-"], [class*="nav-link-"], [class*="sidebar"], nav')) continue;
                    // 优先抓答案正文容器(content-pWK1u_ 这种 hash class)
                    const contentEl = row.querySelector('[class*="content-"]:not([class*="chat-item-"]):not([class*="send-msg"])');
                    const text = (contentEl ? contentEl.innerText : row.innerText) || '';
                    if (text.trim().length > 10) return text;
                }
                return '';
            }""")
            if text and text.strip():
                return text
        except Exception:
            pass

        # Degraded path: try more specific selectors inside .message-list-
        for sel in [
            "[class*='message-list-'] [class*='markdown-body']",
            "[class*='message-list-'] [class*='markdown']",
            "[class*='message-list-'] [class*='rich-text']",
            "[class*='message-list-'] [class*='content-'][class*='pWK']",
        ]:
            try:
                els = await page.locator(sel).all()
                if els:
                    text = await els[-1].inner_text()
                    if text and text.strip():
                        return text
            except Exception:
                continue

        return ""

    # ── Network capture ────────────────────────────────────────

    async def _capture_body(self, response, bucket: List[str]) -> None:
        """Read a response body and stash text containing URL-like content."""
        try:
            text = await response.text()
        except Exception:
            return
        if not text or "http" not in text:
            return
        bucket.append(text[:400_000])
        # Optional debug dump (set GEO_DOUBAO_DUMP_BODIES=1 to re-enable).
        # Useful when adding new citation field shapes; off by default to
        # avoid filling /tmp.
        import os as _os
        if _os.environ.get("GEO_DOUBAO_DUMP_BODIES", "").strip() in ("1", "true"):
            try:
                dump_dir = "/tmp/doubao_bodies"
                _os.makedirs(dump_dir, exist_ok=True)
                ts = int(asyncio.get_event_loop().time() * 1000)
                url_short = response.url[:80].replace("/", "_").replace(":", "")
                with open(f"{dump_dir}/{ts}_{url_short}.txt", "w") as f:
                    f.write(f"URL: {response.url}\n")
                    f.write(f"Status: {response.status}\n")
                    f.write(f"Content-Type: {response.headers.get('content-type','')}\n")
                    f.write("---\n")
                    f.write(text[:200_000])
            except Exception:
                pass

    # ── Answer extraction ──────────────────────────────────────

    async def _extract_answer(self, page) -> str:
        """Extract the answer text from the last assistant message."""
        # Use the shared text-extraction logic
        text = await self._get_last_assistant_text(page)
        if text:
            cleaned = text
            for noise in ("参考来源", "参考资料", "来源：", "以上信息来源于"):
                cleaned = cleaned.replace(noise, "")
            return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return ""

    # ── Citation extraction (multi-path) ───────────────────────

    async def _extract_citations(self, page, answer: str) -> List[Citation]:
        """Additive multi-path extraction with global dedup.

        Priority:
          1. Network capture — API response bodies with structured URLs
          2. Reference section — "参考来源"/"参考资料" ("Reference sources"/"References") area with <a href>
          3. Inline superscript links — [1][2] style references
          4. Assistant container <a href> links
          5. Bare URLs in answer text
        """
        citations: List[Citation] = []
        seen_keys: set[str] = set()

        def _add(cit: Citation | None) -> None:
            if cit is None:
                return
            key = cit.url or cit.domain
            if not key or key in seen_keys:
                return
            seen_keys.add(key)
            # On the CloakBrowser path the SSE response yields CP1252-mojibake titles;
            # clean them once before persisting; on the patchright path clean strings are not altered.
            citations.append(
                Citation(
                    url=cit.url,
                    domain=cit.domain,
                    title=_fix_mojibake(cit.title or ""),
                    snippet=_fix_mojibake(cit.snippet or ""),
                    position=len(citations) + 1,
                )
            )

        # ── 1) Network-capture: SSE-aware JSON walk for url+title+sitename
        # Doubao chat/completion is text/event-stream. Each frame:
        #   id: N
        #   event: STREAM_CHUNK
        #   data: {"patch_op":[{"patch_value":{"content_block":[{
        #     "block_type":10025,
        #     "content":{"search_query_result_block":{"results":[{
        #       "text_card":{
        #         "title":"<brand-k>SU7", "sitename":"懂车帝",   ("Xiaomi SU7", "Dongchedi")
        #         "url":"https://www.dongchedi.com/auto/series/6187",
        #         "summary":"...", "publish_time_second":"..."
        #       }
        #     }, ...]}}
        #   }]}]}
        #
        # Recursively walk dicts; whenever we see a dict with `url` field,
        # pair it with `title`/`sitename`/`summary` from the same level.
        import json as _json
        from urllib.parse import urlparse as _urlparse

        def _walk_search_cards(obj):
            """Take only Doubao's 「引用来源」 ("Cited sources") block - content_block[].content
            .search_query_result_block.results[].text_card.

            This is the 「引用来源(N)」 ("Cited sources (N)") column in the Doubao UI, exactly matching the sources customers see.
            **Do not** use the generic _walk_for_citations below as the main path: it also collects
            "related videos" (the Douyin recommendation module), the retrieval corpus pool and link.wtturl.cn short links
            - measured: one query inflated to 29 items (only 6-9 real citations),
            mixing in non-citation content and double counting the same source as "short link + resolved domain".
            Metric definition: however many items they return, that is how many we give.
            """
            out = []
            if isinstance(obj, dict):
                blk = obj.get("search_query_result_block")
                if isinstance(blk, dict):
                    for r in (blk.get("results") or []):
                        if not isinstance(r, dict):
                            continue
                        card = r.get("text_card") if isinstance(r.get("text_card"), dict) else r
                        url = card.get("url") or card.get("link") or ""
                        if isinstance(url, str) and url.startswith("http"):
                            out.append((
                                url,
                                (card.get("title") or "").strip(),
                                (card.get("sitename") or card.get("source_name")
                                 or card.get("site") or "").strip(),
                                (card.get("summary") or card.get("abstract")
                                 or card.get("snippet") or "").strip(),
                                (card.get("publish_time_second")
                                 or card.get("publish_time") or "").strip(),
                            ))
                for v in obj.values():
                    out.extend(_walk_search_cards(v))
            elif isinstance(obj, list):
                for item in obj:
                    out.extend(_walk_search_cards(item))
            return out

        def _walk_for_citations(obj):
            """Yield (url, title, sitename, snippet, publish_time) for
            every dict containing a usable url field."""
            out = []
            if isinstance(obj, dict):
                url = obj.get("url") or obj.get("link") or obj.get("href") or ""
                if isinstance(url, str) and url.startswith("http"):
                    out.append((
                        url,
                        (obj.get("title") or "").strip(),
                        (obj.get("sitename") or obj.get("source_name")
                         or obj.get("site") or "").strip(),
                        (obj.get("summary") or obj.get("abstract")
                         or obj.get("snippet") or obj.get("description") or "").strip(),
                        (obj.get("publish_time_second")
                         or obj.get("publish_time") or "").strip(),
                    ))
                for v in obj.values():
                    out.extend(_walk_for_citations(v))
            elif isinstance(obj, list):
                for item in obj:
                    out.extend(_walk_for_citations(item))
            return out

        net_url_count = 0
        net_titled_count = 0
        for body in getattr(self, "_captured_bodies", []) or []:
            # SSE: frames are "\n\n"-separated; each frame may have multiple
            # lines (id:, event:, data:). We only care about data: payload.
            for event_block in body.split("\n\n"):
                for line in event_block.split("\n"):
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        obj = _json.loads(payload)
                    except _json.JSONDecodeError:
                        continue
                    for u, title, sitename, snippet, pubtime in _walk_search_cards(obj):
                        net_url_count += 1
                        if any(b in u for b in _DOUBAO_BLOCK_HOSTS):
                            continue
                        # Build display title — prefer article title; fall
                        # back to sitename when no article title is given.
                        display_title = title or sitename
                        # Snippet keeps sitename + summary for downstream context
                        snippet_parts = []
                        if sitename and sitename != display_title:
                            snippet_parts.append(f"[{sitename}]")
                        if snippet:
                            snippet_parts.append(snippet[:280])
                        full_snippet = " ".join(snippet_parts).strip()
                        try:
                            domain = _urlparse(u).netloc.replace("www.", "")
                        except Exception:
                            domain = ""
                        if title:
                            net_titled_count += 1
                        _add(Citation(
                            url=u,
                            domain=domain,
                            title=display_title,
                            snippet=full_snippet,
                            position=0,
                        ))
            # Also try whole-body as single JSON (non-SSE endpoints)
            try:
                obj = _json.loads(body)
                for u, title, sitename, snippet, _pt in _walk_search_cards(obj):
                    net_url_count += 1
                    if any(b in u for b in _DOUBAO_BLOCK_HOSTS):
                        continue
                    display_title = title or sitename
                    snippet_parts = []
                    if sitename and sitename != display_title:
                        snippet_parts.append(f"[{sitename}]")
                    if snippet:
                        snippet_parts.append(snippet[:280])
                    full_snippet = " ".join(snippet_parts).strip()
                    try:
                        domain = _urlparse(u).netloc.replace("www.", "")
                    except Exception:
                        domain = ""
                    if title:
                        net_titled_count += 1
                    _add(Citation(
                        url=u,
                        domain=domain,
                        title=display_title,
                        snippet=full_snippet,
                        position=0,
                    ))
            except Exception:
                pass

        # Keep variable name for the probe log below
        net_urls = [c.url for c in citations]

        # The precise path got Doubao's 「引用来源」 ("Cited sources") list -> stop here, do not run the DOM fallback below.
        # The fallback scrapes every a[href] on the page: related videos, link.wtturl.cn short links,
        # duplicates of the same source... measured inflating 6-9 real citations to 29. The metric definition is "however many they return,
        # that is how many we give", so only degrade to the DOM when the network path got **nothing at all**.
        if citations:
            print(f"[Doubao-probe] citations={len(citations)} (精确路径:引用来源块)",
                  file=sys.stderr, flush=True)
            return citations

        # ── 2) Reference section: "参考来源" / "参考资料" ("Reference sources" / "References") area
        try:
            ref_links = await page.evaluate("""() => {
                const out = [];
                // Look for reference/source sections
                const headings = document.querySelectorAll(
                    'h1, h2, h3, h4, h5, div, p, span'
                );
                let refSection = null;
                for (const h of headings) {
                    const t = (h.innerText || '').trim();
                    if (/参考(来源|资料|文献)|References?|Sources?/.test(t)) {
                        // Walk up to find the parent container
                        refSection = h.closest(
                            '[class*="message"], [class*="answer"], [class*="markdown"], section, div'
                        );
                        break;
                    }
                }
                // If found, extract links from that section
                const container = refSection || document;
                container.querySelectorAll('a[href^="http"]').forEach(a => {
                    out.push({
                        href: a.href,
                        title: (a.innerText || a.textContent || '').trim().slice(0, 200),
                    });
                });
                return out;
            }""") or []
            for link in ref_links:
                href = link.get("href", "")
                title = link.get("title", "")
                if not href.startswith("http"):
                    continue
                if any(b in href for b in _DOUBAO_BLOCK_HOSTS):
                    continue
                _add(Citation.from_url(href, title=title))
        except Exception:
            pass

        # ── 3) Source cards: Doubao shows source cards with links
        try:
            source_links = await page.evaluate("""() => {
                const out = [];
                const seen = new Set();
                // Source cards typically have specific class patterns
                document.querySelectorAll(
                    '[class*="source"] a[href^="http"], ' +
                    '[class*="reference"] a[href^="http"], ' +
                    '[class*="citation"] a[href^="http"], ' +
                    '[class*="search-result"] a[href^="http"]'
                ).forEach(a => {
                    if (seen.has(a.href)) return;
                    seen.add(a.href);
                    out.push({
                        href: a.href,
                        title: (a.innerText || a.textContent || '').trim().slice(0, 200),
                    });
                });
                return out;
            }""") or []
            for link in source_links:
                href = link.get("href", "")
                title = link.get("title", "")
                if not href.startswith("http"):
                    continue
                if any(b in href for b in _DOUBAO_BLOCK_HOSTS):
                    continue
                _add(Citation.from_url(href, title=title))
        except Exception:
            pass

        # ── 4) All <a href> in the last assistant message area
        try:
            # Find the last assistant message container
            for container_sel in [
                "[class*='assistant']",
                "[class*='message'][class*='bot']",
                "[class*='message'][class*='ai']",
            ]:
                containers = await page.locator(container_sel).all()
                if not containers:
                    continue
                last = containers[-1]
                links = await last.locator("a[href^='http']").all()
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        title = await link.inner_text()
                    except Exception:
                        continue
                    if not href or not href.startswith("http"):
                        continue
                    if any(b in href for b in _DOUBAO_BLOCK_HOSTS):
                        continue
                    _add(Citation.from_url(href, title=title))
                break
        except Exception:
            pass

        # ── 5) All page <a href> (fallback, excluding navigation)
        try:
            all_links = await page.locator("a[href^='http']").all()
            for link in all_links:
                try:
                    href = await link.get_attribute("href")
                    title = await link.inner_text()
                except Exception:
                    continue
                if not href or not href.startswith("http"):
                    continue
                if any(b in href for b in _DOUBAO_BLOCK_HOSTS):
                    continue
                # Skip navigation/logo links (short or generic text)
                if title and len(title.strip()) <= 2:
                    continue
                _add(Citation.from_url(href, title=title))
        except Exception:
            pass

        # ── 6) Bare URLs in answer text (last resort)
        for u in extract_urls_from_text(answer):
            if any(b in u for b in _DOUBAO_BLOCK_HOSTS):
                continue
            _add(Citation.from_url(u))

        with_title = sum(1 for c in citations if c.title)
        sys.__stdout__.write(
            f"[Doubao-probe] net_walk_urls={net_url_count} net_titled={net_titled_count} "
            f"citations={len(citations)} with_title={with_title}\n"
        )
        sys.__stdout__.flush()

        return citations

    # ── Debug probe ────────────────────────────────────────────

    async def _probe_page(self, page) -> None:
        """Dump debug info about the current page state for offline analysis."""
        try:
            probe = await page.evaluate("""() => {
                // Buttons and interactive elements
                const buttons = [];
                document.querySelectorAll(
                    'button, [role="button"], div[tabindex], [contenteditable]'
                ).forEach(el => {
                    const text = (el.innerText || '').trim();
                    if (text && text.length <= 30) {
                        buttons.push({
                            tag: el.tagName,
                            text: text,
                            cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
                            ce: el.getAttribute('contenteditable'),
                            role: el.getAttribute('role'),
                        });
                    }
                });
                // Key phrase counts
                const body_text = document.body.innerText || '';
                const keywords = {};
                ['联网搜索', '参考来源', '参考资料', '来源', '引用',
                 '搜索', 'Search', 'Source', 'Reference', 'citation'
                ].forEach(kw => {
                    keywords[kw] = (body_text.match(new RegExp(kw, 'gi')) || []).length;
                });
                // Count links
                const httpLinks = document.querySelectorAll('a[href^="http"]').length;
                return { buttons: buttons.slice(0, 40), keywords, httpLinks };
            }""")

            sys.__stdout__.write(
                f"[Doubao-probe] keywords={probe['keywords']} "
                f"httpLinks={probe['httpLinks']}\n"
            )
            sys.__stdout__.write("[Doubao-probe] buttons:\n")
            for b in probe["buttons"]:
                sys.__stdout__.write(f"  {b}\n")
            sys.__stdout__.flush()
        except Exception as e:
            sys.__stdout__.write(f"[Doubao-probe] failed: {type(e).__name__}: {e}\n")
            sys.__stdout__.flush()

        # Dump body HTML for first-run analysis
        try:
            import os
            dump_path = "/tmp/doubao_q1_body.html"
            if not os.path.exists(dump_path):
                body_html = await page.evaluate("() => document.body.innerHTML")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(body_html)
                sys.__stdout__.write(
                    f"[Doubao-probe] dumped body HTML ({len(body_html)} bytes) -> {dump_path}\n"
                )
                sys.__stdout__.flush()
        except Exception as e:
            sys.__stdout__.write(f"[Doubao-probe] dump failed: {type(e).__name__}: {e}\n")
            sys.__stdout__.flush()
