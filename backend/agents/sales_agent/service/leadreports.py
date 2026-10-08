"""Sales Agent service layer: lead reports: generate report shares and public access for target companies."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from agents.sales_agent.discovery import normalize_domain
from agents.sales_agent.models import SalesAgentCompanyORM, SalesAgentLeadRecommendationORM, SalesAgentReportShareORM
from geo.models.full_diagnosis import FullDiagnosisReportORM

from agents.sales_agent.service.common import SalesAgentError, _clean_list, record_compliance_event
from agents.sales_agent.service.entities import _company_contact_summary, _lead_company, serialize_company
from agents.sales_agent.service.reports import (
    _active_report_share_for_lead,
    _create_report_share,
    _full_report_by_id,
    _full_report_for_share,
    _full_report_public_payload,
    _latest_matching_full_report,
    _public_company_payload,
    _record_report_visit_for_share,
    _sales_agent_report_public_url,
    _sync_lead_report_snapshot,
    serialize_report_share,
    start_detection_report,
)
from agents.sales_agent.service.recommend import serialize_lead


async def generate_lead_report_share(db: Session, user_id: int, lead_id: int, public_base_url: str = "") -> dict[str, Any]:
    lead, company = _lead_company(db, user_id, lead_id)
    if not (company.website or "").strip():
        raise SalesAgentError("目标企业缺少官网,无法生成真实诊断报告。请先补充官网后再生成。")

    report = None
    share = _active_report_share_for_lead(db, user_id, lead.id)
    if share is not None:
        report = _full_report_for_share(db, share)
        if report is not None and report.status in {"generating", "ready"}:
            _sync_lead_report_snapshot(lead, report)
            db.commit()
            db.refresh(lead)
            db.refresh(share)
            return _lead_report_share_response(db, lead, company, share, report, reused=True, public_base_url=public_base_url)

    if lead.source_report_table == "full_diagnosis_reports" and lead.source_report_id:
        existing_report = _full_report_by_id(db, user_id, lead.source_report_id)
        if existing_report is not None and normalize_domain(existing_report.url) == normalize_domain(company.website) and existing_report.status in {"generating", "ready"}:
            report = existing_report

    if report is None:
        report = _latest_matching_full_report(db, user_id, company)

    if report is None:
        result = await start_detection_report(
            db,
            user_id,
            url=company.website,
            brand_name=company.name,
            industry_terms=_clean_list([company.industry], 5),
            brand_terms=_clean_list([company.name], 5),
            competitors=[],
        )
        if result.get("status") == "failed" or not result.get("source_report_id"):
            return {
                "blocked": False,
                "status": "failed",
                "report_status": "failed",
                "lead_id": lead.id,
                "company": serialize_company(company),
                "hint": result.get("error") or "诊断报告发起失败,请稍后重试。",
            }
        report = _full_report_by_id(db, user_id, str(result.get("source_report_id") or ""))
        if report is None:
            raise SalesAgentError("诊断报告已发起,但暂时无法读取报告记录。请稍后刷新。", status_code=503)

    share = _create_report_share(db, user_id, lead.id, company.id, report.id)
    _sync_lead_report_snapshot(lead, report)
    db.commit()
    db.refresh(lead)
    db.refresh(share)
    db.refresh(report)
    record_compliance_event(
        db,
        user_id,
        "generate_lead_report_share",
        "low",
        "allowed",
        "为 Sales Agent 目标企业创建专用公开报告链接",
        {"lead_id": lead.id, "company_id": company.id, "source_report_id": str(report.id), "share_id": share.id},
    )
    return _lead_report_share_response(db, lead, company, share, report, reused=False, public_base_url=public_base_url)


def _lead_report_share_response(
    db: Session,
    lead: SalesAgentLeadRecommendationORM,
    company: SalesAgentCompanyORM,
    share: SalesAgentReportShareORM,
    report: FullDiagnosisReportORM,
    reused: bool = False,
    public_base_url: str = "",
) -> dict[str, Any]:
    report_share = serialize_report_share(share, report, public_base_url)
    contacts = _company_contact_summary(db, company.id)
    lead_payload = serialize_lead(lead, company, contacts, public_base_url=public_base_url)
    report_status = report.status or "generating"
    return {
        "blocked": False,
        "status": report_status,
        "report_status": report_status,
        "report_id": report.id,
        "source_report_table": "full_diagnosis_reports",
        "source_report_id": str(report.id),
        "share_token": share.public_token,
        "public_url": report_share["public_url"],
        "report_share": report_share,
        "lead": lead_payload,
        "company": serialize_company(company, contacts),
        "reused": reused,
        "hint": "报告已完成,可以查看报告并基于报告画像生成邮件。" if report_status == "ready" else "诊断已开始,轻量检测约 1–2 分钟;完成前不会把生成中数据当结论。",
    }


def get_public_report_share(
    db: Session,
    public_token: str,
    public_base_url: str = "",
    delivery_id: int | None = None,
    user_agent: str = "",
    ip_hash: str = "",
) -> dict[str, Any]:
    token = (public_token or "").strip()
    if not token:
        raise SalesAgentError("公开报告链接不存在或已失效。", status_code=404)
    share = db.query(SalesAgentReportShareORM).filter(SalesAgentReportShareORM.public_token == token, SalesAgentReportShareORM.status == "active").first()
    if share is None or (share.expires_at is not None and share.expires_at <= datetime.utcnow()):
        raise SalesAgentError("公开报告链接不存在或已失效。", status_code=404)
    report = _full_report_for_share(db, share)
    if report is None:
        raise SalesAgentError("公开报告对应的诊断记录不存在或已失效。", status_code=404)
    _record_report_visit_for_share(db, share, delivery_id=delivery_id, user_agent=user_agent, ip_hash=ip_hash)
    company = db.get(SalesAgentCompanyORM, share.company_id)
    return {
        "public_token": share.public_token,
        "public_url": _sales_agent_report_public_url(share.public_token, public_base_url),
        "status": share.status,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
        "company": _public_company_payload(company),
        "report": _full_report_public_payload(report),
        "privacy": "该链接只公开本次 Sales Agent 分享的诊断报告白名单内容,不包含联系人、聊天记录、内部线索状态或用户数据。",
    }
