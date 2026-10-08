"""PublisherAdapter - abstract base class for the "write" capability of the self-built account pool.

browser-service originally could only **read** (/search, /fetch-title). This directory is where it learns to **write** -
the design (docs/03-系统设计/内容与发布/多平台多账号分发-开发设计方案.md) §F3 says this is the only part of the whole project that needs to be built new.

Written like the neighboring `EngineAdapter` in `app/engines/base.py:42`: ABC + @abstractmethod +
dataclass result body, one file per platform, registered via the string table in `publishers/__init__.py` +
importlib (isomorphic with main.py:38's ENGINE_MODULE_MAP).

Three hard rules, from the P0 pitfall list in design §4:

1. **dry_run must be supported** - go through login, form filling and upload, stop before clicking "publish" and take a screenshot.
   Without dry_run, selectors cannot be debugged without polluting real accounts. dry_run costs nothing in ban risk,
   while every real publish does.
2. **`verify_identity` returns None when it cannot read the identity, treated as failure, not allowed through** - without identity verification
   we would publish to the wrong account, the failure ops fear most, and it cannot be undone.
3. **`draft_id` must be obtained before clicking publish** - an id recorded only after publishing is unavailable in timeout scenarios,
   so submit_unknown cannot be reconciled and someone has to dig through the platform backend manually.
4. **Call `req.mark_committed(draft_id)` before clicking publish** - see `PublishRequest.commit`.
   `publish()` gets cut off by `asyncio.wait_for`, and when cut off its return value does not exist; the caller can only rely on this
   trace to know "the platform has been touched", otherwise a real publish would be judged a retryable timeout.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)

# Keep-alive navigation timeout. Looser than the selector timeouts in the publish flow: keep-alive has no time pressure, and home pages behind proxies
# being slow is normal - judging too early would report good accounts as unreachable, wasting the trip.
_KEEPALIVE_NAV_TIMEOUT_MS = 30_000


# ── Identity hash ───────────────────────────────────────────────────────
#
# 🔴 This implementation must be **byte-identical** to `_account_hash` in the backend's `backend/geo/models/engine_sessions.py:134`:
# the backend uses it to compute publish_accounts.account_id, and here it computes verify_identity's
# return value; the two must be directly comparable with ==. browser-service is a separate service and cannot import the backend
# package, so copying is the only option. **A change only counts if both sides are changed.**
def account_hash(field_name: str, value: str) -> str:
    """account_id format: "<source field>:<first 16 of sha256>" - no plaintext stored, source traceable.

    The field-name prefix is key: hashes of different sources/values are never equal, so the worst result of extracting wrongly/failing to extract
    is only degradation, never wrongly merging two accounts into one.
    """
    return f"{field_name}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


# ── Request / result ────────────────────────────────────────────────────


@dataclass
class PublishRequest:
    platform: str
    account_id: str                       # expected identity, compared in verify_identity
    title: str
    body: str                             # html / plain / markdown already adapted for the platform
    # 🔴 Which form the body is in. The adapter **must** decide based on this how to feed it into the editor -
    # guessing wrong does not just make the layout ugly, it publishes `<p>` or `##` as visible text, irreversibly.
    # The backend decides it via publish_platforms.content_format (services/publish_render.py).
    body_format: str = "plain"
    images: list[str] = field(default_factory=list)
    dry_run: bool = False
    # ── Submit trace ──────────────────────────────────────────────────
    #
    # 🔴 The adapter **must** write an entry here **before clicking the publish button** (`mark_committed`).
    #
    # Why PublishResult alone is not enough: `publish()`'s return value only exists when it **finishes**, and it
    # can be cut off externally - publish_api wraps it in `asyncio.wait_for(..., PUBLISH_TIMEOUT)`.
    # At the moment of timeout `publish()` is cancelled, the coroutine stack vanishes, and a PublishResult is never produced.
    # If that moment happens to be after the click (the tens of seconds waiting for the article URL are exactly the slowest part of the whole flow),
    # the result is: an article already exists on the platform, while the system gets a "retryable" timeout.
    #
    # This dict is held by the **caller**, so cancelling publish() does not affect it - the caller can still read
    # "publish was clicked" and change the conclusion to submit_unknown (never retried, reconciled via draft_id).
    # It is the only "output parameter" field in the dataclass, deliberately: it must outlive publish().
    commit: dict = field(default_factory=dict)

    def mark_committed(self, draft_id: Optional[str] = None,
                       draft_url: Optional[str] = None,
                       screenshot_path: Optional[str] = None) -> None:
        """Call **before** clicking the publish button. After that, this publish may never again be judged "retryable"."""
        self.commit["clicked"] = True
        if draft_id:
            self.commit["draft_id"] = draft_id
        if draft_url:
            self.commit["draft_url"] = draft_url
        if screenshot_path:
            self.commit["screenshot_path"] = screenshot_path


@dataclass
class PublishResult:
    ok: bool
    published_url: Optional[str] = None
    draft_id: Optional[str] = None        # 🔴 the only basis for submit_unknown reconciliation
    draft_url: Optional[str] = None       # the editor page actually observed; never construct it from draft_id
    fail_type: Optional[str] = None       # aligned with PublishFailureType (backend models/publish_accounts.py)
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    exit_ip: Optional[str] = None
    # The identity verify_identity actually read. When it does not match req.account_id the backend needs to see who was read,
    # otherwise troubleshooting only knows "mismatch" but not "matched whom", and cannot tell whether the account was swapped or the probe broke.
    observed_account_id: Optional[str] = None
    # **How** published_url was observed. The two sources differ in trustworthiness, while the DB stores the same string,
    # indistinguishable afterwards - so the source must travel with the URL into the proof.
    #
    #   "url_redirect"          after publishing the browser address bar itself switched to the article state (strongest, authoritative signal)
    #   "creator_center_anchor" after the URL wait timed out, the anchor href **read** from the creator center content list
    #
    # 🔴 No third source like "construct" is allowed: no observation, no URL (see the section above
    # PUBLISHED_URL_RE in zhihu.py). One purpose of this field is to leave "construction" nowhere to hide -
    # to fill in a URL you must first state where you saw it.
    url_source: Optional[str] = None
    failure_phase: Optional[str] = None
    confirmed_not_published: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "published_url": self.published_url,
            "draft_id": self.draft_id,
            "draft_url": self.draft_url,
            "fail_type": self.fail_type,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
            "exit_ip": self.exit_ip,
            "observed_account_id": self.observed_account_id,
            "url_source": self.url_source,
            "failure_phase": self.failure_phase,
            "confirmed_not_published": self.confirmed_not_published,
        }


# String constants aligned with the backend PublishFailureType. Only those adapters produce are listed here -
# the backend also has exit_ip_drift (determined by comparing exit_ip at receipt time, not produced by adapters).
FAIL_IDENTITY_MISMATCH = "identity_mismatch"
FAIL_LOGIN_LOST = "login_lost"
FAIL_BANNED = "banned"
FAIL_RATE_LIMITED = "rate_limited"
FAIL_CONTENT_REJECTED = "content_rejected"
FAIL_SUBMIT_UNKNOWN = "submit_unknown"
FAIL_DOM_NOT_FOUND = "dom_not_found"
FAIL_TIMEOUT = "timeout"
FAIL_CRASH = "crash"
FAIL_NOT_IMPLEMENTED = "not_implemented"


class PublisherAdapter(ABC):
    platform: str = "unknown"
    # Entry opened when the account center starts a web login. Empty means this adapter does not support interactive web login yet.
    login_url: str = ""
    # Where to navigate for keep-alive / health checks. **Must be an ordinary logged-in browsing page**, not the editor, not an API -
    # keep-alive is meant as "low-frequency active use", and opening the write-article page leaves a pile of empty drafts on the platform.
    # Filled by subclasses; if empty, keepalive() directly judges unreachable (no silent degradation into "read the API only").
    home_url: str = ""

    @abstractmethod
    async def verify_identity(self, page) -> Optional[str]:
        """Read the current login identity and return the account_id hash; None if unreadable (treated as failure, not allowed through).

        Implementation constraint: **trust the platform API/tickets, not the DOM**. `_is_logged_in` in qr_authorize.py:105
        already fell into the trap of judging login from the DOM - it both misjudged (the login page has its own textarea)
        and missed (qwen's QR iframe stays in the DOM after login).
        """

    async def account_handle(self, page) -> Optional[str]:
        """Read the display nickname of the current logged-in account; None if unreadable.

        The nickname is only for account center display and is not used in identity comparison. Not provided by default, so old adapters
        do not end up affecting the publish safety chain for the sake of a display field.
        """
        return None

    @abstractmethod
    async def publish(self, page, req: PublishRequest) -> PublishResult:
        """Run one publish. When req.dry_run=True it must stop before clicking "publish" and take a screenshot."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether the platform is currently reachable (no login session needed)."""

    def observed_draft_url(self, page, draft_id: Optional[str]) -> Optional[str]:
        """Return the draft editor URL actually presented. Not supported by default; adapters must not guess-construct it from the id."""
        return None

    # ── Shared utilities (subclasses use directly, no need to override) ────────────────────────────

    async def keepalive(self, page) -> tuple[bool, Optional[str]]:
        """One keep-alive pass: navigate to home_url, then read the login identity once.

        Returns `(reachable, account_id)`:

          reachable=False  -> **did not reach the platform** (proxy down / network down / site 5xx).
                             🔴 Callers **must never** judge login_lost from this.
          reachable=True, account_id=None
                           -> reached the platform, and the platform said "I do not know you" = authoritative login loss.
          reachable=True, account_id=<hash>
                           -> the account is still alive. The caller rolls the TTL forward and writes back the refreshed login session.

        Why navigate first and then read the identity (instead of calling verify_identity directly):
        the contract of `verify_identity` is `Optional[str]`, and **cannot read** and **cannot connect** look identical
        in its return value (in zhihu.py's implementation, `page.request.get` throwing also returns None), while
        publish_api's verify-identity route maps every None to `login_lost`. So one network outage
        gets reported as "logged out" - exactly why 9 good qwen accounts were wrongly locked up by over-aggressive login_lost classification
        (docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md), and what F6 acceptance item 2
        ("a login_lost caused by an artificial network outage must not mark the account expired") guards against.

        A successful navigation is independent evidence that "the platform is reachable", giving verify_identity's None its meaning -
        so this order is not a matter of style; do not reverse it.

        Also: navigation is a **real logged-in page visit**, and the platform rotates ticket cookies in its responses,
        which is the prerequisite for "renewal" getting a new storage_state. Calling only the API without navigating does not necessarily rotate cookies.
        """
        if not self.home_url:
            _log.warning("[publish] %s has no home_url configured, cannot keep alive", self.platform)
            return False, None
        try:
            await page.goto(self.home_url, wait_until="domcontentloaded",
                            timeout=_KEEPALIVE_NAV_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            _log.warning("[publish] %s keepalive navigation failed (judged unreachable, not logged out): %s",
                         self.platform, e)
            return False, None
        return True, await self.verify_identity(page)

    async def screenshot(self, page, tag: str) -> Optional[str]:
        """Save a screenshot and return the relative path; None on failure (a screenshot failure should not fail the whole publish).

        F10 audit trail: every publish must keep failure screenshots - both a compliance requirement and the only thing that can
        reconstruct "what the page looked like at the time" when troubleshooting.
        """
        from .paths import screenshot_path
        try:
            path, rel = screenshot_path(self.platform, tag)
            await page.screenshot(path=str(path), full_page=False)
            return rel
        except Exception:  # noqa: BLE001
            return None

    async def resolve_exit_ip(self, page) -> Optional[str]:
        """Resolve the actual egress IP from the page context.

        It must be resolved **in the page context** (not by a direct request from the worker process), otherwise it gets the worker's
        own IP rather than this context's egress through the proxy - the latter is what the platform sees, and also
        the basis for judging exit_ip_drift at receipt time.
        """
        for url in ("https://api.ipify.org?format=json", "https://myip.ipip.net/json"):
            try:
                r = await page.request.get(url, timeout=8000)
                if r.status != 200:
                    continue
                d = await r.json()
                ip = d.get("ip") or (d.get("data") or {}).get("ip")
                if isinstance(ip, list):
                    ip = ip[0] if ip else None
                if ip:
                    return str(ip)[:64]
            except Exception:  # noqa: BLE001
                continue
        return None
