"""qwen single query - runs in a separate process (an in-process browser-service context conflicts with the resident hot browser
and the answer cannot be read; the same flow in a separate process reliably produces web answers, verified repeatedly).

Called via subprocess by main.run_qwen_browser:
  <venv>/python app/qwen_query.py "<query>"
Inherits browser-service's env (ENGINE_SESSION_POOL_URL / ENGINE_SESSION_SERVICE_TOKEN),
which create_stealth_page uses to check out an account; checks in at the end. The last stdout line = the JSON result.
"""
import asyncio
import json
import sys
import time

# ★ Hard-coded /opt/browser-service: this service **does not run in a container** (QR-code login sessions + per-account bound egress IPs,
#   see "services must not import each other via same-machine relative paths" in docs/部署文档.md). What is injected here is its **own**
#   install directory, not another service's; when started as a subprocess it is not on sys.path, so it must be added explicitly.
sys.path.insert(0, "/opt/browser-service")

from app import engine_selectors  # noqa: E402
from app.browser import create_stealth_page, close_stealth_context  # noqa: E402
from app.session_store import (  # noqa: E402
    _pool_enabled, get_last_checkout_id, report_session_outcome,
)
from app.qwen_hot_http import _looks_refused  # noqa: E402
from app.reasoning import extract_reasoning  # noqa: E402
from app.engines.qwen_browser import (  # noqa: E402
    _QWEN_READ_JS, _QWEN_BLOCK_HOSTS, QwenBrowserAdapter, looks_refused,
)

# Selectors/API/URL all come from the engine_selectors config (self-healing Phase 1: redesigns change config, not code)
_CFG = engine_selectors.get("qwen")
# For self-healing verification: QWEN_SELECTOR_OVERRIDE (JSON) temporarily overrides the config, not persisted - verifies whether a proposal really works
import os as _os  # noqa: E402
_ov = (_os.environ.get("QWEN_SELECTOR_OVERRIDE") or "").strip()
if _ov:
    try:
        _CFG = {**_CFG, **{k: v for k, v in json.loads(_ov).items() if v}}
    except Exception:  # noqa: BLE001
        pass
_URL = _CFG.get("url", "https://www.qianwen.com/")
INPUT_SELS = tuple(_CFG.get("input_sels") or [])
# On an empty answer, ask again a few times **in the current conversation** (user request 2026-09-16: "when it gives no answer, ask a few more times in the current chat box").
# Tongyi occasionally "accepts but does not answer" - the session is created, the question goes in, the answer area stays empty; opening a new conversation and resending
# discards the preceding context, while following up with the original question in the same conversation usually gets the engine to answer. 0 = off, ask only once.
QWEN_REASK_MAX = int(_os.environ.get("QWEN_REASK_MAX", "2"))
# Follow-ups must finish within the upstream clock: telemetry calls browser-service with 180s, and main.py kills this subprocess per
# QWEN_SUBPROCESS_TIMEOUT (default 175s). So one whole query gets a 165s total budget; if "not a single character has appeared"
# beyond QWEN_SILENT_SEC, do not wait out the full 95s, follow up right away; stop following up when less than 50s of budget remains.
QWEN_TOTAL_BUDGET_SEC = float(_os.environ.get("QWEN_TOTAL_BUDGET_SEC", "165"))
QWEN_SILENT_SEC = float(_os.environ.get("QWEN_SILENT_SEC", "35"))
_REASK_MIN_LEFT_SEC = 50.0
SEND_SELS = tuple(_CFG.get("send_sels") or [])
ANSWER_SELS = list(_CFG.get("answer_sels") or [])
_CHAT_API = _CFG.get("chat_api", "chat2.qianwen.com/api/v2/chat")


def _resolve_host_ip(proxy: dict | None = None) -> str:
    """Resolve the **real egress IP** of this query.

    Fixed 2026-08: it used to probe only the local IP, with a comment saying "qwen does not use a proxy". But accounts can have a proxy bound
    (engine_sessions.proxy_id), and once bound the browser goes out through the proxy, so the local IP is not the real egress -
    so exit_ip always equaled the worker's own IP, looking like "proxy bound but running bare", which even led to wrong
    conclusions. The probe must use the same egress as the query, otherwise this field is fake.
    """
    import re as _re
    import urllib.request as _u
    opener = _u.build_opener()
    if proxy and proxy.get("server"):
        srv = proxy["server"]
        if proxy.get("username"):
            from urllib.parse import quote
            scheme, _, hostport = srv.partition("://")
            cred = f"{quote(proxy['username'], safe='')}:{quote(proxy.get('password') or '', safe='')}"
            srv = f"{scheme or 'http'}://{cred}@{hostport or srv}"
        opener = _u.build_opener(_u.ProxyHandler({"http": srv, "https": srv}))
    for url in ("https://ipinfo.io/ip", "https://myip.ipip.net", "https://api.ipify.org"):
        try:
            with opener.open(url, timeout=8) as r:
                m = _re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", r.read().decode("utf-8", "replace"))
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            continue
    return ""


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


async def _detect_logged_out(page) -> str:
    """Determine whether the current page is **really** logged out. Non-empty string = evidence found, empty = still looks logged in.

    Three independent pieces of evidence (any hit means logged out):
      1. the URL lands on a login/passport domain;
      2. a login entry appears on the page (login button / QR box);
      3. Tongyi's SSO cookie is gone from the browser context (the server actively cleared the ticket).
    If none hit, do not blame the account - most likely the page structure changed.
    """
    try:
        url = (page.url or "").lower()
        if any(k in url for k in ("/login", "signin", "passport", "auth.aliyun")):
            return f"redirected to {url[:80]}"
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-15: the standalone "登录" ("Log in") button in the top bar is also evidence. 10 newly imported accounts (cookies had
    # tongyi_sso_ticket but the server did not accept it) looked exactly like this: top bar "API 服务 | 下载电脑端 | 登录" ("API service | Download desktop | Log in"),
    # "暂无对话" ("No conversations yet") on the left, the input box accepted typing and the send button could be clicked, but the page did not budge - previously recorded as three
    # dom_not_found before quarantine, with the cause written as "page structure changed". A logged-in page shows the account name in the top bar,
    # and has no element whose content is exactly "登录" ("Log in").
    for sel in ("text=扫码登录", "text=手机号登录", "text=登录后可使用",
                "[class*='login-qrcode']", "[class*='loginPanel']",
                "text=/^\\s*登录\\s*$/"):
        try:
            if await page.locator(sel).first.is_visible(timeout=1200):
                return f"login prompt visible ({sel})"
        except Exception:  # noqa: BLE001
            continue
    try:
        cks = await page.context.cookies()
        if not any((c.get("name") or "").startswith("tongyi_sso_ticket") for c in cks):
            return "tongyi_sso_ticket missing in context"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ── Alibaba slider (baxia punishment page) ──────────────────────────────────────────
# Measured on the test host 2026-09-14: the question was sent, the chat API returned 200 throughout, and the URL switched to /chat/<id>, but the page never showed
# an answer - what popped up was `iframe#baxia-dialog-content`, with src
# chat2.qianwen.com/api/v2/chat/_____tmd_____/punish?x5secdata=… ("亲,请拖动下方滑块完成验证" ("please drag the slider below to verify")).
# The popup is in an iframe, so body.innerText cannot see those words, which is why nobody recognized it before: waiting 95s for nothing and reporting empty_answer;
# almost all 280 "empty answers" over 3 days were this; the hot-http path's connect_timeout / 403 is the same punishment.
# It pops up randomly per request: the same account asking again 2 minutes later gets a full answer with citations - the same thing as the 9-06 note
# "half hang, half succeed; asking again right after almost always works".
_CAPTCHA_IFRAME_SELS = ("iframe#baxia-dialog-content", "iframe[src*='_____tmd_____/punish']")


async def _detect_captcha(page) -> bool:
    for sel in _CAPTCHA_IFRAME_SELS:
        try:
            if await page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _parse_qwen_citations(body: str) -> list:
    """Dig web_source citations (url/title) out of the chat2 SSE stream. Each data: line is a complete JSON event;
    recursively find dicts with type==web_source. Same idea as deepseek parsing search_results."""
    from urllib.parse import urlparse
    cites, seen = [], set()
    for chunk in body.split("data:"):
        chunk = chunk.strip()
        if not chunk.startswith("{"):
            continue
        try:
            obj = json.loads(chunk)
        except Exception:  # noqa: BLE001
            continue
        stack = [obj]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if x.get("type") == "web_source" and x.get("url", "").startswith("http"):
                    u = x["url"]
                    if u not in seen and not any(b in u for b in _QWEN_BLOCK_HOSTS):
                        seen.add(u)
                        cites.append({"url": u, "domain": urlparse(u).netloc,
                                      "title": x.get("title") or x.get("name") or "",
                                      "snippet": x.get("snippet") or x.get("summary") or "",
                                      "position": len(cites) + 1})
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return cites


async def run(query: str, _attempt: int = 1, _reuse: tuple | None = None) -> dict:
    """_attempt / _reuse are only for self-retry after hitting a slider / refusal: same page, same account, open a new conversation and resend."""
    out = {"engine": "qwen", "query": query, "answer": "", "citations": [], "error": None,
           "session_id": None}
    adapter = QwenBrowserAdapter()
    if _reuse is not None:
        page, ctx, chat_responses = _reuse
    else:
        try:
            page, ctx = await create_stealth_page("qwen")
        except Exception as e:  # noqa: BLE001
            out["error"] = f"launch: {e}"[:200]
            return out
        chat_responses = []  # captured completion SSE responses (citation sources)
        # The account pool is on but not a single account was obtained (all leased by other machines / all quarantined) -> do not ask anonymously.
        # Measured on the test host 2026-09-16: asking Tongyi anonymously, every question instantly gets 「你好,我无法回答这个问题,
        # 我们换一个话题聊聊吧。」 ("Hello, I can't answer this question, let's talk about something else.") - upstream treats it as a **definitive refusal**, and the question gets
        # held for 2 days in drip without being asked again (refused_skip), meaning one moment without an account kills the customer's term. Report no session
        # available, which the dispatch center recognizes (_TRANSIENT_ERR), backs off and lets another machine claim it.
        if _pool_enabled() and get_last_checkout_id("qwen") is None:
            try:
                await close_stealth_context(ctx)
            except Exception:  # noqa: BLE001
                pass
            out["error"] = "no session available (qwen pool empty for this worker)"
            return out
        # Which account was used travels with the result (success or failure) back to the center and onto the response. Only the pool's check-out slot knows it,
        # and report_session_outcome pops it on check-in, so record it here first. Without it the DB
        # only shows "which machine", and whether refusals/empty answers/sliders concentrate on one account can never be told (the 09-10 investigation got stuck right here).
    out["session_id"] = get_last_checkout_id("qwen")

    def _on_resp(resp):
        try:
            if _CHAT_API in resp.url and resp.request.method == "POST":
                chat_responses.append(resp)
        except Exception:  # noqa: BLE001
            pass

    if _reuse is None:
        page.on("response", _on_resp)
    try:
        await page.goto(_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        await _dismiss_launch_modal(page)
        # Ask -> wait for the answer; on an empty answer ask again **in the same conversation** (up to QWEN_REASK_MAX times), without opening a new one.
        # Only on the first ask does a missing input box raise login/redesign questions; during follow-ups a missing one just means this round failed, closed as an empty answer.
        answer, asks = "", 0
        deadline = time.monotonic() + QWEN_TOTAL_BUDGET_SEC
        while True:
            asks += 1
            if asks > 1:
                print(f"[qwen] 空答案(第 {asks - 1} 次),在当前对话里再问一次", flush=True)
                await asyncio.sleep(3)
            target = None
            for sel in INPUT_SELS:
                try:
                    if await page.locator(sel).first.is_visible(timeout=2000):
                        target = page.locator(sel).first
                        break
                except Exception:  # noqa: BLE001
                    continue
            if target is None and asks > 1:
                print("[qwen] 追问时找不到输入框,按空答案收尾", flush=True)
                break
            if target is None:
                # "Input box not found" **does not equal** login loss - page redesigns, slow loading and risk-control blocks all look like this.
                # And login_lost's quarantine policy is immediate (one occurrence = permanent quarantine), so one misjudgment scraps a
                # good account: in 2026-07, 9 of 12 qwen accounts were killed this way while their tongyi_sso_ticket was actually
                # still valid (requesting the home page with the same cookie gave HTTP 200 without redirecting to login).
                # So report login_lost only when there is **real evidence of being logged out**, otherwise classify as dom_not_found
                # (quarantined only after 3 occurrences, with an engine redesign alert, closer to the reality of "page structure changed").
                reason = await _detect_logged_out(page)
                if reason:
                    report_session_outcome("qwen", result="login_lost")
                    out["error"] = f"login lost: {reason}"
                else:
                    report_session_outcome("qwen", result="dom_not_found")
                    out["error"] = "input not found (page structure changed? session looks valid)"
                return out
            await _focus_editor(target)
            await asyncio.sleep(0.5)
            try:
                await target.fill(query)
            except Exception:  # noqa: BLE001
                await page.keyboard.insert_text(query)
            await asyncio.sleep(1)
            sent = False
            for sel in SEND_SELS:
                try:
                    b = page.locator(sel).first
                    if await b.is_visible(timeout=1500):
                        await b.click()
                        sent = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not sent:
                await page.keyboard.press("Enter")
            # ★ 2026-09-09: submission must be verified, not "clicked means sent". If SEND_SELS is not
            #   visible within 1.5s (in React the send button only enables after the text registers) it falls back to Enter, while in the new
            #   www.qianwen.com contenteditable Enter only inserts a newline and does not submit - the question stays in
            #   the input box, then waits 95s for nothing, gets an empty answer, recorded as empty_answer.
            #   Evidence: the input box was cleared / the stop button appeared / the answer area started showing text; any one means it was sent.
            submitted = False
            for _ in range(20):                      # wait up to 8s
                try:
                    left = (await target.inner_text(timeout=1000) or "").strip()
                except Exception:  # noqa: BLE001
                    submitted = True                 # input box gone = rebuilt after submit
                    break
                if query[:6] not in left:
                    submitted = True
                    break
                try:
                    r = await page.evaluate(_QWEN_READ_JS,
                                            ANSWER_SELS or list(adapter.ASSISTANT_CANDIDATES))
                    if len((((r or {}).get("text")) or "").strip()) > 0:
                        submitted = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.4)
            if not submitted:
                # Try Ctrl+Enter once more; if still not, error out directly - do not wait 95s for nothing and disguise a submit failure
                # as "engine did not answer" (empty_answer gets treated by the pool as semi-broken and repeatedly put back for retries).
                try:
                    await page.keyboard.press("Control+Enter")
                    await asyncio.sleep(2)
                    left = (await target.inner_text(timeout=1000) or "").strip()
                    submitted = query[:6] not in left
                except Exception:  # noqa: BLE001
                    submitted = True
                if not submitted:
                    # If it cannot be sent, first ask "is it logged in at all": a dead-cookie page still has the input box and send button,
                    # clicking just does nothing. That is an account problem (login_lost, quarantined immediately, marked login_ok=false),
                    # not a page structure change (dom_not_found needs 3 occurrences and also alerts).
                    reason = await _detect_logged_out(page)
                    if reason:
                        report_session_outcome("qwen", result="login_lost")
                        out["error"] = f"login lost: {reason}"
                        return out
                    report_session_outcome("qwen", result="dom_not_found")
                    out["error"] = "submit not confirmed (send button not found or click had no effect)"
                    return out
            # After sending, the slider usually pops up within 2-6s. Watch for 8s first; if it pops, stop waiting for the answer:
            # first time -> open a new conversation and resend the original question (asking again right after usually passes); if it pops again the second time -> report as captcha
            # (counted, quarantined at threshold 3), rather than recording empty_answer and erasing the cause.
            captcha = False
            for _ in range(8):
                await asyncio.sleep(1)
                if await _detect_captcha(page):
                    captcha = True
                    break
            if captcha:
                if _attempt == 1:
                    print("[qwen] 阿里滑块验证(第 1 次),新开对话重发一次", flush=True)
                    return await run(query, _attempt=2, _reuse=(page, ctx, chat_responses))
                print("[qwen] 阿里滑块验证(第 2 次),放弃", flush=True)
                report_session_outcome("qwen", result="captcha")
                out["error"] = "captcha: 阿里滑块验证,重发一次仍弹"
                return out
            # Wait a while first (after submitting, qwen has a "thinking/searching" pause; polling too early misjudges "stable" during the pause
            # and breaks, getting empty/status text - that is exactly why the probe's fixed 45s sleep got 568 chars).
            await asyncio.sleep(10)
            # Then poll until the length is stable: require a long enough answer (>60) + not a status placeholder + no growth for 4s straight.
            cands = ANSWER_SELS or list(adapter.ASSISTANT_CANDIDATES)
            last_len, stable_since, el, cur = 0, None, 18.0, ""
            # ★ 2026-09-09: keep the **longest body seen**, not just the cur from the last poll.
            #   It used to be answer = scrub(cur), where cur is the value read in the loop's last round - whenever that final
            #   page.evaluate collided with a streaming re-render (answer card replaced, evaluate threw) and read back empty,
            #   the complete answer read earlier was thrown away entirely and recorded as empty_answer.
            #   That is exactly the source of "same account, same hour, both succeeding and failing": failure depended on the timing of the last
            #   read, not on whether the engine answered. On physical devices qwen had 630 empty_answers in the past week,
            #   success rate dropping from 87% to 38%, all because of this.
            best = ""
            can_reask = asks <= QWEN_REASK_MAX
            while el < 95 and time.monotonic() < deadline:
                try:
                    r = await page.evaluate(_QWEN_READ_JS, cands)
                    cur = (r or {}).get("text") or ""
                except Exception:  # noqa: BLE001
                    cur = ""
                cl = len(cur.strip())
                # The fixed refusal (24 chars) can never reach the >60 "stable" threshold; without special handling it would wait the full 95s for nothing
                if cl and _looks_refused(cur):
                    best = cur
                    break
                # Not a single character (not even status text like "thinking"), already waited QWEN_SILENT_SEC,
                # and budget left to follow up -> do not wait out the full 95s, go follow up. Status text means it is thinking, keep waiting.
                if (cl == 0 and not best and el >= QWEN_SILENT_SEC and can_reask
                        and deadline - time.monotonic() >= _REASK_MIN_LEFT_SEC):
                    print(f"[qwen] {el:.0f}s 没出一个字,提前去追问", flush=True)
                    break
                if cl > len(best.strip()) and not adapter._is_status_only(cur):
                    best = cur
                if cl > 60 and cl <= last_len and not adapter._is_status_only(cur):
                    if stable_since is None:
                        stable_since = el
                    elif el - stable_since >= 4:
                        break
                else:
                    if cl > last_len:
                        last_len = cl
                    stable_since = None
                    # Still no text: every 5s check whether a slider popped up late; do not wait out the full 95s
                    if cl == 0 and not best and int(el) % 5 == 0 and await _detect_captcha(page):
                        if _attempt == 1:
                            print("[qwen] 阿里滑块验证(等答案期间弹出),新开对话重发一次", flush=True)
                            return await run(query, _attempt=2, _reuse=(page, ctx, chat_responses))
                        print("[qwen] 阿里滑块验证(第 2 次,等答案期间弹出),放弃", flush=True)
                        report_session_outcome("qwen", result="captcha")
                        out["error"] = "captcha: 阿里滑块验证,重发一次仍弹"
                        return out
                await asyncio.sleep(1.0)
                el += 1.0
            # Read once more at the end and take the longer of it and best - on normal convergence this read is the complete answer,
            # and when it collides with a re-render and reads back empty, best is the fallback.
            try:
                r = await page.evaluate(_QWEN_READ_JS, cands)
                final = (r or {}).get("text") or ""
            except Exception:  # noqa: BLE001
                final = ""
            if len(final.strip()) > len(best.strip()) and not adapter._is_status_only(final):
                best = final
            answer = adapter._scrub_status_noise(best) if best else ""
            # A refusal also counts as "no answer given": it is probabilistic (same question, same account, every other attempt gives 1000+ chars, see
            # the comment in qwen_hot_http.run_qwen_hot_http), so asking again in the same conversation is worth it.
            if answer and _looks_refused(answer) and asks <= QWEN_REASK_MAX:
                print(f"[qwen] 固定拒绝语(第 {asks} 次),当成没答案再问", flush=True)
                answer = ""
            if answer or asks > QWEN_REASK_MAX:
                break
            if deadline - time.monotonic() < _REASK_MIN_LEFT_SEC:
                print(f"[qwen] 空答案,但预算只剩 {deadline - time.monotonic():.0f}s,不再追问", flush=True)
                break
            # Citations: parse web_source from the intercepted completion SSE stream (with qwen web search the citations are in the API stream,
            # not capturable from the DOM; same as intercepting the deepseek backend for structured citations). Take the most complete response body.
        cites = []
        best_body = ""
        for r in chat_responses:
            try:
                b = await r.text()
            except Exception:  # noqa: BLE001
                continue
            if len(b) > len(best_body):
                best_body = b
        if best_body:
            try:
                cites = _parse_qwen_citations(best_body)
            except Exception:  # noqa: BLE001
                cites = []
        # Refusals (the fixed 24-char refusal, zero sources) follow the session, not the question: 29/30 terms refused in the last 30 days were answered normally
        # in other rounds, and even a purely factual question like 「示例清洁介绍」 ("intro to Example Cleaning") was refused once. So handle it like the slider -
        # on the first hit, open a new conversation on the same page and resend the original question once, saving a whole round of "back to the queue -> another worker
        # claims it -> starts a browser again"; only if refused a second time is it handed back as-is, and the center's refusal gate records it as an error row.
        if answer and _attempt == 1 and looks_refused(answer, cites):
            print("[qwen] 固定拒绝语(第 1 次),新开对话重发一次", flush=True)
            return await run(query, _attempt=2, _reuse=(page, ctx, chat_responses))
        # Success: capture the storage_state refreshed by this active use and write it back to the pool for renewal (qwen runs in a subprocess, not
        # through the hot EngineSession, so it must renew separately here - it is exactly the main victim of the 7-day hard TTL).
        fresh_state = None
        if answer:
            try:
                fresh_state = await ctx.storage_state()
            except Exception:  # noqa: BLE001
                fresh_state = None
        # The egress probe goes through the same proxy as this query (check-out already put the account's proxy into the contextvar)
        from app.browser import _engine_proxy
        report_session_outcome("qwen", result="success" if answer else "empty_answer",
                               exit_ip=_resolve_host_ip(_engine_proxy("qwen")) or None,
                               storage_state=fresh_state)
        out["answer"] = answer
        out["citations"] = cites
        out["reasoning"] = (await extract_reasoning(page, "qwen")) if answer else None
        out["error"] = None if answer else "empty_answer"
    except Exception as e:  # noqa: BLE001
        report_session_outcome("qwen", result="crash", error_msg=str(e)[:200])
        out["error"] = str(e)[:200]
    finally:
        # The retry layer already closed it once; closing again here is harmless (close is silent on an already closed ctx).
        try:
            await close_stealth_context(ctx)
        except Exception:  # noqa: BLE001
            pass
    return out


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "测试"
    res = asyncio.run(run(q))
    print(json.dumps(res, ensure_ascii=False))
