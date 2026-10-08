"""Engine session pool — centralized Playwright storage_state storage.

Each record = the complete storage_state (full cookies + localStorage + sessionStorage) produced by one full login
by a user on one machine to an engine.
browser-service checks out the least-used one from the pool, replaces its local file,
and after running search checks in, reporting the specific FailureType.

The D1 schema has been extended: fail_counts_json / lease / region / preferred_worker_id
(see backend/alembic/versions/a1b2c3d4e5f6_scheduler_p1_p3_p4.py).

D2 (this file) implements P1 — failure signals as an enum + per-type quarantine policies.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import deferred

from geo.database import Base


class EngineSessionORM(Base):
    __tablename__ = "engine_sessions"
    __table_args__ = (
        Index("idx_engine_sessions_pool", "engine", "status", "captcha_count", "last_used_at"),
        Index("idx_sessions_lease", "engine", "status", "leased_until"),
        Index("idx_sessions_region", "engine", "status", "captured_from_region"),
        Index("idx_sessions_account", "engine", "account_id"),
    )

    id = Column(Integer, primary_key=True)
    engine = Column(String, nullable=False, index=True)            # doubao/qwen/deepseek/wenxin/yuanbao
    # Pool marker (2026-07): 'default' = sentiment monitoring pool; 'workbench' = dedicated accounts for the workbench "engine measurement".
    # check-out filters by pool, so accounts of the two pools are never mixed.
    pool = Column(String, nullable=False, default="default", server_default="default")
    source_label = Column(String, nullable=True)                   # "alice-mac", "bob-win-laptop", ...
    # Account identity (2026-06): hash of the stable user identifier extracted from storage_state
    # (format "<field name>:<first 16 of sha256>", see extract_account_id); upsert matches on it first
    account_id = Column(String, nullable=True)
    account_handle = Column(String, nullable=True)                 # Nickname/masked phone number, display only
    # Deferred loading (issue #43): the whole table is 68 MB, mostly frontend caches in localStorage. Account pool stats, lists,
    # and egress usage computation before claiming only need small fields; reading whole rows drags all of it along (and hangs outright with cross-region DB connections).
    # Places that really need it (check-out delivery, Baidu cookies) undefer on the query to load it in one go; do not lazy-load row by row.
    storage_state = deferred(Column(Text, nullable=False))         # JSON string
    user_agent = Column(String, nullable=True)
    platform = Column(String, nullable=True)                       # "MacIntel" / "Linux x86_64" / ...
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    captcha_count = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="active")      # active / quarantined / expired
    expires_at = Column(DateTime, nullable=True)

    # P1 failure signals (D2)
    last_fail_type = Column(String, nullable=True)
    last_fail_at = Column(DateTime, nullable=True)
    # Health fields (added 2026-08-24). Previously "is this account usable" only looked at expires_at — a 7-day TTL
    # arbitrarily added at upload, not reflecting the real login session, so the pool kept dispatching accounts long logged out.
    # login_ok: None = not probed / True = probe confirmed logged in / False = confirmed logged out, no longer dispatched
    login_ok = Column(Boolean, nullable=True)
    login_checked_at = Column(DateTime, nullable=True)
    # Latest real success. The table used to have only failure traces (last_fail_*), no success traces,
    # so there was no way to tell whether an account was still working — dispatch ordering could only guess by use_count.
    last_success_at = Column(DateTime, nullable=True)
    fail_counts_json = Column(Text, nullable=False, default="{}")

    # P3 lease + region affinity (schema added in D2, logic lands in D4)
    leased_by_worker_id = Column(Integer, nullable=True)
    leased_until = Column(DateTime, nullable=True)
    captured_from_region = Column(String, nullable=True)
    captured_from_ip = Column(String, nullable=True)
    preferred_worker_id = Column(Integer, nullable=True)

    # Precise usage control (2026-06): per-account daily check-out hard cap + egress IP binding
    used_today = Column(Integer, nullable=False, default=0)   # Check-outs today (reset automatically when used_date changes)
    used_date = Column(String, nullable=True)                 # 'YYYY-MM-DD' (local date); paired with used_today
    # Hourly window count (migration b4e7d2a91f38). The daily cap cannot control "how fast it is used up" — measured 2026-08-30:
    # three early-morning hours burned through the egress daily quota at maximum instantaneous density, and the engine sat idle for the remaining 20 hours. Risk control looks at
    # rate, so an hourly egress rate limit is added. Same shape as used_today/used_date, reset across windows.
    used_hour = Column(Integer, nullable=False, default=0, server_default="0")
    used_hour_key = Column(String, nullable=True)              # 'YYYY-MM-DD HH' (local time)
    egress = Column(String, nullable=True)                    # None = engine default (PROXY_ENGINES) / 'proxy' = via proxy / 'host' = local IP
    proxy_override = Column(String, nullable=True)            # Full proxy string bound per account (http://user:pass@host:port); highest priority, overrides egress/global. Plaintext (same as storage_state)
    proxy_id = Column(Integer, nullable=True)                 # References the proxy library (engine_proxies.id); resolved into proxy_override at check-out. Takes precedence over the raw proxy_override string
    daily_cap = Column(Integer, nullable=True)               # Per-account daily cap override; None = env default, 0 = unlimited
    exit_ip = Column(String, nullable=True)                  # The account's actual egress IP (resolved and reported by the worker at check-in)
    exit_ip_at = Column(DateTime, nullable=True)             # Last time the egress IP was resolved

    # Authorization type + scheduling state machine (2026-06, modeled on Sub2API)
    auth_type = Column(String, nullable=True)                 # password / apikey / qr / cookie; None = unknown
    credentials = deferred(Column(Text, nullable=True))       # AES-encrypted JSON credentials (password/key), for automatic renewal after logout; deferred as above (#43)
    rate_limited_until = Column(DateTime, nullable=True)      # Rate-limit cooldown expiry (set after receiving 429)
    unschedulable_until = Column(DateTime, nullable=True)     # Temporary unschedulable expiry (account switch/maintenance)
    priority = Column(Integer, nullable=False, default=50)    # Scheduling priority, lower value first
    concurrency = Column(Integer, nullable=False, default=1)  # Max concurrency per account


class EngineProxyORM(Base):
    """Proxy library: centrally registers reusable egress proxies. Accounts reference one via engine_sessions.proxy_id,
    and at check-out the backend resolves it into a full proxy string for the worker. Change proxy_url here and all accounts bound to
    the proxy follow automatically (no rebinding one by one); passwords are also centralized for easier encryption later."""
    __tablename__ = "engine_proxies"
    id = Column(Integer, primary_key=True)
    label = Column(String, nullable=False)                    # Display name, e.g. "Vietnam-203.0.113.10"
    proxy_url = Column(String, nullable=False)                # http://user:pass@host:port (plaintext, same as storage_state)
    note = Column(String, nullable=True)
    disabled = Column(Boolean, nullable=False, default=False) # Disabled: cannot be newly bound, not delivered at check-out
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_tested_at = Column(DateTime, nullable=True)          # Last self-check time
    last_exit_ip = Column(String, nullable=True)              # Self-check egress IP
    last_country = Column(String, nullable=True)              # Self-check egress country


# ── Account identity extraction (2026-06) ────────────────────────────────────────
#
# Goal: when the same real account logs in/uploads again, recognize "this is an existing account" and renew it,
# instead of inserting a new row. Relies on user identifiers in storage_state that stay stable across re-logins:
#
#   doubao   cookie uid_tt / uid_tt_ss (ByteDance passport encrypted uid)
#   qwen     cookie login_aliyunid_pk (Alibaba account PK); JWT token of chat.qwen.ai
#   yuanbao  cookie hy_user (Tencent Hunyuan user id)
#   deepseek localStorage userToken (if JWT, take the user field from the payload)
#   wenxin   Baidu has no plaintext uid cookie (BDUSS changes on every login), uses generic probing;
#            if nothing is found → upload falls back to matching by source_label
#
# Candidate fields are not fully verified (calibrate by scanning the current DB with scripts/scan_session_identity.py);
# the worst case of wrong/missing extraction is just falling back to label matching, never wrongly merging two accounts —
# because account_id carries a field-name prefix + value hash, different sources/values are never equal.

_IDENTITY_COOKIES: dict[str, tuple[str, ...]] = {
    "doubao":  ("uid_tt", "uid_tt_ss"),
    "qwen":    ("login_aliyunid_pk",),
    "yuanbao": ("hy_user",),
}

# Common keys where JWT-shaped tokens are stored (cookie or localStorage)
_TOKEN_KEYS = ("token", "userToken", "access_token")

# Fields in user objects / JWT payloads recognized as "user id", in priority order
_USER_ID_FIELDS = ("user_id", "userId", "uid", "sub", "openid", "open_id",
                   "account_id", "accountId", "id")


def _account_hash(field: str, value: str) -> str:
    """account_id format: "<source field>:<first 16 of sha256>" — no plaintext stored, source traceable."""
    return f"{field}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _jwt_user_claim(token: str) -> Optional[str]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for f in _USER_ID_FIELDS:
        v = payload.get(f)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return None


def extract_account_id(engine: str, storage_state: dict) -> Optional[str]:
    """Extract a stable account identifier from storage_state; returns None if not found (caller falls back to label)."""
    cookies = {c.get("name"): c.get("value")
               for c in (storage_state.get("cookies") or [])
               if c.get("name") and c.get("value")}
    ls: dict[str, str] = {}
    for o in storage_state.get("origins") or []:
        for item in o.get("localStorage") or []:
            if item.get("name") and item.get("value"):
                ls.setdefault(item["name"], item["value"])

    # 1) Known stable identity cookies of the engine
    for name in _IDENTITY_COOKIES.get(engine, ()):
        v = cookies.get(name)
        if v:
            return _account_hash(f"cookie.{name}", v)

    # 2) JWT-shaped token → user field in the payload (stable across logins, though the token itself changes)
    for key in _TOKEN_KEYS:
        for src, store in (("ls", ls), ("cookie", cookies)):
            raw = store.get(key) or ""
            if raw.lstrip().startswith("{"):
                # localStorage often wraps it as {"value": "...", "__expires": ...}
                try:
                    raw = (json.loads(raw) or {}).get("value") or ""
                except Exception:
                    raw = ""
            claim = _jwt_user_claim(raw) if raw else None
            if claim:
                return _account_hash(f"{src}.{key}.jwt", claim)

    # 3) User info JSON in localStorage ({"userId": ...} and the like)
    for key, obj in _user_objects(ls):
        for f in _USER_ID_FIELDS:
            v = obj.get(f)
            if isinstance(v, (str, int)) and str(v).strip():
                return _account_hash(f"ls.{key}.{f}", str(v))

    return None


_HANDLE_FIELDS = ("nickname", "nickName", "nick", "screen_name", "userName",
                  "username", "name", "mobile", "phone", "email")


def _user_objects(ls: dict[str, str]):
    """localStorage items whose key contains user/account and whose value is a JSON object, sorted by key."""
    for key in sorted(ls):
        if "user" not in key.lower() and "account" not in key.lower():
            continue
        raw = ls[key]
        if not raw.lstrip().startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield key, obj


def extract_account_handle(engine: str, storage_state: dict) -> Optional[str]:
    """Dig a nickname/phone number from localStorage user info as the display name; fallback when the collector did not send a handle."""
    ls: dict[str, str] = {}
    for o in storage_state.get("origins") or []:
        for item in o.get("localStorage") or []:
            if item.get("name") and item.get("value"):
                ls.setdefault(item["name"], item["value"])
    for _key, obj in _user_objects(ls):
        for f in _HANDLE_FIELDS:
            v = obj.get(f)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v).strip()[:64]
    return None


class FailureType(str, Enum):
    """The usage result reported by the worker at check-in.

    Each type has its own quarantine policy (see _QUARANTINE_POLICY in engine_sessions.py).
    """
    SUCCESS = "success"
    CAPTCHA = "captcha"
    EMPTY_ANSWER = "empty_answer"      # Returned an empty string; possibly a UI redesign or a half-broken session
    LOGIN_LOST = "login_lost"          # Redirected to login / forced re-login; session fully invalid
    DOM_NOT_FOUND = "dom_not_found"    # Key selector not found; the engine may have been redesigned
    TIMEOUT = "timeout"                # Streaming exceeded max_wait
    CRASH = "crash"                    # Exception raised; not counted against the session
    RATE_LIMITED = "rate_limited"      # Rate-limited/muted by engine risk control (deepseek "user is muted") → cool down until mute_until, no account penalty


# ── Pydantic schemas ───────────────────────────────────────────────


class SessionUploadIn(BaseModel):
    engine: str = Field(..., description="doubao/qwen/deepseek/wenxin/yuanbao")
    pool: str = Field("default", description="账号池:default=舆情检测 / workbench=工作台引擎实测")
    storage_state: dict = Field(..., description="Playwright storage_state full JSON")
    source_label: Optional[str] = Field(None, description="who+device, e.g. 'alice-mac'")
    account_handle: Optional[str] = Field(None, description="采集端抓的昵称/掩码手机号,纯展示")
    user_agent: Optional[str] = None
    platform: Optional[str] = None
    ttl_days: int = Field(7, ge=1, le=30, description="过期天数,默认 7,最多 30")
    auth_type: Optional[str] = Field(None, description="password/apikey/qr/cookie")
    credentials: Optional[str] = Field(None, description="AES 加密的凭证 JSON(供自动续期),不解密只存")


class SessionCheckedOut(BaseModel):
    """Returned to browser-service for use in a single /search call."""
    id: int
    engine: str
    source_label: Optional[str]
    storage_state: dict
    use_count: int
    egress: Optional[str] = None   # None = engine default / 'proxy' = via proxy / 'host' = local IP; browser-service decides the egress from this
    proxy_override: Optional[str] = None   # Full proxy string bound to the account; when non-empty browser-service prefers it, overriding egress/global
    user_agent: Optional[str] = None       # The UA used when the account logged in; the pure-HTTP path (deepseek) uses it to impersonate the owner; nullable


class SessionCheckIn(BaseModel):
    """check-in payload.

    New shape (D2+):
      {"id": 7, "result": "success" | "captcha" | ..., "error_msg": "..." | null}

    Backward-compat: old workers still send {"id": 7, "captcha_triggered": true/false},
    which is automatically translated into result=CAPTCHA / SUCCESS. Allows browser hosts that have not upgraded yet during the transition.
    """
    id: int
    result: FailureType = FailureType.SUCCESS
    error_msg: Optional[str] = None
    exit_ip: Optional[str] = None   # Actual egress IP the worker resolved via the egress proxy (best-effort)
    rate_limited_until: Optional[float] = None  # Cooldown expiry on RATE_LIMITED (unix seconds, e.g. deepseek mute_until)
    # Rolling renewal (2026-06): on a successful check-in the worker returns the storage_state "refreshed by active use".
    # The backend writes it back in the SUCCESS branch + extends expires_at, so regularly used accounts keep living like a real browser,
    # no longer wrongly killed on day 7 by the hard captured_at+ttl TTL (QR engines qwen/wenxin benefit especially).
    storage_state: Optional[dict] = None
    # Legacy field: not in the new API, only used to deserialize old worker payloads
    captcha_triggered: Optional[bool] = None

    @model_validator(mode="after")
    def _translate_legacy(self) -> "SessionCheckIn":
        # Old workers did not set result (default SUCCESS) but set captcha_triggered=true
        # → treated as CAPTCHA. New workers use result and do not send captcha_triggered.
        if self.captcha_triggered is True and self.result == FailureType.SUCCESS:
            self.result = FailureType.CAPTCHA
        return self


class PoolStatusEntry(BaseModel):
    engine: str
    active: int
    quarantined: int
    expired: int
    oldest_active_captured_at: Optional[datetime]


class EngineRatePolicyORM(Base):
    """Engine-level rate-limit policy — moved from code constants/env into the DB; editable in the back office, effective immediately, no restart.

    Before 2026-08-30 these values were scattered in three places: the _DEFAULT_* dicts in code, env
    ENGINE_IP_* overrides, and the per-account daily_cap column. Tuning a parameter meant editing env + restarting the backend,
    and doing it separately for two environments (test host/prod) was easy to miss — we actually saw the inconsistency "daily cap is 80 on test host,
    still 25 on prod".

    Value priority: this table (non-NULL fields) > env > code default.
    NULL = this item does not override and the lower layer's value is used — so to tune one item there is no need to fill the whole row.

    Four gates, each managing one thing (deepseek's production values as an example):
      session_min_gap_sec 600  minimum interval between two calls of one account
      ip_min_gap_sec      400  minimum interval between two calls on the same egress (constant density, naturally serial)
      ip_hourly_cap         9  per-egress hourly cap (total ceiling)
      ip_daily_cap         80  per-egress daily cap (fallback)
    """
    __tablename__ = "engine_rate_policies"

    id = Column(Integer, primary_key=True, index=True)
    engine = Column(String, nullable=False, unique=True, index=True)
    # All nullable: NULL = do not override the lower-layer default
    session_min_gap_sec = Column(Integer, nullable=True)
    ip_min_gap_sec = Column(Integer, nullable=True)
    ip_hourly_cap = Column(Integer, nullable=True)
    ip_daily_cap = Column(Integer, nullable=True)
    per_account_daily_cap = Column(Integer, nullable=True)
    # The two columns below only matter for "physical-device fleet" engines (doubao_browser) — such engines' capacity does not come from
    # the account pool but from a few real computers, so they have two extra parameters. Putting them in the same table is deliberate: when tuning rate limits
    # you should not also have to remember "this engine's config lives elsewhere". These two columns stay NULL for account-pool engines.
    worker_daily_cap = Column(Integer, nullable=True)      # Daily capacity per physical device (denominator for fleet capacity)
    worker_min_ok_rate = Column(Integer, nullable=True)    # Quality gate threshold (percent); below it the device is drained automatically
    enabled = Column(Boolean, nullable=False, default=True)
    note = Column(Text, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
