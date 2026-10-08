"""Call policy resolution + unified audit -- internal and external share the same gate.

Usage (two lines):

    caller = CallerRef(channel=CHANNEL_WEB, account_id=uid, user_id=uid)
    d = enforce(db, caller, "run_now", endpoint="/api/ai-telemetry/topics/run-now", cells=n)
    # d.run_mode == "queue" -> enqueue instead of running synchronously; d.exec_mode == "api" -> API direct only

`enforce` raises `CallDenied` (with an HTTP code) when a limit is hit; **one call_audit row is written whether allowed or denied**.
Internal tiers with unlimited=1 are always allowed, but still recorded -- this is where "internal calls are also recorded like the external gateway" lands.

Tier resolution priority (only tightens, later overrides earlier):
    channel default tier `channel:<channel>` -> tenant `api_tenants.policy_key` -> token `agent_tokens.policy_key`

Limit metric definition is uniformly counted from call_audit (only rows with decision=allow); one algorithm for internal/external,
no longer counting browser_jobs externally and nothing internally as before.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.models.call_policy import (
    ALL_CHANNELS,
    CHANNEL_ADMIN,
    CHANNEL_API,
    CHANNEL_CRON,
    CHANNEL_EMBED,
    CHANNEL_IM,
    CHANNEL_SKILL,
    CHANNEL_WEB,
    DECISION_ALLOW,
    DECISION_DENY,
    EXEC_ANY,
    EXEC_AUTO,
    RUN_ANY,
    RUN_QUEUE,
    CallAuditORM,
    CallPolicyORM,
)

log = logging.getLogger(__name__)

_BJ = timezone(timedelta(hours=8))


def _bj_date(now: datetime | None = None) -> str:
    """Beijing date, computed at write time -- same metric definition as llm_call_log.bj_date / balance_ledger."""
    n = now or datetime.utcnow()
    return (n.replace(tzinfo=timezone.utc).astimezone(_BJ)).strftime("%Y-%m-%d")


# -- Default tiers (created lazily, persisted on first use; afterwards the DB is authoritative and editable in the admin) ----------------
# Three internal tiers with unlimited=1: exempt from all limits, but still audited.
# Four external tiers get conservative initial values, tuned in the admin after launch, no code change or restart.
_SEED: tuple[dict, ...] = (
    {"key": "channel:web", "label": "内部·网页", "channel": CHANNEL_WEB,
     "unlimited": 1, "exec_mode": EXEC_ANY, "run_mode": RUN_ANY, "priority": 1,
     "note": "自家网页登录用户,无限额"},
    {"key": "channel:admin", "label": "内部·运营台", "channel": CHANNEL_ADMIN,
     "unlimited": 1, "exec_mode": EXEC_ANY, "run_mode": RUN_ANY, "priority": 0,
     "note": "运营工作台,无限额、优先级最高"},
    {"key": "channel:cron", "label": "内部·平台自动化", "channel": CHANNEL_CRON,
     "unlimited": 1, "exec_mode": EXEC_ANY, "run_mode": RUN_ANY, "priority": 0,
     "note": "drip 调度:浏览器引擎入调度队列、API 引擎进程内直跑,两条各记各的;"
             "队列里的优先级由种子/扩展(0/1)决定,不取本档 priority"},
    {"key": "channel:api", "label": "对外·开放 API", "channel": CHANNEL_API,
     "exec_mode": EXEC_AUTO, "run_mode": RUN_QUEUE, "priority": 10,
     "daily_runs": 200, "rpm": 60, "max_concurrent": 2,
     "note": "多租户网关 /v1;单租户配额仍以 api_tenants 的每引擎日配额为准"},
    {"key": "channel:embed", "label": "对外·嵌入 token", "channel": CHANNEL_EMBED,
     "exec_mode": EXEC_ANY, "run_mode": RUN_QUEUE, "priority": 10,
     "daily_runs": 50, "rpm": 30, "max_concurrent": 1,
     "note": "/agent/v1 对外 agent token"},
    {"key": "channel:im", "label": "对外·IM", "channel": CHANNEL_IM,
     "exec_mode": EXEC_ANY, "run_mode": RUN_QUEUE, "priority": 10,
     "daily_runs": 30, "rpm": 20, "max_concurrent": 1,
     "note": "飞书 / 企微 / 钉钉"},
    {"key": "channel:skill", "label": "对外·Skill", "channel": CHANNEL_SKILL,
     "exec_mode": EXEC_ANY, "run_mode": RUN_QUEUE, "priority": 10,
     "daily_runs": 50, "rpm": 30, "max_concurrent": 1,
     "note": "skill 包,底层同 embed token,单独一档便于单独收紧"},
)


@dataclass
class CallerRef:
    """Caller identity -- filled by each entry point; **account_id is always injected by the backend, never accepted from the request body**."""

    channel: str = CHANNEL_WEB
    account_id: int | None = None
    user_id: int | None = None
    tenant_id: int | None = None        # External gateway tenant
    tid: str = ""                       # agent token id
    policy_key: str = ""                # Explicit override (configured on tenant / token)
    request_id: str = ""


@dataclass
class Policy:
    """Resolved tier snapshot (detached from session, cacheable)."""

    key: str
    label: str = ""
    channel: str = CHANNEL_WEB
    allow_actions: set[str] = field(default_factory=set)
    deny_actions: set[str] = field(default_factory=set)
    exec_mode: str = EXEC_ANY
    run_mode: str = RUN_ANY
    engines: set[str] = field(default_factory=set)
    daily_runs: int = 0
    rpm: int = 0
    max_concurrent: int = 0
    priority: int = 5
    unlimited: bool = False
    enabled: bool = True


@dataclass
class Decision:
    allowed: bool
    policy: Policy
    exec_mode: str
    run_mode: str
    priority: int
    reason: str = ""
    code: int = 200
    audit_id: int | None = None


class CallDenied(Exception):
    """Denied by policy. code maps directly to HTTP: 403 forbidden / 429 over limit / 503 tier disabled."""

    def __init__(self, code: int, reason: str, decision: "Decision | None" = None):
        self.code = code
        self.reason = reason
        self.decision = decision
        super().__init__(reason)


def _split(raw: str | None) -> set[str]:
    return {x.strip() for x in (raw or "").split(",") if x.strip()}


def _snapshot(row: CallPolicyORM) -> Policy:
    return Policy(
        key=row.key, label=row.label or "", channel=row.channel,
        allow_actions=_split(row.allow_actions), deny_actions=_split(row.deny_actions),
        exec_mode=row.exec_mode, run_mode=row.run_mode, engines=_split(row.engines),
        daily_runs=int(row.daily_runs or 0), rpm=int(row.rpm or 0),
        max_concurrent=int(row.max_concurrent or 0), priority=int(row.priority or 5),
        unlimited=bool(row.unlimited), enabled=bool(row.enabled),
    )


# -- Tier cache: 60s TTL. Revocation of long-lived tokens takes effect via this TTL (same metric definition as embed tokens) --
_CACHE: dict[str, tuple[float, Policy]] = {}
_CACHE_TTL = 60.0
_LOCK = threading.Lock()


def invalidate_cache(key: str | None = None) -> None:
    """Call after editing tiers in the admin to take effect immediately without waiting for the TTL."""
    with _LOCK:
        if key is None:
            _CACHE.clear()
        else:
            _CACHE.pop(key, None)


def ensure_seed(db: Session) -> None:
    """Lazily create default tiers -- no data migration, uniformly covers new and old environments (same idea as lazy balance granting)."""
    have = {k for (k,) in db.query(CallPolicyORM.key).all()}
    missing = [s for s in _SEED if s["key"] not in have]
    if not missing:
        return
    now = datetime.utcnow()
    for s in missing:
        db.add(CallPolicyORM(created_at=now, updated_at=now, **s))
    db.commit()


class _TablesMissing(Exception):
    """call_policies / call_audit not created yet (migration not run). See _fail_open for handling."""


def _is_missing_table(e: Exception) -> bool:
    """Distinguish "table not created yet" from real DB failures -- the former is deployment ordering and should not take down the business."""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    if not isinstance(e, (OperationalError, ProgrammingError)):
        return False
    msg = str(e).lower()
    return ("call_policies" in msg or "call_audit" in msg) and (
        "does not exist" in msg or "no such table" in msg or "undefined" in msg)


def get_policy(db: Session, key: str) -> Policy | None:
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        row = db.query(CallPolicyORM).filter(CallPolicyORM.key == key).first()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        if _is_missing_table(e):
            raise _TablesMissing from e
        raise
    if row is None:
        return None
    snap = _snapshot(row)
    with _LOCK:
        _CACHE[key] = (now + _CACHE_TTL, snap)
    return snap


def resolve_policy(db: Session, caller: CallerRef) -> Policy:
    """Explicit override tier > channel default tier. If neither is available, use the **most conservative fallback** (queue everything + deny writes)."""
    if caller.policy_key:
        p = get_policy(db, caller.policy_key)
        if p is not None and p.enabled:
            return p
    channel = caller.channel if caller.channel in ALL_CHANNELS else CHANNEL_WEB
    key = f"channel:{channel}"
    p = get_policy(db, key)
    if p is None:
        try:
            ensure_seed(db)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            if _is_missing_table(e):
                raise _TablesMissing from e
            raise
        invalidate_cache(key)
        p = get_policy(db, key)
    if p is None or not p.enabled:
        # Fallback: tier missing or disabled -- internal channels are allowed (missing config must not lock our own site),
        # external channels are always denied (external must be closed when config is missing).
        internal = channel in (CHANNEL_WEB, CHANNEL_ADMIN, CHANNEL_CRON)
        return Policy(key=key, label="(兜底)", channel=channel, unlimited=internal,
                      run_mode=RUN_QUEUE, enabled=internal)
    return p


# -- Limits: uniformly counted from call_audit (only allowed rows) ----------------------------
def _count_since(db: Session, caller: CallerRef, policy: Policy, since: datetime) -> int:
    q = db.query(func.count(CallAuditORM.id)).filter(
        CallAuditORM.ts >= since,
        CallAuditORM.decision == DECISION_ALLOW,
        CallAuditORM.policy_key == policy.key,
    )
    # Count per account when there is one (multiple customers on the same tier counted separately); otherwise per tier
    if caller.account_id is not None:
        q = q.filter(CallAuditORM.account_id == caller.account_id)
    elif caller.tenant_id is not None:
        q = q.filter(CallAuditORM.tenant_id == caller.tenant_id)
    return int(q.scalar() or 0)


def _day_start_utc(now: datetime) -> datetime:
    """Start of the Beijing calendar day (in UTC) -- same metric definition as bj_date; do not use UTC midnight."""
    bj = now.replace(tzinfo=timezone.utc).astimezone(_BJ)
    return bj.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def audit(
    db: Session,
    caller: CallerRef,
    policy: Policy,
    action: str,
    decision: str,
    *,
    endpoint: str = "",
    topic_id: int | None = None,
    engine: str = "",
    exec_mode: str = "",
    run_mode: str = "",
    reason: str = "",
    cells: int = 0,
) -> int | None:
    """Write one audit row. **Never block business because the audit write failed** -- unlike charging, this is observability."""
    try:
        now = datetime.utcnow()
        row = CallAuditORM(
            ts=now, bj_date=_bj_date(now),
            channel=caller.channel, policy_key=policy.key,
            tenant_id=caller.tenant_id, tid=caller.tid or "",
            account_id=caller.account_id, user_id=caller.user_id,
            endpoint=endpoint[:128], action=action[:64], topic_id=topic_id,
            engine=engine[:32], exec_mode=exec_mode, run_mode=run_mode,
            decision=decision, reason=reason[:255], cells=int(cells or 0),
            request_id=(caller.request_id or "")[:64],
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception:  # noqa: BLE001
        db.rollback()
        return None


def check(
    db: Session,
    caller: CallerRef,
    action: str,
    *,
    endpoint: str = "",
    topic_id: int | None = None,
    engine: str = "",
    cells: int = 0,
    want_run_mode: str = "",
    want_exec_mode: str = "",
) -> Decision:
    """Decide + audit, returning a Decision (does not raise). Use enforce for "raise when blocked".

    want_run_mode: the timing the call site wants (e.g. run-now wants immediate); if the tier constrains it, the tier wins.
    want_exec_mode: the execution path the call site wants (e.g. one-click diagnosis wants api -- no queue, no browser).
        Same rule as want_run_mode: only adopted when the tier is any; if the tier hardcodes auto/api, the tier wins;
        operators can switch a tier to auto in the admin to force a channel back to the browser, no code change needed.
    """
    try:
        policy = resolve_policy(db, caller)
    except _TablesMissing:
        # Migration not run yet -- a deployment ordering issue, not a policy decision. **Always allow and keep existing behavior**,
        # just log once: a newly added gate must never take down the whole batch run just because its own table is missing.
        log.warning("call_policies/call_audit not created (migration d7c3f9b18e40 not run), allowing this call: %s/%s",
                    caller.channel, action)
        p = Policy(key="(未迁移)", channel=caller.channel, unlimited=True)
        return Decision(True, p, want_exec_mode or p.exec_mode,
                        want_run_mode or p.run_mode, p.priority)

    exec_mode = (want_exec_mode
                 if (policy.exec_mode == EXEC_ANY and want_exec_mode)
                 else policy.exec_mode)
    run_mode = want_run_mode if (policy.run_mode == RUN_ANY and want_run_mode) else policy.run_mode

    def _deny(code: int, reason: str) -> Decision:
        d = Decision(False, policy, exec_mode, run_mode, policy.priority, reason, code)
        d.audit_id = audit(db, caller, policy, action, DECISION_DENY, endpoint=endpoint,
                           topic_id=topic_id, engine=engine, exec_mode=exec_mode,
                           run_mode=run_mode, reason=reason, cells=cells)
        return d

    if not policy.enabled:
        return _deny(503, f"策略档 {policy.key} 已停用")
    if action in policy.deny_actions:
        return _deny(403, f"该档不允许的动作:{action}")
    if policy.allow_actions and action not in policy.allow_actions:
        return _deny(403, f"该档未开放的动作:{action}")
    if engine and policy.engines and engine not in policy.engines:
        return _deny(403, f"该档未开放的引擎:{engine}")

    if not policy.unlimited:
        now = datetime.utcnow()
        if policy.rpm > 0:
            used = _count_since(db, caller, policy, now - timedelta(seconds=60))
            if used >= policy.rpm:
                return _deny(429, f"超过每分钟上限({used}/{policy.rpm}),稍后再试")
        if policy.daily_runs > 0:
            used = _count_since(db, caller, policy, _day_start_utc(now))
            if used >= policy.daily_runs:
                return _deny(429, f"今日触发次数已达上限({used}/{policy.daily_runs})")

    d = Decision(True, policy, exec_mode, run_mode, policy.priority)
    d.audit_id = audit(db, caller, policy, action, DECISION_ALLOW, endpoint=endpoint,
                       topic_id=topic_id, engine=engine, exec_mode=exec_mode,
                       run_mode=run_mode, cells=cells)
    return d


def enforce(db: Session, caller: CallerRef, action: str, **kw) -> Decision:
    """Same as check, but raises CallDenied when denied (code maps to HTTP)."""
    d = check(db, caller, action, **kw)
    if not d.allowed:
        raise CallDenied(d.code, d.reason, d)
    return d
