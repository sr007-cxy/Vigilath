"""DeepSeek browser adapter — automates chat.deepseek.com.

DeepSeek's API has no web search, but the web UI does via the "智能搜索"
(Smart Search) toggle. This adapter uses Playwright to:
1. Dismiss cookie consent banner
2. Enable "智能搜索" for each query
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
from .base import Citation, EngineAdapter, EngineResult, extract_urls_from_text


async def _close_headed(ctx, browser=None, pw=None) -> None:
    """Close headed browser context, browser, and playwright instance.

    CloakBrowser 路径下 browser / pw 都是 None,只 close ctx;patchright
    fallback 路径下要按顺序 close ctx → browser → pw.stop()。
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

    # 2026-05-26:迁到 CloakBrowser(github.com/CloakHQ/CloakBrowser)。
    # CloakBrowser 在编译层 patch chromium(49+ 源码级 patch),比 playwright
    # 的 runtime CDP patch 干净;一并启用 human_click + simulate_browsing 的
    # 鼠标轨迹模拟,跟时序信号一起加固。
    async def _create_hot_page(self):
        """DeepSeek 专用:Xvfb + headed + cloakbrowser。"""
        import os
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        page, ctx = await create_headed_page(
            "deepseek",
            profile=profile,
            record_video=self._record_video,
        )
        return page, ctx

    async def _close_hot_page(self, page, ctx) -> None:
        """DeepSeek 专用:cloakbrowser 的 ctx + patchright fallback 的 browser/pw 都 close."""
        headed_browser = getattr(page, "_headed_browser", None)
        headed_pw = getattr(page, "_pw_ref", None)
        await _close_headed(ctx, headed_browser, headed_pw)

    # D3(2026-05-18):提取出 method 供 hot browser EngineSession 在每次 query
    # 前调用,重置 conversation 防止上下文串扰。one-shot search() 走 inline 那段
    # 不重复 — 那段直接 click("text=开启新对话")。
    async def _start_new_chat(self, page) -> None:
        """chat.deepseek.com 实测(2026-05-18 DOM probe):按钮文本 = "开启新对话",
        class 是 hashed CSS module(`_5a8ac7a`)不稳,所以走 text 匹配最靠谱.
        """
        candidates = [
            "text=开启新对话",
            "[role='button']:has-text('开启新对话')",
            ":text-is('开启新对话')",
            # Fallback if text changes back to "新对话":
            "text=新对话",
            "button:has-text('新对话')",
        ]
        import sys
        for sel in candidates:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await human_delay(0.5, 1.0)
                    sys.__stdout__.write(f"[DeepSeek-new-chat] clicked via {sel!r}\n")
                    sys.__stdout__.flush()
                    return
            except Exception:
                continue
        sys.__stdout__.write("[DeepSeek-new-chat] button NOT found via any selector\n")
        sys.__stdout__.flush()
        # raise 让 EngineSession 归 error,runner 不抽竞品,避免旧会话上下文污染数据
        raise RuntimeError(
            "deepseek new-chat button not found — refusing to send query into stale session"
        )

    # 答案正文候选 selector — DS UI 改版后 .ds-markdown 不再稳定,
    # 加一层 fallback。顺序按特异性递减,稳定性轮询和 final extract 都用同一组。
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

    # D4(2026-05-18):search() 拆成 _prepare_page + _query_with_page,
    # 让 EngineSession 能复用 hot browser.search() 自己也用这俩,所以
    # one-shot 行为 100% 不变.
    async def _prepare_page(self, page, ctx) -> None:
        """开始 query 前的所有"准备好"工作 — goto + banner + 智能搜索 toggle 等.

        EngineSession.init() 在 worker 启动时调一次;search() 也调.
        _start_new_chat 不在这一步,留给每次 query 前调用以重置 conversation.
        """
        await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
        await human_delay(2, 3)
        await self._dismiss_cookie_banner(page)
        # 启用智能搜索 — 一次性,后续 query 都用这个 toggle
        await self._enable_smart_search(page)

    async def _query_with_page(self, page, ctx, query: str) -> EngineResult:
        """假设 page 已 _prepare_page 过,在该 page 上跑一次 query.

        EngineSession.query() 在每次调用前先调 _start_new_chat,再调本方法.
        search() 也走这条路径,只是它在外面包了 launch + close.
        """
        import sys
        # 1) 重置 chat(EngineSession 已经调过 _start_new_chat 也无所谓 — idempotent)
        await self._start_new_chat(page)
        # 2) 暖身 — CloakBrowser 治指纹/CDP,治不了时序;simulate_browsing 让鼠标在
        #    页面上随机游走 + 微 scroll,跟"真人发问前先看一眼页面"一致。
        try:
            await simulate_browsing(page, duration=random.uniform(2.0, 4.0))
        except Exception as e:
            sys.__stdout__.write(f"[DS-hot] simulate warning: {e}\n")
            sys.__stdout__.flush()
        # 3) 找 textarea
        try:
            textarea = page.locator("textarea").first
            await textarea.wait_for(state="visible", timeout=10000)
        except Exception:
            return EngineResult(
                engine=self.name, query=query,
                error="DeepSeek textarea not visible — login may have expired",
            )
        # 4) 人类样式点击 + 逐字 keyboard.type 抖动 60-180ms(替代 fill,fill 是
        #    一次性灌全文,反检测系统单看这一项就足以认定脚本)。
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

        # 3) 轮询稳定答案(原 search() 同款 150 × 1s)
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
            await self._diagnose_empty_answer(page)
        citations = await self._extract_citations(page, answer)

        return EngineResult(
            engine=self.name,
            query=query,
            answer=answer,
            citations=citations,
        )

    async def search(self, query: str) -> EngineResult:
        """One-shot search:launch → prepare → query → save + close.

        EngineSession 走 hot 路径不调本方法.两条路径共用 _prepare_page +
        _query_with_page,行为一致.

        2026-05-26:one-shot 也走 CloakBrowser,跟 hot 路径完全一致,debug 看到的
        行为就是 production 跑的行为。
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
                record_video=self._record_video,
            )
            headed_browser = getattr(page, "_headed_browser", None)
            headed_pw = getattr(page, "_pw_ref", None)

            await self._prepare_page(page, ctx)
            result = await self._query_with_page(page, ctx, query)

            await save_page_session("deepseek", ctx)
            video_path = None
            if self._record_video:
                try:
                    from ..video_store import get_video_path
                    video_path = await get_video_path(page)
                except Exception:
                    pass
            await _close_headed(ctx, headed_browser, headed_pw)
            # video_path 仅 one-shot 模式带,EngineResult 上没有 video_path 字段单独传
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

    async def _diagnose_empty_answer(self, page) -> None:
        """Dump page-state probe when no answer was extracted.

        DS 改 DOM 或 session 过期都会出现空答案,但用户拿到的只是 "—"。
        在日志里 dump 一次 visible text / 候选 selector 命中数,运维能从
        browser-service stdout 一眼看出是 selector 漂移还是登录态丢了。
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
        except Exception as e:
            sys.__stdout__.write(f"[DS-empty-answer] probe failed: {e}\n")
            sys.__stdout__.flush()

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

        DS 新版按钮结构:<div class="... ds-toggle-button ...">...<span>智能搜索</span>...</div>
        点击事件绑在外层 toggle div,内层 span 不一定触发。定位外层 div 再点。
        """
        import sys
        try:
            btn = page.locator('div.ds-toggle-button', has_text='智能搜索').first
            if not await btn.is_visible(timeout=3000):
                sys.__stdout__.write("[DS-smart-search] toggle not visible\n")
                sys.__stdout__.flush()
                return

            before_cls = await btn.evaluate("el => el.className || ''")
            # 激活态典型 token:active / selected / on / checked / primary
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

        # ── 临时探测:关注"智能搜索"按钮当前状态 + dump body HTML ──
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

        # 只在第一次抓不到 citation 时 dump body HTML 给离线分析
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
