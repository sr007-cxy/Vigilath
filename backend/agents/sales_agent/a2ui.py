"""A2UI payload builders for Sales Agent cards."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS

A2UI_MIME = "application/a2ui+json"
A2UI_VERSION = "v0.9"
BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
REPORT_TOOLS = {"generate_detection_report", "summarize_report"}
GUIDANCE_TOOLS = {"build_customer_profile", "recommend_companies"}
RECOMMENDATION_TOOLS = {"recommend_companies", "rank_companies"}
SEND_SCHEDULE_TOOLS = {"schedule_outreach_send", "confirm_scheduled_send", "reschedule_send"}
# The "which batch to send?" card. It isn't returned by a tool; it's assembled deterministically in api.py (see ⑤),
# so it's not in SEND_SCHEDULE_TOOLS — that one is "already scheduled", this one is "not scheduled yet, choose first".
SEND_SCHEDULE_PICKER_TOOL = "send_schedule_batch_picker"


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_of(value: Any, fallback: str = "") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, list):
        text = "、".join(filter(None, (_text_of(x, "") for x in value)))
        return text or fallback
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _short_text(value: Any, max_len: int = 180) -> str:
    text = _text_of(value, "")
    return f"{text[:max_len]}…" if len(text) > max_len else text


def _report_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for text in (_short_text(x, 180) for x in value) if text]
    text = _text_of(value, "").strip()
    return [text] if text else []


def _single_question(value: Any) -> str:
    candidates = value if isinstance(value, list) else [value]
    for raw in candidates:
        text = _text_of(raw, "").strip()
        if not text:
            continue
        lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            continue
        question = next((line for line in lines if "?" in line or "？" in line), lines[0]).strip()
        for prefix in ("1. ", "1.", "1、", "1) ", "① ", "- ", "• "):
            if question.startswith(prefix):
                question = question[len(prefix):].strip()
                break
        return question
    return ""


def _pick_report_value(data: dict[str, Any], summary: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        direct = data.get(key)
        if direct is not None and direct != "":
            return direct
        nested = summary.get(key)
        if nested is not None and nested != "":
            return nested
    return None


def _report_status_label(status: str) -> str:
    return {"generating": "检测中", "ready": "已完成", "failed": "失败", "none": "尚未检测"}.get(status, status or "—")


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _text(id_: str, text_value: str | dict[str, str], variant: str = "body", weight: int | None = None) -> dict[str, Any]:
    component: dict[str, Any] = {"id": id_, "component": "Text", "text": text_value, "variant": variant}
    if weight is not None:
        component["weight"] = weight
    return component


def _column(id_: str, children: list[str], weight: int | None = None) -> dict[str, Any]:
    component: dict[str, Any] = {"id": id_, "component": "Column", "children": children, "align": "stretch"}
    if weight is not None:
        component["weight"] = weight
    return component


def _row(id_: str, children: list[str]) -> dict[str, Any]:
    return {"id": id_, "component": "Row", "children": children, "align": "stretch"}


def _card(id_: str, child: str, weight: int | None = None) -> dict[str, Any]:
    component: dict[str, Any] = {"id": id_, "component": "Card", "child": child}
    if weight is not None:
        component["weight"] = weight
    return component


def _normalize_report(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    summary = _as_record(data.get("summary"))
    brand_name = _text_of(_pick_report_value(data, summary, ["brand_name", "brand", "title"]), "")
    url = _text_of(_pick_report_value(data, summary, ["url", "website"]), "")
    status = _text_of(_pick_report_value(data, summary, ["status"]), "")
    total_score = _text_of(_pick_report_value(data, summary, ["total_score", "score"]), "")
    grade = _text_of(_pick_report_value(data, summary, ["grade"]), "")
    executive_summary = _text_of(_pick_report_value(data, summary, ["executive_summary", "summary_text", "hint", "message"]), "")
    report_page = _text_of(_pick_report_value(data, summary, ["report_page", "report_url"]), "")
    progress = _as_record(_pick_report_value(data, summary, ["progress"]))
    progress_text = f"{_text_of(progress.get('done'), '0')} / {_text_of(progress.get('total'), '0')}" if progress.get("total") is not None else ""
    score_text = f"{total_score}{f' / {grade}' if grade else ''}" if total_score else ""
    dimensions = []
    for i, dim in enumerate(_as_list(_pick_report_value(data, summary, ["dimensions"]))[:8]):
        row = _as_record(dim)
        dimensions.append({
            "name": _text_of(row.get("维度") or row.get("name") or row.get("key") or row.get("dimension"), f"维度 {i + 1}"),
            "score": _text_of(row.get("得分") or row.get("score") or row.get("value"), "—"),
            "note": _text_of(row.get("口径") or row.get("source") or row.get("reason") or row.get("note"), ""),
        })
    stats = [
        {"label": "状态", "value": _report_status_label(status)},
        {"label": "综合评分", "value": score_text},
        {"label": "进度", "value": progress_text},
        {"label": "等级", "value": grade},
    ]
    return {
        "title": brand_name or url or ("诊断报告入口" if tool_name == "generate_detection_report" else "历史报告摘要"),
        "url": url,
        "status": _report_status_label(status),
        "scoreText": score_text,
        "progressText": progress_text,
        "grade": grade,
        "executiveSummary": executive_summary,
        "reportPage": report_page,
        "stats": [stat for stat in stats if stat["value"]],
        "dimensions": dimensions,
        "keyFindings": _report_text_list(_pick_report_value(data, summary, ["key_findings", "findings"])),
        "quickWins": _report_text_list(_pick_report_value(data, summary, ["quick_wins", "actions", "suggestions"])),
    }


def _guidance_options(value: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for item in _as_list(value):
        row = _as_record(item)
        option = {
            "label": _text_of(row.get("label") or row.get("value"), ""),
            "value": _text_of(row.get("value") or row.get("label"), ""),
        }
        desc = _text_of(row.get("desc") or row.get("description"), "")
        if desc:
            option["desc"] = desc
        source = _text_of(row.get("source"), "")
        if source:
            option["source"] = source
        if option["label"] and option["value"]:
            options.append(option)
    return options


def _normalize_guidance(data: dict[str, Any]) -> dict[str, Any]:
    question = _single_question(data.get("next_question")) or _single_question(data.get("questions")) or _single_question(data.get("message"))
    missing_items = [_text_of(item, "") for item in _as_list(data.get("missing_items") or data.get("missing_config")) if _text_of(item, "")]
    question_card = _as_record(data.get("question_card"))
    card_id = _text_of(question_card.get("id"), "")
    title = _text_of(question_card.get("title"), "请先补充信息")
    if card_id.startswith("sales_agent_entry_action"):
        title = "找客户"
    return {
        "kind": "guidance",
        "title": title,
        "message": _text_of(data.get("message"), "这次新对话里我还不了解你的业务情况。为了推荐得更准,请先补充:"),
        "question": question or _text_of(question_card.get("title"), ""),
        "missingItems": missing_items,
        "status": "need_more_info",
        "hint": _text_of(question_card.get("helper_text"), "请直接在下方聊天输入框回答这个问题。"),
        "options": _guidance_options(question_card.get("options")),
        "selectedValues": [_text_of(item, "") for item in _as_list(question_card.get("selected_values")) if _text_of(item, "")],
        "secondaryTitle": _text_of(question_card.get("secondary_title"), ""),
        "secondaryHelperText": _text_of(question_card.get("secondary_helper_text"), ""),
        "secondaryField": _text_of(question_card.get("secondary_field"), ""),
        "secondaryOptions": _guidance_options(question_card.get("secondary_options")),
        "secondarySelectedValues": [_text_of(item, "") for item in _as_list(question_card.get("secondary_selected_values")) if _text_of(item, "")],
        "secondaryDefaultValue": _text_of(question_card.get("secondary_default_value"), ""),
    }


def _normalize_recommendations(data: dict[str, Any]) -> dict[str, Any]:
    result_card = _as_record(data.get("result_card"))
    raw_items = _as_list(result_card.get("items"))
    items: list[dict[str, Any]] = []
    if raw_items:
        for i, item in enumerate(raw_items[:DEFAULT_RECOMMENDATIONS]):
            row = _as_record(item)
            scoring = _as_record(row.get("scoring"))
            match_score = _text_of(row.get("match_score") or scoring.get("match_score") or row.get("score"), "")
            items.append({
                "title": _text_of(row.get("title"), f"企业 {i + 1}"),
                "rank": _text_of(row.get("rank"), str(i + 1)),
                "score": match_score,
                "match_score": match_score,
                "opportunity_score": row.get("opportunity_score") if row.get("opportunity_score") is not None else scoring.get("opportunity_score"),
                "opportunity_score_label": _text_of(row.get("opportunity_score_label") or scoring.get("opportunity_score_label"), "待评估"),
                "confidence": _text_of(row.get("confidence") or scoring.get("confidence"), "low"),
                "confidence_level": _text_of(row.get("confidence_level") or scoring.get("confidence_level"), "低"),
                "recommended_action": _text_of(row.get("recommended_action") or scoring.get("recommended_action"), ""),
                "score_summary": _text_of(row.get("score_summary") or scoring.get("score_summary"), ""),
                "lead_id": row.get("lead_id") or row.get("id"),
                "report_status": _text_of(row.get("report_status"), ""),
                "report_id": _text_of(row.get("report_id") or row.get("source_report_id"), ""),
                "public_report_url": _text_of(row.get("public_report_url"), ""),
                "share_token": _text_of(row.get("share_token"), ""),
                "report_share": _as_record(row.get("report_share")),
                "meta": _text_of(row.get("meta"), ""),
                "reason": _short_text(row.get("reason"), 160),
                "website": _text_of(row.get("website"), "-"),
                "contact": _text_of(row.get("contact"), "未发现公开公司级联系方式"),
            })
    else:
        table = _as_list(data.get("recommendation_table"))
        leads = _as_list(data.get("leads"))
        for i, row_value in enumerate(table[:DEFAULT_RECOMMENDATIONS]):
            row = _as_record(row_value)
            lead = _as_record(leads[i] if i < len(leads) else {})
            company = _as_record(lead.get("company"))
            scoring = _as_record(lead.get("scoring"))
            match_score = _text_of(lead.get("match_score") or scoring.get("match_score") or lead.get("score"), "")
            industry = _text_of(company.get("industry"), "")
            region = _text_of(company.get("region"), "")
            size = _text_of(company.get("employee_size"), "")
            meta = [
                f"目标行业:{industry}" if industry else "",
                f"目标地区:{region}" if region else "",
                f"企业规模:{size}" if size else "",
            ]
            items.append({
                "title": _text_of(row.get("企业") or row.get("company") or row.get("name"), f"企业 {i + 1}"),
                "rank": _text_of(lead.get("rank"), str(i + 1)),
                "score": match_score,
                "match_score": match_score,
                "opportunity_score": lead.get("opportunity_score") if lead.get("opportunity_score") is not None else scoring.get("opportunity_score"),
                "opportunity_score_label": _text_of(lead.get("opportunity_score_label") or scoring.get("opportunity_score_label"), "待评估"),
                "confidence": _text_of(lead.get("confidence") or scoring.get("confidence"), "low"),
                "confidence_level": _text_of(lead.get("confidence_level") or scoring.get("confidence_level"), "低"),
                "recommended_action": _text_of(lead.get("recommended_action") or scoring.get("recommended_action"), ""),
                "score_summary": _text_of(lead.get("score_summary") or scoring.get("score_summary"), ""),
                "lead_id": lead.get("id"),
                "report_status": _text_of(lead.get("report_status"), ""),
                "report_id": _text_of(lead.get("report_id") or lead.get("source_report_id"), ""),
                "public_report_url": _text_of(lead.get("public_report_url"), ""),
                "share_token": _text_of(lead.get("share_token"), ""),
                "report_share": _as_record(lead.get("report_share")),
                "meta": " · ".join([x for x in meta if x]),
                "reason": _short_text(lead.get("reason"), 160),
                "website": _text_of(row.get("官网") or row.get("website"), "-"),
                "contact": _text_of(row.get("公开联系方式") or row.get("contact") or row.get("public_contact"), "未发现公开公司级联系方式"),
            })
    requested = _text_of(data.get("requested_limit"), "")
    actual = _text_of(data.get("actual_count") or data.get("count"), str(len(items)))
    return {
        "kind": "company_recommendations",
        "title": _text_of(result_card.get("title"), "企业推荐预览"),
        "subtitle": _text_of(result_card.get("subtitle"), f"已推荐 {actual}{f' / 目标 {requested}' if requested else ''}"),
        "status": _text_of(result_card.get("status") or data.get("status"), "ok"),
        "items": items,
    }


def _normalize_send_schedule(data: dict[str, Any]) -> dict[str, Any]:
    """Scheduling result -> card model (the panel metric definition of design 4.5, a summary version for chat).

    Three hard rules must be enforced here, not left for each frontend to rewrite:
      ① **Whether drafts are written never enters the user's view** — `has_draft` only affects our own scheduling; the card always shows
         "pending send". Showing "draft not generated" only worries the user, with no action they can take;
      ② **Reasons for undeliverable items are written per company**; `skipped` is spread out as-is, never collapsed into "some companies can't be sent";
      ③ **While the status is still draft, the footer must say not a single email has gone out**. The model often describes "scheduled"
         as "sent"; the card is the last line of interception.
    """
    schedule = _as_record(data.get("schedule"))
    status = _text_of(schedule.get("status"), "draft")
    count = len(_as_list(data.get("included")))
    total = int(schedule.get("total") or count or 0)
    included = [
        {
            "company": _text_of(_as_record(x).get("company"), "(未命名企业)"),
            "email": _text_of(_as_record(x).get("email"), ""),
            # Per-item status only exists in the detail drawer; on the summary card it's not due yet, so always "pending send".
            "statusText": "待发送",
        }
        for x in _as_list(data.get("included"))[:12]
    ]
    skipped = [
        {
            "company": _text_of(_as_record(x).get("company"), "(未命名企业)"),
            "reason": _short_text(_as_record(x).get("reason"), 60),
        }
        for x in _as_list(data.get("skipped"))[:12]
    ]
    more_included = f"另外 {count - len(included)} 家没展开,在计划详情里看" if count > len(included) else ""
    more_skipped = (
        f"另外 {len(_as_list(data.get('skipped'))) - len(skipped)} 家发不了,在计划详情里看"
        if len(_as_list(data.get("skipped"))) > len(skipped)
        else ""
    )
    armed = status in ("armed", "running", "done")
    return {
        "scheduleId": int(schedule.get("id") or 0),
        "title": "已排期" if armed else "定时发送 · 待确认",
        "status": status,
        "statusText": _text_of(schedule.get("status_text"), ""),
        # Don't give a half time like "today 15:00" — users need a complete moment they can verify.
        "whenText": _text_of(schedule.get("scheduled_at_text"), ""),
        "countText": f"{total} 家" if total else "",
        "hint": _text_of(data.get("hint"), ""),
        "warning": _text_of(data.get("time_warning"), ""),
        "includedTitle": f"会发这 {count} 家" if count else "",
        "included": included,
        "moreIncluded": more_included,
        "skippedTitle": f"发不了这 {len(_as_list(data.get('skipped')))} 家" if skipped else "",
        "skipped": skipped,
        "moreSkipped": more_skipped,
        "footer": (
            # The per-company exclusion checkboxes were removed from the panel, so we can't promise "exclude some companies" here anymore.
            # The backend's drop_item_ids contract stays (the compliance gate still uses it right before sending); it's just no longer told to users.
            "到点会逐封自动发,中途可以在计划面板里改时间,或者整个取消。"
            if armed
            else "现在**一封都还没发出去**。确认之后才会到点逐封发 —— 确认的是「给这些企业发信」这个范围。"
        ),
        "confirmLabel": "" if armed else "确认排期",
    }


def _append_section(components: list[dict[str, Any]], root_children: list[str], section_id: str, title: str, body_ids: list[str]) -> None:
    if not body_ids:
        return
    title_id = f"{section_id}-title"
    column_id = f"{section_id}-column"
    components.append(_text(title_id, title, "h4"))
    components.append(_column(column_id, [title_id, *body_ids]))
    components.append(_card(section_id, column_id))
    root_children.append(section_id)


def _report_messages(surface_id: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    root_children = ["renderer-note"]

    components.append(_text("renderer-note", "后端 A2UI 渲染 · Google Agent-to-User Interface", "caption"))

    stat_cards = []
    for i, stat in enumerate(_as_list(report.get("stats"))):
        row = _as_record(stat)
        label_id = f"stat-{i}-label"
        value_id = f"stat-{i}-value"
        body_id = f"stat-{i}-body"
        card_id = f"stat-{i}-card"
        components.append(_text(label_id, _text_of(row.get("label"), ""), "caption"))
        components.append(_text(value_id, _text_of(row.get("value"), ""), "h3"))
        components.append(_column(body_id, [label_id, value_id]))
        components.append(_card(card_id, body_id, 1))
        stat_cards.append(card_id)
    for i in range(0, len(stat_cards), 2):
        row_id = f"stats-row-{i // 2}"
        components.append(_row(row_id, stat_cards[i:i + 2]))
        root_children.append(row_id)

    if report.get("executiveSummary"):
        body_id = "summary-body"
        components.append(_text(body_id, {"path": "/executiveSummary"}, "body"))
        _append_section(components, root_children, "summary-section", "报告摘要", [body_id])

    dimensions = _as_list(report.get("dimensions"))
    if dimensions:
        dimension_rows = []
        for i, dim in enumerate(dimensions[:8]):
            row = _as_record(dim)
            name_id = f"dimension-{i}-name"
            score_id = f"dimension-{i}-score"
            note_id = f"dimension-{i}-note"
            row_id = f"dimension-{i}-row"
            body_id = f"dimension-{i}-body"
            children = [row_id]
            components.append(_text(name_id, {"path": f"/dimensions/{i}/name"}, "body", 1))
            components.append(_text(score_id, {"path": f"/dimensions/{i}/score"}, "body"))
            components.append(_row(row_id, [name_id, score_id]))
            if row.get("note"):
                components.append(_text(note_id, {"path": f"/dimensions/{i}/note"}, "caption"))
                children.append(note_id)
            components.append(_column(body_id, children))
            dimension_rows.append(body_id)
        _append_section(components, root_children, "dimensions-section", "维度得分", dimension_rows)

    finding_ids = []
    for i, item in enumerate(_as_list(report.get("keyFindings"))[:8]):
        id_ = f"finding-{i}"
        components.append(_text(id_, f"{i + 1}. {_text_of(item, '')}", "body"))
        finding_ids.append(id_)
    _append_section(components, root_children, "findings-section", "关键发现", finding_ids)

    win_ids = []
    for i, item in enumerate(_as_list(report.get("quickWins"))[:8]):
        id_ = f"quick-win-{i}"
        components.append(_text(id_, f"{i + 1}. {_text_of(item, '')}", "body"))
        win_ids.append(id_)
    _append_section(components, root_children, "quick-wins-section", "快速优化建议", win_ids)

    report_page = _text_of(report.get("reportPage"), "")
    if report_page:
        components.append(_text("open-report-label", "打开完整报告", "body"))
        components.append({
            "id": "open-report-button",
            "component": "Button",
            "child": "open-report-label",
            "variant": "primary",
            "action": {"event": {"name": "open_report", "context": {"href": report_page}}},
        })
        root_children.append("open-report-button")

    components.append(_column("root", root_children))
    return [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
        {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": report}},
    ]


def _recommendation_messages(surface_id: str, recommendations: dict[str, Any]) -> list[dict[str, Any]]:
    item_cards: list[str] = []
    components: list[dict[str, Any]] = [
        _text("renderer-note", "后端 A2UI 推荐企业卡片 · Google Agent-to-User Interface", "caption"),
        _text("recommendation-title", {"path": "/title"}, "h3"),
        _text("recommendation-subtitle", {"path": "/subtitle"}, "caption"),
    ]
    for i, item in enumerate(_as_list(recommendations.get("items"))[:DEFAULT_RECOMMENDATIONS]):
        row = _as_record(item)
        title_id = f"company-{i}-title"
        badge_id = f"company-{i}-badge"
        header_id = f"company-{i}-header"
        meta_id = f"company-{i}-meta"
        reason_id = f"company-{i}-reason"
        website_id = f"company-{i}-website"
        contact_id = f"company-{i}-contact"
        body_id = f"company-{i}-body"
        card_id = f"company-{i}-card"
        components.append(_text(title_id, {"path": f"/items/{i}/title"}, "body", 1))
        rank_text = _text_of(row.get("rank"), str(i + 1))
        match_score = _text_of(row.get("match_score") or row.get("score"), "")
        match_text = f"匹配度 {match_score}分" if match_score else "匹配度 待评分"
        opportunity_text = f"机会分 {_text_of(row.get('opportunity_score_label'), '待评估')}"
        confidence_text = f"置信度 {_text_of(row.get('confidence_level'), '低')}"
        badge_text = f"#{rank_text} · {match_text}"
        score_row_id = f"company-{i}-score-row"
        match_id = f"company-{i}-match-score"
        opportunity_id = f"company-{i}-opportunity-score"
        confidence_id = f"company-{i}-confidence"
        action_id = f"company-{i}-action"
        summary_id = f"company-{i}-score-summary"
        components.append(_text(badge_id, badge_text, "caption"))
        components.append(_row(header_id, [title_id, badge_id]))
        components.append(_text(match_id, match_text, "caption"))
        components.append(_text(opportunity_id, opportunity_text, "caption"))
        components.append(_text(confidence_id, confidence_text, "caption"))
        components.append(_row(score_row_id, [match_id, opportunity_id, confidence_id]))
        children = [header_id, score_row_id]
        if row.get("recommended_action"):
            components.append(_text(action_id, {"path": f"/items/{i}/recommended_action"}, "body"))
            children.append(action_id)
        if row.get("meta"):
            components.append(_text(meta_id, {"path": f"/items/{i}/meta"}, "caption"))
            children.append(meta_id)
        if row.get("reason"):
            components.append(_text(reason_id, {"path": f"/items/{i}/reason"}, "body"))
            children.append(reason_id)
        if row.get("score_summary"):
            components.append(_text(summary_id, {"path": f"/items/{i}/score_summary"}, "caption"))
            children.append(summary_id)
        components.append(_text(website_id, {"path": f"/items/{i}/website"}, "caption"))
        components.append(_text(contact_id, {"path": f"/items/{i}/contact"}, "caption"))
        children.extend([website_id, contact_id])
        components.append(_column(body_id, children))
        components.append(_card(card_id, body_id))
        item_cards.append(card_id)
    components.append(_column("recommendation-body", ["recommendation-title", "recommendation-subtitle", *item_cards]))
    components.append(_card("recommendation-card", "recommendation-body"))
    components.append(_column("root", ["renderer-note", "recommendation-card"]))
    return [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
        {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": recommendations}},
    ]


def _guidance_messages(surface_id: str, guidance: dict[str, Any]) -> list[dict[str, Any]]:
    option_cards: list[str] = []
    option_components: list[dict[str, Any]] = []
    for i, item in enumerate(_as_list(guidance.get("options"))[:8]):
        opt = _as_record(item)
        label_id = f"option-{i}"
        desc_id = f"option-{i}-desc"
        body_id = f"option-{i}-body"
        card_id = f"option-card-{i}"
        children = [label_id]
        option_components.append(_text(label_id, _text_of(opt.get("label") or opt.get("value"), ""), "body", 1))
        if opt.get("desc"):
            option_components.append(_text(desc_id, {"path": f"/options/{i}/desc"}, "caption"))
            children.append(desc_id)
        option_components.append(_column(body_id, children))
        option_components.append(_card(card_id, body_id))
        option_cards.append(card_id)
    secondary_cards: list[str] = []
    secondary_components: list[dict[str, Any]] = []
    for i, item in enumerate(_as_list(guidance.get("secondaryOptions"))[:8]):
        opt = _as_record(item)
        label_id = f"secondary-option-{i}"
        card_id = f"secondary-option-card-{i}"
        secondary_components.append(_text(label_id, _text_of(opt.get("label") or opt.get("value"), ""), "body", 1))
        secondary_components.append(_card(card_id, label_id))
        secondary_cards.append(card_id)
    secondary_children: list[str] = []
    if secondary_cards:
        secondary_children = ["secondary-divider", "secondary-title", "secondary-hint", *secondary_cards]
    components = [
        _text("renderer-note", "后端 A2UI 找客户工作区 · Google Agent-to-User Interface", "caption"),
        _text("question-title", {"path": "/title"}, "h3"),
        _text("question-text", {"path": "/question"}, "body", 1),
        _text("question-hint", {"path": "/hint"}, "caption"),
        *option_components,
        *([_text("secondary-divider", "────────", "caption"), _text("secondary-title", {"path": "/secondaryTitle"}, "h4", 1), _text("secondary-hint", {"path": "/secondaryHelperText"}, "caption"), *secondary_components] if secondary_cards else []),
        _column("question-body", ["question-title", "question-text", "question-hint", *option_cards, *secondary_children]),
        _card("question-card", "question-body"),
        _column("root", ["renderer-note", "question-card"]),
    ]
    return [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
        {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": guidance}},
    ]


def build_report_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """Build a browser-rendered A2UI payload for report cards."""
    tool = str(tool_name or "")
    if tool not in REPORT_TOOLS:
        return None
    record = _as_record(data)
    if not record:
        return None
    report = _normalize_report(tool, record)
    surface_id = f"sales-report-{_stable_hash([tool, report.get('title'), report.get('url'), report.get('reportPage')])}"
    return {
        "mime": A2UI_MIME,
        "version": A2UI_VERSION,
        "kind": "report",
        "source": "backend",
        "surfaceId": surface_id,
        "catalogId": BASIC_CATALOG_ID,
        "report": report,
        "messages": _report_messages(surface_id, report),
    }


def build_guidance_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """Build a browser-rendered A2UI payload for one-question missing-info guidance."""
    tool = str(tool_name or "")
    if tool not in GUIDANCE_TOOLS:
        return None
    record = _as_record(data)
    if not record:
        return None
    if record.get("status") != "need_more_info" and not record.get("needs_user_input"):
        return None
    guidance = _normalize_guidance(record)
    if not guidance.get("question"):
        return None
    surface_id = f"sales-guidance-{_stable_hash([tool, guidance.get('question'), guidance.get('missingItems')])}"
    return {
        "mime": A2UI_MIME,
        "version": A2UI_VERSION,
        "kind": "guidance",
        "source": "backend",
        "surfaceId": surface_id,
        "catalogId": BASIC_CATALOG_ID,
        "guidance": guidance,
        "model": guidance,
        "messages": _guidance_messages(surface_id, guidance),
    }


def build_recommendations_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """Build a browser-rendered A2UI payload for recommended company cards."""
    tool = str(tool_name or "")
    if tool not in RECOMMENDATION_TOOLS:
        return None
    record = _as_record(data)
    if not record or record.get("status") == "need_more_info" or record.get("needs_user_input"):
        return None
    recommendations = _normalize_recommendations(record)
    if not _as_list(recommendations.get("items")):
        return None
    surface_id = f"sales-recommendations-{_stable_hash([tool, recommendations.get('title'), recommendations.get('subtitle'), recommendations.get('items')])}"
    return {
        "mime": A2UI_MIME,
        "version": A2UI_VERSION,
        "kind": "recommendations",
        "source": "backend",
        "surfaceId": surface_id,
        "catalogId": BASIC_CATALOG_ID,
        "recommendations": recommendations,
        "model": recommendations,
        "messages": _recommendation_messages(surface_id, recommendations),
    }


def _send_schedule_messages(surface_id: str, schedule: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    root_children: list[str] = ["renderer-note", "schedule-card"]
    head_children = ["schedule-title", "schedule-when"]
    components.append(_text("renderer-note", "后端 A2UI 找客户工作区 · Google Agent-to-User Interface", "caption"))
    components.append(_text("schedule-title", {"path": "/title"}, "h3"))
    # Time uses h4 rather than caption: it's the only thing on this card the user needs to **verify**.
    components.append(_text("schedule-when", {"path": "/whenText"}, "h4", 1))
    if schedule.get("hint"):
        components.append(_text("schedule-hint", {"path": "/hint"}, "body"))
        head_children.append("schedule-hint")
    if schedule.get("warning"):
        components.append(_text("schedule-warning", {"path": "/warning"}, "body", 1))
        head_children.append("schedule-warning")

    body_children = list(head_children)
    included = _as_list(schedule.get("included"))
    if included:
        components.append(_text("included-title", {"path": "/includedTitle"}, "h4", 1))
        body_children.append("included-title")
        for i, _ in enumerate(included):
            name_id = f"included-{i}"
            email_id = f"included-{i}-email"
            state_id = f"included-{i}-state"
            row_id = f"included-row-{i}"
            components.append(_text(name_id, {"path": f"/included/{i}/company"}, "body", 1))
            components.append(_text(email_id, {"path": f"/included/{i}/email"}, "caption"))
            components.append(_text(state_id, {"path": f"/included/{i}/statusText"}, "caption"))
            components.append(_row(row_id, [name_id, email_id, state_id]))
            body_children.append(row_id)
        if schedule.get("moreIncluded"):
            components.append(_text("included-more", {"path": "/moreIncluded"}, "caption"))
            body_children.append("included-more")

    skipped = _as_list(schedule.get("skipped"))
    if skipped:
        components.append(_text("skipped-title", {"path": "/skippedTitle"}, "h4", 1))
        body_children.append("skipped-title")
        for i, _ in enumerate(skipped):
            name_id = f"skipped-{i}"
            reason_id = f"skipped-{i}-reason"
            row_id = f"skipped-row-{i}"
            components.append(_text(name_id, {"path": f"/skipped/{i}/company"}, "body", 1))
            # Reason on the same line as the company name: on two lines it reads like two things, and users ask "so what happened to that one".
            components.append(_text(reason_id, {"path": f"/skipped/{i}/reason"}, "caption"))
            components.append(_row(row_id, [name_id, reason_id]))
            body_children.append(row_id)
        if schedule.get("moreSkipped"):
            components.append(_text("skipped-more", {"path": "/moreSkipped"}, "caption"))
            body_children.append("skipped-more")

    components.append(_text("schedule-footer", {"path": "/footer"}, "caption"))
    body_children.append("schedule-footer")
    components.append(_column("schedule-body", body_children))
    components.append(_card("schedule-card", "schedule-body"))
    components.append(_column("root", root_children))
    return [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
        {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": schedule}},
    ]


def build_send_schedule_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """Scheduling summary card (design 4.5).

    A `need_time=True` return **draws no card at all**: there's no plan yet; the tool returned a follow-up question.
    Drawing an empty shell would make users think it's already scheduled — exactly the misunderstanding the 4.7 boundary table avoids.
    """
    tool = str(tool_name or "")
    if tool not in SEND_SCHEDULE_TOOLS:
        return None
    record = _as_record(data)
    if not record or record.get("need_time"):
        return None
    schedule = _normalize_send_schedule(record)
    if not schedule.get("scheduleId"):
        return None
    surface_id = f"sales-send-schedule-{_stable_hash([schedule.get('scheduleId'), schedule.get('status'), schedule.get('whenText'), schedule.get('included')])}"
    return {
        "mime": A2UI_MIME,
        "version": A2UI_VERSION,
        "kind": "send_schedule",
        "source": "backend",
        "surfaceId": surface_id,
        "catalogId": BASIC_CATALOG_ID,
        "sendSchedule": schedule,
        "model": schedule,
        "messages": _send_schedule_messages(surface_id, schedule),
    }


def _normalize_send_schedule_picker(record: dict[str, Any]) -> dict[str, Any]:
    """Data model of the selection card. **Per-batch entries carry only number, time and company count, no company names.**

    The dialog's product decision is to report only counts ("8 companies will be sent · 2 can't be sent"); the list stays in the post-scheduling
    card and the right-side panel. This follows suit: the fields the model can see are the fields the dialog can show,
    and carrying the list in here would sooner or later get it rendered by someone.
    """
    batches: list[dict[str, Any]] = []
    for raw in _as_list(record.get("batches")):
        item = _as_record(raw)
        campaign_id = int(item.get("campaign_id") or 0)
        lead_count = int(item.get("lead_count") or 0)
        created_text = _text_of(item.get("created_at_text"), "").strip()
        label = _text_of(item.get("label"), "").strip() or f"第 {len(batches) + 1} 批"
        batches.append({
            "campaignId": campaign_id,
            "label": label,
            # One line says "which batch, when it was pushed, how many companies"; don't split into three fields on separate lines.
            "countText": f"{created_text} · {lead_count} 家" if created_text else f"{lead_count} 家",
            "leadCount": lead_count,
        })

    total_leads = sum(x["leadCount"] for x in batches)
    warning = _text_of(record.get("time_warning"), "").strip()
    return {
        "title": "定时发送",
        "whenText": _text_of(record.get("when_text"), "").strip(),
        "scheduledAt": _text_of(record.get("scheduled_at"), "").strip(),
        # Bare Beijing-time string; the frontend puts it into `<input min=...>` so the browser blocks past times first.
        "minScheduledAt": _text_of(record.get("min_scheduled_at"), "").strip(),
        "question": "发给哪一批?" if len(batches) > 1 else "发给这一批?",
        "batches": batches,
        "defaultCampaignId": int(record.get("default_campaign_id") or 0),
        # With only one batch, no "select all": that option is the same set of companies as the only batch, and listing it only makes people
        # think there are others.
        "allLabel": f"全部 {len(batches)} 批 · {total_leads} 家" if len(batches) > 1 else "",
        "warning": warning,
        "footer": "现在**一封都还没发出去**。点确定才会按上面的时间到点逐封发。",
        "confirmLabel": "确定,到点发",
    }


def _send_schedule_picker_messages(surface_id: str, picker: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    root_children: list[str] = ["renderer-note", "picker-card"]
    body_children = ["picker-title", "picker-when"]
    components.append(_text("renderer-note", "后端 A2UI 找客户工作区 · Google Agent-to-User Interface", "caption"))
    components.append(_text("picker-title", {"path": "/title"}, "h3"))
    # Time is the only thing on this card users must verify — "before 3pm" gets parsed as 15:00,
    # a semantic gap, so it must be prominent enough for users to spot and fix at a glance.
    components.append(_text("picker-when", {"path": "/whenText"}, "h4", 1))
    if picker.get("warning"):
        components.append(_text("picker-warning", {"path": "/warning"}, "body", 1))
        body_children.append("picker-warning")

    components.append(_text("picker-question", {"path": "/question"}, "h4", 1))
    body_children.append("picker-question")
    for i, _ in enumerate(_as_list(picker.get("batches"))):
        label_id = f"batch-{i}"
        count_id = f"batch-{i}-count"
        row_id = f"batch-row-{i}"
        components.append(_text(label_id, {"path": f"/batches/{i}/label"}, "body", 1))
        components.append(_text(count_id, {"path": f"/batches/{i}/countText"}, "caption"))
        components.append(_row(row_id, [label_id, count_id]))
        body_children.append(row_id)
    if picker.get("allLabel"):
        components.append(_text("batch-all", {"path": "/allLabel"}, "body"))
        body_children.append("batch-all")

    components.append(_text("picker-footer", {"path": "/footer"}, "caption"))
    body_children.append("picker-footer")
    components.append(_column("picker-body", body_children))
    components.append(_card("picker-card", "picker-body"))
    components.append(_column("root", root_children))
    return [
        {"version": A2UI_VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}},
        {"version": A2UI_VERSION, "updateComponents": {"surfaceId": surface_id, "components": components}},
        {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": picker}},
    ]


def build_send_schedule_picker_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """The "which batch to send?" selection card.

    **No batches, no card**: with no batches this card would be just a time and a meaningless sentence,
    and that case should be left to the model to clarify who to send to (see the deterministic branch in api.py).
    """
    if str(tool_name or "") != SEND_SCHEDULE_PICKER_TOOL:
        return None
    record = _as_record(data)
    if not record or not _as_list(record.get("batches")):
        return None
    picker = _normalize_send_schedule_picker(record)
    surface_id = f"sales-send-picker-{_stable_hash([picker.get('scheduledAt'), picker.get('batches'), picker.get('defaultCampaignId')])}"
    return {
        "mime": A2UI_MIME,
        "version": A2UI_VERSION,
        "kind": "send_schedule_picker",
        "source": "backend",
        "surfaceId": surface_id,
        "catalogId": BASIC_CATALOG_ID,
        "sendSchedulePicker": picker,
        # The frontend uses session_id / campaign_id to call the trial-computation and scheduling endpoints; these are raw backend names,
        # not the display naming layer above.
        "sessionId": _text_of(record.get("session_id"), ""),
        "model": picker,
        "messages": _send_schedule_picker_messages(surface_id, picker),
    }


def build_card_a2ui(tool_name: str, data: Any) -> dict[str, Any] | None:
    """Build an A2UI payload for a Sales Agent tool card when supported."""
    return (
        build_report_a2ui(tool_name, data)
        or build_guidance_a2ui(tool_name, data)
        or build_recommendations_a2ui(tool_name, data)
        or build_send_schedule_a2ui(tool_name, data)
        or build_send_schedule_picker_a2ui(tool_name, data)
    )
