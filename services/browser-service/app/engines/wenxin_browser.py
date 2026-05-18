"""Wenxin (文心一言) browser adapter — automates chat.baidu.com.

chat.baidu.com 是百度 AI 搜索,带 web 引用的版本。匿名也可用,回答里
会列出参考来源。
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import List

from ..browser import create_stealth_page, human_delay, save_page_session
from .base import Citation, EngineAdapter, EngineResult, extract_urls_from_text


class WenxinBrowserAdapter(EngineAdapter):
    name = "文心一言"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    # D3(2026-05-18):hot browser 用,每次 query 前 reset 对话.真实 DOM 待补 —
    # 等用户上传 wenxin session 后在 vm03 跑 DOM probe.目前是 best-guess selector
    # + 兜底 text 匹配,找不到时 [Wenxin-new-chat] 日志会告诉你.
    async def _start_new_chat(self, page) -> None:
        candidates = [
            "button:has-text('新对话')",
            "button:has-text('新建对话')",
            "[role='button']:has-text('新对话')",
            "[aria-label*='新对话']",
            "[aria-label*='New chat']",
            "a:has-text('新对话')",
            "text=新对话",
        ]
        import sys
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
        sys.__stdout__.write("[Wenxin-new-chat] button NOT found — needs DOM probe\n")
        sys.__stdout__.flush()

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

    async def search(self, query: str) -> EngineResult:
        try:
            page, ctx = await create_stealth_page("wenxin", record_video=self._record_video)
            # chat.baidu.com 起一个 302 → 主站,domcontentloaded 触发慢(尤其 vm03 网络抖),
            # 30s 经常 timeout。拉到 60s + 失败 fallback 用 load(更早触发)。
            try:
                await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                try:
                    await page.goto(self.CHAT_URL, wait_until="load", timeout=60000)
                except Exception as e:
                    await ctx.close()
                    return EngineResult(engine=self.name, query=query, error=f"goto failed: {e}")
            await human_delay(2, 3)

            await self._dismiss_popups(page)

            input_el = page.locator("textarea, [contenteditable='true']").first
            try:
                await input_el.wait_for(state="visible", timeout=10000)
            except Exception:
                await ctx.close()
                return EngineResult(engine=self.name, query=query, error="input not visible")

            await input_el.click()
            try:
                await input_el.fill(query)
            except Exception:
                await input_el.type(query)
            await human_delay(0.5, 1.0)

            await page.keyboard.press("Enter")

            # Wait for streaming to finish using stability detection.
            # chat.baidu.com uses `.cosd-markdown-content-typingall` during streaming
            # and removes it when done。
            #
            # ⚠ 之前 60s timeout 对复杂 query(FDI/反垄断律师推荐这种 1500+字)
            # 不够,wait 提前 timeout 退出后 extract 拿到的是流式中间态(~600 字)。
            # 实测 1.5K 字 query 需 ~90-120s。timeout 拉到 180s + 加 stability double
            # check(typing 消失后再 poll inner_text 稳定 3s 才返回),跟 qwen 同款。
            await human_delay(3, 5)
            try:
                await page.wait_for_function(
                    """() => !document.querySelector('.cosd-markdown-content-typingall')
                           && !document.querySelector('.cosd-markdown-loading')
                           && !document.querySelector('[data-auto-test="stop_response"]')""",
                    timeout=180000,
                )
            except Exception:
                sys.__stdout__.write("[Wenxin-wait] typing indicator wait timed out (180s) — extracting whatever's there\n")
                sys.__stdout__.flush()

            # 二次确认:typing 消失后再 poll answer container inner_text 稳定 3 秒,
            # 防止 typing class 被瞬间 toggle 误判完成。max 30s 兜底。
            await self._wait_text_stable(page, max_wait=30, stable_secs=3.0)

            # Extra settle time for final markdown render
            await human_delay(1.0, 2.0)

            answer = ""
            # chat.baidu.com 把"正文 + follow-up CTA"渲染成两个独立的
            # .cosd-markdown-content 块,所以要拼接而不是只取 [-1]。
            # 先锁定到最后一个 answer 容器,避免历史 turn 干扰。
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
                # Fallback: 整页扫,拼所有 markdown 块
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

            # citation 折叠在 "参考 N 个网页" chip 里,需点击展开才能抓 a[href]
            await self._expand_references(page)

            # 一次性 dump 渲染后 answer-container 的 HTML — 排查 "只有半个数据" 用,
            # 落盘到 /tmp/wenxin_dump.html(只 dump 一次,文件已存在就跳过)
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

            await save_page_session("wenxin", ctx)

            video_path = None
            if self._record_video:
                try:
                    from ..video_store import get_video_path
                    video_path = await get_video_path(page)
                except Exception:
                    pass

            await ctx.close()

            return EngineResult(
                engine=self.name,
                query=query,
                answer=answer,
                citations=citations,
                video_path=video_path,
            )
        except Exception as e:
            return EngineResult(engine=self.name, query=query, error=str(e))

    async def _wait_text_stable(self, page, max_wait: float = 30.0, stable_secs: float = 3.0) -> None:
        """Poll the last answer container's inner_text until it stops growing.

        Belt-and-suspenders 兜底:wait_for_function 看的是 `.cosd-markdown-content-typingall`
        DOM class,但 wenxin 偶尔在两个段落之间瞬间 toggle 这个 class —— wait 看到 false
        立刻 return,然后下个段落开 stream,extract 就拿不到。
        这里再 poll inner_text 长度,直到 stable_secs 没增长,确认整个流真的完了。
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
        """Click "参考 N 个网页" chip to expand the source drawer.

        chat.baidu.com renders reference chips with text matching "参考.*网页".
        Multiple strategies to find and click the chip.
        """
        strategies = [
            # Strategy 1: Hashed class (may change)
            lambda: page.locator('div[class*="titleText"]', has_text=re.compile(r'参考')).first,
            # Strategy 2: Any element with "参考 N 个网页" text
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
