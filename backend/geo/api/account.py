"""Personal center (/account) endpoints.

Exposes per-user data the `/account` UI needs beyond the existing auth and
membership routes: detection history (list/detail/delete) and password
change. Everything here requires a valid Bearer token — `get_current_user`
returns the `UserORM` row for the caller.
"""
import json
import logging
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from geo.api.auth import get_current_user
from geo.database import SessionLocal
from geo.models.detection import (
    DetectionRecordDetail,
    DetectionRecordList,
    DetectionRecordSummary,
)
from geo.models.payment import PaymentSessionORM
from geo.models.membership import MembershipORM
from geo.models.user import UserORM
from geo.services.detection_service import (
    delete_detection,
    get_detection,
    list_detections,
)
from geo.utils.error_handler import AppException


router = APIRouter()
logger = logging.getLogger(__name__)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


@router.get("/detections", response_model=DetectionRecordList)
async def list_user_detections(
    page: int = 1,
    size: int = 10,
    current_user: UserORM = Depends(get_current_user),
):
    rows, total = list_detections(user_id=current_user.id, page=page, size=size)
    items = [DetectionRecordSummary.model_validate(r) for r in rows]
    return DetectionRecordList(items=items, total=total, page=page, size=size)


@router.get("/detections/{record_id}", response_model=DetectionRecordDetail)
async def get_user_detection(
    record_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    row = get_detection(user_id=current_user.id, record_id=record_id)
    if row is None:
        raise AppException(status_code=404, message="Detection record not found")
    try:
        snapshot = json.loads(row.result_snapshot) if row.result_snapshot else {}
    except json.JSONDecodeError:
        snapshot = {}
    return DetectionRecordDetail(
        id=row.id,
        url=row.url,
        score=row.score,
        grade=row.grade,
        tier=row.tier,
        mode=row.mode,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        result=snapshot,
    )


@router.post("/detections/{record_id}/export-pdf")
async def export_detection_pdf(
    record_id: int,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
):
    """Export an audit as a vector PDF: backend Chrome prints /result/print/{id}.

    Why not in the frontend: the frontend html2canvas path screenshots bitmaps block by block and recomputes text baselines itself,
    which misalign with background boxes under Chinese fonts (fixed several rounds), and a report starts at 6MB. Using
    the browser's own print engine gives vector text, selectable and searchable, at a few hundred KB.

    The path is built by the backend from a row id verified for ownership, never from a frontend-supplied address: there is no arbitrary-address
    rendering surface, isomorphic to the full_diagnosis export. Anonymous users have no history rows and never reach here;
    the result page still falls back to the frontend export for them.
    """
    row = get_detection(user_id=current_user.id, record_id=record_id)
    if row is None:
        raise AppException(status_code=404, message="Detection record not found")

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token:
        raise AppException(status_code=401, message="missing token")

    from geo.api.report_export import capture_document_pdf, _SEM

    lang = request.query_params.get("lang", "zh")
    zh = lang.startswith("zh")
    title = "AI 搜索可见度与传统 SEO 检测报告" if zh else "AI Visibility & SEO Report"
    header_right = (f"检测目标: {row.url}" if zh else f"Target: {row.url}")

    async with _SEM:
        try:
            pdf = await capture_document_pdf(
                f"/result/print/{row.id}", token, lang, current_user,
                title=title, header_right=header_right,
            )
        except Exception as e:  # noqa: BLE001
            # The frontend only sees a 502 and quietly falls back to html2canvas, so the real cause must stay in the logs
            logger.exception("[detection-export] PDF rendering failed record=%s user=%s",
                             row.id, current_user.email)
            raise AppException(status_code=502, message=f"render failed: {e}")

    return Response(content=pdf, media_type="application/pdf")


@router.delete("/detections/{record_id}")
async def delete_user_detection(
    record_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    ok = delete_detection(user_id=current_user.id, record_id=record_id)
    if not ok:
        raise AppException(status_code=404, message="Detection record not found")
    return {"success": True}


class PaymentRecordItem(BaseModel):
    id: int
    membership_slug: str
    amount_cents: int
    currency: str
    status: str
    provider: str = "stripe"
    checkout_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class PaymentRecordList(BaseModel):
    items: list[PaymentRecordItem]
    total: int
    page: int
    size: int


@router.get("/payments", response_model=PaymentRecordList)
async def list_user_payments(
    page: int = 1,
    size: int = 10,
    current_user: UserORM = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        q = (
            db.query(PaymentSessionORM, MembershipORM.slug)
            .outerjoin(MembershipORM, PaymentSessionORM.membership_id == MembershipORM.id)
            .filter(PaymentSessionORM.user_id == current_user.id)
            .order_by(PaymentSessionORM.created_at.desc())
        )
        total = q.count()
        rows = q.offset((page - 1) * size).limit(size).all()
        items = [
            PaymentRecordItem(
                id=ps.id,
                membership_slug=slug or "",
                amount_cents=ps.amount_cents,
                currency=ps.currency,
                status=ps.status,
                provider=ps.provider or "stripe",
                checkout_url=ps.checkout_url if ps.status == "pending" and (ps.provider or "stripe") == "stripe" else None,
                created_at=str(ps.created_at),
                completed_at=str(ps.completed_at) if ps.completed_at else None,
            )
            for ps, slug in rows
        ]
        return PaymentRecordList(items=items, total=total, page=page, size=size)
    finally:
        db.close()


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: UserORM = Depends(get_current_user),
):
    # Truncate to bcrypt's 72-byte limit, matching user_service.create_user.
    old_pw = payload.old_password[:72]
    new_pw = payload.new_password[:72]

    if not bcrypt.checkpw(old_pw.encode("utf-8"), current_user.password_hash.encode("utf-8")):
        raise AppException(status_code=400, message="Current password is incorrect")

    new_hash = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    db = SessionLocal()
    try:
        row = db.query(UserORM).filter(UserORM.id == current_user.id).first()
        if row is None:
            raise AppException(status_code=404, message="User not found")
        row.password_hash = new_hash
        db.commit()
    finally:
        db.close()

    return {"success": True}


# ── External access: self-service account embed tokens (for external agents / OpenClaw ("xiaolongxia") to link this account) ──────────
# The issuing module agents.geo.embed.tokens does not depend on pydantic-ai and can be called safely from the main backend;
# the signing key is shared with the agent service (same .env), so tokens signed by the main backend verify on :8010.

class IssueTokenRequest(BaseModel):
    label: str = ""
    days: int = 365


@router.get("/agent-tokens")
async def list_agent_tokens(current_user: UserORM = Depends(get_current_user)):
    """List this account's embed tokens (no plaintext, metadata only)."""
    from geo.models.agent import AgentTokenORM

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTokenORM)
            .filter(AgentTokenORM.account_id == current_user.id)
            .order_by(AgentTokenORM.id.desc())
            .all()
        )
        return {
            "tokens": [
                {
                    "tid": r.tid,
                    "caps": r.caps,
                    "label": r.label,
                    "enabled": r.enabled == 1,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }
    finally:
        db.close()


@router.post("/agent-token")
async def issue_agent_token(
    payload: IssueTokenRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """Self-service token: issue a 1-year embed token for the current account (plaintext returned only this once).

    All capabilities enabled (read + diagnose/produce drafts); real external publishing is not a token capability and is controlled by platform guardrails (can_publish is always False).
    """
    from agents.geo.embed.tokens import issue_token

    caps = ["read", "write"]
    days = max(1, min(int(payload.days or 365), 366))
    db = SessionLocal()
    try:
        token, tid = issue_token(
            db, current_user.id, caps=caps, label=(payload.label or "")[:255], ttl_days=days,
        )
        return {"token": token, "tid": tid, "caps": caps, "days": days, "can_publish": False}
    finally:
        db.close()


@router.post("/agent-token/{tid}/revoke")
async def revoke_agent_token(tid: str, current_user: UserORM = Depends(get_current_user)):
    """Revoke one of this account's tokens (only your own can be revoked)."""
    from geo.models.agent import AgentTokenORM

    db = SessionLocal()
    try:
        row = (
            db.query(AgentTokenORM)
            .filter(AgentTokenORM.tid == tid, AgentTokenORM.account_id == current_user.id)
            .first()
        )
        if row is None:
            raise AppException(status_code=404, message="token not found")
        row.enabled = 0
        db.commit()
        return {"success": True, "tid": tid}
    finally:
        db.close()


# ── Device code authorization: approval side (browser login session) ────────────────────────────────────────
# The CLI side (start flow + polling) is in agents/geo/embed/device.py, running in the agent service;
# approval requires a login session, so it lives in the main backend. Both sides share the same agent_device_codes table.
def _normalize_user_code(s: str) -> str:
    """Must follow the same rules as normalize_user_code in agents/geo/embed/device.py (codes typed by hand)."""
    raw = "".join(ch for ch in (s or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else raw


def _load_device_row(db, user_code: str):
    from geo.models.agent import AgentDeviceCodeORM

    code = _normalize_user_code(user_code)
    if len(code) != 9:
        raise AppException(status_code=400, message="配对码格式不对(应为 8 位)")
    row = db.query(AgentDeviceCodeORM).filter(AgentDeviceCodeORM.user_code == code).first()
    if row is None:
        raise AppException(status_code=404, message="配对码不存在,请核对终端里显示的码")
    return row


@router.get("/agent-device/{user_code}")
async def get_device_request(user_code: str, current_user: UserORM = Depends(get_current_user)):
    """Shown on the approval page: who this code is, from which machine, and when it was started, so the user can judge whether it is theirs."""
    from datetime import datetime

    db = SessionLocal()
    try:
        row = _load_device_row(db, user_code)
        expired = row.expires_at < datetime.utcnow()
        return {
            "user_code": row.user_code,
            "status": "expired" if (expired and row.status == "pending") else row.status,
            "client": row.client,
            "client_ip": row.client_ip,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "caps": ["read", "write"],
            "can_publish": False,
        }
    finally:
        db.close()


@router.post("/agent-device/{user_code}/approve")
async def approve_device_request(user_code: str, current_user: UserORM = Depends(get_current_user)):
    """Approve: only writes account_id + approved. The token is signed on the fly when the CLI polls (no plaintext kept in the DB)."""
    from datetime import datetime

    db = SessionLocal()
    try:
        row = _load_device_row(db, user_code)
        if row.expires_at < datetime.utcnow():
            raise AppException(status_code=400, message="配对码已过期,请在终端重新发起安装")
        if row.status == "consumed":
            raise AppException(status_code=400, message="该配对码已完成授权,不能重复批准")
        if row.status == "denied":
            raise AppException(status_code=400, message="该配对码已被拒绝,请重新发起安装")
        row.status = "approved"
        row.account_id = current_user.id
        row.approved_at = datetime.utcnow()
        db.commit()
        return {"success": True, "user_code": row.user_code, "account_id": current_user.id}
    finally:
        db.close()


@router.post("/agent-device/{user_code}/deny")
async def deny_device_request(user_code: str, current_user: UserORM = Depends(get_current_user)):
    """Deny: click this if you did not start it; the CLI stops polling immediately and reports "user denied"."""
    db = SessionLocal()
    try:
        row = _load_device_row(db, user_code)
        if row.status == "consumed":
            raise AppException(status_code=400, message="该配对码已完成授权,无法撤销;请到本页吊销对应 token")
        row.status = "denied"
        db.commit()
        return {"success": True, "user_code": row.user_code}
    finally:
        db.close()


# ── External access: IM connectors (self-built app mode, Feishu/WeCom) ────────────────────────────
def _mask(s: str) -> str:
    return (s[:4] + "••••" + s[-2:]) if s and len(s) > 8 else ("••••" if s else "")


class IMConnectorRequest(BaseModel):
    platform: str = "feishu"          # feishu / wecom / dingtalk
    app_id: str                       # Feishu App ID / WeCom CorpID / DingTalk AppKey (robotCode)
    app_secret: str = ""
    verify_token: str = ""            # WeCom Token (may be empty for Feishu)
    aes_key: str = ""                 # WeCom EncodingAESKey / Feishu Encrypt Key
    agentid: str = ""                 # WeCom self-built app AgentId


@router.get("/im-connectors")
async def list_im_connectors(current_user: UserORM = Depends(get_current_user)):
    """List this account's IM connectors (secrets masked)."""
    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        rows = db.query(AgentIMConnectorORM).filter(AgentIMConnectorORM.account_id == current_user.id).all()
        return {
            "connectors": [
                {
                    "id": r.id, "platform": r.platform, "app_id": r.app_id,
                    "app_secret_masked": _mask(r.app_secret),
                    "has_verify_token": bool(r.verify_token), "has_aes_key": bool(r.aes_key),
                    "enabled": r.enabled == 1,
                    "callback_path": f"/api/agent/im/{r.platform}/callback",
                }
                for r in rows
            ]
        }
    finally:
        db.close()


@router.post("/im-connector")
async def upsert_im_connector(
    payload: IMConnectorRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """Save/update this account's IM connector for a platform (one per account per platform). Returns the callback path to fill back into Feishu."""
    from geo.models.agent import AgentIMConnectorORM

    if payload.platform not in ("feishu", "wecom", "dingtalk"):
        raise AppException(status_code=400, message="platform must be feishu / wecom / dingtalk")
    import json as _json

    db = SessionLocal()
    try:
        row = (
            db.query(AgentIMConnectorORM)
            .filter(AgentIMConnectorORM.account_id == current_user.id, AgentIMConnectorORM.platform == payload.platform)
            .first()
        )
        if row is None:
            row = AgentIMConnectorORM(account_id=current_user.id, platform=payload.platform)
            db.add(row)
        row.app_id = payload.app_id.strip()
        row.app_secret = payload.app_secret.strip()
        row.verify_token = payload.verify_token.strip()
        row.aes_key = payload.aes_key.strip()
        # Keep existing quick button/menu config (quick_actions / menu_map); only update agentid
        try:
            cfg = _json.loads(row.config_json) if row.config_json else {}
            if not isinstance(cfg, dict):
                cfg = {}
        except (ValueError, TypeError):
            cfg = {}
        if payload.agentid.strip():
            cfg["agentid"] = payload.agentid.strip()
        else:
            cfg.pop("agentid", None)
        row.config_json = _json.dumps(cfg, ensure_ascii=False)
        row.enabled = 1
        db.commit()
        return {"success": True, "id": row.id, "callback_path": f"/api/agent/im/{payload.platform}/callback"}
    finally:
        db.close()


@router.post("/im-bind-code")
async def gen_im_bind_code(current_user: UserORM = Depends(get_current_user)):
    """Generate a one-time Feishu binding code (ISV mode): the user sends it to the bot in Feishu to bind this account. Valid for 10 minutes."""
    import random
    import string
    from datetime import datetime, timedelta

    from geo.models.agent import AgentIMBindCodeORM

    db = SessionLocal()
    try:
        # 6 characters, excluding easily confused characters (0/O/1/I)
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(8):
            code = "".join(random.choice(alphabet) for _ in range(6))
            if not db.query(AgentIMBindCodeORM).filter(AgentIMBindCodeORM.code == code).first():
                break
        row = AgentIMBindCodeORM(
            code=code, account_id=current_user.id,
            expires_at=datetime.utcnow() + timedelta(minutes=10), used=0,
        )
        db.add(row)
        db.commit()
        return {"code": code, "expires_in": 600}
    finally:
        db.close()


@router.post("/im-connector/{cid}/delete")
async def delete_im_connector(cid: int, current_user: UserORM = Depends(get_current_user)):
    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        row = (
            db.query(AgentIMConnectorORM)
            .filter(AgentIMConnectorORM.id == cid, AgentIMConnectorORM.account_id == current_user.id)
            .first()
        )
        if row is None:
            raise AppException(status_code=404, message="connector not found")
        db.delete(row)
        db.commit()
        return {"success": True}
    finally:
        db.close()


class QuickConfigRequest(BaseModel):
    # Quick buttons at the bottom of replies: [["📈 Placement progress","How are today's placements progressing and performing?"], ...]
    quick_actions: list[list[str]] = []
    # Feishu bot bottom menu event_key -> question: {"today":"How are today's placements performing?", ...}
    menu_map: dict[str, str] = {}


class PushTargetItem(BaseModel):
    type: str = "webhook"          # webhook | bot
    name: str = ""
    url: str = ""                  # For webhook: Feishu custom bot hook URL
    secret: str = ""               # Webhook signing secret (optional)
    chat_id: str = ""              # For bot: group chat_id
    enabled: bool = True


class PushTargetsRequest(BaseModel):
    # Push targets for brief/alert broadcasts: webhooks (can be several) + bot groups, each switchable on/off
    push_targets: list[PushTargetItem] = []


@router.get("/im-connector/{cid}/quick-config")
async def get_im_quick_config(cid: int, current_user: UserORM = Depends(get_current_user)):
    """Read a connector's quick buttons + menu mapping (returns defaults if unset, for editing in the console)."""
    from geo.im_quick_config import DEFAULT_MENU_MAP, DEFAULT_QUICK_ACTIONS, menu_map, quick_actions
    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        row = (db.query(AgentIMConnectorORM)
               .filter(AgentIMConnectorORM.id == cid, AgentIMConnectorORM.account_id == current_user.id).first())
        if row is None:
            raise AppException(status_code=404, message="connector not found")
        return {
            "platform": row.platform,
            "quick_actions": [[l, q] for l, q in quick_actions(row.config_json)],
            "menu_map": menu_map(row.config_json),
            "defaults": {"quick_actions": DEFAULT_QUICK_ACTIONS, "menu_map": DEFAULT_MENU_MAP},
        }
    finally:
        db.close()


@router.post("/im-connector/{cid}/quick-config")
async def set_im_quick_config(
    cid: int, payload: QuickConfigRequest, current_user: UserORM = Depends(get_current_user),
):
    """Save a connector's quick buttons + menu mapping (merged into config_json, credentials untouched)."""
    import json as _json

    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        row = (db.query(AgentIMConnectorORM)
               .filter(AgentIMConnectorORM.id == cid, AgentIMConnectorORM.account_id == current_user.id).first())
        if row is None:
            raise AppException(status_code=404, message="connector not found")
        try:
            cfg = _json.loads(row.config_json) if row.config_json else {}
            if not isinstance(cfg, dict):
                cfg = {}
        except (ValueError, TypeError):
            cfg = {}
        # Sanitize: label/question non-empty, at most 6 buttons
        qa = [[str(it[0]).strip(), str(it[1]).strip()] for it in payload.quick_actions
              if isinstance(it, (list, tuple)) and len(it) >= 2 and str(it[0]).strip() and str(it[1]).strip()][:6]
        mm = {str(k).strip(): str(v).strip() for k, v in payload.menu_map.items()
              if str(k).strip() and str(v).strip()}
        cfg["quick_actions"] = qa
        cfg["menu_map"] = mm
        row.config_json = _json.dumps(cfg, ensure_ascii=False)
        db.commit()
        return {"success": True, "quick_actions": qa, "menu_map": mm}
    finally:
        db.close()


@router.get("/im-connector/{cid}/push-targets")
async def get_im_push_targets(cid: int, current_user: UserORM = Depends(get_current_user)):
    """Read the connector's push targets (webhooks + bot groups, for brief/alert broadcasts)."""
    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        row = (db.query(AgentIMConnectorORM)
               .filter(AgentIMConnectorORM.id == cid, AgentIMConnectorORM.account_id == current_user.id).first())
        if row is None:
            raise AppException(status_code=404, message="connector not found")
        try:
            cfg = json.loads(row.config_json) if row.config_json else {}
        except (ValueError, TypeError):
            cfg = {}
        targets = cfg.get("push_targets") if isinstance(cfg, dict) else None
        return {"push_targets": targets or [], "last_chat_id": row.last_chat_id}
    finally:
        db.close()


@router.post("/im-connector/{cid}/push-targets")
async def set_im_push_targets(
    cid: int, payload: PushTargetsRequest, current_user: UserORM = Depends(get_current_user),
):
    """Save push targets (merged into config_json.push_targets, credentials/quick buttons untouched)."""
    from geo.models.agent import AgentIMConnectorORM

    db = SessionLocal()
    try:
        row = (db.query(AgentIMConnectorORM)
               .filter(AgentIMConnectorORM.id == cid, AgentIMConnectorORM.account_id == current_user.id).first())
        if row is None:
            raise AppException(status_code=404, message="connector not found")
        try:
            cfg = json.loads(row.config_json) if row.config_json else {}
            if not isinstance(cfg, dict):
                cfg = {}
        except (ValueError, TypeError):
            cfg = {}
        clean: list[dict] = []
        for t in payload.push_targets:
            typ = (t.type or "").strip()
            if typ == "webhook":
                url = t.url.strip()
                # Webhooks with an empty URL are **kept and shown** as placeholders, but forced disabled (no accidental sends)
                clean.append({"type": "webhook", "name": t.name.strip(),
                              "url": url, "secret": t.secret.strip(),
                              "enabled": bool(t.enabled) and bool(url)})
            elif typ == "bot" and t.chat_id.strip():
                clean.append({"type": "bot", "name": t.name.strip(),
                              "chat_id": t.chat_id.strip(), "enabled": bool(t.enabled)})
        cfg["push_targets"] = clean
        row.config_json = json.dumps(cfg, ensure_ascii=False)
        db.commit()
        return {"success": True, "push_targets": clean}
    finally:
        db.close()
