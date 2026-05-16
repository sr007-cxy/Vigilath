"""AI 遥测话题 API — CRUD + 立即试跑.

定时跑批由 telemetry-service 自己 cron 触发,本文件只管:
1. 用户的话题 CRUD
2. /run-now 同步转发到 telemetry-service 拿一次结果(modal 里"立即试跑")
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from datetime import datetime, timedelta, timezone

from geo.api.auth import get_current_user
from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryResponseORM, AiTelemetryRunORM, AiTelemetryTopicORM,
    AiTelemetryQueryHitORM, AiTelemetryCellInsightORM, AiTelemetryTopicBriefingORM,
    BrandProfile, BriefingAction, BriefingFeedbackPayload, BriefingOut,
    CellDrawerOut, CellEvidence, CellInsightOut, CellInsightRec, CompetitorMention,
    CompetitorShareEntry, ClusterBreakdownItem, DomainCount, EngineFirstHit,
    FeedbackPayload, IntentBreakdownOut, KpiBlock, MAX_SELECTED_QUERIES,
    OverviewOut, OwnedSplit, PROFILE_REQUIRED_FIELDS, PositionDist,
    ProfileExtractOut, ProfileExtractPayload, QueryHitCell,
    ResponseOut, RunNowCitation,
    RunNowResult, RunOut, SeedPromptSubmitPayload,
    SelectedQueriesPayload, ShareOfVoiceOut,
    TopicOut, TopicPayload, TrackingMatrixOut,
    TrendPoint, VALID_ENGINES,
)
from geo.services.profile_extractor import extract_profile_from_text
from geo.models.user import User
from sqlalchemy import func

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-telemetry")

TELEMETRY_SERVICE_URL = os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _validate_payload(payload: TopicPayload) -> None:
    bad = [e for e in payload.engines if e not in VALID_ENGINES]
    if bad:
        raise HTTPException(400, f"unknown engines: {bad}")
    # de-dup + strip — 同步把 query_cluster_ids 按位置剔除/对齐
    cluster_ids_in = payload.query_cluster_ids if payload.query_cluster_ids else []
    cleaned_queries: list[str] = []
    cleaned_clusters: list[int] = []
    seen: set[str] = set()
    for i, q in enumerate(payload.queries):
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            cleaned_queries.append(q)
            if i < len(cluster_ids_in):
                cleaned_clusters.append(int(cluster_ids_in[i]))
    if not cleaned_queries:
        raise HTTPException(400, "queries cannot be empty")
    payload.queries = cleaned_queries
    payload.query_cluster_ids = (
        cleaned_clusters if len(cleaned_clusters) == len(cleaned_queries) else None
    )
    payload.engines = list(dict.fromkeys(payload.engines))
    # target / aliases cleanup
    payload.target = (payload.target or "").strip()
    aliases_clean: list[str] = []
    seen_a: set[str] = set()
    for a in (payload.target_aliases or []):
        a = a.strip()
        if a and a.lower() not in seen_a:
            seen_a.add(a.lower())
            aliases_clean.append(a)
    payload.target_aliases = aliases_clean


def _queries_with_meta(payload_queries: list[str], existing_raw: str | None,
                       cluster_ids: list[int] | None = None) -> str:
    """把 query 列表升级为 [{text, created_at, cluster_id?, status, ...}] 形态.

    Phase C 起每条 query 都带 `status`(pending/approved/rejected):
    - 已存在的 query:沿用原 status / created_at / cluster_id / submitted_at / approved_at
    - 新加的 query:status="pending", submitted_at=now
    - 校验:不允许删除 approved 的 query(由 caller `_validate_query_diff` 提前拦截)
    """
    now_iso = datetime.utcnow().isoformat()
    existing_by_text: dict[str, dict] = {}
    try:
        for q in json.loads(existing_raw or "[]"):
            if isinstance(q, dict) and q.get("text"):
                existing_by_text[q["text"]] = q
            elif isinstance(q, str):
                # 老版纯字符串 — 视为 topic 创建时即存在,补 approved 状态
                existing_by_text[q] = {
                    "text": q, "created_at": now_iso,
                    "status": "approved", "approved_at": now_iso,
                }
    except Exception:  # noqa: BLE001
        pass
    cluster_ok = isinstance(cluster_ids, list) and len(cluster_ids) == len(payload_queries)
    out = []
    for i, q in enumerate(payload_queries):
        prev = existing_by_text.get(q)
        if prev is not None:
            item = dict(prev)
            item.setdefault("created_at", now_iso)
            item.setdefault("status", "approved")
        else:
            # 新 query — 待审核
            item = {
                "text": q,
                "created_at": now_iso,
                "status": "pending",
                "submitted_at": now_iso,
            }
        cid: int | None = None
        if cluster_ok and isinstance(cluster_ids[i], int) and int(cluster_ids[i]) >= 0:
            cid = int(cluster_ids[i])
        elif isinstance(item.get("cluster_id"), int) and item["cluster_id"] >= 0:
            cid = int(item["cluster_id"])
        if cid is not None:
            item["cluster_id"] = cid
        out.append(item)
    return json.dumps(out, ensure_ascii=False)


def _append_seed_prompts(existing_raw: str | None, texts: list[str] | None) -> str:
    """把 payload.seed_drafts(若非空)逐条追加到 seed_prompts_json,状态=pending.

    幂等:文本已在列表里(不论状态)则跳过.
    texts 为 None / 空列表 / 全空字符串 则原样返回 existing.
    """
    raw = existing_raw or "[]"
    if not texts:
        return raw
    try:
        items = json.loads(raw)
    except Exception:  # noqa: BLE001
        items = []
    if not isinstance(items, list):
        items = []
    existing_texts = {s["text"] for s in items if isinstance(s, dict) and s.get("text")}
    now_iso = datetime.utcnow().isoformat()
    changed = False
    for raw_text in texts:
        cleaned = (raw_text or "").strip()
        if not cleaned or cleaned in existing_texts:
            continue
        items.append({
            "text": cleaned,
            "status": "pending",
            "submitted_at": now_iso,
        })
        existing_texts.add(cleaned)
        changed = True
    return json.dumps(items, ensure_ascii=False) if changed else raw


def _validate_query_diff(payload_queries: list[str], existing_raw: str | None) -> None:
    """Phase C 只增不改:已 approved 的 query 不允许从 payload 中消失.

    text 是 query 的身份标识;如果用户把 approved query 改文案,等价于"删一个、加一个",
    这里阻止该操作 — 抛 422 with code=LOCKED_FIELD.
    """
    approved_texts: set[str] = set()
    try:
        for q in json.loads(existing_raw or "[]"):
            if isinstance(q, dict) and q.get("status") == "approved" and q.get("text"):
                approved_texts.add(q["text"])
    except Exception:  # noqa: BLE001
        return  # 解析失败就跳过,不阻塞 update
    payload_set = set(payload_queries)
    missing = approved_texts - payload_set
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LOCKED_FIELD",
                "field": "queries",
                "message": "approved queries 不可删除或修改 text",
                "locked_items": sorted(missing),
            },
        )


def _get_topic_or_404(db: Session, topic_id: int, user_id: int) -> AiTelemetryTopicORM:
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t or t.user_id != user_id:
        raise HTTPException(404, "topic not found")
    return t


# ─────────────────────────── CRUD ──────────────────────────────


@router.get("/topics", response_model=list[TopicOut])
def list_topics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AiTelemetryTopicORM)
          .filter_by(user_id=current_user.id)
          .order_by(AiTelemetryTopicORM.created_at.desc())
          .all()
    )
    return [TopicOut.from_orm_row(r) for r in rows]


@router.post("/topics", response_model=TopicOut, status_code=201)
def create_topic(
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _validate_payload(payload)
    clusters_dump = [c.model_dump() for c in payload.clusters] if payload.clusters else []
    seed_prompts_init = _append_seed_prompts("[]", payload.seed_drafts)
    profile_init = "{}"
    if payload.profile is not None:
        profile_init = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
    t = AiTelemetryTopicORM(
        user_id=current_user.id,
        name=payload.name.strip(),
        target=payload.target,
        target_aliases_json=json.dumps(payload.target_aliases, ensure_ascii=False),
        industry=payload.industry,
        seed_prompts_json=seed_prompts_init,
        queries_json=_queries_with_meta(payload.queries, None, payload.query_cluster_ids),
        clusters_json=json.dumps(clusters_dump, ensure_ascii=False),
        engines_json=json.dumps(payload.engines, ensure_ascii=False),
        enabled=payload.enabled,
        profile_json=profile_init,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    # 把画像创建动作追加到 changelog(创建时 changelog 还是空,直接补一条)
    if payload.profile is not None:
        _append_changelog(
            t, actor_id=current_user.id, actor_role="user", field="profile",
            after=payload.profile.profile_name or payload.profile.company_short_name,
            note="created with profile",
        )
        db.commit()
        db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.put("/topics/{topic_id}", response_model=TopicOut)
def update_topic(
    topic_id: int,
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _validate_payload(payload)
    t = _get_topic_or_404(db, topic_id, current_user.id)
    # Phase C — 只增不改:approved query 不允许从 payload 消失
    _validate_query_diff(payload.queries, t.queries_json)
    t.name = payload.name.strip()
    t.target = payload.target
    t.target_aliases_json = json.dumps(payload.target_aliases, ensure_ascii=False)
    t.industry = payload.industry
    # Phase D — 编辑时如果带了 profile,就写库 + 记 changelog
    if payload.profile is not None:
        t.profile_json = json.dumps(payload.profile.model_dump(), ensure_ascii=False)
        _append_changelog(
            t, actor_id=current_user.id, actor_role="user", field="profile",
            after=payload.profile.profile_name or payload.profile.company_short_name,
        )
    # Phase C — payload 带 seed_prompt 时追加为 pending(若 text 已存在则跳过)
    t.seed_prompts_json = _append_seed_prompts(t.seed_prompts_json, payload.seed_drafts)
    t.queries_json = _queries_with_meta(payload.queries, t.queries_json, payload.query_cluster_ids)
    # clusters 只在 payload 显式传时才覆盖,避免 PUT 不带 clusters 时把旧簇清掉
    if payload.clusters is not None:
        t.clusters_json = json.dumps(
            [c.model_dump() for c in payload.clusters], ensure_ascii=False,
        )
    t.engines_json = json.dumps(payload.engines, ensure_ascii=False)
    t.enabled = payload.enabled
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _get_topic_or_404(db, topic_id, current_user.id)
    db.delete(t)
    db.commit()


# ─────────── Phase C — 种子提示词审核固化 ────────────


@router.post("/topics/{topic_id}/seed-prompts", response_model=TopicOut)
def submit_seed_prompt(
    topic_id: int,
    payload: SeedPromptSubmitPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """甲方追加新种子提示词 — 默认 status=pending,等 admin 审核."""
    t = _get_topic_or_404(db, topic_id, current_user.id)
    try:
        seeds = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds = []
    if not isinstance(seeds, list):
        seeds = []
    text = payload.text.strip()
    # 同一 topic 下种子词文本不允许重复
    if any(isinstance(s, dict) and s.get("text") == text for s in seeds):
        raise HTTPException(409, {"code": "DUPLICATE", "message": "种子提示词已存在"})
    seeds.append({
        "text": text,
        "status": "pending",
        "submitted_at": datetime.utcnow().isoformat(),
    })
    t.seed_prompts_json = json.dumps(seeds, ensure_ascii=False)
    _append_changelog(t, actor_id=current_user.id, actor_role="user",
                      field="seed_prompts", after=text, note="user submitted new seed")
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


# ─────────── Phase D — 画像 / 提交审核 / 监测问题勾选 ────────────


def _append_changelog(t: AiTelemetryTopicORM, *, actor_id: int | None, actor_role: str,
                       field: str, before: str | None = None, after: str | None = None,
                       note: str | None = None) -> None:
    """topic_changelog_json 末尾追加一条记录(只增不减)."""
    try:
        log_arr = json.loads(t.topic_changelog_json or "[]")
    except Exception:  # noqa: BLE001
        log_arr = []
    if not isinstance(log_arr, list):
        log_arr = []
    entry: dict = {
        "at": datetime.utcnow().isoformat(),
        "actor_id": actor_id,
        "actor_role": actor_role,
        "field": field,
    }
    if before is not None:
        entry["before"] = before[:500]
    if after is not None:
        entry["after"] = after[:500]
    if note is not None:
        entry["note"] = note
    log_arr.append(entry)
    t.topic_changelog_json = json.dumps(log_arr, ensure_ascii=False)


def _append_expansion_log(t: AiTelemetryTopicORM, *, seed: str, model: str,
                           expanded_count: int, raw_excerpt: str) -> None:
    """expansion_log_json 末尾追加一条"种子词扩展"的调用记录."""
    try:
        log_arr = json.loads(t.expansion_log_json or "[]")
    except Exception:  # noqa: BLE001
        log_arr = []
    if not isinstance(log_arr, list):
        log_arr = []
    log_arr.append({
        "at": datetime.utcnow().isoformat(),
        "seed": seed[:200],
        "model": model,
        "expanded_count": int(expanded_count),
        "raw_excerpt": (raw_excerpt or "")[:300],
    })
    t.expansion_log_json = json.dumps(log_arr, ensure_ascii=False)


def _ensure_editable(t: AiTelemetryTopicORM) -> None:
    """资料编辑权限:submission_status ∈ {draft, rejected} 时用户可改;pending/approved 不让动."""
    s = (t.submission_status or "draft")
    if s not in ("draft", "rejected"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LOCKED_STATUS",
                "field": "submission_status",
                "message": f"submission_status={s},不允许编辑;请等待 admin 审核",
            },
        )


def _validate_selected_cap(queries_json_str: str | None) -> None:
    """selected=True 的 query 总数不能超过 MAX_SELECTED_QUERIES."""
    try:
        arr = json.loads(queries_json_str or "[]")
    except Exception:  # noqa: BLE001
        return
    if not isinstance(arr, list):
        return
    n = sum(1 for q in arr if isinstance(q, dict) and bool(q.get("selected", True)))
    if n > MAX_SELECTED_QUERIES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TOO_MANY_SELECTED",
                "field": "queries",
                "message": f"最多勾选 {MAX_SELECTED_QUERIES} 个监测问题,当前 {n}",
            },
        )


@router.put("/topics/{topic_id}/profile", response_model=TopicOut)
def update_topic_profile(
    topic_id: int,
    payload: BrandProfile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新 topic 的品牌画像(7 大模块).只允许在 draft/rejected 状态下改."""
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    old_profile_summary = ""
    try:
        old = json.loads(t.profile_json or "{}")
        old_profile_summary = old.get("profile_name") or old.get("company_short_name") or ""
    except Exception:  # noqa: BLE001
        pass
    t.profile_json = json.dumps(payload.model_dump(), ensure_ascii=False)
    # 把 industry / target 也回填到 topic 顶层字段,跟 profile 保持一致
    if payload.industry:
        t.industry = payload.industry
    if payload.company_short_name and not t.target:
        t.target = payload.company_short_name
    if payload.profile_name and (t.name == "" or t.name == "(unnamed)"):
        t.name = payload.profile_name
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="profile",
        before=old_profile_summary or None,
        after=payload.profile_name or payload.company_short_name or None,
    )
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.post("/topics/{topic_id}/selected-queries", response_model=TopicOut)
def update_selected_queries(
    topic_id: int,
    payload: SelectedQueriesPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """根据用户勾选,更新 queries_json 里每条的 `selected` 字段.

    入参 items 用 text 匹配现有 queries — 不在 payload 里的 text 默认 selected=False.
    新文本(payload 里有但 queries_json 没的)直接加进 queries_json,status=pending + selected=值.
    """
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    try:
        arr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        arr = []
    if not isinstance(arr, list):
        arr = []
    # 升级 legacy str → dict
    upgraded: list[dict] = []
    for q in arr:
        if isinstance(q, str):
            upgraded.append({"text": q, "status": "approved", "selected": True})
        elif isinstance(q, dict) and q.get("text"):
            upgraded.append(dict(q))
    by_text = {q["text"]: q for q in upgraded}
    now_iso = datetime.utcnow().isoformat()
    desired_texts: set[str] = set()
    for item in payload.items:
        text = item.text.strip()
        if not text:
            continue
        desired_texts.add(text)
        if text in by_text:
            by_text[text]["selected"] = bool(item.selected)
        else:
            upgraded.append({
                "text": text,
                "status": "pending",
                "submitted_at": now_iso,
                "selected": bool(item.selected),
            })
            by_text[text] = upgraded[-1]
    # 不在 payload 里的:保留原状态,selected=False
    for text, q in by_text.items():
        if text not in desired_texts:
            q["selected"] = False
    selected_n = sum(1 for q in upgraded if q.get("selected"))
    if selected_n > MAX_SELECTED_QUERIES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TOO_MANY_SELECTED",
                "field": "queries",
                "message": f"最多勾选 {MAX_SELECTED_QUERIES} 个监测问题,当前 {selected_n}",
            },
        )
    t.queries_json = json.dumps(upgraded, ensure_ascii=False)
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="selected_queries",
        after=f"selected_count={selected_n}",
    )
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


@router.post("/topics/{topic_id}/submit-for-review", response_model=TopicOut)
def submit_topic_for_review(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把整张申请置为 pending,等 admin 审核.

    校验:
      - submission_status ∈ {draft, rejected}
      - 画像 PROFILE_REQUIRED_FIELDS 都非空
      - 至少 1 条种子(pending 或 approved)
      - selected query 数 ∈ [1, 50]
    """
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)

    # 1) 画像必填校验
    try:
        profile_raw = json.loads(t.profile_json or "{}")
    except Exception:  # noqa: BLE001
        profile_raw = {}
    if not isinstance(profile_raw, dict):
        profile_raw = {}
    missing: list[str] = []
    for f in PROFILE_REQUIRED_FIELDS:
        v = profile_raw.get(f)
        if v is None or v == "" or v == [] or v == {}:
            missing.append(f)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROFILE_INCOMPLETE",
                "field": "profile",
                "message": "画像必填项缺失",
                "missing": missing,
            },
        )

    # 2) 种子词:至少 1 条 pending/approved
    try:
        seeds = json.loads(t.seed_prompts_json or "[]")
    except Exception:  # noqa: BLE001
        seeds = []
    seed_ok = sum(
        1 for s in seeds
        if isinstance(s, dict) and s.get("text")
        and (s.get("status") in (None, "pending", "approved"))
    )
    if seed_ok < 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_SEED", "field": "seed_prompts",
                    "message": "请至少填写 1 个种子提示词"},
        )

    # 3) selected query ∈ [1, 50]
    try:
        qarr = json.loads(t.queries_json or "[]")
    except Exception:  # noqa: BLE001
        qarr = []
    selected_n = sum(
        1 for q in qarr
        if isinstance(q, dict) and q.get("text") and q.get("selected", True)
    )
    if selected_n < 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_SELECTED", "field": "queries",
                    "message": "请至少勾选 1 个监测问题"},
        )
    if selected_n > MAX_SELECTED_QUERIES:
        raise HTTPException(
            status_code=422,
            detail={"code": "TOO_MANY_SELECTED", "field": "queries",
                    "message": f"最多勾选 {MAX_SELECTED_QUERIES} 个监测问题,当前 {selected_n}"},
        )

    t.submission_status = "pending"
    t.submitted_at = datetime.utcnow()
    t.rejected_at = None
    _append_changelog(
        t, actor_id=current_user.id, actor_role="user", field="submission_status",
        before=t.submission_status, after="pending",
        note=f"selected_queries={selected_n}",
    )
    db.commit()
    db.refresh(t)
    return TopicOut.from_orm_row(t)


# ─────────────────────────── Run-now passthrough ──────────────


@router.post("/topics/run-now", response_model=list[RunNowResult])
async def run_now(
    payload: TopicPayload,
    current_user: User = Depends(get_current_user),
):
    """同步调 telemetry-service,把单话题跑一遍,结果直接返回前端 modal.

    不入库 — 这是"试跑预览"语义。每日定时跑批由 telemetry-service 自跑自存。
    """
    _validate_payload(payload)
    url = f"{TELEMETRY_SERVICE_URL}/run-now"
    body = {
        "name": payload.name,
        "queries": payload.queries,
        "engines": payload.engines,
        "user_id": current_user.id,
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(url, json=body)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("telemetry-service run-now failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")


# ─────────────── AI 智能填充 — 原始资料 → 品牌画像 ────────────


@router.post("/profile/extract", response_model=ProfileExtractOut)
def extract_profile_endpoint(
    payload: ProfileExtractPayload,
    current_user: User = Depends(get_current_user),
):
    """前端 step1「设置」里把用户拖入的文件文本 / 粘贴的原始资料丢给 LLM,
    抽取出一份 BrandProfile 回填。空字段由调用方决定是否合并(默认只填空缺)。

    503 = 后端没配 DEEPSEEK_API_KEY 或 OPENROUTER_API_KEY。
    """
    try:
        profile, model_id = extract_profile_from_text(payload.text)
    except Exception as e:  # noqa: BLE001
        log.warning("profile extract failed for user %s: %s", current_user.id, e)
        raise HTTPException(502, f"AI 解析失败: {e}")
    if not model_id:
        raise HTTPException(503, "未配置 DEEPSEEK_API_KEY / OPENROUTER_API_KEY,AI 智能填充不可用")
    return ProfileExtractOut(profile=profile, used_model=model_id)


# ─────────────── 候选 query 生成(DeepSeek)— 建话题时用 ───────────


@router.post("/suggest-queries")
async def suggest_queries(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    """种子主题 → DeepSeek 候选 query,前端 picker 多选回填。

    body: {seed, count?=20, target?, aliases?=[], industry?}
    resp: {seed, queries: [str, ...]}

    target 非空时 telemetry-service 走 GEO-aware prompt(候选不含 target / aliases 字眼)。
    """
    seed = (payload.get("seed") or "").strip()
    if not seed:
        raise HTTPException(400, "seed cannot be empty")
    try:
        count = int(payload.get("count", 200))
    except (TypeError, ValueError):
        count = 200
    count = max(5, min(count, 300))

    target = (payload.get("target") or "").strip()
    industry = (payload.get("industry") or "").strip()
    aliases_in = payload.get("aliases") or []
    if not isinstance(aliases_in, list):
        aliases_in = []
    aliases = [str(a).strip() for a in aliases_in if str(a).strip()][:20]

    body = {
        "seed": seed, "count": count,
        "target": target, "aliases": aliases, "industry": industry,
    }
    url = f"{TELEMETRY_SERVICE_URL}/suggest-queries"
    try:
        # 200 条候选 DeepSeek 端 30-90s,留 200s 余量
        async with httpx.AsyncClient(timeout=200.0) as client:
            r = await client.post(url, json=body)
    except httpx.HTTPError as e:
        log.warning("telemetry-service suggest-queries failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")

    if r.status_code != 200:
        # 透传 telemetry-service 的错误结构(detail.code / detail.message)
        try:
            raise HTTPException(r.status_code, r.json().get("detail") or r.text)
        except ValueError:
            raise HTTPException(r.status_code, r.text)
    return r.json()


@router.post("/topics/{topic_id}/expand-queries")
async def expand_queries_for_topic(
    topic_id: int, payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Phase D — 针对单个 topic 跑一次种子扩展,把调用记录写进 expansion_log_json.

    body: {seed, count?=50} — count 默认 50(对应监测问题上限).
    resp: {seed, queries: [str, ...]} — 与 /suggest-queries 同 schema.

    扩展候选不直接落 queries_json — 前端拿到后让用户勾选,再调 /selected-queries.
    """
    t = _get_topic_or_404(db, topic_id, current_user.id)
    _ensure_editable(t)
    seed = (payload.get("seed") or "").strip()
    if not seed:
        raise HTTPException(400, "seed cannot be empty")
    try:
        count = int(payload.get("count", 50))
    except (TypeError, ValueError):
        count = 50
    count = max(5, min(count, MAX_SELECTED_QUERIES))

    target = t.target or ""
    industry = t.industry or ""
    try:
        aliases = json.loads(t.target_aliases_json or "[]")
    except Exception:  # noqa: BLE001
        aliases = []
    body = {"seed": seed, "count": count, "target": target,
            "aliases": aliases, "industry": industry}
    url = f"{TELEMETRY_SERVICE_URL}/suggest-queries"
    try:
        async with httpx.AsyncClient(timeout=200.0) as client:
            r = await client.post(url, json=body)
    except httpx.HTTPError as e:
        log.warning("telemetry-service suggest-queries failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    if r.status_code != 200:
        try:
            raise HTTPException(r.status_code, r.json().get("detail") or r.text)
        except ValueError:
            raise HTTPException(r.status_code, r.text)

    data = r.json()
    queries_out = data.get("queries") or []
    model_name = data.get("model") or "deepseek"
    raw_excerpt = ", ".join(queries_out[:5])
    _append_expansion_log(
        t, seed=seed, model=model_name,
        expanded_count=len(queries_out), raw_excerpt=raw_excerpt,
    )
    db.commit()
    return data


# ─────────────────────────── Trigger run + result queries ─────


@router.post("/topics/{topic_id}/run", status_code=202)
async def trigger_run(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """触发一次正式跑批入库,立刻返回(202).结果通过 GET /topics/{id}/runs 轮询."""
    _get_topic_or_404(db, topic_id, current_user.id)
    url = f"{TELEMETRY_SERVICE_URL}/run-topic"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={"topic_id": topic_id})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("telemetry-service /run-topic failed: %s", e)
        raise HTTPException(502, f"telemetry-service unavailable: {e}")


@router.get("/topics/{topic_id}/runs", response_model=list[RunOut])
def list_runs(
    topic_id: int,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(db, topic_id, current_user.id)
    rows = (
        db.query(AiTelemetryRunORM)
          .filter_by(topic_id=topic_id)
          .order_by(AiTelemetryRunORM.id.desc())
          .limit(min(limit, 100))
          .all()
    )
    # response counts in one query
    counts = dict(
        db.query(AiTelemetryResponseORM.run_id, func.count(AiTelemetryResponseORM.id))
          .filter(AiTelemetryResponseORM.run_id.in_([r.id for r in rows]))
          .group_by(AiTelemetryResponseORM.run_id)
          .all()
    ) if rows else {}
    return [RunOut.from_orm_row(r, response_count=counts.get(r.id, 0)) for r in rows]


@router.get("/topics/{topic_id}/overview", response_model=OverviewOut)
def topic_overview(
    topic_id: int,
    period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """概览页聚合 — 4 KPI 卡 + 每日引擎趋势 series.

    KPI 口径:
      - visibility = (Response.hit=True 的成功 response 数 / 总成功 response 数) × 100
      - citations = sum(len(citations_json)) 周期内所有 response
      - growth = citations 相对上一周期的 pct change
      - engines_covered = 本期有 ≥1 成功 response 的引擎数 / topic.engines 总数
    品牌词来源:topic.target + topic.target_aliases_json(与 runner 落 hit 时同源).
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    period_days = max(1, min(period, 90))
    now = datetime.utcnow()
    period_start = now - timedelta(days=period_days)
    prev_start = period_start - timedelta(days=period_days)

    # 品牌词:用 topic 自己的检测词,与 runner detect_hit 同源
    brand_keywords: list[str] = []
    if topic.target:
        brand_keywords.append(topic.target)
    brand_keywords.extend(json.loads(topic.target_aliases_json or "[]"))
    brand_keywords = [k for k in brand_keywords if k]
    brand_lc = [k.lower() for k in brand_keywords]

    topic_engines = json.loads(topic.engines_json or "[]")
    engines_total = len(topic_engines)

    rows_curr = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= period_start)
          .all()
    )
    rows_prev = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= prev_start)
          .filter(AiTelemetryResponseORM.created_at < period_start)
          .all()
    )

    def _aggregate(rows: list[AiTelemetryResponseORM]) -> dict:
        cit_total = 0
        mention_hit = 0
        succ_engines: set[str] = set()
        total = 0
        for r in rows:
            cits = json.loads(r.citations_json or "[]")
            cit_total += len(cits)
            if not r.error:
                total += 1
                succ_engines.add(r.engine)
                if r.hit:
                    mention_hit += 1
        visibility = (mention_hit / total * 100) if total > 0 else 0.0
        return {
            "citations": cit_total,
            "visibility": round(visibility, 1),
            "engines": len(succ_engines),
            "succ_total": total,
        }

    curr = _aggregate(rows_curr)
    prev = _aggregate(rows_prev)

    def _delta(curr_val: float, prev_val: float) -> Optional[float]:
        if prev_val == 0:
            return None
        return round((curr_val - prev_val) / prev_val * 100, 1)

    # 每日 trend: date -> {engine: citation_count} + 每日 hit/total 计 visibility 曲线
    bucket: dict[str, dict[str, int]] = {}
    day_succ: dict[str, int] = {}
    day_hit: dict[str, int] = {}
    engines_in_window: set[str] = set()
    for r in rows_curr:
        d = r.created_at.strftime("%Y-%m-%d")
        if d not in bucket:
            bucket[d] = {}
        cits = json.loads(r.citations_json or "[]")
        bucket[d][r.engine] = bucket[d].get(r.engine, 0) + len(cits)
        engines_in_window.add(r.engine)
        if not r.error:
            day_succ[d] = day_succ.get(d, 0) + 1
            if r.hit:
                day_hit[d] = day_hit.get(d, 0) + 1

    # 填补缺失日期为空,保证 sparkline 连续
    trend: list[TrendPoint] = []
    sparkline_cit: list[float] = []
    sparkline_vis: list[float] = []
    for i in range(period_days):
        d = (period_start + timedelta(days=i)).strftime("%Y-%m-%d")
        day_vals = bucket.get(d, {})
        trend.append(TrendPoint(date=d, values=day_vals))
        sparkline_cit.append(float(sum(day_vals.values())))
        succ = day_succ.get(d, 0)
        sparkline_vis.append(round(day_hit.get(d, 0) / succ * 100, 1) if succ > 0 else 0.0)

    # ── 引用分析:Top domains + owned/other + engine×domain 矩阵 ──
    def _domain_stats(rows: list[AiTelemetryResponseORM]) -> tuple[dict[str, int], int, int, dict[str, dict[str, int]]]:
        """返回 (domain_total_count, owned_count, total_cit_count, engine_domain[engine][domain] = count)."""
        domain_count: dict[str, int] = {}
        owned = 0
        total = 0
        engine_domain: dict[str, dict[str, int]] = {}
        for r in rows:
            if r.error:
                continue
            cits = json.loads(r.citations_json or "[]")
            for c in cits:
                d = (c.get("domain") or "").lower().strip()
                if not d:
                    continue
                domain_count[d] = domain_count.get(d, 0) + 1
                total += 1
                eng = engine_domain.setdefault(r.engine, {})
                eng[d] = eng.get(d, 0) + 1
                if brand_lc and any(k in d for k in brand_lc):
                    owned += 1
        return domain_count, owned, total, engine_domain

    curr_domains, curr_owned, curr_total_cit, curr_engine_domain = _domain_stats(rows_curr)
    _, prev_owned, prev_total_cit, _ = _domain_stats(rows_prev)

    top_n = sorted(curr_domains.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    top_domains_out = [
        DomainCount(
            domain=d, count=c,
            pct=round(c / curr_total_cit * 100, 1) if curr_total_cit > 0 else 0.0,
        )
        for d, c in top_n
    ]
    top_set = {d for d, _ in top_n}

    # 只保留 top domains 的 engine 矩阵,前端 heatmap 用
    engine_domain_filtered: dict[str, dict[str, int]] = {}
    for eng, dom_map in curr_engine_domain.items():
        engine_domain_filtered[eng] = {d: c for d, c in dom_map.items() if d in top_set}

    curr_owned_pct = round(curr_owned / curr_total_cit * 100, 1) if curr_total_cit > 0 else 0.0
    prev_owned_pct = (prev_owned / prev_total_cit * 100) if prev_total_cit > 0 else 0.0
    owned_split = OwnedSplit(
        owned=curr_owned,
        other=curr_total_cit - curr_owned,
        owned_pct=curr_owned_pct,
        delta_pct=_delta(curr_owned_pct, prev_owned_pct),
    )

    return OverviewOut(
        topic_id=topic_id,
        period_days=period_days,
        brand_keywords=brand_keywords,
        visibility=KpiBlock(
            value=curr["visibility"],
            delta_pct=_delta(curr["visibility"], prev["visibility"]),
            sparkline=sparkline_vis,
        ),
        citations=KpiBlock(
            value=float(curr["citations"]),
            delta_pct=_delta(curr["citations"], prev["citations"]),
            sparkline=sparkline_cit,
        ),
        growth=KpiBlock(
            value=_delta(curr["citations"], prev["citations"]) or 0.0,
            delta_pct=None,
            sparkline=[],
        ),
        engines_covered=KpiBlock(
            value=float(curr["engines"]),
            delta_pct=_delta(curr["engines"], prev["engines"]),
            sparkline=[],
        ),
        engines_total=engines_total,
        trend=trend,
        engines=sorted(engines_in_window),
        top_domains=top_domains_out,
        owned_split=owned_split,
        engine_domain_matrix=engine_domain_filtered,
    )


@router.get("/topics/{topic_id}/intent-breakdown", response_model=IntentBreakdownOut)
def topic_intent_breakdown(
    topic_id: int,
    period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按 picker 端聚出的 cluster_id 把本期 response 分组,出每簇 mention / citation 率。

    话题 queries_json 里没有 cluster_id(老话题或聚类失败时全 0)就只有一个簇,
    或者 query 文本与 cluster_id 对不上时落到 uncategorized 桶。
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    period_days = max(1, min(period, 90))
    period_start = datetime.utcnow() - timedelta(days=period_days)

    # 品牌词(沿用 overview 口径:topic.target + aliases,与 runner detect_hit 同源)
    brand_keywords: list[str] = []
    if topic.target:
        brand_keywords.append(topic.target)
    brand_keywords.extend(json.loads(topic.target_aliases_json or "[]"))
    brand_keywords = [k for k in brand_keywords if k]

    # query → cluster_id 映射
    queries_raw = json.loads(topic.queries_json or "[]")
    query_to_cluster: dict[str, int] = {}
    query_count_by_cluster: dict[int, int] = {}
    for q in queries_raw:
        if isinstance(q, dict):
            text = q.get("text") or ""
            cid = q.get("cluster_id")
            cid_int = int(cid) if isinstance(cid, int) else -1
        elif isinstance(q, str):
            text, cid_int = q, -1
        else:
            continue
        if not text:
            continue
        query_to_cluster[text] = cid_int
        query_count_by_cluster[cid_int] = query_count_by_cluster.get(cid_int, 0) + 1

    # 簇标签
    clusters_raw = json.loads(topic.clusters_json or "[]")
    cluster_labels: dict[int, str] = {}
    cluster_order: list[int] = []
    for c in clusters_raw:
        if isinstance(c, dict) and "cluster_id" in c:
            cid = int(c["cluster_id"])
            cluster_labels[cid] = c.get("label") or f"cluster_{cid}"
            cluster_order.append(cid)

    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= period_start)
          .all()
    )

    # 按 cluster_id 聚合 response 维度
    agg: dict[int, dict] = {}
    for r in rows:
        if r.error:
            continue
        cid = query_to_cluster.get(r.query, -1)
        bucket = agg.setdefault(cid, {"response": 0, "mention": 0, "citation": 0})
        bucket["response"] += 1
        if r.hit:
            bucket["mention"] += 1
        bucket["citation"] += len(json.loads(r.citations_json or "[]"))

    def _mk(cid: int, label: str) -> ClusterBreakdownItem:
        b = agg.get(cid, {"response": 0, "mention": 0, "citation": 0})
        return ClusterBreakdownItem(
            cluster_id=cid,
            label=label,
            query_count=query_count_by_cluster.get(cid, 0),
            response_count=b["response"],
            mention_count=b["mention"],
            mention_rate=round(b["mention"] / b["response"], 3) if b["response"] else 0.0,
            citation_count=b["citation"],
        )

    # 按 size 降序排出有名簇,无标签 / 老话题落到 uncategorized
    out_clusters: list[ClusterBreakdownItem] = []
    seen_cids: set[int] = set()
    for cid in cluster_order:
        out_clusters.append(_mk(cid, cluster_labels[cid]))
        seen_cids.add(cid)
    out_clusters.sort(key=lambda c: c.query_count, reverse=True)

    uncat_b = agg.get(-1, {"response": 0, "mention": 0, "citation": 0})
    # 把所有 query 没对上簇的 response 累到 uncategorized
    for cid, b in agg.items():
        if cid != -1 and cid not in seen_cids:
            uncat_b["response"] += b["response"]
            uncat_b["mention"] += b["mention"]
            uncat_b["citation"] += b["citation"]
    uncat = ClusterBreakdownItem(
        cluster_id=-1,
        label="uncategorized",
        query_count=query_count_by_cluster.get(-1, 0)
                  + sum(c for cid, c in query_count_by_cluster.items()
                        if cid not in seen_cids and cid != -1),
        response_count=uncat_b["response"],
        mention_count=uncat_b["mention"],
        mention_rate=round(uncat_b["mention"] / uncat_b["response"], 3)
                   if uncat_b["response"] else 0.0,
        citation_count=uncat_b["citation"],
    )

    return IntentBreakdownOut(
        topic_id=topic_id,
        period_days=period_days,
        brand_keywords=brand_keywords,
        clusters=out_clusters,
        uncategorized=uncat,
    )


@router.get("/runs/{run_id}/responses", response_model=list[ResponseOut])
def list_responses(
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    run = db.get(AiTelemetryRunORM, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    topic = db.get(AiTelemetryTopicORM, run.topic_id)
    if not topic or topic.user_id != current_user.id:
        raise HTTPException(404, "run not found")
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter_by(run_id=run_id)
          .order_by(AiTelemetryResponseORM.id.asc())
          .all()
    )
    out = []
    for r in rows:
        cites = json.loads(r.citations_json or "[]")
        out.append(ResponseOut(
            id=r.id, engine=r.engine, query=r.query,
            answer=r.answer, citations=cites,
            video_url=r.video_url, source_url=r.source_url, error=r.error,
            created_at=r.created_at,
            hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
        ))
    return out


# ─────────────────── v1 引用追踪 ──────────────────────────


def _cell_to_out(c: AiTelemetryQueryHitORM) -> QueryHitCell:
    return QueryHitCell(
        query=c.query, engine=c.engine, status=c.status,
        first_hit_at=c.first_hit_at,
        first_hit_response_id=c.first_hit_response_id,
        last_checked_at=c.last_checked_at,
        total_runs=c.total_runs or 0, total_hits=c.total_hits or 0,
    )


@router.get("/topics/{topic_id}/tracking-matrix", response_model=TrackingMatrixOut)
def get_tracking_matrix(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """v1 引用追踪 — 整个 topic 的 (query × engine) 矩阵 + 首次命中时间线."""
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    queries_raw = json.loads(topic.queries_json or "[]")
    queries: list[str] = []
    for q in queries_raw:
        if isinstance(q, dict):
            t = q.get("text") or ""
            if t:
                queries.append(t)
        elif isinstance(q, str):
            queries.append(q)
    engines: list[str] = json.loads(topic.engines_json or "[]")
    target_aliases: list[str] = json.loads(topic.target_aliases_json or "[]")

    cell_rows = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    by_key: dict[tuple[str, str], AiTelemetryQueryHitORM] = {
        (c.query, c.engine): c for c in cell_rows
    }

    # 填充所有 (query × engine) — 未跑过的 cell 给 pending 占位(不入库,只 in-memory 返回)
    cells: list[QueryHitCell] = []
    for q in queries:
        for e in engines:
            c = by_key.get((q, e))
            if c is not None:
                cells.append(_cell_to_out(c))
            else:
                cells.append(QueryHitCell(
                    query=q, engine=e, status="pending",
                    first_hit_at=None, first_hit_response_id=None,
                    last_checked_at=None, total_runs=0, total_hits=0,
                ))

    # 时间线 — 每个 engine 在所有 query 里最早 first_hit_at
    timeline: list[EngineFirstHit] = []
    for e in engines:
        best: AiTelemetryQueryHitORM | None = None
        for c in cell_rows:
            if c.engine != e:
                continue
            if c.first_hit_at is None:
                continue
            if best is None or (c.first_hit_at < best.first_hit_at):
                best = c
        if best is None:
            timeline.append(EngineFirstHit(
                engine=e, first_hit_at=None, first_hit_query=None, days_after_start=None,
            ))
        else:
            days = (best.first_hit_at - topic.created_at).days if topic.created_at else None
            timeline.append(EngineFirstHit(
                engine=e, first_hit_at=best.first_hit_at,
                first_hit_query=best.query,
                days_after_start=max(0, days) if days is not None else None,
            ))

    # KPI
    total_cells = len(queries) * len(engines)
    hit_cells = sum(1 for c in cells if c.total_hits >= 1)
    hit_pct = round(hit_cells / total_cells * 100, 1) if total_cells > 0 else 0.0

    total_runs = (
        db.query(func.count(AiTelemetryRunORM.id))
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .scalar() or 0
    )

    return TrackingMatrixOut(
        topic_id=topic_id,
        target=topic.target or topic.name or "",
        target_aliases=target_aliases,
        queries=queries,
        engines=engines,
        started_at=topic.created_at,
        cells=cells,
        timeline=timeline,
        total_runs=int(total_runs),
        total_cells=total_cells,
        hit_cells=hit_cells,
        hit_cells_pct=hit_pct,
    )


@router.get("/topics/{topic_id}/share-of-voice", response_model=ShareOfVoiceOut)
def get_share_of_voice(
    topic_id: int, period: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """v1.3 SAIV(Share of AI Voice)聚合 — 品牌 vs 竞品 提及次数对比.

    口径(近 period 天):
      brand_count = COUNT(Response WHERE hit=True)
      competitors[name].count = SUM(competitors_json[*].count) 按 name 聚合
      saiv_pct = brand / (brand + sum(competitors)) × 100
      position_dist = COUNT(Response GROUP BY mention_position) 仅 hit=True
      optimal_rate_pct = SUM(QueryHit.total_hits) / SUM(QueryHit.total_runs) × 100
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    period_days = max(1, min(period, 90))
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= cutoff)
          .all()
    )

    brand_count = 0
    competitors_map: dict[str, int] = {}
    pos = {"lead": 0, "body": 0, "tail": 0, "unknown": 0}
    for r in rows:
        if r.error:
            continue
        if r.hit:
            brand_count += 1
            p = (r.mention_position or "unknown").lower()
            if p not in pos:
                p = "unknown"
            pos[p] += 1
        if r.competitors_json:
            try:
                arr = json.loads(r.competitors_json or "[]")
            except Exception:  # noqa: BLE001
                arr = []
            for c in arr:
                if not isinstance(c, dict):
                    continue
                name = (c.get("name") or "").strip()
                cnt = int(c.get("count") or 0)
                if name and cnt > 0:
                    competitors_map[name] = competitors_map.get(name, 0) + cnt

    comp_total = sum(competitors_map.values())
    total = brand_count + comp_total
    saiv_pct = round(brand_count / total * 100, 1) if total > 0 else 0.0

    top_comps = sorted(competitors_map.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    comp_entries = [
        CompetitorShareEntry(
            name=name, count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for name, cnt in top_comps
    ]

    # 优选率 = sum(QueryHit.total_hits) / sum(QueryHit.total_runs)
    qh_rows = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    sum_hits = sum((c.total_hits or 0) for c in qh_rows)
    sum_runs = sum((c.total_runs or 0) for c in qh_rows)
    optimal_pct = round(sum_hits / sum_runs * 100, 1) if sum_runs > 0 else 0.0

    total_runs = (
        db.query(func.count(AiTelemetryRunORM.id))
          .filter(AiTelemetryRunORM.topic_id == topic_id)
          .scalar() or 0
    )

    return ShareOfVoiceOut(
        topic_id=topic_id,
        target=topic.target or topic.name or "",
        period_days=period_days,
        brand_count=brand_count,
        competitors_count_total=comp_total,
        saiv_pct=saiv_pct,
        competitors=comp_entries,
        position_dist=PositionDist(**pos),
        optimal_rate_pct=optimal_pct,
        total_runs=int(total_runs),
        sample_size=len(rows),
    )


@router.get("/topics/{topic_id}/cells/drawer", response_model=CellDrawerOut)
def get_cell_drawer(
    topic_id: int, query: str, engine: str,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """单个 cell drawer — 状态 + 历次答复(最近 N 条).

    诊断块 insight 不在这里取(那是 LLM 调用 / 按需触发),由前端单独 POST /cell-insight 拉.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    cell = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .filter(AiTelemetryQueryHitORM.query == query)
          .filter(AiTelemetryQueryHitORM.engine == engine)
          .one_or_none()
    )
    if cell is None:
        cell_out = QueryHitCell(
            query=query, engine=engine, status="pending",
            first_hit_at=None, first_hit_response_id=None,
            last_checked_at=None, total_runs=0, total_hits=0,
        )
    else:
        cell_out = _cell_to_out(cell)

    resp_rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.query == query)
          .filter(AiTelemetryResponseORM.engine == engine)
          .order_by(AiTelemetryResponseORM.created_at.desc())
          .limit(min(limit, 30))
          .all()
    )
    evidence = [
        CellEvidence(
            response_id=r.id, run_id=r.run_id, created_at=r.created_at,
            engine=r.engine, query=r.query,
            hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
            mention_position=r.mention_position,
            source_url=r.source_url, answer=r.answer or "",
            citations=[RunNowCitation(**c) for c in json.loads(r.citations_json or "[]")],
        )
        for r in resp_rows
    ]

    # 已有 insight (最新) — 不强制生成
    insight_row = (
        db.query(AiTelemetryCellInsightORM)
          .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
          .filter(AiTelemetryCellInsightORM.query == query)
          .filter(AiTelemetryCellInsightORM.engine == engine)
          .order_by(AiTelemetryCellInsightORM.generated_at.desc())
          .first()
    )
    insight_out = _insight_orm_to_out(insight_row) if insight_row else None

    return CellDrawerOut(cell=cell_out, evidence=evidence, insight=insight_out)


def _insight_orm_to_out(r: AiTelemetryCellInsightORM) -> CellInsightOut:
    comps_raw = json.loads(r.competitors_top3_json or "[]")
    recs_raw = json.loads(r.recommendations_json or "[]")
    return CellInsightOut(
        id=r.id, topic_id=r.topic_id, query=r.query, engine=r.engine,
        window_start=r.window_start, window_end=r.window_end,
        verdict=r.verdict, summary=r.summary or "",
        competitors_top3=[CompetitorMention(**c) for c in comps_raw if isinstance(c, dict)],
        recommendations=[CellInsightRec(**c) for c in recs_raw if isinstance(c, dict)],
        evidence_response_ids=json.loads(r.evidence_response_ids_json or "[]"),
        llm_model=r.llm_model or "", prompt_version=r.prompt_version or "",
        generated_at=r.generated_at, feedback=r.feedback,
    )


# ─────────────────── v1.1 LLM 诊断 ──────────────────────────


@router.post("/topics/{topic_id}/cells/insight", response_model=CellInsightOut)
async def fetch_cell_insight(
    topic_id: int, query: str, engine: str, force: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """drawer 点"分析"按钮触发 — 转发到 telemetry-service 同步生成(LLM 3-8s)."""
    _get_topic_or_404(db, topic_id, current_user.id)
    url = f"{TELEMETRY_SERVICE_URL}/cell-insight"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json={
                "topic_id": topic_id, "query": query, "engine": engine, "force": force,
            })
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    if not isinstance(data, dict) or data.get("status") == "not_found":
        raise HTTPException(404, "cell insight not generated")
    # 入库已在 telemetry-service 那侧完成,这里 raw dict → CellInsightOut
    return CellInsightOut(
        id=data["id"], topic_id=data["topic_id"], query=data["query"], engine=data["engine"],
        window_start=datetime.fromisoformat(data["window_start"]) if data.get("window_start") else datetime.utcnow(),
        window_end=datetime.fromisoformat(data["window_end"]) if data.get("window_end") else datetime.utcnow(),
        verdict=data["verdict"], summary=data.get("summary") or "",
        competitors_top3=[CompetitorMention(**c) for c in data.get("competitors_top3") or [] if isinstance(c, dict)],
        recommendations=[CellInsightRec(**c) for c in data.get("recommendations") or [] if isinstance(c, dict)],
        answer_format=data.get("answer_format"),
        citation_domains=data.get("citation_domains") or [],
        evidence_response_ids=data.get("evidence_response_ids") or [],
        llm_model=data.get("llm_model") or "", prompt_version=data.get("prompt_version") or "",
        generated_at=datetime.fromisoformat(data["generated_at"]) if data.get("generated_at") else datetime.utcnow(),
        feedback=data.get("feedback"),
    )


@router.post("/cell-insights/{insight_id}/feedback", status_code=204)
def post_cell_insight_feedback(
    insight_id: int, payload: FeedbackPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """drawer 里的 👍 / 👎 / wrong 反馈."""
    row = db.get(AiTelemetryCellInsightORM, insight_id)
    if row is None:
        raise HTTPException(404, "insight not found")
    topic = db.get(AiTelemetryTopicORM, row.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "insight not found")
    row.feedback = payload.feedback
    db.commit()


# ─────────────────── v1.2 周报 ──────────────────────────


def _briefing_orm_to_out(b: AiTelemetryTopicBriefingORM) -> BriefingOut:
    actions_raw = json.loads(b.top_actions_json or "[]")
    return BriefingOut(
        id=b.id, topic_id=b.topic_id,
        period_start=b.period_start, period_end=b.period_end,
        body_md=b.body_md or "",
        kpi_snapshot=json.loads(b.kpi_snapshot_json or "{}"),
        top_actions=[BriefingAction(**a) for a in actions_raw if isinstance(a, dict)],
        delivered_email_at=b.delivered_email_at,
        feedback_score=b.feedback_score,
        llm_model=b.llm_model or "", prompt_version=b.prompt_version or "",
        generated_at=b.generated_at,
    )


@router.get("/topics/{topic_id}/briefings", response_model=list[BriefingOut])
def list_briefings(
    topic_id: int, limit: int = 12,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_topic_or_404(db, topic_id, current_user.id)
    rows = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicBriefingORM.period_end.desc())
          .limit(min(limit, 52))
          .all()
    )
    return [_briefing_orm_to_out(b) for b in rows]


@router.get("/briefings/{briefing_id}", response_model=BriefingOut)
def get_briefing(
    briefing_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    b = db.get(AiTelemetryTopicBriefingORM, briefing_id)
    if b is None:
        raise HTTPException(404, "briefing not found")
    topic = db.get(AiTelemetryTopicORM, b.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "briefing not found")
    return _briefing_orm_to_out(b)


@router.post("/briefings/{briefing_id}/feedback", status_code=204)
def post_briefing_feedback(
    briefing_id: int, payload: BriefingFeedbackPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    b = db.get(AiTelemetryTopicBriefingORM, briefing_id)
    if b is None:
        raise HTTPException(404, "briefing not found")
    topic = db.get(AiTelemetryTopicORM, b.topic_id)
    if topic is None or topic.user_id != current_user.id:
        raise HTTPException(404, "briefing not found")
    b.feedback_score = payload.score
    db.commit()


@router.post("/topics/{topic_id}/briefings/generate", response_model=BriefingOut)
async def trigger_briefing_generation(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动触发生成上一周的周报(管理员 / 客户主动点重新生成)."""
    _get_topic_or_404(db, topic_id, current_user.id)
    url = f"{TELEMETRY_SERVICE_URL}/briefing/generate"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, json={"topic_id": topic_id})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"telemetry-service unavailable: {e}")
    if not isinstance(data, dict) or data.get("status") == "not_found":
        raise HTTPException(404, "topic not found")
    # 重读 ORM(已经入库)
    b = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.id == data["id"])
          .one_or_none()
    )
    if b is None:
        raise HTTPException(500, "briefing inconsistency")
    return _briefing_orm_to_out(b)


# ─────────────────── 内部邮件投递 (briefings 调) ────────────


@router.post("/internal/telemetry-briefing-email", include_in_schema=False)
def internal_briefing_email(payload: dict):
    """telemetry-service 周报生成完毕后调用,本机内网投递邮件 (Resend).

    暂时只 log,等 SMTP / Resend 配置确认后填实际邮件代码.
    不需要 auth(内网调用,backend ↔ telemetry-service 同 VPC).
    """
    topic_id = payload.get("topic_id")
    label = payload.get("label")
    body_md = (payload.get("body_md") or "")[:500]
    log.info("[BRIEFING DELIVER] topic=%s week=%s body=%s", topic_id, label, body_md)
    return {"status": "logged"}
