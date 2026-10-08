"""EngineSession — D4 hot browser model.

Each EngineSession holds a long-lived (page, ctx) pair + a binding to one pool session.
query() runs on the already-loaded page (reset chat -> input -> wait -> extract),
instead of launch + goto + setup every time, saving ~10-15s per query.

Lifecycle:
  worker startup -> instantiate an EngineSession per engine -> init() checks out a session
                                                             + launch browser
                                                             + runs the preparation steps
  per query     -> query(text) reuses the hot (page, ctx), returns EngineResult
  session failure -> rotate(reason: FailureType) close + check-in old + check-out new + re-init
  worker shutdown -> close() closes the browser + checks in the session

Concurrency safety: each EngineSession has an internal asyncio.Lock; one instance runs only 1
query at a time. Concurrency needs a pool of M instances (D7 P3 = phase C).

Adapter protocol: an engine adapter must provide two new methods:
  - _prepare_page(page, ctx) -> None  (goto, banner, smart search toggle and other "get ready" work)
  - _query_with_page(page, ctx, query) → EngineResult  (reset + input + wait + extract)
If the adapter does not implement them, EngineSession.init() raises NotImplementedError.

The D4 POC only wires up deepseek; the other 4 engines keep the old one-shot search() flow.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from .browser import create_stealth_page, close_stealth_context
from .engines.base import EngineAdapter, EngineResult
from .session_store import report_session_outcome, renew_checked_out_session, get_last_checkout_id


_log = logging.getLogger(__name__)

# Number of automatic rotate (account switch) + retry attempts on login session loss / DOM failure. Set 0 to disable "switch on failure".
# Default 1: a broken hot session is automatically checked in and retried once with a new session.
AUTO_ROTATE_RETRY = int(os.environ.get("BROWSER_AUTO_ROTATE_RETRY", "1"))
# Periodic rotation: proactively rotate after every N successful queries so accounts in the pool take turns and no single account is overworked
# (an account hit hundreds of times is most likely to trigger risk control / login loss). Set 0 to disable periodic rotation.
ROTATE_EVERY_N = int(os.environ.get("BROWSER_ROTATE_EVERY_N", "30"))
# IP circuit breaker: after this many consecutive "new account still fails immediately" switches -> judged not an account problem (most likely the IP is banned /
# the engine is down); stop switching accounts (avoid feeding every account into the blacklist) and back off, probing at low frequency with the current account only.
IP_BAN_ROTATE_FAILS = int(os.environ.get("BROWSER_IP_BAN_ROTATE_FAILS", "3"))
# Backoff seconds after the breaker trips; no rotate meanwhile, still probing with the current session, recovering on success. Default 15 minutes.
BREAKER_COOLDOWN_SEC = float(os.environ.get("BROWSER_BREAKER_COOLDOWN_SEC", "900"))
# Which failure types trigger automatic rotate - only self-heal failures that "switching accounts might fix";
# captcha / timeout / crash are unlikely to be fixed by switching accounts, so do not waste quota; leave them to the quarantine policy.
#
# empty_answer is also included (added 2026-08-24): the day the whole Doubao account pool died, the adapter returned
# **an empty answer + error=None**, which triggered no account switch at all, so every query dutifully waited the full 120s
# and came back empty-handed, and none of the dead accounts in the pool got switched out. An empty answer is the signal that "this session is dead".
# Switching is capped (AUTO_ROTATE_RETRY=1), and the IP circuit breaker is the fallback for repeated failures, so the pool will not be drained.
_ROTATE_ON = {
    e.strip() for e in os.environ.get(
        "BROWSER_ROTATE_ON", "login_lost,dom_not_found,empty_answer").split(",") if e.strip()
}
# Post-rebuild self-check: after rotate switches to a new account and init completes, run one probe query to confirm it really produces answers,
# otherwise it "switches to an equally dead account and keeps going" - exactly what happened with Doubao today (3 switches, all dead accounts).
ROTATE_PROBE = os.environ.get("BROWSER_ROTATE_PROBE", "1") == "1"
ROTATE_PROBE_QUERY = os.environ.get("BROWSER_ROTATE_PROBE_QUERY", "你好")
# Marker words when the page is kicked back to the logged-out home page. A hit with zero citations and a very short body -> login_lost (switch accounts),
# rather than returning it as a normal answer. Same metric definition as V-networks/workers/desktop-cn/worker.py and the telemetry center-side gate.
_HOMEPAGE_MARKERS = ("为你推荐", "下载豆包电脑版", "有什么我能帮你的吗")
_HOMEPAGE_MAX_LEN = int(os.environ.get("BROWSER_HOMEPAGE_MAX_LEN", "400"))
# Rolling renewal throttle: on a successful query, write storage_state back only if more than this many seconds have passed since the last renewal
# (avoids a check-in per query). Set 0 to disable "renewal during use" and write back only on close. Default 6h.
RENEW_INTERVAL_SEC = float(os.environ.get("BROWSER_SESSION_RENEW_INTERVAL_SEC", str(6 * 3600)))


class EngineSession:
    def __init__(self, engine_name: str, adapter: EngineAdapter):
        self.engine_name = engine_name
        self.adapter = adapter
        self.page = None
        self.ctx = None
        self.query_count = 0
        self.last_used_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._initialized = False
        # Number of queries succeeded since the last rotate (periodic rotation triggers at ROTATE_EVERY_N)
        self._queries_since_rotate = 0
        # Consecutive count of "switched to a new account and still failed immediately" (IP breaker trips at IP_BAN_ROTATE_FAILS)
        self._rotate_fail_streak = 0
        # IP breaker backoff deadline (epoch seconds); when > now, only probe, do not switch accounts
        self._breaker_until = 0.0
        # Last time storage_state was written back to the pool for renewal (epoch seconds); throttled by RENEW_INTERVAL_SEC
        self._last_renew_at = time.time()
        # The following fields are only for snapshot() reporting so the center can see whether hot sessions are alive; they do not affect scheduling
        self._last_rotate_at: Optional[float] = None
        self._last_rotate_reason: Optional[str] = None
        self._rotate_count = 0
        # None = not verified yet; True/False = conclusion of the latest post-switch self-check or query
        self._login_ok: Optional[bool] = None
        # Account id issued by the pool. Only the check-out slot knows it, and check-in pops it, so record it at init
        # and send it back to the center with every result onto the response - troubleshooting can aggregate per account, not just per machine.
        self.session_id: Optional[int] = None

    # ── Adapter capability check ─────────────────────────────────
    def _has_hot_protocol(self) -> bool:
        return (
            hasattr(self.adapter, "_prepare_page")
            and hasattr(self.adapter, "_query_with_page")
        )

    # ── Lifecycle ────────────────────────────────────────────────
    async def init(self) -> None:
        """Launch browser + load session + adapter._prepare_page.

        Adapters may optionally implement _create_hot_page (returning (page, ctx)) to override the default
        create_stealth_page call - Doubao must use it since it goes through cloakbrowser + headed.
        """
        if not self._has_hot_protocol():
            raise NotImplementedError(
                f"engine {self.engine_name} 没实现 hot protocol "
                "(_prepare_page + _query_with_page)."
            )
        async with self._lock:
            if self._initialized:
                return
            t0 = time.time()
            if hasattr(self.adapter, "_create_hot_page"):
                self.page, self.ctx = await self.adapter._create_hot_page()
            else:
                self.page, self.ctx = await create_stealth_page(self.engine_name)
            self.session_id = get_last_checkout_id(self.engine_name)
            await self.adapter._prepare_page(self.page, self.ctx)
            self._initialized = True
            _log.info("[hot-session] %s init OK in %.1fs (account=%s)",
                      self.engine_name, time.time() - t0, self.session_id)

    async def query(self, query_text: str) -> EngineResult:
        """Reuse hot page to run one query.

        Concurrent requests are serialized (_lock). init() must have completed before calling, otherwise raises.

        Three-layer account switching strategy:
        1. Periodic rotation (ROTATE_EVERY_N): proactively rotate every N successes so accounts in the pool take turns,
           no single account overworked - an account hit hundreds of times is most likely to trigger risk control / login loss.
        2. Switch on failure (AUTO_ROTATE_RETRY): on login_lost / dom_not_found, retry with a new account,
           fixing the problem of a hot session whose login died sitting there failing every query and never switching automatically.
        3. IP circuit breaker (IP_BAN_ROTATE_FAILS): several new accounts in a row still fail immediately -> judged not
           an account problem (most likely the IP is banned / the engine is down); stop switching (otherwise every account would be fed into
           the blacklist), back off for BREAKER_COOLDOWN_SEC, probing at low frequency with the current account only, recovering on success.
        """
        if not self._initialized:
            await self.init()

        now = time.time()
        breaker_open = now < self._breaker_until

        # 1) Periodic rotation - only when healthy (breaker not tripped)
        if ROTATE_EVERY_N > 0 and self._queries_since_rotate >= ROTATE_EVERY_N and not breaker_open:
            _log.info("[hot-session] %s periodic rotate after %d queries",
                      self.engine_name, self._queries_since_rotate)
            try:
                await self.rotate(reason="periodic")
                self._queries_since_rotate = 0
            except Exception as e:  # noqa: BLE001
                _log.warning("[hot-session] %s periodic rotate failed: %s", self.engine_name, e)

        attempts = AUTO_ROTATE_RETRY + 1
        result: Optional[EngineResult] = None
        rotated = False                                  # whether this call actually switched accounts
        for attempt in range(attempts):
            result, fail_type = await self._query_once(query_text)
            if fail_type == "login_lost":
                self._login_ok = False
            if fail_type is None:                        # success
                self._queries_since_rotate += 1
                self._rotate_fail_streak = 0
                self._breaker_until = 0.0                # probe succeeded -> clear the breaker
                self._login_ok = True
                return result
            if fail_type not in _ROTATE_ON:
                return _stamp_fail(result, fail_type)    # failure that switching cannot fix -> return directly
            if attempt + 1 >= attempts:
                break                                    # retries exhausted
            if breaker_open:
                _log.info("[hot-session] %s %s but IP-breaker open, probe-only (no rotate)",
                          self.engine_name, fail_type)
                return _stamp_fail(result, fail_type)    # breaker tripped: do not switch, only probe
            _log.warning("[hot-session] %s %s on query, auto-rotating (attempt %d/%d)",
                         self.engine_name, fail_type, attempt + 1, attempts - 1)
            try:
                await self.rotate(reason=fail_type)
                self._queries_since_rotate = 0
                rotated = True
            except Exception as e:                       # noqa: BLE001
                _log.warning("[hot-session] %s auto-rotate failed: %s", self.engine_name, e)
                self._note_rotate_didnt_help()           # could not get a new account (pool empty/all dead)
                return _stamp_fail(result, fail_type)
        # Still failing after switching and retrying -> this switch did not help; accumulate, and trip the IP breaker when enough
        if rotated:
            self._note_rotate_didnt_help()
        return _stamp_fail(result, fail_type)

    def _note_rotate_didnt_help(self) -> None:
        """Record one "switching did not help"; IP_BAN_ROTATE_FAILS consecutive times -> trip the IP breaker."""
        self._rotate_fail_streak += 1
        if self._rotate_fail_streak >= IP_BAN_ROTATE_FAILS:
            self._breaker_until = time.time() + BREAKER_COOLDOWN_SEC
            self._rotate_fail_streak = 0
            _log.error(
                "[hot-session] %s: %d consecutive account switches still failing - IP likely banned / engine down, "
                "breaker open for %.0fs, no more switching (avoid feeding all accounts into the blacklist); probing only meanwhile.",
                self.engine_name, IP_BAN_ROTATE_FAILS, BREAKER_COOLDOWN_SEC,
            )

    async def _query_once(self, query_text: str) -> tuple[EngineResult, Optional[str]]:
        """Run one query (holding _lock). Returns (result, failure_type); failure_type=None on success.

        Failure signals are reported to the pool (check-in accounting drives the quarantine policy) - keeping the original metric definition:
        both paths (result.error / exception) report to the pool by the _classify_error type,
        and the classified failure_type is returned to the upper layer for the automatic rotate decision.
        """
        async with self._lock:
            t0 = time.time()
            try:
                result = await self.adapter._query_with_page(self.page, self.ctx, query_text)
                self.query_count += 1
                self.last_used_at = time.time()
                _log.info(
                    "[hot-session] %s query #%d in %.1fs ans_len=%d err=%s",
                    self.engine_name, self.query_count, time.time() - t0,
                    len(result.answer or ""), result.error,
                )
                if result.error:
                    ft = _classify_error(result.error)
                    report_session_outcome(self.engine_name, result=ft)
                    return result, ft
                # Two kinds of "fake success" with error=None but invalid content, both treated as failures that switch accounts:
                #   a) the page was kicked back to the logged-out home page and what was scraped is the 「为你推荐 …」 ("Recommended for you") slot
                #   b) simply an empty answer (this was the form the day Doubao accounts died on 2026-08-24)
                ft = _fake_success_type(result)
                if ft:
                    _log.warning("[hot-session] %s fake success judged as %s (ans_len=%d cites=%d)",
                                 self.engine_name, ft, len(result.answer or ""),
                                 len(result.citations or []))
                    report_session_outcome(self.engine_name, result=ft)
                    return result, ft
                # Success: per the throttle period, write the storage_state refreshed by active use back to the pool for renewal.
                # Do not pop the check-out slot (the session is still hot; it is checked in normally on close).
                if RENEW_INTERVAL_SEC and (time.time() - self._last_renew_at) >= RENEW_INTERVAL_SEC:
                    try:
                        state = await self.ctx.storage_state()
                        renew_checked_out_session(self.engine_name, state)
                    except Exception as e:
                        _log.warning("[hot-session] %s failed to capture storage_state for renewal: %s",
                                     self.engine_name, e)
                    self._last_renew_at = time.time()
                return result, None
            except Exception as e:
                _log.exception("[hot-session] %s query crashed: %s", self.engine_name, e)
                # Classify and report by exception message (rather than always 'crash'), otherwise login_lost /
                # dom_not_found signals raised by the adapter are lost and the pool cannot quarantine by type (bad sessions with lost login get drawn repeatedly).
                ft = _classify_error(str(e))
                report_session_outcome(self.engine_name, result=ft, error_msg=str(e))
                err = EngineResult(engine=self.adapter.name, query=query_text, error=str(e))
                return err, ft

    async def rotate(self, reason: str = "manual") -> None:
        """Close current ctx + check-in session + re-init with fresh session.

        After the rebuild, run one probe query as a self-check (ROTATE_PROBE): raise if no answer, so the upper layer counts it as
        "this switch did not help" and moves toward the IP breaker - otherwise it keeps running with an equally dead new account,
        each query waiting out the full timeout and coming back empty-handed (that is what happened when Doubao switched through 3 dead accounts on 2026-08-24).
        """
        async with self._lock:
            _log.info("[hot-session] %s rotate reason=%s", self.engine_name, reason)
            await self._close_inner(check_in_result=reason)
            self._initialized = False
            self._last_rotate_at = time.time()
            self._last_rotate_reason = reason
            self._rotate_count += 1
        # Release the lock before calling init (init takes the lock itself, avoiding deadlock)
        await self.init()
        if ROTATE_PROBE and reason != "probe":
            await self._probe_after_rotate()

    async def _probe_after_rotate(self) -> None:
        """Post-switch self-check: run one probe query, raise if no answer (handled by rotate's caller)."""
        result, ft = await self._query_once(ROTATE_PROBE_QUERY)
        if ft is None and (result.answer or "").strip():
            self._login_ok = True
            _log.info("[hot-session] %s post-switch self-check passed (ans_len=%d)",
                      self.engine_name, len(result.answer or ""))
            return
        self._login_ok = False
        raise RuntimeError(
            f"{self.engine_name} 换号自检未通过(fail_type={ft} "
            f"ans_len={len(result.answer or '') if result else 0}) —— 新号大概率同样是死的"
        )

    def snapshot(self) -> dict:
        """Hot session state for heartbeat reporting. The center uses it to show login session/rebuild status on the dashboard and alert."""
        now = time.time()
        return {
            "engine": self.engine_name,
            "initialized": self._initialized,
            "session_id": self.session_id,
            "login_ok": self._login_ok,
            "query_count": self.query_count,
            "queries_since_rotate": self._queries_since_rotate,
            "rotate_count": self._rotate_count,
            "last_rotate_reason": self._last_rotate_reason,
            "last_rotate_ago_sec": (
                int(now - self._last_rotate_at) if self._last_rotate_at else None),
            "rotate_fail_streak": self._rotate_fail_streak,
            "breaker_open": now < self._breaker_until,
            "breaker_remain_sec": max(0, int(self._breaker_until - now)),
            "last_used_ago_sec": (
                int(now - self.last_used_at) if self.last_used_at else None),
        }

    async def close(self) -> None:
        """Worker shutdown: close ctx + check-in session as SUCCESS."""
        async with self._lock:
            await self._close_inner(check_in_result="success")
            self._initialized = False

    async def _close_inner(self, check_in_result: str) -> None:
        fresh_state = None
        if self.ctx is not None:
            # On a successful wrap-up, first capture the storage_state refreshed by active use and write it back with check-in for renewal.
            # It is unavailable after closing ctx, so it must be captured before close.
            if check_in_result == "success":
                try:
                    fresh_state = await self.ctx.storage_state()
                except Exception:
                    fresh_state = None
            try:
                # Adapters may optionally implement _close_hot_page(page, ctx) for special cleanup such as cloakbrowser
                # (Doubao has headed_browser + headed_pw to close).
                if hasattr(self.adapter, "_close_hot_page"):
                    await self.adapter._close_hot_page(self.page, self.ctx)
                else:
                    await close_stealth_context(self.ctx)
            except Exception:
                pass
            self.ctx = None
            self.page = None
        report_session_outcome(self.engine_name, result=check_in_result,
                               storage_state=fresh_state)
        self.session_id = None


def _fake_success_type(result: EngineResult) -> Optional[str]:
    """A "fake success" with error=None but invalid content returns the failure type to assign; a real success returns None.

    - Home page fallback (home page word hit + zero citations + very short body) -> login_lost, switch accounts
    - Empty answer -> empty_answer
    All three criteria must hold to count as the home page, so normal answers that happen to mention these words are not wrongly killed.
    """
    ans = (result.answer or "").strip()
    if not ans:
        return "empty_answer"
    if (not result.citations
            and len(ans) < _HOMEPAGE_MAX_LEN
            and any(m in ans[:200] for m in _HOMEPAGE_MARKERS)):
        # The wording contains "login lost" so _classify_error recognizes it; do not change
        return "login_lost"
    return None


def _stamp_fail(result: Optional[EngineResult], fail_type: Optional[str]):
    """Write the internally determined failure type back to result.error before handing it to the upper layer.

    These places used to `return result` directly - and a "fake success" (empty answer / fell back to home page) has error
    None to begin with, so the center received done + empty answer + error=NULL: **the hot session itself knew something was wrong
    (that is exactly why it switched accounts), but the center knew nothing**, so there was no failure count, no last_fail_type,
    no redesign warning, just a silent hole in the metrics.

    On 2026-08-30 Baidu swapped in the 「任务模式」 ("task mode") onboarding popup, and Wenxin had 20-25% empty answers for two days with zero alerts;
    it was silenced right here - the popup was only the trigger, what actually hid it for two days was this return value.

    Only written when error is empty; it never overrides an error reported by the engine itself (that one is more specific).
    """
    if result is not None and fail_type and not (result.error or "").strip():
        result.error = fail_type
    return result


def _classify_error(err_msg: str) -> str:
    """Best-effort mapping of the error string reported by the adapter to FailureType."""
    e = (err_msg or "").lower()
    # Risk-control rate limiting/muting comes first: it is neither a bad account nor a redesign, and further down it would be misjudged as empty_answer /
    # dom_not_found, accumulating to the threshold -> quarantining a good account that was only temporarily muted. The pool's policy for rate_limited
    # is cooldown (also cooling sibling accounts on the same egress IP), no quarantine, no fail_counts penalty.
    if ("rate limit" in e or "rate_limited" in e or "muted" in e
            or "太频繁" in e or "限流" in e or "请稍后" in e or "繁忙" in e):
        return "rate_limited"
    # Only **explicit** login-loss wording counts. "textarea/input not visible" used to count as login_lost,
    # but invisible controls are more often caused by redesigns/slow loading, and login_lost is immediate quarantine
    # (account dead after one occurrence) - better to classify as dom_not_found (threshold 3 + redesign alert), handled by the branch below.
    if "login lost" in e or "login may have expired" in e or "session expired" in e:
        return "login_lost"
    if "captcha" in e or "验证" in e:
        return "captcha"
    if "timeout" in e or "超时" in e:
        return "timeout"
    # Controls not found/not visible all go here: quarantined only after 3 occurrences, with an alert (possible engine redesign).
    # Do not fall into crash - crash uses the skip policy (not counted) and would swallow the "engine redesign" signal.
    if "not found" in e or "not visible" in e or "selector" in e:
        return "dom_not_found"
    # Resident page stuck: Wenxin "new-chat reset failed … refusing to send query into stale session".
    # On 2026-09-14 the test host reported it 90 times from 14:00 to 17:00 with 22 failed jobs, until the service was manually restarted - because it was classified as
    # crash, and crash is not in _ROTATE_ON, so the broken page was never replaced. Such errors are fixed by a new page/account, so treat it as dom_not_found
    # and go through "switch on failure" (a new session rebuilds the page).
    if "stale session" in e or "reset failed" in e:
        return "dom_not_found"
    return "crash"
