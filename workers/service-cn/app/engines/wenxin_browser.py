"""Wenxin (ERNIE Bot) browser adapter — automates chat.baidu.com.

chat.baidu.com is Baidu AI Search, the version with web citations. Usable anonymously; answers
list reference sources.
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
from typing import List

from ..browser import (
    create_stealth_page,
    create_headed_page,
    human_delay,
    human_click,
    simulate_browsing,
    save_page_session,
)
from ..anti_detect import _pick_profile
from ..video_store import want_record
from .base import Citation, EngineAdapter, EngineResult, extract_urls_from_text
from ..reasoning import extract_reasoning


async def _close_headed(ctx, browser=None, pw=None) -> None:
    """Close headed browser context, browser, and playwright instance.

    On the CloakBrowser path browser / pw are both None, so only ctx is closed; on the patchright
    fallback path close in order ctx -> browser -> pw.stop().
    """
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


class WenxinBrowserAdapter(EngineAdapter):
    name = "文心一言"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    # _WENXIN_PATCH_V2
    async def _dismiss_new_version_modal(self, page) -> None:
        """Close the new first-screen popup 「全新上线任务模式」 ("Task mode newly launched").

        Measured 2026-08-22: this popup covers the whole page; the 「开启新对话」 ("start new chat") button's is_visible is still true,
        but the click is intercepted by pointer events and times out - the upper layer sees "button not found".
        This was the real cause of Wenxin failing 100% on ECS since 8-21 (the test host only avoided it because a long-lived hot session
        created before the redesign never reopened the page).

        The close button is `.cos-dialog-close` / `i.cos-icon-close`; Escape does not close it (measured).
        **Do not click 「试一试」 ("Try it")** - that "enters task mode", and anonymous accounts clicking it get
        「未登录状态下暂不支持使用任务模式能力」 ("task mode is not supported when not logged in"), which gets persisted as a normal answer.
        """
        # **The criterion is the popup itself, not the copy.** It used to be
        # `if page.locator("text=全新上线任务模式").count() == 0: return`,
        # and on 2026-09-01 Baidu replaced this popup (class `_task-mode-guide-dialog_...`, the page
        # no longer has that sentence) - once the copy changed the gate let it through, the popup stayed on the page covering the input box, and every query
        # was `Locator.click: Timeout ... subtree intercepts pointer events`,
        # persisted by the upper layer as an "empty answer". The fleet-wide empty answer rate rose from ~5% to 20-25%, took two days to track down,
        # and switching accounts / rotating hot sessions did not help - not an account problem, just an extra lid on the page.
        try:
            if await page.locator(".cos-dialog").count() == 0:
                return
        except Exception:
            return
        # 1) If there is a close button, press it - cleanest
        for sel in (".cos-dialog-close", "i.cos-icon-close",
                    "[aria-label='关闭']", "[aria-label='close']"):
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click(timeout=3000)
                    await human_delay(0.3, 0.6)
                    break
            except Exception:
                continue
        # 2) If still blocking, remove it from the DOM. **Only remove onboarding types**: the login / captcha popups
        #    must stay so the upper layer sees and handles them; silently removing them would hide real problems
        #    (login loss would turn into "very short answers", harder to debug than now).
        try:
            removed = await page.evaluate(
                """() => {
                    let n = 0;
                    for (const el of document.querySelectorAll('.cos-dialog, [class*=dialog-mask]')) {
                        const cls = String(el.className || '');
                        const txt = el.innerText || '';
                        if (/登录|验证|安全验证|captcha/i.test(txt)) continue;
                        if (/guide|task-mode|banner/i.test(cls)
                            || /任务模式|全新上线|了解一下|新功能/.test(txt)) {
                            el.remove(); n++;
                        }
                    }
                    return n;
                }"""
            )
            if removed:
                sys.__stdout__.write(
                    f"[Wenxin] 引导弹窗挡住输入框,已移除 {removed} 个节点\n")
                sys.__stdout__.flush()
        except Exception:
            pass

    async def _start_new_chat(self, page) -> None:
        """Reset to a brand-new conversation: full-page navigation first, clicking the "开启新对话" ("start new chat") button as fallback.

        Measured on yiyan.baidu.com (2026-05-18 DOM probe): top sidebar div container
        class pattern `sidebarNewSession__<hash>` (CSS module, stable prefix, hash changes),
        text="新对话" ("New chat"). Prefix matching is most stable.

        2026-08-31 added full-page navigation as the first choice: the button path is too fragile - copy/class names change with gradual rollouts (already changed once from
        「新对话」 to 「开启新对话」), when a popup covers it is_visible is still true but it cannot be clicked,
        and once it cannot be found the whole round errors. The batch of 3 wenxin jobs for the interconnect tech customer failed twice in a row on
        "new-chat button not found", while 32 in the same batch were fine - meaning it was not an account or network problem,
        but the button itself being unreliable.

        Navigating to CHAT_URL is a brand-new conversation, independent of any button, and cookies within the same context
        are kept as-is. Criterion: after navigation the page no longer has the previous round's answer container
        (div.conversation-flow-answer-container, the same one _extract uses).
        Same approach as Doubao's _start_new_chat (verified on Doubao first on 2026-08-30).
        """
        await self._dismiss_new_version_modal(page)

        # 1) Full-page navigation hard reset
        try:
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
            # The SPA needs time to mount; without waiting, "not rendered yet" would be misjudged as "cleared", making the check always pass
            await asyncio.sleep(2.0)
            await self._dismiss_new_version_modal(page)
            left = await page.locator("div.conversation-flow-answer-container").count()
            if left == 0:
                sys.__stdout__.write("[Wenxin-new-chat] 整页导航硬重置成功\n")
                sys.__stdout__.flush()
                await human_delay(0.5, 1.0)
                return
            sys.__stdout__.write(
                f"[Wenxin-new-chat] 导航后仍有 {left} 个答案容器,回退点按钮\n")
            sys.__stdout__.flush()
        except Exception as e:  # noqa: BLE001 - if navigation fails, fall back to the button
            sys.__stdout__.write(f"[Wenxin-new-chat] 导航失败: {str(e)[:70]},回退点按钮\n")
            sys.__stdout__.flush()

        # 2) Fallback: click the "开启新对话" ("start new chat") button (original logic)
        candidates = [
            # Measured 2026-08-22: chat.baidu.com now 302s entirely to the new wenxin.baidu.com,
            # and the top of the sidebar became span.new-dialog-container-text with the text 「开启新对话」 ("start new chat").
            "[class*='new-dialog-container']",
            "text=开启新对话",
            # The old version (yiyan.baidu.com era) is kept as a fallback, still usable if the gradual rollout reverts.
            "[class*='sidebarNewSession']",
            "[class*='sideBarTop']:has-text('新对话')",
            "div[class*='sidebarNewSession']:has-text('新对话')",
            "text=新对话",
        ]
        for sel in candidates:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await human_delay(0.5, 1.0)
                    sys.__stdout__.write(f"[Wenxin-new-chat] clicked via {sel!r}\n")
                    sys.__stdout__.flush()
                    return
            except Exception:
                continue
        sys.__stdout__.write(
            "[Wenxin-new-chat] 整页导航与点按钮两条路都没能重置 — needs DOM probe\n")
        sys.__stdout__.flush()
        # raise so EngineSession records an error and the runner does not extract competitors, avoiding data pollution from old conversation context
        raise RuntimeError(
            "wenxin new-chat reset failed (navigation + button) — refusing to send query "
            "into stale session"
        )

    CHAT_URL = "https://chat.baidu.com/"

    async def is_available(self) -> bool:
        try:
            page, ctx = await create_stealth_page("wenxin")
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=15000)
            logged_in = await page.locator("textarea, [contenteditable='true']").count() > 0
            await ctx.close()
            return logged_in
        except Exception:
            return False

    # 2026-05-26: migrated to CloakBrowser (github.com/CloakHQ/CloakBrowser).
    # Baidu's risk control is on par with ByteDance's (doubao); playwright's navigator/webgl/permissions traces
    # got hammered by chat.baidu.com a few times; CloakBrowser's compile-layer patches are clean + mouse trajectories are hardened too.
    async def _create_hot_page(self):
        """Wenxin only: Xvfb + headed + cloakbrowser."""
        import os
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        page, ctx = await create_headed_page(
            "wenxin",
            profile=profile,
            record_video=want_record(self._record_video),
        )
        return page, ctx

    async def _close_hot_page(self, page, ctx) -> None:
        """Wenxin only: close both the cloakbrowser ctx and the patchright fallback's browser/pw."""
        headed_browser = getattr(page, "_headed_browser", None)
        headed_pw = getattr(page, "_pw_ref", None)
        await _close_headed(ctx, headed_browser, headed_pw)

    # D4d(2026-05-18):hot browser protocol — _prepare_page + _query_with_page
    # Shares search()'s helpers; search() itself is **untouched**, one-shot with zero regression.
    async def _prepare_page(self, page, ctx) -> None:
        """Hot init: goto + popups; _start_new_chat is left to each query."""
        try:
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await page.goto(self.CHAT_URL, wait_until="load", timeout=60000)
        await human_delay(2, 3)
        await self._dismiss_popups(page)
        input_el = page.locator("textarea, [contenteditable='true']").first
        try:
            await input_el.wait_for(state="visible", timeout=10000)
        except Exception:
            raise RuntimeError("input not visible after prepare — session likely expired")

    async def _query_with_page(self, page, ctx, query: str) -> EngineResult:
        """Hot query:reset → warm-up → input → submit → wait → extract.

        Shares search()'s _wait_text_stable / _expand_references / _extract_citations.
        2026-05-26: the CloakBrowser path adds simulate_browsing warm-up + human_click focus +
        per-character keyboard.type - Baidu risk control watches timing, and fill() pouring in the whole text at once is the strongest bot signature.
        """
        await self._start_new_chat(page)
        await human_delay(0.5, 1.0)

        try:
            await simulate_browsing(page, duration=random.uniform(2.0, 4.0))
        except Exception as e:
            sys.__stdout__.write(f"[Wenxin-hot] simulate warning: {e}\n")
            sys.__stdout__.flush()

        input_el = page.locator("textarea, [contenteditable='true']").first
        try:
            await input_el.wait_for(state="visible", timeout=10000)
        except Exception:
            return EngineResult(engine=self.name, query=query, error="input not visible")
        await self._focus_input(page, input_el)
        await human_delay(0.8, 1.6)
        for ch in query:
            await page.keyboard.type(ch, delay=random.randint(60, 180))
        await human_delay(0.5, 1.2)
        await page.keyboard.press("Enter")

        await human_delay(3, 5)
        # Same as search() - actively poll from Python (raf stops running after streaming ends,
        # so wait_for_function is unreliable). See the _wait_streaming_done comment.
        await self._wait_streaming_done(page, max_wait=30)
        await self._wait_text_stable(page, max_wait=30, stable_secs=3.0)
        await human_delay(1.0, 2.0)

        # Uses the same multi-block answer concatenation logic as search() (simplified)
        answer = ""
        try:
            container = page.locator("div.conversation-flow-answer-container").last
            if await container.count() > 0:
                blocks = await container.locator(".cosd-markdown-content, .cosd-markdown, .ai-markdown").all()
                parts: list[str] = []
                seen: set[str] = set()
                for b in blocks:
                    try:
                        t = (await b.inner_text()).strip()
                    except Exception:
                        continue
                    if t and t not in seen:
                        seen.add(t)
                        parts.append(t)
                if parts:
                    answer = "\n\n".join(parts)
        except Exception:
            pass

        await self._expand_references(page)
        citations = await self._extract_citations(page, answer)
        reasoning = await extract_reasoning(page, "wenxin")
        return EngineResult(engine=self.name, query=query, answer=answer, citations=citations,
                            reasoning=reasoning)

    async def search(self, query: str) -> EngineResult:
        import os
        # 2026-05-26: one-shot also uses CloakBrowser, fully consistent with the hot path.
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        ctx = None
        page = None
        headed_browser = None
        headed_pw = None
        try:
            page, ctx = await create_headed_page(
                "wenxin",
                profile=profile,
                record_video=want_record(self._record_video),
            )
            headed_browser = getattr(page, "_headed_browser", None)
            headed_pw = getattr(page, "_pw_ref", None)
            # chat.baidu.com first does a 302 -> main site, and domcontentloaded fires slowly (especially with browser host network jitter);
            # 30s often timed out. Raised to 60s + fall back to load on failure (fires earlier).
            try:
                await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                try:
                    await page.goto(self.CHAT_URL, wait_until="load", timeout=60000)
                except Exception as e:
                    await _close_headed(ctx, headed_browser, headed_pw)
                    return EngineResult(engine=self.name, query=query, error=f"goto failed: {e}")
            await human_delay(2, 3)

            await self._dismiss_popups(page)

            try:
                await simulate_browsing(page, duration=random.uniform(2.0, 4.0))
            except Exception as e:
                sys.__stdout__.write(f"[Wenxin-search] simulate warning: {e}\n")
                sys.__stdout__.flush()

            input_el = page.locator("textarea, [contenteditable='true']").first
            try:
                await input_el.wait_for(state="visible", timeout=10000)
            except Exception:
                await _close_headed(ctx, headed_browser, headed_pw)
                return EngineResult(engine=self.name, query=query, error="input not visible")

            await self._focus_input(page, input_el)
            await human_delay(0.8, 1.6)
            for ch in query:
                await page.keyboard.type(ch, delay=random.randint(60, 180))
            await human_delay(0.5, 1.2)

            await page.keyboard.press("Enter")

            # Wait for streaming to finish using stability detection.
            # chat.baidu.com uses `.cosd-markdown-content-typingall` during streaming
            # and removes it when done.
            #
            # ⚠ The previous 60s timeout was not enough for complex queries (1500+ chars like FDI/antitrust lawyer recommendations);
            # wait exited early on timeout and extract got the mid-stream state (~600 chars).
            # Measured: a 1.5K-char query needs ~90-120s. Timeout raised to 180s + a stability double
            # check (after typing disappears, poll inner_text stable for 3s before returning), same as qwen.
            await human_delay(3, 5)
            # 2026-05-18: the real streaming indicator selectors are [class*=stopBtn]
            # / [class*=stopDealBtn] / [class*=name-msg-animation], appearing at t=3s
            # and all gone after t=6s. But page.wait_for_function polls with raf, and after
            # streaming ends the browser stops painting, raf does not fire, and the full 180s gets used up.
            # Changed to an active 0.5s evaluate poll from Python, bypassing the raf problem.
            await self._wait_streaming_done(page, max_wait=30)

            # Second confirmation: after stop disappears, poll the answer container's inner_text until stable for 3 seconds,
            # preventing a momentary stop button toggle from being misjudged as completion. Max 30s fallback.
            await self._wait_text_stable(page, max_wait=30, stable_secs=3.0)

            # Extra settle time for final markdown render
            await human_delay(1.0, 2.0)

            answer = ""
            # chat.baidu.com renders "body + follow-up CTA" as two separate
            # .cosd-markdown-content blocks, so they must be concatenated rather than taking only [-1].
            # Lock onto the last answer container first to avoid interference from earlier turns.
            try:
                container = page.locator("div.conversation-flow-answer-container").last
                if await container.count() > 0:
                    blocks = await container.locator(".cosd-markdown-content, .cosd-markdown, .ai-markdown").all()
                    parts: list[str] = []
                    seen: set[str] = set()
                    for b in blocks:
                        try:
                            t = (await b.inner_text()).strip()
                        except Exception:
                            continue
                        if t and t not in seen:
                            seen.add(t)
                            parts.append(t)
                    if parts:
                        answer = "\n\n".join(parts)
            except Exception:
                pass

            if not answer.strip():
                # Fallback: scan the whole page and concatenate all markdown blocks
                for sel in [
                    ".cosd-markdown-content",
                    ".cosd-markdown",
                    ".ai-markdown",
                    "div.conversation-flow-answer-container .markdown-body",
                    "div.conversation-flow-answer-container",
                    "#answer_text_id",
                    ".custom-html.md-stream",
                    "[class*='md-stream']",
                ]:
                    try:
                        els = await page.locator(sel).all()
                        if not els:
                            continue
                        parts = []
                        for e in els:
                            try:
                                t = (await e.inner_text()).strip()
                            except Exception:
                                continue
                            if t:
                                parts.append(t)
                        if parts:
                            answer = "\n\n".join(parts)
                            break
                    except Exception:
                        continue

            # Citations are folded in the "参考 N 个网页" ("N web pages referenced") chip; it must be clicked to expand before a[href] can be scraped
            await self._expand_references(page)

            # One-time dump of the rendered answer-container HTML - for debugging "only half the data",
            # written to /tmp/wenxin_dump.html (dumped only once; skipped if the file already exists)
            try:
                import os as _os
                dp = "/tmp/wenxin_dump.html"
                if not _os.path.exists(dp):
                    html = await page.evaluate("""() => {
                        const el = document.querySelector('div.conversation-flow-answer-container:last-child') ||
                                   document.querySelector('div.conversation-flow-answer-container');
                        return el ? el.outerHTML : document.body.innerHTML;
                    }""")
                    with open(dp, "w", encoding="utf-8") as f:
                        f.write(html)
                    sys.__stdout__.write(f"[Wenxin-probe] dumped answer HTML ({len(html)} bytes) -> {dp}\n")
                    sys.__stdout__.flush()
            except Exception as _e:
                sys.__stdout__.write(f"[Wenxin-probe] dump failed: {_e}\n")
                sys.__stdout__.flush()

            citations = await self._extract_citations(page, answer)
            reasoning = await extract_reasoning(page, "wenxin")

            await save_page_session("wenxin", ctx)

            video_path = None
            if want_record(self._record_video):
                try:
                    from ..video_store import get_video_path
                    video_path = await get_video_path(page)
                except Exception:
                    pass

            await _close_headed(ctx, headed_browser, headed_pw)

            return EngineResult(
                engine=self.name,
                query=query,
                answer=answer,
                citations=citations,
                video_path=video_path,
                reasoning=reasoning,
            )
        except Exception as e:
            if ctx is not None:
                try:
                    await _close_headed(ctx, headed_browser, headed_pw)
                except Exception:
                    pass
            return EngineResult(engine=self.name, query=query, error=str(e))

    async def _wait_streaming_done(self, page, max_wait: float = 30.0) -> None:
        """Actively poll from Python until the streaming indicator disappears.

        Why not page.wait_for_function: Playwright's wait_for_function schedules callbacks with
        raf (requestAnimationFrame). After wenxin streaming ends the browser goes
        idle and stops painting, raf no longer fires, and wait_for_function uses up the full 180s timeout.
        page.evaluate is a synchronous RPC that does not depend on raf, so it is stable.

        Streaming indicator (measured 2026-05-18):
          - [class*='stopBtn']         main stop button wrapper
          - [class*='stopDealBtn']     another variant of the stop button
          - [class*='name-msg-animation']  inline class of the "AI answering" animation
        All cnt=0 = streaming finished.
        """
        poll = 0.5
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                still = await page.evaluate(
                    """() => !!(
                        document.querySelector('[class*="stopBtn"]')
                     || document.querySelector('[class*="stopDealBtn"]')
                     || document.querySelector('[class*="name-msg-animation"]')
                    )"""
                )
            except Exception:
                still = False
            if not still:
                sys.__stdout__.write(
                    f"[Wenxin-streaming] indicator gone after {elapsed:.1f}s\n"
                )
                sys.__stdout__.flush()
                return
            await asyncio.sleep(poll)
            elapsed += poll
        sys.__stdout__.write(
            f"[Wenxin-streaming] indicator still present after {max_wait}s — extracting whatever's there\n"
        )
        sys.__stdout__.flush()

    async def _wait_text_stable(self, page, max_wait: float = 30.0, stable_secs: float = 3.0) -> None:
        """Poll the last answer container's inner_text until it stops growing.

        Belt-and-suspenders fallback: wait_for_function watches the `.cosd-markdown-content-typingall`
        DOM class, but wenxin occasionally toggles this class momentarily between two paragraphs - wait sees false,
        returns immediately, then the next paragraph starts streaming and extract misses it.
        Here inner_text length is polled again until it has not grown for stable_secs, confirming the whole stream is really done.
        """
        poll = 0.6
        last_len = -1
        stable_since = None
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                container = page.locator("div.conversation-flow-answer-container").last
                if await container.count() > 0:
                    cur = (await container.inner_text()).strip()
                else:
                    cur = ""
            except Exception:
                cur = ""

            cur_len = len(cur)
            if cur_len > 0 and cur_len == last_len:
                if stable_since is None:
                    stable_since = elapsed
                elif elapsed - stable_since >= stable_secs:
                    return
            else:
                last_len = cur_len
                stable_since = None

            await asyncio.sleep(poll)
            elapsed += poll

    async def _expand_references(self, page) -> None:
        """Click "参考 N 个网页" ("N web pages referenced") chip to expand the source drawer.

        chat.baidu.com renders reference chips with text matching "参考.*网页" ("referenced ... web pages").
        Multiple strategies to find and click the chip.
        """
        strategies = [
            # Strategy 1: Hashed class (may change)
            lambda: page.locator('div[class*="titleText"]', has_text=re.compile(r'参考')).first,
            # Strategy 2: Any element with "参考 N 个网页" ("N web pages referenced") text
            lambda: page.locator("text=参考").first,
            # Strategy 3: Source/cosource containers
            lambda: page.locator("[class*='source-caption'], [class*='cosc-source-caption']").first,
        ]

        for get_locator in strategies:
            try:
                el = get_locator()
                if not await el.is_visible(timeout=2000):
                    continue

                # Try clicking the element, then its parent
                try:
                    await el.click()
                except Exception:
                    try:
                        await el.locator("xpath=..").click()
                    except Exception:
                        continue

                await human_delay(0.8, 1.5)
                sys.__stdout__.write("[Wenxin-probe] reference chip clicked\n")
                sys.__stdout__.flush()
                return
            except Exception:
                continue

        sys.__stdout__.write("[Wenxin-probe] reference chip not found\n")
        sys.__stdout__.flush()

    async def _focus_input(self, page, input_el) -> None:
        """Click the input box and confirm it really got focus; if not, remove the onboarding layer and retry.

        2026-09-08 (local patch on the test host, only committed 2026-09-15): `_dismiss_popups` only runs once after goto,
        while Baidu's onboarding layer (`_task-mode-guide-dialog_*`) is **lazily rendered** - it attaches to the DOM only during the subsequent warm-up and
        _start_new_chat. So this click consistently hit `subtree intercepts pointer
        events`; after 30s of retries the upper layer recorded an empty answer, showing up as zero Wenxin answers for whole rounds.
        **The criterion is "does the input box have focus after the click", not "was there a popup when entering the page".**
        On 2026-09-15 a develop build without this section overwrote the test host, and Wenxin immediately went back to whole rounds of empty answers - this section
        used to live only on the machine; now it is in the repo.
        """
        for attempt in range(3):
            # Remove any lid already attached first (returns immediately when there is no popup)
            await self._dismiss_new_version_modal(page)
            try:
                box = await input_el.bounding_box()
                if box:
                    await human_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                else:
                    await input_el.click(timeout=8000)
            except Exception as e:
                if attempt == 2:
                    raise
                sys.__stdout__.write(
                    f"[Wenxin] 输入框点不动(第 {attempt + 1} 次): "
                    f"{str(e).splitlines()[0][:100]}\n")
                sys.__stdout__.flush()
                await human_delay(0.4, 0.9)
                continue
            try:
                focused = await page.evaluate(
                    """() => {
                        const a = document.activeElement;
                        return !!a && (a.tagName === 'TEXTAREA' || a.isContentEditable);
                    }"""
                )
            except Exception:
                focused = True   # if it cannot be determined, do not block the normal path
            if focused:
                return
            if attempt == 2:
                raise RuntimeError(
                    "input not focusable — 引导弹窗摘了 3 次仍盖住输入框")
            sys.__stdout__.write(
                f"[Wenxin] 点击落空,输入框没拿到焦点(第 {attempt + 1} 次),重摘引导层\n")
            sys.__stdout__.flush()
            await human_delay(0.4, 0.9)

    async def _dismiss_popups(self, page) -> None:
        for sel in [
            "text=我知道了",
            "text=同意",
            "text=Accept",
            "[aria-label='close']",
            ".close-btn",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await human_delay(0.3, 0.8)
            except Exception:
                continue
        # The onboarding popup is handled separately: the selectors above are generic "Got it/Agree",
        # and the onboarding popup covering the input box is not among them. The one-shot path only reaches here,
        # unlike the hot path that goes through _start_new_chat, so it must also be called here.
        await self._dismiss_new_version_modal(page)

    _BAIDU_BLOCK = ("baidu.com", "baidubce.com", "bdimg.com", "bdstatic.com")

    async def _extract_citations(self, page, answer: str) -> List[Citation]:
        citations: List[Citation] = []
        seen_keys: set[str] = set()

        def _add(cit: Citation | None) -> None:
            if cit is None:
                return
            key = cit.url or cit.domain
            if not key or key in seen_keys:
                return
            seen_keys.add(key)
            citations.append(
                Citation(
                    url=cit.url,
                    domain=cit.domain,
                    title=cit.title,
                    snippet=cit.snippet,
                    position=len(citations) + 1,
                )
            )

        # ── 1) Extract embedded JSON data: chat.baidu.com stores URLs in
        #    HTML-encoded JSON within the page data: &quot;linkTitle&quot;:&quot;...&quot;
        try:
            embedded = await page.evaluate(r"""() => {
                const html = document.body.innerHTML;
                const out = [];
                const seen = new Set();
                // Match: URL followed by linkTitle
                const re = /(https?:[^"&<>\\]{4,500})&quot;,&quot;linkTitle&quot;:&quot;([^&]*)/g;
                let m;
                while ((m = re.exec(html)) !== null) {
                    let url = m[1]
                        .replace(/&amp;/g, '&')
                        .replace(/\\u002F/g, '/');
                    let title = m[2]
                        .replace(/&amp;/g, '&')
                        .replace(/\\u002F/g, '/');
                    if (!url.startsWith('http')) continue;
                    if (seen.has(url)) continue;
                    seen.add(url);
                    out.push({url, title});
                }
                return out;
            }""") or []
            for item in embedded:
                url = item.get("url", "")
                title = item.get("title", "")
                if not url.startswith("http"):
                    continue
                if any(b in url for b in self._BAIDU_BLOCK):
                    continue
                _add(Citation.from_url(url, title=title))
        except Exception:
            pass

        # ── 2) DOM: cosc-source-a and other link selectors
        try:
            for sel in [
                ".cosc-source-a[href]",
                ".cosc-source a[href]",
                "[class*='source'] a[href]",
                "[class*='reference'] a[href]",
                ".cosd-markdown a[href]",
            ]:
                links = await page.locator(sel).all()
                if links:
                    for link in links:
                        try:
                            href = await link.get_attribute("href")
                            title = await link.inner_text()
                        except Exception:
                            continue
                        if (
                            href
                            and href.startswith("http")
                            and not any(b in href for b in self._BAIDU_BLOCK)
                        ):
                            _add(Citation.from_url(href, title=title))
        except Exception:
            pass

        # ── 3) Bare URLs in answer text
        for u in extract_urls_from_text(answer):
            if not any(b in u for b in self._BAIDU_BLOCK):
                _add(Citation.from_url(u))

        # Debug probe
        try:
            sys.__stdout__.write(
                f"[Wenxin-probe] citations={len(citations)}\n"
            )
            sys.__stdout__.flush()
        except Exception:
            pass

        return citations
