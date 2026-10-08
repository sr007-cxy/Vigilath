"""AgentDeps — tenant context injected into each Agent run (Pydantic AI deps), plus privilege-escalation protection.

Iron rules (Best Approach §12):
  - account_id is injected by resolve_account, **never a model-fillable parameter**;
  - every resource id from the model is checked by assert_owns to belong to the current account.

Two scopes (ops console integration, 2026-08):
  - scope="self"  customer self-service line: resources must belong to account_id itself (assert_owns);
  - scope="admin" ops workspace line: operates on **customers' projects**, so ownership isn't checked; instead **role + project lock**
    are checked (assert_staff + locked_topic_id). See agents/geo/scope.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class AgentDeps:
    """Declared via Agent(deps_type=AgentDeps), injected by Agent.run(msg, deps=...), accessed in tools as ctx.deps."""

    account_id: int
    user_id: int
    db: Session
    topic_id: int | None = None          # Currently active topic (MVP limited to 1; account×topic reserved for multi-topic)
    page_context: dict[str, Any] | None = None   # Frontend "current page" context: {page, describe, snapshot, actions[]}; used by control_page to validate available actions
    budget: dict[str, Any] = field(default_factory=dict)   # Usage guardrails: tokens/steps (not billing)
    caps: list[str] = field(default_factory=lambda: ["read", "write"])   # Capability scope; full internally, narrowed externally by the embed token

    # ── Ops console (admin) line ─────────────────────────────────────────────
    role: str = "user"          # UserORM.role snapshot: admin|operator|user|tenant|maintainer
    actor_name: str = ""        # For the audit trail (email preferred, falls back to name)
    scope: str = "self"         # "self" customer self-service | "admin" ops workspace
    # The single project locked by the ops console. **Deliberately a separate field from topic_id**:
    #   topic_id       = soft hint of "which topic the user is looking at"; the self line may fall back to the latest batch-run topic;
    #   locked_topic_id= the project locked by the ops console, **no fallback** — if the project can't be resolved the model must ask the user,
    #                    rather than operate on "some recently batch-run project" (the direct source of cross-customer mis-writes).
    locked_topic_id: int | None = None
    # ── Call source (used by the unified policy layer) ──────────────────────────
    # Internal and external use the same gates + the same audit table (see geo/services/call_policy.py),
    # distinguished by channel: web|admin|cron|api|embed|im|skill.
    # Defaults to web (our own web app); external entries must set it explicitly — a wrong value only lets external calls pass as internal.
    channel: str = "web"
    tid: str = ""               # tid of the external agent token (audit trail / tier override)
    tenant_id: int | None = None    # External gateway tenant id (api_tenants.id)
    policy_key: str = ""        # Explicitly specified tier; empty = default tier by channel

    turn_id: str = ""           # One uuid4 per HTTP request — the cross-turn credential for two-step confirmation (see confirm.py)
    user_message: str = ""      # This turn's raw user message (without page preamble), for the negation veto


class OwnershipError(PermissionError):
    """Resource doesn't belong to the current account — reject (prevents cross-account reads/writes)."""


def assert_owns(deps: AgentDeps, owner_account_id: int | None, what: str = "resource") -> None:
    """Every tool receiving a model-supplied resource id must call this to check ownership right after fetching the resource.

    Serves only the self line; on the admin line ownership means "belongs to the **locked project**", via scope.own_doc / own_task.
    """
    if owner_account_id is None or owner_account_id != deps.account_id:
        raise OwnershipError(f"{what} 不属于当前账号(account_id={deps.account_id})")


def assert_staff(deps: AgentDeps, what: str = "运营台能力") -> None:
    """First gate for all admin-line tools: must really be on the admin scope, with role admin|operator."""
    if deps.scope != "admin" or deps.role not in ("admin", "operator"):
        raise OwnershipError(f"{what} 仅限运营工作台(admin/operator)")
