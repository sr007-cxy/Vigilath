"""Sales Agent service layer: cross-channel delivery statistics.

There is only one reason for this module: when asked in chat "which companies did we send to today", the answer must include both email and SMS
and label the channel. Before this, `agent_delivery_status` only queried the email table, so sent SMS could not be found,
and the model would confidently say "no record of sending to X".

It sits above `email_send` / `sms_send` as a layering requirement: it references both channels,
and neither channel module may reference it back.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agents.sales_agent.service.email_send import _bj, agent_email_delivery_status, sales_agent_email_stats
from agents.sales_agent.service.sms_send import agent_sms_delivery_status, sales_agent_sms_stats

DELIVERY_CHANNELS = ("email", "sms")

# Keys present in both channels in the merged summary. SMS has no "opens" (no pixel), email has no "billable segments";
# those are channel-private metrics and only appear in `by_channel`.
#
# `*_companies` are per-channel distinct counts; adding them directly counts a company twice if it got both email and SMS.
# No dedup here: the two channels' SQL distinct cannot be merged across tables; accuracy would require fetching all company_ids.
# So in copy it means "companies involved in outreach touches"; to say "how many companies were sent to", count company_name in deliveries.
_SHARED_SUMMARY_KEYS = ("sent", "failed", "attempted", "sent_companies", "attempted_companies")


def _normalize_channel(channel: str) -> str:
    text = str(channel or "").strip().lower()
    return text if text in DELIVERY_CHANNELS else ""


def agent_delivery_status(
    db: Session,
    user_id: int,
    company_name: str = "",
    lead_id: int | None = None,
    status: str = "",
    days: int = 0,
    limit: int = 20,
    since: datetime | None = None,
    until: datetime | None = None,
    admin: bool = False,
    target_user_id: int | None = None,
    activity: str = "",
    channel: str = "",
) -> dict[str, Any]:
    """Delivery status for the Agent to read, **both channels returned by default**. Read-only: does not send or change state.

    `channel="email"` / `"sms"` shows just one channel; empty means all.
    Every record carries `channel`, and `summary.by_channel` gives the per-channel breakdown; answers must state
    the channel split (e.g. "sent to 12 companies: 9 by email, 3 by SMS"), otherwise users think the SMS did not go out.
    """
    wanted = _normalize_channel(channel)
    kwargs = dict(
        company_name=company_name, lead_id=lead_id, status=status, days=days,
        limit=limit, since=since, until=until, admin=admin,
        target_user_id=target_user_id, activity=activity,
    )
    email_data = agent_email_delivery_status(db, user_id, **kwargs) if wanted in ("", "email") else None
    sms_data = agent_sms_delivery_status(db, user_id, **kwargs) if wanted in ("", "sms") else None

    items: list[dict[str, Any]] = []
    if email_data is not None:
        # The email result historically has no channel field, and existing tests assert on it as-is; add it here
        # rather than changing the email module's return structure.
        for row in email_data["deliveries"]:
            items.append({"channel": "email", **row})
    if sms_data is not None:
        items.extend(sms_data["deliveries"])
    # Each channel is already sorted by id descending; after merging, re-sort by time so "most recently sent first" holds.
    items.sort(key=lambda r: (str(r.get("sent_at") or ""), str(r.get("delivery_id") or "")), reverse=True)
    items = items[: max(1, min(int(limit or 20), 50))]

    by_channel: dict[str, Any] = {}
    if email_data is not None:
        by_channel["email"] = email_data["summary"]
    if sms_data is not None:
        by_channel["sms"] = sms_data["summary"]
    summary: dict[str, Any] = {key: sum(int(part.get(key) or 0) for part in by_channel.values()) for key in _SHARED_SUMMARY_KEYS}
    # Opens/report visits exist only for email; clicks only for SMS. The merge layer passes them through as-is, with no "opens + clicks = engagement"
    # metric definition merging: the two mean different things and their sum is uninterpretable.
    if email_data is not None:
        for key in ("opened", "report_visited", "open_count", "report_visit_count", "opened_companies", "report_visited_companies"):
            summary[key] = email_data["summary"].get(key, 0)
    if sms_data is not None:
        for key in ("delivered", "clicked", "click_count", "segments", "clicked_companies"):
            summary[key] = sms_data["summary"].get(key, 0)
    summary["by_channel"] = by_channel

    total = sum(int((part or {}).get("total_matched") or 0) for part in (email_data, sms_data) if part is not None)
    channels_used = [name for name, part in (("email", email_data), ("sms", sms_data)) if part is not None]
    return {
        "total_matched": total,
        "returned": len(items),
        "summary": summary,
        "deliveries": items,
        "channels": channels_used,
        "filters": {
            "company_name": (company_name or "").strip(),
            "lead_id": lead_id or 0,
            "status": status,
            "days": int(days or 0),
            "channel": wanted,
            "since": _bj(since) if since else "",
            "until": _bj(until) if until else "",
            "admin": bool(admin),
            "target_user_id": int(target_user_id or 0),
        },
        "note": (
            "平台在用户逐封确认后用其配置的发件箱/短信通道真实发出;这里是真实投递记录,不是草稿。"
            "邮件与短信都在其中,每条记录的 channel 字段标明渠道,回答时必须写清渠道分布。"
        ),
    }


def sales_agent_delivery_stats(
    db: Session,
    user_id: int,
    admin: bool = False,
    target_user_id: int | None = None,
    channel: str = "",
) -> dict[str, Any]:
    """Aggregate stats for the admin panel. `by_channel` is the breakdown; the top level is the total of both channels."""
    wanted = _normalize_channel(channel)
    by_channel: dict[str, Any] = {}
    if wanted in ("", "email"):
        by_channel["email"] = sales_agent_email_stats(db, user_id, admin=admin, target_user_id=target_user_id)
    if wanted in ("", "sms"):
        by_channel["sms"] = sales_agent_sms_stats(db, user_id, admin=admin, target_user_id=target_user_id)
    shared = ("total", "sent", "failed", "disabled", "no_recipient", "sending")
    merged = {key: sum(int(part.get(key) or 0) for part in by_channel.values()) for key in shared}
    merged["by_channel"] = by_channel
    return merged
