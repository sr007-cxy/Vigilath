"""Pre-action gate: two-level Allow / Deny verdicts (Ask is handled by manual confirmation of email drafts).

Before this, `usage_guardrail_check` was an empty function and all 11 tools passed unconditionally —
any account could have the Agent recommend companies and start real diagnoses without limit (each 8-20 minutes, consuming audit quota and LLM money).
A gate is required before opening registration, otherwise one account could blow through the costs.

Note on the counting metric definition: cost was meant to be read from `llm_usage_daily`, but that table aggregates by service/feature with **no user_id**,
and the detail table's `est_cost_usd` isn't filled yet, so reading it would always give 0. Hence counting here is by **number of actions**,
with this package's own `sales_agent_compliance_logs` as the data source — write a row when allowing, count same-action rows for the day.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from agents.sales_agent.service.common import SalesAgentError, record_compliance_event


class SalesAgentGuardDenied(SalesAgentError):
    """Gate rejection. A subclass of SalesAgentError, so REST entries reuse the existing 403 handling;
    conversation entries catch it separately and reply with natural language rather than a red error."""

_BJ_OFFSET = timedelta(hours=8)

# Action -> (default per-account daily cap, risk level). 0 means unlimited. Unlisted actions (read tools) pass directly.
ACTION_DAILY_LIMITS: dict[str, tuple[int, str]] = {
    "generate_detection_report": (20, "high"),    # Runs a real diagnosis on the main backend, the most expensive
    "recommend_companies": (30, "medium"),
    "discover_companies": (60, "medium"),         # Hits the Tencent search API, billed per call
    "generate_outreach_copy": (200, "low"),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except ValueError:
        return default


def daily_limit_for(action: str) -> int:
    """Defaults can be overridden by `SALES_AGENT_DAILY_LIMIT_<ACTION_UPPERCASE>`; 0 = unlimited."""
    default = ACTION_DAILY_LIMITS.get(action, (0, "low"))[0]
    return _env_int(f"SALES_AGENT_DAILY_LIMIT_{action.upper()}", default)


def disabled_actions() -> set[str]:
    """Ops emergency switch: `SALES_AGENT_DISABLE_ACTIONS=recommend_companies,discover_companies`."""
    raw = os.environ.get("SALES_AGENT_DISABLE_ACTIONS", "")
    return {a.strip() for a in raw.split(",") if a.strip()}


def bj_day_range_utc(now_utc: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, end) of the Beijing day, converted back to UTC — created_at stores utcnow, so the metric definitions must align."""
    now = now_utc or datetime.utcnow()
    bj_midnight = (now + _BJ_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    start = bj_midnight - _BJ_OFFSET
    return start, start + timedelta(days=1)


def _count_today(db: Any, user_id: int, action: str) -> int:
    from agents.sales_agent.models import SalesAgentComplianceLogORM

    start, end = bj_day_range_utc()
    return (
        db.query(SalesAgentComplianceLogORM)
        .filter(
            SalesAgentComplianceLogORM.user_id == user_id,
            SalesAgentComplianceLogORM.action == action,
            SalesAgentComplianceLogORM.decision == "allowed",
            SalesAgentComplianceLogORM.created_at >= start,
            SalesAgentComplianceLogORM.created_at < end,
        )
        .count()
    )


def _account_active(db: Any, user_id: int) -> bool:
    from geo.models.user import UserORM

    row = db.query(UserORM).filter(UserORM.id == user_id).first()
    return row is not None and bool(getattr(row, "is_active", True))


def check(deps: Any, action: str, seller_profile_id: int | None = None) -> None:
    """Tool-side entry: gets db / user_id from deps."""
    check_user(getattr(deps, "db", None), getattr(deps, "user_id", None), action, seller_profile_id)


def check_user(db: Any, user_id: int | None, action: str, seller_profile_id: int | None = None) -> None:
    """Raises SalesAgentGuardDenied on failure; the message is user-facing and exposes no field names or internal counts.

    Checks in order: 1) whether the account is usable  2) whether ops disabled this action  3) whether the current user's own brand profile offers GEO audit/monitoring services
    4) whether today's count exceeds the cap.
    """
    if db is None or not user_id:
        return

    def deny(reason_user: str, reason_log: str, detail: dict[str, Any]) -> None:
        record_compliance_event(db, user_id, action, ACTION_DAILY_LIMITS.get(action, (0, "low"))[1],
                                "blocked", reason_log, detail)
        raise SalesAgentGuardDenied(reason_user, status_code=403)

    if not _account_active(db, user_id):
        deny("当前账号已停用,无法继续使用销售助手。", "账号停用", {"check": "account_active"})

    if action in disabled_actions():
        deny("这个功能暂时关闭了,稍后再试。", f"动作 {action} 被运维开关关闭", {"check": "action_switch"})

    if action == "generate_detection_report":
        # "Generate report" here means the platform's GEO audit tool producing a diagnosis report for the target company's website.
        # The gate only checks whether the current user's own brand profile offers website audit/diagnosis report features;
        # recommended companies are only audit targets, and must not be blocked based on whether they "have report generation features".
        from agents.sales_agent.service.brandprofile import report_capability_denial

        reason = report_capability_denial(db, user_id, seller_profile_id)
        if reason:
            deny(reason, "当前用户品牌画像没有网站检测/诊断报告功能",
                 {"check": "report_capability", "seller_profile_id": seller_profile_id})

    limit = daily_limit_for(action)
    if limit > 0:
        used = _count_today(db, user_id, action)
        if used >= limit:
            deny("今天这个操作的次数已经用完了,明天再来。",
                 f"动作 {action} 当日次数超上限({used}/{limit})",
                 {"check": "daily_limit", "used": used, "limit": limit})
        # Allowing also records a row — it's also the data source for the next count
        record_compliance_event(db, user_id, action, ACTION_DAILY_LIMITS[action][1], "allowed",
                                f"动作 {action} 放行({used + 1}/{limit})",
                                {"check": "daily_limit", "used": used + 1, "limit": limit})
