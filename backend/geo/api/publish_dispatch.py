"""Publish dispatch API — create tasks / claim tasks / receipts (report).

The claim protocol of plan §3.4 lives here. Pull model: workers claim actively, so workers can sit behind residential
networks without a public entry point.

## Three safeguards; missing any one allows duplicate publishing

1. `FOR UPDATE SKIP LOCKED` locks queue rows (per with_for_update in api/engine_sessions.py:459);
2. Account-level advisory lock (services/publish_locks.py::lock_account) — one account may have several
   tasks, and locking queue rows alone cannot stop "two tasks, same account, two concurrent claims";
3. `visible_after` backoff — when a claim fails, push the task back, and **attempt is not incremented** (no retry budget consumed).

## Three deliberate differences from engine-pool check-out

| | Engine pool | Publishing pool |
|---|---|---|
| Account choice | Load-balanced by use_count ASC | 🔴 account already specified by the task, claim does not pick |
| Lease | Worker-sticky, claimable by others after expiry | First successful claim writes owner_worker_id automatically, then bound for life, **no preemption** |
| Receipt | Reports fail_type | 🔴 must also report exit_ip; drift stops the account |

Why no grab fallback (comment at api/engine_sessions.py:518-521; the engine pool removed its third-level fallback for this reason):
grabbing an account leased by another worker would put the same account on two egress IPs at once, triggering cross-region/IP-density
risk control. Publishing is stricter — different machines differ in TLS fingerprint, font list, timezone and kernel features, which is
a **stealthier and harder** ban signal than IP drift.

## Two paths in the success branch

When `report` receives `published_url`:

- With `published_url` → register the candidate URL via `publish_task_service.submit_candidate_url`.
  The account pool does not assemble platform URLs in the backend; it only uses URLs returned by publisher-service/Core Adapter.
- Without a URL → the task stays at `pending_url`. 🔴 Core invariant: a task without `published_url`
  cannot enter `published`, even if the worker claims success.

When `mark_published` raises ValueError (dedup collision / doc deleted) it is **swallowed, not raised to the worker**:
the publish there is already done and irreversible; if report returned 500, the worker would get no confirmation while the task stays claimed,
and after the lease expires it might be re-dispatched — the same article published twice. So it degrades to pending_url + URL kept as evidence + awaits a human.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from typing import Optional, Union
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, aliased

from geo.api.ai_telemetry import get_db
from geo.api.auth import get_current_user, require_roles
from geo.api.publish_accounts import (
    LEASE_TTL_MINUTES,
    RENEW_TTL_DAYS,
    effective_cooldown_minutes,
    effective_cap,
    local_today,
    require_publish_service_token,
    resolve_proxy_url,
)
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicDocPublishTaskORM, TopicGeneratedDocORM,
)
from geo.models.publish_accounts import (
    CHANNEL_AGGREGATOR,
    CHANNEL_SERVER_POOL,
    EGRESS_HOST,
    PublishAccountORM,
    PublishDispatchTaskORM,
    PublishFailureType,
    PublishPlatformORM,
    effective_status,
    publish_policy,
)
from geo.services.publish_locks import lock_account
from geo.services.publish_render import render_for_publish
from geo.services.publish_task_service import mark_published, submit_candidate_url
from geo.services.access_control import (
    ACTION_WRITE,
    apply_enterprise_scope,
    create_resource_operation_context,
    require_resource_access,
    revalidate_operation_context,
    resource_not_found,
)
from geo.services.system_audit import append_event as append_system_audit
from geo.utils.publish_crypto import PublishCryptoUnavailable, decrypt_required

log = logging.getLogger(__name__)

router = APIRouter()
service_router = APIRouter(dependencies=[Depends(require_publish_service_token)])


# ── Configuration ────────────────────────────────────────────────────────────

# How long to push a task back when no account can be claimed. Follows telemetry dispatch's 90s — prevents 3s polling from
# immediately re-claiming the same bad task in a hot loop.
TRANSIENT_BACKOFF_SEC = int(os.environ.get("PUBLISH_TRANSIENT_BACKOFF_SEC", "90"))
# 🔴 Conservative wait for submit_unknown: reconcile by draft_id only after ≥10 minutes, **never blind retry**.
SUBMIT_UNKNOWN_DEFER_SEC = int(os.environ.get("PUBLISH_SUBMIT_UNKNOWN_DEFER_SEC", "600"))
# RATE_LIMITED default cooldown (when the worker gives no rate_limited_until)
RATE_LIMIT_COOLDOWN_SEC = int(os.environ.get("PUBLISH_RATE_LIMIT_COOLDOWN_SEC",
                                             str(6 * 3600)))
# Sibling cooldown: how long sibling accounts on the same egress cool down too. 0 = off.
SIBLING_COOLDOWN_SEC = int(os.environ.get("PUBLISH_SIBLING_COOLDOWN_SEC", str(4 * 3600)))
# How long a claimed/running queue row may stay before manual fallback is allowed. Same env as publish_pool_healer.
CLAIM_STALE_MINUTES = int(os.environ.get("PUBLISH_CLAIM_STALE_MINUTES", "30"))
# Fixed message prefix of the 404. Workers and tests rely on it to judge "temporarily unavailable, not an error" —
# following the engine pool's "no active session available" convention (matched by dispatch's _TRANSIENT_ERR).
NO_ACCOUNT_PREFIX = "no publishable account available"

DRAFT_URL_HOSTS: dict[str, tuple[str, ...]] = {
    "csdn": ("csdn.net",),
    "toutiao": ("toutiao.com", "mp.toutiao.com"),
    "sohu": ("sohu.com", "mp.sohu.com"),
    "smzdm": ("smzdm.com",),
    "wxgzh": ("mp.weixin.qq.com",),
    "xhs": ("xiaohongshu.com",),
    "douyin": ("douyin.com", "creator.douyin.com"),
    "cnblogs": ("cnblogs.com",),
}


def _validated_draft_url(platform: str, url: Optional[str],
                         draft_id: Optional[str]) -> Optional[str]:
    """Only accept edit URLs actually returned by the platform whose identity matches draft_id; never construct one from the id."""
    value = (url or "").strip()
    wanted = (draft_id or "").strip()
    if not value or not wanted:
        return None
    try:
        parsed = urlparse(value)
    except Exception:  # noqa: BLE001
        return None
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").lower()
    if platform == "baijia":
        if host != "baijiahao.baidu.com" or \
                parsed.path.rstrip("/") != "/builder/rc/edit":
            return None
        values = parse_qs(parsed.query)
        return value if wanted in values.get("article_id", []) else None
    if platform == "zhihu":
        if host not in {"zhuanlan.zhihu.com", "www.zhihu.com"}:
            return None
        if parsed.path.rstrip("/") == f"/p/{wanted}/edit":
            return value
        if parsed.path.rstrip("/") == "/write":
            values = parse_qs(parsed.query).get("draftId", [])
            if wanted in values:
                return value
        return None
    allowed_hosts = DRAFT_URL_HOSTS.get(platform, ())
    if allowed_hosts and any(host == h or host.endswith(f".{h}") for h in allowed_hosts):
        if wanted in value:
            return value
    return None


def _official_api_credentials(acct: PublishAccountORM) -> Optional[dict]:
    if acct.auth_type != "official_api":
        return None
    data = json.loads(decrypt_required(acct.credentials_enc or ""))
    if not isinstance(data, dict):
        raise ValueError("credentials_enc is not a JSON object")
    appid = str(data.get("appid") or "").strip()
    appsecret = str(data.get("appsecret") or "").strip()
    if not appid or not appsecret:
        raise ValueError("missing appid/appsecret")
    return {"appid": appid, "appsecret": appsecret}

# ── Request bodies ──────────────────────────────────────────────────────────


class CreateTasksIn(BaseModel):
    doc_id: int
    account_db_ids: list[int] = Field(..., min_length=1, max_length=50)
    planned_publish_at: Optional[datetime] = None


class PreviewIn(BaseModel):
    doc_id: int
    platforms: list[str] = Field(default_factory=list,
                                 description="平台 code 列表;留空 = 所有启用的平台")
    # The body may be long; full text by default (for admin viewing); pass a cap for list pages.
    body_max_chars: int = Field(0, ge=0, le=200_000, description="0 = 不截")


class ClaimIn(BaseModel):
    # The worker's stable numeric id. Same as the engine pool: the worker computes crc32(WORKER_UID) & 0x7FFFFFFF
    # (see workers/service-cn/app/session_store.py:62-64); both sides must use the same method.
    worker_id: int
    platforms: list[str] = Field(default_factory=list, description="留空 = 不限平台")
    # Platform-level account canary. When the key exists, only the listed publish_accounts.id may be claimed; an empty list means the
    # platform claims nothing this round. Without the key, the original "any account" semantics apply.
    account_ids_by_platform: dict[str, list[int]] = Field(default_factory=dict)
    max_tasks: int = Field(1, ge=1, le=5)


class VerificationClaimIn(BaseModel):
    worker_id: int
    max_tasks: int = Field(1, ge=1, le=5)


class VerificationReportIn(BaseModel):
    task_id: int
    account_db_id: int
    worker_id: int
    claim_token: str = Field(..., min_length=16, max_length=200)
    status: str = Field(..., pattern="^(reviewing|published|rejected|draft|not_found|query_error)$")
    raw_status: Optional[int] = None
    audit_fail_reason: Optional[str] = Field(None, max_length=2000)
    publish_time: Optional[str] = Field(None, max_length=100)
    observed_at: Optional[str] = Field(None, max_length=100)
    error_message: Optional[str] = Field(None, max_length=2000)
    exit_ip: Optional[str] = Field(None, max_length=64)
    observed_account_id: Optional[str] = Field(None, max_length=256)
    storage_state: Optional[dict] = None


class ReportIn(BaseModel):
    dispatch_id: int
    worker_id: int
    result: PublishFailureType = PublishFailureType.SUCCESS
    error: Optional[str] = None
    published_url: Optional[str] = None
    # The **observation source** of published_url, provided by the adapter (browser-service publishers/base.py).
    #   url_redirect           the address bar redirects to the article itself after publishing — authoritative signal
    #   creator_center_anchor  after waiting for the URL times out, the anchor href read from the creator center list
    #   publish_response       public URL returned directly by the platform publish API and validated against platform rules
    #   public_page_probe      final URL actually responded after a read-only probe of the candidate public page
    #   official_api           public URL returned by the WeChat official API freepublish/get
    #   wechat_draft_id        WeChat web-login draft saved successfully, only the draft ID is returned
    # Written into proof so "on what basis this URL counted" can be checked later: the sources look identical in the DB,
    # and without recording it you can only dig through logs. Empty means an old worker version or the platform adapter has not implemented it.
    url_source: Optional[str] = None
    draft_id: Optional[Union[str, int]] = None
    draft_url: Optional[str] = Field(None, max_length=1024)
    screenshot_path: Optional[str] = None
    # 🔴 Required (business-wise): the worker must report the actual egress IP of this run. A mismatch with the DB is exit_ip_drift.
    # Nullable in type for compatibility with "resolution failed" — that case is treated as "unknown", not drift
    # (treating resolution failure as drift would stop good accounts on a single network glitch).
    exit_ip: Optional[str] = None
    # Rolling renewal: on success, return the storage_state refreshed by active use
    storage_state: Optional[dict] = None
    observed_account_id: Optional[str] = None
    rate_limited_until: Optional[float] = Field(None, description="unix 秒")
    failure_phase: Optional[str] = Field(
        None, pattern="^(prepare|pre_commit|commit|post_commit|platform_rejected)$",
        description="失败发生阶段；成功时留空")
    confirmed_not_published: bool = Field(
        False, description="执行器明确确认平台未发布；用于安全释放配额")

    @field_validator("draft_id", mode="before")
    @classmethod
    def _coerce_draft_id(cls, value):
        if value is None:
            return None
        return str(value)


class ProgressIn(BaseModel):
    """Persist non-reconstructible draft evidence before the real submit."""
    dispatch_id: int
    worker_id: int
    draft_id: Union[str, int] = Field(..., min_length=1, max_length=256)
    draft_url: Optional[str] = Field(None, max_length=1024)
    screenshot_path: Optional[str] = Field(None, max_length=1000)

    @field_validator("draft_id", mode="before")
    @classmethod
    def _coerce_draft_id(cls, value):
        if value is None:
            return None
        return str(value)


class ResolveStuckIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)
    confirmed_not_published: bool = False


def _normalize_report_result(payload: ReportIn, notes: list[str]) -> PublishFailureType:
    """A URL is stronger evidence: with a candidate URL, let the candidate probing pipeline take over."""
    result = payload.result
    if result == PublishFailureType.SUBMIT_UNKNOWN and (payload.published_url or "").strip():
        notes.append("回执带 published_url — 按候选 URL 处理")
        return PublishFailureType.SUCCESS
    return result


_QUEUE_FILTERS = {"pending", "running", "pending_url", "published", "draft_saved",
                  "url_submitted", "failed", "abandoned", "cancelled"}


def _beijing_day_bounds(value: Optional[str]) -> tuple[str, datetime, datetime]:
    day_text = (value or local_today()).strip()
    try:
        day = date_type.fromisoformat(day_text)
    except ValueError:
        raise HTTPException(422, "date 必须是 YYYY-MM-DD")
    start = datetime.combine(day, time_type.min) - timedelta(hours=8)
    return day.isoformat(), start, start + timedelta(days=1)


def _queue_effective_status(dispatch, task) -> str:
    if task.status == "cancelled":
        return "cancelled"
    if task.status == "published" and (task.published_url or "").strip():
        return "published"
    if task.status == "draft_saved":
        return "draft_saved"
    if dispatch.status in ("claimed", "running"):
        return "running"
    if dispatch.status == "queued":
        return "pending"
    if dispatch.status == "done" and task.status == "pending_url":
        return "pending_url"
    if dispatch.status == "done" and task.status == "url_submitted":
        return "url_submitted"
    if dispatch.status == "failed" or task.status == "failed":
        return "failed"
    if dispatch.status == "abandoned":
        return "abandoned"
    return "pending_url" if dispatch.status == "done" else "failed"


def _queue_link(dispatch, task, effective_status: str) -> tuple[Optional[str], Optional[str]]:
    if effective_status == "published" and (task.published_url or "").strip():
        return "published", task.published_url.strip()
    if effective_status == "draft_saved":
        meta = _channel_meta(task)
        url = str(meta.get("draft_url") or dispatch.draft_url or "").strip()
        if url:
            return "draft", url
    if effective_status == "url_submitted":
        meta = _channel_meta(task)
        url = str((meta.get("url_verification") or {}).get("candidate_url")
                  or meta.get("candidate_url") or "").strip()
        if url:
            return "candidate", url
    if effective_status in ("failed", "abandoned") and (dispatch.draft_url or "").strip():
        return "draft", dispatch.draft_url.strip()
    return None, None


def _queue_status_condition(status: str):
    d, t = PublishDispatchTaskORM, TopicDocPublishTaskORM
    not_business_final = and_(
        t.status != "cancelled",
        t.status != "draft_saved",
        or_(t.status != "published", t.published_url.is_(None), t.published_url == ""),
    )
    conditions = {
        "pending": and_(d.status == "queued", not_business_final),
        "running": and_(d.status.in_(("claimed", "running")), not_business_final),
        "pending_url": and_(d.status == "done", t.status == "pending_url"),
        "url_submitted": and_(d.status == "done", t.status == "url_submitted"),
        "draft_saved": t.status == "draft_saved",
        "published": and_(t.status == "published", t.published_url.isnot(None),
                          t.published_url != ""),
        "failed": and_(or_(d.status == "failed", t.status == "failed"),
                       not_business_final),
        "abandoned": and_(d.status == "abandoned", not_business_final),
        "cancelled": t.status == "cancelled",
    }
    return conditions[status]


@router.get("/publish-dispatch/tasks")
def list_dispatch_tasks(
    day: Optional[str] = Query(None, alias="date"),
    platform: Optional[str] = Query(None, max_length=64),
    status: Optional[str] = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Account center queue for the day; dates use the scheduled time, falling back to queue creation time when unscheduled."""
    day_text, start_utc, end_utc = _beijing_day_bounds(day)
    if status and status not in _QUEUE_FILTERS:
        raise HTTPException(422, f"status 必须是 {sorted(_QUEUE_FILTERS)} 之一")

    effective_at = func.coalesce(
        TopicDocPublishTaskORM.planned_publish_at,
        PublishDispatchTaskORM.created_at,
    )
    q = (db.query(PublishDispatchTaskORM, TopicDocPublishTaskORM,
                  PublishAccountORM, TopicGeneratedDocORM, PublishPlatformORM)
           .join(TopicDocPublishTaskORM,
                 TopicDocPublishTaskORM.id == PublishDispatchTaskORM.task_id)
           .join(PublishAccountORM,
                 PublishAccountORM.id == PublishDispatchTaskORM.account_db_id)
           .join(TopicGeneratedDocORM,
                 TopicGeneratedDocORM.id == TopicDocPublishTaskORM.doc_id)
           .join(AiTelemetryTopicORM,
                 AiTelemetryTopicORM.id == TopicGeneratedDocORM.topic_id)
           .outerjoin(PublishPlatformORM,
                      PublishPlatformORM.code == PublishDispatchTaskORM.platform)
           .filter(PublishAccountORM.channel == CHANNEL_SERVER_POOL,
                   effective_at >= start_utc, effective_at < end_utc))
    q = apply_enterprise_scope(q, db, current_user, AiTelemetryTopicORM.user_id)
    if platform:
        q = q.filter(PublishDispatchTaskORM.platform == platform)
    if status:
        q = q.filter(_queue_status_condition(status))

    total = q.count()
    rows = (q.order_by(effective_at.asc(), PublishDispatchTaskORM.id.desc())
              .offset((page - 1) * page_size).limit(page_size).all())
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=CLAIM_STALE_MINUTES)
    items = []
    for dispatch, task, account, doc, platform_row in rows:
        effective_status = _queue_effective_status(dispatch, task)
        link_type, link_url = _queue_link(dispatch, task, effective_status)
        stale_running = (
            effective_status == "running"
            and (dispatch.claimed_at is None or dispatch.claimed_at < stale_cutoff)
        )
        items.append({
            "dispatch_id": dispatch.id,
            "task_id": task.id,
            "doc_id": doc.id,
            "doc_title": doc.title,
            "platform": dispatch.platform,
            "platform_name": (platform_row.name_zh if platform_row else None)
                             or dispatch.platform,
            "account_db_id": account.id,
            "account_handle": account.account_handle,
            "status": effective_status,
            "dispatch_status": dispatch.status,
            "business_status": task.status,
            "planned_publish_at": (task.planned_publish_at.isoformat()
                                   if task.planned_publish_at else None),
            "created_at": dispatch.created_at.isoformat(),
            "claimed_at": (dispatch.claimed_at.isoformat()
                           if dispatch.claimed_at else None),
            "finished_at": (dispatch.finished_at.isoformat()
                            if dispatch.finished_at else None),
            "last_error": dispatch.last_error or task.last_error,
            "link_type": link_type,
            "link_url": link_url,
            "draft_id": dispatch.draft_id,
            "stuck_candidate": stale_running,
        })
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "date": day_text,
            "claim_stale_minutes": CLAIM_STALE_MINUTES}


# ── Render preview (admin side, pure function, no network) ─────────────────────────────────────


@router.post("/publish-dispatch/preview")
def preview_render(payload: PreviewIn, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """One draft → what the body **actually sent** to each platform looks like. No network, no DB writes, no quota consumed.

    It exists because of a specific blind spot: the same markdown is rendered per `publish_platforms.content_format`
    into three forms (Zhihu markdown / WeChat official account html / Xiaohongshu plain), and the frontend previously **could not see**
    any of them — ops could only publish blind and found out the layout broke after it went out. The duplicate-title defect
    (2026-08-14, doc 81) leaked to production exactly this way: the defect was in the render layer, which had no outlet.

    🔴 Deliberately **no** second renderer here: it calls the same `render_for_publish` used by claim.
    The frontend already has marked/turndown (RichTextBodyEditor), but that is a two-way bridge for "human editing",
    unaware of the target platform (decided only at claim, multi-platform posting) and without platform limits/image extraction/rejection semantics.
    For the preview to be meaningful it must be **the very render that will be sent**, so the frontend reuses the backend instead.

    `render.sha256` uses the same algorithm as what claim stores in `channel_meta.render` —
    if the preview hash matches a published task's hash, the draft has not changed since that publish.
    """
    doc = db.get(TopicGeneratedDocORM, payload.doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, current_user, doc, ACTION_WRITE)

    q = db.query(PublishPlatformORM)
    if payload.platforms:
        q = q.filter(PublishPlatformORM.code.in_(payload.platforms))
    else:
        q = q.filter(PublishPlatformORM.enabled.is_(True))
    rows = q.order_by(PublishPlatformORM.sort_order, PublishPlatformORM.code).all()
    if not rows:
        raise HTTPException(404, "没有匹配的平台配置行(publish_platforms)")

    out = []
    for row in rows:
        r = render_for_publish(doc, row)
        body = r.body
        truncated = False
        if payload.body_max_chars and len(body) > payload.body_max_chars:
            body, truncated = body[:payload.body_max_chars], True
        out.append({
            "platform": row.code,
            "platform_name": row.name_zh,
            "content_format": row.content_format,
            "title": r.title,
            "body": body,
            "body_format": r.body_format,
            "image_urls": r.image_urls,
            "cover_url": r.cover_url,
            # 🔴 A reject_reason means this platform **cannot publish it**, and claim will use it to
            # mark the task failed directly (penalize content, not account). It must be prominent in the preview.
            "reject_reason": r.reject_reason,
            "warnings": r.warnings,
            "limits": {"title": row.title_limit, "body": row.body_limit,
                       "image_max": row.image_max, "needs_cover": row.needs_cover},
            # The fingerprint is computed on the **untruncated** body — it must match channel_meta.render.
            "render": render_fingerprint(r),
            "body_truncated_for_preview": truncated,
        })
    return {
        "doc": {"id": doc.id, "title": doc.title, "status": doc.status,
                "markdown_len": len(doc.body_markdown or "")},
        "previews": out,
    }


# ── Publishable targets (admin side, data source for the unified publish drawer) ───────────────────────────


@router.get("/publish-dispatch/targets")
def publishable_targets(
    doc_id: int = Query(..., description="topic_generated_docs.id"),
    platforms: Optional[str] = Query(None, description="逗号分隔的平台 code;留空 = 全部"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """One draft → all publishable targets **visible to the backend** (per account), each with the platform's preflight verdict for this draft.

    Data source for the unified publish drawer (step 8 / F8): ops only tick "which accounts to publish to"; the channel is decided by the account's
    `channel` field, invisible to ops.

    ## 🔴 This endpoint is **not** the full set of "all selectable accounts"

    It only returns the `server_pool` + `aggregator` channels. `plugin` channel accounts **exist only in the browser extension on the ops
    machine** (`api.getAccounts()`, not via the backend), which the backend cannot see — so "one endpoint
    returns all selectable accounts" is architecturally impossible, and merging the three sources must happen in the frontend. The frontend merges this endpoint's results with
    the extension's accounts into one table; when the extension is offline that group shows "extension not connected" rather than disappearing.

    Registered rows with `channel='plugin'` in the DB are **not** returned either: those rows are ledger entries, and whether one "can publish now"
    is decided by the extension on the ops machine. Returning both would list the same account twice.

    ## queueable vs schedulable

    `queueable` only excludes hard failures such as invalid accounts; leases, cooldowns, rate limits and temporary disabling only make
    `schedulable=false` and do not block enqueueing. So submissions can continue while accounts are busy.

    `server_pool` uses `account_schedulable()` — **the same function** as claim's in-lock criterion.
    This is the main risk of this endpoint: if the two criteria drift, the UI shows "publishable" while the worker never
    claims it, and ops see "the system is broken" without any error (see that function's docstring).
    Case A of `verify_publish_targets.py` asserts per account that the two agree.

    `aggregator` **cannot** use that function (its first line rejects by channel, reasoning "not scheduled by the self-hosted account
    pool" — correct for claim, but moved to the UI it becomes the lie "aggregator accounts can never publish"). Whether the aggregator channel
    can really publish is decided on the Aitoearn side; the backend can only judge what we record ourselves: account status + daily quota.

    ## preflight

    Calls `render_for_publish(doc, platform_row)` directly — the same one used by claim and by
    `POST /publish-dispatch/preview`. This endpoint is the preview expanded per **account**.
    🔴 The frontend must make accounts with a non-empty `reject_reason` unselectable: otherwise ticking them still creates tasks,
    and only at claim is the content judged rejected — a wasted round trip.
    """
    doc = db.get(TopicGeneratedDocORM, doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, current_user, doc, ACTION_WRITE)

    wanted = {p.strip() for p in (platforms or "").split(",") if p.strip()}
    now, today = datetime.utcnow(), local_today()

    q = db.query(PublishAccountORM).filter(
        PublishAccountORM.channel.in_((CHANNEL_SERVER_POOL, CHANNEL_AGGREGATOR)),
        # retired = manually taken offline (usually channel migration), should not appear in the selection list. Other "bad" states
        # (quarantined / unschedulable / expired) are returned as usual — hiding them leaves ops not knowing why the pool
        # is empty, only assuming the system has no accounts.
        PublishAccountORM.status != "retired",
    )
    if wanted:
        q = q.filter(PublishAccountORM.platform.in_(sorted(wanted)))
    accounts = q.order_by(PublishAccountORM.platform.asc(),
                          PublishAccountORM.priority.asc(),
                          PublishAccountORM.id.asc()).all()

    # Platform rows and render results are cached per platform: with N accounts on one platform, rendering happens once
    # (the publish body only depends on draft × platform, not the account).
    plat_rows: dict[str, Optional[PublishPlatformORM]] = {}
    preflights: dict[tuple[str, bool], dict] = {}

    def _preflight(platform: str,
                   allow_missing_cover: bool = False) -> tuple[Optional[PublishPlatformORM], dict]:
        key = (platform, allow_missing_cover)
        if key not in preflights:
            row = (db.query(PublishPlatformORM)
                     .filter(PublishPlatformORM.code == platform).one_or_none())
            r = render_for_publish(doc, row, allow_missing_cover=allow_missing_cover)
            plat_rows[platform] = row
            preflights[key] = {
                "reject_reason": r.reject_reason,
                "warnings": list(r.warnings or []),
                "title_len": len(r.title or ""),
                "body_len": len(r.body or ""),
                "body_format": r.body_format,
                "images": len(r.image_urls or []),
                "has_cover": bool(r.cover_url),
            }
        return plat_rows[platform], preflights[key]

    targets = []
    for acct in accounts:
        allow_missing_cover = (
            acct.channel == CHANNEL_SERVER_POOL
            and acct.platform == "wxgzh"
            and acct.auth_type != "official_api"
        )
        plat_row, pf = _preflight(acct.platform, allow_missing_cover)
        if acct.channel == CHANNEL_SERVER_POOL:
            ok, why = account_schedulable(db, acct, now, today)
            queueable, queue_why = account_queueable(acct, now)
        else:
            ok, why = _aggregator_schedulable(db, acct, now, today)
            # The aggregator channel has no backend wait queue; "temporarily unpublishable" must not be disguised as "queued".
            queueable, queue_why = ok, why
        cap = effective_cap(acct, now)
        used = acct.used_today if acct.used_date == today else 0
        targets.append({
            "account_db_id": acct.id,
            "channel": acct.channel,
            "platform": acct.platform,
            "platform_name": (plat_row.name_zh if plat_row else "") or acct.platform,
            "platform_enabled": bool(plat_row.enabled) if plat_row else False,
            "account_handle": acct.account_handle or acct.account_id or f"#{acct.id}",
            # 🔴 What identifier the channel's **executor** uses for this account. account_handle is for display, this is for submission —
            # deliberately separate, otherwise changing display copy would change the target account of submission.
            #   server_pool  None (the executor is our worker; the task passes account_db_id)
            #   aggregator   Aitoearn-side account_id. Filled into account_handle by ops at registration
            #                (`_register_non_server_pool`: "account_id is provided by the caller via
            #                account_handle"), so the same value is returned here.
            "channel_ref": (None if acct.channel == CHANNEL_SERVER_POOL
                            else (acct.account_handle or "")),
            "effective_status": effective_status(acct, now),
            "schedulable": ok,
            "unschedulable_reason": why,
            "queueable": queueable,
            "queue_block_reason": queue_why,
            "remaining_today": None if cap == 0 else max(0, cap - used),
            "daily_cap": None if cap == 0 else cap,
            "preflight": pf,
        })

    return {
        "doc": {"id": doc.id, "title": doc.title, "status": doc.status,
                "markdown_len": len(doc.body_markdown or "")},
        "targets": targets,
        # 🔴 Tell the frontend explicitly "for the plugin channel ask the extension yourself", instead of letting it guess from an empty result.
        "channels_covered": [CHANNEL_SERVER_POOL, CHANNEL_AGGREGATOR],
        "plugin_note": "plugin 通道的账号只存在于运营本机浏览器插件,后端探不到,"
                       "需前端调 api.getAccounts() 合并",
    }


def _aggregator_schedulable(db: Session, acct: PublishAccountORM, now: datetime,
                            today: str) -> tuple[bool, Optional[str]]:
    """Can an aggregator-channel account publish right now? **Deliberately does not reuse `account_schedulable`.**

    `account_schedulable` encodes **claim's criterion**; its first line rejects by channel
    ("the aggregator channel is not scheduled by the self-hosted pool") — fully correct for claim, but moved straight to the UI it becomes
    the lie "all aggregator accounts can never publish".

    The authority on whether an aggregator account "can publish" is the Aitoearn side (whether the authorization is still valid, its own rate limits), which the backend
    cannot see; here we only judge what our own DB records, leaving the rest to real errors at publish time.
    Deliberately **does not check** worker ownership / lease / min_gap — those are constraints of self-hosted browser automation;
    the aggregator channel has no such concepts, and applying them would block accounts that can publish.
    """
    st = effective_status(acct, now)
    if st not in ("active", "warming"):
        return False, f"账号状态 {st}(聚合通道,授权状态以 Aitoearn 侧为准)"
    if not _quota_ok(db, acct, now, today):
        return False, f"今日发布配额已用满({effective_cap(acct, now)} 篇/天,含在途)"
    return True, None


def account_queueable(acct: PublishAccountORM, now: datetime) -> tuple[bool, Optional[str]]:
    """Can the account accept queued tasks? Busy/cooldown/rate limits only affect claiming, not enqueueing."""
    if acct.channel != CHANNEL_SERVER_POOL:
        return False, f"{acct.channel} 通道不走自建号池队列"
    if acct.status not in ("active", "warming"):
        return False, f"账号状态 {acct.status}"
    if acct.expires_at and acct.expires_at <= now:
        return False, f"登录态已过期({acct.expires_at.isoformat(timespec='seconds')})"
    return True, None


# ── Create tasks (admin side) ─────────────────────────────────────────────────


@router.post("/publish-dispatch/tasks", status_code=201)
def create_dispatch_tasks(payload: CreateTasksIn, db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    """One draft × N accounts → N independent tasks (F8: partial success/failure allowed).

    Idempotency relies on the partial unique index on topic_doc_publish_tasks
    (doc_id, platform, publish_account_id) WHERE publish_account_id IS NOT NULL:
    Repeated clicks **reuse the same task** instead of creating a second one (F5 item 1).
    """
    doc = db.get(TopicGeneratedDocORM, payload.doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, current_user, doc, ACTION_WRITE)
    if doc.status not in ("approved", "published"):
        # Same threshold as the aitoearn channel: this module's input is "approved drafts".
        raise HTTPException(409, f"稿件状态 {doc.status} 未过审,不能发布")
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if topic is None:
        raise HTTPException(404, f"topic id={doc.topic_id} not found")

    _, operation = create_resource_operation_context(
        db, current_user, doc,
        action="publish.dispatch.create", target_resource_type="document",
    )
    append_system_audit(
        db, operation_context=operation, event_type="task_created",
        action="publish.dispatch.create", object_type="document",
        object_id=doc.id, result="success",
    )

    now = datetime.utcnow()
    results = []
    for account_db_id in payload.account_db_ids:
        acct = db.get(PublishAccountORM, account_db_id)
        if acct is None:
            results.append({"account_db_id": account_db_id, "action": "skip",
                            "detail": "账号不存在"})
            continue
        if acct.channel != CHANNEL_SERVER_POOL:
            # Aggregator/plugin channel accounts do not enter this queue — each uses its own channel (aitoearn_channel /
            # browser extension). Hard-binding channel exists to make this check hold.
            results.append({"account_db_id": account_db_id, "action": "skip",
                            "detail": f"{acct.channel} 通道不走自建号池队列"})
            continue
        queueable, why = account_queueable(acct, now)
        if not queueable:
            results.append({"account_db_id": account_db_id, "action": "skip",
                            "detail": why or "账号不可排队"})
            continue
        results.append(_create_one(
            db, doc, topic, acct, payload.planned_publish_at, now, operation.id,
        ))
    db.commit()
    log.info("[publish-dispatch] created doc=%s by=%s results=%s",
             payload.doc_id, getattr(current_user, "id", None),
             [r["action"] for r in results])
    return {"doc_id": payload.doc_id, "results": results}


def _create_one(db: Session, doc, topic, acct: PublishAccountORM,
                planned_at: Optional[datetime], now: datetime,
                operation_context_id: Optional[int] = None) -> dict:
    ctrip_location = _ctrip_location(topic) if acct.platform == "ctrip" else None
    ctrip_topic = _ctrip_topic(topic) if acct.platform == "ctrip" else None
    if acct.platform == "ctrip" and ctrip_location is None:
        return {"account_db_id": acct.id, "action": "skip",
                "detail": "请先在品牌资料填写携程发布地点"}
    task = (db.query(TopicDocPublishTaskORM)
              .filter(TopicDocPublishTaskORM.doc_id == doc.id,
                      TopicDocPublishTaskORM.platform == acct.platform,
                      TopicDocPublishTaskORM.publish_account_id == acct.id)
              .one_or_none())
    reused_task = task is not None
    if task is None:
        task = TopicDocPublishTaskORM(
            topic_id=topic.id, doc_id=doc.id, vscl_action_id=doc.vscl_action_id,
            platform=acct.platform,
            media_name=(acct.account_handle or acct.account_id or "")[:128],
            channel_type="auto_account", publish_mode="auto",
            status="planned", planned_publish_at=planned_at,
            operation_context_id=operation_context_id,
            publish_account_id=acct.id,
            channel_meta_json=json.dumps(
                {"channel": CHANNEL_SERVER_POOL, "account_db_id": acct.id,
                 "account_id": acct.account_id,
                 **({"ctrip_location": ctrip_location} if ctrip_location else {}),
                 **({"ctrip_topic": ctrip_topic} if ctrip_topic else {})},
                ensure_ascii=False),
        )
        db.add(task)
        db.flush()          # Get the autoincrement id for the queue row below
    elif task.status == "published":
        return {"account_db_id": acct.id, "task_id": task.id, "action": "skip",
                "detail": "该账号已发过这篇稿"}

    dispatch = (db.query(PublishDispatchTaskORM)
                  .filter(PublishDispatchTaskORM.task_id == task.id,
                          PublishDispatchTaskORM.status.in_(
                              ("queued", "claimed", "running")))
                  .first())
    if dispatch is not None:
        return {"account_db_id": acct.id, "task_id": task.id,
                "dispatch_id": dispatch.id, "action": "reused",
                "detail": f"已有在途队列行(status={dispatch.status})"}

    if operation_context_id is not None and task.status in (
        "planned", "pending_publish", "failed", "cancelled",
    ):
        task.operation_context_id = operation_context_id

    # When a user chooses to publish a failed task again, the business task is reused and a clean queue row is created.
    # Also clear the previous round's terminal display, otherwise the new queue is already queued while the publish center still shows the old failed.
    if reused_task and task.status == "failed":
        task.status = "planned"
        task.last_error = None

    cmeta = _channel_meta(task)
    cmeta.update(
        channel=CHANNEL_SERVER_POOL,
        account_db_id=acct.id,
        account_id=acct.account_id,
    )
    if ctrip_location:
        cmeta["ctrip_location"] = ctrip_location
    if ctrip_topic:
        cmeta["ctrip_topic"] = ctrip_topic
    task.channel_meta_json = json.dumps(cmeta, ensure_ascii=False)

    dispatch = PublishDispatchTaskORM(
        task_id=task.id, platform=acct.platform, account_db_id=acct.id,
        operation_context_id=operation_context_id,
        status="queued", visible_after=planned_at or now,
    )
    db.add(dispatch)
    db.flush()
    return {"account_db_id": acct.id, "task_id": task.id, "dispatch_id": dispatch.id,
            "action": "reused_task" if reused_task else "created", "detail": ""}


# ── Claim (worker side) ────────────────────────────────────────────────


@service_router.post("/publish-dispatch/claim")
def claim(payload: ClaimIn, db: Session = Depends(get_db)):
    """Claim a task. On success returns the task + decrypted login session + the proxy bound to the account.

    When nothing can be claimed, returns **404** with the fixed message prefix `no publishable account available`.
    The 404 is part of the protocol, not an error: the task's visible_after has been pushed back and attempt not incremented —
    no retry budget consumed (F4 acceptance: two workers concurrently claiming the same account, only one gets it,
    the other gets 404 and the task goes back to the queue).
    """
    now = datetime.utcnow()
    today = local_today()
    out = []
    for _ in range(payload.max_tasks):
        got = _claim_one(db, payload, now, today)
        if got is None:
            break
        out.append(got)
    if not out:
        raise HTTPException(404, f"{NO_ACCOUNT_PREFIX} for worker={payload.worker_id}")
    return {"tasks": out}


@service_router.post("/publish-dispatch/verification/claim")
def claim_verifications(payload: VerificationClaimIn, db: Session = Depends(get_db)):
    """Claim due Ctrip review status checks. Only takes the account lease, does not consume publishing quota."""
    now = datetime.utcnow()
    out = []
    for _ in range(payload.max_tasks):
        item = _claim_one_verification(db, payload.worker_id, now)
        if item is None:
            break
        out.append(item)
    if not out:
        raise HTTPException(404, f"no ctrip verification due for worker={payload.worker_id}")
    return {"tasks": out}


def _verification_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# Max candidates examined per verification claim. A defensive upper bound: if all url_submitted Ctrip tasks are
# metadata-invalid / not yet due, do not scan the whole table looking for a claimable one.
# (80b643ed removed the original same-named constant when changing publish claim to while True, but it is still used here —
#  after its removal, /verification/claim raised NameError 500 every time.)
_MAX_CANDIDATES = 20


def _claim_one_verification(db: Session, worker_id: int,
                            now: datetime) -> Optional[dict]:
    skipped: set[int] = set()
    for _ in range(_MAX_CANDIDATES):
        q = (db.query(TopicDocPublishTaskORM, PublishAccountORM)
               .join(PublishAccountORM,
                     PublishAccountORM.id == TopicDocPublishTaskORM.publish_account_id)
               .with_for_update(skip_locked=True, of=TopicDocPublishTaskORM)
               .filter(
                   TopicDocPublishTaskORM.status == "url_submitted",
                   TopicDocPublishTaskORM.platform == "ctrip",
                   PublishAccountORM.channel == CHANNEL_SERVER_POOL,
                   PublishAccountORM.owner_worker_id == worker_id,
                   PublishAccountORM.status.in_(("active", "warming")),
                   or_(PublishAccountORM.expires_at.is_(None),
                       PublishAccountORM.expires_at > now),
                   or_(PublishAccountORM.lease_until.is_(None),
                       PublishAccountORM.lease_until <= now),
               ))
        if skipped:
            q = q.filter(TopicDocPublishTaskORM.id.notin_(sorted(skipped)))
        row = q.order_by(TopicDocPublishTaskORM.id.asc()).first()
        if row is None:
            return None
        task, acct = row
        meta = _channel_meta(task)
        state = meta.get("url_verification")
        if not isinstance(state, dict) \
                or state.get("verification_method") != "platform_api" \
                or state.get("verification_executor") != "server_pool" \
                or state.get("platform_code") != "ctrip" \
                or not state.get("external_post_id"):
            skipped.add(task.id)
            db.rollback()
            continue
        next_at = _verification_time(state.get("next_at"))
        claim = state.get("claim") if isinstance(state.get("claim"), dict) else {}
        claim_expires = _verification_time(claim.get("expires_at"))
        if (next_at and next_at > now) or (claim_expires and claim_expires > now):
            skipped.add(task.id)
            db.rollback()
            continue

        lock_account(db, acct.id)
        db.refresh(acct)
        if acct.owner_worker_id != worker_id or acct.status not in ("active", "warming") \
                or (acct.lease_until and acct.lease_until > now):
            skipped.add(task.id)
            db.rollback()
            continue
        proxy_url = resolve_proxy_url(db, acct.proxy_id)
        if not proxy_url and acct.egress != EGRESS_HOST:
            state.update({"next_at": (now + timedelta(minutes=10)).isoformat(),
                          "last_error": "账号绑定的代理不可用"})
            meta["url_verification"] = state
            task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            skipped.add(task.id)
            continue
        try:
            storage_state = json.loads(decrypt_required(acct.storage_state_enc or ""))
        except Exception as exc:  # noqa: BLE001
            acct.status = "quarantined"
            acct.last_fail_type = "login_lost"
            acct.last_fail_reason = f"审核查询无法解密登录态:{exc}"[:1000]
            acct.last_fail_at = now
            state.update({"next_at": (now + timedelta(hours=1)).isoformat(),
                          "last_error": "携程登录态不可用"})
            meta["url_verification"] = state
            task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            skipped.add(task.id)
            continue

        token = secrets.token_urlsafe(24)
        lease_until = now + timedelta(minutes=LEASE_TTL_MINUTES)
        state["claim"] = {"token": token, "worker_id": worker_id,
                          "claimed_at": now.isoformat(),
                          "expires_at": lease_until.isoformat()}
        meta["url_verification"] = state
        task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
        acct.lease_worker_id = worker_id
        acct.lease_until = lease_until
        acct.updated_at = now
        result = {
            "task_id": task.id,
            "account_db_id": acct.id,
            "worker_id": worker_id,
            "claim_token": token,
            "platform": "ctrip",
            "external_post_id": str(state["external_post_id"]),
            "account_id": acct.account_id,
            "storage_state": storage_state,
            "proxy_url": proxy_url,
            "user_agent": acct.user_agent,
            "ua_platform": acct.ua_platform,
            "expected_exit_ip": acct.exit_ip,
            "lease_until": lease_until.isoformat(),
        }
        db.commit()
        return result
    return None


@service_router.post("/publish-dispatch/verification/report")
def report_verification(payload: VerificationReportIn, db: Session = Depends(get_db)):
    from geo.services import publish_task_service
    from geo.utils.publish_crypto import encrypt_required

    task = db.get(TopicDocPublishTaskORM, payload.task_id)
    if task is None or task.publish_account_id != payload.account_db_id:
        raise HTTPException(404, "verification task not found")
    acct = db.get(PublishAccountORM, payload.account_db_id)
    if acct is None:
        raise HTTPException(404, "publish account not found")
    meta = _channel_meta(task)
    state = meta.get("url_verification") if isinstance(meta.get("url_verification"), dict) else {}
    claim = state.get("claim") if isinstance(state.get("claim"), dict) else {}
    if claim.get("worker_id") != payload.worker_id \
            or not secrets.compare_digest(str(claim.get("token") or ""), payload.claim_token):
        raise HTTPException(409, "verification claim does not belong to this worker")
    if (_verification_time(claim.get("expires_at")) or datetime.min) <= datetime.utcnow():
        raise HTTPException(409, "verification claim has expired")
    if acct.owner_worker_id != payload.worker_id:
        raise HTTPException(409, "account does not belong to this worker")

    now = datetime.utcnow()
    if payload.observed_account_id and payload.observed_account_id != acct.account_id:
        acct.status = "unschedulable"
        acct.last_fail_type = "identity_mismatch"
        acct.last_fail_reason = "审核查询观测到的携程账号与绑定账号不一致"
        acct.last_fail_at = now
        acct.lease_worker_id = None
        acct.lease_until = None
        db.commit()
        raise HTTPException(409, "observed Ctrip account identity mismatch")
    if payload.exit_ip and acct.exit_ip and payload.exit_ip != acct.exit_ip:
        acct.status = "unschedulable"
        acct.last_fail_type = "exit_ip_drift"
        acct.last_fail_reason = "携程审核查询的出口 IP 与账号绑定出口不一致"
        acct.last_fail_at = now
        acct.lease_worker_id = None
        acct.lease_until = None
        db.commit()
        raise HTTPException(409, "Ctrip verification exit IP drift")

    try:
        publish_task_service.report_platform_verification(
            db, task, platform_code="ctrip",
            external_post_id=str(state.get("external_post_id") or ""),
            status=payload.status, raw_status=payload.raw_status,
            audit_fail_reason=payload.audit_fail_reason or "",
            publish_time=payload.publish_time or "",
            observation_id=payload.claim_token,
            observed_at=payload.observed_at or "",
            error_message=payload.error_message or "",
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc

    refreshed = _channel_meta(task)
    refreshed_state = (refreshed.get("url_verification")
                       if isinstance(refreshed.get("url_verification"), dict) else {})
    refreshed_state.pop("claim", None)
    refreshed["url_verification"] = refreshed_state
    task.channel_meta_json = json.dumps(refreshed, ensure_ascii=False)
    if payload.storage_state and payload.storage_state.get("cookies"):
        acct.storage_state_enc = encrypt_required(
            json.dumps(payload.storage_state, ensure_ascii=False))
        acct.expires_at = now + timedelta(days=RENEW_TTL_DAYS)
    if payload.exit_ip and not acct.exit_ip:
        acct.exit_ip = payload.exit_ip
        acct.exit_ip_at = now
    acct.lease_worker_id = None
    acct.lease_until = None
    acct.updated_at = now
    db.commit()
    return {"ok": True, "task": publish_task_service.task_to_dict(task, db=db)}


def _candidate_query(db: Session, payload: ClaimIn, now: datetime, skip_ids: set[int]):
    earlier = aliased(PublishDispatchTaskORM)
    q = (db.query(PublishDispatchTaskORM, PublishAccountORM)
           .join(PublishAccountORM,
                 PublishAccountORM.id == PublishDispatchTaskORM.account_db_id)
           # Only lock queue rows (of=). Locking account rows would block the same account's other tasks, while account-level
           # mutual exclusion is handled by the advisory lock — that lock is **transaction-scoped**, better suited to holding across statements.
           .with_for_update(skip_locked=True, of=PublishDispatchTaskORM)
           .filter(
               PublishDispatchTaskORM.status == "queued",
               PublishDispatchTaskORM.visible_after <= now,
               PublishDispatchTaskORM.attempt < PublishDispatchTaskORM.max_attempts,
               PublishAccountORM.channel == CHANNEL_SERVER_POOL,
               PublishAccountORM.status.in_(("active", "warming")),
               # Bound accounts can only be claimed by their owner; unbound accounts may enter candidates, and within the account-level
               # advisory lock the first worker to **successfully claim** takes permanent ownership.
               # The lock ensures that two workers concurrently seeing the same unbound account do not double-bind it.
               or_(PublishAccountORM.owner_worker_id.is_(None),
                   PublishAccountORM.owner_worker_id == payload.worker_id),
               or_(PublishAccountORM.expires_at.is_(None),
                   PublishAccountORM.expires_at > now),
               or_(PublishAccountORM.rate_limited_until.is_(None),
                   PublishAccountORM.rate_limited_until <= now),
               or_(PublishAccountORM.unschedulable_until.is_(None),
                   PublishAccountORM.unschedulable_until <= now),
               # Lease not held by someone else (normally the holder is the owner itself)
               or_(PublishAccountORM.lease_until.is_(None),
                   PublishAccountORM.lease_until <= now),
               # With unlimited backlog, each account only exposes its earliest currently visible task. While a claimed/running
               # task exists, the next one may not jump ahead; it must first reach a terminal state via receipt or timeout sweep.
               ~db.query(earlier.id).filter(
                   earlier.account_db_id == PublishDispatchTaskORM.account_db_id,
                   or_(
                       earlier.status.in_(("claimed", "running")),
                       and_(
                           earlier.status == "queued",
                           earlier.visible_after <= now,
                           earlier.attempt < earlier.max_attempts,
                           earlier.id < PublishDispatchTaskORM.id,
                       ),
                   ),
               ).exists(),
           ))
    if payload.platforms:
        q = q.filter(PublishDispatchTaskORM.platform.in_(payload.platforms))
    for platform, account_ids in payload.account_ids_by_platform.items():
        code = (platform or "").strip()
        if not code:
            continue
        allowed = [int(value) for value in account_ids if int(value) > 0]
        q = q.filter(or_(PublishDispatchTaskORM.platform != code,
                         PublishAccountORM.id.in_(allowed) if allowed else False))
    if skip_ids:
        q = q.filter(PublishDispatchTaskORM.id.notin_(sorted(skip_ids)))
    # No rotation by use_count — publishing accounts should stay as stable as possible. Order only by account priority, then task id.
    return q.order_by(PublishAccountORM.priority.asc(), PublishDispatchTaskORM.id.asc())


def account_schedulable(db: Session, acct: PublishAccountORM, now: datetime, today: str,
                        *, worker_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
    """Can this account really take a publish right now? Returns (can_or_not, human-readable reason if not).

    🔴 **This is the single shared source of claim's in-lock criterion and the UI's "publishable/not publishable".**

    It exists because of a specific deceptive scenario: the publish drawer (step 8) shows "can publish" on each account row,
    while claim has its own filters. Two separate copies drift sooner or later — the UI shows "publishable",
    ops tick it, the task is created, and the worker never claims it (claim's conditions are stricter), leaving a task in the queue
    that never runs. Ops see "the system is broken" without any error.

    Therefore:
      · `_claim_one`'s in-lock re-read calls it (do **not** write the check again there);
      · `GET /publish-dispatch/targets` calls it to fill `schedulable` / `unschedulable_reason`.

    `worker_id`: when given, ownership is checked (the claim scenario — bound accounts must be claimed by their owner);
    when omitted, ownership is skipped (the UI scenario — the admin console asks "can this account publish", not
    "can my machine publish it"). Empty `owner_worker_id` means it will be bound automatically on the first successful claim.

    ⚠️ Only checks the **account side**. Queue-row conditions (status='queued' / visible_after / attempt cap)
    are each caller's responsibility — the UI side has no queue row yet.
    """
    if acct.channel != CHANNEL_SERVER_POOL:
        # Aggregator/plugin channels do not use this queue; their "can publish" is decided by their own services.
        return False, f"{acct.channel} 通道不由自建号池调度"
    if acct.status not in ("active", "warming"):
        return False, f"账号状态 {acct.status}"
    if worker_id is not None and acct.owner_worker_id is not None and \
            acct.owner_worker_id != worker_id:
        return False, (f"账号归属 worker {acct.owner_worker_id},"
                       f"当前 worker {worker_id} 不能代发")
    if acct.expires_at and acct.expires_at <= now:
        return False, f"登录态已过期({acct.expires_at.isoformat(timespec='seconds')})"
    if acct.rate_limited_until and acct.rate_limited_until > now:
        return False, (f"限流冷却中,"
                       f"{acct.rate_limited_until.isoformat(timespec='seconds')} 解封")
    if acct.unschedulable_until and acct.unschedulable_until > now:
        return False, (f"人工禁用中,"
                       f"{acct.unschedulable_until.isoformat(timespec='seconds')} 到期")
    if acct.lease_until and acct.lease_until > now:
        return False, f"正在发布中(租约至 {acct.lease_until.isoformat(timespec='seconds')})"
    cooldown_minutes = effective_cooldown_minutes(acct)
    if cooldown_minutes > 0 and acct.last_used_at and \
            acct.last_used_at > now - timedelta(minutes=cooldown_minutes):
        nxt = acct.last_used_at + timedelta(minutes=cooldown_minutes)
        return False, (f"距上次发布不足 {cooldown_minutes} 分钟,"
                       f"{nxt.isoformat(timespec='seconds')} 后可发")
    if not _quota_ok(db, acct, now, today):
        cap = effective_cap(acct, now)
        return False, f"今日发布配额已用满({cap} 篇/天,含在途)"
    return True, None


def _claim_one(db: Session, payload: ClaimIn, now: datetime, today: str) -> Optional[dict]:
    """Examine candidates until one is claimed or candidates are exhausted.

    🔴 **Each candidate is evaluated in its own transaction, one at a time (`.first()`).**
    The earlier version fetched a batch with `.limit(20).all()` and committed for individual candidates in the loop — that is wrong:
    commit releases both the FOR UPDATE row locks and the advisory lock, so **the remaining already-fetched candidate
    rows are no longer locked**; another worker can claim them while this loop keeps processing them → the same
    queue row claimed by two workers → the same article published twice on the same account (irreversible).

    Current shape: fetch one → take the account lock → re-read the account inside the lock → decide → commit/rollback to end the transaction
    → query again. `skip_ids` keeps soft-skipped candidates (quota full / interval not reached) from being fetched again,
    otherwise `.first()` would keep returning the same row, an infinite loop.
    """
    skip_ids: set[int] = set()
    while True:
        row = _candidate_query(db, payload, now, skip_ids).first()
        if row is None:
            return None
        dispatch, acct = row

        # Second safeguard: the account-level advisory lock. It must be taken **before** rechecking quota,
        # otherwise two claims would each read the same used_today and both pass (TOCTOU).
        lock_account(db, acct.id)
        # **Re-read** the account after taking the lock: the FOR UPDATE above only locked the queue row, not the account row,
        # and between SELECT and locking another worker may have just committed changes to used_today/lease/status.
        # Not re-reading means judging quota from a possibly stale snapshot.
        db.refresh(acct)
        # 🔴 The criterion is account_schedulable — the same function as the UI's "publishable/not publishable".
        # Do not write it again here: drift shows up as the UI saying publishable, the task created, the worker never claiming it,
        # and a never-executing task sitting in the queue without any error (see that function's docstring).
        # It already covers: status / ownership / expires_at / rate_limited / unschedulable /
        # lease / min_gap / daily quota (including in-flight).
        ok, why = account_schedulable(db, acct, now, today, worker_id=payload.worker_id)
        if not ok:
            # Re-read inside the lock found the account unavailable right now → drop this candidate, try the next
            log.debug("[publish-dispatch] dispatch=%s skipping account %s: %s",
                      dispatch.id, acct.id, why)
            skip_ids.add(dispatch.id)
            db.rollback()
            continue

        proxy_url = resolve_proxy_url(db, acct.proxy_id)
        host_egress_ok = False
        if not proxy_url:
            if acct.egress == EGRESS_HOST:
                log.warning("[publish-dispatch] account=%s dispatched via local direct egress exit_ip=%s",
                            acct.id, acct.exit_ip)
                host_egress_ok = True
        # 🔴 If the egress is gone (proxy disabled/deleted) we must not continue: this publish would use the worker's own
        # IP, i.e. a remote login. Stop the account for manual handling; do not degrade silently.
        # Accounts with host_egress_ok **deliberately** have no proxy; this does not apply to them.
        if not proxy_url and not host_egress_ok:
            acct.status = "unschedulable"
            acct.last_fail_type = "exit_ip_drift"
            acct.last_fail_at = now
            acct.updated_at = now
            _defer(dispatch, now, "账号绑定的代理不可用,已停号待人工处理")
            db.commit()
            log.error("[publish-dispatch] account=%s proxy_id=%s unavailable → "
                      "unschedulable", acct.id, acct.proxy_id)
            skip_ids.add(dispatch.id)
            continue

        try:
            state = json.loads(decrypt_required(acct.storage_state_enc or ""))
        except PublishCryptoUnavailable as e:
            # Key not configured = the whole pool is unusable, not this account's problem. Back off and retry, do not penalize the account,
            # and no need to look at other candidates (they would fail the same way).
            _defer(dispatch, now, str(e))
            db.commit()
            log.error("[publish-dispatch] %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            # Corrupted ciphertext (most common: PUBLISH_CRED_KEY was changed) → the login session must be re-imported.
            acct.status = "quarantined"
            acct.last_fail_type = "login_lost"
            acct.last_fail_at = now
            acct.updated_at = now
            _defer(dispatch, now, f"登录态解密失败:{e}")
            db.commit()
            log.error("[publish-dispatch] account=%s decrypt failed: %s", acct.id, e)
            skip_ids.add(dispatch.id)
            continue

        try:
            official_api = _official_api_credentials(acct)
        except PublishCryptoUnavailable as e:
            _defer(dispatch, now, str(e))
            db.commit()
            log.error("[publish-dispatch] %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            acct.status = "quarantined"
            acct.last_fail_type = "login_lost"
            acct.last_fail_at = now
            acct.updated_at = now
            _defer(dispatch, now, f"官方 API 凭据解密失败:{e}")
            db.commit()
            log.error("[publish-dispatch] account=%s official credentials failed: %s",
                      acct.id, e)
            skip_ids.add(dispatch.id)
            continue

        task = db.get(TopicDocPublishTaskORM, dispatch.task_id)
        if task is None:
            dispatch.status = "abandoned"
            dispatch.last_error = "业务任务已不存在"
            dispatch.finished_at = now
            db.commit()
            skip_ids.add(dispatch.id)
            continue

        operation = None
        context_id = dispatch.operation_context_id or task.operation_context_id
        if context_id is not None:
            from geo.models.organization import OperationContextORM
            operation = db.get(OperationContextORM, context_id)
            try:
                revalidate_operation_context(db, context_id)
            except Exception as exc:
                dispatch.status = "abandoned"
                dispatch.last_error = "authorization revoked"
                dispatch.finished_at = now
                task.status = "cancelled"
                task.last_error = "authorization revoked"
                append_system_audit(
                    db, operation_context=operation,
                    event_type="authorization_denied",
                    action="publish.dispatch.claim", object_type="publish_task",
                    object_id=task.id, result="denied", reason=str(exc),
                )
                db.commit()
                skip_ids.add(dispatch.id)
                continue

        doc = db.get(TopicGeneratedDocORM, task.doc_id)
        if doc is None:
            dispatch.status = "abandoned"
            dispatch.last_error = "稿件已不存在"
            dispatch.finished_at = now
            db.commit()
            skip_ids.add(dispatch.id)
            continue

        # ── Render the publish body. **Before changing any state** ────────────────────────
        # The body source is exactly the same as other channels: topic_generated_docs.title + body_markdown.
        # Content problems like empty body / missing cover should be blocked before going out — discovering them in the editor costs
        # a real login + an empty draft left on the platform, while this check costs nothing.
        # 🔴 Penalize content, not the account: set dispatch failed and max out attempt (will not be claimed again),
        # leaving the account itself completely untouched.
        platform_row = (db.query(PublishPlatformORM)
                          .filter(PublishPlatformORM.code == acct.platform)
                          .one_or_none())
        rendered = render_for_publish(
            doc,
            platform_row,
            allow_missing_cover=acct.platform == "wxgzh" and not official_api,
        )
        if rendered.reject_reason:
            dispatch.status = "failed"
            dispatch.attempt = dispatch.max_attempts
            dispatch.last_error = f"内容不满足平台要求:{rendered.reject_reason}"[:2000]
            dispatch.finished_at = now
            dispatch.updated_at = now
            task.status = "failed"
            task.last_error = dispatch.last_error
            db.commit()
            log.warning("[publish-dispatch] dispatch=%s content rejected (not sent): %s",
                        dispatch.id, rendered.reject_reason)
            skip_ids.add(dispatch.id)
            continue

        # ── Claim succeeded: persist "dispatched" evidence first, then return ──────────────────
        if acct.owner_worker_id is None:
            # First claim inside the advisory lock: automation only does the first binding; afterwards it is still a strong lifetime binding.
            # Worker retirement or migration still goes through admin rebind-worker; never switch machines automatically on lease expiry.
            acct.owner_worker_id = payload.worker_id
            log.info("[publish-dispatch] account=%s first claim, automatically bound to worker=%s",
                     acct.id, payload.worker_id)
        dispatch.status = "claimed"
        dispatch.attempt += 1
        dispatch.claimed_by_worker_id = payload.worker_id
        dispatch.claimed_at = now
        dispatch.last_error = None
        dispatch.updated_at = now
        acct.lease_worker_id = payload.worker_id
        acct.lease_until = now + timedelta(minutes=LEASE_TTL_MINUTES)
        acct.use_count += 1
        acct.last_used_at = now
        if acct.used_date != today:
            acct.used_date = today
            acct.used_today = 1
        else:
            acct.used_today += 1
        acct.updated_at = now
        # The business state machine enters publishing — persist "initiated" evidence before going out, same idea as the aitoearn channel
        # (step 4 of _publish_one in api/aitoearn_channel.py).
        if task.status in ("planned", "pending_publish", "failed"):
            task.status = "publishing"
        # Persist the fingerprint of this render. The title/body in out below are gone once sent, and the source draft is
        # overwritten in place — without a fingerprint, "what was the live article's content at the time" cannot be answered later.
        cmeta = _channel_meta(task)
        cmeta["render"] = render_fingerprint(rendered)
        task.channel_meta_json = json.dumps(cmeta, ensure_ascii=False)
        out = {
            "dispatch_id": dispatch.id,
            "task_id": task.id,
            "doc_id": task.doc_id,
            # The publish body ships with the task. The worker no longer reads the DB — it cannot reach the business DB, and
            # "the claimed task" and "the content sent" must come from the same render; otherwise if the draft changed between two renders
            # a body different from the reviewed content would be sent (drafts are overwritten in place,
            # see models/ai_telemetry.py:732).
            "title": rendered.title,
            "body": rendered.body,
            "body_format": rendered.body_format,
            "source_markdown": rendered.source_markdown,
            "images": list(rendered.image_urls or []),
            "location": cmeta.get("ctrip_location") if acct.platform == "ctrip" else None,
            "topics": cmeta.get("ctrip_topic") if acct.platform == "ctrip" else None,
            "render_warnings": rendered.warnings,
            "platform": acct.platform,
            "account_db_id": acct.id,
            "account_id": acct.account_id,
            "attempt": dispatch.attempt,
            "worker_id": payload.worker_id,
            "storage_state": state,
            "proxy_url": proxy_url,
            "ua_platform": acct.ua_platform,
            "user_agent": acct.user_agent,
            "expected_exit_ip": acct.exit_ip,
            "lease_until": acct.lease_until.isoformat(),
        }
        if official_api:
            out["official_api"] = official_api
        if operation is not None:
            append_system_audit(
                db,
                operation_context=operation,
                event_type="authorization_rechecked",
                action="publish.dispatch.claim",
                object_type="publish_task",
                object_id=task.id,
                result="success",
            )
            append_system_audit(
                db,
                operation_context=operation,
                event_type="task_started",
                action="publish.dispatch.claim",
                object_type="publish_task",
                object_id=task.id,
                result="success",
            )
        # The return value is assembled **before** commit: commit expires all ORM attributes,
        # and reading dispatch.id / acct.platform afterwards would trigger another SELECT (in a new transaction, without locks).
        db.commit()
        return out

    return None


def _ctrip_location(topic: AiTelemetryTopicORM) -> Optional[dict[str, str]]:
    """Take a Ctrip location snapshot from the topic profile; it no longer follows profile changes after task creation."""
    try:
        profile = json.loads(topic.profile_json or "{}")
    except Exception:  # noqa: BLE001
        return None
    raw = profile.get("ctrip_location") if isinstance(profile, dict) else None
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    city = str(raw.get("city") or "").strip()
    address = str(raw.get("address") or "").strip()
    return {"name": name, **({"city": city} if city else {}),
            **({"address": address} if address else {})}


def _ctrip_topic(topic: AiTelemetryTopicORM) -> Optional[str]:
    """Take a Ctrip topic snapshot from the brand profile's "name"."""
    try:
        profile = json.loads(topic.profile_json or "{}")
    except Exception:  # noqa: BLE001
        return None
    name = str(profile.get("profile_name") or "").strip() if isinstance(profile, dict) else ""
    return name or None


def _quota_ok(db: Session, acct: PublishAccountORM, now: datetime, today: str) -> bool:
    """Is there remaining daily quota? 0 = unlimited.

    🔴 **In-flight claimed tasks count toward the quota**, otherwise concurrency would over-publish: used_today is only +1 at claim,
    but a claimed task without a receipt already holds a slot; without counting it, multiple claims within the same second
    would all see the same used_today.
    """
    cap = effective_cap(acct, now)
    if cap == 0:
        return True
    used = acct.used_today if acct.used_date == today else 0
    inflight = (db.query(PublishDispatchTaskORM)
                  .filter(PublishDispatchTaskORM.account_db_id == acct.id,
                          PublishDispatchTaskORM.status.in_(("claimed", "running")))
                  .count())
    return used + inflight < cap


def _defer(dispatch: PublishDispatchTaskORM, now: datetime, reason: str,
           seconds: int = TRANSIENT_BACKOFF_SEC) -> None:
    """Push the queue row back. **attempt is not incremented** — no retry budget consumed (F4)."""
    dispatch.visible_after = now + timedelta(seconds=seconds)
    dispatch.last_error = reason[:2000]
    dispatch.updated_at = now


# ── Manual fallback (admin side) ────────────────────────────────────────────────


@router.post("/publish-dispatch/tasks/{dispatch_id}/resolve-stuck")
def resolve_stuck_dispatch_task(
    dispatch_id: int,
    payload: ResolveStuckIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("admin", "operator")),
):
    """After manual reconciliation, finish queue rows stuck in claimed/running; never re-dispatch."""
    dispatch = (db.query(PublishDispatchTaskORM)
                  .filter(PublishDispatchTaskORM.id == dispatch_id)
                  .with_for_update().one_or_none())
    if dispatch is None:
        raise HTTPException(404, f"dispatch id={dispatch_id} not found")
    out = _resolve_stuck_dispatch(
        db, dispatch, payload, operator_id=getattr(current_user, "id", None))
    db.commit()
    return out


def _resolve_stuck_dispatch(db: Session, dispatch: PublishDispatchTaskORM,
                            payload: ResolveStuckIn, *,
                            operator_id: Optional[int] = None,
                            now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    if dispatch.status not in ("claimed", "running"):
        if dispatch.status in ("done", "failed", "abandoned"):
            return {"ok": True, "resolved": False, "dispatch_status": dispatch.status,
                    "notes": ["already finalized"]}
        raise HTTPException(409, f"dispatch {dispatch.id} status={dispatch.status},"
                                  "不是执行中任务")

    cutoff = now - timedelta(minutes=CLAIM_STALE_MINUTES)
    if dispatch.claimed_at is not None and dispatch.claimed_at >= cutoff:
        raise HTTPException(
            409,
            f"dispatch {dispatch.id} 还未超过 {CLAIM_STALE_MINUTES} 分钟兜底阈值",
        )

    acct = db.get(PublishAccountORM, dispatch.account_db_id)
    if acct is None:
        raise HTTPException(404, f"publish account id={dispatch.account_db_id} not found")

    notes: list[str] = []
    reason = " ".join(payload.reason.split())[:500]
    err = (f"人工对账后结束:{reason}。禁止重试 —— 无法从系统侧证明平台未发布,"
           f"请以平台后台/创作中心为准(operator={operator_id}, draft_id={dispatch.draft_id})")

    dispatch.status = "failed"
    dispatch.attempt = dispatch.max_attempts
    dispatch.finished_at = now
    dispatch.last_error = err[:2000]
    dispatch.updated_at = now
    _fail_business_task(db, dispatch, dispatch.last_error)

    if acct.lease_until is None:
        notes.append("account lease already clear")
    elif acct.lease_worker_id == dispatch.claimed_by_worker_id \
            and acct.lease_until <= now:
        acct.lease_worker_id = None
        acct.lease_until = None
        notes.append("account lease cleared")
    else:
        notes.append("account lease kept (belongs to a newer run)")

    if payload.confirmed_not_published:
        _release_claimed_quota(acct, now, notes)
    else:
        notes.append("claimed quota kept (not confirmed unpublished)")
    acct.updated_at = now

    log.warning("[publish-dispatch] dispatch=%s manually resolved stuck by=%s "
                "quota_release=%s", dispatch.id, operator_id,
                payload.confirmed_not_published)
    return {"ok": True, "resolved": True, "dispatch_status": dispatch.status,
            "account_status": acct.status, "notes": notes}


# ── Receipts (worker side) ────────────────────────────────────────────────


@service_router.post("/publish-dispatch/progress")
def persist_progress(payload: ProgressIn, db: Session = Depends(get_db)):
    """Persist draft_id before the platform commit; on failure the caller must stop publishing."""
    dispatch = (db.query(PublishDispatchTaskORM)
                  .filter(PublishDispatchTaskORM.id == payload.dispatch_id)
                  .with_for_update().one_or_none())
    if dispatch is None:
        raise HTTPException(404, f"dispatch id={payload.dispatch_id} not found")
    if dispatch.status not in ("claimed", "running"):
        raise HTTPException(409, f"dispatch {dispatch.id} status={dispatch.status},不接受进度")
    if dispatch.claimed_by_worker_id != payload.worker_id:
        raise HTTPException(409, f"dispatch {dispatch.id} 由 worker "
                                 f"{dispatch.claimed_by_worker_id} 领取,不接受 "
                                 f"worker {payload.worker_id} 的进度")
    acct = db.get(PublishAccountORM, dispatch.account_db_id)
    now = datetime.utcnow()
    if acct is None:
        raise HTTPException(404, f"publish account id={dispatch.account_db_id} not found")
    if acct.lease_worker_id != payload.worker_id or not acct.lease_until or acct.lease_until <= now:
        raise HTTPException(409, f"账号 {acct.id} 的 worker 租约已失效,禁止进入 commit")
    if dispatch.draft_id and dispatch.draft_id != payload.draft_id:
        raise HTTPException(409, f"dispatch {dispatch.id} 已绑定另一 draft_id,禁止覆盖")
    dispatch.draft_id = payload.draft_id
    draft_url = _validated_draft_url(dispatch.platform, payload.draft_url, payload.draft_id)
    if draft_url:
        dispatch.draft_url = draft_url
    elif payload.draft_url:
        log.warning("[publish-dispatch] ignored invalid draft_url dispatch=%s platform=%s",
                    dispatch.id, dispatch.platform)
    if payload.screenshot_path:
        dispatch.screenshot_path = payload.screenshot_path
    dispatch.status = "running"
    dispatch.updated_at = now
    db.commit()
    return {"ok": True, "dispatch_id": dispatch.id, "draft_id": dispatch.draft_id,
            "draft_url": dispatch.draft_url}


@service_router.post("/publish-dispatch/report")
def report(payload: ReportIn, db: Session = Depends(get_db)):
    """Receipt. Handles the account + queue row per _PUBLISH_QUARANTINE_POLICY branches.

    Branch evaluation order: reset → cooldown → terminal → skip → threshold.
    """
    dispatch = (db.query(PublishDispatchTaskORM)
                  .filter(PublishDispatchTaskORM.id == payload.dispatch_id)
                  .with_for_update().one_or_none())
    if dispatch is None:
        raise HTTPException(404, f"dispatch id={payload.dispatch_id} not found")
    if dispatch.claimed_by_worker_id != payload.worker_id:
        # Only the claimer may send a receipt — otherwise one worker could declare success on behalf of another,
        # "settling" an in-flight task out of its hands while it is still really publishing.
        raise HTTPException(409, f"dispatch {dispatch.id} 由 worker "
                                 f"{dispatch.claimed_by_worker_id} 领取,不接受 "
                                 f"worker {payload.worker_id} 的回执")
    acct = db.get(PublishAccountORM, dispatch.account_db_id)
    if acct is None:
        raise HTTPException(404, f"publish account id={dispatch.account_db_id} not found")
    if dispatch.status not in ("claimed", "running"):
        # report may be delivered repeatedly when the caller did not get a response. Terminal receipts must be idempotent, and in particular must not release
        # used_today twice; return the persisted conclusion directly.
        return {"account_status": acct.status, "dispatch_status": dispatch.status,
                "notes": ["duplicate report ignored"]}

    now = datetime.utcnow()
    notes: list[str] = []
    result = _normalize_report_result(payload, notes)
    policy = publish_policy(result)
    failure_phase = (payload.failure_phase or "").strip()
    before_commit_failure = (
        result not in (PublishFailureType.SUCCESS, PublishFailureType.SUBMIT_UNKNOWN)
        and payload.confirmed_not_published
        and failure_phase in ("prepare", "pre_commit")
    )
    rejected_without_publish = (
        result == PublishFailureType.CONTENT_REJECTED
        and payload.confirmed_not_published
        and failure_phase in ("commit", "platform_rejected")
    )
    # The admin console only needs a human-readable reason; truncate and strip control characters, never write request bodies/login credentials into the account row.
    fail_reason = " ".join((payload.error or result.value).split())[:1000]

    if payload.draft_id:
        dispatch.draft_id = payload.draft_id
    draft_url = _validated_draft_url(
        dispatch.platform, payload.draft_url, payload.draft_id or dispatch.draft_id)
    if draft_url:
        dispatch.draft_url = draft_url
    elif payload.draft_url:
        log.warning("[publish-dispatch] ignored invalid report draft_url dispatch=%s platform=%s",
                    dispatch.id, dispatch.platform)
    if payload.screenshot_path:
        dispatch.screenshot_path = payload.screenshot_path

    # ── Egress IP drift: takes priority over all other checks ────────────────────────────
    # 🔴 Egress drift means this account has left a remote-login record on the platform side. Further automatic retries would only turn
    # "possibly flagged" into "definitely banned". So even if this publish "succeeded", stop the account.
    # Resolution failure (exit_ip=None) is treated as "unknown", not drift — treating one network glitch as drift
    # would stop good accounts.
    if payload.exit_ip and acct.exit_ip and payload.exit_ip != acct.exit_ip:
        old_exit_ip = acct.exit_ip
        acct.status = "unschedulable"
        acct.last_fail_type = PublishFailureType.EXIT_IP_DRIFT.value
        acct.last_fail_reason = "检测到账号实际出口 IP 与绑定出口不一致"
        acct.last_fail_at = now
        acct.exit_ip = payload.exit_ip
        acct.exit_ip_at = now
        acct.lease_worker_id = None
        acct.lease_until = None
        acct.updated_at = now
        dispatch.status = "abandoned"
        dispatch.finished_at = now
        dispatch.last_error = (f"出口 IP 从 {old_exit_ip} 漂移到 {payload.exit_ip},"
                               f"已停号待人工处理")
        dispatch.updated_at = now
        _fail_business_task(db, dispatch, dispatch.last_error)
        db.commit()
        log.error("[publish-dispatch] account=%s exit_ip drift → unschedulable", acct.id)
        return {"account_status": acct.status, "dispatch_status": dispatch.status,
                "notes": ["exit_ip_drift"]}

    if payload.exit_ip and not acct.exit_ip:
        acct.exit_ip = payload.exit_ip
        acct.exit_ip_at = now

    # ── Account-side handling ──────────────────────────────────────────────────
    fc = _fail_counts(acct)
    if policy.get("reset"):
        fc = {}
        acct.last_fail_type = None
        acct.last_fail_reason = None
        acct.last_fail_at = None
        # 🔴 Rolling TTL: counted from the last **successful use**, not from capture. A hard TTL expiry wrongly kills
        # good accounts — 6 of 8 Wenxin accounts died that way (expired → cannot be claimed → can never send a successful
        # receipt → can never renew). Extend unconditionally here, even if the worker returned no new storage_state.
        acct.expires_at = now + timedelta(days=RENEW_TTL_DAYS)
        if payload.storage_state and payload.storage_state.get("cookies"):
            # Only write back when cookies are non-empty — otherwise an empty shell would overwrite a good session
            # (same defense as the engine pool's SUCCESS branch).
            try:
                from geo.utils.publish_crypto import encrypt_required
                acct.storage_state_enc = encrypt_required(
                    json.dumps(payload.storage_state, ensure_ascii=False))
                notes.append("storage_state renewed")
            except Exception as e:  # noqa: BLE001
                # A failed renewal should not fail the whole receipt: the login session is still the old one and usable next time.
                log.warning("[publish-dispatch] account=%s storage_state renew failed: %s",
                            acct.id, e)
        # Warm-up ended → promoted to regular
        if acct.status == "warming" and acct.warmup_until and acct.warmup_until <= now:
            acct.status = "active"
            notes.append("warmup finished")
    elif policy.get("cooldown"):
        until = _safe_cooldown_until(payload.rate_limited_until)
        acct.rate_limited_until = until
        acct.last_fail_type = result.value
        acct.last_fail_reason = fail_reason
        acct.last_fail_at = now
        fc[result.value] = fc.get(result.value, 0) + 1
        n = _cool_down_siblings(db, acct, now)
        notes.append(f"cooled down until {until.isoformat()}, siblings={n}")
    elif policy.get("terminal"):
        acct.status = "unschedulable"
        acct.last_fail_type = result.value
        acct.last_fail_reason = fail_reason
        acct.last_fail_at = now
        fc[result.value] = fc.get(result.value, 0) + 1
        notes.append("account set unschedulable — 需人工处置,不会自动恢复")
    elif policy.get("skip"):
        # The account itself is fine (content violation / worker crash / unknown result), so its status is untouched.
        # But last_fail_* is still recorded so troubleshooting can see what happened.
        acct.last_fail_type = result.value
        acct.last_fail_reason = fail_reason
        acct.last_fail_at = now
    else:
        threshold = policy.get("threshold", 3)
        fc[result.value] = fc.get(result.value, 0) + 1
        acct.last_fail_type = result.value
        acct.last_fail_reason = fail_reason
        acct.last_fail_at = now
        if fc[result.value] >= threshold:
            acct.status = "quarantined"
            notes.append(f"quarantined ({result.value} x{fc[result.value]} >= {threshold})")

    acct.fail_counts_json = json.dumps(fc, ensure_ascii=False)
    # The lease is released on receipt (unlike the engine pool, which deliberately keeps sticky leases). Publishing pool ownership is
    # guaranteed for life by owner_worker_id; the lease only marks "running" and should be released when done.
    acct.lease_worker_id = None
    acct.lease_until = None
    acct.updated_at = now

    # used_today was already +1 at claim. Only when the executor explicitly proves the commit never reached the platform, or the platform explicitly
    # rejected it and confirmed not published, may this quota slot be returned; submit_unknown must never reach here.
    if before_commit_failure or rejected_without_publish:
        _release_claimed_quota(acct, now, notes)

    # ── Queue-row handling ──────────────────────────────────────────────────
    if result == PublishFailureType.SUCCESS:
        dispatch.status = "done"
        dispatch.finished_at = now
        dispatch.last_error = None
        # Only step 6 hooks publish_task_service.mark_published in here (URL backfill + retest registration).
        # For now the business task is left at publishing / returned to pending_url, to be finished by a human or later steps —
        # 🔴 Core invariant: a task without published_url **cannot** enter published.
        _note_success_without_url(db, dispatch, payload, notes)
    elif result == PublishFailureType.SUBMIT_UNKNOWN:
        # 🔴 The request may already have reached the platform. **Never blind retry** — a blind retry publishes the same article twice, irreversibly.
        # Mark the queue row failed and push visible_after ≥10 minutes out, then reconcile by draft_id
        # (reconciliation belongs to step 6; this round only guarantees evidence is persisted + no code path re-dispatches it).
        dispatch.status = "failed"
        dispatch.finished_at = now
        dispatch.visible_after = now + timedelta(seconds=SUBMIT_UNKNOWN_DEFER_SEC)
        dispatch.last_error = (f"submit_unknown:{payload.error or ''} "
                               f"— 保守等待后凭 draft_id={dispatch.draft_id} 对账,禁止重试")
        # Max out attempt so no future claim filter can pick it up. This is the mechanical guarantee of "no blind retry",
        # not relying on callers' discipline.
        dispatch.attempt = dispatch.max_attempts
        _fail_business_task(db, dispatch, dispatch.last_error)
        notes.append("submit_unknown — 已封禁重试,等人工/步骤6 对账")
    elif (before_commit_failure
          and not policy.get("terminal") and not policy.get("no_retry")
          and acct.status not in ("quarantined", "unschedulable", "retired")
          and dispatch.attempt < dispatch.max_attempts):
        dispatch.status = "queued"
        dispatch.claimed_by_worker_id = None
        dispatch.claimed_at = None
        # This round definitely did not reach the platform commit; the next attempt generates a new operation/draft.
        # Without clearing, progress's anti-overwrite check would treat the new draft_id as a conflict, causing a permanent 409.
        dispatch.draft_id = None
        dispatch.draft_url = None
        dispatch.screenshot_path = None
        dispatch.visible_after = now + timedelta(seconds=TRANSIENT_BACKOFF_SEC)
        dispatch.last_error = f"{result.value}:{payload.error or ''}"[:2000]
        _requeue_business_task(db, dispatch, dispatch.last_error)
        notes.append(f"{failure_phase} failure confirmed before commit — requeued")
    elif policy.get("terminal"):
        dispatch.status = "abandoned"
        dispatch.finished_at = now
        dispatch.last_error = f"{result.value}:{payload.error or ''}"[:2000]
        _fail_business_task(db, dispatch, dispatch.last_error)
    else:
        dispatch.status = "failed"
        dispatch.finished_at = now
        dispatch.last_error = f"{result.value}:{payload.error or ''}"[:2000]
        _fail_business_task(db, dispatch, dispatch.last_error)

    dispatch.updated_at = now
    db.commit()
    log.info("[publish-dispatch] report dispatch=%s account=%s result=%s → "
             "acct=%s queue=%s %s",
             dispatch.id, acct.id, result.value, acct.status, dispatch.status, notes)
    return {"account_status": acct.status, "dispatch_status": dispatch.status,
            "notes": notes}


def _note_success_without_url(db: Session, dispatch: PublishDispatchTaskORM,
                             payload: ReportIn, notes: list[str]) -> None:
    """Business task handling for a success receipt — with a URL, complete the full loop; without one, stay at pending_url.

    🔴 **Core invariant: a task without published_url cannot enter published**
    (hard-coded in the class comment at models/ai_telemetry.py:840). So without a URL, pending_url at most,
    never published — even if the worker claims success.

    With a URL, go through publish_task_service.mark_published: normalize + dedupe + sync doc +
    register retest. It is the one loop shared by manual backfill and paid-media receipts; the self-hosted pool does not build another —
    two paths writing published mean two sets of invariants that will each drift sooner or later.
    """
    task = db.get(TopicDocPublishTaskORM, dispatch.task_id)
    if task is None:
        return
    url = (payload.published_url or "").strip()
    if not url:
        if dispatch.platform == "wxgzh" and payload.url_source == "wechat_draft_id" and payload.draft_id:
            meta = _channel_meta(task)
            meta["draft_id"] = payload.draft_id
            meta["url_source"] = payload.url_source
            meta["draft_saved_at"] = datetime.utcnow().isoformat()
            if payload.draft_url:
                meta["draft_url"] = payload.draft_url
            task.channel_meta_json = json.dumps(meta, ensure_ascii=False)
            task.status = "draft_saved"
            task.last_error = None
            notes.append(f"公众号草稿已保存 draft_id={payload.draft_id}")
            return
        task.status = "pending_url"
        task.last_error = None
        notes.append("成功但无 URL — 停在 pending_url(不变量:没 URL 不进 published)")
        return

    # Keep the URL as evidence unconditionally first, then try backfill. Order matters: mark_published may raise (dedup collision,
    # doc/topic deleted), and by then **the article has really been published** — dropping the URL would leave a live
    # article the system cannot find, recoverable only by someone digging through the platform. Keeping evidence does not depend on backfill success.
    meta = _channel_meta(task)
    meta["reported_url"] = url
    meta["reported_at"] = datetime.utcnow().isoformat()
    if payload.url_source:
        meta["url_source"] = payload.url_source
    if payload.draft_id:
        meta["draft_id"] = payload.draft_id
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False)

    try:
        proof = {"source": "server_pool",
                 "account_db_id": dispatch.account_db_id,
                 "draft_id": payload.draft_id,
                 "exit_ip": payload.exit_ip,
                 "url_source": payload.url_source,
                 "screenshot": payload.screenshot_path}
        if dispatch.platform == "ctrip" and payload.draft_id:
            proof.update({
                "verification_method": "platform_api",
                "verification_executor": "server_pool",
                "platform_code": "ctrip",
                "external_post_id": str(payload.draft_id),
            })
        if dispatch.platform == "wxgzh":
            mark_published(db, task, url, proof=proof)
            notes.append(f"公众号发布回执已确认:{url}")
        else:
            submit_candidate_url(db, task, url, proof=proof)
            notes.append(f"已登记候选 URL,等待探测或人工确认:{url}"
                         f"{f'(来源 {payload.url_source})' if payload.url_source else ''}")
    except ValueError as e:
        # 🔴 Do not raise this exception. On the worker side the publish is **already done and irreversible**; if report
        # returned 500, the worker would get no confirmation while the task is still claimed in the DB — after lease expiry it might be re-dispatched,
        # publishing the same article twice. So: swallow the exception, leave the task at pending_url for a human.
        task.status = "pending_url"
        task.last_error = f"URL 回填被拒:{e}"[:2000]
        notes.append(f"🔴 已发布但回填失败({e})— 停 pending_url 待人工,URL 已留证")
        log.error("[publish-dispatch] task=%s published successfully but mark_published failed: %s",
                  task.id, e)


def _fail_business_task(db: Session, dispatch: PublishDispatchTaskORM, err: str) -> None:
    task = db.get(TopicDocPublishTaskORM, dispatch.task_id)
    if task is None:
        return
    if task.status == "published":
        return          # A task already published successfully is not rewritten by one queue failure
    task.status = "failed"
    task.last_error = err[:2000]


def _requeue_business_task(db: Session, dispatch: PublishDispatchTaskORM, err: str) -> None:
    task = db.get(TopicDocPublishTaskORM, dispatch.task_id)
    if task is None or task.status == "published":
        return
    task.status = "planned"
    task.last_error = err[:2000]


def _release_claimed_quota(acct: PublishAccountORM, now: datetime,
                           notes: list[str]) -> None:
    """Release the daily quota pre-reserved by this claim; across Beijing calendar days, today's count is not changed."""
    today = local_today()
    if acct.used_date == today and acct.used_today > 0:
        acct.used_today -= 1
        notes.append("claimed quota released")
    else:
        notes.append("claimed quota release skipped (different day or already zero)")


def render_fingerprint(rendered) -> dict:
    """Fingerprint of the render at claim time — answers later "what exactly was the live article's content at the time".

    🔴 Fills a real gap: the output of `render_for_publish` (title/body/body_format/images)
    **only exists within that one claim request** and is gone after being sent to the worker; all that remains in the DB is
    the source draft `topic_generated_docs.body_markdown`, which is **overwritten in place**
    (models/ai_telemetry.py:732). Once the draft is edited later, what the live article looked like can no longer be reconstructed.

    The full body is not stored (size × every task), only enough for an **identity check**: body hash + length +
    format + the render_warnings at that time. To review content, run the same renderer version on the source draft and compare hashes —
    a match means unchanged, a mismatch tells you to look at the draft's edits.
    """
    body = rendered.body or ""
    return {
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "body_len": len(body),
        "title_len": len(rendered.title or ""),
        "body_format": rendered.body_format,
        "images": len(rendered.image_urls or []),
        "warnings": list(rendered.warnings or []),
        "at": datetime.utcnow().isoformat(),
    }


def _channel_meta(task: TopicDocPublishTaskORM) -> dict:
    try:
        d = json.loads(task.channel_meta_json or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _fail_counts(acct: PublishAccountORM) -> dict:
    try:
        d = json.loads(acct.fail_counts_json or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _safe_cooldown_until(ts) -> datetime:
    """Safely convert the worker-reported rate_limited_until (unix seconds) into a cooldown expiry.

    Follows the existing implementation at api/engine_sessions.py:222. Guards against three bad values:
      ① 13-digit millisecond timestamps → divide by 1000. Otherwise utcfromtimestamp parses it as year 50,000,
        permanently excluding the account from claim = silently deleting it;
      ② past values → immediately claimable and immediately rate-limited again = hot loop;
      ③ non-numeric / overflow → fall back to the default cooldown.
    """
    now = datetime.utcnow()
    default = now + timedelta(seconds=RATE_LIMIT_COOLDOWN_SEC)
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return default
    if ts > 1e12:
        ts = ts / 1000.0
    try:
        when = datetime.utcfromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return default
    return min(max(when, now + timedelta(minutes=1)), now + timedelta(days=30))


def _cool_down_siblings(db: Session, row: PublishAccountORM, now: datetime) -> int:
    """When an account is rate-limited, cool down sibling accounts on the same platform and egress; returns the number affected.

    🔴 **Rate limiting is judged per IP, not per account.** Cooling only the affected account, the system would feed
    the remaining accounts on this already-flagged IP in one by one to hit the wall (engine pool observed: accounts on the same IP died together
    within a minute). Copied from _cool_down_siblings at api/engine_sessions.py:242.

    The "same egress" check is deliberately **broader** than quota grouping: same proxy_id **or** same exit_ip both count —
    this is a safety fallback; better to cool a few extra accounts than miss accounts truly sharing the IP.
    Only extends, never shortens, a later cooldown a sibling already has.
    """
    if SIBLING_COOLDOWN_SEC <= 0:
        return 0
    if not row.proxy_id and not row.exit_ip:
        return 0
    until = now + timedelta(seconds=SIBLING_COOLDOWN_SEC)
    n = 0
    sibs = (db.query(PublishAccountORM)
              .filter(PublishAccountORM.platform == row.platform,
                      PublishAccountORM.id != row.id).all())
    for sib in sibs:
        same = ((row.proxy_id and sib.proxy_id == row.proxy_id)
                or (row.exit_ip and sib.exit_ip == row.exit_ip))
        if not same:
            continue
        if sib.rate_limited_until is None or sib.rate_limited_until < until:
            sib.rate_limited_until = until
            n += 1
    return n
