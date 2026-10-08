"""Agent action trail — afterwards we must be able to tell "ops clicked it on the page" from "the agent clicked it on their behalf".

Three trail layers; this module handles only the second:
  ① Business changes   ai_telemetry_topics.topic_changelog_json — built into the directly-called route functions, pattern ③ for free
  ② Action audit       compliance_audit_logs(event_type="agent_action") — this module
  ③ Service logs       logging

detail **must** include via=agent + turn_id + tool, otherwise the changelog and audit can't be matched up.
Doesn't commit; follows the caller's transaction (same convention as compliance_audit.record).
"""
from __future__ import annotations

import json
import logging

from agents.geo.deps import AgentDeps

log = logging.getLogger(__name__)

_MAX_FIELD = 1200   # Trim limit for args / result before persisting, so large JSON doesn't blow up the audit table


def _crop(v) -> object:
    if v is None:
        return None
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = str(v)
    return s if len(s) <= _MAX_FIELD else s[:_MAX_FIELD] + f"…(截断,原长 {len(s)})"


def record_agent_action(deps: AgentDeps, *, tool: str, topic_id: int, risk: str,
                        args: dict | None = None, result: object = None,
                        confirmed: bool = False, object_type: str = "",
                        object_id: int | None = None) -> None:
    """Record one agent action audit. risk ∈ read|write|act|paid; read is not recorded (too much volume)."""
    if risk == "read":
        return
    try:
        from geo.services import compliance_audit

        compliance_audit.record(
            deps.db,
            topic_id=topic_id,
            event_type="agent_action",
            object_type=object_type or "",
            object_id=object_id,
            actor_type="operator",
            actor_id=deps.user_id,
            actor_name=deps.actor_name,
            detail={
                "via": "agent", "tool": tool, "risk": risk, "confirmed": confirmed,
                "turn_id": deps.turn_id, "scope": deps.scope,
                "args": _crop(args), "result": _crop(result),
            },
        )
    except Exception:  # noqa: BLE001 — a failed trail must never interrupt business
        log.exception("[agent-audit] record failed tool=%s topic=%s", tool, topic_id)
