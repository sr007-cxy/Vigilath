"""aitoearn multi-platform distribution channel endpoints (publish via own authorized accounts, free).

  - GET  /api/aitoearn/status                              — channel status (configured / account count)
  - GET  /api/aitoearn/accounts                            — publishable account list (article platforms)
  - POST /api/aitoearn/docs/{doc_id}/preview               — pre-publish dry run (word count / images / rejection reasons)
  - POST /api/aitoearn/docs/{doc_id}/publish               — create tasks and publish flows (multi-account, partial success possible)
  - POST /api/aitoearn/publish-tasks/{task_id}/reconcile   — manually reconcile to pull article links

Permissions: all logged-in users may publish their own drafts to their own bound accounts; company accounts remain staff-only.
The free channel has no membership/charge gate, but content really goes out — publishing must pass compliance_scanner.publish_gate.
Dedup: state-machine optimistic lock sets publishing + flow_id is persisted to channel_meta (commit) before going out;
timeouts/unknown results stay in publishing without blind retries, and reconcile verifies by flow_id.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import ALGORITHM, SECRET_KEY, get_current_user, require_roles
from geo.models.aitoearn import AitoearnAccountBindingORM, AitoearnAuthAttemptORM
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicDocPublishTaskORM, TopicGeneratedDocORM,
    TopicMediaORM,
)
from geo.services import compliance_audit, publish_task_service
from geo.services import aitoearn_publisher as ae_pub
from geo.services.aitoearn_client import (
    AitoearnApiError, AitoearnConfigError, AitoearnError, configured, get_client,
)
from geo.services.aitoearn_poller import is_aitoearn_task, reconcile_task
from geo.services.compliance_scanner import publish_gate
from geo.services.access_control import (
    ACTION_WRITE,
    create_resource_operation_context,
    require_resource_access,
    revalidate_operation_context,
    resource_not_found,
)
from geo.services.system_audit import append_event as append_system_audit

router = APIRouter(prefix="/aitoearn", dependencies=[Depends(get_current_user)])
_PREFLIGHT_AUDIENCE = "aitoearn-publish-preflight"
_PREFLIGHT_TTL = timedelta(minutes=5)


def _is_staff(user) -> bool:
    return getattr(user, "role", "") in ("admin", "operator")


def _parse_utc(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.utcnow() + timedelta(minutes=5)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _api_error(e: AitoearnError) -> HTTPException:
    if isinstance(e, AitoearnConfigError):
        return HTTPException(503, {"code": "AITOEARN_NOT_CONFIGURED", "message": str(e)})
    if isinstance(e, ae_pub.AitoearnPublishRejected):
        return HTTPException(409, {"code": "AITOEARN_REJECTED", "message": str(e)})
    return HTTPException(502, {"code": "AITOEARN_API", "message": str(e)})


def _task_meta(task: TopicDocPublishTaskORM) -> dict:
    try:
        meta = json.loads(task.channel_meta_json or "{}")
        return meta if isinstance(meta, dict) else {}
    except ValueError:
        return {}


# ─────────────────────── Status / accounts ──────────────────────────

@router.get("/status")
async def aitoearn_status(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Channel status. Returns configured:false when not configured (no 5xx; frontend shows "not enabled")."""
    if not configured():
        return {"configured": False, "account_count": 0, "auth_platforms": []}
    try:
        auth_platforms = await ae_pub.auth_platforms()
        accounts = await _visible_accounts(db, current_user)
    except AitoearnError as e:
        return {"configured": True, "account_count": None,
                "auth_platforms": [], "error": str(e)}
    except httpx.RequestError as e:
        return {"configured": True, "account_count": None,
                "auth_platforms": [], "error": f"aitoearn 接口不可达:{e}"}
    return {"configured": True, "account_count": len(accounts),
            "auth_platforms": auth_platforms}


@router.get("/accounts")
async def aitoearn_accounts(refresh: int = 0, current_user=Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """Authorized accounts that support article publishing (platform metadata cached 1h, refresh=1 forces refresh)."""
    try:
        return {"accounts": await _visible_accounts(db, current_user, force=bool(refresh))}
    except AitoearnError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "AITOEARN_NETWORK",
                                  "message": f"aitoearn 接口不可达:{e}"})


# ─────────────────────── Preflight (dry run, no real publishing) ──────────────────────

class AccountsPayload(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=20)
    media_ids: list[int] = Field(default_factory=list, max_length=9)
    preflight_token: Optional[str] = None


class PluginPublishStartPayload(BaseModel):
    account_id: str = Field(min_length=1)
    media_ids: list[int] = Field(default_factory=list, max_length=9)


class PluginPublishCompletePayload(BaseModel):
    success: bool
    share_link: Optional[str] = None
    work_id: Optional[str] = None
    error: Optional[str] = None


class AuthSelectionsPayload(BaseModel):
    accounts: list[dict] = Field(min_length=1, max_length=50)


def _binding_account(binding: AitoearnAccountBindingORM, upstream: Optional[dict]) -> dict:
    """Return a local account reference; never expose the shared upstream id."""
    row = dict(upstream or {})
    row.update({
        "account_id": f"binding:{binding.id}",
        "binding_id": binding.id,
        "account_type": binding.account_type,
        "platform": ae_pub.TYPE_TO_PLATFORM.get(binding.account_type, binding.account_type),
        "nickname": (upstream or {}).get("nickname") or binding.display_name,
        "avatar": (upstream or {}).get("avatar") or binding.avatar_url,
        "scope": "personal",
    })
    return row


async def _visible_accounts(db: Session, user, force: bool = False) -> list[dict]:
    upstream = [row for row in await ae_pub.list_article_accounts(force=force)
                if row.get("account_type") in ae_pub.ENABLED_ACCOUNT_TYPES]
    by_id = {row["account_id"]: row for row in upstream}
    bindings = db.query(AitoearnAccountBindingORM).filter(
        AitoearnAccountBindingORM.user_id == user.id).all()
    out = [_binding_account(row, by_id.get(row.upstream_account_id))
           for row in bindings if row.upstream_account_id in by_id]
    if _is_staff(user):
        bound_ids = {value for (value,) in db.query(
            AitoearnAccountBindingORM.upstream_account_id).all()}
        for row in upstream:
            if row["account_id"] not in bound_ids:
                company = dict(row)
                company["account_id"] = f"company:{row['account_id']}"
                company["scope"] = "company"
                out.append(company)
    return out


@router.post("/accounts/auth/{platform}")
async def start_account_auth(platform: str, current_user=Depends(get_current_user),
                             db: Session = Depends(get_db)):
    """Start upstream OAuth/QR auth while keeping its session id server-side."""
    if platform not in ae_pub.ENABLED_ACCOUNT_TYPES:
        raise HTTPException(422, "unsupported platform")
    try:
        if platform not in await ae_pub.auth_platforms(force=True):
            raise HTTPException(409, {
                "code": "AITOEARN_PLATFORM_UNAVAILABLE",
                "message": f"当前 AiToEarn 实例未启用 {ae_pub.TYPE_TO_PLATFORM.get(platform, platform)} 授权与图文发布能力",
            })
        result = await get_client().start_account_auth(platform)
    except AitoearnError as e:
        raise _api_error(e)
    attempt = AitoearnAuthAttemptORM(
        id=str(uuid4()), user_id=current_user.id, platform=platform,
        upstream_session_id=str(result["sessionId"]),
        expires_at=_parse_utc(result.get("expiresAt")),
    )
    db.add(attempt)
    db.commit()
    return {
        "attempt_id": attempt.id,
        "url": result["url"],
        "expires_at": attempt.expires_at,
        "auth_instructions": result.get("authInstructions"),
    }


@router.get("/accounts/auth-attempts/{attempt_id}")
async def account_auth_status(attempt_id: str, current_user=Depends(get_current_user),
                              db: Session = Depends(get_db)):
    attempt = db.query(AitoearnAuthAttemptORM).filter(
        AitoearnAuthAttemptORM.id == attempt_id,
        AitoearnAuthAttemptORM.user_id == current_user.id,
    ).first()
    if attempt is None:
        raise HTTPException(404, "authorization attempt not found")
    if attempt.platform not in ae_pub.ENABLED_ACCOUNT_TYPES:
        return {"status": "failed", "error": "该平台已停止开放，请重新选择海外平台"}
    if attempt.completed_at:
        return {"status": "completed", "accounts": await _visible_accounts(db, current_user)}
    if attempt.expires_at < datetime.utcnow():
        return {"status": "failed", "error": "授权已过期，请重新发起"}
    try:
        result = await get_client().get_account_auth_status(
            attempt.platform, attempt.upstream_session_id)
    except AitoearnError as e:
        raise _api_error(e)
    status = str(result.get("status") or "pending")
    if status != "completed":
        return {
            "status": status,
            "error_code": result.get("errorCode"),
            "requires_selection": bool(result.get("requiresSelection")),
            "selectable_accounts": result.get("selectableAccounts") or [],
        }

    snapshots = {str(row.get("accountId")): row for row in (result.get("accounts") or [])}
    account_ids = [str(value) for value in (result.get("accountIds") or []) if value]
    if not account_ids and result.get("accountId"):
        account_ids = [str(result["accountId"])]
    if not account_ids:
        raise HTTPException(502, {"code": "AITOEARN_AUTH_EMPTY", "message": "授权完成但未返回账号"})

    conflicts = db.query(AitoearnAccountBindingORM).filter(
        AitoearnAccountBindingORM.upstream_account_id.in_(account_ids),
        AitoearnAccountBindingORM.user_id != current_user.id,
    ).all()
    if conflicts:
        raise HTTPException(409, {
            "code": "AITOEARN_ACCOUNT_BOUND",
            "message": "该社交账号已绑定其他 Vigilath 用户",
        })
    existing = {row.upstream_account_id: row for row in db.query(
        AitoearnAccountBindingORM).filter(
            AitoearnAccountBindingORM.upstream_account_id.in_(account_ids)).all()}
    for upstream_id in account_ids:
        snap = snapshots.get(upstream_id) or {}
        binding = existing.get(upstream_id)
        if binding is None:
            binding = AitoearnAccountBindingORM(
                user_id=current_user.id, upstream_account_id=upstream_id,
                account_type=str(snap.get("platform") or attempt.platform),
            )
            db.add(binding)
        binding.display_name = str(snap.get("displayName") or binding.display_name or "")
        binding.avatar_url = snap.get("avatarUrl") or binding.avatar_url
    attempt.completed_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, {
            "code": "AITOEARN_ACCOUNT_BOUND",
            "message": "该社交账号已绑定其他 Vigilath 用户",
        })
    return {"status": "completed", "accounts": await _visible_accounts(db, current_user, force=True)}


@router.post("/accounts/auth-attempts/{attempt_id}/selections")
async def submit_account_auth_selections(
    attempt_id: str, payload: AuthSelectionsPayload,
    current_user=Depends(get_current_user), db: Session = Depends(get_db),
):
    attempt = db.query(AitoearnAuthAttemptORM).filter(
        AitoearnAuthAttemptORM.id == attempt_id,
        AitoearnAuthAttemptORM.user_id == current_user.id,
        AitoearnAuthAttemptORM.completed_at.is_(None),
    ).first()
    if attempt is None:
        raise HTTPException(404, "authorization attempt not found")
    if attempt.platform not in ae_pub.ENABLED_ACCOUNT_TYPES:
        raise HTTPException(409, "platform is no longer enabled")
    if attempt.expires_at < datetime.utcnow():
        raise HTTPException(409, "authorization attempt expired")
    selected = []
    for row in payload.accounts:
        platform_uid = str(row.get("platformUid") or "").strip()
        if not platform_uid:
            raise HTTPException(422, "platformUid is required")
        item = {"platformUid": platform_uid}
        if row.get("account"):
            item["account"] = str(row["account"])
        selected.append(item)
    try:
        await get_client().submit_account_auth_selections(
            attempt.upstream_session_id, selected)
    except AitoearnError as e:
        raise _api_error(e)
    return {"submitted": True}


@router.get("/account-bindings")
def audit_account_bindings(_current_user=Depends(require_roles("admin", "operator")),
                           db: Session = Depends(get_db)):
    """Staff audit view. It intentionally omits upstream account identifiers."""
    rows = db.query(AitoearnAccountBindingORM).order_by(
        AitoearnAccountBindingORM.created_at.desc()).all()
    return {"bindings": [{
        "binding_id": row.id,
        "user_id": row.user_id,
        "account_type": row.account_type,
        "display_name": row.display_name,
        "created_at": row.created_at,
    } for row in rows]}


@router.get("/bindings")
def own_account_bindings(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(AitoearnAccountBindingORM).filter(
        AitoearnAccountBindingORM.user_id == current_user.id,
    ).order_by(AitoearnAccountBindingORM.created_at.desc()).all()
    return {"accounts": [{
        "account_id": f"binding:{row.id}",
        "binding_id": row.id,
        "account_type": row.account_type,
        "platform": ae_pub.TYPE_TO_PLATFORM.get(row.account_type, row.account_type),
        "nickname": row.display_name,
        "avatar": row.avatar_url,
        "scope": "personal",
        "publish_enabled": row.account_type in ae_pub.ENABLED_ACCOUNT_TYPES,
    } for row in rows]}


@router.delete("/accounts/{binding_id}")
async def unbind_account(binding_id: int, current_user=Depends(get_current_user),
                         db: Session = Depends(get_db)):
    binding = db.query(AitoearnAccountBindingORM).filter(
        AitoearnAccountBindingORM.id == binding_id,
        AitoearnAccountBindingORM.user_id == current_user.id,
    ).first()
    if binding is None:
        raise HTTPException(404, "account binding not found")
    try:
        await get_client().delete_account(binding.upstream_account_id)
    except AitoearnError as e:
        raise _api_error(e)
    db.delete(binding)
    db.commit()
    return {"deleted": True}


def _doc_or_409(
    db: Session, doc_id: int, actor,
) -> tuple[TopicGeneratedDocORM, AiTelemetryTopicORM]:
    doc = db.get(TopicGeneratedDocORM, doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, actor, doc, ACTION_WRITE)
    if doc.status not in ("approved", "published"):
        raise HTTPException(409, {"code": "NEED_APPROVED", "message": "请先通过审核再投放"})
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if topic is None:
        raise resource_not_found()
    return doc, topic


def _pick_doc_images(db: Session, doc: TopicGeneratedDocORM,
                     media_ids: list[int]) -> list[TopicMediaORM]:
    """Only allow images under the current draft's topic; keep request order and reject unauthorized IDs."""
    if not media_ids:
        return []
    rows = db.query(TopicMediaORM).filter(
        TopicMediaORM.id.in_(media_ids),
        TopicMediaORM.topic_id == doc.topic_id,
        TopicMediaORM.kind == "image",
    ).all()
    by_id = {row.id: row for row in rows}
    missing = [str(mid) for mid in media_ids if mid not in by_id]
    if missing:
        raise HTTPException(422, {
            "code": "UNKNOWN_MEDIA",
            "message": f"图片不存在或不属于当前主题:{', '.join(missing)}",
        })
    return [by_id[mid] for mid in media_ids]


def _media_publish_url(row: TopicMediaORM) -> str:
    # aitoearn_publisher prefers to recognize this URL as a local file and read it from disk directly.
    return f"/api/ai-telemetry/public/topic-media/{row.topic_id}/{Path(row.storage_path).name}"


def _preflight_token(doc_id: int, user_id: int, payload: AccountsPayload,
                     account_types: dict[str, str], snapshots: dict[str, dict]) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + _PREFLIGHT_TTL
    token = jwt.encode({
        "aud": _PREFLIGHT_AUDIENCE,
        "sub": str(user_id),
        "doc_id": doc_id,
        "account_ids": sorted(payload.account_ids),
        "media_ids": payload.media_ids,
        "account_types": account_types,
        "snapshots": snapshots,
        "exp": expires_at,
    }, SECRET_KEY, algorithm=ALGORITHM)
    return token, expires_at


def _verified_preflight_token(doc_id: int, user_id: int,
                              payload: AccountsPayload) -> dict:
    """Returns an empty result when the token is invalid/expired; real publishing safely degrades to a full recheck."""
    if not payload.preflight_token:
        return {}
    try:
        value = jwt.decode(
            payload.preflight_token, SECRET_KEY, algorithms=[ALGORITHM],
            audience=_PREFLIGHT_AUDIENCE,
        )
        if (str(value.get("sub")) != str(user_id)
                or value.get("doc_id") != doc_id
                or not set(payload.account_ids).issubset(set(value.get("account_ids") or []))
                or value.get("media_ids") != payload.media_ids):
            return {}
        return value if isinstance(value, dict) else {}
    except JWTError:
        return {}


async def _pick_accounts(db: Session, user, account_ids: list[str]) -> list[dict]:
    """Resolve local references and enforce ownership before revealing upstream ids."""
    visible = {a["account_id"]: a for a in await _visible_accounts(db, user)}
    accounts = visible
    missing = [i for i in account_ids if i not in accounts]
    if missing:
        raise HTTPException(422, {
            "code": "UNKNOWN_ACCOUNT",
            "message": f"账号不存在或不支持图文发布:{', '.join(missing)}(试试刷新账号列表)",
        })
    picked = []
    for ref in account_ids:
        item = dict(accounts[ref])
        item["account_ref"] = ref
        if ref.startswith("binding:"):
            binding = db.query(AitoearnAccountBindingORM).filter(
                AitoearnAccountBindingORM.id == int(ref.split(":", 1)[1]),
                AitoearnAccountBindingORM.user_id == user.id,
            ).first()
            if binding is None:
                raise HTTPException(404, "account binding not found")
            item["account_id"] = binding.upstream_account_id
            item["binding_id"] = binding.id
        elif ref.startswith("company:") and _is_staff(user):
            item["account_id"] = ref.split(":", 1)[1]
            item["binding_id"] = None
        else:
            raise HTTPException(404, "account not found")
        picked.append(item)
    return picked


@router.post("/docs/{doc_id}/preview")
async def preview(doc_id: int, payload: AccountsPayload,
                  current_user=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Per-account dry-run rendering: word count / limits / images / warnings / rejection reasons, shown to the frontend before publishing."""
    doc, _topic = _doc_or_409(db, doc_id, current_user)
    images = _pick_doc_images(db, doc, payload.media_ids)
    extra_urls = [_media_publish_url(row) for row in images]
    try:
        picked = await _pick_accounts(db, current_user, payload.account_ids)
        metas = await ae_pub.platform_meta()
    except AitoearnError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "AITOEARN_NETWORK",
                                  "message": f"aitoearn 接口不可达:{e}"})
    rows = []
    checks = {}
    snapshots = {}
    account_types = {}
    for acc in picked:
        account_type = acc["account_type"]
        if account_type not in checks:
            checks[account_type] = await ae_pub.preflight_content(
                doc, metas.get(account_type) or {}, extra_image_urls=extra_urls)
        check = checks[account_type]
        snapshots[account_type] = ae_pub.preflight_snapshot(check, metas.get(account_type) or {})
        account_types[acc["account_ref"]] = account_type
        r = check.rendered
        rows.append({
            "account_id": acc["account_ref"],
            "platform": acc["platform"],
            "nickname": acc["nickname"],
            "status": check.status,
            "ok": check.status != "blocked",
            "reject_reason": check.reject_reason,
            "warnings": check.warning_messages,
            "issues": [issue.as_dict() for issue in check.issues],
            "images": [image.as_dict(i) for i, image in enumerate(check.images, 1)],
            "title": r.title,
            "body": r.body,
            "body_len": len(r.body),
            "max_body": acc.get("max_body"),
            "image_count": len(r.image_urls),
            "need_cover": acc.get("need_cover"),
        })
    token, expires_at = _preflight_token(
        doc.id, current_user.id, payload, account_types, snapshots)
    return {"doc_id": doc.id, "rows": rows, "preflight_token": token,
            "expires_at": expires_at.isoformat()}


@router.post("/docs/{doc_id}/plugin-publish/start")
async def plugin_publish_start(
    doc_id: int,
    payload: PluginPublishStartPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create/reset a Xiaohongshu task without calling the Flow API.

    The browser invokes the installed Aitoearn extension after this endpoint
    returns.  Keeping task creation server-side preserves the compliance gate
    and gives the extension result a durable task to complete.
    """
    doc, topic = _doc_or_409(db, doc_id, current_user)
    images = _pick_doc_images(db, doc, payload.media_ids)
    selected_media_ids = [row.id for row in images]
    selected_media_urls = [_media_publish_url(row) for row in images]

    try:
        picked = await _pick_accounts(db, current_user, [payload.account_id])
        metas = await ae_pub.platform_meta()
    except AitoearnError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "AITOEARN_NETWORK", "message": str(e)})

    account = picked[0]
    if account.get("account_type") != "xhs":
        raise HTTPException(422, {
            "code": "PLUGIN_PLATFORM_UNSUPPORTED",
            "message": "插件直发当前仅支持小红书账号",
        })

    rendered = ae_pub.render_for_platform(
        doc,
        metas.get("xhs") or {},
        extra_image_urls=selected_media_urls,
    )
    if rendered.reject_reason:
        raise HTTPException(409, {
            "code": "AITOEARN_REJECTED",
            "message": rendered.reject_reason,
        })

    ok, reason = publish_gate(
        db, doc, topic, hook="publish_aitoearn_plugin",
        actor_id=current_user.id,
        actor_name=getattr(current_user, "name", "") or "",
    )
    if not ok:
        db.commit()
        raise HTTPException(409, {"code": "COMPLIANCE_BLOCKED", "message": reason})

    _, operation = create_resource_operation_context(
        db, current_user, doc,
        action="publish.plugin.start", target_resource_type="document",
    )

    tasks = publish_task_service.create_tasks(
        db, doc, topic,
        [{"platform": account["platform"], "media_name": account["nickname"],
          "channel_type": "auto_account", "publish_mode": "auto"}],
        assigned_to_user_id=current_user.id,
        initial_status="pending_publish",
        operation_context_id=operation.id,
        channel_meta={
            "channel": "aitoearn_plugin",
            "account_id": account["account_id"],
            "account_type": "xhs",
            "selected_media_ids": selected_media_ids,
            "selected_media_urls": selected_media_urls,
        },
    )
    task = tasks[0]
    if task.status == "published":
        return {
            "task": publish_task_service.task_to_dict(task, redact=False),
            "action": "skip",
            "title": rendered.title,
            "body": rendered.body,
            "warnings": rendered.warnings,
        }

    task.status = "pending_publish"
    task.last_error = None
    task.channel_type = "auto_account"
    task.publish_mode = "auto"
    task.channel_meta_json = json.dumps({
        "channel": "aitoearn_plugin",
        "account_id": account["account_id"],
        "account_type": "xhs",
        "selected_media_ids": selected_media_ids,
        "selected_media_urls": selected_media_urls,
        "plugin_started_at": datetime.utcnow().isoformat(),
    }, ensure_ascii=False)
    try:
        revalidate_operation_context(db, operation.id)
    except Exception as exc:
        task.status = "cancelled"
        task.last_error = "authorization revoked"
        append_system_audit(
            db, operation_context=operation,
            event_type="authorization_denied",
            action="publish.plugin.ready", object_type="publish_task",
            object_id=task.id, result="denied", reason=str(exc),
        )
        db.commit()
        raise resource_not_found()
    append_system_audit(
        db, operation_context=operation, event_type="authorization_rechecked",
        action="publish.plugin.ready", object_type="publish_task",
        object_id=task.id, result="success",
    )
    append_system_audit(
        db, operation_context=operation, event_type="task_started",
        action="publish.plugin.ready", object_type="publish_task",
        object_id=task.id, result="success",
    )
    db.commit()
    db.refresh(task)
    return {
        "task": publish_task_service.task_to_dict(task, redact=False),
        "action": "ready",
        "title": rendered.title,
        "body": rendered.body,
        "warnings": rendered.warnings,
    }


@router.post("/plugin-publish-tasks/{task_id}/complete")
def plugin_publish_complete(
    task_id: int,
    payload: PluginPublishCompletePayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(TopicDocPublishTaskORM, task_id)
    if task is None or _task_meta(task).get("channel") != "aitoearn_plugin":
        raise resource_not_found()
    require_resource_access(db, current_user, task, ACTION_WRITE)

    if not payload.success:
        task.status = "failed"
        task.last_error = (payload.error or "小红书插件发布失败")[:1000]
        db.commit()
        db.refresh(task)
        return {"task": publish_task_service.task_to_dict(task, redact=False), "action": "failed"}

    share_link = (payload.share_link or "").strip()
    if not share_link:
        task.status = "pending_url"
        task.last_error = "插件已完成发布，但未返回作品链接"
        db.commit()
        db.refresh(task)
        return {"task": publish_task_service.task_to_dict(task, redact=False), "action": "pending_url"}

    try:
        publish_task_service.submit_candidate_url(
            db, task, share_link,
            proof={"source": "aitoearn_plugin", "work_id": payload.work_id},
            actor_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(409, {"code": "PLUGIN_RESULT_INVALID", "message": str(e)})
    db.commit()
    db.refresh(task)
    return {"task": publish_task_service.task_to_dict(task, redact=False), "action": "url_submitted"}


# ─────────────────────── Publish ──────────────────────────

@router.post("/docs/{doc_id}/publish")
async def publish(
    doc_id: int,
    payload: AccountsPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create tasks and publish flows (one task and one single-item flow per account).

    Guard chain: review-state check → publish_gate compliance gate (where content really goes out) → per-task state-machine
    optimistic lock to publishing + flow_id persisted first (commit) → create flow → pending_url.
    Failure of some accounts does not block the others; per-task results are returned.
    """
    doc, topic = _doc_or_409(db, doc_id, current_user)
    images = _pick_doc_images(db, doc, payload.media_ids)
    selected_media_ids = [row.id for row in images]
    selected_media_urls = [_media_publish_url(row) for row in images]
    if not configured():
        raise HTTPException(503, {"code": "AITOEARN_NOT_CONFIGURED",
                                  "message": "aitoearn 通道未配置(AITOEARN_API_KEY)"})
    try:
        picked = await _pick_accounts(db, current_user, payload.account_ids)
    except AitoearnError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "AITOEARN_NETWORK",
                                  "message": f"aitoearn 接口不可达:{e}"})
    token_data = _verified_preflight_token(doc_id, current_user.id, payload)
    token_account_types = token_data.get("account_types") or {}
    token_snapshots = token_data.get("snapshots") or {}

    ok, reason = publish_gate(
        db, doc, topic, hook="publish_aitoearn",
        actor_id=current_user.id, actor_name=getattr(current_user, "name", "") or "",
    )
    if not ok:
        db.commit()  # Record the block for audit
        raise HTTPException(409, {"code": "COMPLIANCE_BLOCKED", "message": reason})

    _, operation = create_resource_operation_context(
        db, current_user, doc,
        action="publish.aitoearn", target_resource_type="document",
    )

    results = []
    for acc in picked:
        # Independent channel_meta per account; create_tasks is idempotent on (doc, platform, media_name)
        tasks = publish_task_service.create_tasks(
            db, doc, topic,
            [{"platform": acc["platform"], "media_name": acc["nickname"],
              "channel_type": "auto_account", "publish_mode": "auto",
              "aitoearn_binding_id": acc.get("binding_id")}],
            assigned_to_user_id=current_user.id,
            initial_status="planned",
            operation_context_id=operation.id,
            channel_meta={"channel": ae_pub.CHANNEL,
                          "account_id": acc["account_id"],
                          "account_type": acc["account_type"],
                          "selected_media_ids": selected_media_ids,
                          "selected_media_urls": selected_media_urls},
        )
        db.commit()
        task = tasks[0]
        results.append(await _publish_one(
            db, task, doc, acc, current_user,
            selected_media_ids=selected_media_ids,
            selected_media_urls=selected_media_urls,
            preflight_snapshot=(token_snapshots.get(acc["account_type"])
                                if token_account_types.get(acc["account_ref"])
                                == acc["account_type"] else None),
        ))
    return {"doc_id": doc.id, "results": results}


async def _publish_one(db: Session, task: TopicDocPublishTaskORM,
                       doc: TopicGeneratedDocORM, acc: dict, current_user, *,
                       selected_media_ids: list[int],
                       selected_media_urls: list[str],
                       preflight_snapshot: Optional[dict] = None) -> dict:
    """Publish a single task; returns {task, action, detail}. Exceptions are not raised (partial-success semantics)."""
    def _row(action: str, detail: str) -> dict:
        return {"task": publish_task_service.task_to_dict(task, redact=False),
                "action": action, "detail": detail}

    if task.status == "published":
        return _row("skip", "该账号已发布过这篇稿")
    if task.status in ("publishing", "pending_url"):
        return _row("skip", f"任务在途(状态 {task.status}),可用「拉取回执」对账")

    operation = None
    if task.operation_context_id is not None:
        from geo.models.organization import OperationContextORM
        operation = db.get(OperationContextORM, task.operation_context_id)
        try:
            revalidate_operation_context(db, task.operation_context_id)
        except Exception as exc:
            task.status = "cancelled"
            task.last_error = "authorization revoked"
            append_system_audit(
                db, operation_context=operation,
                event_type="authorization_denied",
                action="publish.aitoearn.start", object_type="publish_task",
                object_id=task.id, result="denied", reason=str(exc),
            )
            db.commit()
            return _row("cancelled", "authorization revoked")

    # State-machine optimistic lock: planned/failed → publishing, prevents double clicks / double workers
    locked = db.execute(
        sa_text("UPDATE topic_doc_publish_tasks SET status='publishing' "
                "WHERE id=:id AND status IN ('planned','failed')"),
        {"id": task.id},
    )
    if locked.rowcount != 1:
        db.rollback()
        db.refresh(task)
        return _row("skip", "任务状态已变化(可能有并发操作),请刷新")

    # Persist "initiated" evidence before going out: flow_id goes into channel_meta, reconcile relies on it for verification
    meta = _task_meta(task)
    attempt = int(meta.get("attempt") or 0) + 1
    meta.update(
        channel=ae_pub.CHANNEL, account_id=acc["account_id"],
        account_type=acc["account_type"], attempt=attempt,
        selected_media_ids=selected_media_ids,
        selected_media_urls=selected_media_urls,
        flow_id=ae_pub.make_flow_id(task.id, attempt),
        publish_started_at=datetime.utcnow().isoformat(),
    )
    db.execute(
        sa_text("UPDATE topic_doc_publish_tasks SET channel_meta_json=:m WHERE id=:id"),
        {"id": task.id, "m": json.dumps(meta, ensure_ascii=False)},
    )
    if operation is not None:
        append_system_audit(
            db,
            operation_context=operation,
            event_type="authorization_rechecked",
            action="publish.aitoearn.start",
            object_type="publish_task",
            object_id=task.id,
            result="success",
        )
        append_system_audit(
            db,
            operation_context=operation,
            event_type="task_started",
            action="publish.aitoearn.start",
            object_type="publish_task",
            object_id=task.id,
            result="success",
        )
    db.commit()
    db.refresh(task)

    try:
        result = await ae_pub.publish_task(
            task, doc, signed_preflight=preflight_snapshot)
    except ae_pub.AitoearnPublishRejected as e:
        # Content does not meet platform hard requirements (e.g. WeChat official account without images) — definitely not sent, failed prompts to add content
        task.status = "failed"
        task.last_error = str(e)
        db.commit()
        return _row("rejected", str(e))
    except AitoearnApiError as e:
        # Server returned an explicit error — flow was not created
        task.status = "failed"
        task.last_error = f"发布失败:{e}"
        compliance_audit.record(
            db, topic_id=task.topic_id, event_type="channel_publish",
            object_type="publish_task", object_id=task.id,
            actor_type="admin", actor_id=current_user.id,
            detail={"action": "publish_failed", "message": str(e)},
        )
        db.commit()
        return _row("failed", str(e))
    except httpx.ConnectError as e:
        # Connection never established — the request certainly did not reach the server, safe to roll back
        task.status = "planned"
        task.last_error = f"网络连接失败,未发布:{e}"
        db.commit()
        return _row("revert", task.last_error)
    except (httpx.RequestError, AitoearnError) as e:
        # Timeout/disconnected midway — result unknown, stay in publishing, reconcile verifies by flow_id
        task.last_error = f"发布结果未知({e}),请稍后「拉取回执」对账,勿重复发布"
        db.commit()
        return _row("unknown", task.last_error)

    task.status = "pending_url"
    task.last_error = None
    meta = _task_meta(task)
    meta["asset_map"] = result.get("asset_map") or {}
    meta["publish_warnings"] = result.get("warnings") or []
    meta["flow_response"] = result.get("response")
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False, default=str)
    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="channel_publish",
        object_type="publish_task", object_id=task.id,
        actor_type="admin", actor_id=current_user.id,
        actor_name=getattr(current_user, "name", "") or "",
        detail={"action": "flow_created", "flow_id": meta.get("flow_id"),
                "platform": task.platform, "account_id": acc["account_id"]},
    )
    db.commit()
    detail = "已提交发布,等待平台产出作品链接"
    if meta["publish_warnings"]:
        detail += ";" + ";".join(meta["publish_warnings"])
    return _row("submitted", detail)


# ─────────────────────── Reconciliation / receipts ──────────────────────────

@router.post("/publish-tasks/{task_id}/reconcile")
async def reconcile(
    task_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually pull receipts: query flow details to advance state (verify publishing / get link for pending_url)."""
    task = db.get(TopicDocPublishTaskORM, task_id)
    if task is None or not is_aitoearn_task(task):
        raise resource_not_found()
    require_resource_access(db, current_user, task, ACTION_WRITE)
    if task.status not in ("publishing", "pending_url"):
        raise HTTPException(409, {"code": "BAD_STATUS",
                                  "message": f"任务当前状态 {task.status} 无需对账"})
    try:
        out = await reconcile_task(db, task, actor_id=current_user.id)
    except AitoearnError as e:
        db.rollback()
        raise _api_error(e)
    except httpx.RequestError as e:
        db.rollback()
        raise HTTPException(502, {"code": "AITOEARN_NETWORK",
                                  "message": f"aitoearn 接口不可达:{e}"})
    db.commit()
    return {"task": publish_task_service.task_to_dict(task, redact=False), **out}
