"""Sales Agent deps — user context injected into each Agent run, plus privilege-escalation protection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class SalesAgentDeps:
    """Injected via Pydantic AI deps; tools can only get the current user info from ctx.deps."""

    account_id: int
    user_id: int
    db: Session
    session_id: int | None = None
    session_public_id: str | None = None
    page_context: dict[str, Any] | None = None
    user_role: str = "user"
    budget: dict[str, Any] = field(default_factory=dict)
    # Only holds tool hand-off state for this turn/session, e.g. a just-generated profile ID; must not be treated as the historical profile default for new recommendations.
    tool_state: dict[str, Any] = field(default_factory=dict)
    caps: list[str] = field(default_factory=lambda: ["read", "write"])


class SalesAgentOwnershipError(PermissionError):
    """The resource doesn't belong to the current user."""


def assert_user_owns(deps: SalesAgentDeps, owner_user_id: int | None, what: str = "resource") -> None:
    if owner_user_id is None or owner_user_id != deps.user_id:
        raise SalesAgentOwnershipError(f"{what} 不属于当前用户(user_id={deps.user_id})")
