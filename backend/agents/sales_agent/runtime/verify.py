"""Deterministic validation of tool output.

Originally only email copy in the whole repo had a hard gate (`_outreach_copy_policy_violations`); whatever recommendation results and
report summaries emitted was written back into context as-is — the 2026-08-22 company size bucketing bug
(two of five options swallowed into 「1-10 名员工」 "1-10 employees") survived to production precisely because nobody validated this kind of thing.

Follows the email approach: **deterministic assertions, no model calls**. Problems found don't pass silently;
the caller degrades to "partial result + explanation" and records a compliance log.
"""
from __future__ import annotations

from typing import Any

from agents.sales_agent.runtime.input_guard import (
    FLOW_ACTION_TOKENS,
    identity_or_reference_kind,
    is_casual_chat,
    is_detail_followup,
    is_identity_or_reference_question,
    normalize_flow_action_text,
    resolve_delivery_status_followup,
    resolve_detection_action,
    resolve_lead_action,
    resolve_orphan_flow_action,
    resolve_stats_domain_correction,
    verify_detection_keywords_answer,
    verify_slot_answer,
)


def verify_recommendation(payload: dict[str, Any]) -> list[str]:
    """Validate the self-consistency of recommendation results. Returns a list of violation descriptions; an empty list means passed."""
    if not isinstance(payload, dict):
        return ["推荐结果结构异常"]

    problems: list[str] = []
    leads = payload.get("leads")
    leads = leads if isinstance(leads, list) else []
    requested = _int(payload.get("requested_limit"))
    actual = _int(payload.get("actual_count"))

    if requested and len(leads) > requested:
        problems.append(f"返回 {len(leads)} 条超过请求上限 {requested} 条")

    ids = [l.get("company_id") for l in leads if isinstance(l, dict) and l.get("company_id")]
    if len(ids) != len(set(ids)):
        problems.append(f"同一家企业出现多次({len(ids) - len(set(ids))} 条重复)")

    if actual and actual != len(leads):
        problems.append(f"actual_count={actual} 与实际 {len(leads)} 条对不上")

    shortfall = _int(payload.get("shortfall"))
    if requested and shortfall != max(0, requested - len(leads)):
        problems.append(f"缺口 {shortfall} 与请求/实际不自洽")

    if payload.get("status") == "ok" and shortfall:
        problems.append("状态标为 ok 但仍有缺口")

    problems += _condition_mismatch(payload, len(leads))
    return problems


def _condition_mismatch(payload: dict[str, Any], total: int) -> list[str]:
    """Whether the results match the conditions the user just chose.

    Consistent counts ≠ correct results. Production on 2026-08-23: the user chose "education & training + Beijing" and got 20 Qingdao medical device
    and Wuxi semiconductor companies, while the UI said 「已按当前筛选条件推荐 20 家」 ("recommended 20 per current filters") and 「缺口 0 家」 ("shortfall 0") — all three count checks
    passed. When candidates are restricted to the seed pool, industry/region only affect ranking, not filtering, and that must be stated.
    """
    if not total:
        return []
    out: list[str] = []
    for label, want_key, hit_key in (("行业", "requested_industries", "industry_match_count"),
                                     ("地区", "requested_regions", "region_match_count")):
        wanted = payload.get(want_key)
        if not isinstance(wanted, list) or not wanted:
            continue
        hit = _int(payload.get(hit_key))
        if hit == 0:
            out.append(f"这批结果没有一家符合所选{label}({'、'.join(str(w) for w in wanted)})")
        elif hit * 2 < total:
            out.append(f"只有 {hit}/{total} 家符合所选{label}({'、'.join(str(w) for w in wanted)})")
    if out and not payload.get("filter_applied", True):
        out.append("候选来自内置企业库,所选行业/地区只参与排序、未过滤候选范围")
    return out


# --- Report summary ---

# Fields that must never appear in unfinished reports -> Chinese wording (spliced into hints shown to users, without exposing field names)
_UNFINISHED_FORBIDDEN: dict[str, str] = {
    "score": "分数",
    "total_score": "总分",
    "grade": "等级",
    "level": "等级",
    "conclusion": "结论",
    "executive_summary": "结论摘要",
    "key_findings": "关键发现",
    "quick_wins": "改进建议",
}


def verify_report_summary(payload: dict[str, Any], report_ready: bool) -> list[str]:
    """When a report is unfinished, the summary may contain no scores/grades/conclusions — those are the easiest things to fabricate."""
    if report_ready or not isinstance(payload, dict):
        return []
    leaked = dict.fromkeys(label for key, label in _UNFINISHED_FORBIDDEN.items() if payload.get(key))
    return [f"报告尚未完成,不应给出{'、'.join(leaked)}"] if leaked else []


def redact_unfinished_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Erase conclusion fields of unfinished reports, keeping only neutral info like progress. Modifies in place and returns the same dict."""
    if not isinstance(payload, dict):
        return payload
    for key in _UNFINISHED_FORBIDDEN:
        if key not in payload:
            continue
        current = payload[key]
        payload[key] = [] if isinstance(current, list) else "" if isinstance(current, str) else None
    return payload


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --- Outreach/report status answers ---


def verify_delivery_status_reply(message: str, reply: str, data: dict[str, Any], kind: str = "") -> list[str]:
    """Validate whether email/report status answers match the queried data. Returns a list of feedback; an empty list means passed."""
    if not isinstance(data, dict):
        return ["状态查询结果结构异常"]
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    text = str(message or "")
    output = str(reply or "")
    wants_report = kind == "report_visit" or "报告" in text
    wants_open = kind == "open" or any(k in text for k in ("开信", "阅读", "已读"))

    problems: list[str] = []
    report_visit_count = _int(summary.get("report_visit_count"))
    report_visited_rows = _int(summary.get("report_visited"))
    report_visited_companies = _int(summary.get("report_visited_companies"))
    if wants_report and (report_visit_count > 0 or report_visited_rows > 0 or report_visited_companies > 0):
        if "没有找到报告访问" in output or "报告访问记录也是 0" in output or "报告被访问 0" in output:
            problems.append(f"报告访问数据非 0(report_visit_count={report_visit_count}),回答却说没有找到/为 0")
        if report_visit_count > 0 and str(report_visit_count) not in output:
            problems.append(f"回答未包含邮件记录页同口径的报告访问次数 {report_visit_count}")

    opened = _int(summary.get("opened"))
    opened_companies = _int(summary.get("opened_companies"))
    open_count = _int(summary.get("open_count"))
    if wants_open and (opened > 0 or opened_companies > 0 or open_count > 0):
        if "没有找到开信" in output or "阅读记录 0" in output:
            problems.append(f"开信/阅读数据非 0(opened={opened},open_count={open_count}),回答却说没有找到/为 0")
    return problems


def verify_delivery_target_reply(target: str, reply: str, data: dict[str, Any]) -> list[str]:
    """Validate **named** answers like "was this email address / this company emailed".

    The most common error in such questions isn't miscounting but answering the wrong question — replying to a named question with account-wide aggregates
    (production: asked whether `a@b.cn` was emailed, answered 「管理范围总共尝试发送 950 封邮件」 "950 emails attempted across the managed scope"; every number right, the answer wrong).
    So besides the numbers matching this **filtered** query, the answer must also mention the asked-about target.
    """
    if not isinstance(data, dict):
        return ["定向发送查询结果结构异常"]
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    output = str(reply or "")
    name = str(target or "").strip()
    attempted = _int(summary.get("attempted")) or (_int(summary.get("sent")) + _int(summary.get("failed")))

    problems: list[str] = []
    if name and name not in output:
        problems.append(f"问的是「{name}」,回答里却没有出现这个对象,等于又答成了全账号口径")
    if attempted > 0:
        if any(k in output for k in ("没有查到", "没有找到", "没有发送记录")):
            problems.append(f"该对象有 {attempted} 条发送记录,回答却说没有")
        if str(attempted) not in output:
            problems.append(f"回答未包含该对象的发送记录数 {attempted}")
    elif not any(k in output for k in ("没有查到", "没有找到", "没有")):
        problems.append("该对象没有发送记录,回答没有把这一点说清楚")
    return problems


# Input understanding functions moved to runtime/input_guard.py; this module keeps the imports for compatibility with old call sites.
