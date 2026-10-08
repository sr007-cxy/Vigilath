"""Call policy — unified admission tiers + unified call audit for internal and external calls.

**What needs governing is not model tiers but batch-run scheduling**: who may trigger batch runs / which execution path / run now or queue.

Background: the external multi-tenant gateway (`/v1` in `telemetry-service/app/gateway.py`) has long had a policy —
api_keys → tenant → engine whitelist + per-engine daily quota + RPM + priority + browser_jobs queue.
But **internal calls bypass it entirely**: run-now, drip batch runs, draft generation and diagnosis all call straight through,
neither bound by quotas nor leaving a single record. This module brings both sides into one layer:

  caller (channel / token) → CallPolicy (tier) → decision → CallAudit (internal calls are recorded too)

Two configurable execution dimensions (what was needed this time):
  exec_mode  use **automation** (browser account pool / browser_jobs) or **direct API**
  run_mode   **execute immediately** (synchronous run-now) or **go through scheduling queues** (queue / drip)

Tier assignment (tighten-only, later takes precedence):
  channel default tier (channel)  →  tenant override (api_tenants.policy_key)  →  token override (agent_tokens.policy_key)

Internal tier unlimited=1: all limits pass, but **audit is still written**.
No foreign keys: same as llm_usage / balance; telemetry-service is deployed independently, connects to the same DB,
and has its own mirror implementation writing these tables directly; FKs would let deletes on the main DB block its writes.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from geo.database import Base

# ── Channels (where the call comes in) ────────────────────────────────────────────────
CHANNEL_WEB = "web"          # Logged-in users of our own web app
CHANNEL_ADMIN = "admin"      # Ops workbench
CHANNEL_CRON = "cron"        # Platform automation (drip scheduling / scheduled jobs)
CHANNEL_API = "api"          # External multi-tenant gateway /v1 (api_keys)
CHANNEL_EMBED = "embed"      # External agent token /agent/v1
CHANNEL_IM = "im"            # Feishu / WeCom / DingTalk
CHANNEL_SKILL = "skill"      # Skill package (also an embed token underneath, tiered separately to tighten easily)
ALL_CHANNELS = (CHANNEL_WEB, CHANNEL_ADMIN, CHANNEL_CRON, CHANNEL_API,
                CHANNEL_EMBED, CHANNEL_IM, CHANNEL_SKILL)

# ── Execution path: automation or API ────────────────────────────────────────
EXEC_AUTO = "auto"           # Browser automation (account pool / browser_jobs / dispatch worker)
EXEC_API = "api"             # Direct API (Doubao ARK / OpenRouter / deepseek_http)
EXEC_ANY = "any"             # Unrestricted, decided by the engine's own capabilities
ALL_EXEC_MODES = (EXEC_AUTO, EXEC_API, EXEC_ANY)

# ── Execution timing: run now or queue ────────────────────────────────────────────
RUN_IMMEDIATE = "immediate"  # Execute synchronously now (run-now direct, using the account pool's current concurrency)
RUN_QUEUE = "queue"          # Enter the scheduling queue, claimed by worker / drip
RUN_ANY = "any"              # Unrestricted, decided by the call site
ALL_RUN_MODES = (RUN_IMMEDIATE, RUN_QUEUE, RUN_ANY)

# ── Decision results ────────────────────────────────────────────────────────────
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"


class CallPolicyORM(Base):
    """A permission tier. key is a stable identifier (channel default tiers use forms like `channel:web`; custom tiers can be named freely).

    All "unlimited" values are 0 (daily_runs / rpm / max_concurrent), never NULL —
    NULL is ambiguous between "missing means unlimited" and "missing means forbidden"; 0 is not.
    """

    __tablename__ = "call_policies"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), nullable=False, unique=True, index=True)
    label = Column(String(128), nullable=False, default="")
    channel = Column(String(16), nullable=False, default=CHANNEL_WEB)   # Default channel this tier applies to

    # Action allow/deny lists (comma-separated action names, e.g. run_now / run_topic / draft_articles).
    # Empty allow_actions = allow all; deny_actions always takes precedence over allow.
    allow_actions = Column(Text, nullable=False, default="")
    deny_actions = Column(Text, nullable=False, default="")

    # The two execution dimensions — the core of what is configured this time
    exec_mode = Column(String(16), nullable=False, default=EXEC_ANY)
    run_mode = Column(String(16), nullable=False, default=RUN_ANY)

    engines = Column(Text, nullable=False, default="")      # Engine whitelist, empty = allow all
    daily_runs = Column(Integer, nullable=False, default=0)  # Triggers allowed per day, 0 = unlimited
    rpm = Column(Integer, nullable=False, default=0)         # Per-minute cap, 0 = unlimited
    max_concurrent = Column(Integer, nullable=False, default=0)  # Topics running at the same time, 0 = unlimited
    priority = Column(Integer, nullable=False, default=5)     # Enqueue priority (smaller = claimed first)

    unlimited = Column(Integer, nullable=False, default=0)    # 1 = internal tier, exempt from all limits (still audited)
    enabled = Column(Integer, nullable=False, default=1)

    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_by_id = Column(Integer, nullable=True)


class CallAuditORM(Base):
    """Unified call audit — internal and external share one table, one row per scheduling trigger.

    This is where "internal calls are also recorded like the external gateway" lands: previously only external jobs had browser_jobs rows to check,
    and internal batch runs had no record at all; now both do, distinguished by channel.

    Only scheduling actions are recorded (triggering batch runs / draft generation / diagnosis), **not every model call** — that is
    llm_call_log's job; the two are linked by request_id / trace_id.
    """

    __tablename__ = "call_audit"
    __table_args__ = (
        Index("idx_call_audit_account_ts", "account_id", "ts"),
        Index("idx_call_audit_policy_ts", "policy_key", "ts"),
        Index("idx_call_audit_channel_ts", "channel", "ts"),
        Index("idx_call_audit_day", "bj_date"),
        Index("idx_call_audit_request", "request_id"),
    )

    # SQLite's rowid alias only recognizes INTEGER PRIMARY KEY; BIGINT would lose autoincrement
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)

    ts = Column(DateTime, nullable=False, default=datetime.utcnow)   # UTC
    bj_date = Column(String(10), nullable=False)                      # Beijing date, computed at write time

    channel = Column(String(16), nullable=False, default=CHANNEL_WEB)
    policy_key = Column(String(64), nullable=False, default="")
    tenant_id = Column(Integer, nullable=True)      # External gateway tenant (api_tenants.id)
    tid = Column(String(64), nullable=False, default="")   # agent token id
    account_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)

    endpoint = Column(String(128), nullable=False, default="")   # Entry point: /api/.../run-now
    action = Column(String(64), nullable=False, default="")      # Action name
    topic_id = Column(Integer, nullable=True)
    engine = Column(String(32), nullable=False, default="")
    exec_mode = Column(String(16), nullable=False, default="")   # Path actually taken after the decision
    run_mode = Column(String(16), nullable=False, default="")    # Timing actually used after the decision

    decision = Column(String(8), nullable=False, default=DECISION_ALLOW)
    reason = Column(String(255), nullable=False, default="")     # On deny, states why

    cells = Column(Integer, nullable=False, default=0)           # Question × engine scale of this trigger
    request_id = Column(String(64), nullable=False, default="")  # Aligned with llm_call_log.trace_id
