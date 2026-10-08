"""Report/complaint API: public reporting entry + admin handling closed loop.

Algorithm filing remediation list item 13 (Interim Measures for the Management of Generative AI Services, Article 15:
a reporting mechanism must be established, with timely acceptance, handling and feedback).
Design in `docs/09-软著与专利/备案补正/02-方案与代办台账.md` §4.6.

Public endpoints (**no login required**: reporters are mostly not our users, and a login wall would make this entry useless):
- POST /report                       - submit a report, returns a ticket number
- GET  /report/meta                  - report type dictionary + public email + promised response time (for rendering the frontend form)
- GET  /report/{report_no}           - reporter checks progress with ticket number + email (the user side of the feedback loop)

Admin endpoints (require_workbench):
- GET  /admin/reports                - ticket list (filter by status)
- GET  /admin/reports/{id}           - single ticket details
- POST /admin/reports/{id}/accept    - accept -> processing
- POST /admin/reports/{id}/resolve   - handling done -> resolved, **reply required**
- POST /admin/reports/{id}/reject    - not accepted -> rejected, **reason required**
- GET  /admin/reports/{id}/attachment- download report attachment

Both terminal states require a reply: without a reply to the reporter it is not a closed loop; the list requires all four steps
"report -> accept -> handle -> feedback", and a screenshot missing one step will not pass.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import require_workbench
from geo.models.compliance import AbuseReportORM
from geo.services import content_marking
from geo.services.email_service import email_service

log = logging.getLogger(__name__)

router = APIRouter()

# Public reporting email: the page, receipt emails and user agreement all reference the same value; do not write separate copies.
# Use <abuse-email> rather than a new dedicated abuse@ address: this mailbox has long been published in the footer
# and is known to be read. Publishing an address not yet set up means the reviewer gets a bounce on the first try, worse than not publishing.
ABUSE_EMAIL = os.environ.get("GEO_ABUSE_EMAIL", "<abuse-email>").strip()
# Promised handling time (calendar days). The page publishes this number, and the admin side computes timeouts from it
SLA_DAYS = int(os.environ.get("GEO_ABUSE_SLA_DAYS", "7"))

# Report types. The first four map to the illegal/non-compliant scenarios the Interim Measures care about; the last two align with the reviewer's example image
# ("用户体验问题 / 模型问题 / 其他问题" ("user experience issue / model issue / other issue") in 03-清单示例图/13-举报投诉入口-示例.png)
CATEGORIES: list[dict] = [
    {"key": "content_illegal", "label": "违法违规内容"},
    {"key": "infringement",    "label": "侵权(著作权 / 商标 / 肖像)"},
    {"key": "privacy",         "label": "个人信息与隐私"},
    {"key": "false_info",      "label": "虚假或误导信息"},
    {"key": "model",           "label": "模型问题(答非所问 / 有害输出)"},
    {"key": "experience",      "label": "用户体验问题"},
    {"key": "other",           "label": "其他"},
]
_CATEGORY_KEYS = {c["key"] for c in CATEGORIES}
_CATEGORY_LABELS = {c["key"]: c["label"] for c in CATEGORIES}

STATUS_LABELS = {
    "received":   "已受理",
    "processing": "处理中",
    "resolved":   "已处置",
    "rejected":   "不予受理",
}

# Attachment limits: this is an **anonymous** upload endpoint, and without hard limits it becomes a free file host
_MAX_ATTACH_BYTES = int(os.environ.get("GEO_ABUSE_MAX_ATTACH_MB", "5")) * 1024 * 1024
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}

# Per-IP submission rate gate: at most 5 within 10 minutes. The reporting entry must be open anonymously,
# so it will certainly be spammed by scripts; without a block the admin ticket list would be flooded and real reports drowned out
_RATE_WINDOW_MIN = 10
_RATE_MAX = 5

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _attach_root() -> Path:
    root = Path(os.environ.get("GEO_DATA_DIR") or "./data") / "abuse_reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_report_no(report_id: int, created_at: Optional[datetime] = None) -> str:
    """`VGR-<entity code>-<yyyymmdd>-<id six digits>`, the same word-formation as content numbers (VGL/VGI)."""
    base = created_at or datetime.utcnow()
    day = (base + timedelta(hours=8)).strftime("%Y%m%d")
    return f"VGR-{content_marking.SUBJECT_CODE}-{day}-{int(report_id):06d}"


# ─────────────────────────── schema ───────────────────────────
class ReportMetaOut(BaseModel):
    categories: list[dict]
    abuse_email: str
    sla_days: int
    max_attach_mb: int
    allowed_ext: list[str]


class ReportSubmitOut(BaseModel):
    report_no: str
    status: str
    status_label: str
    due_at: Optional[datetime] = None
    message: str


class ReportPublicOut(BaseModel):
    """Progress shown to the reporter: only returns what they filled in + the handling conclusion, not internal handling records."""
    report_no: str
    category: str
    category_label: str
    status: str
    status_label: str
    created_at: datetime
    due_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    reply: str = ""


class ReportAdminOut(BaseModel):
    id: int
    report_no: str
    category: str
    category_label: str
    target_url: str = ""
    target_trace_no: str = ""
    description: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    has_attachment: bool = False
    attachment_name: str = ""
    reporter_user_id: Optional[int] = None
    client_ip: str = ""
    status: str
    status_label: str
    created_at: datetime
    due_at: Optional[datetime] = None
    overdue: bool = False
    handling: str = ""
    reply: str = ""
    handler_name: str = ""
    accepted_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class ReportListOut(BaseModel):
    total: int
    open_count: int       # Not in a terminal state (received + processing)
    overdue_count: int
    items: list[ReportAdminOut]


class HandlePayload(BaseModel):
    handling: str = Field("", max_length=4000)
    reply: str = Field("", max_length=4000)


def _public_out(r: AbuseReportORM) -> ReportPublicOut:
    return ReportPublicOut(
        report_no=r.report_no, category=r.category,
        category_label=_CATEGORY_LABELS.get(r.category, r.category),
        status=r.status, status_label=STATUS_LABELS.get(r.status, r.status),
        created_at=r.created_at, due_at=r.due_at, resolved_at=r.resolved_at,
        reply=r.reply or "",
    )


def _admin_out(r: AbuseReportORM) -> ReportAdminOut:
    open_ = r.status in ("received", "processing")
    return ReportAdminOut(
        id=r.id, report_no=r.report_no, category=r.category,
        category_label=_CATEGORY_LABELS.get(r.category, r.category),
        target_url=r.target_url or "", target_trace_no=r.target_trace_no or "",
        description=r.description or "",
        contact_name=r.contact_name or "", contact_email=r.contact_email or "",
        contact_phone=r.contact_phone or "",
        has_attachment=bool(r.attachment_path),
        attachment_name=r.attachment_name or "",
        reporter_user_id=r.reporter_user_id, client_ip=r.client_ip or "",
        status=r.status, status_label=STATUS_LABELS.get(r.status, r.status),
        created_at=r.created_at, due_at=r.due_at,
        overdue=bool(open_ and r.due_at and r.due_at < datetime.utcnow()),
        handling=r.handling or "", reply=r.reply or "",
        handler_name=r.handler_name or "",
        accepted_at=r.accepted_at, resolved_at=r.resolved_at,
    )


# ─────────────────────────── Public endpoints ───────────────────────────
@router.get("/report/meta", response_model=ReportMetaOut)
def report_meta():
    """Form dictionary: types, public email and promised response time are all delivered by the backend, not hard-coded in the frontend."""
    return ReportMetaOut(
        categories=CATEGORIES, abuse_email=ABUSE_EMAIL, sla_days=SLA_DAYS,
        max_attach_mb=_MAX_ATTACH_BYTES // 1024 // 1024,
        allowed_ext=sorted(_ALLOWED_EXT),
    )


@router.post("/report", response_model=ReportSubmitOut, status_code=201)
async def submit_report(
    request: Request,
    category: str = Form("other"),
    description: str = Form(...),
    target_url: str = Form(""),
    target_trace_no: str = Form(""),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    attachment: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Submit a report: usable anonymously. Uses multipart because attachments are accepted.

    Contact info is not mandatory: named reports can get a reply, and anonymous reports must be possible too. But without an email no feedback is possible,
    and the page explains this clearly.
    """
    desc = (description or "").strip()
    if len(desc) < 5:
        raise HTTPException(422, {"code": "DESC_TOO_SHORT",
                                  "message": "请简要描述问题(至少 5 个字)"})
    if len(desc) > 5000:
        desc = desc[:5000]
    if category not in _CATEGORY_KEYS:
        category = "other"
    email = (contact_email or "").strip()
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(422, {"code": "BAD_EMAIL", "message": "联系邮箱格式不正确"})

    ip = content_marking.client_ip(request)
    _check_rate(db, ip)

    saved_path, saved_name = await _save_attachment(attachment)

    now = datetime.utcnow()
    row = AbuseReportORM(
        report_no="",             # Backfilled after getting the primary key
        category=category,
        target_url=(target_url or "").strip()[:1024],
        target_trace_no=(target_trace_no or "").strip().upper()[:48],
        description=desc,
        contact_name=(contact_name or "").strip()[:64],
        contact_email=email[:128],
        contact_phone=(contact_phone or "").strip()[:32],
        attachment_path=saved_path, attachment_name=saved_name,
        reporter_user_id=_maybe_user_id(request),
        client_ip=ip,
        status="received",
        due_at=now + timedelta(days=SLA_DAYS),
        created_at=now, updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    row.report_no = make_report_no(row.id, row.created_at)
    db.commit()

    _notify_submitted(row)
    log.info("abuse report received: %s cat=%s ip=%s", row.report_no, category, ip)
    return ReportSubmitOut(
        report_no=row.report_no, status=row.status,
        status_label=STATUS_LABELS[row.status], due_at=row.due_at,
        message=(f"举报已受理,工单号 {row.report_no}。我们将在 {SLA_DAYS} 个自然日内处理并反馈。"
                 + ("" if email else "(你未留联系邮箱,处理结果只能凭工单号自行查询)")),
    )


@router.get("/report/{report_no}", response_model=ReportPublicOut)
def query_report(
    report_no: str,
    email: str = "",
    db: Session = Depends(get_db),
):
    """Reporter checks progress. If an email was left, the same email must be used to verify: the ticket number appears in the receipt email,
    and allowing lookup by number alone would let whoever finds a number read someone else's report."""
    r = (db.query(AbuseReportORM)
           .filter(AbuseReportORM.report_no == (report_no or "").strip().upper())
           .first())
    if not r:
        raise HTTPException(404, {"code": "REPORT_NOT_FOUND", "message": "工单号不存在"})
    if r.contact_email and (email or "").strip().lower() != r.contact_email.lower():
        raise HTTPException(403, {"code": "EMAIL_MISMATCH",
                                  "message": "请填写提交时使用的联系邮箱"})
    return _public_out(r)


# ─────────────────────────── Admin endpoints ───────────────────────────
@router.get("/admin/reports", response_model=ReportListOut)
def list_reports(
    status: str = "",
    limit: int = 100,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Ticket list. Defaults to everything in descending order; non-terminal ones are surfaced by filtering on status, no fancy sorting."""
    limit = max(1, min(500, int(limit or 100)))
    base = db.query(AbuseReportORM)
    if status and status in STATUS_LABELS:
        base = base.filter(AbuseReportORM.status == status)
    total = base.count()
    now = datetime.utcnow()
    open_q = db.query(AbuseReportORM).filter(
        AbuseReportORM.status.in_(("received", "processing")))
    rows = base.order_by(AbuseReportORM.created_at.desc()).limit(limit).all()
    return ReportListOut(
        total=total,
        open_count=open_q.count(),
        overdue_count=open_q.filter(AbuseReportORM.due_at < now).count(),
        items=[_admin_out(r) for r in rows],
    )


@router.get("/admin/reports/{report_id}", response_model=ReportAdminOut)
def get_report(
    report_id: int,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    r = db.get(AbuseReportORM, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    return _admin_out(r)


@router.get("/admin/reports/{report_id}/attachment")
def get_attachment(
    report_id: int,
    _admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    r = db.get(AbuseReportORM, report_id)
    if not r or not r.attachment_path:
        raise HTTPException(404, "attachment not found")
    p = _attach_root() / r.attachment_path
    if not p.is_file():
        raise HTTPException(404, "attachment file missing")
    return FileResponse(str(p), filename=r.attachment_name or p.name)


@router.post("/admin/reports/{report_id}/accept", response_model=ReportAdminOut)
def accept_report(
    report_id: int,
    admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Accept: received -> processing, recording the handler."""
    r = _open_or_404(db, report_id)
    if r.status != "received":
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": f"工单当前 {STATUS_LABELS.get(r.status)},不能重复受理"})
    r.status = "processing"
    r.accepted_at = datetime.utcnow()
    r.handler_id = admin.id
    r.handler_name = getattr(admin, "email", "") or ""
    db.commit()
    db.refresh(r)
    return _admin_out(r)


@router.post("/admin/reports/{report_id}/resolve", response_model=ReportAdminOut)
def resolve_report(
    report_id: int,
    payload: HandlePayload,
    admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Handling done -> resolved. **A reply is required**: without feedback there is no closed loop."""
    return _close(db, report_id, admin, payload, status="resolved")


@router.post("/admin/reports/{report_id}/reject", response_model=ReportAdminOut)
def reject_report(
    report_id: int,
    payload: HandlePayload,
    admin=Depends(require_workbench),
    db: Session = Depends(get_db),
):
    """Not accepted -> rejected. A reason is required too: rejection is also a form of feedback."""
    return _close(db, report_id, admin, payload, status="rejected")


# ─────────────────────────── Internal ───────────────────────────
def _open_or_404(db: Session, report_id: int) -> AbuseReportORM:
    r = db.get(AbuseReportORM, report_id)
    if not r:
        raise HTTPException(404, "report not found")
    return r


def _close(db: Session, report_id: int, admin, payload: HandlePayload,
           *, status: str) -> ReportAdminOut:
    r = _open_or_404(db, report_id)
    if r.status in ("resolved", "rejected"):
        raise HTTPException(409, {"code": "WRONG_STATUS",
                                  "message": "工单已终态,不能重复处置"})
    reply = (payload.reply or "").strip()
    if not reply:
        raise HTTPException(422, {
            "code": "REPLY_REQUIRED",
            "message": "必须填写给举报人的答复 —— 举报处置要求「受理→处置→反馈」闭环,缺答复不算办结",
        })
    r.status = status
    r.handling = (payload.handling or "").strip()
    r.reply = reply
    r.resolved_at = datetime.utcnow()
    r.handler_id = admin.id
    r.handler_name = getattr(admin, "email", "") or ""
    if r.accepted_at is None:
        r.accepted_at = r.resolved_at
    db.commit()
    db.refresh(r)
    _notify_closed(r)
    return _admin_out(r)


def _maybe_user_id(request: Request) -> Optional[int]:
    """Record user_id along the way when logged in; if unavailable, never mind: this endpoint allows anonymity by design.

    Does not use `Depends(get_current_user)`: that would 401 unauthenticated requests and break the whole reporting entry.
    The JWT sub is the username, not the id, so the DB must be queried again after decoding.
    """
    try:
        auth = (request.headers.get("authorization") or "")
        if not auth.lower().startswith("bearer "):
            return None
        import jwt

        from geo.api.auth import ALGORITHM, SECRET_KEY
        from geo.services.user_service import user_service

        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET_KEY,
                             algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        u = user_service.get_user_by_username(username)
        return getattr(u, "id", None)
    except Exception:  # noqa: BLE001 - anonymous / expired tokens all land here; staying silent is fine
        return None


def _check_rate(db: Session, ip: str) -> None:
    if not ip:
        return
    since = datetime.utcnow() - timedelta(minutes=_RATE_WINDOW_MIN)
    n = (db.query(AbuseReportORM)
           .filter(AbuseReportORM.client_ip == ip)
           .filter(AbuseReportORM.created_at >= since)
           .count())
    if n >= _RATE_MAX:
        raise HTTPException(429, {
            "code": "TOO_MANY_REPORTS",
            "message": f"提交过于频繁,请 {_RATE_WINDOW_MIN} 分钟后再试;"
                       f"紧急情况可直接邮件 {ABUSE_EMAIL}",
        })


async def _save_attachment(f: Optional[UploadFile]) -> tuple[str, str]:
    """Save the attachment, returning (relative path, original filename). None given or non-compliant -> ('', '').

    The anonymous endpoint only accepts images and PDFs, renamed by extension when written to disk: writing to disk with the user-given filename
    would accept path traversal and executable suffixes along with it.
    """
    if f is None or not (f.filename or "").strip():
        return "", ""
    ext = Path(f.filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(422, {
            "code": "BAD_ATTACHMENT",
            "message": f"附件只支持 {'/'.join(sorted(e.lstrip('.') for e in _ALLOWED_EXT))}",
        })
    raw = await f.read()
    if len(raw) > _MAX_ATTACH_BYTES:
        raise HTTPException(413, {
            "code": "ATTACHMENT_TOO_LARGE",
            "message": f"附件不能超过 {_MAX_ATTACH_BYTES // 1024 // 1024} MB",
        })
    if not raw:
        return "", ""
    name = f"{uuid.uuid4().hex}{ext}"
    (_attach_root() / name).write_bytes(raw)
    return name, Path(f.filename).name[:255]


def _notify_submitted(r: AbuseReportORM) -> None:
    """Receipt to the reporter + notify the internal mailbox. Send failures do not roll back: the ticket is already persisted to DB."""
    try:
        if r.contact_email:
            email_service.send_abuse_report_email(
                to=r.contact_email, kind="received", report_no=r.report_no,
                category_label=_CATEGORY_LABELS.get(r.category, r.category),
                sla_days=SLA_DAYS, description=r.description,
            )
        if ABUSE_EMAIL:
            email_service.send_abuse_report_email(
                to=ABUSE_EMAIL, kind="internal", report_no=r.report_no,
                category_label=_CATEGORY_LABELS.get(r.category, r.category),
                sla_days=SLA_DAYS, description=r.description,
                target=r.target_url or r.target_trace_no,
                reporter=r.contact_email or r.contact_name or "匿名",
            )
    except Exception:  # noqa: BLE001
        log.exception("abuse report notify failed: %s", r.report_no)


def _notify_closed(r: AbuseReportORM) -> None:
    try:
        if r.contact_email:
            email_service.send_abuse_report_email(
                to=r.contact_email,
                kind=("resolved" if r.status == "resolved" else "rejected"),
                report_no=r.report_no,
                category_label=_CATEGORY_LABELS.get(r.category, r.category),
                sla_days=SLA_DAYS, description=r.description, reply=r.reply,
            )
    except Exception:  # noqa: BLE001
        log.exception("abuse report close notify failed: %s", r.report_no)
