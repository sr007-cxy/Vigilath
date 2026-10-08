"""DeepSeek browser adapter — automates chat.deepseek.com.

DeepSeek's API has no web search, but the web UI does via the "智能搜索" ("Smart Search")
(Smart Search) toggle. This adapter uses Playwright to:
1. Dismiss cookie consent banner
2. Enable "智能搜索" ("Smart Search") for each query
3. Submit query, wait for answer
4. Extract answer text + citation links

Requires: a valid DeepSeek session (run scripts/deepseek_login.py first).
"""

from __future__ import annotations

import asyncio
import random
import re
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


class DeepSeekBrowserAdapter(EngineAdapter):
    name = "DeepSeek"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    CHAT_URL = "https://chat.deepseek.com/"

    async def is_available(self) -> bool:
        try:
            page, ctx = await create_stealth_page("deepseek")
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=15000)
            logged_in = await page.locator("textarea").count() > 0
            await ctx.close()
            return logged_in
        except Exception:
            return False

    # 2026-05-26: migrated to CloakBrowser (github.com/CloakHQ/CloakBrowser).
    # CloakBrowser patches chromium at the compile layer (49+ source-level patches), cleaner than playwright's
    # runtime CDP patch; human_click + simulate_browsing mouse trajectory simulation are enabled too,
    # hardening together with timing signals.
    async def _create_hot_page(self):
        """DeepSeek only: Xvfb + headed + cloakbrowser."""
        import os
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        page, ctx = await create_headed_page(
            "deepseek",
            profile=profile,
            record_video=want_record(self._record_video),
        )
        return page, ctx

    async def _close_hot_page(self, page, ctx) -> None:
        """DeepSeek only: close both the cloakbrowser ctx and the patchright fallback's browser/pw."""
        headed_browser = getattr(page, "_headed_browser", None)
        headed_pw = getattr(page, "_pw_ref", None)
        await _close_headed(ctx, headed_browser, headed_pw)

    # D3 (2026-05-18): extracted as a method for the hot browser EngineSession to call before each query,
    # resetting the conversation to prevent context bleed. The inline part of one-shot search()
    # is not duplicated - it directly does click("text=开启新对话") ("start new chat").
    async def _start_new_chat(self, page) -> None:
        """chat.deepseek.com measured (2026-05-18 DOM probe): button text = "开启新对话" ("start new chat");
        the class is a hashed CSS module (`_5a8ac7a`) and unstable, so text matching is most reliable.
        """
        candidates = [
            "text=开启新对话",
            "[role='button']:has-text('开启新对话')",
            ":text-is('开启新对话')",
            # Fallback if text changes back to "新对话" ("New chat"):
            "text=新对话",
            "button:has-text('新对话')",
            # 2026-06: the DS web app switched to an English UI and the button text became "New chat" (measured by DOM probe, account still logged in)
            ":text-is('New chat')",
            "[role='button']:has-text('New chat')",
            "text=New chat",
        ]
        import sys, time
        # Under cloakbrowser headed, DS's SPA renders slowly and the button takes a few seconds to appear, and the language varies with fingerprint/locale
        # (Chinese "开启新对话" / English "New chat"). Poll for up to ~18s and click whichever candidate is visible,
        # avoiding "checking too early -> misjudging a stale session".
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            for sel in candidates:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=800):
                        await btn.click()
                        await human_delay(0.5, 1.0)
                        sys.__stdout__.write(f"[DeepSeek-new-chat] clicked via {sel!r}\n")
                        sys.__stdout__.flush()
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.8)
        # Button not found within 18s -> distinguish two cases: 1) this session lost its login (shows the login page) -> report login_lost,
        # letting the account pool quarantine this bad session so it is not drawn again (self-healing); 2) really not rendered -> dom_not_found.
        login_page = False
        try:
            txt = (await page.inner_text("body"))[:1500]
            if any(kw in txt for kw in ("发送验证码", "密码登录", "微信扫码登录", "注册登录", "手机号登录")):
                login_page = True
        except Exception:
            pass
        sys.__stdout__.write(
            f"[DeepSeek-new-chat] button NOT found via any selector (18s) login_page={login_page}\n")
        sys.__stdout__.flush()
        if login_page:
            # Contains "login may have expired" -> _classify_error maps it to login_lost -> the pool quarantines this bad session
            raise RuntimeError(
                "deepseek login may have expired (login page shown, no new-chat button)"
            )
        raise RuntimeError(
            "deepseek new-chat button not found — refusing to send query into stale session"
        )

    # Answer body candidate selectors - after the DS UI redesign .ds-markdown is no longer stable,
    # so add a fallback layer. Ordered by decreasing specificity; the stability polling and final extract use the same set.
    _ANSWER_SELECTORS = (
        ".ds-markdown",
        ".markdown",
        "[class*='ds-markdown']",
        "[class*='markdown-body']",
        "[class*='message-content']",
        "[class*='msg-content']",
        "[class*='_response_']",
        "[class*='message'][class*='assistant']",
    )

    # D4 (2026-05-18): search() split into _prepare_page + _query_with_page,
    # so EngineSession can reuse the hot browser. search() itself also uses both, so
    # one-shot behavior is 100% unchanged.
    async def _prepare_page(self, page, ctx) -> None:
        """All "get ready" work before starting a query - goto + banner + Smart Search toggle etc.

        EngineSession.init() calls it once at worker startup; search() also calls it.
        _start_new_chat is not part of this step; it is called before each query to reset the conversation.
        """
        await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
        await human_delay(2, 3)
        await self._dismiss_cookie_banner(page)
        # Enable Smart Search - once; subsequent queries all use this toggle
        await self._enable_smart_search(page)

    async def _query_with_page(self, page, ctx, query: str) -> EngineResult:
        """Assuming the page has been through _prepare_page, run one query on it.

        EngineSession.query() calls _start_new_chat before each call, then this method.
        search() also takes this path, just wrapped in launch + close.
        """
        import sys
        # 1) Reset chat (fine if EngineSession already called _start_new_chat - idempotent)
        await self._start_new_chat(page)
        # 2) Warm-up - CloakBrowser handles fingerprint/CDP but not timing; simulate_browsing moves the mouse
        #    randomly over the page + small scrolls, consistent with "a real person glancing at the page before asking".
        try:
            await simulate_browsing(page, duration=random.uniform(2.0, 4.0))
        except Exception as e:
            sys.__stdout__.write(f"[DS-hot] simulate warning: {e}\n")
            sys.__stdout__.flush()
        # 3) Find the textarea
        try:
            textarea = page.locator("textarea").first
            await textarea.wait_for(state="visible", timeout=10000)
        except Exception:
            return EngineResult(
                engine=self.name, query=query,
                error="DeepSeek textarea not visible — login may have expired",
            )
        # 4) Human-style click + per-character keyboard.type with 60-180ms jitter (instead of fill; fill pours in the
        #    whole text at once, which alone is enough for anti-detection systems to flag a script).
        try:
            box = await textarea.bounding_box()
            if box:
                await human_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await textarea.click()
        except Exception:
            await textarea.click()
        await human_delay(0.8, 1.6)
        for ch in query:
            await page.keyboard.type(ch, delay=random.randint(60, 180))
        await human_delay(0.5, 1.2)
        await page.keyboard.press("Enter")

        # 3) Poll for a stable answer (same 150 x 1s as the original search())
        await asyncio.sleep(3)
        last_len = 0
        stable_count = 0
        matched_sel = None
        for _ in range(150):
            await asyncio.sleep(1)
            cur_text, cur_sel = await self._read_answer_text(page)
            cur_len = len(cur_text)
            if cur_len > 0 and cur_len == last_len:
                stable_count += 1
                if stable_count >= 5:
                    matched_sel = cur_sel
                    break
            else:
                last_len = cur_len
                stable_count = 0
                if cur_sel:
                    matched_sel = cur_sel
        sys.__stdout__.write(
            f"[DS-wait] final last_len={last_len} matched_sel={matched_sel!r}\n"
        )
        sys.__stdout__.flush()

        # 4) Extract
        answer, _ = await self._read_answer_text(page)
        if not answer.strip():
            hints = await self._diagnose_empty_answer(page)
            # The page explicitly says rate-limited/too frequent -> report rate_limited, do not let it fall into empty_answer.
            # empty_answer is cumulative (threshold 5 -> quarantine) and would scrap a good account that was only temporarily muted as a bad one;
            # rate_limited uses the cooldown policy (also cooling sibling accounts on the same egress IP). The pure HTTP path
            # (deepseek_http.py reads "user is muted" + mute_until) has done this all along; the browser
            # path previously only logged without reporting, a coverage gap between the two paths.
            rl = [k for k in ("太频繁", "rate limit", "繁忙", "请稍后") if k in (hints or {})]
            if rl:
                return EngineResult(
                    engine=self.name, query=query, answer="", citations=[],
                    error=f"rate limit (deepseek 风控限流: {'/'.join(rl)})",
                )
        citations = await self._extract_citations(page, answer)

        return EngineResult(
            engine=self.name,
            query=query,
            answer=answer,
            citations=citations,
        )

    async def search(self, query: str) -> EngineResult:
        """One-shot search:launch → prepare → query → save + close.

        EngineSession's hot path does not call this method. Both paths share _prepare_page +
        _query_with_page, so behavior is consistent.

        2026-05-26: one-shot also uses CloakBrowser, fully consistent with the hot path; the behavior seen when debugging
        is the behavior production runs.
        """
        import os
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
                "deepseek",
                profile=profile,
                record_video=want_record(self._record_video),
            )
            headed_browser = getattr(page, "_headed_browser", None)
            headed_pw = getattr(page, "_pw_ref", None)

            await self._prepare_page(page, ctx)
            result = await self._query_with_page(page, ctx, query)

            await save_page_session("deepseek", ctx)
            video_path = None
            if want_record(self._record_video):
                try:
                    from ..video_store import get_video_path
                    video_path = await get_video_path(page)
                except Exception:
                    pass
            await _close_headed(ctx, headed_browser, headed_pw)
            # video_path is only present in one-shot mode; EngineResult has no video_path field, so it is passed separately
            if video_path is not None:
                result.video_path = video_path
            return result
        except Exception as e:
            if ctx is not None:
                try:
                    await _close_headed(ctx, headed_browser, headed_pw)
                except Exception:
                    pass
            return EngineResult(engine=self.name, query=query, error=str(e))

    async def _read_answer_text(self, page) -> tuple[str, str | None]:
        """Try each candidate answer selector, return (text, winning_selector).

        Used by both the stability poll loop and the final extract step so they
        stay in lockstep — wait sees content; extract pulls the same content.
        """
        for sel in self._ANSWER_SELECTORS:
            try:
                els = await page.locator(sel).all()
                if not els:
                    continue
                text = await els[-1].inner_text()
                if text and text.strip():
                    return text, sel
            except Exception:
                continue
        return "", None

    async def _diagnose_empty_answer(self, page) -> dict:
        """Dump page-state probe when no answer was extracted; returns the matched keyword hints.

        Both a DS DOM change and session expiry produce empty answers, but the user only gets "—".
        Dumping the visible text / candidate selector hit counts once in the log lets ops tell at a glance from
        browser-service stdout whether it is selector drift or a lost login session.
        The caller also uses hints to distinguish "rate limited" from "truly empty answer" (the former should not quarantine the account as bad).
        """
        import sys
        try:
            probe = await page.evaluate(
                """(sels) => {
                    const counts = {};
                    for (const s of sels) {
                        try { counts[s] = document.querySelectorAll(s).length; }
                        catch (e) { counts[s] = 'err:' + e.message; }
                    }
                    const bodyLen = (document.body.innerText || '').length;
                    // Login wall / paywall / rate-limit hints
                    const text = (document.body.innerText || '').slice(0, 4000);
                    const hints = {};
                    for (const kw of ['登录', 'Sign in', 'Login', '验证', 'CAPTCHA',
                                      '太频繁', 'rate limit', '繁忙', '请稍后']) {
                        if (text.includes(kw)) hints[kw] = true;
                    }
                    return { counts, bodyLen, hints };
                }""",
                list(self._ANSWER_SELECTORS),
            )
            sys.__stdout__.write(
                f"[DS-empty-answer] selector_hits={probe['counts']} "
                f"body_len={probe['bodyLen']} hints={probe['hints']}\n"
            )
            sys.__stdout__.flush()
            return probe.get("hints") or {}
        except Exception as e:
            sys.__stdout__.write(f"[DS-empty-answer] probe failed: {e}\n")
            sys.__stdout__.flush()
            return {}

    async def _dismiss_cookie_banner(self, page) -> None:
        """Dismiss the cookie consent popup if visible."""
        try:
            for text in ["接受全部", "Accept All", "Accept"]:
                btn = page.locator(f"text={text}").first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await human_delay(0.5, 1)
                    return
        except Exception:
            pass

    async def _enable_smart_search(self, page) -> None:
        """Ensure '智能搜索' (Smart Search) toggle is ON.

        New DS button structure: <div class="... ds-toggle-button ...">...<span>智能搜索</span>...</div> ("Smart Search")
        The click handler is bound on the outer toggle div; the inner span does not necessarily trigger it. Locate the outer div and click it.
        """
        import sys
        try:
            btn = page.locator('div.ds-toggle-button', has_text='智能搜索').first
            if not await btn.is_visible(timeout=3000):
                sys.__stdout__.write("[DS-smart-search] toggle not visible\n")
                sys.__stdout__.flush()
                return

            before_cls = await btn.evaluate("el => el.className || ''")
            # Typical active-state tokens: active / selected / on / checked / primary
            already_on = any(tok in before_cls.lower() for tok in ('active', 'selected', '--on', 'checked', 'primary'))
            if already_on:
                sys.__stdout__.write(f"[DS-smart-search] already ON: cls={before_cls!r}\n")
                sys.__stdout__.flush()
                return

            await btn.click()
            await human_delay(0.5, 1.0)
            after_cls = await btn.evaluate("el => el.className || ''")
            sys.__stdout__.write(
                f"[DS-smart-search] clicked. before={before_cls!r} after={after_cls!r}\n"
            )
            sys.__stdout__.flush()
        except Exception as e:
            sys.__stdout__.write(f"[DS-smart-search] failed: {type(e).__name__}: {e}\n")
            sys.__stdout__.flush()

    async def _extract_citations(self, page, answer: str) -> List[Citation]:
        """Extract citation links from the DeepSeek response."""
        import sys
        citations = []

        # ── Temporary probe: watch the current state of the "智能搜索" ("Smart Search") button + dump body HTML ──
        try:
            probe = await page.evaluate("""() => {
                // 所有短文本按钮,看搜索 toggle 现在叫什么
                const buttons = [];
                document.querySelectorAll('button, [role="button"], div[tabindex]').forEach(el => {
                    const text = (el.innerText || '').trim();
                    if (text && text.length <= 20) {
                        buttons.push({
                            tag: el.tagName,
                            text: text,
                            cls: (typeof el.className === 'string' ? el.className : '').slice(0, 100),
                            aria_pressed: el.getAttribute('aria-pressed'),
                            data_active: el.getAttribute('data-active'),
                        });
                    }
                });
                // 关键词出现次数(判断搜索是否真正执行了)
                const body_text = (document.body.innerText || '');
                const keywords = {};
                ['智能搜索', '联网搜索', '深度思考', '已搜索', '来源', '参考资料', '引用', 'Search', 'Source', 'Reference'].forEach(kw => {
                    keywords[kw] = (body_text.match(new RegExp(kw, 'gi')) || []).length;
                });
                // iframe / shadow root 检测
                const iframes = document.querySelectorAll('iframe').length;
                let shadowRoots = 0;
                document.querySelectorAll('*').forEach(el => { if (el.shadowRoot) shadowRoots++; });
                return { buttons: buttons.slice(0, 30), keywords, iframes, shadowRoots };
            }""")
            sys.__stdout__.write(f"[DS-probe] iframes={probe['iframes']} shadowRoots={probe['shadowRoots']}\n")
            sys.__stdout__.write(f"[DS-probe] keywords={probe['keywords']}\n")
            sys.__stdout__.write(f"[DS-probe] buttons (first 30):\n")
            for b in probe['buttons']:
                sys.__stdout__.write(f"  {b}\n")
            sys.__stdout__.flush()
        except Exception as _e:
            sys.__stdout__.write(f"[DS-probe] probe failed: {type(_e).__name__}: {_e}\n")
            sys.__stdout__.flush()

        # Dump body HTML for offline analysis only the first time no citation is captured
        try:
            import os
            dump_path = "/tmp/deepseek_q1_body.html"
            if not os.path.exists(dump_path):
                body_html = await page.evaluate("() => document.body.innerHTML")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(body_html)
                sys.__stdout__.write(f"[DS-probe] dumped body HTML ({len(body_html)} bytes) -> {dump_path}\n")
                sys.__stdout__.flush()
        except Exception as _e:
            sys.__stdout__.write(f"[DS-probe] dump failed: {type(_e).__name__}: {_e}\n")
            sys.__stdout__.flush()

        # Try to find citation links in the answer area (updated selectors)
        try:
            link_selectors = [f"{s} a[href]" for s in self._ANSWER_SELECTORS]
            for sel in link_selectors:
                links = await page.locator(sel).all()
                if links:
                    seen = set()
                    for i, link in enumerate(links):
                        href = await link.get_attribute("href")
                        title = await link.inner_text()
                        if (
                            href
                            and href.startswith("http")
                            and "deepseek.com" not in href
                            and href not in seen
                        ):
                            seen.add(href)
                            citations.append(
                                Citation.from_url(href, title=title, position=i + 1)
                            )
                    if citations:
                        break
        except Exception:
            pass

        # Also try to find source reference cards/panels
        try:
            source_cards = await page.locator("[class*=search-result] a[href], [class*=source] a[href], [class*=citation] a[href]").all()
            seen = {c.url for c in citations}
            for i, card in enumerate(source_cards):
                href = await card.get_attribute("href")
                title = await card.inner_text()
                if href and href.startswith("http") and href not in seen:
                    seen.add(href)
                    citations.append(
                        Citation.from_url(href, title=title, position=len(citations) + 1)
                    )
        except Exception:
            pass

        # Fallback: extract URLs from answer text
        if not citations:
            urls = extract_urls_from_text(answer)
            citations = [Citation.from_url(u, position=i + 1) for i, u in enumerate(urls)]

        return citations
