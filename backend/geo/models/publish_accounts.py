"""Publishing account pool -- accounts / platform catalog / dispatch queue for the self-hosted pool (server_pool channel).

Design basis: `docs/03-系统设计/内容与发布/多平台多账号分发-开发设计方案.md` section 3.2.

**Why a new table instead of reusing `engine_sessions`**: the semantics are opposite -- the engine pool is **read-heavy, concurrent,
retryable on failure**; the publish pool is **write-light, strictly serial, irreversible on failure**. Merging would let check-out filters
contaminate each other (the engine pool's "least used first" rotation is harmful for publish accounts, which should stay as stable as possible).

Columns largely mirror `EngineSessionORM` (engine_sessions.py:28 in the same dir), with four deliberate differences:

  storage_state        -> 🔴 stored encrypted (engine pool is plaintext). A leaked engine cookie only allows queries;
                          a leaked publish cookie allows **publishing as someone else** -- different blast radius.
  leased_by_worker_id  -> owner_worker_id lifetime binding, **no account-grab fallback** (see section "claim protocol").
  preferred_worker_id  -> removed (replaced by owner_worker_id).
  proxy_override       -> removed. Publish accounts must use the proxy table (proxy_id); raw plaintext strings are not allowed.

The `status` value set deliberately deviates from the state-machine diagram in design section 1.3: **`rate_limited` / `leased`
are not status values**; they are expressed via the `rate_limited_until` / `lease_until` columns, and
`effective_status()` derives the display state from the design diagram. Reason: the engine pool proved that "a status column + timestamp columns
encoding the same thing" fight each other (both the TTL deadlock and misjudgment incidents in `docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md`
were of this kind).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)

from geo.database import Base
# Identity hash reuses the engine pool implementation directly -- format "<source field>:<first 16 of sha256>", no plaintext stored,
# source traceable, and because of the field-name prefix, different sources/values never collide (extraction failure only degrades, never merges two accounts).
from geo.models.engine_sessions import _account_hash

# -- Enum values (string constants, no DB-level CHECK -- consistent with existing tables) ------------

# Channel: one real account belongs to exactly one channel. channel is a **fixed attribute of the row**, not an
# optional route per publish -- switching channel = explicit migration (retire old row + create new row). Reason for the hard binding, see design section 1.2:
# the three channels obtain account identity from different sources (self-hosted pool hashes the cookie / Aitoearn gives its own account id /
# plugin gives the platform nickname); mixing them breaks the idempotency unique constraint, showing up as the same article published twice on one account.
CHANNEL_SERVER_POOL = "server_pool"   # Our own DB (encrypted cookies) + browser automation
CHANNEL_AGGREGATOR = "aggregator"     # Aitoearn-side authorization
CHANNEL_PLUGIN = "plugin"             # Operator's local browser plugin, fallback path
ALL_CHANNELS = (CHANNEL_SERVER_POOL, CHANNEL_AGGREGATOR, CHANNEL_PLUGIN)

# Account status. rate_limited / leased are not listed here (see module docstring).
ACCOUNT_STATUSES = (
    "pending_auth",     # Registered, but authoritative identity not yet resolved from the login session
    "warming",          # Warm-up period: behavioral transition from manual to automated publishing, rate limited
    "active",
    "quarantined",      # Consecutive failures; can return to active after a self-heal probe passes
    "unschedulable",    # 🔴 Egress IP drift / suspected ban -- the only branch that cannot auto-recover, requires manual action
    "retired",          # Manually retired (e.g. channel migration)
)

# Egress form. The real invariant is "egress IP is constant and equals the egress at login"; a proxy is just one way to achieve it --
# what enforces it is the exit_ip comparison in the receipt (exit_ip_drift branch in api/publish_dispatch.py),
# independent of whether a proxy is used.
EGRESS_PROXY = "proxy"   # Bound to one row in engine_proxies; production form
EGRESS_HOST = "host"     # Direct: egress is the public IP of the machine running the browser
ALL_EGRESS = (EGRESS_PROXY, EGRESS_HOST)

# Dispatch task status
DISPATCH_STATUSES = ("queued", "claimed", "running", "done", "failed", "abandoned")


class PublishAccountORM(Base):
    """One owned account on one platform = one row. Unique constraint (platform, account_id)."""
    __tablename__ = "publish_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "account_id", name="uq_publish_accounts_platform_account"),
        # claim hot path: filter by platform + status + owning worker
        Index("idx_publish_accounts_claim", "platform", "status", "owner_worker_id"),
        # Sibling cooldown: rate limiting is per IP, not per account -- need to quickly find accounts sharing an egress
        Index("idx_publish_accounts_egress", "proxy_id", "exit_ip"),
    )

    id = Column(Integer, primary_key=True)
    platform = Column(String, nullable=False, index=True)     # zhihu / toutiao / baijia / ...
    # Stable account identity hash. Authoritative source is the platform-side stable id probed by browser-service verify-identity
    # (Zhihu = url_token from /api/v4/me); offline registration degrades to a hash of the ticket cookie value (see
    # provisional_account_id's docstring -- that kind of id **does not take part** in verify_identity comparison).
    account_id = Column(String, nullable=True)
    account_handle = Column(String, nullable=True)            # Nickname, display only

    # Channel (immutable after creation)
    channel = Column(String, nullable=False, default=CHANNEL_SERVER_POOL)

    # -- Login session ----------------------------------------------
    # 🔴 Ciphertext. The _enc suffix is deliberate: makes "plaintext written here" visible at code review.
    # Encryption/decryption via geo/utils/publish_crypto.py (Fernet, dedicated key PUBLISH_CRED_KEY).
    storage_state_enc = Column(Text, nullable=True)
    credentials_enc = Column(Text, nullable=True)             # Encrypted JSON, for auto-renewal
    auth_type = Column(String, nullable=True)                 # cookie / qr / sms
    user_agent = Column(String, nullable=True)
    ua_platform = Column(String, nullable=True)               # "MacIntel" / "Linux x86_64"

    # -- Ownership --------------------------------------------------
    # 🔴 Credential owner. Whose name the existing account's phone number is bound to determines renewal cost: bound to an employee's personal phone,
    # every re-authorization after a logout needs that person present, so "unattended" is fake; only a company phone range makes it truly unattended.
    # Must be recorded at registration to make this cost explicit.
    credential_owner = Column(String, nullable=False, default="")
    group_id = Column(Integer, ForeignKey("publish_account_groups.id", ondelete="SET NULL"),
                      nullable=True)

    # -- State machine ----------------------------------------------
    status = Column(String, nullable=False, default="pending_auth")
    expires_at = Column(DateTime, nullable=True)              # 🔴 Rolling TTL: N days after the last **successful use**
    # 🔴 Warm-up does not mean "nurturing a new account" but "**behavioral transition from manual to automated publishing**".
    # An account that has published manually for two years suddenly publishing on a fixed cadence is itself
    # a risk-control signal. During warm-up, rate limit, keep some manual publishing, and mix automated/manual.
    warmup_until = Column(DateTime, nullable=True)
    warmup_daily_cap = Column(Integer, nullable=True)         # None=use env default, 0=unlimited
    rate_limited_until = Column(DateTime, nullable=True)      # 429 cooldown expiry
    unschedulable_until = Column(DateTime, nullable=True)     # Expiry of manual temporary disable. **Different from status='unschedulable'**
    last_fail_type = Column(String, nullable=True)
    # Human-readable explanation of the latest account anomaly. Only redacted platform errors, for the account center to explain bans/logouts;
    # passwords, cookies and storage_state must never be written here.
    last_fail_reason = Column(Text, nullable=True)
    last_fail_at = Column(DateTime, nullable=True)
    fail_counts_json = Column(Text, nullable=False, default="{}")

    # -- Lease ------------------------------------------------------
    # 🔴 owner_worker_id is a lifetime hard binding, **no account-grab fallback**. The engine pool check-out's third-level fallback
    # ("if all are taken, grab one") was removed for the same reason -- see api/engine_sessions.py:518-521:
    # grabbing an account leased by another worker puts the same account on two egress IPs at once, triggering remote-login/IP
    # density risk control. Publishing is stricter: TLS fingerprint, font list, timezone and kernel features differ across machines,
    # which is a **subtler and harder** ban signal than IP drift.
    owner_worker_id = Column(Integer, nullable=True)
    lease_worker_id = Column(Integer, nullable=True)          # Current lease holder (normally == owner)
    lease_until = Column(DateTime, nullable=True)

    # -- Quota ------------------------------------------------------
    use_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)
    used_today = Column(Integer, nullable=False, default=0)
    used_date = Column(String, nullable=True)                 # 'YYYY-MM-DD' (Beijing date)
    daily_cap = Column(Integer, nullable=True)                # None=use env default, 0=unlimited
    # Minimum gap between two successful publishes; None=use global PUBLISH_MIN_GAP_SEC, 0=no limit.
    cooldown_minutes = Column(Integer, nullable=True)
    priority = Column(Integer, nullable=False, default=50)    # Lower value first
    concurrency = Column(Integer, nullable=False, default=1)  # 🔴 Concurrency unit is the account, always 1

    # -- Egress -----------------------------------------------------
    # Reuses the existing proxy table engine_proxies (models/engine_sessions.py:88), no new proxy table --
    # change one proxy_url and all bound accounts follow automatically.
    proxy_id = Column(Integer, ForeignKey("engine_proxies.id", ondelete="RESTRICT"), nullable=True)
    # 'proxy' (bound proxy_id) / 'host' (direct, see EGRESS_HOST comment) / None=legacy row
    egress = Column(String, nullable=True)
    exit_ip = Column(String, nullable=True)                   # Resolved at authorization, compared on every receipt afterwards
    exit_ip_at = Column(DateTime, nullable=True)

    identity_probe_json = Column(Text, nullable=True)         # verify_identity config/result snapshot
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # No tenant_id / owner_org_id: all accounts are company-owned, no multi-tenancy (design section zero).


class PublishAccountGroupORM(Base):
    """Account group. Exists only to give publish_accounts.group_id referential integrity; carries no business logic."""
    __tablename__ = "publish_account_groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PublishPlatformORM(Base):
    """Platform capability matrix -- **config driven**; adding a platform is an INSERT, no code change or restart.

    Follows SentimentPlatformORM (models/sentiment.py:135). Design section 4 P1 item 12:
    hardcoding platform capabilities -> every new platform needs a code change + restart.

    Zero semantics of numeric columns follow the engine pool daily_cap convention: **0 = unlimited**.
    """
    __tablename__ = "publish_platforms"
    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True, index=True)   # zhihu / toutiao / ...
    name_zh = Column(String, nullable=False, default="")
    channel = Column(String, nullable=False, default=CHANNEL_SERVER_POOL)
    supports_article = Column(Boolean, nullable=False, default=True)
    title_limit = Column(Integer, nullable=False, default=0)
    body_limit = Column(Integer, nullable=False, default=0)
    image_min = Column(Integer, nullable=False, default=0)
    image_max = Column(Integer, nullable=False, default=0)
    needs_cover = Column(Boolean, nullable=False, default=False)
    content_format = Column(String, nullable=False, default="html")  # html / plain
    # List of the platform's ticket cookie names (JSON array). Login is judged **by ticket cookies, not the DOM** --
    # _is_logged_in in qr_authorize.py:105 already hit this pitfall.
    identity_cookies_json = Column(Text, nullable=False, default="[]")
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublishDispatchTaskORM(Base):
    """Dispatch queue for the self-hosted pool (pull model: workers claim tasks).

    **Does not reuse** telemetry-service dispatch_tasks: quota dimensions differ (there per engine,
    here per platform+account), and its existing share/reserve accounting already caused one
    "false lockup" incident -- do not add new dimensions to it (design section 1.1).

    Division of labor with topic_doc_publish_tasks: that table is the **business state machine** (planned -> ... -> published,
    the core invariant "no published_url means not published" is enforced there); this table is only the **execution queue**
    (who is running, which attempt, when visible). One business task maps to one queue row.
    """
    __tablename__ = "publish_dispatch_tasks"
    __table_args__ = (
        Index("idx_publish_dispatch_claim", "status", "platform", "visible_after", "id"),
        Index("idx_publish_dispatch_account", "account_db_id", "status"),
        Index("idx_publish_dispatch_task", "task_id"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("topic_doc_publish_tasks.id", ondelete="CASCADE"),
                     nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    platform = Column(String, nullable=False)
    account_db_id = Column(Integer, ForeignKey("publish_accounts.id", ondelete="CASCADE"),
                           nullable=False)
    status = Column(String, nullable=False, default="queued")
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    # If no account can be claimed, push the task back **without consuming retry budget** (attempt not incremented). Mirrors telemetry dispatch's
    # visible_after backoff, so 3s polling does not immediately re-claim the same bad task in a hot loop.
    visible_after = Column(DateTime, nullable=False, default=datetime.utcnow)
    claimed_by_worker_id = Column(Integer, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    # 🔴 The only basis for submit_unknown reconciliation. Must be obtained and persisted to DB **before clicking publish** -- an id
    # recorded after publishing is unavailable on timeout, leaving only manual lookup in the platform backend (unacceptable at 80 accounts).
    draft_id = Column(String, nullable=True)
    # Draft editor URL actually observed by the platform browser. Must not be guessed from draft_id; empty for legacy tasks.
    draft_url = Column(String(1024), nullable=True)
    screenshot_path = Column(String, nullable=True)   # Failure/dry_run screenshot (F10 audit trail)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# -- Account identity extraction ----------------------------------------------
#
# Two kinds of id with completely different semantics; do not mix them:
#
#   stable_account_id()       Authoritative identity. browser-service opens a browser through the account's proxy,
#                             reads the stable id from the platform API -> hash. **This is the definition of account_id**,
#                             and what verify_identity compares before every publish.
#   provisional_account_id()  Placeholder identity. Hash of the ticket cookie value. Only for offline registration without a browser.
#
# Why the cookie hash alone is not enough: ticket cookies rotate (Zhihu z_c0 does), changing the hash after rotation;
# using it as account_id would make verify_identity block 100% falsely -- and verify_identity is the third layer of the F5 triple idempotency
# protection, guarding against "publishing to the wrong account", the failure operators fear most and cannot undo.

# Ticket cookies are calibrated per platform. This is only the fallback default when the DB is not configured;
# the authoritative source is publish_platforms.identity_cookies_json (just INSERT to add a platform).
_TICKET_COOKIES: dict[str, tuple[str, ...]] = {
    "zhihu": ("z_c0",),        # Zhihu login ticket. d_c0 is a device id, not identity; do not use it
    "wxgzh": ("slave_user", "bizuin", "slave_sid"),
    "xhs": ("web_session",),
    "douyin": ("sessionid", "sid_tt", "uid_tt"),
}

# Fields in the platform API response treated as the "stable user id", in priority order.
# Zhihu /api/v4/me -> url_token (profile slug, stable across logins) takes priority over the numeric id.
_STABLE_ID_FIELDS = ("url_token", "id", "user_id", "userId", "uid", "openid", "open_id")


def ticket_cookies(platform: str, configured: Optional[str] = None) -> tuple[str, ...]:
    """Ticket cookie names for the platform. configured = raw publish_platforms.identity_cookies_json."""
    if configured:
        try:
            names = json.loads(configured)
            if isinstance(names, list):
                out = tuple(str(n) for n in names if str(n).strip())
                if out:
                    return out
        except Exception:  # noqa: BLE001 -- broken config falls back to code defaults; do not fail the whole registration
            pass
    return _TICKET_COOKIES.get(platform, ())


def _cookie_dicts(storage_state: dict) -> list[dict]:
    cookies = storage_state.get("cookies") if isinstance(storage_state, dict) else None
    if not isinstance(cookies, list):
        return []
    return [c for c in cookies if isinstance(c, dict)]


def provisional_account_id(platform: str, storage_state: dict,
                           configured_cookies: Optional[str] = None) -> Optional[str]:
    """Placeholder identity: hash of the ticket cookie value; returns None if not found.

    🔴 Only for offline registration with resolve_identity=False (table setup/claim tests without a browser).
    Ticket cookies rotate, so this id **does not take part** in verify_identity comparison -- accounts holding it
    stay in pending_auth and need one real identity resolution before becoming active.
    """
    cookies = {c.get("name"): c.get("value")
               for c in _cookie_dicts(storage_state)
               if c.get("name") and c.get("value")}
    for name in ticket_cookies(platform, configured_cookies):
        v = cookies.get(name)
        if v:
            return _account_hash(f"prov.cookie.{name}", str(v))
    return None


def stable_account_id_from_probe(probe: Any) -> Optional[str]:
    """Authoritative identity: hash the platform-side id probed by verify_identity into account_id.

    probe can be the account_id string returned directly by browser-service (already hashed, returned as-is),
    or raw API JSON (fields picked and hashed here). Both paths exist because the registration flow over HTTP
    receives a finished hash, while offline scripts/tests may feed raw JSON directly.
    """
    if isinstance(probe, str):
        s = probe.strip()
        # Already in "<field>:<16 hex>" form -> use as-is, do not hash twice
        return s or None
    if not isinstance(probe, dict):
        return None
    for f in _STABLE_ID_FIELDS:
        v = probe.get(f)
        if isinstance(v, (str, int)) and str(v).strip():
            return _account_hash(f"api.me.{f}", str(v))
    return None


def has_login_cookies(platform: str, storage_state: dict,
                      configured_cookies: Optional[str] = None) -> bool:
    """Whether a ticket cookie exists -- the minimum bar at registration, rejecting imports that "never logged in".

    Mirrors engine_sessions.upload rejecting storage_state without cookies. When a platform has no calibrated ticket
    cookie, degrade to "any cookie passes", so missing calibration does not block onboarding new platforms.
    """
    names = {c.get("name") for c in _cookie_dicts(storage_state) if c.get("name")}
    wanted = ticket_cookies(platform, configured_cookies)
    if not wanted:
        return bool(names)
    return any(n in names for n in wanted)


# -- Failure signals and handling policy -------------------------------------


class PublishFailureType(str, Enum):
    """Result of this publish reported by the worker in the receipt.

    Reuses FailureType (models/engine_sessions.py:233) semantics + publish-specific types.
    Handling per type: see _PUBLISH_QUARANTINE_POLICY below.
    """
    SUCCESS = "success"

    # 🔴 Logged out != banned (design section 1.3). Confusing them kills good accounts -- 6 of 8 Wenxin accounts were wrongly expired
    # by the hard TTL deadlock, and 9 good qwen accounts were wrongly quarantined by an overly aggressive login_lost classification
    # (docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md).
    LOGIN_LOST = "login_lost"                 # Ticket cookie invalid = logged out -> prompt re-authorization
    RATE_LIMITED = "rate_limited"             # 429 = rate limited -> cool down + cool down same-IP siblings too
    CONTENT_REJECTED = "content_rejected"     # Content removed but account can log in = content violation -> penalize template, not account
    BANNED = "banned"                         # Login redirects to verification/ban notice = banned -> manual

    # Publish-specific
    IDENTITY_MISMATCH = "identity_mismatch"   # verify_identity mismatch -> abort immediately, prevents publishing to the wrong account
    EXIT_IP_DRIFT = "exit_ip_drift"           # Egress IP drift -> a remote-login record is already left on the platform
    SUBMIT_UNKNOWN = "submit_unknown"         # 🔴 Request may have reached the platform but the response timed out -> never blindly retry
    NOT_IMPLEMENTED = "not_implemented"       # Adapter path not implemented yet (real publishing = step 6)

    # Engineering issues
    DOM_NOT_FOUND = "dom_not_found"
    TIMEOUT = "timeout"
    CRASH = "crash"


# Declarative handling policy. Follows the _QUARANTINE_POLICY shape in api/engine_sessions.py:195,
# adding a terminal branch (not present in the engine pool). Branch evaluation order: see report in api/publish_dispatch.py.
#
#   reset     success: clear failure count + roll expires_at forward
#   cooldown  set rate_limited_until and cool down sibling accounts on the same egress IP
#   terminal  🔴 status='unschedulable', **no expiry, no auto-recovery**, requires manual handling
#   skip      leave the account alone (the account itself is fine)
#   threshold quarantined only after reaching the threshold (self-healable)
#   requeue   return the task to the queue (account untouched)
_PUBLISH_QUARANTINE_POLICY: dict[PublishFailureType, dict] = {
    PublishFailureType.SUCCESS: {"reset": True},

    # Logged out: threshold 2 instead of the engine pool's "immediate quarantine". The engine pool set login_lost to immediate,
    # and qwen classified "input box not found" as login_lost too, sending 9 good accounts to quarantine at once.
    # The publish pool gives two chances, and step 7's live ticket probe (not fail_type here) makes the final call.
    PublishFailureType.LOGIN_LOST: {"threshold": 2},

    PublishFailureType.RATE_LIMITED: {"cooldown": True},

    # Content violation: penalize the content template, not the account. Task marked failed, account untouched.
    PublishFailureType.CONTENT_REJECTED: {"skip": True, "task": "failed"},

    # The three non-auto-recoverable kinds. Egress IP drift does not auto-recover because the account has already
    # left a remote-login record on the platform; continuing to retry automatically only turns "possibly flagged" into "definitely banned".
    PublishFailureType.BANNED: {"terminal": True},
    PublishFailureType.EXIT_IP_DRIFT: {"terminal": True},
    PublishFailureType.IDENTITY_MISMATCH: {"terminal": True},

    # 🔴 submit_unknown: the request may have reached the platform. **Never blindly retry** -- a blind retry publishes the same article twice,
    # irreversibly. The task is set to failed with visible_after pushed >= 10 minutes out, to be reconciled via the draft_id persisted to DB
    # (reconciliation logic belongs to step 6; this round only guarantees evidence is persisted + no code path re-submits it).
    PublishFailureType.SUBMIT_UNKNOWN: {"skip": True, "task": "failed",
                                        "defer_sec": 600, "no_retry": True},

    PublishFailureType.NOT_IMPLEMENTED: {"skip": True, "task": "failed", "no_retry": True},

    PublishFailureType.DOM_NOT_FOUND: {"threshold": 3},
    PublishFailureType.TIMEOUT: {"threshold": 5, "requeue": True},
    # The worker itself crashed; not the account's fault
    PublishFailureType.CRASH: {"skip": True, "requeue": True},
}


def publish_policy(result: PublishFailureType) -> dict:
    return _PUBLISH_QUARANTINE_POLICY.get(result, {"threshold": 3})


# -- Derived status -----------------------------------------------------------


def effective_status(row: PublishAccountORM, now: Optional[datetime] = None) -> str:
    """Collapse "status column + several timestamp columns" into the single display state of the design section 1.3 state machine.

    Read-only derivation, no DB writes -- deliberately: a status column and timestamp columns encoding the same thing was the common
    cause of both engine pool misjudgment incidents. Write in one place only (status / *_until) and merge on read.
    """
    now = now or datetime.utcnow()
    st = row.status or "pending_auth"
    if st in ("unschedulable", "retired", "quarantined", "pending_auth"):
        return st
    if row.unschedulable_until and row.unschedulable_until > now:
        return "disabled"          # Manual temporary disable, distinct from terminal unschedulable
    if row.expires_at and row.expires_at <= now:
        return "expired"
    if row.rate_limited_until and row.rate_limited_until > now:
        return "rate_limited"
    if row.lease_until and row.lease_until > now:
        return "leased"
    if row.warmup_until and row.warmup_until > now:
        return "warming"
    if st == "warming":
        return "warming" if row.warmup_until is None else "active"
    return st


def account_to_dict(row: PublishAccountORM, *, now: Optional[datetime] = None,
                    today: Optional[str] = None, effective_cap: Optional[int] = None) -> dict:
    """Shared serialization for list/detail.

    🔴 Never includes storage_state_enc / credentials_enc -- not even ciphertext leaves the backend.
    Platform passwords / cookies are never logged and never returned to the frontend (F2).
    """
    used_today = row.used_today if (today and row.used_date == today) else 0
    cap = effective_cap if effective_cap is not None else (row.daily_cap or 0)
    return {
        "id": row.id,
        "platform": row.platform,
        "channel": row.channel,
        "account_id": row.account_id,
        "account_handle": row.account_handle,
        "status": row.status,
        "effective_status": effective_status(row, now),
        "credential_owner": row.credential_owner,
        "auth_type": row.auth_type,
        "group_id": row.group_id,
        "owner_worker_id": row.owner_worker_id,
        "lease_worker_id": row.lease_worker_id,
        "lease_until": row.lease_until.isoformat() if row.lease_until else None,
        "proxy_id": row.proxy_id,
        "egress": row.egress,
        "exit_ip": row.exit_ip,
        "exit_ip_at": row.exit_ip_at.isoformat() if row.exit_ip_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "warmup_until": row.warmup_until.isoformat() if row.warmup_until else None,
        "warmup_daily_cap": row.warmup_daily_cap,
        "rate_limited_until": (row.rate_limited_until.isoformat()
                               if row.rate_limited_until else None),
        "unschedulable_until": (row.unschedulable_until.isoformat()
                                if row.unschedulable_until else None),
        "last_fail_type": row.last_fail_type,
        "last_fail_reason": row.last_fail_reason,
        "last_fail_at": row.last_fail_at.isoformat() if row.last_fail_at else None,
        "fail_counts": _load_json(row.fail_counts_json) or {},
        "use_count": row.use_count,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "used_today": used_today,
        "daily_cap": row.daily_cap,
        "cooldown_minutes": row.cooldown_minutes,
        "effective_cap": cap,
        "remaining": None if cap == 0 else max(0, cap - used_today),
        "priority": row.priority,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _load_json(raw: Optional[str]) -> Any:
    try:
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


# ── Pydantic schemas ───────────────────────────────────────────────


class PublishAccountRegisterIn(BaseModel):
    """Account registration = **moving an existing account's login session into the system**, not "creating an account" (F1).

    The easiest path for existing accounts is manual cookie import -- the owner is logged in locally and exports the login session.
    """
    platform: str = Field(..., min_length=1, description="publish_platforms.code")
    channel: str = Field(CHANNEL_SERVER_POOL, description="server_pool / aggregator / plugin")
    storage_state: Optional[dict] = Field(None, description="Playwright storage_state 全量 JSON")
    # 🔴 The proxy used at authorization **must exactly equal** the account's runtime proxy. The engine pool kept an ENGINE_AUTHZ_PROXY fallback
    # to get around deepseek login page region limits; **publish accounts must not copy that approach** --
    # login proxy != runtime proxy = remote login on first publish (design section 4 P0 item 3).
    proxy_id: Optional[int] = Field(None, description="engine_proxies.id;server_pool 必填")
    auto_proxy: bool = Field(False, description="未指定 proxy_id 时从可用代理库自动分配")
    auth_type: str = Field("cookie", description="cookie / qr / sms")
    account_handle: Optional[str] = Field(None, max_length=64)
    # Required, non-empty -- see the comment on PublishAccountORM.credential_owner
    credential_owner: str = Field(..., min_length=1, max_length=128,
                                  description="手机号/凭据归属人;决定续期是否需要人在场")
    group_id: Optional[int] = None
    daily_cap: Optional[int] = Field(0, ge=0, description="None=env 默认,0=不限")
    ttl_days: int = Field(14, ge=1, le=90)
    warmup_enabled: bool = Field(False, description="是否开启预热期")
    warmup_days: int = Field(7, ge=1, le=90, description="预热期天数")
    warmup_daily_cap: int = Field(0, ge=0, le=10000, description="预热期每日发布上限;0=不限")
    owner_worker_id: Optional[int] = Field(None, description="终身绑定的 worker;留空后续首次领取时绑")
    egress: Optional[str] = Field(None, description="proxy(需 proxy_id 或 auto_proxy)/host(本机直连)")
    # Escape hatch: no browser call; persist a placeholder id from the ticket cookie hash, status stays pending_auth.
    # Only for table setup/claim tests without a browser -- do not use for production registration.
    resolve_identity: bool = True


class PublishAccountRebindIn(BaseModel):
    """Rebind worker. owner_worker_id lifetime binding means accounts are stranded forever when a worker is scrapped,
    so this explicit exit must exist -- but it changes the machine fingerprint, so the proxy must be confirmed at the same time."""
    owner_worker_id: int
    proxy_id: Optional[int] = Field(None, description="egress=proxy 时必填")
    auto_proxy: bool = Field(False, description="egress=proxy 时从可用代理库自动分配")
    egress: str = Field(EGRESS_PROXY, description="proxy=自动/固定代理;host=本机直连")
    reason: str = Field(..., min_length=1, max_length=256)


class PublishAccountCheckedOut(BaseModel):
    """Handed to a worker to run one publish. storage_state is decrypted -- consistent with the engine pool check-out
    sending plaintext storage_state (over 127.0.0.1 / private network; browser-service does not need the key)."""
    account_db_id: int
    platform: str
    account_id: Optional[str]
    storage_state: dict
    proxy_url: Optional[str] = None
    egress: Optional[str] = None
    user_agent: Optional[str] = None
    ua_platform: Optional[str] = None
    expected_exit_ip: Optional[str] = None


class PublishPoolStatusEntry(BaseModel):
    """Data source for the pool level dashboard (F7). Four questions: how many accounts can publish / how many posts left today /
    which accounts are about to log out / any signs of correlated bans."""
    platform: str
    total: int
    schedulable: int          # Claimable right now
    warming: int
    rate_limited: int
    quarantined: int
    unschedulable: int
    expiring_soon: int        # Expiring within 48h
    remaining_today: Optional[int]   # None = some account is unlimited
    egress_groups_at_risk: list[dict] = []   # Groups on the same egress IP with batch anomalies
