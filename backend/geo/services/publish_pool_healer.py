"""Publish pool self-healing -- freshness / renewal / inspection (design section F6, development order step 7).

## What disease it treats

`publish_accounts.expires_at` is a rolling TTL of "last **successful use** + N days", and renewal only happens
in the receipt's success branch (`policy.get("reset")` in `api/publish_dispatch.py`). So:

    no tasks dispatched for a while -> TTL expires -> expired accounts are excluded by claim's filter
    -> never claim a task -> never a successful receipt -> never renewed = **permanent death**

The engine pool already lost 6 Wenxin accounts this way (`docs/06-发布运维/运维/排障-账号池误判与TTL死锁.md`); item 1 of that doc's
"unfixed mechanism gaps" says "expired accounts have no revival path: the TTL deadlock will inevitably recur". Publish accounts are
**irreplaceable existing accounts** (with history, followers and weight; once banned/lost they cannot be replaced), so this path must be fixed.

The fix: periodically open a browser and "use" the account once --

    freshness  one real logged-in homepage visit (browser-service POST /publish/keepalive)
    inspection after the visit, read the platform identity API once = **live ticket probe**
    renewal    after the visit, export the refreshed storage_state, write it back + extend expires_at

## 🔴 Three rules that must not be broken

1. **Judgment trusts only live probes, never the `fail_type` reported by workers.** A failure type is only the reporter's judgment,
   not the truth. Two incidents prove it: 6 of 8 Wenxin accounts were wrongly expired by the hard TTL deadlock; 9 good qwen accounts were
   wrongly quarantined by an overly aggressive `login_lost` classification.
2. **"Cannot connect" is not "logged out".** `/publish/keepalive` separates the two into the `unreachable` and
   `login_lost` fail_types, and this module **changes not a single field** on `unreachable` --
   that is the mechanical guarantee for F6 acceptance item 2 (login_lost caused by a deliberate network cut will not mark the account expired).
3. **`unschedulable` never auto-recovers, not even probed.** It means egress IP drift / suspected ban,
   and continuing to retry automatically only turns "possibly flagged" into "definitely banned". Recovery only goes through the admin's
   `POST /api/publish-accounts/{id}/reinstate` (requires a person to check the platform backend + give a reason).

## Three things that need no browser (pure DB, done along the way)

  B. TTL extension for jailed accounts -- prevents "dying right when released" (expires_at of deepseek's 7 jailed accounts were **all** earlier than
     their respective release times, effectively a suspended death sentence). Copied from the same-named branch in scripts/session_pool_healer.py.
  C. Claim leak fallback -- when a worker process is killed, queue rows stay stuck in `claimed` forever. 🔴 Only mark failed,
     **never resubmit**, see `_reap_stale_claims` for why.
  D. Low-water mark / correlated ban sign alerts -- data source is `api/publish_accounts.py::pool_status` directly.

## Usage

    # Read-only by default: print a report, writing nothing to the DB
    python -m geo.services.publish_pool_healer
    # Actually write to the DB
    python -m geo.services.publish_pool_healer --apply
    # Only handle one account / one platform; --no-probe only does the three pure-DB things (no browser)
    python -m geo.services.publish_pool_healer --apply --account 43
    python -m geo.services.publish_pool_healer --apply --platform zhihu --no-probe

crontab (scheduled after session_pool_healer at 00:50):

    55 0 * * * cd .../backend && ./venv/bin/python -m geo.services.publish_pool_healer \
        --apply >> data/publish_pool_healer.log 2>&1

crontab is used deliberately instead of APScheduler: a misconfigured `GEO_SCHEDULER_LEADER` static flag leader means
"running 0 times or N times", silently (already noted in CLAUDE.md), and running this N times means **the same account opened by N
processes' browsers at once** -- exactly what the account-level exclusive lease is meant to prevent.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from geo.api.publish_accounts import (
    BROWSER_SERVICE_URL,
    RENEW_TTL_DAYS,
    local_today,
    pool_status,
    resolve_proxy_url,
)
from geo.models.ai_telemetry import TopicDocPublishTaskORM
from geo.models.publish_accounts import (
    CHANNEL_SERVER_POOL,
    EGRESS_HOST,
    PublishAccountORM,
    PublishDispatchTaskORM,
    PublishFailureType,
)
from geo.services.publish_locks import lock_account
from geo.utils.publish_crypto import (
    PublishCryptoUnavailable,
    decrypt_required,
    encrypt_required,
)

log = logging.getLogger(__name__)


# -- Configuration ----------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Regular inspection cadence: probe again when the last successful probe is older than this
PROBE_EVERY_HOURS = _int_env("PUBLISH_HEAL_PROBE_EVERY_HOURS", 24)
# Freshness threshold: if not actually used for publishing for this many days, "use" it via a freshness visit
KEEPALIVE_IDLE_DAYS = _int_env("PUBLISH_KEEPALIVE_IDLE_DAYS", 3)
# Deadlock rescue window: accounts whose expires_at falls in this window (or already expired) get top probe priority
PRE_EXPIRY_HOURS = _int_env("PUBLISH_HEAL_PRE_EXPIRY_HOURS", 48)
# Max browser runs per round. The cap is defensive: with a large pool, probing everything at once would fill browser-service's
# concurrency semaphore and push real publishing past its deadline (publishing has time requirements, freshness does not).
MAX_PER_RUN = _int_env("PUBLISH_HEAL_MAX_PER_RUN", 10)
# 🔴 Upper bound of the random interval between runs. Equal intervals are themselves a machine signature (design section 4 P1 item 9) --
# 80 accounts queuing to visit their homepages within the same minute is more conspicuous than any single visit.
JITTER_SEC = _int_env("PUBLISH_HEAL_JITTER_SEC", 300)
# How long the healer holds the lease while probing. Just enough to finish one keepalive run (server budget 120s).
HEAL_LEASE_MINUTES = _int_env("PUBLISH_HEAL_LEASE_MINUTES", 5)
# Max automatic revivals per account -- prevents a "revive -> quarantine -> revive" spin from masking the real problem.
# Copied from session_pool_healer's MAX_HEALS.
MAX_HEALS = _int_env("PUBLISH_HEAL_MAX_PER_ACCOUNT", 5)
# Quarantine only after this many consecutive **authoritative** logouts. 2 rather than 1: the engine pool set login_lost to immediate quarantine,
# and qwen classified "input box not found" as login_lost too, sending 9 good accounts to quarantine at once.
LOGIN_LOST_THRESHOLD = _int_env("PUBLISH_HEAL_LOGIN_LOST_THRESHOLD", 2)
# A jailed account's expires_at must last at least until "release time + this many days", otherwise it is already TTL-dead on release day.
MUTE_GRACE_DAYS = _int_env("PUBLISH_HEAL_MUTE_GRACE_DAYS", 7)
# Terminate queue rows still without a receipt 30 minutes after being claimed. Shares the same env as the API's manual fallback threshold.
CLAIM_STALE_MINUTES = _int_env("PUBLISH_CLAIM_STALE_MINUTES", 30)
# Alert when schedulable accounts per platform fall below this
MIN_USABLE = _int_env("PUBLISH_POOL_MIN_USABLE", 1)
ALERT_WEBHOOK = ((os.environ.get("PUBLISH_POOL_ALERT_WEBHOOK") or "").strip()
                 or (os.environ.get("ENGINE_POOL_ALERT_WEBHOOK") or "").strip())
KEEPALIVE_HTTP_TIMEOUT = float(os.environ.get("PUBLISH_KEEPALIVE_HTTP_TIMEOUT", "180"))

# 🔴 Sentinel value written into lease_worker_id when the healer itself holds the lease.
#
# Why a sentinel instead of owner_worker_id: when releasing the lease we must be able to answer "did I take this lease?".
# If written as owner, the healer could clear a lease held by **real publishing** as if it were its own --
# letting claim hand this account to a second task in the middle of publishing.
#
# A negative number can never collide with a real worker_id: those are crc32(WORKER_UID) & 0x7FFFFFFF, always non-negative.
HEALER_LEASE_WORKER_ID = -1

# The non-auto-recoverable tier of probe results, same metric definition as report's terminal branch.
_TERMINAL_FAIL = {"identity_mismatch"}


# -- Probe client (injectable, so acceptance scripts need no real browser) ------------------

def probe_account_http(platform: str, expected_account_id: str, storage_state: dict,
                       proxy_url: Optional[str], ua_platform: Optional[str]) -> dict:
    """Call browser-service's POST /publish/keepalive.

    🔴 The account's bound `proxy_url` must be passed. A freshness visit is a **real logged-in visit**; going out through the wrong
    egress is a remote login -- exactly the incident this module is meant to prevent. The `ENGINE_AUTHZ_PROXY` fallback the engine pool kept
    to get around deepseek region limits must not be copied for publish accounts (design section 4 P0 item 3).

    Response shape: see publish_api.py::keepalive. Network-level failures are all classified `unreachable` --
    **failing to reach browser-service is not the account's fault** and must not turn into action on the account.
    """
    body: dict[str, Any] = {"platform": platform,
                            "expected_account_id": expected_account_id,
                            "storage_state": storage_state}
    if proxy_url:
        body["proxy_url"] = proxy_url
    if ua_platform:
        body["ua_platform"] = ua_platform
    headers = {"Content-Type": "application/json"}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=KEEPALIVE_HTTP_TIMEOUT) as c:
            r = c.post(f"{BROWSER_SERVICE_URL}/publish/keepalive",
                       json=body, headers=headers)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reachable": False, "fail_type": "unreachable",
                "error": f"打不到 browser-service:{type(e).__name__}: {e}"}
    if r.status_code != 200:
        return {"ok": False, "reachable": False, "fail_type": "unreachable",
                "error": f"browser-service /publish/keepalive HTTP {r.status_code}: "
                         f"{r.text[:200]}"}
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reachable": False, "fail_type": "unreachable",
                "error": f"响应不是 JSON:{e}"}


def local_worker_id_http() -> Optional[int]:
    """Ask the local browser-service for its worker_id (for ownership checks).

    Returns None if unavailable, and the caller then **skips all probes** rather than "assuming ownership is fine" --
    see the comment on `_ownership_ok`.
    """
    headers = {}
    tok = (os.environ.get("PUBLISH_SERVICE_TOKEN") or "").strip()
    if tok:
        headers["X-Publish-Service-Token"] = tok
    try:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(f"{BROWSER_SERVICE_URL}/publish/worker-info", headers=headers)
        if r.status_code != 200:
            return None
        wid = r.json().get("worker_id")
        return int(wid) if isinstance(wid, int) else None
    except Exception:  # noqa: BLE001
        return None


# -- Small helpers ----------------------------------------------------------------

def _load(raw: Optional[str]) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _dump(model) -> dict:
    """Get a dict on both pydantic v2 / v1 -- the main backend is pinned at pydantic 2.5, but do not let the report depend on that."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def last_probe_at(row: PublishAccountORM) -> Optional[datetime]:
    """Last probe time. Stored in the existing `identity_probe_json`, **no new column**.

    That column is defined as "verify_identity config/result snapshot" (see the model comment),
    and probe records are exactly its content; an alembic migration for a timestamp written once a day is not worth it.
    """
    return _parse_iso(_load(row.identity_probe_json).get("at"))


# -- Account selection ------------------------------------------------------------

def _skip_reason(row: PublishAccountORM, now: datetime,
                 local_worker: Optional[int], db: Session) -> Optional[str]:
    """Why this account should not be probed; None = can probe.

    The order matters: first exclude "should never touch", then "cannot touch" -- the reason in the report must
    tell operators directly what to do next.
    """
    if row.channel != CHANNEL_SERVER_POOL:
        return f"{row.channel} 通道的登录态不在我们手上"
    if row.status in ("unschedulable", "retired"):
        # 🔴 The only branch that cannot auto-recover. Not even probed: a probe is itself a login,
        # turning "possibly flagged" into "definitely banned". Recovery only via admin reinstate.
        return f"status={row.status},不自动处置(admin reinstate 才能回池)"
    if row.status == "pending_auth":
        return "pending_auth:权威身份还没解析出来,那是 register 的活"
    if not row.account_id or row.account_id.startswith("prov.cookie."):
        # Placeholder ids change as cookies rotate; using one as expected_account_id would 100% falsely report
        # identity_mismatch (see models/publish_accounts.py::provisional_account_id).
        return "只有占位身份(prov.cookie.*),需先补一次真实身份解析"
    if not row.storage_state_enc:
        return "没有登录态"
    if row.rate_limited_until and row.rate_limited_until > now:
        # Putting a jailed account back only keeps hitting the wall and pushes the penalty ladder higher. But its TTL must be extended,
        # see _extend_muted_ttl.
        return f"限流中,解封 {_iso(row.rate_limited_until)}"
    if row.lease_until and row.lease_until > now and \
            row.lease_worker_id != HEALER_LEASE_WORKER_ID:
        # Actually publishing. Opening a second context for the same account = two fingerprints/two sessions online at once.
        return f"有在途租约(worker={row.lease_worker_id},至 {_iso(row.lease_until)})"
    if row.owner_worker_id is None:
        return "还没绑 owner_worker_id(register / rebind-worker 才能绑)"
    if local_worker is None:
        # 🔴 When the local worker_id is unavailable (WORKER_UID not set / browser-service not running), **do not probe**.
        # "Assume fine if we cannot ask" means opening a company account from an uncertain machine -- different machines differ in TLS
        # fingerprint, font list, timezone and kernel features, a subtler and harder ban signal than IP drift.
        return "拿不到本机 browser-service 的 worker_id,无法做归属校验"
    if row.owner_worker_id != local_worker:
        return (f"归属 worker={row.owner_worker_id},本机是 {local_worker} —— "
                f"不代跑(指纹必须与发布时同一台机器)")
    if row.egress != EGRESS_HOST and resolve_proxy_url(db, row.proxy_id) is None:
        # 🔴 Never open a browser when the egress is gone: that would make the freshness visit itself log in from the wrong IP.
        return f"绑定的代理 proxy_id={row.proxy_id} 不可用,需人工处理"
    return None


def _probe_priority(row: PublishAccountORM, now: datetime) -> Optional[tuple[int, str]]:
    """Whether to probe, and why (lower number = higher priority); None = not its turn this round."""
    soon = now + timedelta(hours=PRE_EXPIRY_HOURS)
    if row.expires_at is None or row.expires_at <= soon:
        # Expired / about to expire -- deadlock rescue, top priority. Accounts with empty expires_at have no TTL protection,
        # so probe them once too (a successful probe fills in the rolling TTL).
        state = "已过期" if (row.expires_at and row.expires_at <= now) else "即将过期"
        return 0, f"{state}(expires_at={_iso(row.expires_at)})"
    if row.status == "quarantined":
        return 1, "quarantined —— 误判复活候选(判定以活体探测为准)"
    idle_since = row.last_used_at
    if idle_since is None or idle_since <= now - timedelta(days=KEEPALIVE_IDLE_DAYS):
        return 2, (f"闲置 ≥{KEEPALIVE_IDLE_DAYS} 天(last_used_at={_iso(idle_since)})"
                   f" —— 保鲜")
    lp = last_probe_at(row)
    if lp is None or lp <= now - timedelta(hours=PROBE_EVERY_HOURS):
        return 3, f"常规巡检(上次探测 {_iso(lp)})"
    return None


def select_candidates(db: Session, now: datetime, local_worker: Optional[int], *,
                      platform: Optional[str] = None,
                      account_id: Optional[int] = None,
                      ) -> tuple[list[tuple[PublishAccountORM, str]], list[dict]]:
    """(accounts to probe + reason, skipped accounts + reason).

    When `--account` names a single account it **bypasses the pacing check** (ops is manually rescuing one account), but **does not bypass any
    safety skip condition** -- unschedulable / in-flight lease / ownership mismatch / missing proxy still block it.
    """
    q = db.query(PublishAccountORM)
    if platform:
        q = q.filter(PublishAccountORM.platform == platform)
    if account_id:
        q = q.filter(PublishAccountORM.id == account_id)
    rows = q.order_by(PublishAccountORM.platform.asc(), PublishAccountORM.id.asc()).all()

    picked: list[tuple[int, PublishAccountORM, str]] = []
    skipped: list[dict] = []
    for r in rows:
        why_not = _skip_reason(r, now, local_worker, db)
        if why_not:
            skipped.append({"id": r.id, "platform": r.platform,
                            "status": r.status, "reason": why_not})
            continue
        pr = _probe_priority(r, now)
        if pr is None:
            if account_id:
                pr = (0, "手工指定(--account)")
            else:
                continue
        picked.append((pr[0], r, pr[1]))
    picked.sort(key=lambda t: (t[0], t[1].id))
    return [(r, why) for _, r, why in picked[:MAX_PER_RUN]], skipped


# ── Probe one account ──────────────────────────────────────────────────────

def _take_heal_lease(db: Session, row: PublishAccountORM, now: datetime) -> bool:
    """Grab a healer lease so this account cannot be handed publish tasks while being probed.

    Deliberately reuses claim's existing exclusivity: claim's candidate filter already has
    `lease_until IS NULL OR lease_until <= now` (api/publish_dispatch.py), so while the lease is
    held the account naturally gets no work -- no need for a new "probing" status column,
    and no repeat of the "status column + timestamp column encoding the same thing" conflicts.

    Lock order matches claim: advisory lock -> **re-read inside the lock** -> decide. Without the re-read
    we'd judge the lease off a possibly stale snapshot (another worker may claim between SELECT and lock).
    """
    lock_account(db, row.id)
    db.refresh(row)
    if row.lease_until and row.lease_until > now and \
            row.lease_worker_id != HEALER_LEASE_WORKER_ID:
        db.rollback()
        return False
    row.lease_worker_id = HEALER_LEASE_WORKER_ID
    row.lease_until = now + timedelta(minutes=HEAL_LEASE_MINUTES)
    row.updated_at = now
    db.commit()
    return True


def _release_heal_lease(row: PublishAccountORM) -> None:
    """Release only our own lease -- never clear a lease held by a real publish."""
    if row.lease_worker_id == HEALER_LEASE_WORKER_ID:
        row.lease_worker_id = None
        row.lease_until = None


def _apply_probe_result(row: PublishAccountORM, res: dict, now: datetime,
                        notes: list[str]) -> str:
    """Apply one probe result to the account row; return the verdict label for the report.

    Caller guarantees the account row is writable here (healer holds the lease). This function **does not commit**.
    """
    fc = _load(row.fail_counts_json)
    fail_type = res.get("fail_type")
    exit_ip = res.get("exit_ip")

    # ── (1) Unreachable: change nothing ────────────────────────────────────────
    # 🔴 Mechanical guarantee for F6 acceptance item 2. Dead proxy / offline / site 5xx all land here, and
    # these are **not the account's fault**. Changing any field punishes a good account for a network blip --
    # both false kills (Wenxin accounts 6/8 and 9 qwen accounts) started by "treating unclear as clear".
    if fail_type == "unreachable" or not res.get("reachable"):
        fc["_probe_error"] = int(fc.get("_probe_error", 0) or 0) + 1
        row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
        notes.append("unreachable —— 不是账号的账,账号状态/TTL 一个字段都没动")
        return "unreachable"

    # ── (2) Egress IP comparison (before the success check) ──────────────────────────
    # Egress drift means a remote-login record is already on the platform side, so act even if this "probe succeeded".
    drift = bool(exit_ip and row.exit_ip and exit_ip != row.exit_ip)
    if drift and row.egress != EGRESS_HOST:
        # Proxy account drift = proxy egress really changed; same metric definition as report's exit_ip_drift: suspend for manual review.
        old = row.exit_ip
        row.status = "unschedulable"
        row.last_fail_type = PublishFailureType.EXIT_IP_DRIFT.value
        row.last_fail_reason = f"保鲜巡检发现出口 IP 从 {old} 漂移到 {exit_ip}"
        row.last_fail_at = now
        row.exit_ip, row.exit_ip_at = exit_ip, now
        fc[PublishFailureType.EXIT_IP_DRIFT.value] = \
            int(fc.get(PublishFailureType.EXIT_IP_DRIFT.value, 0) or 0) + 1
        row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
        notes.append(f"🔴 出口 IP {old} → {exit_ip} 漂移 → unschedulable,需人工")
        return "exit_ip_drift"
    if drift:
        # 🔴 Direct accounts (egress=host) **are not suspended** on drift. Direct egress is the public IP of the
        # machine running the browser, which changes with office/datacenter DHCP renewals -- an inherent property of this calibration scaffold
        # (see the EGRESS_HOST comment in models/publish_accounts.py), not an account problem.
        # Suspension can only be undone by admin reinstate; trading a DHCP renewal for a stalled pool isn't worth it.
        notes.append(f"⚠️ 直连出口 IP {row.exit_ip} → {exit_ip}(egress=host,"
                     f"随本机公网 IP 变;只更新记录不停号,但请确认这台机器没换网络)")
        row.exit_ip, row.exit_ip_at = exit_ip, now

    # ── (3) Identity mismatch: terminal ───────────────────────────────────────────
    if fail_type in _TERMINAL_FAIL:
        row.status = "unschedulable"
        row.last_fail_type = PublishFailureType.IDENTITY_MISMATCH.value
        row.last_fail_reason = "保鲜巡检读到的登录身份与账号登记身份不一致"
        row.last_fail_at = now
        fc[fail_type] = int(fc.get(fail_type, 0) or 0) + 1
        row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
        notes.append(f"🔴 identity_mismatch(读到 {res.get('account_id')})→ "
                     f"unschedulable,需人工")
        return "identity_mismatch"

    # ── (4) Authoritative logout: count, quarantine only at threshold. 🔴 Never touch expires_at ────────
    if fail_type == "login_lost":
        n = int(fc.get("_probe_login_lost", 0) or 0) + 1
        fc["_probe_login_lost"] = n
        row.last_fail_type = PublishFailureType.LOGIN_LOST.value
        row.last_fail_reason = "平台可达但登录身份已失效，自愈巡检未能恢复"
        row.last_fail_at = now
        if n >= LOGIN_LOST_THRESHOLD:
            row.status = "quarantined"
            notes.append(f"权威掉线 ×{n} ≥ {LOGIN_LOST_THRESHOLD} → quarantined,"
                         f"需重新导入登录态")
        else:
            notes.append(f"权威掉线 ×{n} < {LOGIN_LOST_THRESHOLD} → 先记账不隔离")
        # 🔴 Never shorten expires_at: TTL is "last successful use + N days"; a logout is not
        # evidence of "use failed" where TTL should move. Hard-TTL expiry is exactly what killed Wenxin accounts 6/8.
        row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
        row.updated_at = now
        return "login_lost"

    if not res.get("ok"):
        # keepalive only produces the three fail_types above; reaching here means browser-service is
        # newer than the backend. Treat as unknown: record it, leave status alone (same conservative direction as unreachable).
        fc["_probe_unknown"] = int(fc.get("_probe_unknown", 0) or 0) + 1
        row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
        notes.append(f"未知 fail_type={fail_type!r} —— 只记账,不动账号")
        return "unknown"

    # ── (5) Success: renew + revive ────────────────────────────────────────
    heals = int(fc.get("_heals", 0) or 0)
    was = row.status
    # 🔴 Rolling TTL is extended unconditionally -- this line is the whole reason this module exists.
    row.expires_at = now + timedelta(days=RENEW_TTL_DAYS)
    row.last_fail_type = None
    row.last_fail_reason = None
    row.last_fail_at = None
    if exit_ip and not row.exit_ip:
        row.exit_ip, row.exit_ip_at = exit_ip, now
    account_handle = str(res.get("account_handle") or "").strip()[:64]
    if account_handle:
        row.account_handle = account_handle

    state = res.get("storage_state")
    renewed_state = False
    if isinstance(state, dict) and state.get("cookies"):
        # Write back only when cookies are non-empty -- an empty shell overwriting a good session is a defense report's SUCCESS branch already has.
        try:
            row.storage_state_enc = encrypt_required(
                json.dumps(state, ensure_ascii=False))
            row.captured_at = now
            renewed_state = True
        except PublishCryptoUnavailable as e:
            # A failed renewal shouldn't waste the whole pass: TTL is already extended and the old login session still works.
            notes.append(f"⚠️ storage_state 未回写({e})")
        except Exception as e:  # noqa: BLE001
            notes.append(f"⚠️ storage_state 回写失败:{type(e).__name__}: {e}")

    if was == "quarantined":
        if heals >= MAX_HEALS:
            # Accounts that keep reviving and re-quarantining go to manual review -- spinning hides the real issue (copied from session_pool_healer).
            notes.append(f"🔴 已自愈 {heals} 次仍反复进隔离,不再自动复活,需排查")
            row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
            row.updated_at = now
            return "ok_but_heal_capped"
        row.status = "active"
        row.warmup_until = None
        notes.append("复活:quarantined → active")
        fc = {"_heals": heals + 1}      # clear accumulated false positives, keep only heal count
    else:
        fc = {k: v for k, v in fc.items() if k == "_heals"}

    row.fail_counts_json = json.dumps(fc, ensure_ascii=False)
    row.identity_probe_json = json.dumps(
        {"at": now.isoformat(timespec="seconds"), "account_id": res.get("account_id"),
         "account_handle": account_handle or None, "exit_ip": exit_ip, "by": "publish_pool_healer",
         "state_renewed": renewed_state},
        ensure_ascii=False)
    row.updated_at = now
    notes.append(f"TTL 顺延至 {_iso(row.expires_at)}"
                 f"{';登录态已刷新' if renewed_state else ''}")
    return "ok"


def probe_one(db: Session, row: PublishAccountORM, now: datetime, *,
              apply: bool, probe_fn) -> dict:
    """Probe one account: take lease -> (outside txn) HTTP call -> persist to DB -> release lease.

    The HTTP call is deliberately outside the transaction: the advisory lock is **transaction-scoped**; holding it across a browser call
    that may take 120s would block claims on the same account on that lock.
    """
    out: dict = {"id": row.id, "platform": row.platform, "account_id": row.account_id,
                 "was_status": row.status, "was_expires_at": _iso(row.expires_at)}
    if not apply:
        # Read-only mode: no lease, no network. DB writes and network go together -- a keepalive leaves a
        # real visit record on the platform, which is itself a kind of "write" and must not happen in dry-run.
        out["result"] = "dry-run(未探测)"
        return out

    try:
        state = json.loads(decrypt_required(row.storage_state_enc or ""))
    except PublishCryptoUnavailable as e:
        out["result"] = "skipped"
        out["error"] = str(e)
        return out
    except Exception as e:  # noqa: BLE001
        # Ciphertext is broken (most commonly: PUBLISH_CRED_KEY was rotated) -> login session must be re-imported.
        # This isn't a liveness verdict, so only flag it; don't change status.
        out["result"] = "skipped"
        out["error"] = f"登录态解密失败,需重新导入:{type(e).__name__}: {e}"
        return out

    if not _take_heal_lease(db, row, now):
        out["result"] = "skipped"
        out["error"] = "抢租约时发现号已被真实发布领走"
        return out

    proxy_url = resolve_proxy_url(db, row.proxy_id)
    try:
        res = probe_fn(row.platform, row.account_id, state, proxy_url, row.ua_platform)
    except Exception as e:  # noqa: BLE001
        # probe_fn already folds network-layer errors into unreachable; reaching here means some other bug.
        # Key point: **don't leave the lease rotting in the DB** -- otherwise this account gets no tasks for HEAL_LEASE_MINUTES.
        lock_account(db, row.id)
        db.refresh(row)
        _release_heal_lease(row)
        db.commit()
        out["result"] = "skipped"
        out["error"] = f"探测调用本身抛了:{type(e).__name__}: {e}"
        log.exception("[publish-heal] account=%s probe call raised (lease released)", row.id)
        return out

    notes: list[str] = []
    lock_account(db, row.id)
    db.refresh(row)
    label = _apply_probe_result(row, res, datetime.utcnow(), notes)
    _release_heal_lease(row)
    db.commit()

    out.update({"result": label, "notes": notes,
                "now_status": row.status, "now_expires_at": _iso(row.expires_at),
                "error": res.get("error")})
    log.info("[publish-heal] account=%s platform=%s → %s (%s)",
             row.id, row.platform, label, "; ".join(notes) or "-")
    return out


# ── Pure DB: extend TTL for held accounts ──────────────────────────────────────────

def extend_muted_ttl(db: Session, now: datetime, *, apply: bool,
                     platform: Optional[str] = None) -> list[dict]:
    """Held (rate-limit cooldown) accounts: push expires_at past the unblock time. **Extend only, never shorten.**

    Copied from the same-named branch in scripts/session_pool_healer.py. It fixes a TTL deadlock in disguise:
    a rate-limited account shouldn't be put back to hit the wall, but if its expires_at is before the unblock time, by unblock day the account
    is TTL-dead -> still gets no tasks -> can never renew, effectively a suspended death sentence.
    Measured 2026-08-08: expires_at of **all** 7 held deepseek accounts was earlier than their unblock times.
    """
    q = db.query(PublishAccountORM).filter(
        PublishAccountORM.channel == CHANNEL_SERVER_POOL,
        PublishAccountORM.rate_limited_until.isnot(None),
        PublishAccountORM.rate_limited_until > now,
        PublishAccountORM.status.notin_(("retired", "unschedulable")),
    )
    if platform:
        q = q.filter(PublishAccountORM.platform == platform)
    out = []
    for r in q.all():
        want = r.rate_limited_until + timedelta(days=MUTE_GRACE_DAYS)
        if r.expires_at is not None and r.expires_at >= want:
            continue
        out.append({"id": r.id, "platform": r.platform,
                    "unmute_at": _iso(r.rate_limited_until),
                    "expires_was": _iso(r.expires_at), "expires_now": _iso(want)})
        if apply:
            # Only extend TTL, leave status -- after unblock the next round decides normally via liveness probe.
            r.expires_at = want
            r.updated_at = now
    if apply and out:
        db.commit()
    return out


# ── Pure DB: claim-leak fallback ──────────────────────────────────────────

def reap_stale_claims(db: Session, now: datetime, *, apply: bool,
                      platform: Optional[str] = None) -> list[dict]:
    """Queue rows stuck in claimed/running for over 30 minutes -> release and mark failed. **Never requeue.**

    Gap: when a worker process is killed there's no receipt. The account's `lease_until` expires and frees it on its own,
    but the queue row stays `claimed` forever -- and claim's candidate filter only looks at `status == "queued"`,
    so nobody ever touches this task again and the draft silently never publishes.

    🔴 **Why mark failed instead of returning to queued (even when it "obviously wasn't published"):**

    `dispatch.draft_id` is only persisted **on receipt** (`dispatch.draft_id = payload.draft_id` in report).
    A worker that dies sends no receipt, so draft_id in the DB is NULL -- yet the adapter **may already have clicked
    publish** (it records that in the `req.commit` dict inside the browser-service process, which vanishes
    with the process). In other words, "publish was never clicked" **cannot be proven**.

    Requeuing without proof risks publishing the same article twice, irreversibly. So handling is identical to
    `submit_unknown`: set failed + **max out attempt** (so no future claim filter can pick it up) +
    state clearly that manual reconciliation against the platform backend is needed.

    Better to have one draft wait for a human than have an account show two duplicate articles on the platform.
    """
    cutoff = now - timedelta(minutes=CLAIM_STALE_MINUTES)
    q = (db.query(PublishDispatchTaskORM)
           .filter(PublishDispatchTaskORM.status.in_(("claimed", "running")),
                   or_(PublishDispatchTaskORM.claimed_at.is_(None),
                       PublishDispatchTaskORM.claimed_at <= cutoff)))
    if apply:
        q = q.with_for_update(skip_locked=True)
    if platform:
        q = q.filter(PublishDispatchTaskORM.platform == platform)
    out = []
    for d in q.order_by(PublishDispatchTaskORM.id.asc()).all():
        if apply:
            # Keep claim's lock order "queue row -> account advisory lock" so concurrent reclaim
            # and new claims don't overwrite each other.
            lock_account(db, d.account_db_id)
        err = (f"claim 泄漏:{CLAIM_STALE_MINUTES} 分钟内没有回执(worker "
               f"{d.claimed_by_worker_id} 大概已退出)。🔴 禁止重试 —— draft_id 只在回执时"
               f"落库,无法证明这次有没有点过发布;请凭平台后台/创作中心人工对账"
               f"(draft_id={d.draft_id})")
        out.append({"dispatch_id": d.id, "task_id": d.task_id, "platform": d.platform,
                    "account_db_id": d.account_db_id, "was": d.status,
                    "claimed_at": _iso(d.claimed_at),
                    "claimed_by_worker_id": d.claimed_by_worker_id,
                    "draft_id": d.draft_id})
        if not apply:
            continue
        d.status = "failed"
        d.attempt = d.max_attempts       # mechanical guarantee: no future claim can pick it up
        d.finished_at = now
        d.last_error = err[:2000]
        d.updated_at = now
        task = db.get(TopicDocPublishTaskORM, d.task_id)
        # Business tasks that already published successfully aren't rewritten by a queue leak (same metric definition as _fail_business_task).
        if task is not None and task.status != "published":
            task.status = "failed"
            task.last_error = err[:2000]
        acct = db.get(PublishAccountORM, d.account_db_id)
        lease_released = False
        if acct is not None and acct.lease_worker_id == d.claimed_by_worker_id \
                and acct.lease_until is not None and acct.lease_until <= now:
            acct.lease_worker_id = None
            acct.lease_until = None
            acct.updated_at = now
            lease_released = True
        out[-1]["lease_released"] = lease_released
    if apply and out:
        db.commit()
        log.warning("[publish-heal] reclaimed %d leaked queue rows (expired leases released, marked failed, not requeued)",
                    len(out))
    return out


# ── Alerts ────────────────────────────────────────────────────────────

def build_alert(report: dict) -> Optional[str]:
    """Low water mark / signs of correlated bans / needs manual review -> a text blob; return None when there's nothing to say."""
    lines = ["【发布池巡检】"]
    body = False
    low = report.get("below_watermark") or {}
    risk = report.get("egress_groups_at_risk") or []
    need_human = report.get("need_human") or []
    revived = [x for x in report.get("probed", []) if x.get("result") == "ok"]
    if low:
        lines.append("可调度账号低于水位:"
                     + ", ".join(f"{p}={n}" for p, n in sorted(low.items())))
        body = True
    if risk:
        # Only multiple accounts failing on the same egress signals "this IP got flagged"; a single-account failure doesn't count.
        lines.append("🔴 相关性封号迹象(同出口成批异常):"
                     + ", ".join(f"{g['egress']} {g['bad']}/{g['total']}" for g in risk))
        body = True
    if need_human:
        lines.append("需人工:" + "; ".join(
            f"#{x['id']} {x.get('reason') or x.get('result')}" for x in need_human[:10]))
        body = True
    if revived:
        lines.append(f"自动续期/复活 {len(revived)} 个号")
    stale = report.get("stale_claims") or []
    if stale:
        lines.append(f"🔴 {len(stale)} 条队列行 claim 泄漏,已判 failed 待人工对账"
                     f"(禁止重投)")
        body = True
    return "\n".join(lines) if body else None


def push_alert(text: str) -> None:
    if not ALERT_WEBHOOK:
        log.warning("[publish-heal] no webhook configured, alert logged only:\n%s", text)
        return
    try:
        httpx.post(ALERT_WEBHOOK, json={"msgtype": "text", "text": {"content": text}},
                   timeout=10)
    except Exception as e:  # noqa: BLE001
        log.warning("[publish-heal] webhook push failed: %s", e)


# ── One round ────────────────────────────────────────────────────────────

def run_once(db: Session, *, apply: bool = False, probe: bool = True,
             platform: Optional[str] = None, account_id: Optional[int] = None,
             probe_fn=probe_account_http, worker_id_fn=local_worker_id_http,
             sleep_fn=time.sleep) -> dict:
    """Run one round: three pure-DB jobs + probing. Returns a report ready for json.dumps.

    `probe_fn` / `worker_id_fn` / `sleep_fn` are injectable -- the acceptance script uses this to assert every branch
    without a real browser or real sleep (scripts/verify_publish_healer.py).
    """
    now = datetime.utcnow()
    today = local_today()
    report: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"),
        "mode": "apply" if apply else "dry-run",
    }

    # B. Extend TTL for held accounts (prevent dying on unblock)
    report["mute_ttl_extended"] = extend_muted_ttl(db, now, apply=apply,
                                                   platform=platform)
    # C. Claim-leak fallback (mark failed only, never requeue)
    report["stale_claims"] = reap_stale_claims(db, now, apply=apply, platform=platform)

    # A. Keepalive / renewal / inspection
    probed: list[dict] = []
    skipped: list[dict] = []
    if probe:
        # The ownership check also runs in dry-run: it's a read-only GET to 127.0.0.1 that never touches the platform,
        # and without it the dry-run report's "who would be probed" is fake (ownership-mismatched accounts should be skipped).
        local_worker = worker_id_fn()
        report["local_worker_id"] = local_worker
        candidates, skipped = select_candidates(
            db, now, local_worker, platform=platform, account_id=account_id)
        for i, (row, why) in enumerate(candidates):
            if i and apply:
                # 🔴 Jitter: evenly spaced visits are themselves a bot signature. No wait on the first pass (otherwise every cron
                # run wastes time up front); afterwards wait a random interval each pass.
                sleep_fn(random.uniform(0, JITTER_SEC))
            r = probe_one(db, row, datetime.utcnow(), apply=apply, probe_fn=probe_fn)
            r["why"] = why
            probed.append(r)
    report["probed"] = probed
    report["skipped"] = skipped

    # D. Water mark + needs manual review
    status = [_dump(s) for s in pool_status(db)]
    report["pool_status"] = status
    report["below_watermark"] = {s["platform"]: s["schedulable"] for s in status
                                if s["schedulable"] < MIN_USABLE}
    report["egress_groups_at_risk"] = [g for s in status
                                       for g in (s.get("egress_groups_at_risk") or [])]
    report["need_human"] = (
        [{"id": x["id"], "result": x["result"],
          "reason": "; ".join(x.get("notes") or []) or x.get("error")}
         for x in probed
         if x["result"] in ("identity_mismatch", "exit_ip_drift", "ok_but_heal_capped")
         or (x["result"] == "login_lost"
             and x.get("now_status") == "quarantined")]
        + [{"id": s["id"], "result": "skipped", "reason": s["reason"]}
           for s in skipped
           if "代理" in s["reason"] or "归属" in s["reason"] or "worker_id" in s["reason"]]
    )
    report["today"] = today
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="发布池自愈:保鲜 / 续期 / 巡检")
    ap.add_argument("--apply", action="store_true",
                    help="真的写库并真的开浏览器探测(默认只读、不出网)")
    ap.add_argument("--platform", default=None, help="只处理该平台")
    ap.add_argument("--account", type=int, default=None,
                    help="只处理该 publish_accounts.id(绕过节奏判定,不绕安全跳过)")
    ap.add_argument("--no-probe", action="store_true",
                    help="只做纯 DB 那几件事(在押 TTL 顺延 / claim 泄漏兜底 / 告警)")
    ap.add_argument("--no-alert", action="store_true", help="不推 webhook")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from geo.database import SessionLocal

    db = SessionLocal()
    try:
        report = run_once(db, apply=args.apply, probe=not args.no_probe,
                          platform=args.platform, account_id=args.account)
    finally:
        db.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    text = build_alert(report)
    if text and not args.no_alert:
        push_alert(text)
    return 0


def _preflight() -> Optional[str]:
    """Environment self-check for the scheduled entry point; returns the error reason, None = OK to run.

    🔴 Fixes a failure that **raises no error**: the default of `Settings.DATABASE_URL` is SQLite
    (CLAUDE.md: "DB default is misleading"), and the local PG address often lives only in a manually sourced
    env file. So a healer started without `DATABASE_URL` connects to an empty SQLite DB,
    reports "0 accounts, all fine" and **exits normally** -- it looks like it ran, but no account was kept alive,
    while the real accounts quietly hit TTL expiry.

    Scheduled jobs (crontab / Windows Task Scheduler) hit this most: when running by hand you sourced it in passing,
    but nobody sources it for you in the scheduler. Same shape as the incident recorded in cron_env.py -- the cron environment is
    clean, and failing to get config isn't a crash, it's silently wrong behavior.

    So here we **prefer refusing to run**: one explicit failure (exit code 2 + log) beats a falsely green report.
    """
    from geo.database import engine
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return ("DATABASE_URL 没有值。不拒绝的后果不是报错,而是静默连上默认的 SQLite 空库、"
                "报告「0 个账号、一切正常」正常退出 —— 你会以为它跑过了。\n"
                "解决:在调度器的环境里给 DATABASE_URL,或把它写进 backend/.env。")
    if engine.dialect.name != "postgresql":
        # Probe leases use pg_advisory_xact_lock, which is a no-op on SQLite -- meaning
        # the invariant "this account gets no publish tasks while being probed" simply doesn't hold.
        return (f"DATABASE_URL 的方言是 {engine.dialect.name},不是 postgresql。"
                f"发布池的账号锁在 SQLite 上是 no-op,不要在上面跑保鲜。")
    return None


if __name__ == "__main__":
    # 🔴 Under crontab / Task Scheduler the environment is clean: without loading .env first there's no PUBLISH_CRED_KEY,
    # and renewal fails silently (cron_env.py's docstring records the incident where gen_retry wiped the retry queue because of this).
    from geo.cron_env import load_env_for_cron

    load_env_for_cron()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    _why = _preflight()
    if _why:
        log.error("[publish-heal] refusing to run -- %s", _why)
        sys.exit(2)
    sys.exit(main())
