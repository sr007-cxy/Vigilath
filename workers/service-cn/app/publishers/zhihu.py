"""Zhihu publish adapter - the M1 technical validation target of the server_pool channel.

Selectors have been calibrated once on real pages (2026-08-12/13, 24 dry_runs in total). Those still marked
`TODO(calibrate)` are the ones that **cannot be triggered on demand**: rate-limit notices, content rejection notices, image upload -
they can only be fixed from screenshots once actually hit in production. Until then they simply "do not match" and fall into
submit_unknown, the most conservative level, never treating a failure as success.

Why Zhihu first: Aitoearn does not cover it + there is no official publish API, so a browser is the only way, and it is
the most representative long-form platform in server_pool. Design §7 open item 1 ("is the ban risk of server-side automatic publishing
on Zhihu acceptable") is the only route-level risk of the whole server_pool route - M1 exists to get that number.

🔴 Real publishing has **two gates**, both required:
    1. dry_run=False in the request
    2. env `ZHIHU_ALLOW_REAL_PUBLISH=1` on the browser-service side (unset by default = off)
  Why the second exists: publishing is the only irreversible action in this system, and the first is just a boolean field -
  one bad backend value, one dirty queue row, or one fat-fingered curl could make company accounts publish externally. Two gates make
  "can publish" a state explicitly enabled by ops, not a capability you get by flipping false to true in some request body.

Success is judged through **two observation points**, in fixed order:
    1. the address bar switches from /p/<id>/edit to /p/<id> (authoritative signal, url_source=url_redirect)
    2. after the redirect wait times out, check the creator center content list (url_source=creator_center_anchor) -
       on 2026-08-14 we hit a false negative once: the article was live while this reported submit_unknown.
       See the docstring of find_published_url_in_creator_center.
  Both points are **observations**; under no circumstances may a URL be constructed from draft_id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

from .base import (
    FAIL_BANNED,
    FAIL_CONTENT_REJECTED,
    FAIL_CRASH,
    FAIL_DOM_NOT_FOUND,
    FAIL_IDENTITY_MISMATCH,
    FAIL_LOGIN_LOST,
    FAIL_NOT_IMPLEMENTED,
    FAIL_RATE_LIMITED,
    FAIL_SUBMIT_UNKNOWN,
    FAIL_TIMEOUT,
    PublisherAdapter,
    PublishRequest,
    PublishResult,
    account_hash,
)

_log = logging.getLogger(__name__)


# ── Constants that need calibration ─────────────────────────────────────────────────
#
# TODO(calibrate): check each of the following against Zhihu's current page structure. How: run dry_run and look at
# the screenshots under data/snapshots/publish/zhihu/ + the returned fail_type; dom_not_found means
# the corresponding selector did not match.

# Identity API. Zhihu's /api/v4/me returns {id, url_token, name, ...}; url_token is the profile page
# slug, stable across logins, preferred over the numeric id.
ME_API = "https://www.zhihu.com/api/v4/me"

# Article editor. After creating a draft, the write-article page puts the draft id into the URL:
#   https://zhuanlan.zhihu.com/p/<draft_id>/edit
WRITE_URL = "https://zhuanlan.zhihu.com/write"
# TODO(calibrate): confirm the form of the draft id in the URL. Zhihu has historically used both /write?draftId=xxx and
# /p/<id>/edit; try both.
DRAFT_ID_PATTERNS = (
    re.compile(r"/p/(\d+)/edit"),
    re.compile(r"[?&]draftId=(\d+)"),
)

# TODO(calibrate): title input. Zhihu's article editor uses a textarea (not an input); the placeholder
# copy may be "请输入标题(最多 100 个字)" ("Enter a title (up to 100 characters)").
SEL_TITLE = "textarea[placeholder*='标题']"
# TODO(calibrate): body. Zhihu uses Draft.js; the editable area is a contenteditable div,
# so fill() cannot be used - must click to focus, then keyboard.type.
SEL_BODY = "div.public-DraftEditor-content[contenteditable='true']"
# Publish button. **Only clicked when dry_run=False.**
#
# 🔴 Corrected once by the 2026-08-13 DOM probe; do not change it back. It used to be
#     "button.PublishPanel-stepTwoBtn, button:has-text('发布')" ('发布' = "Publish")
# Both parts were wrong:
#   1. The class `PublishPanel-stepTwoBtn` **does not exist at all** on Zhihu's page; I guessed it assuming "publishing
#      is probably two steps". There is no PublishPanel component in the DOM - topics/columns are **in-page** controls
#      (the probe saw an inline 「添加话题」 ("Add topic") input in topicish), so clicking 「发布」 ("Publish") submits directly.
#   2. `:has-text()` is a **substring** match, and the bottom-left footer also has a 「发布设置」 ("Publish settings") button, which in the DOM
#      **comes before the real publish button**. So `.first` always selected 「发布设置」 ("Publish settings").
#      Consequence: in dry_run the "button visible and clickable" check examined that dropdown (always clickable),
#      and in real publishing clicking it would only expand the settings panel - nothing would ever get published, yet it would report submit_unknown.
#      21 green dry_runs did not expose it, because the green item tested the wrong element.
# `:text-is()` is an exact match, hitting only the one whose text is exactly 「发布」 ("Publish").
# No fallback to CSS-in-JS hash class names like css-d0uhtl - those change with every frontend release.
SEL_PUBLISH = "button:text-is('发布')"
# The **authoritative signal** of a successful publish: the URL changes from the edit state /p/<id>/edit to the article state /p/<id>.
#
# 🔴 Zhihu's draft_id equals the article id, so "constructing /p/<draft_id>" looks feasible -
# but it must never be done. That would report a published_url without observing anything, while the core
# invariant is "a task without published_url cannot go to published". A guessed URL would register a failed
# publish as a success, entering re-testing and the reward window, while the article does not exist on the platform. It must be **observed**.
PUBLISHED_URL_RE = re.compile(r"^https://zhuanlan\.zhihu\.com/p/(\d+)/?$")
# Creator center "Content management -> Articles" list. **The second observation point after the URL wait times out**.
#
# 🔴 Determined by testing on 2026-08-14; do not switch back to the API:
#   · anonymous/bare page.request to `/api/v4/articles/{id}` returns 403 code=10003 (blocked, not
#     "article does not exist"); judging by it would mark a live article as not published;
#   · `/api/v4/me/drafts` (drafts API) returns **404** - this API no longer exists;
#   · the author page `/people/{token}/posts` is blocked by anti-scraping, and the newly published article cannot be read from its list.
# Only the creator center, a **logged-in** page, is reliably readable, and it renders article links directly as anchors -
# so "getting the URL" is still **reading**, not constructing (see the PUBLISHED_URL_RE section above).
CREATOR_ARTICLES_URL = "https://www.zhihu.com/creator/manage/creation/article"
# Article links on the list page. Zhihu has both absolute zhuanlan domain links and site-relative /p/ here; collect both.
SEL_CREATOR_ARTICLE_LINKS = "a[href*='/p/']"
# Extract the article id from the anchor's href. **Only used to compare the read href against our own draft_id**,
# never to generate an href.
ANCHOR_ARTICLE_ID_RE = re.compile(r"/p/(\d+)")
# TODO(calibrate): notice copy for publish rejection / rate limiting. These two **cannot be triggered on demand**, so they are written
# from experience for now and will be fixed from screenshots once actually hit in production. Until then they simply "do not match" -> submit_unknown,
# the most conservative level, never misjudged as success.
SEL_RATE_LIMIT_HINT = ("text=操作过于频繁", "text=请稍后再试", "text=发布过于频繁")
SEL_CONTENT_REJECT_HINT = ("text=内容涉嫌违规", "text=含有敏感信息", "text=不符合社区规范",
                           "text=请修改后重新发布")
# The "Markdown 语法输入中" ("Markdown syntax input on") status indicator at the bottom of the editor.
# 🔴 When on, typed `**bold**` / leading `#` `-` `>` are **interpreted as formatting on the spot**;
# when off, they display literally. And this is **each account's own editor UI state**, not in the request and beyond
# our control - the same body is bold on account A and four asterisks on account B.
# This round does not change it, but **logs it on every publish**: turning an invisible uncertainty into a checkable fact.
# When some article's layout goes wrong in the future, the log can directly answer "which mode was it in".
SEL_MARKDOWN_MODE = "text=Markdown 语法输入中"
# TODO(calibrate): the file input for image upload (hidden input). This round's dry_run does not upload images.
SEL_IMAGE_INPUT = "input[type='file'][accept*='image']"

# Detection of not logged in / banned. 🔴 Logged out != banned (design §1.3); the two are handled completely differently:
# logged out -> automatic renewal/reminder to re-authorize; banned -> unschedulable + manual. So they must be judged separately.
# TODO(calibrate): copy per the real page.
SEL_LOGIN_WALL = "div.SignFlow, button:has-text('登录/注册')"
SEL_BANNED_HINT = ("text=账号状态异常", "text=账号已被封禁", "text=你的账号涉嫌违规")

_NAV_TIMEOUT_MS = 45_000
_SEL_TIMEOUT_MS = 15_000
# Budget for the **first** element after navigation - it is waiting for the SPA to mount, not for the element to appear. A cold-started chromium
# has no HTTP cache and must re-download the Zhihu editor bundle; 15s is not enough (see the comment in publish()).
_EDITOR_MOUNT_TIMEOUT_MS = 60_000
# Upper bound for dry_run waiting for the "publishable state" to converge. 15s because it must cover one draft autosave round trip -
# that round trip is exactly what caused "body visible but publish button disabled" on the 10th run in the first calibration round.
_PUBLISH_READY_TIMEOUT_SEC = 15.0
# Upper bound for waiting for the URL to become the article state after clicking "publish". Wider than elsewhere: the cost of a timeout is not "slow" but
# submit_unknown - that level requires someone to reconcile manually via draft_id in the platform backend, the most expensive failure.
_PUBLISH_COMMIT_TIMEOUT_MS = 60_000
# Budget for checking the creator center after the URL wait times out. Deliberately tight: it is **gap-filling**, not the main path -
# the main path has already spent 60s, and the whole /publish is wrapped by publish_api's PUBLISH_TIMEOUT (default 240s);
# a slower check would turn a "self-healable submit_unknown" into "a cancel without even a screenshot".
_RECHECK_NAV_TIMEOUT_MS = 20_000
# The list page is an SPA; entries are still loading after domcontentloaded. This is time for rendering, not a network wait.
_RECHECK_SETTLE_MS = 4_000


class ZhihuPublisher(PublisherAdapter):
    platform = "zhihu"
    login_url = "https://www.zhihu.com/signin"
    # Keep-alive navigation target (used by base.keepalive). Deliberately the home page rather than WRITE_URL:
    # opening the write-article page creates an empty draft on the platform every time; 80 accounts once a day means a pile of junk drafts,
    # and "frequently opening the editor without publishing" is itself suspicious behavior. Home page = an ordinary logged-in visit.
    home_url = "https://www.zhihu.com/"

    async def is_available(self) -> bool:
        """Platform reachability probe. Needs no login session, so /api/v4/me cannot be used."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                r = await c.get("https://www.zhihu.com/robots.txt")
            return r.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    # ── Identity ────────────────────────────────────────────────────────

    async def _me_profile(self, page) -> Optional[dict]:
        try:
            r = await page.request.get(ME_API, timeout=_SEL_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            _log.warning("[zhihu] /me request failed: %s", e)
            return None
        if r.status != 200:
            # 401/403 = ticket invalid (logged out), meaning nothing other than "cannot read the identity".
            # Return None uniformly here; publish() judges login_lost from context.
            _log.info("[zhihu] /me HTTP %s → treated as not logged in", r.status)
            return None
        try:
            d = await r.json()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(d, dict):
            return None
        return d

    async def verify_identity(self, page) -> Optional[str]:
        """Read the current login identity -> stable account_id hash; None if unreadable (treated as failure, not allowed through).

        🔴 **Trust the API, not the DOM.** `_is_logged_in` in qr_authorize.py:105 already fell into
        the trap of judging login from the DOM: it both misjudged (the login page has its own textarea -> thought logged in) and missed
        (qwen's QR iframe stays in the DOM after login -> thought not logged in).

        Uses page.request rather than a separate httpx client: page.request reuses the context's cookies
        and proxy, so it gets "who the platform thinks I am right now"; a separate client has neither the login session nor the proxy.
        """
        d = await self._me_profile(page)
        if not d:
            return None
        for f in ("url_token", "id"):
            v = d.get(f)
            if isinstance(v, (str, int)) and str(v).strip():
                # The prefix must match what the backend's stable_account_id_from_probe generates, otherwise
                # every verify would "mismatch". The backend uses f"api.me.{field}".
                return account_hash(f"api.me.{f}", str(v))
        return None

    async def account_handle(self, page) -> Optional[str]:
        """Read the Zhihu account nickname, only for account center display, not used in account_id comparison."""
        d = await self._me_profile(page)
        if not d:
            return None
        for f in ("name", "url_token", "id"):
            v = d.get(f)
            text = str(v).strip() if isinstance(v, (str, int)) else ""
            if text:
                return text[:64]
        return None

    def observed_draft_url(self, page, draft_id: Optional[str]) -> Optional[str]:
        """Only accept a Zhihu editor URL actually presented on the current page and consistent with draft_id."""
        if not draft_id:
            return None
        url = (page.url or "").strip()
        try:
            parsed = urlparse(url)
        except Exception:  # noqa: BLE001
            return None
        if parsed.scheme != "https" or parsed.hostname not in {
            "zhuanlan.zhihu.com", "www.zhihu.com",
        }:
            return None
        for pat in DRAFT_ID_PATTERNS:
            match = pat.search(url)
            if match and match.group(1) == str(draft_id):
                return url
        return None

    # ── Publish ────────────────────────────────────────────────────────

    async def publish(self, page, req: PublishRequest) -> PublishResult:
        """Execution order: verify identity -> enter the editor -> get draft_id -> fill the form -> (dry_run stops here)."""
        try:
            return await self._publish_inner(page, req)
        except asyncio.TimeoutError:
            return self._unknown_if_committed(
                req, FAIL_TIMEOUT, "publish timed out",
                await self.screenshot(page, "error-timeout"))
        except Exception as e:  # noqa: BLE001
            _log.exception("[zhihu] publish crashed")
            return self._unknown_if_committed(
                req, FAIL_CRASH, f"{type(e).__name__}: {e}",
                await self.screenshot(page, "error-crash"))
        # asyncio.CancelledError is deliberately **not** caught: it is a BaseException that `except Exception`
        # does not catch - let it propagate back to publish_api's wait_for, which reads the same req.commit to make
        # the same reclassification. Catching it here would be worse: after cancel the page is already closing, and a screenshot would throw again.

    def _unknown_if_committed(self, req: PublishRequest, clean_fail: str,
                              error: str, shot) -> PublishResult:
        """Wrap-up for the fallback excepts: once publish was clicked, it may not be judged retryable.

        🔴 These two excepts are the **last** safety net, and the net used to have a hole: they only looked at "what exception was thrown",
        not "was publish clicked before it was thrown". Step 7 (waiting for the article URL after clicking) is the slowest part of the whole flow,
        i.e. the most likely place to throw - and it is right after the click. Judging timeout/crash marks
        "an article already exists on the platform" as retryable, and the next claim publishes it a second time.

        And this hole would **override** the same defense on the publish_api side: that side only reads req.commit when it cannot get a PublishResult,
        while returning here amounts to giving an adapter conclusion, which takes precedence.
        """
        if not req.commit.get("clicked"):
            return PublishResult(ok=False, fail_type=clean_fail, error=error,
                                 screenshot_path=shot)
        draft_id = req.commit.get("draft_id")
        _log.error("[zhihu] 🔴 exception after publish was clicked (%s) - reclassified as submit_unknown, "
                   "draft_id=%s", error, draft_id)
        return PublishResult(
            ok=False, fail_type=FAIL_SUBMIT_UNKNOWN, draft_id=draft_id,
            draft_url=req.commit.get("draft_url"),
            error=f"已点击发布后异常收场({error});"
                  f"请凭 draft_id={draft_id} 对账,不要重试",
            screenshot_path=shot or req.commit.get("screenshot_path"))

    async def _body_for_editor(self, page, req: PublishRequest) -> tuple[str, str]:
        """Turn req.body into "text that can be typed character by character into the Zhihu editor", returning (text, decision note).

        🔴 This solves the only problem this round that **makes layout unpredictable**. The Zhihu editor has a
        "Markdown 语法输入中" ("Markdown syntax input on") status at the bottom: when on, typed `**bold**` / leading `#` `-` `>` are
        **interpreted as formatting on the spot**; when off, they display literally. And it is **each account's own UI state**, not in the request
        and beyond our control - the same body is bold on account A and four asterisks on account B; this inconsistency
        only surfaces when a second account goes live.

        So instead of "controlling" it, we **observe first, then adapt**, so the final layout is the same in both states:

        | req.body_format | markdown mode | approach |
        |---|---|---|
        | markdown | on | type as-is - formatting takes effect normally |
        | markdown | off | markers would not take effect; degrade to plain text (no pile of visible `**`) |
        | plain    | on | **escape** metacharacters so the text displays literally |
        | plain    | off | type as-is |
        | html     | any | strip tags, degrade to plain, then handle as above |

        html cannot take this path: per-character typing would type `<p>` as visible text. The correct form for Zhihu is
        markdown (see the zhihu row in seeds/publish_platforms.py); html is only a fallback.
        """
        body = req.body or ""
        fmt = (req.body_format or "plain").strip().lower()
        md_on = await _visible(page, SEL_MARKDOWN_MODE)

        if fmt == "html":
            # Strip tags. Loses formatting but not the body - publishing `<p>` is an irreversible error,
            # while a plain layout is merely unattractive.
            body, fmt = _strip_html(body), "plain"
            note = "html→plain(逐字符输入无法送 html 标签)"
        else:
            note = fmt

        if fmt == "markdown" and md_on:
            # Markers take effect, but the editor auto-continues lists / turns blank lines into extra empty paragraphs - see
            # the two measured defects in _adapt_markdown_for_editor.
            body = _adapt_markdown_for_editor(body)
            note += " / 已适配编辑器的自动续列表与段落行为"
        elif fmt == "markdown" and not md_on:
            body = _markdown_to_plain(body)
            note += " / markdown 模式**关闭**→ 降级纯文本(否则页面上是一堆可见的 ** 和 #)"
        elif fmt != "markdown" and md_on:
            body = _escape_markdown(body)
            note += " / markdown 模式**开启**→ 转义元字符,保证字面显示"

        _log.warning("[zhihu] body_format=%s markdown_mode=%s -> %s (%d chars)",
                     req.body_format, md_on, note, len(body))
        return body[:_BODY_MAX_CHARS], note

    async def _publish_inner(self, page, req: PublishRequest) -> PublishResult:
        # ── 1) Mandatory identity check, **before entering any editor page** ────────────────
        # F5 item 3. Prevents "publishing to the wrong account" - the failure ops fear most, and irreversible. On mismatch return immediately,
        # without typing a single character into the editor.
        observed = await self.verify_identity(page)
        if observed is None:
            return PublishResult(
                ok=False, fail_type=FAIL_LOGIN_LOST,
                error="无法读取当前登录身份(票据可能已失效)",
                screenshot_path=await self.screenshot(page, "login-lost"),
            )
        if req.account_id and observed != req.account_id:
            return PublishResult(
                ok=False, fail_type=FAIL_IDENTITY_MISMATCH,
                error="当前登录身份与任务指定账号不一致,已中止",
                observed_account_id=observed,
                screenshot_path=await self.screenshot(page, "identity-mismatch"),
            )

        exit_ip = await self.resolve_exit_ip(page)

        # ── 2) Enter the editor ────────────────────────────────────────────
        await page.goto(WRITE_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        await _human_pause(1.5)

        banned = await self._banned_hint(page)
        if banned:
            return PublishResult(
                ok=False, fail_type=FAIL_BANNED, error=f"平台提示账号异常:{banned}",
                observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "banned"),
            )
        # /me passed but the editor page shows a login wall - usually write permission was revoked; treat as logged out (self-healable),
        # not banned (banned is terminal and cannot recover automatically; misjudging is too costly).
        if await _visible(page, SEL_LOGIN_WALL):
            return PublishResult(
                ok=False, fail_type=FAIL_LOGIN_LOST, error="编辑页要求重新登录",
                observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "login-wall"),
            )

        # ── 3) 🔴 Get draft_id before filling the form ──────────────────────
        # Reconciliation of submit_unknown (the request may have reached the platform but the response timed out) relies **only** on this id.
        # An id recorded after publishing is unavailable in timeout scenarios, leaving only manual digging through the platform backend -
        # unacceptable at the scale of 80 accounts (design §3.3).
        draft_id = await self._wait_draft_id(page)
        if not draft_id:
            _log.warning("[zhihu] draft_id not obtained - submit_unknown will not be reconcilable")

        # ── 4) Fill in title / body ───────────────────────────────────────
        # 🔴 The title box is the **first** element to wait for after navigation, so what it really waits for is "has the SPA mounted",
        # not "is this selector right". Give it a separate, longer budget: in the second calibration round on 2026-08-12,
        # the first dry_run after a browser-service restart failed right here - in the screenshot the loading bar at the top of the page
        # had only reached 8%, the editor was not even mounted yet, and the generic 15s timeout was not enough for a cold start.
        #
        # The consequence of not fixing it is not just "occasionally red": dom_not_found has threshold=3 in the quarantine policy table,
        # so three cold starts can push a perfectly healthy account into quarantined. Wrongly killing good accounts is exactly the kind of mistake
        # the engine pool already made (docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md).
        title_el = await _first_visible(page, SEL_TITLE, timeout=_EDITOR_MOUNT_TIMEOUT_MS)
        if title_el is None:
            return PublishResult(
                ok=False, fail_type=FAIL_DOM_NOT_FOUND, error=f"标题输入框未找到({SEL_TITLE})",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "no-title-input"),
            )
        await title_el.click()
        # Per-character input instead of fill(): instant injection is a machine signature (same idea as design §F9 -
        # evenly spaced/instant actions are machine traits in themselves). The delay follows the same
        # BROWSER_DELAY_SCALE idea as browser.human_*; Playwright's delay parameter is used directly here.
        await title_el.type(req.title, delay=_type_delay_ms())

        await _human_pause(0.8)

        body_el = await _first_visible(page, SEL_BODY)
        if body_el is None:
            return PublishResult(
                ok=False, fail_type=FAIL_DOM_NOT_FOUND, error=f"正文编辑区未找到({SEL_BODY})",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "no-body-editor"),
            )
        await body_el.click()
        # Draft.js is a controlled component and does not accept fill()/innerHTML injection - keyboard events are required.
        # The body form (html/plain/markdown) and the editor's current markdown mode are aligned in
        # _body_for_editor; see that method's table.
        body_text, _note = await self._body_for_editor(page, req)
        if not body_text.strip():
            # The backend render_for_publish already blocks empty bodies; blocking again here because
            # _body_for_editor's degradation chain (strip html / strip markdown) may also strip the body to nothing -
            # publishing then would be an empty article, irreversibly.
            return PublishResult(
                ok=False, fail_type=FAIL_CONTENT_REJECTED,
                error=f"正文转换后为空(body_format={req.body_format},原始 "
                      f"{len(req.body or '')} 字符)",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "empty-body"),
            )
        await page.keyboard.type(body_text, delay=_type_delay_ms())

        await _human_pause(1.2)

        # draft_id may only appear after filling the form triggers autosave; fetch once more to fill it in
        if not draft_id:
            draft_id = await self._wait_draft_id(page, tries=2)

        # ── 5) dry_run: stop before clicking "publish" ────────────────────────────
        if req.dry_run:
            # 🔴 The threshold cannot be just "is the publish button there". In the first 10 calibration runs on 2026-08-12, the 10th
            # hit this: the body was visibly rendered on the page, but Zhihu's word count was 0, the preview button
            # was grayed out and the publish button was faded too - the editor's internal state had not picked up this input yet
            # (autosave happened to land in between). That run was still judged PASS because only "visible" was checked then.
            #
            # In this state a real publish would click a disabled button: either silently nothing happens -> waiting for
            # published_url times out -> submit_unknown (the worst kind, must never be blindly retried, only reconciled manually
            # via draft_id), or it throws outright. The whole point of dry_run is to catch this state **while it costs nothing
            # in ban risk**, so two conditions are added to the threshold: the body really entered the editor + the button is really
            # clickable.
            #
            # Wait for the state to converge before the screenshot: something that fixes itself within 2 seconds is not a failure, and only then is the
            # screenshot of a stuck state actually the stuck frame (it used to screenshot first and judge later, capturing the page from before the judgment).
            ready, why = await self._wait_publish_ready(page)
            shot = await self.screenshot(page, "before-publish")
            if os.environ.get("ZHIHU_DUMP_PUBLISH_DOM"):
                await self._dump_publish_dom(page)
            if not ready:
                return PublishResult(
                    ok=False, fail_type=FAIL_DOM_NOT_FOUND,
                    error=f"已到发布前一步,但页面没进入可发布状态:{why}",
                    draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                    screenshot_path=shot,
                )
            return PublishResult(
                ok=True, published_url=None, draft_id=draft_id,
                observed_account_id=observed, exit_ip=exit_ip, screenshot_path=shot,
            )

        # ── 6) Real publish ────────────────────────────────────────────
        #
        # 🔴 Double gate. dry_run=False is only the **first**; the browser-service side must also explicitly enable
        # ZHIHU_ALLOW_REAL_PUBLISH=1. Reason: this is the only irreversible action in the whole system, and its
        # trigger condition is a boolean field - one bad backend value, one dirty queue row, or one fat-fingered curl
        # could make company accounts publish externally. Two gates mean the "publish capability" is explicitly enabled by ops, not something
        # you get by flipping a false to true in some request body. Off by default.
        if not os.environ.get("ZHIHU_ALLOW_REAL_PUBLISH"):
            return PublishResult(
                ok=False, fail_type=FAIL_NOT_IMPLEMENTED,
                error="真实发布未启用:browser-service 侧需显式设 "
                      "ZHIHU_ALLOW_REAL_PUBLISH=1(这是不可撤回操作的第二道闸)",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "real-publish-disabled"),
            )

        # Same convergence check as dry_run - the body really entered the editor and the button is really clickable.
        # If not ready, **do not click**: clicking a disabled button yields submit_unknown, whereas here we know the
        # cause explicitly; reporting dom_not_found makes it retryable, far better than leaving a "may or may not have been published".
        ready, why = await self._wait_publish_ready(page)
        if not ready:
            return PublishResult(
                ok=False, fail_type=FAIL_DOM_NOT_FOUND,
                error=f"发布前状态未收敛,已中止(未点击发布):{why}",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "not-ready-aborted"),
            )

        pre_shot = await self.screenshot(page, "pre-commit")
        # Keep a separate screenshot of the frame before the click. The first question manual reconciliation of submit_unknown must answer is
        # "was the page right before clicking" - a post-click screenshot alone cannot answer it.
        _log.warning("[zhihu] about to click publish - irreversible. draft_id=%s account=%s pre_shot=%s",
                     draft_id, observed, pre_shot)
        # 🔴 Recorded on the caller-held dict, **must be before the click**. From this line on, this publish
        # can never again be judged "retryable" - even if the whole publish() is cut off by wait_for and
        # none of the returns below execute, publish_api can use it to reclassify as submit_unknown.
        req.mark_committed(
            draft_id=draft_id,
            draft_url=self.observed_draft_url(page, draft_id),
            screenshot_path=pre_shot,
        )
        try:
            await page.locator(SEL_PUBLISH).first.click(timeout=_SEL_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            # The click itself threw - the request almost certainly never reached the platform; judge dom_not_found (retryable)
            # rather than submit_unknown (not retryable). Misjudging "not sent" as "may have been sent" would leave this
            # draft stuck in the manual reconciliation queue, which costs more than one retry.
            return PublishResult(
                ok=False, fail_type=FAIL_DOM_NOT_FOUND,
                error=f"点击发布按钮失败:{type(e).__name__}: {e}",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "click-failed"),
            )

        # ── 7) Wait for the authoritative success signal: the URL becomes the article state ──────────────────────
        try:
            await page.wait_for_url(PUBLISHED_URL_RE, timeout=_PUBLISH_COMMIT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
            # Timed out. First look for an **explicit** rejection/rate-limit notice - with a notice it is not "unknown",
            # but known to have not gone out, so we can give the precise disposition of account innocent (content_rejected) or
            # cooldown (rate_limited).
            shot = await self.screenshot(page, "commit-timeout")
            # The check navigates the page away, so `page.url` afterwards reads the creator center - troubleshooting needs
            # "where it was at the moment of timeout", so save it first.
            stuck_url = page.url
            hint = await self._first_hint(page, SEL_RATE_LIMIT_HINT)
            if hint:
                return PublishResult(
                    ok=False, fail_type=FAIL_RATE_LIMITED, error=f"平台限流:{hint}",
                    draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                    screenshot_path=shot,
                )
            hint = await self._first_hint(page, SEL_CONTENT_REJECT_HINT)
            if hint:
                # 🔴 Penalize the content template, not the account - the account itself is fine, it can log in and write.
                return PublishResult(
                    ok=False, fail_type=FAIL_CONTENT_REJECTED, error=f"内容被拒:{hint}",
                    draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                    screenshot_path=shot,
                )
            banned = await self._banned_hint(page)
            if banned:
                return PublishResult(
                    ok=False, fail_type=FAIL_BANNED, error=f"发布时提示账号异常:{banned}",
                    draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                    screenshot_path=shot,
                )
            # ── The address bar did not switch, but that **does not mean** it was not published ─────────────────
            # Actually hit on 2026-08-14: the article was live (the creator center showed "发布于 5 分钟前" ("published 5 minutes ago")),
            # while this reported submit_unknown -> attempt maxed out + task failed, rescued only by manual verification +
            # a compensation script. So before handing over submit_unknown, **take one more look at the platform**.
            #
            # Deliberately placed after the three "definite bad news" checks: rate limiting/content rejection/ban are explicit verdicts
            # and should not be overridden by a list page check. Only reaching here is truly "unknown".
            recheck = await self.find_published_url_in_creator_center(
                page, draft_id=draft_id, title=req.title)
            if recheck.get("url"):
                _log.warning("[zhihu] address bar did not switch but the creator center shows this article - judged success. "
                             "url=%s draft_id=%s(%s)",
                             recheck["url"], draft_id, recheck.get("note"))
                return PublishResult(
                    ok=True, published_url=recheck["url"], draft_id=draft_id,
                    observed_account_id=observed, exit_ip=exit_ip,
                    url_source="creator_center_anchor",
                    # Use the check screenshot as evidence: it is the only proof that "the article is live". The one from the URL timeout
                    # (shot) only proves "it was still on the editor page at the time"; keeping it in the log is enough.
                    screenshot_path=recheck.get("screenshot") or shot,
                )
            _log.error("[zhihu] creator center check did not recognize this article either: %s", recheck.get("note"))

            # 🔴 Truly unknown. The click has gone out, and the platform may already have received it - this level **must never be blindly
            # retried**; retrying means publishing the same article twice. Hand it to the backend for reconciliation with draft_id (design §4 P0).
            _log.error("[zhihu] submit_unknown: publish was clicked but the article URL never appeared. "
                       "draft_id=%s url=%s", draft_id, stuck_url)
            return PublishResult(
                ok=False, fail_type=FAIL_SUBMIT_UNKNOWN,
                error=f"已点击发布但未等到文章 URL(当时停在 {stuck_url});"
                      f"创作中心回查也未认出({recheck.get('note')});"
                      f"请凭 draft_id={draft_id} 对账,不要重试",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=shot,
            )

        # ── 8) Got the URL ────────────────────────────────────────
        published_url = page.url
        m = PUBLISHED_URL_RE.match(published_url or "")
        if not m:
            # wait_for_url passed but re-reading does not match - meaning it redirected again in between. Better to report
            # submit_unknown than a published_url we are not sure of ourselves: the core invariant is
            # "a task without published_url cannot go to published", and conversely "a reported URL must
            # really be that article" holds just as well.
            return PublishResult(
                ok=False, fail_type=FAIL_SUBMIT_UNKNOWN,
                error=f"URL 已跳转但形态不符文章态:{published_url}",
                draft_id=draft_id, observed_account_id=observed, exit_ip=exit_ip,
                screenshot_path=await self.screenshot(page, "url-unexpected"),
            )
        _log.warning("[zhihu] publish succeeded url=%s draft_id=%s", published_url, draft_id)
        return PublishResult(
            ok=True, published_url=published_url, draft_id=draft_id,
            observed_account_id=observed, exit_ip=exit_ip,
            url_source="url_redirect",
            screenshot_path=await self.screenshot(page, "published"),
        )

    # ── Internal utilities ────────────────────────────────────────────────────

    async def find_published_url_in_creator_center(
        self, page, draft_id: Optional[str] = None,
        title: Optional[str] = None) -> dict:
        """**Read** this article's link from the creator center content list. Read-only, clicks no buttons.

        Returns `{"url", "note", "screenshot", "anchors"}`; when unreadable, `url` is None and
        `note` explains why - the caller uses it to decide between success and staying submit_unknown.

        **Why it is needed**: `submit_unknown` literally means "publish was clicked but the article URL never appeared",
        and on 2026-08-14 testing showed a **false negative**: the article was already live (the creator center showed "发布于
        5 分钟前" ("published 5 minutes ago")), yet the adapter reported submit_unknown because the address bar did not switch, so the queue attempt maxed out,
        the task was judged failed, and it was rescued only by manual login verification + a script backfilling the receipt. The address bar switch is a **sufficient
        but not necessary** condition; without this second observation point, every network blip costs a manual reconciliation.

        **Why "read" and not "construct"**: Zhihu's draft_id equals the article id, so `/p/{draft_id}` looks like
        the answer - but the core invariant forbids reporting any unobserved URL (see the section above
        PUBLISHED_URL_RE). What is taken here is **the anchor href the platform itself renders on the list page**; `draft_id` is only used
        to recognize which of several rows is ours, never to build a string.

        **Priority**: draft_id match > title match. A title match must be **unique** - when the same title
        was published twice, better return None for manual handling than fill the URL into the wrong article.
        """
        note_bits: list[str] = []
        want_id = (draft_id or "").strip() or None
        kw = (title or "").strip()[:12]
        if not want_id and len(kw) < 6:
            return {"url": None, "anchors": 0, "screenshot": None,
                    "note": "既没有 draft_id 也没有足够长的标题特征,无法在列表里认人 —— 不猜"}

        # 🔴 Measured 2026-08-14: **navigating from the editor page to the creator center always fails** - two real publishes
        # hit it twice in a row, both `Page.goto: net::ERR_ABORTED`. The editor has beforeunload, and
        # Chromium judges this navigation aborted outright (not a timeout); attaching a dialog accept listener does not help.
        #
        # This failure point is especially nasty: this method is called **only** "right after clicking publish, still on /p/<id>/edit",
        # i.e. it does not work 100% of the time in the only scenario that needs it - while in the stub self-test goto always succeeds,
        # so not a single assertion went red. In real publishing both articles were live, yet both reported submit_unknown.
        #
        # Fix: **open a new page in the same context** to read. Cookies / egress / fingerprint are exactly the same
        # (context-level), but the new page has no beforeunload; the editor page also stays as-is as evidence.
        # When a new page cannot be opened, fall back to navigating the original page + dialog accept - that path will most likely still be ERR_ABORTED,
        # but "tried and failed" is no worse than "did not try", and it keeps single-page contexts working.
        own_page = None
        target = page
        try:
            own_page = await page.context.new_page()
            target = own_page
        except Exception as e:  # noqa: BLE001
            note_bits.append(f"新开页失败({type(e).__name__}),退回当前页导航")

        def _accept(dlg) -> None:
            asyncio.ensure_future(dlg.accept())

        if target is page:
            page.on("dialog", _accept)
        try:
            await target.goto(CREATOR_ARTICLES_URL, wait_until="domcontentloaded",
                              timeout=_RECHECK_NAV_TIMEOUT_MS)
            await target.wait_for_timeout(_RECHECK_SETTLE_MS)
            anchors = await target.eval_on_selector_all(
                SEL_CREATOR_ARTICLE_LINKS,
                "els => els.map(e => ({href: e.href || '',"
                " text: (e.innerText || '').trim().slice(0, 80)}))")
            shot = await self.screenshot(target, "creator-center-recheck")
        except Exception as e:  # noqa: BLE001
            # A failed check **must not** change the conclusion - it can only "upgrade submit_unknown to success",
            # never the reverse. If it cannot open, treat it as not checked.
            return {"url": None, "anchors": 0, "screenshot": None,
                    "note": f"创作中心打不开({type(e).__name__}: {e})—— 未获得任何新信号"}
        finally:
            if target is page:
                try:
                    page.remove_listener("dialog", _accept)
                except Exception:  # noqa: BLE001
                    pass
            # The screenshot was already taken inside the try, so it can be closed here. Failing to close is fine (the context is closed as a whole afterwards),
            # but a successful observation **must not** turn into an exception because closing the page failed - that would bring back the false negative.
            if own_page is not None:
                try:
                    await own_page.close()
                except Exception:  # noqa: BLE001
                    pass
        anchors = [a for a in (anchors or []) if ANCHOR_ARTICLE_ID_RE.search(a.get("href") or "")]

        picked: Optional[str] = None
        if want_id:
            for a in anchors:
                m = ANCHOR_ARTICLE_ID_RE.search(a["href"])
                if m and m.group(1) == want_id:
                    picked = a["href"]
                    note_bits.append(f"列表页上读到 href 的文章 id == draft_id({want_id})")
                    break
        if picked is None and len(kw) >= 6:
            hits = [a["href"] for a in anchors if kw in (a.get("text") or "")]
            if len(hits) == 1:
                picked = hits[0]
                note_bits.append(f"draft_id 未命中,但标题特征 {kw!r} 在列表里唯一匹配")
            elif len(hits) > 1:
                return {"url": None, "anchors": len(anchors), "screenshot": shot,
                        "note": f"标题特征 {kw!r} 匹配到 {len(hits)} 行 —— 有歧义,不猜"}

        if picked is None:
            return {"url": None, "anchors": len(anchors), "screenshot": shot,
                    "note": f"创作中心可读({len(anchors)} 条文章链接)但没有一条是这篇"}

        # Only trim query/hash (those are tracking params); **do not change host or path** - rewriting amounts to constructing.
        url = picked.split("?")[0].split("#")[0]
        if not PUBLISHED_URL_RE.match(url):
            # Host is not zhuanlan (Zhihu also uses www.zhihu.com/p/<id> on-site). Return as-is and just log it:
            # the backend's mark_published does url_canonical normalization; rewriting it here on our own would be wrong.
            note_bits.append(f"注意:读到的 href 形态不是 zhuanlan 文章态({url})")
            if not ANCHOR_ARTICLE_ID_RE.search(url):
                return {"url": None, "anchors": len(anchors), "screenshot": shot,
                        "note": f"读到的 href 里没有 /p/<id>:{url}"}
        return {"url": url, "anchors": len(anchors), "screenshot": shot,
                "note": ";".join(note_bits)}

    async def _dump_publish_dom(self, page) -> None:
        """Log the DOM structure of the publish area. **Read-only, never clicks.**

        The biggest unknown of step 6 is "what happens after clicking publish" - does it go out directly, or first pop a topic/
        column selection panel to confirm? The answer decides whether the remaining calibration is free or costs a real article each time:
        if it is two steps, the first click only opens the panel and dry_run can continue; if it is one step, that click is the publish.
        Finding out by trying costs an article, finding out by reading costs nothing - so read first.
        Controlled by an env switch (ZHIHU_DUMP_PUBLISH_DOM=1); on the regular publish path the overhead is one dict lookup.
        """
        js = """
        () => {
          const out = {};
          const btns = Array.from(document.querySelectorAll('button'));
          const pub = btns.filter(b => (b.innerText || '').trim().match(/^发布/));
          out.publishButtons = pub.map(b => ({
            text: (b.innerText || '').trim(), cls: b.className,
            disabled: b.disabled, type: b.type,
            parentCls: b.parentElement ? b.parentElement.className : null,
            grandCls: b.parentElement && b.parentElement.parentElement
                      ? b.parentElement.parentElement.className : null,
          }));
          // 面板类组件是否**已经存在于 DOM 里**(哪怕隐藏)——存在通常意味着两步流程
          const pat = /Publish|publish-panel|PublishPanel|Modal|Popover/;
          out.panelish = Array.from(document.querySelectorAll('div,section,form'))
            .filter(e => pat.test(e.className || ''))
            .slice(0, 25)
            .map(e => ({ tag: e.tagName, cls: e.className,
                         hidden: !(e.offsetWidth || e.offsetHeight),
                         text: (e.innerText || '').slice(0, 60) }));
          // 话题 / 专栏选择器的痕迹
          out.topicish = Array.from(document.querySelectorAll('input,button,div'))
            .filter(e => /话题|专栏|收录/.test((e.getAttribute('placeholder')||'') + (e.innerText||'').slice(0,20)))
            .slice(0, 15)
            .map(e => ({ tag: e.tagName, cls: (e.className||'').slice(0,80),
                         text: (e.innerText||'').slice(0,40) }));
          out.url = location.href;
          return out;
        }
        """
        try:
            info = await page.evaluate(js)
            _log.warning("[zhihu][DOM-PROBE] %s",
                         json.dumps(info, ensure_ascii=False, indent=2))
        except Exception as e:  # noqa: BLE001
            _log.warning("[zhihu][DOM-PROBE] failed: %s", e)

    async def _wait_publish_ready(self, page) -> tuple[bool, str]:
        """Wait until the page really enters the "can click publish" state; returns (ready, reason if not ready).

        Checks two things, both required:
          1. The body really entered the editor - read the editor area's innerText, not Zhihu's "word count" label
             (that is the platform's own UI copy and may vanish in a redesign; innerText is the editor's fact).
          2. The publish button is **clickable** - is_enabled(), not just is_visible().
        """
        deadline = time.monotonic() + _PUBLISH_READY_TIMEOUT_SEC
        why = "未知"
        while True:
            try:
                txt = (await page.locator(SEL_BODY).first.inner_text(timeout=2000)) or ""
            except Exception:  # noqa: BLE001
                txt = ""
            if not txt.strip():
                why = f"正文编辑区读回来是空的({SEL_BODY})"
            else:
                try:
                    btn = page.locator(SEL_PUBLISH).first
                    if not await btn.is_visible(timeout=1200):
                        why = f"发布按钮不可见({SEL_PUBLISH})"
                    elif not await btn.is_enabled(timeout=1200):
                        why = "发布按钮是禁用态 —— 编辑器还没接住这次输入"
                    else:
                        return True, ""
                except Exception as e:  # noqa: BLE001
                    why = f"发布按钮判定失败:{type(e).__name__}"
            if time.monotonic() >= deadline:
                return False, f"{why}(等了 {_PUBLISH_READY_TIMEOUT_SEC:.0f}s)"
            await asyncio.sleep(0.5)

    async def _wait_draft_id(self, page, tries: int = 6) -> Optional[str]:
        """Wait for the draft id to appear in the URL. After entering /write Zhihu creates a draft asynchronously and rewrites the URL."""
        for _ in range(max(1, tries)):
            url = page.url or ""
            for pat in DRAFT_ID_PATTERNS:
                m = pat.search(url)
                if m:
                    return m.group(1)
            await asyncio.sleep(1.0)
        return None

    async def _banned_hint(self, page) -> Optional[str]:
        return await self._first_hint(page, SEL_BANNED_HINT)

    async def _first_hint(self, page, selectors: tuple) -> Optional[str]:
        """Return the first visible notice selector; None if none are visible.

        Short 800ms timeout each: these notices are toasts, and the check happens on the "something already went wrong" path;
        waiting long on each would drag one failure out to tens of seconds. Better to miss one - a miss falls into
        submit_unknown, the most conservative level, never treating a failure as success.
        """
        for sel in selectors:
            try:
                if await page.locator(sel).first.is_visible(timeout=800):
                    return sel
            except Exception:  # noqa: BLE001
                continue
        return None


# ── Module-level helpers ───────────────────────────────────────────────────


def _type_delay_ms() -> int:
    """Per-character input interval (milliseconds). Follows the same BROWSER_DELAY_SCALE knob as browser.py,
    so slowing down/speeding up the humanized rhythm has a single switch."""
    import os
    try:
        scale = float(os.environ.get("BROWSER_DELAY_SCALE", "1") or 1)
    except ValueError:
        scale = 1.0
    return max(5, int(35 * scale))


def _strip_html(body: str) -> str:
    """html -> plain text. Block-level tags become newlines, everything else is removed, then entities are decoded.

    Only used when body_format=html, as a **fallback**: the correct form for Zhihu is markdown. Per-character typing
    cannot send html, and typing `<p>` as visible text is an irreversible error, so losing formatting is preferable.
    """
    import html as html_mod
    txt = re.sub(r"(?i)<br\s*/?>", "\n", body or "")
    txt = re.sub(r"(?i)</(p|div|h[1-6]|li|blockquote|pre|tr)>", "\n\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html_mod.unescape(txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


# Line-leading markdown block markers: # heading / - * + list / 1. ordered / > quote / ``` code fence
_MD_BLOCK_RE = re.compile(r"^(\s*)(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s*|```.*$)")
# Inline markers: **bold** __bold__ *italic* _italic_ `code` ~~strikethrough~~
_MD_INLINE_RE = re.compile(r"(\*\*|__|~~|`)|(?<![\w*])[*_](?![\s*_])|(?<![\s*_])[*_](?![\w*])")
# Metacharacters to escape when markdown mode is off. Only **inline** ones are escaped; line-leading block markers are
# handled separately by _escape_markdown with a zero-width space (a backslash cannot escape a line-leading `#`/`-`).
_MD_ESCAPE_CHARS = "*_`~["
# Upper limit for one input. Per-character typing has a delay; an overly long body would drag one publish to minutes and hit
# PUBLISH_TIMEOUT (publish_api.py:54). Truncation is the backend body_limit's job; this is only a fallback.
_BODY_MAX_CHARS = 20_000


_MD_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_MD_QUOTE_RE = re.compile(r"^\s*>\s*")


def _adapt_markdown_for_editor(md: str) -> str:
    """Rewrite markdown into a form that is also correct when "typed character by character into an editor that auto-continues lists".

    🔴 Found from screenshots on 2026-08-13. Knowing markdown mode is on is **not enough** - the editor is
    WYSIWYG and reacts to keyboard events in its own way, and typing as-is hits two problems:

    1. **List auto-continuation**: after typing `- item one` and pressing Enter, the editor **itself** inserts the next bullet; our
       subsequently typed `- item two` then becomes **visible text** `- item two` inside that bullet. The symptom: the first list
       item is clean, and from the second on each carries an extra dash. So within a continuous list, only the first item keeps the marker.
    2. **Blank line = one extra Enter**: one Enter in the editor already makes a new paragraph, so `\\n\\n` creates an extra
       empty paragraph - visibly oversized gaps between paragraphs. So only one `\\n` is sent between paragraphs.
       But the **end** of a list keeps `\\n\\n`: the second Enter is exactly the "exit list" action; without it, the
       paragraph after the list gets stuffed into a bullet.

    Content inside code fences (```) passes through as-is: the `-` `>` inside are code, not markers.
    """
    lines = (md or "").splitlines()
    out: list[str] = []
    in_fence = False
    prev_kind: Optional[str] = None      # "list" | "quote" | "text" | None

    for ln in lines:
        fence = ln.lstrip().startswith("```")
        if in_fence and not fence:
            # Inside a code fence: pass through as-is; between lines is a single Enter
            out.append(("\n" if out else "") + ln)
            continue
        if fence:
            in_fence = not in_fence
            out.append(("\n" if out else "") + ln)
            prev_kind = "text"
            continue
        if not ln.strip():
            continue                      # blank lines are not sent directly; the joiner below decides

        lm = _MD_LIST_RE.match(ln)
        qm = None if lm else _MD_QUOTE_RE.match(ln)
        if lm:
            # 🔴 Ordered / unordered must be kept apart. If merged into one, `- item` followed by `1. item` would be treated as continuation lines
            # of the same list, and `1.` would be stripped as a redundant marker - losing the numbering for the whole list.
            kind = "list:ol" if lm.group(1)[0].isdigit() else "list:ul"
        else:
            kind = "quote" if qm else "text"
        listish = ("list:ol", "list:ul", "quote")

        if not out:
            sep = ""
        elif kind == prev_kind and kind in listish:
            # Continuation line of the same list/quote - the editor adds the marker automatically; we only send the content
            ln = ln[(lm or qm).end():]
            sep = "\n"
        elif prev_kind in listish and kind != prev_kind:
            sep = "\n\n"                  # double Enter exits the list/quote
        else:
            sep = "\n"                    # between ordinary paragraphs: one Enter makes a new paragraph
        out.append(sep + ln)
        prev_kind = kind

    return "".join(out)


def _markdown_to_plain(md: str) -> str:
    """markdown -> plain text (used when markdown mode is **off**).

    Same idea as the backend's aitoearn_publisher._md_to_plain, without importing it -
    browser-service is a separate service and cannot depend on the backend package.
    """
    out: list[str] = []
    for ln in (md or "").splitlines():
        s = ln.strip()
        if s.startswith("```"):
            continue                       # the code fence itself is not content
        m = _MD_BLOCK_RE.match(ln)
        if m:
            marker = m.group(2)
            s = ln[m.end():].strip()
            if re.match(r"^[-*+]\s+$", marker) or re.match(r"^\d+\.\s+$", marker):
                s = "· " + s
        out.append(_MD_INLINE_RE.sub("", s))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _escape_markdown(text: str) -> str:
    """Make plain text display literally in an editor with markdown mode **on**.

    Inline metacharacters get a backslash; a zero-width space (U+200B) is inserted before line-leading block markers - a backslash cannot escape a line-leading
    `#` / `-`, while the zero-width space keeps the line from starting with a marker and is invisible.
    """
    out: list[str] = []
    for ln in (text or "").splitlines():
        esc = "".join(f"\\{c}" if c in _MD_ESCAPE_CHARS else c for c in ln)
        if _MD_BLOCK_RE.match(ln):
            esc = "​" + esc.lstrip()
        out.append(esc)
    return "\n".join(out)


async def _human_pause(seconds: float) -> None:
    import os
    import random
    try:
        scale = float(os.environ.get("BROWSER_DELAY_SCALE", "1") or 1)
    except ValueError:
        scale = 1.0
    # ±30% jitter: a fixed interval is itself a machine signature
    await asyncio.sleep(max(0.05, seconds * scale * random.uniform(0.7, 1.3)))


async def _visible(page, selector: str, timeout: int = 1200) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout)
    except Exception:  # noqa: BLE001
        return False


async def _first_visible(page, selector: str, timeout: int = _SEL_TIMEOUT_MS):
    """Wait until the selector is visible and return the locator; None on timeout (the caller judges dom_not_found)."""
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:  # noqa: BLE001
        return None
