"""Compliance audit center API (P0) — ledger queries + compliance profile + audit timeline.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md chapter 8.
User side checks topic ownership (admin can read across users); lexicon management requires require_admin.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.api.auth import _user_role, get_current_user, is_staff, require_admin
from geo.api.ai_telemetry import get_db
from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicGeneratedDocORM
from geo.models.compliance import (
    ComplianceAlertORM, ComplianceAuditLogORM, ComplianceLexiconORM,
    ComplianceLexiconTermORM, ComplianceScanResultORM, ComplianceTicketORM,
)
from geo.services import compliance_audit, compliance_scanner
from geo.services.access_control import (
    ACTION_READ, ACTION_WRITE, require_resource_access, resource_not_found,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/compliance")
admin_router = APIRouter(prefix="/admin/compliance")


def _topic_or_404(
    db: Session, topic_id: int, user, action: str = ACTION_WRITE,
) -> AiTelemetryTopicORM:
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t:
        raise resource_not_found()
    require_resource_access(db, user, t, action)
    return t


# ── Compliance profile ─────────────────────────────────────────────────────────────

YMYL_MODES = {
    1: {"review_layers": 4, "sample_pct": 20, "log_retention_days": 1095},
    2: {"review_layers": 3, "sample_pct": 10, "log_retention_days": 365},
    3: {"review_layers": 2, "sample_pct": 5, "log_retention_days": 180},
    0: {"review_layers": 2, "sample_pct": 0, "log_retention_days": 180},
}


@router.get("/topics/{topic_id}/profile")
def get_profile(
    topic_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    profile = compliance_scanner.load_profile(t)
    industry_tier = compliance_scanner.tier_of(t.industry or "")
    ymyl = profile.get("ymyl_tier")
    if not isinstance(ymyl, int):
        ymyl = compliance_scanner.ymyl_tier_of(t.industry or "")
    mode = YMYL_MODES.get(ymyl, YMYL_MODES[0])

    lexicons = db.query(ComplianceLexiconORM).filter(ComplianceLexiconORM.enabled.is_(True)).all()
    term_counts = dict(
        db.query(ComplianceLexiconTermORM.lexicon_id, func.count(ComplianceLexiconTermORM.id))
          .filter(ComplianceLexiconTermORM.active.is_(True))
          .filter(ComplianceLexiconTermORM.status == "active")
          .group_by(ComplianceLexiconTermORM.lexicon_id).all()
    )
    admission_ok, admission_reason = compliance_scanner.check_admission(
        t.industry or "", getattr(t, "name", "") or "")
    return {
        "topic_id": t.id,
        "industry": t.industry or "",
        "industry_tier": industry_tier,
        "ymyl_tier": ymyl,
        "admission": {"allowed": admission_ok, "reason": admission_reason},
        "required_qualification_types": profile.get("required_qualification_types") or [],
        "review_layers": mode["review_layers"],
        "sample_pct": mode["sample_pct"],
        "log_retention_days": mode["log_retention_days"],
        "archive_retention_years": 3 if ymyl else 0,   # Record retention: YMYL always ≥3 years (7.2)
        "restrictions": profile.get("restrictions") or [],
        "lexicons": [
            {
                "id": l.id,
                "code": l.code, "name": l.name, "tier": l.tier, "version": l.version,
                "term_count": int(term_counts.get(l.id, 0)),
                "on": l.tier in ("generic", industry_tier),
            }
            for l in lexicons
        ],
        "qualifications": compliance_scanner.qualification_status(t),
        "disclaimers": profile.get("disclaimers") or [],
    }


class QualificationItem(BaseModel):
    type: str
    ref: str = ""
    valid_until: str = ""          # YYYY-MM-DD, empty = indefinite
    evidence_ref: str = ""


class QualificationsPayload(BaseModel):
    qualifications: list[QualificationItem]
    required_qualification_types: Optional[list[str]] = None


@router.put("/topics/{topic_id}/qualifications")
def put_qualifications(
    topic_id: int,
    payload: QualificationsPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Gate 0: register / update the entity qualification profile (owner or admin)."""
    t = _topic_or_404(db, topic_id, current_user)
    profile = compliance_scanner.load_profile(t)
    profile["qualifications"] = [q.dict() for q in payload.qualifications]
    if payload.required_qualification_types is not None:
        profile["required_qualification_types"] = payload.required_qualification_types
    t.compliance_profile_json = json.dumps(profile, ensure_ascii=False)
    compliance_audit.record(
        db, topic_id=t.id, event_type="admission", object_type="topic", object_id=t.id,
        actor_type=_user_role(current_user),
        actor_id=current_user.id, actor_name=getattr(current_user, "email", "") or "",
        detail={"action": "qualifications_updated",
                "types": [q.type for q in payload.qualifications]},
    )
    db.commit()
    return {"ok": True, "qualifications": compliance_scanner.qualification_status(t)}


# ── Metric rows ───────────────────────────────────────────────────────────────

@router.get("/topics/{topic_id}/kpis")
def get_kpis(
    topic_id: int,
    days: int = Query(30, ge=1, le=365),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    since = datetime.utcnow() - timedelta(days=days)
    base = (db.query(ComplianceScanResultORM)
              .filter(ComplianceScanResultORM.topic_id == t.id)
              .filter(ComplianceScanResultORM.scanned_at >= since))
    scanned_docs = base.with_entities(
        func.count(func.distinct(ComplianceScanResultORM.doc_id))).scalar() or 0
    returned_docs = (base.filter(ComplianceScanResultORM.verdict == "returned")
                         .with_entities(func.count(func.distinct(ComplianceScanResultORM.doc_id)))
                         .scalar() or 0)
    first_log_at = (db.query(func.min(ComplianceAuditLogORM.created_at))
                      .filter(ComplianceAuditLogORM.topic_id == t.id).scalar())
    log_days = (datetime.utcnow() - first_log_at).days if first_log_at else 0

    # P1 in-process metrics
    alerts_total = (db.query(func.count(ComplianceAlertORM.id))
                      .filter(ComplianceAlertORM.topic_id == t.id)
                      .filter(ComplianceAlertORM.found_at >= since)
                      .filter(ComplianceAlertORM.status != "dismissed").scalar() or 0)
    alerts_closed = (db.query(func.count(ComplianceAlertORM.id))
                       .filter(ComplianceAlertORM.topic_id == t.id)
                       .filter(ComplianceAlertORM.found_at >= since)
                       .filter(ComplianceAlertORM.status == "closed").scalar() or 0)
    closed_tickets = (db.query(ComplianceTicketORM)
                        .filter(ComplianceTicketORM.topic_id == t.id)
                        .filter(ComplianceTicketORM.status == "closed")
                        .filter(ComplianceTicketORM.opened_at >= since).all())
    if closed_tickets:
        hours = [(tk.closed_at - tk.opened_at).total_seconds() / 3600
                 for tk in closed_tickets if tk.closed_at]
        avg_hours = round(sum(hours) / len(hours), 1) if hours else None
    else:
        avg_hours = None
    return {
        "days": days,
        "scanned_docs": int(scanned_docs),
        "returned_docs": int(returned_docs),
        "alerts": int(alerts_total),
        "alerts_closed": int(alerts_closed),
        "avg_disposal_hours": avg_hours,
        "log_retained_days": max(log_days, 0),
    }


# ── Pre-publication review ledger ─────────────────────────────────────────────────────────

_DOC_STATUS_CHAIN = {
    # doc.status → (review chain label, tone)
    "rejected": ("智能审核拦截 / 退回", "ban"),
    "pending_review": ("人工初审中", "warn"),
    "approved": ("人工审核通过", "ok"),
    "published": ("已发布", "ok"),
    "draft": ("待送审", "plain"),
    "generating": ("生成中", "plain"),
}


@router.get("/topics/{topic_id}/scans")
def list_scans(
    topic_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pre-publication review ledger — aggregated per doc, taking each doc's latest scan."""
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    latest_ids = (
        db.query(func.max(ComplianceScanResultORM.id))
          .filter(ComplianceScanResultORM.topic_id == t.id)
          .group_by(ComplianceScanResultORM.doc_id)
    )
    q = (db.query(ComplianceScanResultORM, TopicGeneratedDocORM)
           .join(TopicGeneratedDocORM, TopicGeneratedDocORM.id == ComplianceScanResultORM.doc_id)
           .filter(ComplianceScanResultORM.id.in_(latest_ids))
           .order_by(ComplianceScanResultORM.scanned_at.desc()))
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for scan, doc in rows:
        all_hits = json.loads(scan.hits_json or "[]")
        # Hits judged non-violating by semantic review do not enter the ledger summary; they are only listed as "exempted" in details
        hits = [h for h in all_hits if not h.get("suppressed")]
        n_exempted = len(all_hits) - len(hits)
        chain_label, chain_tone = _DOC_STATUS_CHAIN.get(doc.status, ("—", "plain"))
        items.append({
            "scan_id": scan.id,
            "doc_id": doc.id,
            "scanned_at": scan.scanned_at.isoformat() if scan.scanned_at else None,
            "hook": scan.hook,
            "title": doc.title or "(未命名稿件)",
            "channel": doc.platform or "",
            "doc_status": doc.status,
            "verdict": scan.verdict,
            "ban_count": scan.ban_count, "warn_count": scan.warn_count,
            "evid_count": scan.evid_count,
            "missing_disclaimers": [m.get("label") for m in
                                    json.loads(scan.missing_disclaimers_json or "[]")],
            "hits_brief": [{"term": h.get("term"), "level": h.get("level")} for h in hits[:8]],
            "exempted_count": n_exempted,
            "chain_label": chain_label, "chain_tone": chain_tone,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/scans/{scan_id}")
def get_scan_detail(
    scan_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scan = db.get(ComplianceScanResultORM, scan_id)
    if not scan:
        raise HTTPException(404, "scan not found")
    _topic_or_404(db, scan.topic_id, current_user, ACTION_READ)
    doc = db.get(TopicGeneratedDocORM, scan.doc_id)
    all_hits = json.loads(scan.hits_json or "[]")
    trace = (db.query(ComplianceAuditLogORM)
               .filter(ComplianceAuditLogORM.topic_id == scan.topic_id)
               .filter(ComplianceAuditLogORM.object_type == "doc")
               .filter(ComplianceAuditLogORM.object_id == scan.doc_id)
               .order_by(ComplianceAuditLogORM.created_at.asc())
               .limit(20).all())
    return {
        "scan_id": scan.id,
        "doc_id": scan.doc_id,
        "scanned_at": scan.scanned_at.isoformat() if scan.scanned_at else None,
        "hook": scan.hook,
        "verdict": scan.verdict,
        "lexicon_versions": json.loads(scan.lexicon_versions_json or "{}"),
        # hits = hits still counted as violations (frontend highlight + basis for judgment);
        # exempted = hits judged non-violating by semantic review and excluded entirely (shown as "exempted", kept for audit)
        "hits": [h for h in all_hits if not h.get("suppressed")],
        "exempted": [h for h in all_hits if h.get("suppressed")],
        "missing_disclaimers": json.loads(scan.missing_disclaimers_json or "[]"),
        "external": json.loads(scan.external_json or "{}"),
        "doc": {
            "id": doc.id if doc else scan.doc_id,
            "title": (doc.title if doc else "") or "",
            "body_markdown": (doc.body_markdown if doc else "") or "",
            "status": doc.status if doc else "",
            "reject_reason": (doc.reject_reason if doc else "") or "",
            "platform": (doc.platform if doc else "") or "",
        },
        "trace": [
            {
                "at": r.created_at.isoformat() if r.created_at else None,
                "event_type": r.event_type,
                "actor_type": r.actor_type, "actor_name": r.actor_name,
                "detail": json.loads(r.detail_json or "{}"),
            }
            for r in trace
        ],
    }


# ── In-process monitoring: alerts and remediation tickets (P1) ──────────────────────────────────────────

_TICKET_ACTIONS = {"limit_fix", "takedown", "suspend"}


def _alert_out(a: ComplianceAlertORM, ticket: Optional[ComplianceTicketORM]) -> dict:
    try:
        evidence = json.loads(a.evidence_json or "{}")
    except Exception:  # noqa: BLE001
        evidence = {}
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "level": a.level,
        "detected_by": a.detected_by,
        "confidence": a.confidence,
        "status": a.status,
        "quote": a.quote,
        "reason": a.reason,
        "channel": a.channel,
        "engine": a.engine,
        "query": a.query,
        "found_at": a.found_at.isoformat() if a.found_at else None,
        "source_url": evidence.get("source_url") or "",
        "answer_excerpt": evidence.get("answer_excerpt") or "",
        "recurrence_count": len(evidence.get("recurrences") or []),
        "handled_by_name": a.handled_by_name or "",
        "ticket": (
            {
                "id": ticket.id, "ticket_no": ticket.ticket_no,
                "action": ticket.action, "status": ticket.status,
                "deadline_at": ticket.deadline_at.isoformat() if ticket.deadline_at else None,
                "clean_streak": ticket.clean_streak,
                "retest_count": len(json.loads(ticket.retest_json or "[]")),
                "resolution": ticket.resolution or "",
            } if ticket else None
        ),
    }


@router.get("/topics/{topic_id}/alerts")
def list_alerts(
    topic_id: int,
    status: Optional[str] = Query(None),
    level: Optional[int] = Query(None, ge=1, le=3),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    q = db.query(ComplianceAlertORM).filter(ComplianceAlertORM.topic_id == t.id)
    if status:
        q = q.filter(ComplianceAlertORM.status == status)
    if level:
        q = q.filter(ComplianceAlertORM.level == level)
    q = q.order_by(ComplianceAlertORM.found_at.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    tickets = {}
    ticket_ids = [a.ticket_id for a in rows if a.ticket_id]
    if ticket_ids:
        for tk in db.query(ComplianceTicketORM).filter(
                ComplianceTicketORM.id.in_(ticket_ids)).all():
            tickets[tk.id] = tk
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_alert_out(a, tickets.get(a.ticket_id)) for a in rows]}


def _next_ticket_no(db: Session) -> str:
    """Display number CZ-MMDD-NN (daily sequence); a unique index is the fallback, conflict retries are triggered by the caller's flush."""
    prefix = "CZ-" + datetime.utcnow().strftime("%m%d") + "-"
    n_today = (db.query(func.count(ComplianceTicketORM.id))
                 .filter(ComplianceTicketORM.ticket_no.like(prefix + "%")).scalar() or 0)
    return f"{prefix}{n_today + 1:02d}"


class ConfirmAlertPayload(BaseModel):
    action: str                        # limit_fix / takedown / suspend
    assignee_name: str = ""
    note: str = ""


@router.post("/alerts/{alert_id}/confirm")
def confirm_alert(
    alert_id: int,
    payload: ConfirmAlertPayload,
    background: BackgroundTasks,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirm a violation and open a remediation ticket (owner / admin). Deadline by level: L3=24h L2=72h L1=7d."""
    from geo.services.compliance_monitor import DEADLINE_HOURS, notify_ticket_opened

    a = db.get(ComplianceAlertORM, alert_id)
    if not a:
        raise HTTPException(404, "alert not found")
    _topic_or_404(db, a.topic_id, current_user)
    if a.status != "open":
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"告警状态为 {a.status},仅待处理告警可确认"})
    if payload.action not in _TICKET_ACTIONS:
        raise HTTPException(422, "无效的处置动作")

    now = datetime.utcnow()
    ticket = ComplianceTicketORM(
        ticket_no=_next_ticket_no(db),
        alert_id=a.id, topic_id=a.topic_id,
        action=payload.action, level=a.level,
        deadline_at=now + timedelta(hours=DEADLINE_HOURS.get(a.level, 168)),
        assignee_id=current_user.id,
        assignee_name=payload.assignee_name or getattr(current_user, "email", "") or "",
        status="open", opened_at=now, last_action_at=now,
    )
    db.add(ticket)
    db.flush()
    a.status = "confirmed"
    a.ticket_id = ticket.id
    a.confirmed_at = now
    a.handled_by_id = current_user.id
    a.handled_by_name = getattr(current_user, "email", "") or ""
    actor_type = _user_role(current_user)
    compliance_audit.record(
        db, topic_id=a.topic_id, event_type="ticket_open", object_type="ticket",
        object_id=ticket.id, actor_type=actor_type, actor_id=current_user.id,
        actor_name=a.handled_by_name,
        detail={"ticket_no": ticket.ticket_no, "action": payload.action,
                "level": a.level, "alert_id": a.id, "note": payload.note[:200],
                "deadline_at": ticket.deadline_at.isoformat()},
    )
    db.commit()
    db.refresh(ticket)
    background.add_task(notify_ticket_opened, ticket.id)
    return _alert_out(a, ticket)


class DismissAlertPayload(BaseModel):
    reason: str = ""


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(
    alert_id: int,
    payload: DismissAlertPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark as false positive (owner / admin), terminal state."""
    a = db.get(ComplianceAlertORM, alert_id)
    if not a:
        raise HTTPException(404, "alert not found")
    _topic_or_404(db, a.topic_id, current_user)
    if a.status != "open":
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"告警状态为 {a.status},仅待处理告警可判误报"})
    a.status = "dismissed"
    a.closed_at = datetime.utcnow()
    a.handled_by_id = current_user.id
    a.handled_by_name = getattr(current_user, "email", "") or ""
    compliance_audit.record(
        db, topic_id=a.topic_id, event_type="alert", object_type="alert",
        object_id=a.id,
        actor_type=_user_role(current_user),
        actor_id=current_user.id, actor_name=a.handled_by_name,
        detail={"action": "dismissed", "reason": (payload.reason or "")[:300]},
    )
    db.commit()
    return _alert_out(a, None)


@router.get("/topics/{topic_id}/tickets")
def list_tickets(
    topic_id: int,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    q = db.query(ComplianceTicketORM).filter(ComplianceTicketORM.topic_id == t.id)
    if status:
        q = q.filter(ComplianceTicketORM.status == status)
    q = q.order_by(ComplianceTicketORM.opened_at.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    alerts: dict[int, ComplianceAlertORM] = {}
    if rows:
        for a in db.query(ComplianceAlertORM).filter(
                ComplianceAlertORM.id.in_([r.alert_id for r in rows])).all():
            alerts[a.id] = a
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                "id": tk.id, "ticket_no": tk.ticket_no, "action": tk.action,
                "level": tk.level, "status": tk.status,
                "deadline_at": tk.deadline_at.isoformat() if tk.deadline_at else None,
                "assignee_name": tk.assignee_name, "resolution": tk.resolution,
                "clean_streak": tk.clean_streak,
                "opened_at": tk.opened_at.isoformat() if tk.opened_at else None,
                "closed_at": tk.closed_at.isoformat() if tk.closed_at else None,
                "alert": _alert_out(alerts[tk.alert_id], None) if tk.alert_id in alerts else None,
            }
            for tk in rows
        ],
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tk = db.get(ComplianceTicketORM, ticket_id)
    if not tk:
        raise HTTPException(404, "ticket not found")
    _topic_or_404(db, tk.topic_id, current_user, ACTION_READ)
    a = db.get(ComplianceAlertORM, tk.alert_id)
    trace = (db.query(ComplianceAuditLogORM)
               .filter(ComplianceAuditLogORM.object_type == "ticket")
               .filter(ComplianceAuditLogORM.object_id == tk.id)
               .order_by(ComplianceAuditLogORM.created_at.asc()).limit(50).all())
    return {
        "id": tk.id, "ticket_no": tk.ticket_no, "action": tk.action, "level": tk.level,
        "status": tk.status,
        "deadline_at": tk.deadline_at.isoformat() if tk.deadline_at else None,
        "assignee_name": tk.assignee_name, "resolution": tk.resolution,
        "clean_streak": tk.clean_streak,
        "retests": json.loads(tk.retest_json or "[]"),
        "opened_at": tk.opened_at.isoformat() if tk.opened_at else None,
        "last_action_at": tk.last_action_at.isoformat() if tk.last_action_at else None,
        "closed_at": tk.closed_at.isoformat() if tk.closed_at else None,
        "alert": _alert_out(a, None) if a else None,
        "trace": [
            {"at": r.created_at.isoformat() if r.created_at else None,
             "event_type": r.event_type, "actor_type": r.actor_type,
             "actor_name": r.actor_name, "detail": json.loads(r.detail_json or "{}")}
            for r in trace
        ],
    }


class TicketActionPayload(BaseModel):
    mark: str                          # start / done / extend_deadline
    note: str = ""
    new_deadline_at: Optional[str] = None   # Used by extend_deadline, ISO format


@router.post("/tickets/{ticket_id}/action")
def ticket_action(
    ticket_id: int,
    payload: TicketActionPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remediation action: start=begin remediation / done=remediated pending retest / extend_deadline=extend (admin only)."""
    tk = db.get(ComplianceTicketORM, ticket_id)
    if not tk:
        raise HTTPException(404, "ticket not found")
    _topic_or_404(db, tk.topic_id, current_user)
    if tk.status == "closed":
        raise HTTPException(409, {"code": "WRONG_STATUS", "message": "处置单已闭环"})

    now = datetime.utcnow()
    if payload.mark == "start":
        tk.status = "in_progress"
    elif payload.mark == "done":
        tk.status = "pending_retest"
        if payload.note:
            tk.resolution = ((tk.resolution + "\n") if tk.resolution else "") + payload.note[:500]
    elif payload.mark == "extend_deadline":
        if not is_staff(current_user):
            raise HTTPException(403, "延期仅限工作台人员")
        if not payload.new_deadline_at:
            raise HTTPException(422, "缺少新时限")
        try:
            tk.deadline_at = datetime.fromisoformat(payload.new_deadline_at)
        except ValueError:
            raise HTTPException(422, "时限格式应为 ISO 日期时间")
        if tk.status == "overdue":
            tk.status = "in_progress"
        tk.overdue_notified = False
    else:
        raise HTTPException(422, "无效的处置动作")
    tk.last_action_at = now
    compliance_audit.record(
        db, topic_id=tk.topic_id, event_type="ticket_action", object_type="ticket",
        object_id=tk.id,
        actor_type=_user_role(current_user),
        actor_id=current_user.id,
        actor_name=getattr(current_user, "email", "") or "",
        detail={"ticket_no": tk.ticket_no, "mark": payload.mark,
                "note": payload.note[:300], "status_after": tk.status},
    )
    db.commit()
    return {"ok": True, "id": tk.id, "status": tk.status,
            "deadline_at": tk.deadline_at.isoformat() if tk.deadline_at else None}


# ── Audit log timeline ───────────────────────────────────────────────────────

@router.get("/topics/{topic_id}/timeline")
def get_timeline(
    topic_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _topic_or_404(db, topic_id, current_user, ACTION_READ)
    q = (db.query(ComplianceAuditLogORM)
           .filter(ComplianceAuditLogORM.topic_id == t.id)
           .order_by(ComplianceAuditLogORM.created_at.desc()))
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                "id": r.id,
                "log_id": f"LG-{r.id:05d}",
                "at": r.created_at.isoformat() if r.created_at else None,
                "event_type": r.event_type,
                "object_type": r.object_type, "object_id": r.object_id,
                "actor_type": r.actor_type, "actor_name": r.actor_name,
                "detail": json.loads(r.detail_json or "{}"),
            }
            for r in rows
        ],
    }


# ── Admin: lexicons ─────────────────────────────────────────────────────────

@admin_router.get("/lexicons")
def admin_list_lexicons(
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    lexicons = db.query(ComplianceLexiconORM).order_by(ComplianceLexiconORM.id.asc()).all()
    counts = dict(
        db.query(ComplianceLexiconTermORM.lexicon_id, func.count(ComplianceLexiconTermORM.id))
          .filter(ComplianceLexiconTermORM.active.is_(True))
          .group_by(ComplianceLexiconTermORM.lexicon_id).all()
    )
    return [
        {
            "id": l.id, "code": l.code, "name": l.name, "tier": l.tier,
            "version": l.version, "enabled": l.enabled,
            "term_count": int(counts.get(l.id, 0)),
            "rules_text": l.rules_text,
            "updated_at": l.updated_at.isoformat() if l.updated_at else None,
        }
        for l in lexicons
    ]


@admin_router.get("/lexicons/{lexicon_id}/terms")
def admin_list_terms(
    lexicon_id: int,
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (db.query(ComplianceLexiconTermORM)
              .filter(ComplianceLexiconTermORM.lexicon_id == lexicon_id)
              .filter(ComplianceLexiconTermORM.active.is_(True))
              .order_by(ComplianceLexiconTermORM.id.asc()).all())
    return [
        {"id": r.id, "term": r.term, "level": r.level, "category": r.category,
         "fix": r.fix, "aliases": json.loads(r.aliases_json or "[]"),
         "status": r.status, "source": r.source, "origin_note": r.origin_note}
        for r in rows
    ]


# ── User side: lexicon view (clicked from the compliance page profile bar) ───────────────────────────────────

@router.get("/lexicons/{lexicon_id}/terms")
def list_lexicon_terms(
    lexicon_id: int,
    _user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read-only view of entries in enabled lexicons — lexicons are the platform-level compliance basis, viewable by logged-in users."""
    lex = db.get(ComplianceLexiconORM, lexicon_id)
    if not lex or not lex.enabled:
        raise HTTPException(404, "lexicon not found")
    rows = (db.query(ComplianceLexiconTermORM)
              .filter(ComplianceLexiconTermORM.lexicon_id == lex.id)
              .filter(ComplianceLexiconTermORM.active.is_(True))
              .filter(ComplianceLexiconTermORM.status == "active")
              .order_by(ComplianceLexiconTermORM.category.asc(),
                        ComplianceLexiconTermORM.id.asc()).all())
    return {
        "lexicon": {"id": lex.id, "code": lex.code, "name": lex.name,
                    "tier": lex.tier, "version": lex.version,
                    "rules_text": lex.rules_text, "term_count": len(rows)},
        "items": [
            {"term": r.term, "level": r.level, "category": r.category,
             "fix": r.fix, "aliases": json.loads(r.aliases_json or "[]")}
            for r in rows
        ],
    }


# ── Admin: entry candidate pool (P2, design doc 5.7 one-way feedback) ────────────────────────

@admin_router.get("/terms")
def admin_list_terms_by_status(
    status: str = Query("pending"),
    _admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (db.query(ComplianceLexiconTermORM, ComplianceLexiconORM.code)
              .join(ComplianceLexiconORM,
                    ComplianceLexiconORM.id == ComplianceLexiconTermORM.lexicon_id)
              .filter(ComplianceLexiconTermORM.status == status)
              .order_by(ComplianceLexiconTermORM.id.desc())
              .limit(500).all())
    return [
        {"id": r.id, "term": r.term, "level": r.level, "category": r.category,
         "fix": r.fix, "lexicon_code": code, "source": r.source,
         "origin_note": r.origin_note}
        for r, code in rows
    ]


class TermCreatePayload(BaseModel):
    term: str
    level: str = "warn"                 # ban / warn / evid
    category: str = ""
    fix: str = ""
    aliases: list[str] = []
    lexicon_code: str = ""              # Empty = goes into the candidate pool
    status: str = "pending"             # Manually created entries may pass active


class TermAdoptPayload(BaseModel):
    lexicon_id: int                     # Lexicon to adopt into
    level: str                          # Level set by admin (external entries do not go straight to ban; a human decides)
    category: str = ""
    fix: str = ""


def _bump_lexicon_version(lex: ComplianceLexiconORM) -> None:
    """Entry set changed → version gets a date stamp; updated_at invalidates the scan cache, triggering a rescan of existing content."""
    lex.version = datetime.utcnow().strftime("%Y.%m.%d")
    lex.updated_at = datetime.utcnow()


@admin_router.post("/terms")
def admin_create_term(
    payload: TermCreatePayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from geo.services import compliance_scanner
    term = payload.term.strip()
    if not term:
        raise HTTPException(422, "term required")
    if payload.level not in ("ban", "warn", "evid"):
        raise HTTPException(422, "level must be ban/warn/evid")
    if payload.lexicon_code:
        lex = (db.query(ComplianceLexiconORM)
                 .filter(ComplianceLexiconORM.code == payload.lexicon_code).first())
        if not lex:
            raise HTTPException(404, "lexicon not found")
    else:
        lex = compliance_scanner._candidate_lexicon(db)   # noqa: SLF001
    status = payload.status if payload.status in ("pending", "active") else "pending"
    row = ComplianceLexiconTermORM(
        lexicon_id=lex.id, term=term, level=payload.level,
        category=payload.category, fix=payload.fix,
        aliases_json=json.dumps(payload.aliases, ensure_ascii=False),
        status=status, source="manual",
        origin_note=f"管理员 {getattr(admin, 'email', '') or ''} 手工添加"[:256],
    )
    db.add(row)
    if status == "active":
        _bump_lexicon_version(lex)
    compliance_audit.record(
        db, topic_id=0, event_type="lexicon_upgrade", object_type="lexicon",
        object_id=lex.id, actor_type="admin", actor_id=admin.id,
        actor_name=getattr(admin, "email", "") or "",
        detail={"action": "term_created", "term": term, "status": status,
                "lexicon": lex.code},
    )
    db.commit()
    return {"ok": True, "id": row.id, "status": row.status, "lexicon_code": lex.code}


@admin_router.post("/terms/{term_id}/adopt")
def admin_adopt_term(
    term_id: int,
    payload: TermAdoptPayload,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Adopt a candidate: set level + category + move into the target lexicon; version bump triggers a rescan of existing content."""
    row = db.get(ComplianceLexiconTermORM, term_id)
    if not row or row.status != "pending":
        raise HTTPException(404, "pending term not found")
    if payload.level not in ("ban", "warn", "evid"):
        raise HTTPException(422, "level must be ban/warn/evid")
    lex = db.get(ComplianceLexiconORM, payload.lexicon_id)
    if not lex:
        raise HTTPException(404, "target lexicon not found")
    row.lexicon_id = lex.id
    row.level = payload.level
    row.category = payload.category or row.category
    row.fix = payload.fix or row.fix
    row.status = "active"
    _bump_lexicon_version(lex)
    compliance_audit.record(
        db, topic_id=0, event_type="lexicon_upgrade", object_type="lexicon",
        object_id=lex.id, actor_type="admin", actor_id=admin.id,
        actor_name=getattr(admin, "email", "") or "",
        detail={"action": "term_adopted", "term": row.term, "level": row.level,
                "lexicon": lex.code, "source": row.source},
    )
    db.commit()
    return {"ok": True, "id": row.id, "lexicon_code": lex.code,
            "version": lex.version}


@admin_router.post("/terms/{term_id}/reject")
def admin_reject_term(
    term_id: int,
    admin = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reject a candidate: kept as rejected so external checks do not keep recommending the same term."""
    row = db.get(ComplianceLexiconTermORM, term_id)
    if not row or row.status != "pending":
        raise HTTPException(404, "pending term not found")
    row.status = "rejected"
    compliance_audit.record(
        db, topic_id=0, event_type="lexicon_upgrade", object_type="lexicon",
        object_id=row.lexicon_id, actor_type="admin", actor_id=admin.id,
        actor_name=getattr(admin, "email", "") or "",
        detail={"action": "term_rejected", "term": row.term, "source": row.source},
    )
    db.commit()
    return {"ok": True, "id": row.id}
