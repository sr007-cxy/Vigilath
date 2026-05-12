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
    DomainCount, KpiBlock, OverviewOut, OwnedSplit, ResponseOut, RunNowResult,
    RunOut, TopicOut, TopicPayload, TrendPoint, VALID_ENGINES,
)
from geo.models.sentiment import SentimentAccountORM
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
    # de-dup + strip
    cleaned_queries = []
    seen = set()
    for q in payload.queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            cleaned_queries.append(q)
    if not cleaned_queries:
        raise HTTPException(400, "queries cannot be empty")
    payload.queries = cleaned_queries
    payload.engines = list(dict.fromkeys(payload.engines))


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
    t = AiTelemetryTopicORM(
        user_id=current_user.id,
        name=payload.name.strip(),
        queries_json=json.dumps(payload.queries, ensure_ascii=False),
        engines_json=json.dumps(payload.engines, ensure_ascii=False),
        enabled=payload.enabled,
    )
    db.add(t)
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
    t.name = payload.name.strip()
    t.queries_json = json.dumps(payload.queries, ensure_ascii=False)
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
      - visibility = (含品牌词的 response 数 / 总 response 数) × 100
      - citations = sum(len(citations_json)) 周期内所有 response
      - growth = citations 相对上一周期的 pct change
      - engines_covered = 本期有 ≥1 成功 response 的引擎数 / topic.engines 总数
    品牌词来源:用户的 sentiment_account.target + aliases.
    """
    topic = _get_topic_or_404(db, topic_id, current_user.id)
    period_days = max(1, min(period, 90))
    now = datetime.utcnow()
    period_start = now - timedelta(days=period_days)
    prev_start = period_start - timedelta(days=period_days)

    # 品牌词
    acc = (
        db.query(SentimentAccountORM)
          .filter_by(user_id=current_user.id, active=True)
          .order_by(SentimentAccountORM.created_at.asc())
          .first()
    )
    brand_keywords: list[str] = []
    if acc:
        brand_keywords.append(acc.target)
        brand_keywords.extend(json.loads(acc.aliases_json or "[]"))
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
        for r in rows:
            cits = json.loads(r.citations_json or "[]")
            cit_total += len(cits)
            if not r.error:
                succ_engines.add(r.engine)
                if brand_lc and r.answer and any(k in r.answer.lower() for k in brand_lc):
                    mention_hit += 1
        total = sum(1 for r in rows if not r.error)
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

    # 每日 trend: date -> {engine: citation_count}
    bucket: dict[str, dict[str, int]] = {}
    engines_in_window: set[str] = set()
    for r in rows_curr:
        d = r.created_at.strftime("%Y-%m-%d")
        if d not in bucket:
            bucket[d] = {}
        cits = json.loads(r.citations_json or "[]")
        bucket[d][r.engine] = bucket[d].get(r.engine, 0) + len(cits)
        engines_in_window.add(r.engine)

    # 填补缺失日期为空,保证 sparkline 连续
    trend: list[TrendPoint] = []
    sparkline_cit: list[float] = []
    sparkline_vis: list[float] = []
    for i in range(period_days):
        d = (period_start + timedelta(days=i)).strftime("%Y-%m-%d")
        day_vals = bucket.get(d, {})
        trend.append(TrendPoint(date=d, values=day_vals))
        sparkline_cit.append(float(sum(day_vals.values())))
        # 简化:visibility sparkline 与 citations 用同形状(每天 mention 率算法太重,前端先不展示精确)
        sparkline_vis.append(float(sum(day_vals.values())))

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
            video_url=r.video_url, error=r.error,
            created_at=r.created_at,
        ))
    return out
