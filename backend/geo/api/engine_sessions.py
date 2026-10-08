"""Engine session pool — upload (harvester) + check-out/check-in (browser-service).

Three roles:
  1. **harvester** (scripts/harvest_sessions.py runs on user machines):
       POST /api/engine-sessions/upload  +  header X-Harvest-Token
  2. **browser-service** (browser host fetches a session before search):
       POST /api/engine-sessions/check-out  +  header X-Service-Token
       POST /api/engine-sessions/check-in   +  header X-Service-Token
  3. **dashboard / ops** (check pool health):
       GET  /api/engine-sessions/pool-status  (no auth, read-only stats)

Environment variables (set at deploy):
  ENGINE_SESSION_HARVEST_TOKEN  — distributed to all users, for upload auth
  ENGINE_SESSION_SERVICE_TOKEN  — internal token between backend ↔ browser-service
  ENGINE_SESSION_VALID_ENGINES  — comma-separated, default doubao,qwen,deepseek,wenxin,yuanbao
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, case as sa_case, func, or_
from sqlalchemy.orm import Session, undefer

from geo.database import SessionLocal
from geo.models.engine_sessions import (
    EngineProxyORM,
    EngineRatePolicyORM,
    EngineSessionORM,
    FailureType,
    PoolStatusEntry,
    SessionCheckIn,
    SessionCheckedOut,
    SessionUploadIn,
    extract_account_handle,
    extract_account_id,
)

_log = logging.getLogger(__name__)


router = APIRouter()


# ── DB session helper ───────────────────────────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Auth helpers ────────────────────────────────────────────────────


def _tok_eq(got: Optional[str], expected: str) -> bool:
    """Constant-time comparison of the internal trust-root token, avoiding the != timing side channel leaking prefix match length."""
    if not expected:
        return False
    return hmac.compare_digest(got or "", expected)


def _require_harvest_token(x_harvest_token: Optional[str] = Header(None)) -> None:
    expected = (os.environ.get("ENGINE_SESSION_HARVEST_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "harvest endpoint disabled (server token not configured)")
    if not _tok_eq(x_harvest_token, expected):
        raise HTTPException(401, "invalid X-Harvest-Token")


def _require_service_token(x_service_token: Optional[str] = Header(None)) -> None:
    expected = (os.environ.get("ENGINE_SESSION_SERVICE_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "service endpoints disabled (server token not configured)")
    if not _tok_eq(x_service_token, expected):
        raise HTTPException(401, "invalid X-Service-Token")


def _require_upload_token(
    x_harvest_token: Optional[str] = Header(None),
    x_service_token: Optional[str] = Header(None),
) -> None:
    """upload accepts the harvest token (collector) or the service token (internal authorizer auto-login)."""
    harvest = (os.environ.get("ENGINE_SESSION_HARVEST_TOKEN") or "").strip()
    service = (os.environ.get("ENGINE_SESSION_SERVICE_TOKEN") or "").strip()
    if _tok_eq(x_harvest_token, harvest):
        return
    if _tok_eq(x_service_token, service):
        return
    raise HTTPException(401, "invalid X-Harvest-Token / X-Service-Token")


# ── Config ──────────────────────────────────────────────────────────


def _valid_engines() -> set[str]:
    raw = os.environ.get("ENGINE_SESSION_VALID_ENGINES") or "doubao,qwen,deepseek,wenxin,yuanbao,kimi"
    return {e.strip() for e in raw.split(",") if e.strip()}


# Engines whose login session lives in localStorage rather than cookie auth (kimi: access_token / refresh_token /
# msh_user_id keys). upload and check-in write-back used to require non-empty cookies — that gate protects browser engines from
# "a logged-out state with empty cookies overwriting a good session"; for these engines check localStorage instead; others unchanged.
# Keep in sync with _LOCALSTORAGE_AUTH_ENGINES in workers/service-cn/app/session_store.py.
_LOCALSTORAGE_AUTH_ENGINES = {"kimi"}
_LOCALSTORAGE_AUTH_KEYS = {"kimi": ("access_token", "refresh_token")}


def _state_has_auth(engine: str, storage_state: Optional[dict]) -> bool:
    """Does this state look "still logged in": non-empty cookies, or (localStorage-auth engines) all auth keys present."""
    if not storage_state:
        return False
    if storage_state.get("cookies"):
        return True
    keys = _LOCALSTORAGE_AUTH_KEYS.get((engine or "").lower())
    if not keys:
        return False
    found: set = set()
    for o in storage_state.get("origins") or []:
        ls = (o or {}).get("localStorage") or []
        if isinstance(ls, dict):
            ls = [{"name": k, "value": v} for k, v in ls.items()]
        for it in ls:
            if it.get("name") in keys and it.get("value"):
                found.add(it["name"])
    return all(k in found for k in keys)


_CAPTCHA_QUARANTINE_THRESHOLD = 3   # Challenged with CAPTCHA 3 times cumulatively → quarantine
# Identity binding: when check-out carries worker_id, the account gets a lease and sticks to that worker/IP for this long;
# active workers renew on each check-out, and a dead worker's lease expires, releasing the account to others.
_LEASE_TTL_HOURS = int(os.environ.get("ENGINE_SESSION_LEASE_TTL_HOURS", "6"))
# Rolling renewal window: when a successful check-in brings back a new storage_state, extend expires_at by this many days.
# Default matches upload's default ttl_days (7).
_RENEW_TTL_DAYS = int(os.environ.get("ENGINE_SESSION_RENEW_TTL_DAYS", "7"))


def _local_today() -> str:
    """Local (Beijing) date string, used to reset per-account daily usage."""
    return (datetime.utcnow() + timedelta(hours=8)).date().isoformat()


def _local_hour_key() -> str:
    """Local (Beijing) hour key 'YYYY-MM-DD HH', used to reset the egress hourly rate-limit window.

    Same definition as _local_today — both windows use one timezone so troubleshooting lines up;
    mixing UTC/local would make "used today" and "used this hour" look contradictory.
    """
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H")


# Default per-account daily check-out cap (0 = unlimited). Overridden by env ENGINE_SESSION_DAILY_CAP_<ENGINE>,
# and the per-account daily_cap column overrides env. doubao uses the ARK API with no account limit → 0.
# kimi 20: risk control only has samples of 20+ questions per account per day; run 3~5 days then tune (consistent with the telemetry scheduler).
_DEFAULT_DAILY_CAP = {
    "deepseek": 30, "qwen": 30, "wenxin": 30, "yuanbao": 30, "doubao": 0, "kimi": 20,
}


def _session_daily_cap(engine: str) -> int:
    """Engine-level default per-account daily check-out cap; 0 = unlimited.
    env ENGINE_SESSION_DAILY_CAP_<ENGINE> overrides the default; the per-account daily_cap column overrides env."""
    ov = os.environ.get(f"ENGINE_SESSION_DAILY_CAP_{engine.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_DAILY_CAP.get(engine.lower(), 0)


# ── Anti rate-limiting: rate / density constraints (2026-08) ──────────────────────────────
#
# The daily cap only limits "how many per day", not "how fast" or "from which IP". deepseek's risk control looks at both:
#   · the same account asking repeatedly in a short time → looks like a bot;
#   · multiple accounts taking turns on the same egress IP → judged per IP, mutes are issued **in clusters** (observed: accounts on the same IP
#     muted together in the same minute), with escalating penalties (6 hours → 8 days → 26 days); the harder you hit, the later the unban.
# So two more gates are added here, both reusing existing columns (last_used_at / used_today): no new columns, no migration.

# Minimum interval (seconds) between two check-outs of one account; 0 = unlimited. Default deepseek 600s (≤6 per account per hour,
# consistent with the safety line in docs/03-系统设计/引擎采集层/IP代理与账号容量规划.md). Overridden by env ENGINE_SESSION_MIN_GAP_SEC_<ENGINE>.
# kimi 300: not a risk-control parameter but a way to **serialize per account** — leases only prevent cross-worker sharing; concurrent coroutines in one worker
# would get the same account, both refresh refresh_token once, and the second always 401s (design doc §Design 2). Value ≥ streaming timeout
# 240s + renewal time; read that section before lowering it.
_DEFAULT_MIN_GAP_SEC = {"deepseek": 600, "kimi": 300}

# Per egress IP (proxy_id first, else exit_ip) daily check-out cap; 0 = unlimited.
# Prevents "when the pool is banned down to a few accounts, load automatically piles onto one IP" — exactly the source of the next mute wave.
# Overridden by env ENGINE_IP_DAILY_CAP_<ENGINE>.
_DEFAULT_IP_DAILY_CAP = {"deepseek": 15}

# Per egress IP **hourly** check-out cap; 0 = unlimited. Overridden by env ENGINE_IP_HOURLY_CAP_<ENGINE>.
#
# This layer is what targets risk control itself: platforms judge "how fast", while the daily cap only judges "how many". Measured 2026-08-30:
# with only a daily cap, the actual pattern was — 25 quota used up in three hours at 02:00-04:00 (maximum instantaneous density,
# exactly what most easily triggers cluster mutes), then nothing dispatched for 20 hours, and 25 jobs ended up
# as "no session available, terminal state".
#
# After switching to 3 per hour: a single IP gets 24×3=72 per day (2.4x the daily cap of 30), while peak density actually drops.
# Consistent with the deepseek safety line in the doc "IP proxy and account capacity planning" (≤6 per account per hour,
# with ample margin for 4 accounts on one egress). Start at 3, watch rate_limited_until / captcha_count for a week,
# then consider raising to 4-5.
# Egress hourly cap: total fallback. Works with the egress interval below — the interval keeps "density constant", the cap keeps "total bounded".
# 9 per hour, with a 400s interval, spreads these 9 evenly across an hour (3600÷9=400).
_DEFAULT_IP_HOURLY_CAP = {"deepseek": 9}

# Minimum interval (seconds) between two check-outs on the same egress; 0 = unlimited. Overridden by env ENGINE_IP_MIN_GAP_SEC_<ENGINE>.
#
# Why hourly_cap alone is not enough: the cap only limits "how many per hour", not "how they are distributed" — 3 could all
# be fired within the same minute without lowering peak density at all, and risk control looks at density. The interval constraint is constant.
#
# Where 400s comes from: the goal is "one IP runs 9 times an hour, evenly spread", 3600÷9=400. With 4 accounts on one egress
# taking turns, the same account comes up again after 400×4=1600s, still satisfying the per-account 600s interval — the two gates do not conflict.
# It also naturally gives "serial within an IP": requests on the same egress are forcibly spaced and never crowd concurrently.
_DEFAULT_IP_MIN_GAP_SEC = {"deepseek": 400}


# ── Engine-level rate-limit policy: DB > env > code default ─────────────────────────────
# Once parameters moved into the DB (engine_rate_policies, migration c1f8b3d24e97), back-office changes take effect immediately
# without restart, and test host / prod share one copy — previously env was configured separately on each, and missing one caused
# inconsistencies like "daily cap is 80 on test host but still 25 on prod" (measured 2026-08-30).
#
# Cache 60s: check-out is a hot path and cannot query the DB every time; a 60s lag is more than fine for rate-limit parameters.
_POLICY_CACHE_TTL_SEC = 60
_policy_cache: dict = {}          # engine -> (read time, {field: value})


def _rate_policy(engine: str) -> dict:
    """Read the engine's rate-limit policy row (cached). Table missing / not found → empty dict, caller falls back to env."""
    e = (engine or "").lower()
    hit = _policy_cache.get(e)
    now = datetime.utcnow()
    if hit and (now - hit[0]).total_seconds() < _POLICY_CACHE_TTL_SEC:
        return hit[1]
    vals: dict = {}
    try:
        with SessionLocal() as db:
            row = (db.query(EngineRatePolicyORM)
                   .filter(EngineRatePolicyORM.engine == e,
                           EngineRatePolicyORM.enabled == True)      # noqa: E712
                   .one_or_none())
            if row is not None:
                vals = {
                    "session_min_gap_sec": row.session_min_gap_sec,
                    "ip_min_gap_sec": row.ip_min_gap_sec,
                    "ip_hourly_cap": row.ip_hourly_cap,
                    "ip_daily_cap": row.ip_daily_cap,
                    "per_account_daily_cap": row.per_account_daily_cap,
                }
    except Exception:  # noqa: BLE001 — migration not applied / DB glitch: fall back to env, do not block check-out
        vals = {}
    _policy_cache[e] = (now, vals)
    return vals


def _policy_int(engine: str, field: str) -> Optional[int]:
    """Value from the policy table; NULL or unreadable → None (meaning no override, defer to the lower layer)."""
    v = _rate_policy(engine).get(field)
    return int(v) if v is not None else None


def invalidate_rate_policy_cache(engine: Optional[str] = None) -> None:
    """Call after changing a policy so new values take effect immediately instead of waiting 60s."""
    if engine:
        _policy_cache.pop((engine or "").lower(), None)
    else:
        _policy_cache.clear()


def _session_min_gap_sec(engine: str) -> int:
    db_v = _policy_int(engine, "session_min_gap_sec")
    if db_v is not None:
        return db_v
    ov = os.environ.get(f"ENGINE_SESSION_MIN_GAP_SEC_{engine.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_MIN_GAP_SEC.get(engine.lower(), 0)


def _ip_daily_cap(engine: str) -> int:
    db_v = _policy_int(engine, "ip_daily_cap")
    if db_v is not None:
        return db_v
    ov = os.environ.get(f"ENGINE_IP_DAILY_CAP_{engine.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_IP_DAILY_CAP.get(engine.lower(), 0)


def _ip_hourly_cap(engine: str) -> int:
    db_v = _policy_int(engine, "ip_hourly_cap")
    if db_v is not None:
        return db_v
    ov = os.environ.get(f"ENGINE_IP_HOURLY_CAP_{engine.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_IP_HOURLY_CAP.get(engine.lower(), 0)


def _ip_min_gap_sec(engine: str) -> int:
    db_v = _policy_int(engine, "ip_min_gap_sec")
    if db_v is not None:
        return db_v
    ov = os.environ.get(f"ENGINE_IP_MIN_GAP_SEC_{engine.upper()}")
    if ov and ov.isdigit():
        return int(ov)
    return _DEFAULT_IP_MIN_GAP_SEC.get(engine.lower(), 0)


def _egress_key(row: EngineSessionORM) -> Optional[str]:
    """The account's "egress" identifier — platforms judge risk control by it, so grouping must use it.

    **proxy_id first** (the egress this run will use); `exit_ip` is only the fallback when no proxy is bound.

    Why not exit_ip first: `exit_ip` is the **egress actually used last time**, a historical observation. After ops rebind an account
    to a new proxy, exit_ip only updates after its next successful run — during that gap, grouping by exit_ip would count accounts on the new egress
    into the old egress group, penalized by the old egress's quota used today. Measured 2026-08-03: three accounts had just been assigned three
    different new egresses, but because their exit_ip was still the same old egress IP (that group had used 103 that day), the per-egress
    gate blocked all of them and not a single deepseek account could be dispatched.

    (It was once configured the other way, reasoning "the same physical IP would be split into two groups" — that was because some accounts then had no
    proxy_id and shared an IP with bound ones. Now every account explicitly binds a proxy, so that premise no longer holds.)
    """
    if row.proxy_id:
        return f"proxy:{row.proxy_id}"
    if row.exit_ip:
        return row.exit_ip
    return None

# ── Quarantine policy (D2 — P1 failure signals as enum) ──────────────────
#
# Quarantine policy per FailureType:
#   - threshold: quarantine after N cumulative occurrences
#   - immediate: quarantine on a single occurrence (irreversible fails like login_lost)
#   - skip: not counted against the session, usually a worker-side issue (crash)
#
# policy=None means SUCCESS, resetting all fail_counts (so old failures do not accumulate forever).
_QUARANTINE_POLICY: dict[FailureType, dict] = {
    FailureType.SUCCESS:        {"reset": True},
    FailureType.CAPTCHA:        {"threshold": 3, "legacy_col": "captcha_count"},
    FailureType.LOGIN_LOST:     {"immediate": True},
    FailureType.EMPTY_ANSWER:   {"threshold": 5},
    # alert: the engine may have been redesigned. **Alerts are not sent here** — one account reporting dom_not_found says little;
    # only many accounts reporting at once signals a redesign, so the check lives in the telemetry scheduler's
    # _check_engine_revamp (aggregates accounts reporting dom_not_found/empty_answer per engine over the last 24h).
    # This flag only annotates semantics and drives no behavior (before 2026-07 nobody consumed it at all).
    FailureType.DOM_NOT_FOUND:  {"threshold": 3, "alert": True},
    FailureType.TIMEOUT:        {"threshold": 5},
    FailureType.CRASH:          {"skip": True},
    FailureType.RATE_LIMITED:   {"cooldown": True},   # Risk-control rate limit/mute: cool this account down until mute_until, no quarantine or penalty
}

# RATE_LIMITED default cooldown (when rate_limited_until is absent): env ENGINE_RATE_LIMIT_COOLDOWN_SEC, default 6h
_RATE_LIMIT_COOLDOWN_SEC = int(os.environ.get("ENGINE_RATE_LIMIT_COOLDOWN_SEC", str(6 * 3600)))

# Per-engine overrides of the policy above. Tongyi's "CAPTCHA" is Alibaba's baxia slider, **shown randomly per request**, not per account:
# the same account asking again 2 minutes later works fine (measured on test host 2026-09-14). Treating it like Doubao's "3 strikes → quarantine"
# would let one bad window quarantine all main accounts (that is how #43/#65/#100 were lost on the morning of 09-15; #65 had been 60/60 the day before).
# So Tongyi's captcha only cools down that account for half an hour: no counting, no sibling penalty, no quarantine.
_QUARANTINE_POLICY_BY_ENGINE: dict[str, dict[FailureType, dict]] = {
    "qwen": {
        FailureType.CAPTCHA: {"cooldown": True, "cooldown_sec": 1800, "siblings": False},
        # Same for empty answers: Tongyi "accepted but did not answer" / hot-http connect_timeout, http_403,
        # and 「被挤爆啦」 ("overloaded") all land as empty_answer, and hot-http reports once per retry, so one question can
        # rack up 3. These are all risk control on **the egress IP at that moment**, not the account — on the morning of 2026-09-16
        # the freshly added #100 (login verified OK at 05:45) hit 5 within two hours from another host and was quarantined.
        # Now: 30-minute cooldown, no counting, no quarantine; a real lost login is still quarantined immediately via login_lost.
        FailureType.EMPTY_ANSWER: {"cooldown": True, "cooldown_sec": 1800, "siblings": False},
    },
}

# Sibling cooldown: when an account is rate-limited/muted, sibling accounts on the same engine and egress (proxy_id first, else exit_ip)
# cool down this long too. deepseek mutes are **judged per IP** — cooling only the affected account, the system
# would feed the remaining accounts on this already-flagged IP in one by one to be muted (observed: accounts on the same IP died together within a minute).
# Only extends, never shortens, a later cooldown a sibling already has. 0 = off. env ENGINE_RATE_LIMIT_SIBLING_COOLDOWN_SEC.
_RATE_LIMIT_SIBLING_COOLDOWN_SEC = int(
    os.environ.get("ENGINE_RATE_LIMIT_SIBLING_COOLDOWN_SEC", str(2 * 3600))
)


def _safe_cooldown_until(ts) -> datetime:
    """Safely convert the worker-reported rate_limited_until (unix seconds) into a cooldown expiry.
    Guards against bad values: ① 13-digit millisecond timestamps → seconds; ② clamp to [now+1min, now+30 days]. Otherwise a millisecond timestamp
    would be parsed by utcfromtimestamp as year 50,000, permanently excluding the account from check-out = silently deleting it; a past
    value would make it immediately claimable and muted again → hot loop. Bad values always fall back to the default cooldown."""
    now = datetime.utcnow()
    default = now + timedelta(seconds=_RATE_LIMIT_COOLDOWN_SEC)
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return default
    if ts > 1e12:                      # Millisecond timestamp (far beyond seconds for year 2001)
        ts = ts / 1000.0
    try:
        when = datetime.utcfromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return default
    return min(max(when, now + timedelta(minutes=1)), now + timedelta(days=30))


def _cool_down_siblings(db: Session, row: EngineSessionORM) -> int:
    """When an account is rate-limited/muted, cool down sibling accounts on the same engine and egress; returns the number affected.

    The "same egress" check here is **broader** than check_out's grouping: same proxy_id **or** same exit_ip
    both count as the same physical egress. This is a safety fallback — better to cool a few extra accounts (only 2 hours) than miss
    accounts truly sharing the IP and keep feeding them in. check_out's quota grouping must use "the egress about to be used"
    (proxy_id first); the different definitions are intentional.
    """
    if _RATE_LIMIT_SIBLING_COOLDOWN_SEC <= 0:
        return 0
    if not row.proxy_id and not row.exit_ip:
        return 0
    until = datetime.utcnow() + timedelta(seconds=_RATE_LIMIT_SIBLING_COOLDOWN_SEC)
    n = 0
    sibs = (db.query(EngineSessionORM)
            .filter(EngineSessionORM.engine == row.engine)
            .filter(EngineSessionORM.id != row.id).all())
    for sib in sibs:
        same = ((row.proxy_id and sib.proxy_id == row.proxy_id)
                or (row.exit_ip and sib.exit_ip == row.exit_ip))
        if not same:
            continue
        if sib.rate_limited_until is None or sib.rate_limited_until < until:
            sib.rate_limited_until = until
            n += 1
    return n


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("/engine-sessions/upload", status_code=201)
def upload_session(
    payload: SessionUploadIn,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_upload_token),
):
    """Harvester uploads a storage_state from a fresh login.

    Two-level account recognition: first match by the stable identity account_id extracted from storage_state
    (extract_account_id, not relying on human input); if not extractable/no match, then match by
    (engine, source_label). A recognized existing account is renewed in place (refresh
    storage_state / captured_at / expires_at, status back to active, failure counts cleared),
    rather than inserting a new row — otherwise old accounts would hang "expired" forever and the pool would keep growing.
    Other historical duplicate rows of the same account (from early repeated uploads) are deleted along the way.
    Only when neither level recognizes it is a new row inserted.
    """
    if payload.engine not in _valid_engines():
        raise HTTPException(400, f"unsupported engine: {payload.engine!r}")
    if not _state_has_auth(payload.engine, payload.storage_state):
        if payload.engine in _LOCALSTORAGE_AUTH_ENGINES:
            raise HTTPException(400, "storage_state has no auth tokens in localStorage — likely not logged in")
        raise HTTPException(400, "storage_state has no cookies — likely not logged in")

    now = datetime.utcnow()
    label = (payload.source_label or "").strip() or None
    handle = ((payload.account_handle or "").strip()
              or extract_account_handle(payload.engine, payload.storage_state))
    account_id = extract_account_id(payload.engine, payload.storage_state)

    # Matching is limited to the same pool: even if one real account appears in both the default and workbench pools,
    # no cross-pool renew merge is done — otherwise a workbench upload would "renew" away the default pool's old row and the pools would mix.
    matches: list[EngineSessionORM] = []
    if account_id:
        matches = (
            db.query(EngineSessionORM)
            .filter(EngineSessionORM.engine == payload.engine)
            .filter(EngineSessionORM.pool == payload.pool)
            .filter(EngineSessionORM.account_id == account_id)
            .order_by(EngineSessionORM.captured_at.desc(), EngineSessionORM.id.desc())
            .all()
        )
    if not matches and label:
        # Old row has no account_id yet (historical data) / this engine cannot be extracted → fall back to label matching
        matches = (
            db.query(EngineSessionORM)
            .filter(EngineSessionORM.engine == payload.engine)
            .filter(EngineSessionORM.pool == payload.pool)
            .filter(EngineSessionORM.source_label == label)
            .order_by(EngineSessionORM.captured_at.desc(), EngineSessionORM.id.desc())
            .all()
        )
        if account_id:
            # If the label-matched row is already bound to another account_id, the same label switched accounts → not the same account
            matches = [m for m in matches if not m.account_id or m.account_id == account_id]

    row = matches[0] if matches else None
    for stale in matches[1:]:
        db.delete(stale)

    renewed = row is not None
    if renewed:
        row.account_id = account_id or row.account_id
        row.account_handle = handle or row.account_handle
        row.source_label = label or row.source_label
        row.storage_state = json.dumps(payload.storage_state, ensure_ascii=False)
        row.user_agent = payload.user_agent
        row.platform = payload.platform
        row.captured_at = now
        row.expires_at = now + timedelta(days=payload.ttl_days)
        row.status = "active"
        # New login = clean start: clear CAPTCHA / failure counts; keep use_count (used for cross-account load balancing)
        row.captcha_count = 0
        row.fail_counts_json = "{}"
        row.last_fail_type = None
        row.last_fail_at = None
        # login_ok must be cleared too, otherwise an account pinned False by login_lost cannot be released even after rescanning
        # — dispatch filtering only accepts (login_ok IS NULL OR login_ok = True). Reset to None (not probed)
        # rather than True directly: a storage_state from QR scanning may also be bad; let the first real use
        # confirm it, and if bad the probe will mark login_lost again.
        row.login_ok = None
        row.login_checked_at = None
        if payload.auth_type:
            row.auth_type = payload.auth_type
        if payload.credentials:                 # Only update when credentials are provided, to avoid clearing stored ones
            row.credentials = payload.credentials
    else:
        row = EngineSessionORM(
            engine=payload.engine,
            pool=payload.pool,
            source_label=label,
            account_id=account_id,
            account_handle=handle,
            storage_state=json.dumps(payload.storage_state, ensure_ascii=False),
            user_agent=payload.user_agent,
            platform=payload.platform,
            captured_at=now,
            expires_at=now + timedelta(days=payload.ttl_days),
            status="active",
            auth_type=payload.auth_type,
            credentials=payload.credentials,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "engine": row.engine,
            "expires_at": row.expires_at.isoformat(), "renewed": renewed,
            "account_id": row.account_id, "account_handle": row.account_handle}


@router.get("/engine-sessions/refresh-candidates")
def refresh_candidates(
    engine: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """Password-type accounts with encrypted credentials, which the daemon automatically re-logs in to renew. Candidate categories:
      1) offline (quarantined) — accounts quarantined after failures;
      2) **active but login session near/past expiry** (expires_at ≤ now + grace) — accounts that aged out without actively failing
         (status still active); the original implementation missed them and they were never auto-renewed (like id52/61).
      3) **probe already judged logged out** (login_ok = False) — 2026-09-20: deepseek #51/#57/#77, three accounts with
         passwords, had long-dead tokens but status still active and expires_at days away, unreachable by the first two rules,
         so nobody re-logged them in. Dispatch filtering already skips login_ok=False accounts; re-login is their only way out.
    Excludes accounts cooling under rate_limited_until (re-login cannot clear server-side rate limits and wastes a login slot) —
    but login_ok=False accounts are exempt: their "cooldown" usually comes from sibling penalties on the same proxy; the account itself
    was not running, so re-login is unrelated to rate limiting.
    Returns [{id, engine, credentials (ciphertext), account_handle, proxy_url}]; proxy_url lets the daemon
    re-login through the account's original egress IP, keeping 1 account = 1 IP consistent."""
    now = datetime.utcnow()
    grace = timedelta(hours=int(os.environ.get("ENGINE_REFRESH_EXPIRY_GRACE_HOURS", "6")))
    cutoff = now + grace
    login_dead = EngineSessionORM.login_ok == False                    # noqa: E712
    q = (db.query(EngineSessionORM)
         .options(undefer(EngineSessionORM.credentials))   # credentials returned per row below; load in one go (#43)
         .filter(EngineSessionORM.auth_type == "password")
         .filter(EngineSessionORM.credentials != None)  # noqa: E711
         .filter(or_(EngineSessionORM.rate_limited_until == None,       # noqa: E711
                     EngineSessionORM.rate_limited_until <= now,
                     login_dead))
         .filter(or_(
             EngineSessionORM.status == "quarantined",
             login_dead,
             and_(EngineSessionORM.expires_at != None,                  # noqa: E711
                  EngineSessionORM.expires_at <= cutoff),
         )))
    if engine:
        q = q.filter(EngineSessionORM.engine == engine)
    rows = q.order_by(EngineSessionORM.last_fail_at.asc().nullsfirst()).limit(max(1, min(limit, 100))).all()

    def _resolve_proxy(r):
        # Consistent with check-out proxy resolution: proxy_id → engine_proxies.proxy_url, else the raw proxy_override string.
        p = r.proxy_override
        if r.proxy_id:
            px = db.query(EngineProxyORM).filter(EngineProxyORM.id == r.proxy_id).first()
            if px and not px.disabled and px.proxy_url:
                p = px.proxy_url
        return p

    return [{"id": r.id, "engine": r.engine, "credentials": r.credentials,
             "account_handle": r.account_handle, "proxy_url": _resolve_proxy(r)} for r in rows]


@router.post("/engine-sessions/check-out", response_model=SessionCheckedOut)
def check_out(
    engine: str,
    worker_id: Optional[int] = None,
    pool: str = "default",
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """browser-service gets one active session.

    Identity binding (2026-06): with worker_id, prefer the session that worker used last time (sticky lease),
    so accounts stick to a fixed worker / egress IP, reducing per-IP risk control; if none, claim an idle / lease-expired
    one and bind it; only when all are taken by others fall back to grabbing the "least used". Without worker_id → old strategy.

    Base filter: active + captcha_count < threshold + not expired. Order: use_count ASC, last_used ASC.
    """
    if engine not in _valid_engines():
        raise HTTPException(400, f"unsupported engine: {engine!r}")

    now = datetime.utcnow()
    today = _local_today()
    cap = _session_daily_cap(engine)
    min_gap = _session_min_gap_sec(engine)
    ip_cap = _ip_daily_cap(engine)
    ip_hourly_cap = _ip_hourly_cap(engine)
    ip_min_gap = _ip_min_gap_sec(engine)
    hour_key = _local_hour_key()

    # Egress usage: grouped by _egress_key (proxy_id first), checked against two windows; if either is full the whole group is not dispatched.
    #   · daily window (ip_cap)   — total fallback
    #   · hourly window (ip_hourly_cap) — rate control; this layer targets risk control itself
    # Computed in Python — the pool has only a few dozen rows, not worth cross-DB (sqlite/PG) string-concatenation grouping SQL.
    blocked_ids: set = set()
    if ip_cap > 0 or ip_hourly_cap > 0:
        by_egress: dict = {}
        rows_today = (db.query(EngineSessionORM)
                      .filter(EngineSessionORM.engine == engine).all())
        for r in rows_today:
            k = _egress_key(r)
            if not k:
                continue
            g = by_egress.setdefault(k, {"used": 0, "used_hour": 0, "ids": [],
                                        "last_used": None})
            g["ids"].append(r.id)
            # The egress's "last used time" = max last_used_at across accounts in the group.
            # No new column needed — this information already exists.
            if r.last_used_at and (g["last_used"] is None or r.last_used_at > g["last_used"]):
                g["last_used"] = r.last_used_at
            if r.used_date == today:
                g["used"] += (r.used_today or 0)
            # Window key not equal to the current hour = leftover count from the previous hour, should not be counted
            if r.used_hour_key == hour_key:
                g["used_hour"] += (r.used_hour or 0)
        for g in by_egress.values():
            if ip_cap > 0 and g["used"] >= ip_cap:
                blocked_ids.update(g["ids"])
            elif ip_hourly_cap > 0 and g["used_hour"] >= ip_hourly_cap:
                blocked_ids.update(g["ids"])
            elif (ip_min_gap > 0 and g["last_used"]
                  and (now - g["last_used"]).total_seconds() < ip_min_gap):
                # Egress interval: this IP was just used and the interval has not elapsed — the whole group yields until the next round.
                # This is what actually flattens density: the cap only limits "how many per hour", not whether they are
                # all fired within the same minute, and risk control looks at the latter.
                blocked_ids.update(g["ids"])

    def _base():
        q = (
            db.query(EngineSessionORM)
            .options(undefer(EngineSessionORM.storage_state))   # The selected account must ship its login session; load in one go (#43)
            # Row lock + skip rows locked by other concurrent check-outs: eliminates the race where "two workers' .first() get
            # the same row, double lease + use_count undercount" (effective on Postgres; no-op under SQLite's serialized writes).
            .with_for_update(skip_locked=True)
            .filter(EngineSessionORM.engine == engine)
            .filter(EngineSessionORM.pool == pool)
            .filter(EngineSessionORM.status == "active")
            .filter(EngineSessionORM.captcha_count < _CAPTCHA_QUARANTINE_THRESHOLD)
            .filter((EngineSessionORM.expires_at == None) | (EngineSessionORM.expires_at > now))  # noqa: E711
        )
        # Exclude accounts "disabled" by admins / cooling from rate limits — so unschedulable_until / rate_limited_until really take effect
        q = q.filter(
            (EngineSessionORM.unschedulable_until == None)        # noqa: E711
            | (EngineSessionORM.unschedulable_until <= now)
        ).filter(
            (EngineSessionORM.rate_limited_until == None)         # noqa: E711
            | (EngineSessionORM.rate_limited_until <= now)
        )
        # ① Accounts confirmed logged out by probing are no longer dispatched (login_ok=False). None = not probed → dispatch as before;
        # probing is an incremental capability and must not lock the whole pool just because accounts were not probed.
        # 2026-08-24: all 5 Doubao accounts were active and unexpired but the server saw user_id:0, and qwen dispatched
        # anonymous pages — expires_at alone (the 7-day TTL stamped at upload) cannot tell alive from dead.
        q = q.filter((EngineSessionORM.login_ok == None)          # noqa: E711
                     | (EngineSessionORM.login_ok == True))       # noqa: E712
        # Per-account hard daily cap: per-account daily_cap overrides the engine default cap (coalesce); 0 = unlimited.
        # Exclude accounts that "hit the cap today" (used_date != today or never used → treated as below cap).
        eff_cap = func.coalesce(EngineSessionORM.daily_cap, cap)
        q = q.filter(
            (EngineSessionORM.used_date == None)                  # noqa: E711
            | (EngineSessionORM.used_date != today)
            | (eff_cap == 0)
            | (EngineSessionORM.used_today < eff_cap)
        )
        # Per-account minimum request interval: rest recently used accounts, do not ask back to back (risk control looks at rhythm, not just totals)
        if min_gap > 0:
            q = q.filter(
                (EngineSessionORM.last_used_at == None)           # noqa: E711
                | (EngineSessionORM.last_used_at < now - timedelta(seconds=min_gap))
            )
        # Per-egress daily cap: egresses that are full today get no further dispatch for the whole group today
        if blocked_ids:
            q = q.filter(EngineSessionORM.id.notin_(sorted(blocked_ids)))
        return q

    # ② Ordering: originally pure `use_count ASC` (least used first), which means **newly added accounts come first** —
    # a freshly uploaded dead account would be repeatedly dispatched first, while battle-tested good accounts sat last (measured 2026-08-24:
    # newly added qwen id=86 was claimed 25 times in twenty minutes).
    # Add a leading key: accounts used 3+ times but never successful (last_success_at empty) are pushed to the end.
    # Unprobed/new accounts are unaffected; rotation fairness is preserved.
    _never_ok = sa_case(
        (and_(EngineSessionORM.last_success_at == None,           # noqa: E711
              EngineSessionORM.use_count > 3), 1),
        else_=0,
    )
    # ③ The primary sort key changed from **lifetime** use_count to **today's** usage.
    #
    # use_count only grows, so the spread keeps widening — measured 2026-09-09 the deepseek pool ranged from 222 to 531,
    # a gap of 309. And check_out prefers sticky (first picking among "accounts I bound last time"), so each worker
    # keeps picking the lowest lifetime counts within its own group, and the top 1~2 in the group never get a turn:
    #     worker 166858703 holds 10, used only 8 that day; #33(380), #78(338) had zero use for several days
    #     worker 1747477915 holds 7; #73(334) has not been dispatched since 09-04
    # When daily demand is below pool capacity this is harmless (low-count accounts cannot even use up their daily_cap),
    # but once the pool is full it becomes a real loss — on 09-04, 480 were queued and 14 "no active session" occurred that day.
    #
    # With today's usage, rotation **resets naturally at midnight** and never accumulates a permanent tail;
    # within a day it is still "whoever used least goes first", so the density-flattening effect is unchanged.
    # last_used_at is the second key (at equal usage, the longest untouched goes first); use_count becomes the final stable sort key.
    #
    # The _never_ok leading key is unchanged: a freshly added dead account with used_today=0 sorts first, but after 3 uses
    # without success it is pushed to the end — exactly the pattern it was meant to fix; changing the sort key does not affect it.
    _used_today_eff = sa_case(
        (EngineSessionORM.used_date == today, EngineSessionORM.used_today),
        else_=0,     # A count not from today = not used today yet
    )
    _least = (_never_ok.asc(),
              _used_today_eff.asc(),
              EngineSessionORM.last_used_at.asc().nullsfirst(),
              EngineSessionORM.use_count.asc())

    if worker_id is not None:
        # 0) Accounts explicitly pinned to this worker (preferred_worker_id) first — enforces the "account ↔ fixed IP/platform" topology
        row = _base().filter(EngineSessionORM.preferred_worker_id == worker_id).order_by(*_least).first()
        # 1) The account I bound last time (sticky)
        if row is None:
            row = _base().filter(EngineSessionORM.leased_by_worker_id == worker_id).order_by(*_least).first()
        if row is None:
            # 2) Idle / lease-expired ones → claim and bind to me
            row = (_base()
                   .filter((EngineSessionORM.leased_by_worker_id == None)  # noqa: E711
                           | (EngineSessionORM.leased_until == None)       # noqa: E711
                           | (EngineSessionORM.leased_until < now))
                   .order_by(*_least).first())
        # Note: there is no longer a "grab one if all are taken" fallback — grabbing an account leased by another worker (leased_until>now)
        # would put the same account on two egress IPs at once, triggering cross-region/IP-density risk control (one root cause of the
        # deepseek mute surge). Better to return None (404) and let the task back off and retry (visible_after backoff is in place) until the lease
        # expires (_LEASE_TTL_HOURS) or an account frees up.
        if row is not None:
            row.leased_by_worker_id = worker_id
            row.leased_until = now + timedelta(hours=_LEASE_TTL_HOURS)
    else:
        row = _base().order_by(*_least).first()

    if row is None:
        # Keep the "no active session available" prefix — dispatch's _TRANSIENT_ERR relies on it to classify
        # a "transient failure" → the task goes back to the queue with backoff, without consuming retry budget. The reason after it is only for troubleshooting.
        why = []
        if min_gap > 0:
            why.append(f"min_gap={min_gap}s")
        if blocked_ids:
        # The two gates are reported separately — during troubleshooting on 2026-08-30 we only had "ip_cap=25 blocked=40",
        # which could not tell whether the total was exhausted or this hour was too dense; those point in very different directions.
            why.append(f"ip_cap={ip_cap} ip_hourly_cap={ip_hourly_cap}"
                       f" ip_min_gap={ip_min_gap}s hour={hour_key}"
                       f" blocked={len(blocked_ids)}")
        raise HTTPException(404, f"no active session available for engine={engine} pool={pool}"
                                 + (f" ({', '.join(why)})" if why else ""))

    # Immediately +1 use_count + last_used_at, so concurrent check-outs do not get the same row
    row.use_count = (row.use_count or 0) + 1
    row.last_used_at = now
    # Per-account daily count: reset across days, otherwise +1 (precise per-account daily usage control)
    if row.used_date != today:
        row.used_date = today
        row.used_today = 1
    else:
        row.used_today = (row.used_today or 0) + 1
    # Hourly window count: same shape, reset across hours. Egress rate limiting sums these (see by_egress above)
    if row.used_hour_key != hour_key:
        row.used_hour_key = hour_key
        row.used_hour = 1
    else:
        row.used_hour = (row.used_hour or 0) + 1
    db.commit()
    db.refresh(row)

    # Proxy resolution: prefer the proxy-library reference (proxy_id → engine_proxies.proxy_url), else fall back to the raw string
    # proxy_override. Change the url in the proxy library and all accounts bound to it get the new value at check-out automatically.
    resolved_proxy = row.proxy_override
    if row.proxy_id:
        px = db.query(EngineProxyORM).filter(EngineProxyORM.id == row.proxy_id).first()
        if px and not px.disabled and px.proxy_url:
            resolved_proxy = px.proxy_url

    return SessionCheckedOut(
        id=row.id,
        engine=row.engine,
        source_label=row.source_label,
        storage_state=json.loads(row.storage_state),
        use_count=row.use_count,
        egress=row.egress,
        proxy_override=resolved_proxy,
        user_agent=row.user_agent,
    )


# ③ For engine-level outage detection: window / minimum accounts involved / threshold ratio
_OUTAGE_WINDOW_SEC = int(os.environ.get("ENGINE_OUTAGE_WINDOW_SEC", "900"))
_OUTAGE_MIN_SESSIONS = int(os.environ.get("ENGINE_OUTAGE_MIN_SESSIONS", "3"))
_OUTAGE_RATIO = float(os.environ.get("ENGINE_OUTAGE_RATIO", "0.8"))


def _engine_wide_outage(db: Session, engine: str, fail_type: str) -> bool:
    """Decide "this is an engine-level outage, not broken accounts".

    Criterion: within the last _OUTAGE_WINDOW_SEC, more than _OUTAGE_RATIO of the engine's **used accounts**
    had their latest failure of the same type, and at least _OUTAGE_MIN_SESSIONS accounts are involved.
    One bad account does not implicate others; all accounts failing the same way can only mean the page/risk control changed.
    """
    since = datetime.utcnow() - timedelta(seconds=_OUTAGE_WINDOW_SEC)
    rows = (db.query(EngineSessionORM.last_fail_type,
                     EngineSessionORM.last_fail_at,
                     EngineSessionORM.last_success_at)
            .filter(EngineSessionORM.engine == engine)
            .filter(EngineSessionORM.last_used_at != None)          # noqa: E711
            .filter(EngineSessionORM.last_used_at >= since)
            .all())
    if len(rows) < _OUTAGE_MIN_SESSIONS:
        return False
    # Only count accounts whose "latest result is this failure". last_fail_type is not cleared on SUCCESS (kept as a trace),
    # so an account succeeding daily now still carries the label if it hit empty_answer a few days ago —
    # every account in the pool hits it once sooner or later, so ratio is always ≥ 0.8, every empty_answer is treated as
    # an "engine-level outage" and not counted, and dead accounts are never quarantined. Tongyi on 2026-09-14 was exactly this: all 20 accounts carried
    # the empty_answer label, 7 had not succeeded once in three days but kept being dispatched, and the engine success rate fell to 30%.
    def _currently_failing(t, fail_at, ok_at) -> bool:
        if t != fail_type or fail_at is None or fail_at < since:
            return False
        return ok_at is None or fail_at > ok_at
    same = sum(1 for t, fail_at, ok_at in rows if _currently_failing(t, fail_at, ok_at))
    return same / len(rows) >= _OUTAGE_RATIO


@router.post("/engine-sessions/check-in")
def check_in(
    payload: SessionCheckIn,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """browser-service reports a session is done + this run's FailureType.

    SUCCESS  → reset fail_counts (so historical failures do not accumulate forever)
    CAPTCHA  → captcha_count++, threshold 3 → quarantine
    LOGIN_LOST → quarantine immediately (session invalid, not kept)
    EMPTY_ANSWER / TIMEOUT → fail_counts[type]++, threshold 5 → quarantine
    DOM_NOT_FOUND → fail_counts[type]++, threshold 3 → quarantine + alert
    CRASH    → skip (not counted against the session; treated as a worker-side issue)

    Backward-compat: old workers still send captcha_triggered=true/false; SessionCheckIn's
    validator already translates it to result=CAPTCHA/SUCCESS, so only payload.result is checked here.
    """
    row = db.query(EngineSessionORM).get(payload.id)
    if row is None:
        raise HTTPException(404, f"session id={payload.id} not found")

    result = payload.result
    policy = (_QUARANTINE_POLICY_BY_ENGINE.get((row.engine or "").lower(), {}).get(result)
              or _QUARANTINE_POLICY.get(result, {}))
    siblings_cooled = 0

    # Parse existing fail_counts (SQLite stores a JSON string)
    try:
        fc = json.loads(row.fail_counts_json or "{}")
    except json.JSONDecodeError:
        fc = {}

    if policy.get("reset"):
        # SUCCESS: clear accumulated failures but keep captcha_count (captcha is an independent counter)
        fc = {}
        now = datetime.utcnow()
        # Success is the hardest evidence that "this account is still alive" — the table used to record only failures, not successes,
        # so dispatch ordering could not tell whether an account was still working (see the _never_ok demotion).
        row.last_success_at = now
        row.login_ok = True
        row.login_checked_at = now
        # Rolling renewal: after a successful run the worker returns the storage_state refreshed by active use → write back + extend expires_at.
        # If cookies are empty (not captured / malformed), do not overwrite storage_state, to avoid replacing a good session with an empty shell.
        # localStorage-auth engines (kimi) check three keys — their refresh_token rotates on every renewal,
        # and this write-back is the only way renewal gets persisted.
        if _state_has_auth(row.engine, payload.storage_state):
            row.storage_state = json.dumps(payload.storage_state, ensure_ascii=False)
            row.captured_at = now
        # Whether or not storage_state was returned, any "successful use" extends expires_at — otherwise deepseek's pure
        # HTTP token flow (never returns cookies) and some QR adapters would be wrongly killed by the hard TTL at captured_at+7d,
        # even with a valid login session. The TTL semantics change from "absolute 7 days after capture" to "7 days after the last successful use".
        # Truly dead accounts never have a SUCCESS check-in (they go login_lost→quarantine), so only live accounts are extended.
        row.expires_at = now + timedelta(days=_RENEW_TTL_DAYS)
    elif policy.get("cooldown"):
        # RATE_LIMITED: rate-limited/muted by risk control → cool down this account (rate_limited_until), no quarantine, no fail_counts penalty.
        # Use mute_until (unix seconds) if provided, otherwise the default cooldown. check_out skips accounts that are cooling down.
        if payload.rate_limited_until:
            row.rate_limited_until = _safe_cooldown_until(payload.rate_limited_until)
        else:
            row.rate_limited_until = datetime.utcnow() + timedelta(
                seconds=int(policy.get("cooldown_sec", _RATE_LIMIT_COOLDOWN_SEC)))
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
        # Also cool down sibling accounts on the same egress — mutes are judged per IP, and this egress is already flagged;
        # dispatching more accounts there only gets them muted one by one, with the penalty ladder growing longer (observed up to dozens of days).
        if policy.get("siblings", True):
            siblings_cooled = _cool_down_siblings(db, row)
    elif policy.get("skip"):
        # CRASH: treated as a worker-side issue, session counters untouched
        pass
    elif policy.get("immediate"):
        # LOGIN_LOST: quarantine immediately
        row.status = "quarantined"
        fc[result.value] = fc.get(result.value, 0) + 1
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
        # Also pin login_ok — status may be manually set back to active (very common while troubleshooting:
        # on the night of 2026-08-29, as an emergency, expires_at was extended and three accounts manually revived, but their
        # cookies had long been revoked server-side, so after revival they kept being dispatched and every run idled). login_ok=False
        # is the second gate: only a rescan (storage_state update) should release it; extending time does not count.
        row.login_ok = False
        row.login_checked_at = datetime.utcnow()
    elif _engine_wide_outage(db, row.engine, result.value):
        # ③ Page-level outages do not penalize accounts: when **almost all accounts** of an engine fail the same way within a short time,
        # the engine was redesigned / risk-control policy changed, not these accounts broken. Counting as usual would quarantine the whole pool —
        # on 2026-08-24 a decorative image on Tongyi's first screen covered the input box, every account hit dom_not_found, and 6 of 12
        # accounts were quarantined while none was actually broken. In this case only record a trace: no accumulation, no quarantine.
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
        _log.warning("[session-pool] %s suspected engine-level outage (%s): many accounts failed the same way in a short time, "
                     "not penalizing or quarantining accounts this time", row.engine, result.value)
    else:
        # Cumulative: count + quarantine when the threshold is reached
        fc[result.value] = fc.get(result.value, 0) + 1
        row.last_fail_type = result.value
        row.last_fail_at = datetime.utcnow()
        # captcha uses its legacy dedicated column; others use fail_counts_json
        if policy.get("legacy_col") == "captcha_count":
            row.captcha_count = (row.captcha_count or 0) + 1
            count_for_threshold = row.captcha_count
        else:
            count_for_threshold = fc[result.value]
        threshold = policy.get("threshold")
        if threshold is not None and count_for_threshold >= threshold:
            row.status = "quarantined"

    row.fail_counts_json = json.dumps(fc)

    # Egress IP report (best-effort): the actual egress IP the worker resolved via the egress proxy
    if payload.exit_ip:
        row.exit_ip = payload.exit_ip
        row.exit_ip_at = datetime.utcnow()

    # Identity binding (2026-06): check-in does not clear the lease — the account keeps sticking to this worker/IP,
    # and the next check-out from the same worker gets it back first (sticky). A dead worker's accounts are released automatically
    # when leased_until expires (_LEASE_TTL_HOURS); quarantined / expired ones are excluded by the base filter, so the lease is harmless.

    db.commit()
    return {
        "id": row.id,
        "status": row.status,
        "use_count": row.use_count,
        "captcha_count": row.captcha_count,
        "fail_counts": fc,
        "last_fail_type": row.last_fail_type,
        "siblings_cooled": siblings_cooled,
    }


class RatePolicyIn(BaseModel):
    """Only send the fields to change; null means "do not override this one, fall back to env/code default"."""
    session_min_gap_sec: Optional[int] = None
    ip_min_gap_sec: Optional[int] = None
    ip_hourly_cap: Optional[int] = None
    ip_daily_cap: Optional[int] = None
    per_account_daily_cap: Optional[int] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


@router.get("/engine-sessions/rate-policies")
def list_rate_policies(
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """List each engine's rate-limit policy + the current **effective values** (including env/default fallback).

    Both columns are given: the stored value (possibly NULL = no override) and the final effective value — looking only at the stored value misleads;
    on 2026-08-30 we spent ages unable to tell "not configured" from "configured as 0".
    """
    rows = db.query(EngineRatePolicyORM).order_by(EngineRatePolicyORM.engine).all()
    stored = {r.engine: r for r in rows}
    engines = sorted(set(list(stored.keys())
                         + list(_DEFAULT_MIN_GAP_SEC.keys())
                         + list(_DEFAULT_IP_DAILY_CAP.keys())
                         + list(_DEFAULT_IP_HOURLY_CAP.keys())
                         + list(_DEFAULT_IP_MIN_GAP_SEC.keys())))
    out = []
    for e in engines:
        r = stored.get(e)
        out.append({
            "engine": e,
            "stored": None if r is None else {
                "session_min_gap_sec": r.session_min_gap_sec,
                "ip_min_gap_sec": r.ip_min_gap_sec,
                "ip_hourly_cap": r.ip_hourly_cap,
                "ip_daily_cap": r.ip_daily_cap,
                "per_account_daily_cap": r.per_account_daily_cap,
                "enabled": bool(r.enabled),
                "note": r.note,
                "updated_by": r.updated_by,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            },
            "effective": {
                "session_min_gap_sec": _session_min_gap_sec(e),
                "ip_min_gap_sec": _ip_min_gap_sec(e),
                "ip_hourly_cap": _ip_hourly_cap(e),
                "ip_daily_cap": _ip_daily_cap(e),
            },
            # Shows at a glance the theoretical daily capacity these parameters allow (per egress)
            "theoretical_per_ip_daily": (
                _ip_hourly_cap(e) * 24 if _ip_hourly_cap(e) > 0 else None),
        })
    return out


@router.put("/engine-sessions/rate-policies/{engine}")
def upsert_rate_policy(
    engine: str,
    body: RatePolicyIn,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_service_token),
):
    """Create/update an engine's rate-limit policy. The cache is cleared immediately, no 60s wait, no restart."""
    e = (engine or "").strip().lower()
    if not e:
        raise HTTPException(400, "engine 不能为空")
    row = (db.query(EngineRatePolicyORM)
           .filter(EngineRatePolicyORM.engine == e).one_or_none())
    if row is None:
        row = EngineRatePolicyORM(engine=e)
        db.add(row)
    for f in ("session_min_gap_sec", "ip_min_gap_sec", "ip_hourly_cap",
              "ip_daily_cap", "per_account_daily_cap", "note"):
        v = getattr(body, f)
        if v is not None:
            setattr(row, f, v)
    if body.enabled is not None:
        row.enabled = body.enabled
    row.updated_at = datetime.utcnow()
    row.updated_by = "api"
    db.commit()
    invalidate_rate_policy_cache(e)
    return {
        "ok": True, "engine": e,
        "effective": {
            "session_min_gap_sec": _session_min_gap_sec(e),
            "ip_min_gap_sec": _ip_min_gap_sec(e),
            "ip_hourly_cap": _ip_hourly_cap(e),
            "ip_daily_cap": _ip_daily_cap(e),
        },
    }


@router.get("/engine-sessions/pool-status", response_model=List[PoolStatusEntry])
def pool_status(db: Session = Depends(get_db)):
    """For the dashboard: how many active / quarantined / expired rows each engine currently has."""
    # One grouped aggregate computed in the DB (#43): previously each engine read full rows (including storage_state) back and counted in Python,
    # 12 queries, tens of MB. now is passed in from Python's utcnow — the DB timezone is Asia/Shanghai,
    # and the DB's now() would be 8 hours off from the naive-UTC expires_at.
    now = datetime.utcnow()
    E = EngineSessionORM
    is_active = E.status == "active"
    agg = {
        r.engine: r
        for r in db.query(
            E.engine,
            func.sum(sa_case((is_active & ((E.expires_at == None) | (E.expires_at > now)), 1), else_=0)).label("active"),  # noqa: E711
            func.sum(sa_case((E.status == "quarantined", 1), else_=0)).label("quarantined"),
            func.sum(sa_case(((E.expires_at != None) & (E.expires_at <= now), 1), else_=0)).label("expired"),  # noqa: E711
            func.min(sa_case((is_active, E.captured_at))).label("oldest_active"),
        ).group_by(E.engine).all()
    }
    out: List[PoolStatusEntry] = []
    for engine in sorted(_valid_engines()):
        r = agg.get(engine)
        out.append(PoolStatusEntry(
            engine=engine,
            active=int(r.active or 0) if r else 0,
            quarantined=int(r.quarantined or 0) if r else 0,
            expired=int(r.expired or 0) if r else 0,
            oldest_active_captured_at=r.oldest_active if r else None,
        ))
    return out
