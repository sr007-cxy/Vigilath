"""Sales Agent service layer: reports: summary normalization, public share tokens, launching a real one-click full diagnosis."""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any
import jwt
from sqlalchemy import or_
from sqlalchemy.orm import Session
from agents.sales_agent.discovery import normalize_domain
from agents.sales_agent.models import (
    SalesAgentCompanyORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentReportShareORM,
)
from geo.database import settings
from geo.models.detection import DetectionRecordORM
from geo.models.full_diagnosis import FullDiagnosisReportORM
from geo.models.user import UserORM

from agents.sales_agent.service.common import (
    ALGORITHM,
    GEO_BACKEND_URL,
    SalesAgentError,
    _clean_list,
    _dumps,
    _loads,
    _normalize_report_table,
    record_compliance_event,
)
from agents.sales_agent.service.entities import _event_metadata, _record_email_event

# Audit scope for lead reports: only the four site-side checks (about 1-2 minutes), not the heavy engine queue stage.
# Requirements: docs/08-独立模块/SalesAgent/02-需求分析/找客户推荐与邮件流程.md §7.3 (find-customers recommendation and email flow).
LEAD_REPORT_SCOPE = "lite"


def _full_report_summary(row: FullDiagnosisReportORM) -> dict[str, Any]:
    scores = _loads(getattr(row, "scores_json", None), {})
    narrative = _loads(getattr(row, "narrative_json", None), {})
    progress = _loads(getattr(row, "progress_json", None), {})
    subresults = _loads(getattr(row, "subresults_json", None), {})
    return {
        "source_report_table": "full_diagnosis_reports",
        "source_report_id": str(row.id),
        "status": row.status,
        "url": row.url or "",
        "brand_name": row.brand_name or "",
        "industry_terms": _loads(getattr(row, "industry_terms_json", None), []),
        "brand_terms": _loads(getattr(row, "brand_terms_json", None), []),
        "competitor_domains": _loads(getattr(row, "competitor_domains_json", None), []),
        "total_score": scores.get("total"),
        "grade": scores.get("grade"),
        "dimensions": scores.get("dimensions") or [],
        "missing_dimensions": scores.get("missing_dimensions") or [],
        "executive_summary": narrative.get("executive_summary") or "",
        "key_findings": (narrative.get("key_findings") or [])[:8],
        "quick_wins": (narrative.get("quick_wins") or [])[:8],
        "progress": {"done": progress.get("done"), "total": progress.get("total"), "steps": progress.get("steps") or []},
        "detail_points": _report_detail_points({"subresults": subresults}),
        "subresults_keys": list(subresults.keys())[:20] if isinstance(subresults, dict) else [],
        "report_page": f"/diagnosis/{row.id}",
        "has_real_detection": row.status == "ready",
    }


def _detection_summary(row: DetectionRecordORM) -> dict[str, Any]:
    snapshot = _loads(getattr(row, "result_snapshot", None), {})
    seo = snapshot.get("seo") or snapshot.get("seo_result") or snapshot.get("seo_summary") or {}
    geo = snapshot.get("geo") if isinstance(snapshot.get("geo"), dict) else snapshot
    return {
        "source_report_table": "detection_records",
        "source_report_id": str(row.id),
        "status": "ready",
        "url": row.url or "",
        "brand_name": snapshot.get("brand_name") or snapshot.get("brand") or "",
        "total_score": getattr(row, "score", None),
        "grade": getattr(row, "grade", "") or "",
        "tier": getattr(row, "tier", "") or "",
        "mode": getattr(row, "mode", "") or "",
        "seo_score": seo.get("score") if isinstance(seo, dict) else None,
        "geo_summary": geo if isinstance(geo, dict) else {},
        "raw_snapshot_keys": list(snapshot.keys())[:20] if isinstance(snapshot, dict) else [],
        "has_real_detection": True,
    }


def _guard_report_summary(db: Session, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Unfinished reports must not carry scores/conclusions out: erase them and say so truthfully, never silently let them through."""
    from agents.sales_agent.runtime.verify import redact_unfinished_report, verify_report_summary

    problems = verify_report_summary(payload, bool(payload.get("has_real_detection")))
    if not problems:
        return payload
    redact_unfinished_report(payload)
    payload["verification_problems"] = problems
    payload["hint"] = "报告尚未完成,当前只有进度信息,没有可引用的结论。"
    record_compliance_event(
        db, user_id, "get_report_summary", "medium", "needs_approval",
        "报告未完成但摘要携带结论,已抹除:" + ";".join(problems),
        {"source_report_id": payload.get("source_report_id", ""), "status": payload.get("status", ""), "problems": problems},
    )
    return payload


def get_report_summary(db: Session, user_id: int, source_report_table: str = "", source_report_id: str = "") -> dict[str, Any]:
    """Read and normalize the current user's real audit report; never generate or fabricate."""
    table = _normalize_report_table(source_report_table)
    sid = str(source_report_id or "").strip()

    if not table:
        full = (
            db.query(FullDiagnosisReportORM)
            .filter(FullDiagnosisReportORM.user_id == user_id, FullDiagnosisReportORM.deleted_at.is_(None))
            .order_by(FullDiagnosisReportORM.id.desc())
            .first()
        )
        if full is not None:
            return _guard_report_summary(db, user_id, _full_report_summary(full))
        det = (
            db.query(DetectionRecordORM)
            .filter(DetectionRecordORM.user_id == user_id, DetectionRecordORM.deleted_at.is_(None))
            .order_by(DetectionRecordORM.id.desc())
            .first()
        )
        if det is not None:
            return _detection_summary(det)
        return {"status": "none", "has_real_detection": False, "hint": "当前用户还没有可读取的检测报告。"}

    if not sid:
        raise SalesAgentError("读取指定报告时必须提供 source_report_id。", public=True)
    try:
        rid = int(sid)
    except ValueError as e:
        raise SalesAgentError("source_report_id 必须是数字字符串。", public=True) from e

    if table == "full_diagnosis_reports":
        row = (
            db.query(FullDiagnosisReportORM)
            .filter(FullDiagnosisReportORM.id == rid, FullDiagnosisReportORM.user_id == user_id, FullDiagnosisReportORM.deleted_at.is_(None))
            .first()
        )
        if row is None:
            raise SalesAgentError("报告不存在或不属于当前用户。", status_code=404)
        return _guard_report_summary(db, user_id, _full_report_summary(row))

    row = (
        db.query(DetectionRecordORM)
        .filter(DetectionRecordORM.id == rid, DetectionRecordORM.user_id == user_id, DetectionRecordORM.deleted_at.is_(None))
        .first()
    )
    if row is None:
        raise SalesAgentError("检测记录不存在或不属于当前用户。", status_code=404)
    return _detection_summary(row)


def _sales_agent_public_base_url(public_base_url: str = "") -> str:
    return (public_base_url or os.environ.get("SALES_AGENT_PUBLIC_BASE_URL") or getattr(settings, "FRONTEND_URL", "") or "").strip().rstrip("/")


def _sales_agent_report_public_url(public_token: str, public_base_url: str = "") -> str:
    path = f"/sales-agent/report/{public_token}"
    base = _sales_agent_public_base_url(public_base_url)
    return f"{base}{path}" if base else path


def _active_report_share_for_lead(db: Session, user_id: int, lead_id: int) -> SalesAgentReportShareORM | None:
    now = datetime.utcnow()
    return (
        db.query(SalesAgentReportShareORM)
        .filter(
            SalesAgentReportShareORM.user_id == user_id,
            SalesAgentReportShareORM.lead_id == int(lead_id),
            SalesAgentReportShareORM.status == "active",
            or_(SalesAgentReportShareORM.expires_at.is_(None), SalesAgentReportShareORM.expires_at > now),
        )
        .order_by(SalesAgentReportShareORM.id.desc())
        .first()
    )


def _full_report_by_id(db: Session, user_id: int, report_id: str | int) -> FullDiagnosisReportORM | None:
    try:
        rid = int(report_id)
    except Exception:  # noqa: BLE001
        return None
    return (
        db.query(FullDiagnosisReportORM)
        .filter(FullDiagnosisReportORM.id == rid, FullDiagnosisReportORM.user_id == user_id, FullDiagnosisReportORM.deleted_at.is_(None))
        .first()
    )


def _full_report_for_share(db: Session, share: SalesAgentReportShareORM) -> FullDiagnosisReportORM | None:
    if (share.source_report_table or "") != "full_diagnosis_reports":
        return None
    return _full_report_by_id(db, share.user_id, share.source_report_id)


def serialize_report_share(share: SalesAgentReportShareORM, report: FullDiagnosisReportORM | None = None, public_base_url: str = "") -> dict[str, Any]:
    report_status = (report.status if report is not None else "") or ""
    return {
        "id": share.id,
        "public_token": share.public_token,
        "share_token": share.public_token,
        "public_url": _sales_agent_report_public_url(share.public_token, public_base_url),
        "source_report_table": share.source_report_table,
        "source_report_id": share.source_report_id,
        "report_id": share.source_report_id,
        "report_status": report_status,
        "status": share.status,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
    }


def _public_company_payload(company: SalesAgentCompanyORM | None) -> dict[str, Any]:
    if company is None:
        return {}
    return {
        "id": company.id,
        "name": company.name,
        "industry": company.industry,
        "region": company.region,
        "website": company.website,
        "employee_size": company.employee_size,
    }


def _full_report_public_payload(row: FullDiagnosisReportORM) -> dict[str, Any]:
    subresults = _loads(row.subresults_json, {})
    return {
        "id": row.id,
        "status": row.status or "generating",
        "url": row.url or "",
        "brand_name": row.brand_name or "",
        "competitor_domains": _loads(row.competitor_domains_json, []),
        "industry_terms": _loads(row.industry_terms_json, []),
        "brand_terms": _loads(row.brand_terms_json, []),
        "progress": _loads(row.progress_json, {}) or None,
        "scores": _loads(row.scores_json, {}) or None,
        "narrative": _loads(row.narrative_json, {}) or None,
        "subresults": subresults if isinstance(subresults, dict) else None,
        "subresults_keys": list(subresults.keys())[:20] if isinstance(subresults, dict) else [],
        "error": row.error if row.status == "failed" else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _create_report_share(db: Session, user_id: int, lead_id: int, company_id: int, report_id: str | int) -> SalesAgentReportShareORM:
    existing = (
        db.query(SalesAgentReportShareORM)
        .filter(
            SalesAgentReportShareORM.user_id == user_id,
            SalesAgentReportShareORM.lead_id == int(lead_id),
            SalesAgentReportShareORM.source_report_table == "full_diagnosis_reports",
            SalesAgentReportShareORM.source_report_id == str(report_id),
            SalesAgentReportShareORM.status == "active",
        )
        .order_by(SalesAgentReportShareORM.id.desc())
        .first()
    )
    if existing is not None:
        return existing
    token = secrets.token_urlsafe(32)
    for _ in range(5):
        if not db.query(SalesAgentReportShareORM).filter(SalesAgentReportShareORM.public_token == token).first():
            break
        token = secrets.token_urlsafe(32)
    share = SalesAgentReportShareORM(
        public_token=token,
        user_id=user_id,
        lead_id=int(lead_id),
        company_id=int(company_id),
        source_report_table="full_diagnosis_reports",
        source_report_id=str(report_id),
        status="active",
    )
    db.add(share)
    db.flush()
    return share


def _report_summary_matches_company(company: SalesAgentCompanyORM, report: dict[str, Any]) -> bool:
    report_domain = normalize_domain(str(report.get("url") or ""))
    company_domain = normalize_domain(company.website)
    return bool(report_domain and company_domain and report_domain == company_domain)


def _sync_lead_report_snapshot(lead: SalesAgentLeadRecommendationORM, report: FullDiagnosisReportORM) -> None:
    lead.source_report_table = "full_diagnosis_reports"
    lead.source_report_id = str(report.id)
    lead.report_snapshot = _dumps(_full_report_summary(report))
    if report.status == "ready" and lead.status not in {"Unsubscribed", "Blacklisted", "Pending Approval", "Contacted", "Replied", "Interested", "Meeting Booked"}:
        lead.status = "Diagnosed"


def _latest_matching_full_report(db: Session, user_id: int, company: SalesAgentCompanyORM) -> FullDiagnosisReportORM | None:
    """Find a reusable report for this company: both full and lite tiers count.

    A full report is a superset of a lite report, never less useful for leads; reports carry their own scope and the frontend uses it to show
    "not audited this time" items, so we do not filter by tier here and just take the latest one for the same domain.
    """
    company_domain = normalize_domain(company.website)
    if not company_domain:
        return None
    rows = (
        db.query(FullDiagnosisReportORM)
        .filter(FullDiagnosisReportORM.user_id == user_id, FullDiagnosisReportORM.deleted_at.is_(None), FullDiagnosisReportORM.status.in_(["generating", "ready"]))
        .order_by(FullDiagnosisReportORM.id.desc())
        .limit(20)
        .all()
    )
    for row in rows:
        if normalize_domain(row.url) == company_domain:
            return row
    return None


async def start_detection_report(
    db: Session,
    user_id: int,
    url: str = "",
    brand_name: str = "",
    industry_terms: list[str] | None = None,
    brand_terms: list[str] | None = None,
    competitors: list[str] | None = None,
    scope: str = LEAD_REPORT_SCOPE,
) -> dict[str, Any]:
    """Launch a report via the main backend's real one-click full diagnosis entry; Sales Agent does not fake audits in this process.

    scope defaults to lite: lead reports only run the four site-side checks, results in 1-2 minutes, without the heavy engine queue stage,
    which is the entire source of waiting and hangs. For full conclusions use the user's own one-click full diagnosis.
    """
    import httpx

    site = (url or "").strip()
    brand = (brand_name or "").strip()
    if not site and not brand:
        raise SalesAgentError("请提供官网 URL 或品牌名后再发起检测。")
    if not brand and site:
        domain = re.sub(r"^https?://", "", site, flags=re.IGNORECASE).split("/", 1)[0].split(":", 1)[0].lower()
        if domain.startswith("www."):
            domain = domain[4:]
        brand = (domain.split(".", 1)[0] if domain else site).strip()
    terms = _clean_list(industry_terms, 5)
    brand_values = _clean_list(brand_terms, 5) or ([brand] if brand else [])
    # lite only runs the four site-side checks and needs no keywords at all: industry keywords feed the heavy engine stage, so do not block here
    if not terms and scope != "lite":
        raise SalesAgentError("请先补充行业关键词后再生成诊断报告。")
    if not brand_values:
        raise SalesAgentError("请先补充品牌词后再生成诊断报告。")
    user = db.get(UserORM, user_id)
    if user is None or not user.email:
        raise SalesAgentError("当前账号没有可用邮箱,无法签发检测请求。", status_code=401)
    token = jwt.encode({"sub": user.email, "exp": datetime.utcnow() + timedelta(minutes=30)}, settings.SECRET_KEY, algorithm=ALGORITHM)
    payload = {
        "url": site,
        "brand_name": brand or site,
        "industry_terms": terms,
        "brand_terms": brand_values,
        "competitor_domains": _clean_list(competitors, 5),
        "scope": scope,
    }
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            resp = await client.post(
                f"{GEO_BACKEND_URL}/api/check/full/generate",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as e:
        record_compliance_event(db, user_id, "start_detection_report", "medium", "blocked", "主后端不可达", {"error": str(e)})
        return {"status": "failed", "error": f"发起诊断失败(后端不可达):{e}"}
    if resp.status_code >= 400:
        try:
            msg = (resp.json() or {}).get("message") or (resp.json() or {}).get("detail") or resp.text[:200]
        except Exception:  # noqa: BLE001
            msg = resp.text[:200]
        record_compliance_event(db, user_id, "start_detection_report", "medium", "blocked", f"主后端返回 {resp.status_code}", {"message": msg})
        return {"status": "failed", "error": f"发起诊断失败({resp.status_code}):{msg}"}
    body = resp.json() if resp.content else {}
    report_id = body.get("report_id")
    record_compliance_event(db, user_id, "start_detection_report", "low", "allowed", "通过真实检测入口发起", {"payload": payload, "response": body})
    return {
        "report_id": report_id,
        "source_report_table": "full_diagnosis_reports",
        "source_report_id": str(report_id or ""),
        "status": body.get("status", "generating"),
        "report_page": f"/diagnosis/{report_id}" if report_id else "",
        "hint": "诊断已开始,轻量检测约 1–2 分钟;完成前不能把生成中数据当结论。",
    }


def _record_report_visit_for_share(db: Session, share: SalesAgentReportShareORM, delivery_id: int | None = None, user_agent: str = "", ip_hash: str = "") -> None:
    delivery = None
    if delivery_id:
        delivery = db.query(SalesAgentEmailDeliveryORM).filter(
            SalesAgentEmailDeliveryORM.id == int(delivery_id),
            SalesAgentEmailDeliveryORM.report_share_id == share.id,
        ).first()
    if delivery is None:
        delivery = db.query(SalesAgentEmailDeliveryORM).filter(SalesAgentEmailDeliveryORM.report_share_id == share.id).order_by(SalesAgentEmailDeliveryORM.id.desc()).first()
    now = datetime.utcnow()
    if delivery is not None:
        if delivery.first_report_visit_at is None:
            delivery.first_report_visit_at = now
        delivery.last_report_visit_at = now
        delivery.report_visit_count = int(delivery.report_visit_count or 0) + 1
        if delivery.read_at is None:
            delivery.read_at = now
        _record_email_event(db, delivery, delivery.user_id, "report_visit", _event_metadata(user_agent, ip_hash, public_token=share.public_token))
    else:
        _record_email_event(db, None, share.user_id, "report_visit", _event_metadata(user_agent, ip_hash, public_token=share.public_token), lead_id=share.lead_id, company_id=share.company_id)
    db.commit()


def _report_detail_points(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("subresults")
    if not isinstance(raw, dict):
        return []
    points: list[dict[str, Any]] = []
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        label = str(value.get("title") or value.get("label") or key).strip()
        status = str(value.get("status") or data.get("status") or value.get("level") or "").strip()
        score = value.get("score", data.get("score"))
        summary = str(value.get("summary") or value.get("message") or data.get("summary") or data.get("message") or "").strip()
        issues = value.get("issues") or value.get("findings") or data.get("issues") or data.get("findings") or []
        if not isinstance(issues, list):
            issues = [issues]
        issue_text = [str(x.get("text") if isinstance(x, dict) else x).strip() for x in issues]
        issue_text = [x for x in issue_text if x][:3]
        if score is None and not summary and not issue_text:
            continue
        points.append({
            "key": key,
            "label": label,
            "status": status,
            "score": score,
            "summary": summary[:220],
            "issues": issue_text,
        })
        if len(points) >= 10:
            break
    return points


# Report sub-item -> user-facing wording. progress_json stores the mode (base/aeo/...),
# and showing it directly exposes internal names to users (explicitly forbidden in prompts).
# The single source of truth is in the orchestration layer: its failure copy must use the same wording, or two separately edited copies will drift.
from geo.services.full_diagnosis_generator import STEP_LABELS as REPORT_STEP_LABELS


def _heal_stale_report(db: Session, row: FullDiagnosisReportORM) -> bool:
    """Mark zombie rows failed in place; return whether anything changed.

    The background work runs in a daemon thread and is killed whenever the backend restarts, leaving status stuck at generating.
    There used to be only two fallbacks: one scan at startup + a lazy check in `GET /check/full/{id}`, and neither covers
    the Sales Agent progress stream. The restart at 2026-08-23 23:42 interrupted 5 reports; at the next restart 20 minutes later
    they had only been idle 21 minutes (under the 25-minute threshold) so were not caught, there was no restart after that, and the workspace
    showed "diagnosing" for 17 hours. Scanning at startup inevitably misses the batch the restart itself just created.
    """
    from geo.services.full_diagnosis_generator import STALE_THRESHOLD

    if row.status != "generating" or not row.updated_at:
        return False
    if datetime.utcnow() - row.updated_at <= STALE_THRESHOLD:
        return False
    row.status = "failed"
    row.error = "后端在诊断途中重启,后台任务被中断。请重新生成。"
    row.updated_at = datetime.utcnow()
    return True


def running_report_progress(db: Session, user_id: int, include_recent_seconds: int = 120) -> list[dict[str, Any]]:
    """Reports currently running for this user + ones that just finished.

    Just-finished ones are included too (within 2 minutes by default): otherwise the client can only see "running" and never "finished",
    and the last progress update is stuck at 7/8 forever.

    Also does zombie self-healing: this is polled every 3 seconds, the most reliable self-healing moment, independent of "the next restart".
    """
    cutoff = datetime.utcnow() - timedelta(seconds=max(0, int(include_recent_seconds)))
    rows = (
        db.query(FullDiagnosisReportORM)
        .filter(
            FullDiagnosisReportORM.user_id == user_id,
            FullDiagnosisReportORM.deleted_at.is_(None),
            or_(FullDiagnosisReportORM.status == "generating",
                FullDiagnosisReportORM.updated_at >= cutoff),
        )
        .order_by(FullDiagnosisReportORM.id.desc())
        .limit(50)
        .all()
    )
    # Do not write this as any(generator): that would short-circuit at the first zombie, leaving later zombies in the same batch untreated
    healed = [row for row in rows if _heal_stale_report(db, row)]
    if healed:
        db.commit()

    out: list[dict[str, Any]] = []
    for row in rows:
        progress = _loads(getattr(row, "progress_json", None), {})
        steps = progress.get("steps") if isinstance(progress.get("steps"), list) else []
        current = ""
        for step in steps:
            if isinstance(step, dict) and step.get("status") in (None, "", "pending", "running"):
                current = REPORT_STEP_LABELS.get(str(step.get("mode") or ""), "")
                break
        leads = (
            db.query(SalesAgentLeadRecommendationORM.id)
            .filter(SalesAgentLeadRecommendationORM.user_id == user_id,
                    SalesAgentLeadRecommendationORM.source_report_id == str(row.id))
            .all()
        )
        started = row.created_at or row.updated_at
        out.append({
            "type": "report_progress",
            "report_id": row.id,
            "lead_ids": [x[0] for x in leads],
            "status": row.status,
            "done": int(progress.get("done") or 0),
            "total": int(progress.get("total") or 0),
            "current_step": current,
            "elapsed_seconds": int((datetime.utcnow() - started).total_seconds()) if started else 0,
            "url": row.url or "",
            "brand_name": row.brand_name or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        })
    return out
