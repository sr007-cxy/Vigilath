"""qwen semi-browser HTTP - keep one logged-in hot page and fetch Tongyi's chat API from inside the page.

Why (measured 2026-08):
  * The chat API of Tongyi's CN site www.qianwen.com is POST https://chat2.qianwen.com/api/v2/chat,
    but it carries a full set of Alibaba risk-control signatures (bx-ua / bx_et / bx-umidtoken / clt-acs-sign / clt-acs-reqt /
    eo-clt-dvidn / eo-clt-actkn / x-device-id / x-wpk-*, 19 headers in total), and **the signatures are computed in real time**:
    replaying captured headers as-is with only nonce+timestamp changed, the server immediately returns 403 `EX015 签名错误` ("signature error").
    -> Pure Python direct calls do not work unless the signing algorithm is reverse engineered (breaks whenever Alibaba changes it, not worth it).
  * But `window.fetch` in the page **has already been patched by the site itself** (not native code),
    and the signing functions `etSign` / `LTKSign` also hang on window. So **fetching the same API from the page context,
    the site's JS fills in the signatures automatically** - measured HTTP 200, `text/event-stream`, a normal 826KB stream.
  * Tongyi **does not have Doubao's shark risk-control verify layer** (Doubao gets blocked by verify even with correct signatures), so it passes on the first try.

Compared with the old DOM scraping (app/qwen_query.py + engine_selectors), this path:
  - depends on no selectors -> page redesigns no longer produce `dom_not_found`
  - sources come from structured JSON (sources.list[]), without the missed/extra captures of DOM scraping
  - additionally gets metadata like authority / reliable / publish_time / host_name
  - runs serially on one hot page, saving a subprocess + cold browser start per query

Metric definition: take only the source list Tongyi returns, with no secondary filtering - however many they give, that is how many we give.

env:
  QWEN_HOT_HTTP=1   enable this path (off by default; when off the caller falls back to the original DOM flow)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, Optional

_CHAT_URL_KEY = "/api/v2/chat"
_HOME = "https://www.qianwen.com/"

# Executed in the page: send one query and parse the SSE, returning {answer, citations, ...}
# Note the whole thing runs in the browser - so fetch uses the site's patched implementation and carries signatures automatically.
_JS_QUERY = r"""
async ([url, body, maxBytes, idleMs, hardMs, connectMs]) => {
  // 时钟从**发请求之前**起算。分两段各自计时会让最坏情况变成「连接 160s + 收流
  // 165s」,加起来早就超过外层 180s —— 那样 hardMs 兜底等于没有。
  const t0 = Date.now();
  // 连接阶段也得能掐断:2026-09-05 test host 上新版读循环已生效,却仍出现整整 180s 的
  // 外层超时、且日志里一次都没打出返回值,说明卡在 fetch 本身,压根没进读循环。
  // 挂起的 fetch 只有 AbortController 能中止;abort 之后 reader.read() 也会一并拒绝。
  const ac = new AbortController();
  const kill = () => { try { ac.abort(); } catch (e) {} };
  // 连接阶段单独给一道更短的闸:请求头迟迟不来就是这条连接废了,没必要陪它耗满
  // 整个 hardMs —— 早点失败,调用方还来得及回退到 DOM 流程跑完这道题。
  let killer = setTimeout(kill, connectMs);
  const bail = (o) => { clearTimeout(killer); return o; };

  // **不能只靠 abort**:window.fetch 被站点自己 patch 过(整条路子就是借它自动签名),
  // patch 版本不保证认 AbortSignal —— 2026-09-05 test host 实测 45s 的 abort 闸挂上去之后,
  // 照样出现整整 180s 的外层超时、JS 一次没返回,就是 abort 被吞了。所以再套一层
  // Promise.race:超时就直接把这次调用结掉,底下那条请求爱挂着挂着去(页面会轮换)。
  let r = null, connErr = null;
  const raced = await Promise.race([
    fetch(url, {
      method: "POST",
      headers: {"content-type": "application/json",
                "accept": "application/json, text/event-stream, text/plain, */*"},
      body: JSON.stringify(body),
      credentials: "include",
      signal: ac.signal,
    }).then((x) => ({resp: x}), (e) => ({err: e})),
    new Promise((res) => setTimeout(() => res({timeout: true}), connectMs)),
  ]);
  // phase 是排障时区分「连不上」和「连上了但没答完」的唯一线索,别省
  if (raced.timeout) {
    return bail({error: "connect_timeout", phase: "connect", elapsedMs: Date.now() - t0});
  }
  if (raced.err) {
    connErr = raced.err;
    return bail({error: "fetch_failed: " + ((connErr && connErr.name) || String(connErr)),
                 phase: "connect", elapsedMs: Date.now() - t0});
  }
  r = raced.resp;
  clearTimeout(killer);
  killer = setTimeout(kill, Math.max(1000, hardMs - (Date.now() - t0)));
  if (!r.ok) return bail({status: r.status, error: "http_" + r.status});
  if (!r.body) return bail({status: r.status, error: "no_body"});

  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "", total = 0, answer = "", thinking = "";
  const cites = [], seen = new Set();

  // 通义把信源放在 sources.list[] 里:{normalized_url|raw_url|url, title,
  // host_name, publish_time, authority, reliable, sc_name}
  const collect = (o) => {
    if (!o || typeof o !== "object") return;
    if (Array.isArray(o)) { for (const it of o) collect(it); return; }
    const u = o.normalized_url || o.raw_url || o.url || o.link;
    if (typeof u === "string" && u.startsWith("http")
        && !/qianwen\.com|aliyun\.com|alicdn\.com|tongyi\.com/.test(u)) {
      if (!seen.has(u)) {
        seen.add(u);
        cites.push({
          url: u,
          title: String(o.title || o.doc_name || o.sc_name || "").slice(0, 300),
          site: String(o.host_name || o.site_name || o.sc_name || "").slice(0, 80),
          publish_time: String(o.publish_time || "").slice(0, 40),
          authority: o.authority ?? null,
          reliable: o.reliable ?? null,
        });
      }
    }
    for (const v of Object.values(o)) collect(v);
  };

  // 收流的三道出口。**只等 done(流关闭)是不够的**:通义答完后经常把连接挂着
  // 不关,reader.read() 就一直挂在那儿,外层 180s 超时把**已经收全的答案连同信源
  // 一起丢掉**,再去跑一遍慢得多的 DOM 流程。2026-09-05 test host 的 /tmp/qwen_hot.log
  // 里一半调用是这么废掉的(其余成功的那些,答案 1200~2100 字、信源 5~19 条,
  // 说明数据本来就到手了)。
  //   ① [DONE] / finished 标记 —— 服务端明说答完了
  //   ② 静默 idleMs 还没有新字节,且已经有答案 —— 当作答完(挂着的连接就是这一类)
  //   ③ hardMs 兜底 —— 有多少要多少,绝不空手而归
  let finished = false, lastAt = Date.now();
  // pending 必须跨轮保留:read() 输给静默超时后**那次读还在排队**,直接丢掉重新
  // read() 会让它领到的那块数据无人接收(流式读请求是排队消费的),等于每次静默
  // 误判都吃掉一段正文。只有真正拿到 chunk 才清空。
  let pending = null;
  const readOnce = () => {
    if (!pending) pending = reader.read();
    return Promise.race([
      pending.then((v) => ({chunk: v})),
      new Promise((res) => setTimeout(() => res({idle: true}), Math.max(1000, idleMs))),
    ]);
  };

  try {
  while (true) {
    const got = await readOnce();
    if (got.idle) {
      // 静默超时:有答案就收工,没答案说明还在思考(通义联网检索前期无输出),继续等
      if (answer) break;
      if (Date.now() - t0 > hardMs) break;
      continue;
    }
    pending = null;
    const {done, value} = got.chunk;
    if (done) break;
    lastAt = Date.now();
    buf += dec.decode(value, {stream: true});
    total += value.length;
    const lines = buf.split("\n");
    buf = lines.pop();
    for (const line of lines) {
      const s = line.trim();
      if (!s.startsWith("data:")) continue;
      const p = s.slice(5).trim();
      if (!p) continue;
      if (p === "[DONE]") { finished = true; continue; }
      let d; try { d = JSON.parse(p); } catch (e) { continue; }
      collect(d);
      // 答案文本:messages[].content 是全量覆盖式下发,取最后一次即可
      const msgs = (d.data && d.data.messages) || d.messages || [];
      for (const m of msgs) {
        // 思维链(2026-09-16):按类型字段分流,think/reason 字样的进 thinking,不混进正文。
        // 字段名各版本不一(mime_type / contentType / type),形状没实测过 —— 认到哪个算哪个,
        // 认不出就是 thinking 为空,正文不受影响。
        const kind = String(m.mime_type || m.contentType || m.content_type || m.type || "");
        if (/think|reason/i.test(kind)) {
          if (typeof m.content === "string" && m.content.length > thinking.length) thinking = m.content;
          continue;
        }
        if (typeof m.content === "string" && m.content.length > answer.length
            && m.mime_type !== "signal/post") {
          answer = m.content;
        }
      }
      // 服务端自报答完(字段名各版本不一,认到哪个算哪个)
      const st = (d.data && (d.data.msgStatus || d.data.stopReason)) || d.msgStatus || d.stopReason;
      if (st === "finished" || st === "stop" || d.finished === true) finished = true;
    }
    if (finished && answer) break;
    if (total > maxBytes) break;
    if (Date.now() - t0 > hardMs) break;
  }
  } catch (e) {
    // 收流中途被 abort / 断流:已经收到的那部分照样交出去,别整单作废
    return bail({status: r.status, bytes: total, answer, thinking, cites, finished,
                 phase: "stream", elapsedMs: Date.now() - t0,
                 error: answer ? null : ("stream_failed: " + ((e && e.name) || String(e)))});
  }
  try { await reader.cancel(); } catch (e) {}
  return bail({status: r.status, bytes: total, answer, thinking, cites,
               idleMs: Date.now() - lastAt, finished, elapsedMs: Date.now() - t0});
}
"""


def hot_http_enabled() -> bool:
    return os.environ.get("QWEN_HOT_HTTP", "").strip() in ("1", "true", "True")


def _log(msg: str) -> None:
    """Write diagnostic logs to disk - the worker process's stdout does not go into the journal, so this is how to see inside.
    On by default; set QWEN_HOT_LOG=0 to turn off."""
    if os.environ.get("QWEN_HOT_LOG", "1").strip() == "0":
        return
    try:
        import time
        with open("/tmp/qwen_hot.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


async def _focus_editor(target) -> None:
    """Focus the Tongyi input box: prefer JS focus(), **not click()**.

    2026-08-24: Tongyi's first screen has a large decorative image (absolute inset-0 h-[266px] object-cover) over
    the center of the input box, and elementFromPoint hits that image. So Playwright's click() keeps waiting for
    "element is visible, enabled and stable" until the 30s timeout - the upper layer sees "input box
    not found" / "page may not be logged in", while the account and input box are actually fine (measured: focus() + keyboard.type
    does get the text in). click is kept as a fallback.
    """
    try:
        await target.evaluate("e => e.focus()")
        return
    except Exception:  # noqa: BLE001
        pass
    await target.click(timeout=8000)


async def _dismiss_launch_modal(page) -> None:
    """Close Tongyi's new-feature popup (2026-08's 「定时任务已上线」 ("Scheduled tasks are live")).

    It has a `fixed inset-0 z-[1001]` backdrop-blur overlay that covers the whole input box: the element is in
    the DOM and CSS-visible (display:block/visibility:visible), but elementFromPoint hits the
    image in the popup, so click() is intercepted and times out. Both paths break - hot HTTP cannot capture the
    /api/v2/chat template (and the error is misreported as "page may not be logged in" while the account is fine),
    and the DOM flow reports Locator.click timeout. That is why the test host fell back 323 times in one day on 2026-08-24.

    Measured: Escape closes it. **Do not click 「体验定时任务」 ("Try scheduled tasks")**, that enters the feature flow (we just fell into the same trap with Wenxin's popup,
    where clicking 「试一试」 ("Try it") entered task mode).
    """
    try:
        if await page.locator("text=定时任务已上线").count() == 0:
            return
    except Exception:  # noqa: BLE001
        return
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        if await page.locator("text=定时任务已上线").count() == 0:
            return
    except Exception:  # noqa: BLE001
        pass
    for sel in ("[class*='close']", "[aria-label='关闭']"):
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.click(timeout=3000)
                await asyncio.sleep(0.6)
                return
        except Exception:  # noqa: BLE001
            continue


class QwenHotHttp:
    """Holds one logged-in hot page + one request template (URL / body skeleton).

    The template comes from the /api/v2/chat request captured when "the page first really sends" - the URL's
    device/ut/biz_id params and the body's session_id/topic_id are all session-level;
    assembling them ourselves easily misses fields, so capturing once is most reliable.
    """

    def __init__(self) -> None:
        self.page = None
        self.ctx = None
        self.session_id = None            # account id issued by the pool (troubleshooting: which account is refusing)
        self._prime_done = False          # whether the prime SSE has been fully received (see _prime)
        self._tmpl: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._max_bytes = int(os.environ.get("QWEN_HOT_MAX_BYTES", "1500000"))
        # Rotation: the hot page keeps holding the same checked-out account; without switching, all queries
        # pile onto one account - quickly hitting the per-account daily limit and easily triggering risk control (today Doubao account 83
        # got banned after 38 queries in one day, a live example). After N queries, close the page and rebuild -> automatically check out the next account.
        # Same idea as EngineSession's _queries_since_rotate.
        self._rotate_after = int(os.environ.get("QWEN_HOT_ROTATE_AFTER", "15"))
        self._queries_since_rotate = 0
        # How long of silence counts as "done answering". After answering, Tongyi often leaves the SSE connection open instead of closing it; waiting only for the stream to end
        # would hang until the outer timeout and throw away the fully received answer along with its sources. 20s is margin for
        # pauses during web retrieval (measured: normal streams emit output at second-level intervals).
        self._idle_ms = int(os.environ.get("QWEN_HOT_IDLE_MS", "20000"))
        # Connection gate. Measured 2026-09-06: with the gate raised to 150s, hung requests **still returned not a single byte**
        # (150088ms used up), while normal ones produce the full text in 8-15s - so hung ones are dead connections, and waiting
        # even one more second is wasted. So the gate only makes it fail early; recovery relies on the page-switch retry below.
        self._connect_sec = float(os.environ.get("QWEN_HOT_CONNECT_SEC", "30"))
        # Outer fallback timeout. The JS hard limit is 15s less than this, ensuring JS wraps up first and hands over the answer,
        # rather than being cut off by asyncio.wait_for and returning empty.
        self._timeout = float(os.environ.get("QWEN_HOT_TIMEOUT_SEC", "180"))

    # ── Lifecycle ────────────────────────────────────────────────
    async def init(self) -> None:
        from .browser import create_stealth_page

        _log("init: 开始 create_stealth_page(qwen)")
        self.page, self.ctx = await create_stealth_page("qwen")
        # Which account is used is only known by the pool's check-out slot (not on the page object). Essential for troubleshooting:
        # on 9/6 in production, the three other hosts had every query refused from some moment on while the test host was fine all day,
        # and without an account id one cannot tell "this machine's account was flagged by risk control" from "this session broke itself".
        try:
            from .session_store import get_last_checkout_id
            self.session_id = get_last_checkout_id("qwen")
        except Exception:  # noqa: BLE001
            self.session_id = None
        _log(f"init: 页面已建, 账号={self.session_id}")
        tmpl = self._tmpl

        def _on_req(req):
            try:
                if _CHAT_URL_KEY in req.url and req.method == "POST" and "url" not in tmpl:
                    tmpl["url"] = req.url
                    tmpl["body"] = req.post_data
            except Exception:  # noqa: BLE001
                pass

        def _on_done(req):
            # When the prime SSE is really fully received - see the explanation in _prime
            try:
                if _CHAT_URL_KEY in req.url and req.method == "POST":
                    self._prime_done = True
            except Exception:  # noqa: BLE001
                pass

        self.page.on("request", _on_req)
        self.page.on("requestfinished", _on_done)
        self.page.on("requestfailed", _on_done)
        await self.page.goto(_HOME, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(8)
        try:
            n = len(await self.page.inner_text("body"))
            logged = await self.page.evaluate(
                "() => !!(document.querySelector(\"[contenteditable='true']\") || document.querySelector('textarea'))")
            _log(f"init: goto 完成, 页面{n}字, 有输入框={logged}")
        except Exception as e:  # noqa: BLE001
            _log(f"init: goto 后取状态失败 {e!r}")
        # The new-feature popup covers the input box; without closing it nothing can be sent and no template captured
        await _dismiss_launch_modal(self.page)
        # The first time, really send one via the DOM (only to capture the template); all later queries use in-page fetch
        await self._prime()
        _log(f"init: prime 完成, 抓到模板={'url' in self._tmpl}")
        if "url" not in self._tmpl:
            raise RuntimeError("qwen hot-http: 没抓到 /api/v2/chat 模板(页面可能未登录)")

    async def _prime(self) -> None:
        for sel in ("[contenteditable='true']", "textarea"):
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await _focus_editor(el)
                    await asyncio.sleep(0.4)
                    await self.page.keyboard.type("你好", delay=35)
                    await asyncio.sleep(0.5)
                    await self.page.keyboard.press("Enter")
                    break
            except Exception:  # noqa: BLE001
                continue
        # Wait for the template request to be sent
        for _ in range(30):
            if "url" in self._tmpl:
                break
            await asyncio.sleep(1)
        # **Then wait for the prime stream to be fully received**. Capturing the request URL only means it was sent; the answer is still streaming.
        # Tongyi seems to accept only one request at a time per session: the next one sent while prime is still generating
        # does not even get response headers back (measured 2026-09-06, zero bytes even with the connection gate at 150s) -
        # the reason the first query after creating a page is especially prone to hanging is that it collides with prime's own stream.
        for _ in range(40):
            if getattr(self, "_prime_done", False):
                break
            await asyncio.sleep(1)
        _log(f"_prime: 模板={'有' if 'url' in self._tmpl else '无'} "
             f"prime流收完={getattr(self, '_prime_done', False)}")

    async def close(self) -> None:
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        self.page = self.ctx = None
        self._tmpl = {}

    # ── Single query ────────────────────────────────────────────────
    def _build_body(self, query: str) -> dict:
        body = json.loads(self._tmpl["body"])
        body["req_id"] = uuid.uuid4().hex
        body["parent_req_id"] = "0"        # treat each as a new round to avoid context bleed
        body["scene_param"] = "first_turn"
        body["deep_search"] = "1"          # enable web retrieval - without it there are no sources
        body["messages"] = [{
            "mime_type": "text/plain",
            "content": query,
            "meta_data": {"ori_query": query},
            "status": "complete",
        }]
        return body

    def _fresh_url(self) -> str:
        """Replace nonce/timestamp in the URL; the page computes the signature for the new URL at fetch time."""
        import re

        u = self._tmpl["url"]
        u = re.sub(r"nonce=[^&]*", "nonce=" + uuid.uuid4().hex[:11], u)
        u = re.sub(r"timestamp=\d+", "timestamp=%d" % int(time.time() * 1000), u)
        return u

    async def query(self, query_text: str, timeout: Optional[float] = None) -> dict:
        # The outer timeout is only a fallback: normal completion is decided by the three exits in JS: [DONE]/silence/hard limit.
        # Leave a 15s margin so JS wraps up and hands over the answer itself, rather than being cut off here and dropping it as empty.
        timeout = float(timeout if timeout is not None else self._timeout)
        # Which account was used travels with the result (success or failure) back to the center and onto the response: without it the DB only shows
        # "which machine", and whether refusals/empty answers/sliders concentrate on one account can never be told (the 09-10 investigation got stuck right here).
        empty = {"engine": "qwen", "query": query_text, "answer": "", "citations": [],
                 "source_url": None, "video_url": None, "error": None,
                 "session_id": self.session_id}
        async with self._lock:
            if self.page is None:
                return {**empty, "error": "hot-http not initialized"}
            # A stuck call leaves only a timeout line, and without the question it cannot be reproduced - log it before starting
            _log(f"query: 发出 [{query_text[:60]}]")
            try:
                res = await asyncio.wait_for(
                    self.page.evaluate(_JS_QUERY,
                                       [self._fresh_url(), self._build_body(query_text),
                                        self._max_bytes, self._idle_ms,
                                        int(max(10.0, timeout - 15.0) * 1000),
                                        int(self._connect_sec * 1000)]),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                _log(f"query: page.evaluate 超时 {timeout}s")
                return {**empty, "error": f"timeout ({timeout}s)"}
            except Exception as e:  # noqa: BLE001
                _log(f"query: page.evaluate 异常 {e!r}")
                return {**empty, "error": f"page.evaluate: {e}"[:200]}

        if not isinstance(res, dict):
            _log(f"query: 返回非 dict: {type(res)}")
            return {**empty, "error": "bad result"}
        # finished/idle are the only clues to "was the answer fully received or cut off"; must-see for troubleshooting:
        # finished=True means the server said it finished; finished=False with an answer = took the silence exit
        # (the kind where the connection stays open); neither and empty-handed = a real problem.
        _log(f"query: fetch status={res.get('status')} bytes={res.get('bytes')} "
             f"answer_len={len(res.get('answer') or '')} cites={len(res.get('cites') or [])} "
             f"finished={res.get('finished')} idle={res.get('idleMs')}ms "
             f"phase={res.get('phase') or 'ok'} took={res.get('elapsedMs')}ms "
             f"js_err={res.get('error')}")
        if res.get("error"):
            # http_403 = signature did not take effect (page lost login / patch broken) -> let the caller switch accounts or fall back
            return {**empty, "error": str(res["error"])[:120]}

        answer = (res.get("answer") or "").strip()
        cites = []
        for i, c in enumerate(res.get("cites") or []):
            url = c.get("url") or ""
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:  # noqa: BLE001
                domain = ""
            snippet_bits = []
            if c.get("site"):
                snippet_bits.append(f"[{c['site']}]")
            if c.get("publish_time"):
                snippet_bits.append(str(c["publish_time"]))
            cites.append({
                "url": url,
                "domain": domain,
                "title": c.get("title") or c.get("site") or "",
                "snippet": " ".join(snippet_bits),
                "position": i + 1,
            })
        self._queries_since_rotate += 1
        out = {**empty, "answer": answer, "citations": cites,
               "reasoning": (res.get("thinking") or "").strip() or None}
        if not answer:
            out["error"] = "empty_answer"
        return out

    def needs_rotate(self) -> bool:
        return self._queries_since_rotate >= self._rotate_after


# ── In-process singleton (like deepseek_http, one call per query) ────────────
_singleton: Optional[QwenHotHttp] = None
_singleton_lock = asyncio.Lock()
# When the page was last built (= last check-out from the pool). Rebuilding is not free: qwen's pool policy is
# a 300s minimum interval per account and 360s / 10 per hour per egress; before the interval passes check-out returns 404 outright,
# and in explicit pool mode, not getting an account **does not fall back to local files** -> the new page has no login session -> the whole query is wasted.
# So "rebuild on failure" must check this time first; if the interval is not enough, just retry in place.
_last_build_at: float = 0.0


async def get_hot() -> QwenHotHttp:
    global _singleton, _last_build_at
    async with _singleton_lock:
        # Rotation threshold reached -> close the old page (releasing the account); on rebuild create_stealth_page
        # checks out the next account from the pool, spreading usage across the pool.
        if _singleton is not None and _singleton.page is not None and _singleton.needs_rotate():
            print(f"[qwen-hot] 跑满 {_singleton._rotate_after} 条,轮换账号", flush=True)
            try:
                await _singleton.close()
            except Exception:  # noqa: BLE001
                pass
            _singleton = None
        if _singleton is None or _singleton.page is None:
            inst = QwenHotHttp()
            await inst.init()
            _singleton = inst
            _last_build_at = time.time()
        return _singleton


async def _drop_singleton(hot: QwenHotHttp) -> None:
    """Discard the current hot page. The next get_hot() rebuilds it - new browser context, new connection, new session."""
    global _singleton
    try:
        await hot.close()
    except Exception:  # noqa: BLE001
        pass
    if _singleton is hot:
        _singleton = None


def _looks_refused(answer: str) -> bool:
    """The criterion lives in engines/qwen_browser.looks_refused, shared with the DOM subprocess path."""
    from .engines.qwen_browser import looks_refused
    return looks_refused(answer)


async def run_qwen_hot_http(query: str) -> dict:
    """Run one qwen query (semi-browser HTTP). Retry if not acceptable; only after retries still fail is it handed back for the caller to fall back to DOM.

    Why retries are necessary (measured on the test host 2026-09-06, same account same question asked 8 times in a row):
      the same Tongyi session **accepts roughly every other request** - the hung one does not even return response headers
      (zero bytes even with the connection gate at 150s, so it is dead and waiting longer is useless), but asking again in place right after
      produces 1900 chars + 15 sources in 11 seconds; success and failure strictly alternate. In other words this path's ceiling is
      "half hang, half succeed"; not retrying throws away half the tasks - all the empty_answers in production on 9/6 were this.
    Retry order: first ask again in place (cheap; measured, after one hang the next one almost always succeeds); if that fails, discard the page and rebuild
    (new session + new connection + new check-out). **Refusals always rebuild directly**: the same batch of refused questions asked on the test host
    with each of the other hosts' accounts, all four accounts answered normally with 1500-2600 chars, so refusals are
    neither an account problem nor a question problem, and retrying in place changes no variable.

    Cost: rebuilding requires a new check-out (the same worker gets the same account back via sticky) + about 15-25s to bring up the page,
    so it is only done on failure, with total count and total time capped; do not burn the account's daily quota on retries.
    """
    attempts = max(1, int(os.environ.get("QWEN_HOT_ATTEMPTS", "3")))
    budget = float(os.environ.get("QWEN_HOT_BUDGET_SEC", "240"))
    # Minimum rebuild interval: a bit wider than the pool's 300s per-account minimum interval, otherwise check-out gets no account,
    # the new page has no login session, and a query that could have succeeded on retry is killed outright.
    rebuild_gap = float(os.environ.get("QWEN_HOT_REBUILD_GAP_SEC", "320"))
    t0 = time.time()
    last: dict = {"engine": "qwen", "query": query, "answer": "", "citations": [],
                  "source_url": None, "video_url": None, "error": "hot-http not attempted"}
    for i in range(attempts):
        try:
            hot = await get_hot()
        except Exception as e:  # noqa: BLE001
            # Page build failed (most likely no account available in the pool). Failing in the first round -> error so the caller falls back to DOM;
            # failing in a retry round -> hand back the previous round's result as-is; do not upgrade "hung once" into "page build failed".
            _log(f"run: 第{i+1}次 init 失败 {e!r}")
            if i == 0:
                return {**last, "error": f"hot-http init failed: {e}"[:200]}
            return last
        res = await hot.query(query)
        answer = (res.get("answer") or "").strip()
        if answer and not _looks_refused(answer):
            return res
        refused = bool(answer)
        last = res
        _log(f"run: 第{i+1}/{attempts}次不合格({'refused' if refused else res.get('error') or 'empty'}), "
             f"账号={hot.session_id}")
        if time.time() - t0 > budget:
            _log(f"run: 预算 {budget}s 用完,不再重试")
            break
        # Switch page or ask again in place: refusals switch page (retrying in place changes no variable); the first hang asks again in place
        # (cheap; measured, the one right after a hang usually succeeds), and only then switches page; and switching page depends on whether the pool's
        # minimum interval has passed; if not, keep retrying in place, which beats getting no account.
        if (refused or i > 0) and (time.time() - _last_build_at) > rebuild_gap:
            await _drop_singleton(hot)
        else:
            await asyncio.sleep(2)
    # The page state is really broken (lost login / signature broken) -> do not leave it for the next query, even if the rebuild interval has not passed:
    # if the next get_hot() gets no account, at most that one query falls back to DOM, while keeping a 401 page ruins every query.
    if str(last.get("error") or "").startswith("http_40") and _singleton is not None:
        await _drop_singleton(_singleton)
    return last
