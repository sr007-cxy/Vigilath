"""Qwen (Tongyi Qianwen) browser adapter — automates www.qianwen.com.

Migrated on 2026-05-18 from chat.qwen.ai -> www.qianwen.com:
- chat.qwen.ai is Alibaba's international version, supports email login
- www.qianwen.com is Qianwen's main CN site, phone number login, matching the domain of the user's browser extension

Migration strategy: DOM selectors use a "generic + fallback chain" without hard-coding tongyi-specific classes -
the generic pattern of React Ant Design style chat apps:
  - Input box: textarea / contenteditable with a chat-related placeholder
  - Submit: Enter (Shift+Enter for newline)
  - Completion signal: text unchanged for N consecutive seconds AND no visible "停止/Stop" button
  - Answer container: the message bubble after the last user message

Once things stabilize, target tongyi-specific selectors to reduce false positives.
If the first probe fails, search the `[Qwen-probe]` logs, which print stage / count / selector hit info.
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


_QWEN_BLOCK_HOSTS = (
    # Own domains, citations not collected
    "qwen.ai", "qianwen.com", "tongyi.aliyun.com", "aliyun.com",
    "alibabacloud.com", "alicdn.com", "alipay.com", "taobao.com", "tmall.com",
    # Generic tracking/CDN
    "w3.org", "googletagmanager.com", "google-analytics.com",
    "googlesyndication.com", "doubleclick.net", "facebook.net", "analytics",
)

# Read the answer container's innerText immediately (without waiting for element stability); candidates take the last one of the first selector with text, in order.
# locator.inner_text() times out waiting for stability on elements that keep re-rendering during streaming, so evaluate is used instead.
_QWEN_READ_JS = r"""
(sels) => {
  for (const sel of sels) {
    const els = document.querySelectorAll(sel);
    if (els.length) {
      const t = els[els.length - 1].innerText || '';
      if (t.trim()) return { sel, text: t };
    }
  }
  return { sel: null, text: '' };
}
"""

# Chinese source display name -> actual domain (kept as before, engine-agnostic)
_CN_DISPLAY_TO_DOMAIN = {
    "百度": "baidu.com",
    "百度百科": "baike.baidu.com",
    "百度知道": "zhidao.baidu.com",
    "百度文库": "wenku.baidu.com",
    "百家号": "baijiahao.baidu.com",
    "搜狐": "sohu.com",
    "新浪": "sina.com.cn",
    "新浪财经": "finance.sina.com.cn",
    "新浪科技": "tech.sina.com.cn",
    "网易": "163.com",
    "网易新闻": "news.163.com",
    "腾讯": "qq.com",
    "腾讯新闻": "news.qq.com",
    "知乎": "zhihu.com",
    "微博": "weibo.com",
    "今日头条": "toutiao.com",
    "澎湃新闻": "thepaper.cn",
    "界面新闻": "jiemian.com",
    "第一财经": "yicai.com",
    "财联社": "cls.cn",
    "36氪": "36kr.com",
    "虎嗅": "huxiu.com",
    "钛媒体": "tmtpost.com",
    "人民网": "people.com.cn",
    "人民日报": "people.com.cn",
    "新华网": "news.cn",
    "央视网": "cctv.com",
    "中国新闻网": "chinanews.com.cn",
    "环球网": "huanqiu.com",
    "光明网": "gmw.cn",
    "四川在线": "scol.com.cn",
}


def _log(msg: str) -> None:
    sys.__stdout__.write(f"[Qwen-probe] {msg}\n")
    sys.__stdout__.flush()


def looks_refused(answer: str, citations=None) -> bool:
    """Tongyi's fixed refusal: 「你好,我无法回答这个问题,我们换一个话题聊聊吧。」 ("Hello, I can't answer this question, let's talk about something else.") (24 chars, zero sources).

    Shared by the DOM subprocess path (qwen_query.py) and the hot HTTP path (qwen_hot_http.py).
    It is an **intentional duplicate implementation** of telemetry-service dispatch._looks_refused (the two services are deployed separately
    and cannot import each other) - change the wording on both sides together. Answers with sources never count: a body discussing this sentence must not be misjudged.
    """
    a = (answer or "").strip()
    if not a or citations:
        return False
    return len(a) <= 60 and ("无法回答这个问题" in a or "换一个话题" in a
                             or "我不能回答这个问题" in a)


class QwenBrowserAdapter(EngineAdapter):
    name = "通义千问"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    # www.qianwen.com entry - goes to the chat page automatically after login; no hard-coded suffix, let the site redirect itself.
    CHAT_URL = "https://www.qianwen.com/"

    # Input box selector candidate chain (in hit priority order).
    # Modern React chat apps use either a textarea or a contenteditable div.
    INPUT_CANDIDATES = (
        "textarea[placeholder*='提问']",
        "textarea[placeholder*='输入']",
        "textarea[placeholder*='对话']",
        "textarea[placeholder*='Message']",
        "div[contenteditable='true'][role='textbox']",
        "div[contenteditable='true']",
        "textarea",
    )

    # Answer container candidate chain - grabs "the last assistant message".
    # Real DOM measured on www.qianwen.com (2026-05-18):
    #   .chat-answers-card-wrap (data-chat-answers-wrap=...) is the stable shell of a single answer,
    #   containing only the assistant answer, not the user question. .answer-common-card is a narrower inner layer of the same item.
    ASSISTANT_CANDIDATES = (
        "[data-chat-answers-wrap]",
        ".chat-answers-card-wrap",
        ".answer-common-card",
        # Generic fallback (if redesigned later, one of these will most likely still match)
        "[data-message-role='assistant']",
        "[data-role='assistant']",
        "[class*='assistant-message']",
    )

    # The "停止生成" ("Stop generating") button during streaming - if present, generation is not finished.
    STOP_BUTTON_CANDIDATES = (
        "button:has-text('停止')",
        "button:has-text('Stop')",
        "[aria-label*='停止']",
        "[aria-label*='Stop']",
        "button[class*='stop']",
    )

    async def is_available(self) -> bool:
        try:
            page, ctx = await create_stealth_page("qwen")
            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=15000)
            input_el = await self._find_input(page, timeout=8000)
            await ctx.close()
            return input_el is not None
        except Exception:
            return False

    # 2026-05-26: migrated to CloakBrowser (github.com/CloakHQ/CloakBrowser).
    # Alibaba's chat has fingerprint/CDP detection like other Chinese vendors; a unified stack makes later troubleshooting easier.
    async def _create_hot_page(self):
        """Tongyi only: Xvfb + headed + cloakbrowser."""
        import os
        if not os.environ.get("DISPLAY"):
            from ..xvfb import start_xvfb
            start_xvfb()
        profile = _pick_profile(platform_filter="Linux x86_64")
        page, ctx = await create_headed_page(
            "qwen",
            profile=profile,
            record_video=want_record(self._record_video),
        )
        return page, ctx

    async def _close_hot_page(self, page, ctx) -> None:
        """Tongyi only: close both the cloakbrowser ctx and the patchright fallback's browser/pw."""
        headed_browser = getattr(page, "_headed_browser", None)
        headed_pw = getattr(page, "_pw_ref", None)
        await _close_headed(ctx, headed_browser, headed_pw)

    # D4d (2026-05-18): hot browser protocol - EngineSession reuses the page via _prepare_page +
    # _query_with_page, sharing helpers with search().
    async def _prepare_page(self, page, ctx) -> None:
        """Hot init: goto + popups + verify the input is visible (otherwise treated as login loss).

        Deliberately no network capture (qianwen's chat API contains nav/analytics URLs
        that would pollute citations, see the v2 fix).
        """
        await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
        await human_delay(2, 3)
        _log(f"prepare_page landed at {page.url}")
        await self._dismiss_popups(page)
        input_el = await self._find_input(page, timeout=12000)
        if input_el is None:
            raise RuntimeError("input not visible after prepare — session likely expired")

    async def _query_with_page(self, page, ctx, query: str) -> EngineResult:
        """Hot query:reset → warm-up → input → submit → wait → extract.

        2026-05-26: the CloakBrowser path adds simulate_browsing warm-up + human_click focus +
        per-character keyboard.type (60-180ms jitter) - Alibaba risk control watches timing, and fill / insert_text
        pouring in the whole text at once are bot signatures.
        """
        await self._start_new_chat(page)

        try:
            await simulate_browsing(page, duration=random.uniform(2.0, 4.0))
        except Exception as e:
            _log(f"simulate warning: {e}")

        # Redo _find_input every time (the DOM may briefly disappear after reset)
        input_el = await self._find_input(page, timeout=8000)
        if input_el is None:
            return EngineResult(engine=self.name, query=query, error="input not visible (tongyi DOM)")
        try:
            box = await input_el.bounding_box()
            if box:
                await human_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await input_el.click()
        except Exception:
            await input_el.click()
        await human_delay(0.8, 1.6)
        for ch in query:
            await page.keyboard.type(ch, delay=random.randint(60, 180))
        # Verify the input really went into the contenteditable - in the new qwen, per-character typing often does not land ->
        # empty content submitted -> no answer (the real cause of last_len=0 in tests). If not, fall back to fill().
        if not await self._composer_has(input_el, query):
            try:
                await input_el.fill(query)
                _log("input via fill() fallback — char-type 未进")
            except Exception:  # noqa: BLE001
                try:
                    await page.keyboard.insert_text(query)
                except Exception:  # noqa: BLE001
                    pass
            # ★ Must verify again after the fallback. fill()/insert_text failures used to be silent,
            #   so it carried on -> submitted empty content -> waited 180s for nothing -> reported empty_answer,
            #   and the center saw "engine did not answer" while the real cause (the text never entered the box) was swallowed.
            if not await self._composer_has(input_el, query):
                _log("input still empty after fill fallback — abort before submit")
                return EngineResult(
                    engine=self.name, query=query,
                    error="input not filled (composer selector changed?)")
        await human_delay(0.5, 1.2)

        # Submit: in the new www.qianwen.com contenteditable, Enter no longer submits (only inserts a newline);
        # must click the "发送消息" ("Send message") button (aria-label contains "发送" ("Send")).
        #
        # ★ 2026-09-09: this used to be "clicking the button counts as submitted; if it cannot be clicked, fall back to Enter" -
        #   while the comment just above says Enter does not submit in the new version. On physical devices qwen had in the past week
        #   630 empty_answer, success rate dropping from 87% to 38%, failures evenly spread across every hour,
        #   the same account in the same hour both succeeding and failing - not a redesign and not dead accounts, but this **never verified** submit path:
        #   if the button was not visible within 1.5s (in React the button only enables after the text registers) it used Enter,
        #   Enter only inserts a newline -> the question stays in the input box -> waits the full 180s -> empty_answer.
        #   Changed to: verify "it was really submitted" for every submit method; if nothing submitted after trying all, return immediately,
        #   no longer burning 180s and no longer disguising a submit failure as an empty engine answer.
        submitted = await self._submit_and_confirm(page, input_el, query)
        if not submitted:
            _log("submit not confirmed by any strategy — abort")
            return EngineResult(
                engine=self.name, query=query,
                error="submit not confirmed (send button not found or click had no effect)")

        await human_delay(2, 3)
        assistant_sel = await self._wait_for_stable_answer(
            page, max_wait=180, stable_secs=2.5
        )
        await human_delay(1.0, 2.0)

        answer = await self._extract_answer(page, assistant_sel)
        citations = await self._extract_citations(page, answer, assistant_sel)
        reasoning = await extract_reasoning(page, "qwen")
        _log(f"hot query: ans_len={len(answer)} cite={len(citations)} sel={assistant_sel!r} "
             f"reasoning={'-' if reasoning is None else len(reasoning)}")
        return EngineResult(engine=self.name, query=query, answer=answer, citations=citations,
                            reasoning=reasoning)

    async def search(self, query: str) -> EngineResult:
        """One-shot:launch → _prepare_page → _query_with_page → save + close.

        Shares the hot protocol methods, zero behavioral regression.
        2026-05-26: one-shot also uses CloakBrowser, fully consistent with the hot path.
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
                "qwen",
                profile=profile,
                record_video=want_record(self._record_video),
            )
            headed_browser = getattr(page, "_headed_browser", None)
            headed_pw = getattr(page, "_pw_ref", None)

            await self._prepare_page(page, ctx)
            result = await self._query_with_page(page, ctx, query)

            await save_page_session("qwen", ctx)

            if want_record(self._record_video):
                try:
                    from ..video_store import get_video_path
                    result.video_path = await get_video_path(page)
                except Exception:
                    pass

            await _close_headed(ctx, headed_browser, headed_pw)
            return result
        except Exception as e:
            _log(f"search crashed: {type(e).__name__}: {e}")
            if ctx is not None:
                try:
                    await _close_headed(ctx, headed_browser, headed_pw)
                except Exception:
                    pass
            return EngineResult(engine=self.name, query=query, error=str(e))

    # Submit button candidates. The first two are aria-labels (used by the new www.qianwen.com); the last two are fallbacks for redesigns.
    SEND_BUTTON_CANDIDATES = (
        "button[aria-label*='发送']",
        "button[aria-label*='Send']",
        "button[class*='send']",
        "button[data-testid*='send']",
    )

    async def _composer_text(self, input_el) -> str:
        """Read the input box's current text - innerText for contenteditable, value for textarea.

        ★ Reading only inner_text is not enough: a <textarea>'s content is in value and innerText is always empty,
          so "the text did not go in" and "this is a textarea" look exactly the same. Read both and take the non-empty one.
        """
        for reader in ("inner_text", "input_value"):
            try:
                fn = getattr(input_el, reader)
                txt = (await fn(timeout=2000) or "").strip()
                if txt:
                    return txt
            except Exception:  # noqa: BLE001
                continue
        return ""

    async def _composer_has(self, input_el, query: str) -> bool:
        """The input box really contains this query (judged by the first 6 chars, same as the original logic)."""
        txt = await self._composer_text(input_el)
        return bool(txt) and query[:6] in txt

    async def _confirm_submitted(self, page, input_el, query: str,
                                 before_answer_len: int, timeout: float = 8.0) -> bool:
        """Poll for evidence that "the question was really sent"; any one holding counts as a successful submit.

        One of three (disjunction, not conjunction) - accept any page reaction to the submit:
          A. The input box was cleared (qwen clears the composer after submitting, most direct)
          B. The "停止生成" ("Stop generating") button appeared (already streaming the answer)
          C. The answer area text got longer than before submitting (the answer started rendering)
        Any one is enough. Disjunction is used so that a UI version that does not clear the input box is not misjudged as a failure -
        misjudging as failure throws away a good request, which is worse than missing one.
        """
        elapsed = 0.0
        step = 0.4
        while elapsed < timeout:
            # A. Composer cleared
            try:
                if not await self._composer_has(input_el, query):
                    return True
            except Exception:  # noqa: BLE001
                # The input box disappearing from the DOM entirely also means submitted (the new version rebuilds the composer after submit)
                return True
            # B. Stop button
            for sb in self.STOP_BUTTON_CANDIDATES:
                try:
                    if await page.locator(sb).first.is_visible(timeout=150):
                        return True
                except Exception:
                    continue
            # C. Answer area grew
            try:
                r = await page.evaluate(_QWEN_READ_JS, list(self.ASSISTANT_CANDIDATES))
                if len((((r or {}).get("text")) or "").strip()) > before_answer_len:
                    return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(step)
            elapsed += step
        return False

    async def _submit_and_confirm(self, page, input_el, query: str) -> bool:
        """Try "send button -> Enter -> Ctrl+Enter" in order, verifying each time whether it really submitted.

        A failed method no longer silently carries on - if all fail, return False and the caller returns an error immediately,
        saving the 180s idle wait in _wait_for_stable_answer.
        """
        try:
            r = await page.evaluate(_QWEN_READ_JS, list(self.ASSISTANT_CANDIDATES))
            before = len((((r or {}).get("text")) or "").strip())
        except Exception:  # noqa: BLE001
            before = 0

        for sel in self.SEND_BUTTON_CANDIDATES:
            try:
                btn = page.locator(sel).first
                if not await btn.is_visible(timeout=1500):
                    continue
                # The button is often disabled before the text registers, and click waits for actionable until timeout.
                # Give it 5s (it used to be the default 30s; one selector could eat half a minute).
                await btn.click(timeout=5000)
            except Exception:
                continue
            if await self._confirm_submitted(page, input_el, query, before):
                _log(f"submit confirmed via button: {sel}")
                return True
            _log(f"clicked {sel} but no submit evidence — trying next")

        for key in ("Enter", "Control+Enter"):
            try:
                await input_el.click()
                await page.keyboard.press(key)
            except Exception:  # noqa: BLE001
                continue
            if await self._confirm_submitted(page, input_el, query, before):
                _log(f"submit confirmed via {key}")
                return True
            _log(f"pressed {key} but no submit evidence")

        return False

    async def _find_input(self, page, timeout: float = 10000):
        """Try selectors one by one and return the first visible input/contenteditable."""
        # Poll for a while - the page hydrates slowly
        deadline = timeout
        elapsed = 0
        step = 500
        last_counts: dict[str, int] = {}
        while elapsed < deadline:
            for sel in self.INPUT_CANDIDATES:
                try:
                    loc = page.locator(sel).first
                    cnt = await page.locator(sel).count()
                    last_counts[sel] = cnt
                    if cnt > 0 and await loc.is_visible():
                        _log(f"input hit: {sel} (visible)")
                        return loc
                except Exception:
                    continue
            await asyncio.sleep(step / 1000)
            elapsed += step
        _log(f"input not found after {timeout}ms; counts={last_counts}")
        return None

    # D3 (2026-05-18): in hot browser mode, call this before each query to reset the chat context,
    # preventing the previous query's conversation from being used as context for the next answer.
    # search() is still one-shot for now, so this method is not called yet; reserved for D4's EngineSession.
    async def _start_new_chat(self, page) -> None:
        """Click "新建对话" ("New conversation") sidebar button to reset conversation.

        Measured on www.qianwen.com (2026-05-18 DOM probe): the top sidebar `<button>` text = "新建对话" ("New conversation")
        (note it is "新**建**对话", not "新对话"). The `<button>` tag is rare, so it is the most stable; text-is
        matching as fallback ("新对话" ("New chat") is also kept in case a redesign changes it back).
        """
        candidates = [
            "button:has-text('新建对话')",
            "button:has-text('新对话')",
            "[class*='sideBar']:has-text('新建对话')",
            "text=新建对话",
            "text=新对话",
        ]
        for sel in candidates:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await human_delay(0.5, 1.0)
                    _log(f"new-chat clicked via {sel!r}")
                    return
            except Exception:
                continue
        _log("new-chat button NOT found — needs DOM probe to fix selector")
        # raise so EngineSession records an error and the runner does not extract competitors, avoiding data pollution from old conversation context
        raise RuntimeError(
            "qwen new-chat button not found — refusing to send query into stale session"
        )

    async def _dismiss_popups(self, page) -> None:
        for sel in [
            "text=我知道了",
            "text=同意",
            "text=Accept",
            "text=接受全部",
            "text=不再提示",
            "[aria-label='close']",
            "[aria-label='Close']",
            ".ant-modal-close",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click()
                    await human_delay(0.3, 0.6)
            except Exception:
                continue

    _STATUS_PHRASES = (
        "正在读取来源",
        "正在搜索",
        "正在思考",
        "正在生成",
        "思考中",
        "搜索中",
        "读取中",
        "Searching",
        "Reading sources",
        "Thinking",
    )

    def _is_status_only(self, text: str) -> bool:
        if not text:
            return True
        stripped = text.strip()
        if len(stripped) < 30 and any(p in stripped for p in self._STATUS_PHRASES):
            return True
        return False

    async def _wait_for_stable_answer(
        self, page, max_wait: float = 180.0, stable_secs: float = 2.5
    ) -> str | None:
        """Poll the found assistant selector until its inner_text is stable + no stop button.

        Returns the winning selector (reused by later _extract_answer / _extract_citations),
        or None (timeout).
        """
        poll_interval = 0.6
        last_text = ""
        last_len = 0
        stable_since = None
        elapsed = 0.0
        winning_sel: str | None = None

        while elapsed < max_wait:
            # Read innerText directly with page.evaluate: locator.inner_text() on elements that keep re-rendering during streaming
            # times out "waiting for the element to be stable" -> cur is always empty (the real cause of last_len=0 in tests).
            # evaluate is an instant snapshot that does not wait for stability, consistent with the working probe.
            cur = ""
            sel_hit = None
            try:
                r = await page.evaluate(_QWEN_READ_JS, list(self.ASSISTANT_CANDIDATES))
                cur = (r or {}).get("text") or ""
                sel_hit = (r or {}).get("sel")
            except Exception:  # noqa: BLE001
                cur = ""
                sel_hit = None

            # Check whether the "停止生成" ("Stop generating") button is still present
            stop_visible = False
            for sb in self.STOP_BUTTON_CANDIDATES:
                try:
                    if await page.locator(sb).first.is_visible(timeout=200):
                        stop_visible = True
                        break
                except Exception:
                    continue

            # Stability is now judged as "length stops growing" (the new qwen attaches dynamic elements to the end of answers, so exact text comparison
            # is never equal -> runs to max_wait). Text stops growing + not a status placeholder + no stop button = generation complete.
            cur_len = len(cur.strip())
            if (
                cur
                and cur_len > 8
                and cur_len <= last_len
                and not self._is_status_only(cur)
                and not stop_visible
            ):
                if stable_since is None:
                    stable_since = elapsed
                elif elapsed - stable_since >= stable_secs:
                    winning_sel = sel_hit or winning_sel
                    _log(f"stable@{elapsed:.1f}s len={cur_len} sel={winning_sel!r}")
                    return winning_sel
            else:
                if cur_len > last_len:
                    last_len = cur_len
                    winning_sel = sel_hit or winning_sel
                last_text = cur
                stable_since = None

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        _log(f"wait timeout @ {max_wait}s, last_len={len(last_text)} stop_visible={stop_visible}")
        return winning_sel  # return the found selector even on timeout; extract can still salvage something

    async def _extract_answer(self, page, assistant_sel: str | None) -> str:
        # Also read instantly via page.evaluate (locator.inner_text times out on streaming elements)
        sels = [assistant_sel] if assistant_sel else []
        sels.extend(s for s in self.ASSISTANT_CANDIDATES if s != assistant_sel)
        sels = [s for s in sels if s]
        try:
            r = await page.evaluate(_QWEN_READ_JS, sels)
            raw = (r or {}).get("text") or ""
            if raw.strip():
                cleaned = self._scrub_status_noise(raw)
                if cleaned.strip():
                    return cleaned
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _scrub_status_noise(self, raw: str) -> str:
        if not raw:
            return ""
        cleaned = raw
        for noise in (
            "正在搜索网络", "正在读取来源", "正在思考", "正在生成", "正在搜索",
            "跳过", "已深度思考", "已搜索网络", "已经完成思考",
        ):
            cleaned = cleaned.replace(noise, "")
        cleaned = re.sub(r"\.{3,}", "", cleaned)
        cleaned = re.sub(r"…+", "", cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    async def _extract_citations(
        self, page, answer: str, assistant_sel: str | None
    ) -> List[Citation]:
        """Citation extraction strictly scoped to <a href> inside the assistant container + bare URLs in the answer body.

        Deliberately does **not** use network capture - qianwen's chat API stream is stuffed with CDN/analytics
        URLs, and a generic regex would inflate the KPI "AI citations total" to 80+. If qianwen answers later
        switch to plain text + a source drawer, add a strictly scoped drawer click ladder.
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
            citations.append(
                Citation(
                    url=cit.url,
                    domain=cit.domain,
                    title=cit.title,
                    snippet=cit.snippet,
                    position=len(citations) + 1,
                )
            )

        # 1) `<a href>` inside the assistant container (main path)
        dom_count = 0
        if assistant_sel:
            try:
                links = await page.locator(f"{assistant_sel} a[href^='http']").all()
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        title = await link.inner_text()
                    except Exception:
                        continue
                    if not href or not href.startswith("http"):
                        continue
                    if any(b in href for b in _QWEN_BLOCK_HOSTS):
                        continue
                    _add(Citation.from_url(href, title=title))
                    dom_count += 1
            except Exception:
                pass

        # 2) Bare URLs in the answer body (complement - written in markdown but not rendered as <a>)
        text_count = 0
        for u in extract_urls_from_text(answer):
            if any(b in u for b in _QWEN_BLOCK_HOSTS):
                continue
            if _add(Citation.from_url(u)) is not None:
                text_count += 1

        _log(f"citations: dom_a={dom_count} text_url={text_count} final={len(citations)}")
        return citations
