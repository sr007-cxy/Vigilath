"""Paid sponsored-article channel endpoints for the media-library family (M2, docs/04-开发实现/研发计划/M2-软文匠通道接入计划.md).

One set of endpoints serves two isomorphic sites (`provider`: `rwj`=Ruanwenjiang, `mjk`=Meijieku, default rwj) —
the two are two sites of the same backend, with protocol differences contained in rwj_client.SiteSpec. Resource browsing/draft pushing
uses the provider passed in; publishing/reconciliation take the provider from the task's channel_meta, which the frontend cannot change.

  - GET  /api/rwj/providers                              — available site list (level/balance)
  - GET  /api/rwj/status                                 — channel status (level/balance)
  - POST /api/rwj/media/search                           — live media resource search (with live prices)
  - POST /api/rwj/docs/{doc_id}/push-draft               — push draft to drafts box (free, multiple media) + create planned tasks
  - POST /api/rwj/publish-tasks/{task_id}/publish-preview — pre-publish price check, issue a confirmation token
  - POST /api/rwj/publish-tasks/{task_id}/publish        — place the real order after second confirmation (charged, dual gate)
  - POST /api/rwj/publish-tasks/batch-publish-preview     — batch price check (total + token covering multiple media)
  - POST /api/rwj/publish-tasks/batch-publish             — one confirmation, orders placed serially per media (charged)
  - POST /api/rwj/publish-tasks/{task_id}/reconcile      — manual reconciliation to pull receipts

Multi-select media: the platform's own member center supports ticking several media for the same article at once (`resource` is already an array).
On our side one draft push = one draft on the platform (N media share a draft_id) + N independent placement tasks;
a paid confirmation may cover several media, but **orders are still independent and placed serially per media** — only each media's own receipt URL
can enter retest and citation attribution.

Permissions (opened up 2026-08-05): any logged-in user can self-serve media selection / draft pushing / confirmed paid ordering,
no longer requiring admin | operator to act on their behalf. Along with that, **every action checks draft ownership**
(doc / task's topic.user_id == current user; staff can still cross users); the platform wallet balance
is only returned to staff — paid placements spend the platform balance, which is internal financial information not exposed.
Charging guardrails: RWJ_PUBLISH_ENABLED off by default; preview→confirm_token (HMAC-bound price, 10 minutes)
→publish two-step confirmation; state-machine optimistic lock prevents duplicates before ordering; timeouts do not blind retry, use reconcile.
Compliance: both draft pushing and real ordering pass compliance_scanner.publish_gate (where content really goes out).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from geo.api.ai_telemetry import get_db
from geo.api.auth import get_current_user, is_staff, require_roles
from geo.database import settings
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicDocPublishTaskORM, TopicGeneratedDocORM,
)
from geo.models.balance import KIND_PUBLISH_MEDIA
from geo.services import (
    compliance_audit, publish_limits, publish_task_service, rwj_pricing,
)
from geo.services.access_control import (
    ACTION_WRITE, create_resource_operation_context,
    require_resource_access, resource_not_found, revalidate_operation_context,
)
from geo.services.system_audit import append_event as append_system_audit
from geo.services import ruanwenjiang_publisher as rwj_pub
from geo.services.compliance_scanner import publish_gate
from geo.services.rwj_client import (
    DEFAULT_PROVIDER, SITES, RwjApiError, RwjAuthError, delivery_options,
    filter_fields, get_client, pick_delivery, resource_price, site, sort_expr,
)
from geo.services.rwj_order_poller import reconcile_task

log = logging.getLogger(__name__)

router = APIRouter(prefix="/rwj", dependencies=[Depends(get_current_user)])

def _price_tolerance_pct() -> float:
    """Allowed price-increase tolerance (percent). Beyond it, 409 so the user sees the new price and decides.

    Paid ordering now works as "publish if the balance suffices", with no price-bound token. Instead: the frontend sends the
    external price it saw as expected_price_cents, the server re-checks the live price before ordering, orders directly if the increase is within
    tolerance, otherwise 409 + new price. **Price decreases always pass** — users never complain about it getting cheaper.
    """
    try:
        return max(0.0, float(os.environ.get("RWJ_PRICE_TOLERANCE_PCT") or 5))
    except ValueError:
        return 5.0


def _own_doc_or_404(db: Session, doc_id: int, user) -> TopicGeneratedDocORM:
    """Draft write operations all go through enterprise authorization."""
    doc = db.get(TopicGeneratedDocORM, doc_id)
    if doc is None:
        raise resource_not_found()
    require_resource_access(db, user, doc, ACTION_WRITE)
    return doc


def _actor_type(user) -> str:
    return "admin" if is_staff(user) else "user"


def _provider_or_422(provider: Optional[str]) -> str:
    """An externally supplied provider must be in the registry — it goes straight into route construction and cannot be passed through."""
    try:
        return site(provider or DEFAULT_PROVIDER).provider
    except RwjApiError:
        raise HTTPException(422, {"code": "RWJ_PROVIDER",
                                  "message": f"未知的发稿站点 {provider}"})


def _label(provider: str) -> str:
    return site(provider).label


def _api_error(e: RwjApiError) -> HTTPException:
    if isinstance(e, rwj_pub.RwjNotConfigured):
        return HTTPException(503, {"code": "RWJ_NOT_CONFIGURED", "message": str(e)})
    if isinstance(e, rwj_pub.RwjBalanceError):
        return HTTPException(402, {"code": "RWJ_BALANCE", "message": str(e)})
    if isinstance(e, RwjAuthError):
        return HTTPException(502, {"code": "RWJ_AUTH", "message": f"软文匠登录态异常:{e}"})
    return HTTPException(502, {"code": "RWJ_API", "message": str(e)})


# ─────────────────────── Status / search ──────────────────────────

def _one_status(provider: str, current_user) -> dict:
    spec = site(provider)
    base = {"provider": spec.provider, "label": spec.label,
            "configured": False, "level": None, "balance": None}
    try:
        st = rwj_pub.channel_status(provider)
    except rwj_pub.RwjNotConfigured:
        return base
    except (RwjApiError, httpx.RequestError) as e:
        # One site being down should not 502 the whole list — mark it unavailable, the other remains usable
        return {**base, "error": str(e)[:200]}
    if not is_staff(current_user):
        st = {**st, "balance": None}
    return st


@router.get("/providers", dependencies=[Depends(require_roles("admin", "operator"))])
def rwj_providers(current_user = Depends(get_current_user)):
    """Available publishing sites (Ruanwenjiang / Meijieku). The frontend supplier switch renders from this."""
    return {"default": DEFAULT_PROVIDER,
            "providers": [_one_status(p, current_user) for p in SITES]}


@router.get("/status", dependencies=[Depends(require_roles("admin", "operator"))])
def rwj_status(provider: Optional[str] = None, current_user = Depends(get_current_user)):
    """Single-site channel status. The platform wallet balance is staff-only — regular users only need the level and availability."""
    p = _provider_or_422(provider)
    try:
        st = rwj_pub.channel_status(p)
        if not is_staff(current_user):
            st = {**st, "balance": None}
        return st
    except rwj_pub.RwjNotConfigured:
        return {"provider": p, "label": _label(p),
                "configured": False, "level": None, "balance": None}
    except RwjApiError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "RWJ_NETWORK",
                                  "message": f"{_label(p)}接口不可达:{e}"})


# Browsable resource types (fully fetched); types orderable online are in publisher.SUPPORTED_TYPES
BROWSABLE_TYPES = ("Website", "Wemedia", "Shortvideo", "Question", "Ghostwrite")

# Chinese labels for filter dimensions (Base/init gives options but not dimension names; organized per the member center page semantics)
_FILTER_LABELS = {
    "Website": {
        "field_1": "频道类型", "field_2": "门户网站", "field_3": "地区",
        "field_4": "入口位置", "field_5": "网页收录", "field_6": "可带网址",
        "field_7": "出稿速度", "field_8": "特殊行业", "field_9": "其他",
    },
    "Wemedia": {
        "field_1": "平台", "field_2": "领域", "field_3": "地区",
        "field_4": "粉丝量", "field_5": "阅读量", "field_6": "认证情况",
        "field_7": "媒体属性", "field_8": "其他",
    },
    "Shortvideo": {
        "field_1": "平台", "field_2": "行业", "field_3": "地区",
        "field_4": "领域", "field_5": "内容标签", "field_6": "达人性别",
        "field_7": "投放方式", "field_8": "内容修改", "field_10": "粉丝量",
        "field_11": "作品量", "field_12": "播放量",
    },
    "Question": {
        "field_1": "平台", "field_2": "联系方式", "field_3": "回答时效",
        "field_4": "收录保障", "field_6": "特殊行业", "field_7": "其他",
    },
    "Ghostwrite": {
        "field_1": "文体类型", "field_2": "领域", "field_3": "修改次数",
        "field_4": "篇幅", "field_5": "交稿时效", "field_6": "写手类型",
    },
}

# Meijieku has a few more dimensions than Ruanwenjiang, and some same-numbered dimensions changed meaning — overlaid on Ruanwenjiang's labels.
# Named ones copy the labels from Meijieku's own publishing form; short-video field_9/10/13 are not in the form,
# so they are inferred from /System/init option ranges (work count 0-10~1000+, follower count 0-100~50000+).
_MJK_FILTER_LABELS = {
    "Website": {"field_2": "综合门户", "field_3": "所在地区", "field_4": "入口级别",
                "field_5": "收录情况", "field_6": "链接类型", "field_7": "发稿速度",
                "field_9": "其他选项", "field_10": "媒体标签", "field_11": "价格区间"},
    "Wemedia": {"field_2": "行业类型", "field_3": "所在地区", "field_6": "账号认证",
                "field_7": "官方媒体", "field_8": "其他选项", "field_10": "价格区间"},
    "Shortvideo": {"field_2": "行业类型", "field_3": "所在地区", "field_6": "性别",
                   "field_7": "投放方式", "field_8": "修改内容", "field_9": "作品量",
                   "field_10": "粉丝量", "field_13": "账号认证"},
    "Question": {"field_1": "平台类型", "field_2": "联系方式", "field_3": "出稿速度",
                 "field_4": "链接时效", "field_6": "特殊行业", "field_7": "其他选项",
                 "field_8": "价格区间"},
    "Ghostwrite": {"field_1": "代写类型", "field_2": "擅长领域", "field_3": "修改次数",
                   "field_4": "篇幅字数", "field_5": "交稿时间", "field_6": "写手规模",
                   "field_7": "价格区间"},
}


def _dim_labels(provider: str, type_: str) -> dict[str, str]:
    base = dict(_FILTER_LABELS.get(type_, {}))
    if provider == "mjk":
        base.update(_MJK_FILTER_LABELS.get(type_, {}))
    return base


# The third info column of the list uses different fields by type (website = indexed, self-media = verification, others best effort)
_INCLUDE_FIELD = {
    "Website": "field_5_name", "Wemedia": "field_6_name",
    "Shortvideo": "field_7_name", "Question": "field_4_name", "Ghostwrite": "field_4_name",
}

# init data barely changes; cached in-process for 1 hour (bucketed by provider+type)
_filters_cache: dict = {}


@router.get("/media/filters", dependencies=[Depends(require_roles("admin", "operator"))])
def media_filters(type: str = "Website", provider: Optional[str] = None):
    """Media filter dimensions (dimension → option list), matching the site's member center filter rows."""
    p = _provider_or_422(provider)
    if type not in BROWSABLE_TYPES:
        raise HTTPException(422, {"code": "RWJ_TYPE", "message": f"未知的媒体类型 {type}"})
    c = get_client(p)
    if not c.configured:
        raise HTTPException(503, {"code": "RWJ_NOT_CONFIGURED",
                                  "message": f"{_label(p)}通道未配置"})
    cache_key = (p, type)
    cached = _filters_cache.get(cache_key)
    if cached and cached["at"] > time.time() - 3600:
        return cached["data"]
    try:
        init = c.base_init()
    except RwjApiError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "RWJ_NETWORK",
                                  "message": f"{_label(p)}接口不可达:{e}"})
    fields = (init.get("field") or {}).get(type.lower()) or {}
    labels = _dim_labels(p, type)
    dims = []
    for f in filter_fields(p, type):   # Only expose filter fields the list API actually accepts, keeping page order
        opts = fields.get(f)
        if not isinstance(opts, list) or not opts:
            continue
        dims.append({
            "field": f,
            "label": labels.get(f, f),
            "options": [
                {"id": o.get("field_id"), "title": o.get("field_title")}
                for o in opts if isinstance(o, dict) and o.get("status", 1) == 1
            ],
        })
    data = {"type": type, "provider": p, "dims": dims}
    _filters_cache[cache_key] = {"at": time.time(), "data": data}
    return data


class MediaSearchPayload(BaseModel):
    provider: Optional[str] = None
    type: str = "Website"
    title: str = ""
    price_min: str = ""
    price_max: str = ""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=10, le=50)
    sort: Optional[str] = None   # "price_asc" | "price_desc", mapped server-side to whitelisted strings
    # Dimension filters: {field_1: 1001, ...}; keys must be in the type's FILTER_FIELDS, values are option field_ids
    filters: dict[str, int] = Field(default_factory=dict)


@router.post("/media/search", dependencies=[Depends(require_roles("admin", "operator"))])
def search_media(payload: MediaSearchPayload,
                 current_user=Depends(get_current_user)):
    """Live media resource search (all five types browsable). Returns live prices and placement methods;
    whether it can be ordered online is marked by orderable (publisher.SUPPORTED_TYPES).

    ★ display_price is the **external price** (cost × markup, with a floor), not the upstream cost.
      This once exposed the cost price while charging the retail price: a ¥1 media showed ¥1 and charged ¥4.
      The cost price is staff-only, in upstream_price."""
    p = _provider_or_422(payload.provider)
    if payload.type not in BROWSABLE_TYPES:
        raise HTTPException(422, {"code": "RWJ_TYPE", "message": f"未知的媒体类型 {payload.type}"})
    c = get_client(p)
    if not c.configured:
        raise HTTPException(503, {"code": "RWJ_NOT_CONFIGURED",
                                  "message": f"{_label(p)}通道未配置"})
    try:
        info = c.ensure_session()
        level = int(info.get("level") or 1)
        overrides: dict = {"pageNo": payload.page, "pageSize": payload.page_size}
        if payload.title.strip():
            overrides["title"] = payload.title.strip()
        if payload.price_min.strip():
            overrides["price_min"] = payload.price_min.strip()
        if payload.price_max.strip():
            overrides["price_max"] = payload.price_max.strip()
        # Q&A pricing fields are price_{n}_{level}; the server has no price_{level} column, and sorting would cause a SQL error
        if payload.sort and payload.type != "Question":
            overrides["sort"] = sort_expr(payload.sort, level)  # Whitelist mapping, prevents injection
        allowed = filter_fields(p, payload.type)
        for k, v in payload.filters.items():   # Dimension filters: whitelisted keys + values coerced to int
            if k in allowed:
                overrides[k] = int(v)
        data = c.list_resources(payload.type, **overrides)
    except RwjApiError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "RWJ_NETWORK",
                                  "message": f"{_label(p)}接口不可达:{e}"})

    include_field = _INCLUDE_FIELD.get(payload.type, "field_5_name")
    staff = is_staff(current_user)
    rows = []
    for r in data.get("data") or []:
        try:
            d = pick_delivery(r)
            if payload.type == "Question":
                # Q&A pricing: price_{answerCount}_{level}, showing the single Q&A unit price
                price = resource_price(r, level, answer_count=1)
            else:
                price = resource_price(r, level, delivery=d)
        except RwjApiError:
            continue  # Resources without an available placement method / with malformed price fields are not shown
        retail = rwj_pricing.retail_from_yuan_str(str(price))
        if retail is None:
            continue                 # Resources without an external price are not shown; never fall back to the cost price
        row = {
            "resource_id": r.get("resource_id"),
            "title": r.get("title"),
            "display_price": retail,
            "delivery": {"label": d.get("label"), "value": d.get("value")},
            "delivery_options": [
                {"label": o.get("label"), "value": o.get("value")}
                for o in delivery_options(r)
            ],
            "publish_rate": r.get("publish_rate"),
            "publish_time": r.get("publish_time"),
            "pc_weigh": r.get("pc_weigh"),
            "wap_weigh": r.get("wap_weigh"),
            "remarks": r.get("remarks"),
            "channel": r.get("field_1_name"),   # Channel/platform (news / Toutiao …)
            "region": r.get("field_3_name"),    # Region (some types lack this dimension)
            "include": r.get(include_field),    # Indexed/verification/placement method etc., by type
        }
        if staff:
            row["upstream_price"] = str(price)   # Cost price, only for ops to verify
        rows.append(row)
    return {
        "provider": p,
        "label": _label(p),
        "level": level,
        "total": data.get("total"),
        "page": data.get("current_page"),
        "last_page": data.get("last_page"),
        "orderable": payload.type in rwj_pub.SUPPORTED_TYPES,
        "rows": rows,
    }


# ─────────────────────── Push draft (free) ──────────────────────────

class ResourceRef(BaseModel):
    resource_id: int
    resource_title: str   # The list has no lookup by id; verification pages through and matches by name keyword


class PushDraftPayload(BaseModel):
    provider: Optional[str] = None
    type: str = "Website"
    # Multi-select: place to several media at once (that is how the platform's own member center works).
    # resource_id / resource_title are the old single-media inputs, still accepted only for old frontends and scripts.
    resources: list[ResourceRef] = Field(default_factory=list)
    resource_id: Optional[int] = None
    resource_title: Optional[str] = None

    def targets(self) -> list[dict]:
        """Normalize to [{resource_id, resource_title}], deduped by id, keeping selection order."""
        raw = list(self.resources)
        if self.resource_id:
            raw.append(ResourceRef(resource_id=self.resource_id,
                                   resource_title=self.resource_title or ""))
        out: list[dict] = []
        seen: set[int] = set()
        for r in raw:
            if r.resource_id in seen:
                continue
            seen.add(r.resource_id)
            out.append({"resource_id": r.resource_id, "resource_title": r.resource_title})
        return out


@router.post("/docs/{doc_id}/push-draft")
def push_draft(
    doc_id: int,
    payload: PushDraftPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Push the draft to the platform drafts box (no charge) and create planned placement tasks; supports several media at once.

    Several media merge into one draft on the platform (N media share a draft_id), but each media gets its own placement task,
    with its own resource and price — paid confirmation, receipt URLs and retest attribution are all per media.
    Content leaves this system for a third-party platform — passes the publish_gate compliance gate.
    """
    p = _provider_or_422(payload.provider)
    targets = payload.targets()
    if not targets:
        raise HTTPException(422, {"code": "NO_RESOURCE", "message": "请先勾选要投放的媒体"})
    if len(targets) > settings.RWJ_MULTI_MAX:
        raise HTTPException(422, {
            "code": "TOO_MANY_RESOURCES",
            "message": f"一次最多投放 {settings.RWJ_MULTI_MAX} 家媒体,请分批提交",
        })
    doc = _own_doc_or_404(db, doc_id, current_user)
    if doc.status not in ("approved", "published"):
        raise HTTPException(409, {"code": "NEED_APPROVED", "message": "请先通过审核再投放付费媒体"})
    topic = db.get(AiTelemetryTopicORM, doc.topic_id)
    if topic is None:
        raise HTTPException(404, "topic not found")

    ok, reason = publish_gate(
        db, doc, topic, hook="publish_paid_media",
        actor_id=current_user.id, actor_name=getattr(current_user, "name", "") or "",
    )
    if not ok:
        db.commit()  # Record the block for audit
        raise HTTPException(409, {"code": "COMPLIANCE_BLOCKED", "message": reason})

    _, operation = create_resource_operation_context(
        db, current_user, doc,
        action="publish.rwj.push_draft", target_resource_type="document",
    )
    # The external draft API uses a context visible to a separate transaction; then this external call completes while holding the authorization lock.
    db.commit()
    revalidate_operation_context(db, operation.id)

    try:
        results, failed = rwj_pub.push_drafts(
            doc, type_=payload.type, provider=p, targets=targets,
        )
    except RwjApiError as e:
        db.rollback()
        operation = db.get(type(operation), operation.id)
        append_system_audit(
            db, operation_context=operation, event_type="task_failed",
            action="publish.rwj.push_draft", object_type="document",
            object_id=doc.id, result="failed", reason=str(e),
        )
        db.commit()
        raise _api_error(e)
    except httpx.RequestError as e:
        db.rollback()
        operation = db.get(type(operation), operation.id)
        append_system_audit(
            db, operation_context=operation, event_type="task_failed",
            action="publish.rwj.push_draft", object_type="document",
            object_id=doc.id, result="failed", reason=str(e),
        )
        db.commit()
        raise HTTPException(502, {"code": "RWJ_NETWORK",
                                  "message": f"{_label(p)}接口不可达:{e}"})

    quoted_at = datetime.utcnow().isoformat()
    task_targets = [
        {"platform": rwj_pub.platform_name(p), "media_name": r.resource_title,
         "channel_type": "external_media", "publish_mode": "paid_media",
         # Each media's resource/price differs — channel_meta must be persisted per row
         "channel_meta": {
             "channel": p,
             "provider": p,
             "type": payload.type,
             "resource_id": r.resource_id,
             "resource_title": r.resource_title,
             "draft_id": r.draft_id,
             "delivery": r.delivery,
             "quoted_price": r.price,
             "quoted_level": r.level,
             "quoted_at": quoted_at,
             "dropped_images": r.dropped_images,
         }}
        for r in results
    ]
    tasks = publish_task_service.create_tasks(
        db, doc, topic, task_targets,
        assigned_to_user_id=current_user.id,
        initial_status="planned",
        operation_context_id=operation.id,
    )
    append_system_audit(
        db, operation_context=operation, event_type="task_created",
        action="publish.rwj.push_draft", object_type="document",
        object_id=doc.id, result="success",
    )
    db.commit()
    return {
        "tasks": [_task_out(db, t, current_user) for t in tasks],
        "failed": failed,
        # Images not carried over in the body (one per article, same for all media) — the frontend must warn, not let users discover it after the media publishes
        "dropped_images": results[0].dropped_images if results else [],
        # Old-frontend compatibility: with a single media the task can still be read directly
        "task": _task_out(db, tasks[0], current_user) if tasks else None,
    }


@router.post("/docs/{doc_id}/auto-select")
def auto_select_media(
    doc_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger AI automatic media selection (backfill/rerun for old drafts). Runs in the background; the frontend polls the task list.

    Idempotent: if the draft already has Ruanwenjiang tasks the background job skips.
    """
    doc = _own_doc_or_404(db, doc_id, current_user)
    if doc.status not in ("approved", "published"):
        raise HTTPException(409, {"code": "NEED_APPROVED", "message": "请先通过审核再选媒"})
    if not settings.RWJ_AUTO_SELECT_ENABLED:
        raise HTTPException(503, {"code": "AUTO_SELECT_DISABLED", "message": "自动选媒开关未开启"})
    from geo.services.media_select import schedule_auto_select
    schedule_auto_select(doc.id, actor_id=current_user.id)
    return {"scheduled": True, "doc_id": doc.id,
            "message": "已开始自动选媒,约 10-30 秒后任务出现在下方列表"}


# ─────────────────────── Publish (charged, two-step confirmation) ─────────────────────

def _task_out(db: Session, task: TopicDocPublishTaskORM, user) -> dict:
    """Task output: regular users get a redacted version (no vendor / resource id / cost price / upstream response)."""
    return publish_task_service.task_to_dict(
        task, redact=not is_staff(user), db=db)


# Keys in the price-check response that only ops may see — supplier vendor, upstream resource id, cost price.
# The user side sees "one media, one external price", with no notion of channels.
_PREVIEW_STAFF_ONLY = ("provider", "provider_label", "resource_id",
                       "resource_title", "price", "quoted_price")


def _price_changed(expected_cents: Optional[int], live_price: str) -> Optional[dict]:
    """Is the live price much higher than what the user saw? If so return a 409 body, otherwise None (including decreases).

    Increases within tolerance order directly — upstream prices fluctuate by a few cents, and bouncing the user back to the list page
    to start over only yields a more annoyed user and the same order.
    """
    if expected_cents is None:
        return None
    live = _retail_cents_of(live_price)
    if live is None:
        return {"code": "PRICE_UNKNOWN", "message": "拿不到这家媒体的报价,未下单"}
    ceiling = int(round(int(expected_cents) * (1 + _price_tolerance_pct() / 100.0)))
    if live <= ceiling:
        return None
    return {
        "code": "PRICE_CHANGED",
        "message": f"这家媒体的价格已从 ¥{int(expected_cents) / 100:.2f} 涨到 "
                   f"¥{live / 100:.2f},未下单。确认后可重新提交。",
        "expected_price_cents": int(expected_cents),
        "retail_price_cents": live,
        "retail_price": f"{live / 100:.2f}",
    }


def _rwj_task_or_404(db: Session, task_id: int, user) -> TopicDocPublishTaskORM:
    """Task lookup + ownership check — once the charged endpoint is open to users, it must block publishing others' drafts."""
    task = db.get(TopicDocPublishTaskORM, task_id)
    if task is None or task.platform not in rwj_pub.PLATFORM_NAMES:
        raise resource_not_found()
    require_resource_access(db, user, task, ACTION_WRITE)
    return task


def _task_meta(task: TopicDocPublishTaskORM) -> dict:
    try:
        meta = json.loads(task.channel_meta_json or "{}")
        return meta if isinstance(meta, dict) else {}
    except ValueError:
        return {}


def _task_provider(task: TopicDocPublishTaskORM, meta: dict) -> str:
    """The site a task belongs to — per the task table's platform (the provider for ordering/reconciliation must never be
    specified by the requester). Old tasks have platform 「软文匠」 (Ruanwenjiang), naturally falling back to rwj."""
    return (rwj_pub.provider_of(task.platform)
            or _provider_or_422(meta.get("provider") or meta.get("channel")))


def _err(code: str, message: str, status: int = 409, *, fatal: bool = False,
         task: Optional[TopicDocPublishTaskORM] = None) -> dict:
    """Unified failure structure — single endpoints translate it to HTTPException, batch endpoints collect it per item.

    fatal=True means "do not try the remaining media either" (gate/credentials/balance/network/login session).
    """
    out = {"ok": False, "code": code, "message": message, "status": status, "fatal": fatal}
    if task is not None:
        out["task_id"] = task.id
        out["media_name"] = task.media_name
    return out


def _raise(res: dict):
    """Translate the _err structure back into today's HTTPException as-is (single-endpoint behavior unchanged)."""
    raise HTTPException(res.get("status", 409),
                        {"code": res["code"], "message": res["message"]})


def _preview_one(db: Session, task: TopicDocPublishTaskORM, current_user) -> dict:
    """Single-task price check: verify the resource still exists, fetch the live price, record it. No commit, no raise (failures return _err)."""
    if task.status not in ("planned", "failed"):
        return _err("BAD_STATUS", f"任务当前状态 {task.status} 不可发布", task=task)
    meta = _task_meta(task)
    resource_id = meta.get("resource_id")
    resource_title = str(meta.get("resource_title") or task.media_name or "")
    type_ = str(meta.get("type") or "Website")
    if not resource_id:
        return _err("NO_RESOURCE", "任务缺少媒体资源信息,请重新推送草稿", task=task)
    p = _task_provider(task, meta)

    c = get_client(p)
    if not c.configured:
        return _err("RWJ_NOT_CONFIGURED", "媒体通道暂不可用,请稍后重试", 503,
                    fatal=True, task=task)
    try:
        info = c.ensure_session()
        level = int(info.get("level") or 1)
        res, delivery, price = rwj_pub.verify_resource(
            c, type_, int(resource_id), resource_title, level)
    except RwjAuthError as e:
        return _err("RWJ_AUTH", str(e), 502, fatal=True, task=task)
    except RwjApiError as e:
        exc = _api_error(e)
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "RWJ_API", "message": str(e)}
        return _err(detail.get("code", "RWJ_API"), detail.get("message", str(e)),
                    exc.status_code, task=task)
    except httpx.RequestError as e:
        log.warning("rwj network error provider=%s: %s", p, e)
        return _err("RWJ_NETWORK", "媒体通道暂时连不上,请稍后重试", 502,
                    fatal=True, task=task)

    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="paid_publish",
        object_type="publish_task", object_id=task.id,
        actor_type=_actor_type(current_user), actor_id=current_user.id,
        actor_name=getattr(current_user, "name", "") or "",
        detail={"action": "preview", "resource_id": resource_id,
                "resource_title": resource_title, "price": str(price)},
    )
    return {
        "ok": True,
        "task_id": task.id,
        "doc_id": task.doc_id,
        "provider": p,
        "provider_label": _label(p),
        "type": type_,
        "resource_id": int(resource_id),
        "resource_title": str(res.get("title") or resource_title),
        "media_name": task.media_name,
        "delivery": {"label": delivery.get("label"), "value": delivery.get("value")},
        # price / quoted_price are the **upstream cost price**, an internal definition, not for display or comparison —
        # _publish_one still uses it to compute the retail price once; switching it to retail here would mark up twice (1.2² = 1.44)
        "price": str(price),
        "quoted_price": meta.get("quoted_price"),
        "price_changed": str(price) != str(meta.get("quoted_price")),
        # retail_* are the external prices; the frontend always reads these two
        "retail_price": rwj_pricing.retail_from_yuan_str(str(price)),
        "retail_price_cents": _retail_cents_of(str(price)),
    }


@router.post("/publish-tasks/{task_id}/publish-preview")
def publish_preview(
    task_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pre-publish price check: re-fetch the list to verify the resource, fetch the live price, return a price-bound confirmation token (10 minutes)."""
    task = _rwj_task_or_404(db, task_id, current_user)
    item = _preview_one(db, task, current_user)
    if not item.get("ok"):
        db.commit()   # Keep the price-check record (if any) even on failure
        _raise(item)
    p = item["provider"]
    db.commit()
    out = {
        **{k: v for k, v in item.items() if k not in ("ok", "doc_id", "type", "media_name")},
        "publish_enabled": bool(settings.RWJ_PUBLISH_ENABLED),
        "price_tolerance_pct": _price_tolerance_pct(),
    }
    if is_staff(current_user):
        # The upstream account balance is internal financial information, ops only
        out["balance"] = rwj_pub.channel_status(p).get("balance")
    else:
        for k in _PREVIEW_STAFF_ONLY:
            out.pop(k, None)
    return out


class PublishPayload(BaseModel):
    # The frontend sends back the external price (cents) seen on the page. Omitted = no price-change check, order at the live price.
    expected_price_cents: Optional[int] = None


@router.post("/publish-tasks/{task_id}/publish")
def publish_task(
    task_id: int,
    payload: PublishPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Place the real order (charged). **No second confirmation; publish if the balance suffices.**

    Guard chain: gate (RWJ_PUBLISH_ENABLED) → live price check before ordering → 409 if price change exceeds tolerance →
    per-order/daily cap → balance pre-deduction → compliance rescan → state-machine optimistic lock to publishing
    (commit first, prevents double click/double worker) → /add → full refund on failure.
    Timeout/unknown-result tasks stay in publishing, never auto-retried, verified by reconcile.

    With the confirmation token removed, the "provider binding" that _confirm_sig provided as a side effect is gone too —
    so p can only come from _task_provider (the task table's platform column); endpoint inputs never participate,
    and an assertion below pins this down.
    """
    if not settings.RWJ_PUBLISH_ENABLED:
        raise HTTPException(503, {
            "code": "RWJ_PUBLISH_DISABLED",
            "message": "软文匠扣费闸门未开(RWJ_PUBLISH_ENABLED=0),仅允许推草稿",
        })
    task = _rwj_task_or_404(db, task_id, current_user)
    meta = _task_meta(task)
    resource_id = meta.get("resource_id")
    resource_title = str(meta.get("resource_title") or task.media_name or "")
    type_ = str(meta.get("type") or "Website")
    if not resource_id:
        raise HTTPException(409, {"code": "NO_RESOURCE", "message": "任务缺少媒体资源信息,请重新推送草稿"})
    p = _task_provider(task, meta)
    # The site is taken only from the task table, never the request body — mixing them would order on one site with the other's resource id
    assert p == rwj_pub.provider_of(task.platform), "provider must come from task.platform"

    doc = db.get(TopicGeneratedDocORM, task.doc_id)
    topic = db.get(AiTelemetryTopicORM, task.topic_id)
    if doc is None or topic is None:
        raise HTTPException(404, "doc/topic not found")

    # Live price check before ordering — prices in the mirror table/list page may be stale
    c = get_client(p)
    try:
        info = c.ensure_session()
        level = int(info.get("level") or 1)
        _res, _delivery, price = rwj_pub.verify_resource(
            c, type_, int(resource_id), resource_title, level)
    except RwjApiError as e:
        raise _api_error(e)
    except httpx.RequestError as e:
        raise HTTPException(502, {"code": "RWJ_NETWORK",
                                  "message": f"{_label(p)}接口不可达:{e}"})
    changed = _price_changed(payload.expected_price_cents, str(price))
    if changed is not None:
        raise HTTPException(409, changed)

    res = _publish_one(db, task, doc, topic, meta, p, type_,
                       int(resource_id), resource_title, str(price), current_user)
    if not res.get("ok"):
        _raise(res)
    return res["task"]


# ── Charging wrapper ────────────────────────────────────────────────────────────────
# Paid media placement previously **charged users nothing**: cost_cents records what we pay upstream (platform cost),
# while the frontend said "confirm charge ¥X and publish". This actually wires up the wallet.
#
# Decisions:
# 1. **Charge first, publish, refund on failure**. Charging after a successful order risks the balance being spent concurrently, leaving a debt,
#    while the order is already out and upstream already paid, unrecoverable.
# 2. **Refunds in one place only**. _publish_one_inner has seven or eight failure branches; inserting refunds into each is bound to miss one,
#    so it is wrapped outside and refunded uniformly by return value.
# 3. **Ops (admin/operator) are not charged**. They act for customers or run internal placements, paid by the platform itself;
#    charging current_user would mean "whoever clicks pays", and ops wallets would be drained by customers' placements.
#    So existing accounts do **not** need bulk credits — ops publish as usual; regular users with insufficient balance get a 402 prompting a top-up.
# 4. The RWJ_CHARGE_ENABLED gate is on by default. Off = users publish free, platform pays; only for temporary use while troubleshooting.
def _charge_enabled() -> bool:
    return (os.environ.get("RWJ_CHARGE_ENABLED", "1") or "").strip().lower() in ("1", "true", "yes")


def _should_charge(user) -> bool:
    """Should this order be charged? Ops acting on behalf are not charged — the platform's own placements should not be billed to an individual."""
    return _charge_enabled() and user is not None and not is_staff(user)


def _retail_cents_of(price: str) -> Optional[int]:
    """Upstream cost price (yuan string) → external price (cents). The single source of the markup definition is services/rwj_pricing."""
    yuan = rwj_pricing.retail_from_yuan_str(price)
    return None if yuan is None else int(round(float(yuan) * 100))


def _publish_one(db: Session, task: TopicDocPublishTaskORM,
                 doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM,
                 meta: dict, p: str, type_: str, resource_id: int,
                 resource_title: str, price: str, current_user,
                 gate_cache: Optional[dict] = None) -> dict:
    """Charge → order → refund on failure. Signature identical to _publish_one_inner (both call sites pass positionally)."""
    from geo.services import balance_service

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
                action="publish.rwj.start", object_type="publish_task",
                object_id=task.id, result="denied", reason=str(exc),
            )
            db.commit()
            return _err("RESOURCE_NOT_FOUND", "Resource not found", 404, task=task)

    charged_cents = 0
    # The **external** price of this order. Not the same as charged_cents: on DUPLICATE (idempotency hit)
    # charged_cents=0 (no double refund), but the money was collected earlier, so task.cost_cents should still be this number.
    billed_cents: Optional[int] = None
    idem = f"rwjpub:{task.id}"
    if _should_charge(current_user):
        retail = _retail_cents_of(str(price))
        if retail is None:
            return _err("PRICE_UNKNOWN", "拿不到这家媒体的报价,未下单", task=task)

        # Per-order / daily cap. Here rather than in the endpoint: both single and batch entries go through _publish_one,
        # and putting it in the endpoint would miss one. Batch goes item by item and naturally stops at the media that hits the daily cap.
        over = publish_limits.check_order(db, current_user.id, retail)
        if over:
            out = _err(over["code"], over["message"], status=402, fatal=True, task=task)
            out.update({k: v for k, v in over.items() if k not in ("code", "message")})
            return out

        res = balance_service.charge(
            # ★ Made a constant: it was once hard-coded "publish_paid_media" (18 chars), while balance_ledger.kind
            #   is String(16) — SQLite does not check length so it did not break, but it broke the day we switched to PG
            db, user_id=current_user.id, kind=KIND_PUBLISH_MEDIA,
            amount_cents=retail, idem_key=idem, topic_id=task.topic_id,
            ref_type="publish_task", ref_id=str(task.id),
            note={"media": task.media_name, "upstream_price": str(price),
                  "retail_cents": retail},
        )
        # ★ Compare with module constants, not hard-coded strings: balance_service returns lowercase "insufficient",
        #   and comparing against uppercase once made the insufficient-balance check miss and order anyway (we paid upstream, the user was not charged).
        if res.status == balance_service.INSUFFICIENT:
            db.commit()
            out = _err("INSUFFICIENT_BALANCE",
                       f"积分不足:这家媒体需要 {retail} 积分,"
                       f"当前积分 {res.balance_cents}。"
                       f"充值后可继续发布。", status=402, fatal=True, task=task)
            out["required_cents"] = retail
            out["balance_cents"] = res.balance_cents
            out["topup"] = {"web": "/account/balance", "agent_payment": "moltspay"}
            return out
        db.commit()                      # Persist the charge first, then place the order
        charged_cents = res.amount_cents if res.status == balance_service.OK else 0
        billed_cents = retail

    try:
        out = _publish_one_inner(db, task, doc, topic, meta, p, type_,
                                 resource_id, resource_title, price, current_user, gate_cache,
                                 billed_cents=billed_cents)
    except Exception:  # noqa: BLE001
        # ★ The money was already committed above. If the inner call **raises** (instead of returning an error dict), the
        #   "refund if not sent" below is never reached — on 2026-09-10 a single TypeError left a customer
        #   charged 600 credits with no order placed and no refund, for four days. So refund first here, then re-raise
        #   the exception as-is: the 500 is still reported (bugs to fix must not be swallowed), but the books must not be wrong.
        if charged_cents:
            try:
                db.rollback()
                from geo.services import publish_refund
                publish_refund.refund_task(
                    db, task, "publish_failed", actor_id=current_user.id)
                db.commit()
                log.error("[rwj] ordering code raised, refunded task=%s cents=%s",
                          task.id, charged_cents, exc_info=True)
            except Exception:  # noqa: BLE001
                log.critical("[rwj] ordering raised and refund also failed task=%s cents=%s — manual refund required",
                             task.id, charged_cents, exc_info=True)
        raise

    # Refund if nothing was sent. On DUPLICATE (idempotency hit) charged_cents=0, so no double refund.
    # Goes through publish_refund, the **single refund entry** — the other three cases where value is not delivered
    # (reconciliation finds no order / media rejects the article / appeal refund succeeds) also refund there, so definitions and idempotency keys agree.
    if charged_cents and not out.get("ok"):
        from geo.services import publish_refund

        refunded = publish_refund.refund_task(
            db, task, "publish_failed", actor_id=current_user.id)
        db.commit()
        out["refunded_cents"] = refunded or 0
        charged_cents = 0
    if out.get("ok"):
        out["charged_cents"] = charged_cents
    return out


def _publish_one_inner(db: Session, task: TopicDocPublishTaskORM,
                 doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM,
                 meta: dict, p: str, type_: str, resource_id: int,
                 resource_title: str, price: str, current_user,
                 gate_cache: Optional[dict] = None,
                 *, billed_cents: Optional[int] = None) -> dict:
    """Place a real order for one media (charged): compliance gate → optimistic lock → /add → tiered handling. Commits itself.

    Does not raise HTTPException; failures return _err — single endpoints translate it back to today's error codes, batch endpoints
    collect per item. gate_cache makes one batch run the compliance rescan only once per draft.
    """
    # Compliance gate (real outbound point)
    if gate_cache is not None and doc.id in gate_cache:
        ok, reason = gate_cache[doc.id]
    else:
        ok, reason = publish_gate(
            db, doc, topic, hook="publish_paid_media",
            actor_id=current_user.id, actor_name=getattr(current_user, "name", "") or "",
        )
        if gate_cache is not None:
            gate_cache[doc.id] = (ok, reason)
    if not ok:
        db.commit()
        # Draft-level problem: other media for the same draft are blocked by the same conclusion (gate_cache reuses it,
        # no rescan), but a cross-draft batch should not be penalized by one draft — so it is not fatal
        return _err("COMPLIANCE_BLOCKED", reason, task=task)

    # Last irreversible boundary: the recheck and planned/failed → publishing must be in the same transaction.
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
                db,
                operation_context=operation,
                event_type="authorization_denied",
                action="publish.rwj.start",
                object_type="publish_task",
                object_id=task.id,
                result="denied",
                reason=str(exc),
            )
            db.commit()
            return _err(
                "RESOURCE_NOT_FOUND", "Resource not found", 404, task=task,
            )

    # State-machine optimistic lock: planned/failed → publishing; affected rows ≠ 1 = a concurrent request got there first, reject
    locked = db.execute(
        sa_text("UPDATE topic_doc_publish_tasks SET status='publishing' "
                "WHERE id=:id AND status IN ('planned','failed')"),
        {"id": task.id},
    )
    if locked.rowcount != 1:
        db.rollback()
        return _err("BAD_STATUS", "任务状态已变化(可能有并发操作),请刷新", task=task)
    meta["publish_started_at"] = datetime.utcnow().isoformat()
    meta["confirmed_price"] = str(price)
    db.execute(
        sa_text("UPDATE topic_doc_publish_tasks SET channel_meta_json=:m WHERE id=:id"),
        {"id": task.id, "m": json.dumps(meta, ensure_ascii=False)},
    )
    if operation is not None:
        append_system_audit(
            db,
            operation_context=operation,
            event_type="authorization_rechecked",
            action="publish.rwj.start",
            object_type="publish_task",
            object_id=task.id,
            result="success",
        )
        append_system_audit(
            db,
            operation_context=operation,
            event_type="task_started",
            action="publish.rwj.start",
            object_type="publish_task",
            object_id=task.id,
            result="success",
        )
    db.commit()   # Persist "initiated" evidence first, then go out

    try:
        result = rwj_pub.publish(
            doc, type_=type_, provider=p, resource_id=int(resource_id),
            resource_title=resource_title, expected_price=str(price),
            # Downstream order number = this task's id. A timeout retry hits the platform with the same number →
            # the platform returns the first order idempotently without charging twice (interface 2 of the "downstream agent API integration doc")
            out_order_no=f"vgl-{task.id}",
        )
    except rwj_pub.RwjBalanceError as e:
        db.refresh(task)
        task.status = "planned"
        task.last_error = f"余额不足:{e}"
        compliance_audit.record(
            db, topic_id=task.topic_id, event_type="paid_publish",
            object_type="publish_task", object_id=task.id,
            actor_type="admin", actor_id=current_user.id,
            detail={"action": "publish_failed", "reason": "balance", "message": str(e)},
        )
        db.commit()
        # Insufficient balance is account-level — later media in the batch will also fail, so abort
        exc = _api_error(e)
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _err(detail.get("code", "RWJ_BALANCE"), detail.get("message", str(e)),
                    exc.status_code, fatal=True, task=task)
    except httpx.ConnectError as e:
        # Connection never established — the request certainly did not reach the server, safe to roll back
        db.refresh(task)
        task.status = "planned"
        task.last_error = f"网络连接失败,未下单:{e}"
        db.commit()
        return _err("RWJ_NETWORK", f"网络连接失败,未下单:{e}", 502,
                    fatal=True, task=task)
    except (httpx.RequestError, RwjAuthError) as e:
        # Timeout/disconnected midway — result unknown, stay in publishing, never auto-retry
        db.refresh(task)
        task.last_error = f"下单结果未知({e}),请稍后「拉取回执」对账,勿重复下单"
        compliance_audit.record(
            db, topic_id=task.topic_id, event_type="paid_publish",
            object_type="publish_task", object_id=task.id,
            actor_type="admin", actor_id=current_user.id,
            detail={"action": "publish_unknown", "message": str(e)},
        )
        db.commit()
        # Unknown result = the channel is no longer reliable; the remaining media in the batch must not risk ordering
        return _err("RWJ_UNKNOWN_RESULT",
                    "下单结果未知,任务已锁定,请稍后用「拉取回执」对账确认,不要重复下单",
                    502, fatal=True, task=task)
    except RwjApiError as e:
        # Server returned an explicit error — no order placed
        db.refresh(task)
        task.status = "failed"
        task.last_error = f"下单失败:{e}"
        compliance_audit.record(
            db, topic_id=task.topic_id, event_type="paid_publish",
            object_type="publish_task", object_id=task.id,
            actor_type="admin", actor_id=current_user.id,
            detail={"action": "publish_failed", "message": str(e)},
        )
        db.commit()
        # This media's own problem (resource delisted/invalid parameters) — skip it in the batch and continue with the next
        exc = _api_error(e)
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _err(detail.get("code", "RWJ_API"), detail.get("message", str(e)),
                    exc.status_code, task=task)

    # Order succeeded: wait for the media to produce a URL (pending_url); receipts are pulled by reconcile / poller
    db.refresh(task)
    task.status = "pending_url"
    task.last_error = None
    # ★ Record the **actual charge** (external price), not the upstream cost price.
    #   This used to be `rwj_pub.price_to_cents(result.price)` — result.price is the upstream cost price returned by Ruanwenjiang,
    #   persisted ×100 with neither markup nor floor. Two consequences:
    #     · the placement center card's "charged 300 credits" disagreed with the button's "charge 400 credits" (400 was actually charged);
    #     · cost_cents is meant to be "amount actually paid, for report aggregation", and publish_attribution
    #       sums cost_cents for the placement cost report — counting cost price systematically omits one markup.
    #   The upstream price is not lost: meta["confirmed_price"] below holds it, and reconciliation still uses it.
    task.cost_cents = billed_cents if billed_cents is not None else 0
    meta = _task_meta(task)
    meta["confirmed_price"] = result.price
    meta["add_response"] = result.raw     # Archive the full raw /add response
    meta["out_order_no"] = f"vgl-{task.id}"
    # The submit response carries the order number and article id directly — receipts query by them precisely instead of paging and guessing by title
    if result.order_no:
        meta["order_no"] = result.order_no
    if result.article_id:
        meta["article_id"] = result.article_id
    if result.idempotent:
        # The platform judged this a duplicate submission (no double charge); recorded for reconciliation
        meta["idempotent_hit"] = True
    if result.dropped_images:
        meta["dropped_images"] = result.dropped_images
    task.channel_meta_json = json.dumps(meta, ensure_ascii=False, default=str)
    compliance_audit.record(
        db, topic_id=task.topic_id, event_type="paid_publish",
        object_type="publish_task", object_id=task.id,
        actor_type=_actor_type(current_user), actor_id=current_user.id,
        actor_name=getattr(current_user, "name", "") or "",
        detail={"action": "published_order", "price": result.price,
                "resource_id": resource_id, "resource_title": resource_title},
    )
    db.commit()
    out = {"ok": True, "task": _task_out(db, task, current_user),
           "dropped_images": result.dropped_images}
    if is_staff(current_user):
        out.update({"price": result.price, "cost_cents": task.cost_cents})
    return out


# ───────────── Batch: one price check, one confirmation, serial per-media ordering ─────────────

class BatchPreviewPayload(BaseModel):
    task_ids: list[int] = Field(default_factory=list)


class BatchPublishItem(BaseModel):
    task_id: int
    expected_price_cents: Optional[int] = None


class BatchPublishPayload(BaseModel):
    """items carry each media's expected price; task_ids is shorthand without expected prices (no price-change check)."""
    items: list[BatchPublishItem] = Field(default_factory=list)
    task_ids: list[int] = Field(default_factory=list)

    def expected_map(self) -> dict[int, Optional[int]]:
        out: dict[int, Optional[int]] = {t: None for t in self.task_ids}
        for it in self.items:
            out[it.task_id] = it.expected_price_cents
        return out


def _retail_sum_cents(items: list[dict]) -> int:
    return sum(int(i.get("retail_price_cents") or 0) for i in items)


def _price_sum(items: list[dict]) -> str:
    total = Decimal("0")
    for i in items:
        try:
            total += Decimal(str(i["price"]))
        except (InvalidOperation, KeyError):
            continue
    return format(total.normalize(), "f")


def _dedup_ids(task_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for tid in task_ids:
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


@router.post("/publish-tasks/batch-publish-preview")
def batch_publish_preview(
    payload: BatchPreviewPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batch price check: re-fetch live prices per media, return details + total, and issue one confirmation token covering all media."""
    ids = _dedup_ids(payload.task_ids)
    if not ids:
        raise HTTPException(422, {"code": "NO_TASK", "message": "请先勾选要投放的任务"})
    if len(ids) > settings.RWJ_MULTI_MAX:
        raise HTTPException(422, {
            "code": "TOO_MANY_TASKS",
            "message": f"一次最多投放 {settings.RWJ_MULTI_MAX} 家媒体,请分批提交",
        })

    items: list[dict] = []
    failed: list[dict] = []
    for tid in ids:
        task = _rwj_task_or_404(db, tid, current_user)   # Per-item ownership check
        one = _preview_one(db, task, current_user)
        if one.get("ok"):
            items.append(one)
        else:
            failed.append(one)
            if one.get("fatal"):
                break     # Channel-level failure, no point trying the remaining media
    db.commit()
    if not items:
        _raise(failed[0] if failed else _err("NO_TASK", "没有可下单的任务"))

    staff = is_staff(current_user)
    drop = ("ok",) if staff else ("ok",) + _PREVIEW_STAFF_ONLY
    total_cents = _retail_sum_cents(items)
    out = {
        "items": [{k: v for k, v in i.items() if k not in drop} for i in items],
        "failed": [{k: v for k, v in f.items() if k not in ("ok", "status")} for f in failed],
        "count": len(items),
        "total_retail_cents": total_cents,
        "total_retail_price": f"{total_cents / 100:.2f}",
        "price_changed_count": sum(1 for i in items if i.get("price_changed")),
        "publish_enabled": bool(settings.RWJ_PUBLISH_ENABLED),
        "price_tolerance_pct": _price_tolerance_pct(),
    }
    if staff:
        providers = {i["provider"] for i in items}
        out["total_price"] = _price_sum(items)      # Total upstream cost
        out["balances"] = {p: rwj_pub.channel_status(p).get("balance") for p in providers}
    return out


@router.post("/publish-tasks/batch-publish")
def batch_publish(
    payload: BatchPublishPayload,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batch ordering (charged). **No second confirmation; publish if the balance suffices.**

    Guardrails are identical to the single version, with "one media" replaced by "serially per media": gate → re-check price per media →
    409 with price-change details if any media's increase exceeds tolerance → batch total cap → each media goes through _publish_one
    (per-order/daily cap + optimistic lock + /add + tiered handling).
    On account-level failures (insufficient balance / limit exceeded / login session invalid / network unreachable / unknown result) abort immediately;
    the remaining media stay in planned as-is and are never blindly charged further. **Never order concurrently.**
    """
    if not settings.RWJ_PUBLISH_ENABLED:
        raise HTTPException(503, {
            "code": "RWJ_PUBLISH_DISABLED",
            "message": "付费发稿扣费闸门未开(RWJ_PUBLISH_ENABLED=0),仅允许推草稿",
        })
    expected = payload.expected_map()
    ids = _dedup_ids(list(expected.keys()))
    if not ids:
        raise HTTPException(422, {"code": "NO_TASK", "message": "请先勾选要投放的任务"})
    if len(ids) > settings.RWJ_MULTI_MAX:
        raise HTTPException(422, {
            "code": "TOO_MANY_TASKS",
            "message": f"一次最多投放 {settings.RWJ_MULTI_MAX} 家媒体,请分批提交",
        })

    # Re-check price per media
    tasks: dict[int, TopicDocPublishTaskORM] = {}
    items: list[dict] = []
    for tid in ids:
        task = _rwj_task_or_404(db, tid, current_user)
        tasks[tid] = task
        one = _preview_one(db, task, current_user)
        if not one.get("ok"):
            db.commit()
            _raise(one)
        items.append(one)
    db.commit()

    bumped = [
        {**chg, "task_id": i["task_id"], "media_name": i["media_name"]}
        for i in items
        for chg in [_price_changed(expected.get(i["task_id"]), i["price"])]
        if chg is not None
    ]
    if bumped:
        total = _retail_sum_cents(items)
        raise HTTPException(409, {
            "code": "PRICE_CHANGED",
            "message": f"{len(bumped)} 家媒体涨价了(当前合计 ¥{total / 100:.2f}),未下单。"
                       f"确认后可重新提交。",
            "items": bumped,
            "total_retail_cents": total,
        })

    over = publish_limits.check_batch(
        db, current_user.id, _retail_sum_cents(items), len(items))
    if over:
        raise HTTPException(402, over)

    succeeded: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []
    gate_cache: dict[int, tuple] = {}     # One compliance rescan per draft per batch
    aborted = False
    for i in items:
        task = tasks[i["task_id"]]
        if aborted:
            skipped.append({"task_id": task.id, "media_name": task.media_name})
            continue
        doc = db.get(TopicGeneratedDocORM, task.doc_id)
        topic = db.get(AiTelemetryTopicORM, task.topic_id)
        if doc is None or topic is None:
            failed.append(_err("NOT_FOUND", "稿件或主题不存在", 404, task=task))
            continue
        out = _publish_one(db, task, doc, topic, _task_meta(task), i["provider"],
                           i["type"], i["resource_id"], i["resource_title"],
                           i["price"], current_user, gate_cache)
        if out.get("ok"):
            succeeded.append(out)
        else:
            failed.append(out)
            if out.get("fatal"):
                aborted = True

    return {
        "succeeded": [s["task"] for s in succeeded],
        "failed": [{k: v for k, v in f.items() if k not in ("ok", "status")} for f in failed],
        "skipped": skipped,
        "aborted": aborted,
        "total_charged_cents": sum(s.get("charged_cents") or 0 for s in succeeded),
    }


# ─────────────────────── Reconciliation / receipts ──────────────────────────

@router.post("/publish-tasks/{task_id}/reconcile")
def reconcile(
    task_id: int,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually pull receipts: query the article list to advance state (verify publishing / get URL for pending_url)."""
    task = _rwj_task_or_404(db, task_id, current_user)
    if task.status not in ("publishing", "pending_url"):
        raise HTTPException(409, {"code": "BAD_STATUS", "message": f"任务当前状态 {task.status} 无需对账"})
    try:
        out = reconcile_task(db, task, actor_id=current_user.id)
    except RwjApiError as e:
        db.rollback()
        raise _api_error(e)
    except httpx.RequestError as e:
        db.rollback()
        raise HTTPException(502, {"code": "RWJ_NETWORK", "message": f"软文匠接口不可达:{e}"})
    db.commit()
    return {"task": _task_out(db, task, current_user), **out}
