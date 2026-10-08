"""Flywheel P1 (section 5.3) -- shared publish task service (used by both user side and ops side).

Key step of the loop: after a manual / semi-auto / external media publish, **only backfilling the real article URL counts as a real placement**,
which then registers re-tests and enters the reward window (section 5.2/6 core invariant). auto channels (Mediumsly/official site) get the URL via the
existing auto-publish pipeline; this mainly carries the manual path "choose publish -> pending backfill -> backfill confirm".

Short term it coexists with topic_generated_docs.publish_targets_json: once a task is published, write back to
publish_targets_json so existing backfill_cited_by / frontend display keep working (section 5.3.8).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, TopicDocPublishTaskORM, TopicGeneratedDocORM,
)
from geo.services.citation_match import canonicalize

log = logging.getLogger(__name__)

# Prior states that allow URL backfill (published / cancelled no longer accept)
SUBMITTABLE_STATUSES = {"planned", "pending_publish", "pending_url", "url_submitted", "failed"}
# Channels that can auto-publish (can get the real URL); the rest use manual backfill
AUTO_PLATFORMS = {"mediumsly", "官网", "官网博客", "自有站点"}
PLATFORM_VERIFICATION_INITIAL_DELAY = timedelta(seconds=max(
    1, int(os.environ.get("CTRIP_VERIFY_INITIAL_DELAY_SECONDS", "3600"))))
PLATFORM_VERIFICATION_CONFIRM_DELAY = timedelta(seconds=max(
    1, int(os.environ.get("CTRIP_VERIFY_CONFIRM_DELAY_SECONDS", "86400"))))


def _infer_channel(platform: str) -> tuple[str, str]:
    """Infer (channel_type, publish_mode) from the platform name."""
    if (platform or "").strip().lower() in AUTO_PLATFORMS:
        return "auto_account", "auto"
    return "manual_account", "manual"


def _load_json(raw: Optional[str]) -> Any:
    try:
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


# channel_meta keys visible to the user side. An allowlist, not a denylist -- if the upstream response gains a field later,
# a denylist silently leaks it; an allowlist only gives less.
_META_PUBLIC_KEYS = (
    "type", "delivery", "draft_id", "target_platform",
    "select_reason", "auto_selected", "quoted_at",
    # Images not carried in the body -- the user's own images, our public URLs, nothing upstream
    "dropped_images",
    "candidate_url", "url_verification",
)

# Platform name shown to users for paid publish tasks. The task table's platform column **must** keep storing the vendor's Chinese name
# (_task_provider / rwj_order_poller / rwj_liveness all rely on it to look up the site), so de-branding
# can only be done at this layer.
PAID_PLATFORM_DISPLAY = "付费媒体"


def _charged_cents(db, t: TopicDocPublishTaskORM) -> Optional[int]:
    """How much this task actually charged the user (net). cost_cents is our purchase price paid upstream and must not be shown to users."""
    if db is None:
        return None
    try:
        from geo.models.balance import KIND_REFUND, BalanceLedgerORM

        rows = (
            db.query(BalanceLedgerORM)
              .filter(BalanceLedgerORM.ref_type == "publish_task",
                      BalanceLedgerORM.ref_id == str(t.id))
              .all()
        )
    except Exception:  # noqa: BLE001 — a missing billing table shouldn't break the task list
        return None
    total = 0
    for r in rows:
        amt = int(r.amount_cents or 0)
        total += -amt if r.kind == KIND_REFUND else amt
    return max(0, total)


def task_to_dict(t: TopicDocPublishTaskORM, *, redact: bool = True,
                 db=None) -> dict[str, Any]:
    """Task output.

    ⚠️ `redact=True` is the default and must stay the default -- this function has about ten call sites;
    if the default returned everything, one missed argument would send the supplier vendor, upstream resource id, purchase price and raw upstream response
    to the user. Places that need internal fields pass `redact=False` explicitly.
    """
    meta = _load_json(t.channel_meta_json)
    # The worker claim-verification token is a service-to-service credential and is never exposed regardless of user role.
    if isinstance(meta, dict) and isinstance(meta.get("url_verification"), dict):
        meta = dict(meta)
        verification = dict(meta["url_verification"])
        verification.pop("claim", None)
        meta["url_verification"] = verification
    out = {
        "id": t.id, "topic_id": t.topic_id, "doc_id": t.doc_id,
        "vscl_action_id": t.vscl_action_id,
        "platform": t.platform, "media_name": t.media_name,
        "channel_type": t.channel_type, "publish_mode": t.publish_mode,
        "status": t.status,
        "planned_publish_at": t.planned_publish_at.isoformat() if t.planned_publish_at else None,
        "actual_published_at": t.actual_published_at.isoformat() if t.actual_published_at else None,
        "published_url": t.published_url,
        "proof": _load_json(t.proof_json),
        "channel_meta": meta,
        "cost_cents": t.cost_cents,
        "last_error": t.last_error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if not redact:
        out["is_paid_media"] = (t.platform or "") in _paid_platform_names()
        return out

    is_paid = (t.platform or "") in _paid_platform_names()
    if isinstance(meta, dict):
        out["channel_meta"] = {k: v for k, v in meta.items() if k in _META_PUBLIC_KEYS}
        quoted = meta.get("quoted_price")
        if quoted is not None:
            from geo.services import rwj_pricing
            out["retail_price_yuan"] = rwj_pricing.retail_from_yuan_str(quoted)

    # ★ `platform` itself is the vendor's Chinese name -- adding just platform_display isn't enough;
    #   the frontend not showing it doesn't mean it wasn't sent. Here platform is replaced with the display value directly;
    #   the frontend should check the is_paid_media boolean to tell "is this a paid publish task".
    display = PAID_PLATFORM_DISPLAY if is_paid else t.platform
    out["platform"] = display
    out["platform_display"] = display
    out["is_paid_media"] = is_paid
    out.pop("cost_cents", None)          # purchase price, internal finance info
    out["charged_cents"] = _charged_cents(db, t)
    return out


def _paid_platform_names() -> tuple:
    """Vendor Chinese name for paid publishing. Lazy import: publish_task_service is reverse-depended on by the rwj side."""
    try:
        from geo.services.ruanwenjiang_publisher import PLATFORM_NAMES

        return tuple(PLATFORM_NAMES)
    except Exception:  # noqa: BLE001
        return ()


def _dump_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def _task_meta(task: TopicDocPublishTaskORM) -> dict:
    value = _load_json(task.channel_meta_json)
    return value if isinstance(value, dict) else {}


def _validate_url_for_task(db: Session, task: TopicDocPublishTaskORM, url: str) -> tuple[str, str]:
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("请填写以 http(s):// 开头的完整文章地址")
    canon = canonicalize(url)
    if not canon:
        raise ValueError("URL 无法解析")
    dup = (
        db.query(TopicDocPublishTaskORM)
          .filter(
              TopicDocPublishTaskORM.topic_id == task.topic_id,
              TopicDocPublishTaskORM.id != task.id,
              TopicDocPublishTaskORM.url_canonical == canon,
              TopicDocPublishTaskORM.status == "published",
          ).first()
    )
    if dup is not None:
        raise ValueError(f"该文章地址已被任务 #{dup.id} 回填过,请勿重复")
    return url, canon


def list_tasks(db: Session, topic_id: int, *, doc_id: Optional[int] = None,
               statuses: Optional[list[str]] = None) -> list[TopicDocPublishTaskORM]:
    q = db.query(TopicDocPublishTaskORM).filter(TopicDocPublishTaskORM.topic_id == topic_id)
    if doc_id is not None:
        q = q.filter(TopicDocPublishTaskORM.doc_id == doc_id)
    if statuses:
        q = q.filter(TopicDocPublishTaskORM.status.in_(statuses))
    return q.order_by(TopicDocPublishTaskORM.id.desc()).all()


def create_tasks(db: Session, doc: TopicGeneratedDocORM, topic: AiTelemetryTopicORM,
                 targets: list[dict], *, assigned_to_user_id: Optional[int] = None,
                 initial_status: str = "pending_url",
                 channel_meta: Optional[dict] = None,
                 operation_context_id: Optional[int] = None,
                 ) -> list[TopicDocPublishTaskORM]:
    """"Choose publish" creates tasks: one draft x multiple media channels -> multiple pending publish/backfill tasks (idempotent).

    targets: [{platform, media_name?, channel_type?, publish_mode?, planned_publish_at?,
               channel_meta?}]
    Idempotent: if a non-cancelled task already exists for the same (doc, platform, media_name), reuse it instead of creating a new one.
    Paid media channels (Ruanwenjiang) use initial_status="planned" (draft synced, awaiting payment confirmation)
    + channel_meta (resource snapshot/draft_id/quote); default arguments keep manual path behavior unchanged.
    When placing with multiple media at once, each task's resource/quote differs -- the `channel_meta` in target
    overrides per task; the function-level channel_meta is only the default for those without one.
    """
    out: list[TopicDocPublishTaskORM] = []
    for tg in targets:
        platform = str(tg.get("platform") or "").strip()
        if not platform:
            continue
        media_name = str(tg.get("media_name") or "").strip()
        ch, mode = _infer_channel(platform)
        channel_type = str(tg.get("channel_type") or ch)
        publish_mode = str(tg.get("publish_mode") or mode)
        meta = tg.get("channel_meta")
        if not isinstance(meta, dict):
            meta = channel_meta
        binding_id = tg.get("aitoearn_binding_id")
        existing_query = db.query(TopicDocPublishTaskORM).filter(
            TopicDocPublishTaskORM.doc_id == doc.id,
            TopicDocPublishTaskORM.status != "cancelled",
        )
        if binding_id is not None:
            existing_query = existing_query.filter(
                TopicDocPublishTaskORM.aitoearn_binding_id == binding_id)
        else:
            existing_query = existing_query.filter(
                TopicDocPublishTaskORM.platform == platform,
                TopicDocPublishTaskORM.media_name == media_name,
            )
        existing = existing_query.first()
        if existing is not None:
            # Reuse existing task; in the planned stage channel metadata may be refreshed (re-pushing a draft updates the quote)
            if meta is not None and existing.status == "planned":
                existing.channel_meta_json = json.dumps(meta, ensure_ascii=False)
            if (
                operation_context_id is not None
                and existing.status in ("planned", "failed")
            ):
                # A new attempt on the same business task uses this initiator's context; historical context and audit stay unchanged.
                existing.operation_context_id = operation_context_id
            out.append(existing)
            continue
        task = TopicDocPublishTaskORM(
            topic_id=topic.id, doc_id=doc.id, vscl_action_id=doc.vscl_action_id,
            operation_context_id=operation_context_id,
            platform=platform, media_name=media_name,
            aitoearn_binding_id=binding_id,
            channel_type=channel_type, publish_mode=publish_mode,
            status=initial_status,
            assigned_to_user_id=assigned_to_user_id,
            channel_meta_json=(json.dumps(meta, ensure_ascii=False)
                               if meta is not None else None),
        )
        db.add(task)
        out.append(task)
    db.flush()
    return out


def submit_url(db: Session, task: TopicDocPublishTaskORM, url: str, *,
               actual_published_at: Optional[datetime] = None,
               proof: Optional[dict] = None,
               actor_id: Optional[int] = None) -> TopicDocPublishTaskORM:
    """Backfill the real article URL (manual path): validate status, then go through the shared mark_published loop.

    Raising ValueError means a business error (status not allowed / invalid URL / duplicate conflict); the API layer turns it into 4xx.
    """
    if task.status not in SUBMITTABLE_STATUSES:
        raise ValueError(f"任务当前状态 {task.status} 不允许回填")
    verification = _task_meta(task).get("url_verification")
    if isinstance(verification, dict) and verification.get("verification_method") == "platform_api":
        raise ValueError("平台接口核验任务不能通过普通链接回填绕过审核")
    return mark_published(
        db, task, url,
        actual_published_at=actual_published_at, proof=proof, actor_id=actor_id,
    )


def submit_candidate_url(db: Session, task: TopicDocPublishTaskORM, url: str, *,
                         actual_published_at: Optional[datetime] = None,
                         proof: Optional[dict] = None,
                         actor_id: Optional[int] = None) -> TopicDocPublishTaskORM:
    """Register a post-publish candidate URL: show it to people, but don't enter it into the formal placement ledger.

    Auto/plugin channels often can only derive the public URL from the article id. This URL may be shown for ops to click,
    but mark_published is only called after a successful probe or manual confirmation.
    """
    if task.status not in SUBMITTABLE_STATUSES | {"publishing", "published"}:
        raise ValueError(f"任务当前状态 {task.status} 不允许回填")
    url, canon = _validate_url_for_task(db, task, url)
    now = datetime.utcnow()
    if task.status == "published":
        # When the same draft is published to the same account again, create_tasks reuses this task; the new link becomes
        # the current candidate pending review, and the old link stays in the placement history in doc.publish_targets_json.
        task.published_url = None
        task.url_canonical = None
        task.actual_published_at = None
        task.verified_at = None
        task.retest_probe_id = None
    meta = _task_meta(task)
    source = (proof or {}).get("source") if isinstance(proof, dict) else None
    verification_method = str((proof or {}).get("verification_method") or "url").strip()
    if verification_method not in {"url", "platform_api"}:
        verification_method = "url"
    platform_code = str((proof or {}).get("platform_code") or "").strip().lower()
    external_post_id = str((proof or {}).get("external_post_id") or "").strip()
    verification_executor = str((proof or {}).get("verification_executor") or "").strip()
    if verification_executor not in {"", "plugin", "server_pool"}:
        raise ValueError("平台核验执行器无效")
    if verification_method == "platform_api" and (not platform_code or not external_post_id):
        raise ValueError("平台接口核验需要 platform_code 和 external_post_id")
    if len(platform_code) > 50 or len(external_post_id) > 200:
        raise ValueError("平台接口核验标识过长")
    candidate = {
        "candidate_url": url,
        "url_canonical": canon,
        "status": "pending",
        "attempts": 0,
        "next_at": (now + (
            PLATFORM_VERIFICATION_INITIAL_DELAY
            if verification_method == "platform_api"
            else timedelta(minutes=1)
        )).isoformat(),
        "last_at": None,
        "last_error": None,
        "source": source,
        "submitted_at": now.isoformat(),
        "verification_method": verification_method,
    }
    if verification_method == "platform_api":
        candidate.update({
            "initial_delay_seconds": int(PLATFORM_VERIFICATION_INITIAL_DELAY.total_seconds()),
            "confirmation_delay_seconds": int(PLATFORM_VERIFICATION_CONFIRM_DELAY.total_seconds()),
            "first_published_at": None,
            "last_observed_at": None,
            "last_platform_status": None,
        })
    if platform_code:
        candidate["platform_code"] = platform_code
    if external_post_id:
        candidate["external_post_id"] = external_post_id
    if verification_executor:
        candidate["verification_executor"] = verification_executor
    meta["candidate_url"] = url
    meta["url_verification"] = candidate
    task.channel_meta_json = _dump_json(meta)
    task.status = "url_submitted"
    task.submitted_by_user_id = actor_id
    task.submitted_at = now
    task.actual_published_at = actual_published_at
    task.last_error = "候选 URL 已生成，等待公开探测或人工确认"
    if proof is not None:
        task.proof_json = _dump_json(proof)
    db.flush()
    return task


def _platform_publish_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip().replace("/", "-")
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone(timedelta(hours=8)))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_metadata_time(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at 格式应为 ISO 时间") from exc
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _observation_time(raw: Optional[str], now: datetime) -> datetime:
    if not raw:
        return now
    value = _utc_metadata_time(raw)
    if value > now + timedelta(minutes=5):
        raise ValueError("observed_at 不能晚于服务器时间超过 5 分钟")
    return value


def report_platform_verification(
    db: Session,
    task: TopicDocPublishTaskORM,
    *,
    platform_code: str,
    external_post_id: str,
    status: str,
    raw_status: Optional[int] = None,
    audit_fail_reason: str = "",
    publish_time: str = "",
    observation_id: str = "",
    observed_at: str = "",
    error_message: str = "",
    actor_id: Optional[int] = None,
) -> TopicDocPublishTaskORM:
    """Accept reports from the side holding the platform login session and normalize them into publish task status."""
    platform_code = (platform_code or "").strip().lower()
    external_post_id = (external_post_id or "").strip()
    status = (status or "").strip().lower()
    if not platform_code or len(platform_code) > 50:
        raise ValueError("核验平台标识无效")
    if not external_post_id or len(external_post_id) > 200:
        raise ValueError("核验文章 ID 无效")
    if status not in {"reviewing", "published", "rejected", "draft", "not_found", "query_error"}:
        raise ValueError(f"不支持的发布核验状态: {status}")
    audit_fail_reason = (audit_fail_reason or "").strip()[:2000]
    publish_time = (publish_time or "").strip()[:100]
    observation_id = (observation_id or "").strip()[:200]
    error_message = (error_message or "").strip()[:2000]

    meta = _task_meta(task)
    state = meta.get("url_verification") if isinstance(meta.get("url_verification"), dict) else {}
    if state.get("verification_method") != "platform_api":
        raise ValueError("该任务不是平台接口核验任务")
    if str(state.get("platform_code") or "").lower() != platform_code:
        raise ValueError("核验平台与任务不一致")
    if str(state.get("external_post_id") or "") != external_post_id:
        raise ValueError("核验文章 ID 与任务不一致")

    now = datetime.utcnow()
    observed = _observation_time(observed_at, now)
    last_observed = _utc_metadata_time(state.get("last_observed_at")) \
        if state.get("last_observed_at") else None
    if observation_id and observation_id == state.get("last_observation_id"):
        return task
    if last_observed is not None and observed <= last_observed:
        return task
    if task.status in {"published", "failed"}:
        return task
    if task.status != "url_submitted":
        raise ValueError(f"任务当前状态 {task.status} 不允许回报核验结果")

    first_published = _utc_metadata_time(state.get("first_published_at")) \
        if state.get("first_published_at") else None
    retry_delay = (PLATFORM_VERIFICATION_CONFIRM_DELAY
                   if first_published else PLATFORM_VERIFICATION_INITIAL_DELAY)
    state.update({
        "attempts": int(state.get("attempts", 0) or 0) + 1,
        "last_at": observed.isoformat(),
        "last_observed_at": observed.isoformat(),
        "last_observation_id": observation_id or None,
        "last_platform_status": status,
        "raw_status": raw_status,
        "audit_fail_reason": audit_fail_reason or None,
        "last_error": None,
    })

    if status == "published":
        if first_published is None:
            state.update({
                "status": "published_pending_confirmation",
                "first_published_at": observed.isoformat(),
                "first_publish_time": publish_time or None,
                "publish_time": publish_time or None,
                "next_at": (observed + PLATFORM_VERIFICATION_CONFIRM_DELAY).isoformat(),
            })
            meta["url_verification"] = state
            task.channel_meta_json = _dump_json(meta)
            task.last_error = None
            db.flush()
            return task

        next_at = _utc_metadata_time(state.get("next_at")) \
            if state.get("next_at") else first_published + PLATFORM_VERIFICATION_CONFIRM_DELAY
        if observed < next_at:
            state.update({
                "status": "published_pending_confirmation",
                "next_at": next_at.isoformat(),
                "publish_time": publish_time or state.get("publish_time"),
            })
            meta["url_verification"] = state
            task.channel_meta_json = _dump_json(meta)
            db.flush()
            return task

        state.update({
            "status": "published_confirmed",
            "next_at": None,
            "publish_time": publish_time or state.get("publish_time"),
        })
        meta["url_verification"] = state
        task.channel_meta_json = _dump_json(meta)
        proof = _load_json(task.proof_json)
        proof = proof if isinstance(proof, dict) else {}
        proof.update({
            "source": "platform_api",
            "platform_code": platform_code,
            "external_post_id": external_post_id,
            "raw_status": raw_status,
        })
        return mark_published(
            db,
            task,
            str(state.get("candidate_url") or meta.get("candidate_url") or ""),
            actual_published_at=_platform_publish_time(
                str(state.get("first_publish_time") or publish_time or "")),
            proof=proof,
            actor_id=actor_id,
        )

    if status == "rejected":
        state.update({"status": "rejected", "next_at": None})
        reason = audit_fail_reason or "平台审核未通过"
        task.status = "failed"
        task.published_url = None
        task.actual_published_at = None
        task.last_error = f"携程审核未通过：{reason}"[:2000]
        task.verified_at = now
    elif status == "query_error":
        state.update({
            "status": "published_pending_confirmation" if first_published else "query_error",
            "next_at": (observed + retry_delay).isoformat(),
            "last_error": error_message or "携程审核状态查询失败",
        })
        task.last_error = state["last_error"]
    elif status == "not_found":
        state.update({
            "status": "published_pending_confirmation" if first_published else status,
            "next_at": (observed + retry_delay).isoformat(),
        })
        task.last_error = "携程内容管理页暂未找到该文章，稍后继续查询"
        state["last_error"] = task.last_error
    else:
        state.update({
            "status": "published_pending_confirmation" if first_published else status,
            "next_at": (observed + retry_delay).isoformat(),
        })
        task.last_error = None
    meta["url_verification"] = state
    task.channel_meta_json = _dump_json(meta)
    db.flush()
    return task


def confirm_candidate_url(db: Session, task: TopicDocPublishTaskORM, *,
                          actor_id: Optional[int] = None,
                          proof: Optional[dict] = None) -> TopicDocPublishTaskORM:
    meta = _task_meta(task)
    verification = meta.get("url_verification") if isinstance(meta.get("url_verification"), dict) else {}
    if verification.get("verification_method") == "platform_api":
        raise ValueError("平台接口核验任务不能人工确认候选链接")
    url = str(verification.get("candidate_url") or meta.get("candidate_url") or "").strip()
    if not url:
        raise ValueError("没有可确认的候选 URL")
    merged_proof = {"source": "manual_candidate_confirm", **(proof or {})}
    return mark_published(db, task, url, proof=merged_proof, actor_id=actor_id)


def mark_published(db: Session, task: TopicDocPublishTaskORM, url: str, *,
                   actual_published_at: Optional[datetime] = None,
                   proof: Optional[dict] = None,
                   actor_id: Optional[int] = None) -> TopicDocPublishTaskORM:
    """Shared loop after obtaining the real URL: normalize -> dedupe -> published -> sync doc -> register re-test.

    Shared by manual backfill (submit_url) and paid media receipts (Ruanwenjiang reconcile/poller) --
    the core invariant is guarded here: no valid URL means no published; only published registers re-tests.
    Raising ValueError means a business error; the caller decides between 4xx or recording last_error.
    """
    url, canon = _validate_url_for_task(db, task, url)

    doc = db.get(TopicGeneratedDocORM, task.doc_id)
    topic = db.get(AiTelemetryTopicORM, task.topic_id)
    if doc is None or topic is None:
        raise ValueError("稿件或话题不存在")

    now = datetime.utcnow()
    task.published_url = url
    task.url_canonical = canon
    task.actual_published_at = actual_published_at or task.actual_published_at or now
    task.submitted_by_user_id = actor_id
    task.submitted_at = now
    task.verified_at = now
    task.status = "published"
    task.last_error = None
    if proof is not None:
        task.proof_json = json.dumps(proof, ensure_ascii=False)

    # Write back to doc.publish_targets_json (same structure as existing _mark_platform_target, so
    # backfill_cited_by attribution by URL and frontend display keep working)
    try:
        targets = json.loads(doc.publish_targets_json or "[]")
        if not isinstance(targets, list):
            targets = []
    except Exception:  # noqa: BLE001
        targets = []
    targets.append({
        "platform": task.platform, "media": task.media_name, "url": url,
        "marked_at": now.isoformat(), "marked_by": actor_id or 0,
    })
    doc.publish_targets_json = json.dumps(targets, ensure_ascii=False)
    if doc.status != "published":
        doc.status = "published"

    meta = _task_meta(task)
    if meta.get("url_verification"):
        meta["url_verification"]["status"] = "verified"
        meta["url_verification"]["verified_at"] = now.isoformat()
        task.channel_meta_json = _dump_json(meta)

    # Register the post-placement targeted re-test probe (only a real URL reaches here -> reward window starts from the real placement)
    try:
        from geo.services.retest_service import schedule_for_doc
        probe = schedule_for_doc(db, doc, topic)
        if probe is not None:
            db.flush()             # let the new probe get its autoincrement id before writing back the link
            task.retest_probe_id = probe.id
    except Exception:  # noqa: BLE001
        log.exception("[publish-task] retest probe failed task=%s doc=%s", task.id, doc.id)

    db.flush()
    return task
