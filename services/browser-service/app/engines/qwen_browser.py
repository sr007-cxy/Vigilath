"""Qwen (通义千问) browser adapter — automates www.qianwen.com.

2026-05-18 迁移自 chat.qwen.ai → www.qianwen.com:
- chat.qwen.ai 是阿里出海版,可用邮箱登录
- www.qianwen.com 是千问国内主站,手机号登录,跟用户的扩展插件域名一致

迁移策略:DOM selector 选 "通用 + fallback 链",不写死 tongyi 特定 class —
React Ant Design 类型的 chat 应用通用模式:
  - 输入框:textarea / contenteditable,带 chat-related placeholder
  - 提交:Enter(Shift+Enter 换行)
  - 完成信号:连续 N 秒文本无变化 AND 没有可见的 "停止/Stop" 按钮
  - 答案容器:最后一条 user message 后面的 message bubble

日后稳定下来再针对 tongyi 抓特定 selector 减少 false-positive。
首次 probe 失败时,搜 `[Qwen-probe]` 日志,会打出 stage / count / selector 命中信息.
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import List

from ..browser import create_stealth_page, human_delay, save_page_session
from .base import Citation, EngineAdapter, EngineResult, extract_urls_from_text


_QWEN_BLOCK_HOSTS = (
    # 自家域,citation 不收
    "qwen.ai", "qianwen.com", "tongyi.aliyun.com", "aliyun.com",
    "alibabacloud.com", "alicdn.com", "alipay.com", "taobao.com", "tmall.com",
    # 通用埋点/CDN
    "w3.org", "googletagmanager.com", "google-analytics.com",
    "googlesyndication.com", "doubleclick.net", "facebook.net", "analytics",
)

# 中文来源显示名 → 实际 domain(沿用,跟具体引擎无关)
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


class QwenBrowserAdapter(EngineAdapter):
    name = "通义千问"
    _record_video = False

    def __init__(self):
        import os
        self._record_video = os.environ.get("GEO_RECORD_VIDEO", "").strip() in ("1", "true")

    # www.qianwen.com 入口 — 登录后自动进 chat 页,不写死后缀,让站点自己 redirect。
    CHAT_URL = "https://www.qianwen.com/"

    # 输入框 selector 候选链(按命中优先级)。
    # 现代 React chat 应用要么是 textarea,要么是 contenteditable div。
    INPUT_CANDIDATES = (
        "textarea[placeholder*='提问']",
        "textarea[placeholder*='输入']",
        "textarea[placeholder*='对话']",
        "textarea[placeholder*='Message']",
        "div[contenteditable='true'][role='textbox']",
        "div[contenteditable='true']",
        "textarea",
    )

    # 答案容器候选链 — 抓"最后一条 assistant message"
    # tongyi 没确切 DOM 之前,先用通用 React chat 常见命名 pattern。
    ASSISTANT_CANDIDATES = (
        "[data-message-role='assistant']",
        "[data-role='assistant']",
        "[class*='assistant-message']",
        "[class*='answer-content']",
        "[class*='chatItem-answer']",
        "[class*='message-item-answer']",
        "[class*='ai-message']",
        # 末位兜底:任意被标为 ai/bot 的 message
        "[class*='message'][class*='bot']",
        "[class*='message'][class*='ai']",
    )

    # 流式中"停止生成"按钮 — 它在,说明还没生成完。
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

    async def search(self, query: str) -> EngineResult:
        try:
            page, ctx = await create_stealth_page("qwen", record_video=self._record_video)

            # 网络抓包:tongyi 的 chat API 流也带 citation URL,JSON 路径未知,
            # 通用提取"任意 https?:// URL"已足够覆盖。
            captured_bodies: List[str] = []
            self._captured_bodies = captured_bodies

            def _on_response(response):
                url_l = response.url.lower()
                if not any(kw in url_l for kw in ("/api/", "chat", "completion", "search", "stream")):
                    return
                ctype = (response.headers.get("content-type") or "").lower()
                if ctype and not any(
                    t in ctype for t in ("json", "event-stream", "text/plain", "text/html")
                ):
                    return
                try:
                    asyncio.create_task(self._capture_body(response, captured_bodies))
                except Exception:
                    pass

            page.on("response", _on_response)

            await page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=30000)
            await human_delay(2, 3)
            _log(f"navigated to {page.url}")

            await self._dismiss_popups(page)

            input_el = await self._find_input(page, timeout=12000)
            if input_el is None:
                await ctx.close()
                return EngineResult(engine=self.name, query=query, error="input not visible (tongyi DOM)")

            # 填 query — 先 click 聚焦,再 fill / type fallback
            try:
                await input_el.click()
                await human_delay(0.2, 0.5)
            except Exception:
                pass
            filled = False
            try:
                await input_el.fill(query)
                filled = True
            except Exception:
                pass
            if not filled:
                # contenteditable 不支持 fill,用 type
                try:
                    await page.keyboard.insert_text(query)
                    filled = True
                except Exception:
                    pass
            if not filled:
                await input_el.type(query, delay=20)

            await human_delay(0.5, 1.0)
            await page.keyboard.press("Enter")

            # ── 等生成完 ──
            # 策略:轮询最后一个 assistant 容器 inner_text,连续 stable_secs 不变
            # AND 没有可见的 "停止" 按钮 → 生成结束。
            await human_delay(2, 3)
            assistant_sel = await self._wait_for_stable_answer(
                page, max_wait=180, stable_secs=2.5
            )
            await human_delay(1.0, 2.0)

            answer = await self._extract_answer(page, assistant_sel)
            citations = await self._extract_citations(page, answer, assistant_sel)

            _log(
                f"final: answer_len={len(answer)} citations={len(citations)} "
                f"sel={assistant_sel!r}"
            )

            await save_page_session("qwen", ctx)

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
            _log(f"search crashed: {type(e).__name__}: {e}")
            return EngineResult(engine=self.name, query=query, error=str(e))

    async def _find_input(self, page, timeout: float = 10000):
        """逐个 selector 试,返回第一个可见的 input/contenteditable。"""
        # poll 一段时间 — 页面 hydrate 慢
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
        """轮询找到的 assistant selector,等其 inner_text 稳定 + 无停止按钮。

        返回胜出的 selector(后续 _extract_answer / _extract_citations 复用),
        或 None(超时)。
        """
        poll_interval = 0.6
        last_text = ""
        stable_since = None
        elapsed = 0.0
        winning_sel: str | None = None

        while elapsed < max_wait:
            # 找到当前命中的 assistant 容器(每轮重检 — 流式期间容器才出现)
            cur = ""
            sel_hit = None
            for sel in self.ASSISTANT_CANDIDATES:
                try:
                    els = await page.locator(sel).all()
                    if not els:
                        continue
                    text = await els[-1].inner_text()
                    if text and len(text.strip()) > len(cur.strip()):
                        cur = text
                        sel_hit = sel
                except Exception:
                    continue

            # 检查 "停止生成" 按钮是否还在
            stop_visible = False
            for sb in self.STOP_BUTTON_CANDIDATES:
                try:
                    if await page.locator(sb).first.is_visible(timeout=200):
                        stop_visible = True
                        break
                except Exception:
                    continue

            if (
                cur
                and len(cur.strip()) > 8
                and cur == last_text
                and not self._is_status_only(cur)
                and not stop_visible
            ):
                if stable_since is None:
                    stable_since = elapsed
                elif elapsed - stable_since >= stable_secs:
                    winning_sel = sel_hit
                    _log(f"stable@{elapsed:.1f}s len={len(cur)} sel={sel_hit!r}")
                    return winning_sel
            else:
                last_text = cur
                stable_since = None

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        _log(f"wait timeout @ {max_wait}s, last_len={len(last_text)} stop_visible={stop_visible}")
        return winning_sel  # 即便超时也返回找到的 selector,extract 还能抢救

    async def _extract_answer(self, page, assistant_sel: str | None) -> str:
        sels = [assistant_sel] if assistant_sel else []
        sels.extend(s for s in self.ASSISTANT_CANDIDATES if s != assistant_sel)
        for sel in sels:
            if not sel:
                continue
            try:
                els = await page.locator(sel).all()
                if not els:
                    continue
                raw = await els[-1].inner_text()
                if raw and raw.strip():
                    cleaned = self._scrub_status_noise(raw)
                    if cleaned.strip():
                        return cleaned
            except Exception:
                continue
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

    async def _capture_body(self, response, bucket: List[str]) -> None:
        try:
            text = await response.text()
        except Exception:
            return
        if not text or "http" not in text:
            return
        bucket.append(text[:400_000])

    async def _extract_citations(
        self, page, answer: str, assistant_sel: str | None
    ) -> List[Citation]:
        """多路径提取:网络抓包 + assistant 容器 `<a>` + 答案正文裸 URL。

        tongyi 的具体 citation DOM 待 v2 适配,先用通用 `<a href>` 扫描兜底。
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

        # 1) Network capture
        net_urls: List[str] = []
        try:
            for body in getattr(self, "_captured_bodies", []) or []:
                for m in re.finditer(
                    r'"(?:url|link|href|source_url|web_url|redirect_url)"\s*:\s*"'
                    r'(https?://[^"\\\s]+)"',
                    body,
                ):
                    net_urls.append(m.group(1))
                for m in re.finditer(r'https?:\\?/\\?/[^\s"<>\\]{4,500}', body):
                    u = m.group(0).replace("\\/", "/")
                    net_urls.append(u)
        except Exception:
            pass
        net_urls = list(dict.fromkeys(net_urls))
        for u in net_urls:
            if any(b in u for b in _QWEN_BLOCK_HOSTS):
                continue
            if not u.startswith("http"):
                continue
            _add(Citation.from_url(u))

        # 2) Assistant 容器 `<a href>`
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
            except Exception:
                pass

        # 3) 答案正文裸 URL
        for u in extract_urls_from_text(answer):
            if any(b in u for b in _QWEN_BLOCK_HOSTS):
                continue
            _add(Citation.from_url(u))

        _log(
            f"citations: net={len(net_urls)} dom_a=(included above) "
            f"final={len(citations)}"
        )
        return citations
