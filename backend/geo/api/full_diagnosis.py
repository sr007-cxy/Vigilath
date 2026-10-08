"""One-click full diagnosis API.

POST /api/check/full/generate      trigger diagnosis (login + pro tier), 202 returns report_id
GET  /api/check/full/{report_id}   poll/fetch report (generating state omits large fields)
GET  /api/check/full               history list (paged)
POST /api/check/full/{report_id}/export-pdf  backend Playwright print export to vector PDF

All task state lives in the DB (full_diagnosis_reports), so polling is naturally consistent across multiple uvicorn workers;
the interaction protocol matches the "strategy optimization plan": the frontend polls every 3s until ready/failed.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel

from geo.api.auth import get_current_user
from geo.api.advanced import _ensure_tier, _check_url
from geo.database import SessionLocal
from geo.models.full_diagnosis import (
    FullDiagnosisReportORM,
    FullDiagnosisOut,
    FullDiagnosisProgress,
    FullDiagnosisSummary,
    FullDiagnosisList,
    GenerateFullDiagnosisPayload,
)
from geo.models.user import UserORM
from geo.services.full_diagnosis_generator import (
    DEFAULT_SCOPE,
    schedule_full_diagnosis,
    SCOPE_MODES,
    HARD_DEADLINE,
    STALE_THRESHOLD,
    _refund_full_diagnosis,
)
from geo.utils.error_handler import AppException
from geo.utils.request_log import request_log

log = logging.getLogger(__name__)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _loads(s: str | None, default):
    try:
        v = json.loads(s or "")
        return v if isinstance(v, type(default)) else default
    except Exception:  # noqa: BLE001
        return default


def _row_to_out(row: FullDiagnosisReportORM, *, full: bool) -> FullDiagnosisOut:
    progress = _loads(row.progress_json, {})
    out = FullDiagnosisOut(
        id=row.id,
        status=row.status or "generating",
        scope=getattr(row, "scope", None) or "full",
        url=row.url or "",
        brand_name=row.brand_name or "",
        competitor_domains=_loads(row.competitor_domains_json, []),
        industry_terms=_loads(row.industry_terms_json, []),
        brand_terms=_loads(row.brand_terms_json, []),
        progress=FullDiagnosisProgress(**progress) if progress else None,
        llm_model=row.llm_model or "",
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    if full:
        out.scores = _loads(row.scores_json, {}) or None
        out.narrative = _loads(row.narrative_json, {}) or None
        out.subresults = _loads(row.subresults_json, {}) or None
    return out


def _get_own_row(db: Session, report_id: int, user_id: int) -> FullDiagnosisReportORM:
    row = (
        db.query(FullDiagnosisReportORM)
          .filter(FullDiagnosisReportORM.id == report_id,
                  FullDiagnosisReportORM.user_id == user_id,
                  FullDiagnosisReportORM.deleted_at.is_(None))
          .first()
    )
    if row is None:
        raise AppException(status_code=404, message="诊断报告不存在")
    return row


def _ensure_can_afford_full_diagnosis(db: Session, user, scope: str,
                                      manual_count: int) -> None:
    """Is there enough to run one full diagnosis? If not, 402 with the shortfall and the top-up entry.

    The estimate shares its source with the actual charge in the generator: terms × engines × `batch_cell`. Terms are not
    planned yet here — use the manual term pack if given, otherwise the planner's target count `TARGET_QUERY_COUNT`.
    **Estimate only, no charge**; when the real term count is below the estimate, the user actually pays less.

    The lite tier skips the heavy-engine section, burns no cells, and is let through directly.
    """
    from geo.models.balance import PRICE_BATCH_CELL
    from geo.services import balance_service, topic_billing
    from geo.services.full_diagnosis_competitive import _engines
    from geo.services.full_diagnosis_queries import TARGET_QUERY_COUNT

    if (scope or "").strip().lower() == "lite":
        return
    uid = topic_billing.user_payer_id(db, getattr(user, "id", None))
    if uid is None or not topic_billing.enabled():
        return
    queries = manual_count if manual_count > 0 else TARGET_QUERY_COUNT
    engines = max(1, len(_engines()))
    need = queries * engines * balance_service.get_price(db, PRICE_BATCH_CELL)
    if need <= 0 or balance_service.can_afford(db, uid, need):
        return
    bal = balance_service.get_balance(db, uid)
    raise AppException(
        status_code=402,
        message=f"积分不足:本次诊断约需 {need} 积分({queries} 词 × {engines} 引擎),"
                f"当前积分 {bal}。到「账户 → 我的积分」充值后即可开始。",
    )


class SuggestTermsIn(BaseModel):
    brand: str = ""
    url: str = ""


@router.post("/check/suggest-industry-terms")
def suggest_industry_terms(body: SuggestTermsIn):
    """Infer industry term candidates from the brand name, for the frontend "one-click extract, click to add".

    **No login required**: extracting industry terms is just a form helper, not a charged action (charging happens at report generation).
    Uses the lightweight suggest_industry_terms_llm call (returns only a few category terms), back in 3-8s — do not use
    full profile extraction, which takes 26-53s and often hits the frontend timeout, causing "extraction failed/empty".
    """
    from geo.services.profile_extractor import suggest_industry_terms_llm

    brand = (body.brand or "").strip()
    if not brand:
        raise AppException(status_code=400, message="请先填品牌名,再提取行业词")

    try:
        terms, model_id = suggest_industry_terms_llm(brand)
    except Exception as e:  # noqa: BLE001
        log.warning("suggest-terms extraction failed brand=%s: %s", brand, e)
        raise AppException(status_code=502, message="行业词提取失败,请稍后重试或手动填写")
    if not model_id:
        raise AppException(status_code=503, message="AI 提取暂未开通,请手动填写行业词")

    # Dedupe, drop the brand name, cap at 10
    low_brand = brand.lower()
    seen, out = set(), []
    for c in terms:
        c = (c or "").strip()
        if not c or c.lower() == low_brand:
            continue
        k = c.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= 10:
            break
    return {"terms": out}


@router.post("/check/full/generate", status_code=202)
async def generate_full_diagnosis(
    body: GenerateFullDiagnosisPayload,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_tier(request, "full")
    # Website is optional: brands without a website (offline service providers, merchants with only an official account/mini program) can still be diagnosed;
    # the four site-side items (base/aeo/crawlTest/authority) are marked skipped in orchestration, and the AI side runs as usual.
    raw_url = (body.url or "").strip()
    clean_url = _check_url(raw_url) if raw_url else ""
    brand = (body.brand_name or "").strip()
    if not brand:
        raise AppException(status_code=400, message="品牌名不能为空")
    competitors = [d.strip() for d in (body.competitor_domains or []) if d.strip()][:5]
    industry_terms = [t.strip() for t in (body.industry_terms or []) if t.strip()][:5]
    brand_terms = [t.strip() for t in (body.brand_terms or []) if t.strip()][:5]
    # Manual term pack: only strip + dedupe + 40-item cap, **no rewriting, truncation, or adding terms**
    # (terms finalized in the consultant's plan are authoritative input; the system's only freedom is forcing scene, see build_manual_plan)
    query_pack: list[dict] = []
    seen_q: set[str] = set()
    for item in (body.queries or []):
        text = " ".join((item.text or "").split()).strip()
        if not text:
            continue
        key = "".join(text.split()).lower()
        if key in seen_q:
            continue
        seen_q.add(key)
        query_pack.append({"text": text[:200], "scene": (item.scene or "").strip()})
        if len(query_pack) >= 40:
            break
    # Audit scope tier: full (eight items) / lite (four site-side items). Omitted means full.
    scope = (body.scope or DEFAULT_SCOPE).strip().lower()
    if scope not in SCOPE_MODES:
        raise AppException(status_code=400,
                           message=f"检测范围只支持 {'/'.join(SCOPE_MODES)}")
    # lite only runs site-side items; without a website there is nothing to run
    if scope == "lite" and not clean_url:
        raise AppException(status_code=400, message="轻量检测只跑站点侧检测,必须提供官网地址")
    # Industry terms are just the planner's term source; with a manual term pack they are no longer needed.
    # lite runs no term-based items, so industry terms are meaningless for it and not required.
    if scope != "lite" and not industry_terms and not query_pack:
        raise AppException(status_code=400, message="请至少填写一个行业词")
    if not brand_terms:
        brand_terms = [brand]

    # Credits pre-check — the real charge happens in the generator (by the **actually** planned term count); this is just an early "enough or not"
    # block so users see 402 before clicking, instead of discovering halfway through that the heavy-engine section was skipped.
    _ensure_can_afford_full_diagnosis(db, current_user, scope, len(query_pack))

    # Idempotent: if the same user + URL + tier already has an unexpired generating row → return it, preventing duplicate tasks from double clicks/refreshes.
    # The tier must be part of the key: a running lite report must not block the full audit the user clicked as a duplicate, and vice versa.
    cutoff = datetime.utcnow() - STALE_THRESHOLD
    q = (
        db.query(FullDiagnosisReportORM)
          .filter(FullDiagnosisReportORM.user_id == current_user.id,
                  FullDiagnosisReportORM.status == "generating",
                  FullDiagnosisReportORM.scope == scope,
                  FullDiagnosisReportORM.updated_at >= cutoff)
    )
    # Without a website url is an empty string; deduping by url would mistake the same user's other brand task for the same order
    if clean_url:
        q = q.filter(FullDiagnosisReportORM.url == clean_url)
    else:
        q = q.filter(FullDiagnosisReportORM.url == "",
                     FullDiagnosisReportORM.brand_name == brand)
    existing = q.first()
    if existing is not None:
        return {"report_id": existing.id, "status": "generating"}

    row = FullDiagnosisReportORM(
        user_id=current_user.id,
        status="generating",
        scope=scope,
        url=clean_url,
        brand_name=brand,
        competitor_domains_json=json.dumps(competitors, ensure_ascii=False),
        industry_terms_json=json.dumps(industry_terms, ensure_ascii=False),
        query_pack_json=json.dumps(query_pack, ensure_ascii=False),
        brand_terms_json=json.dumps(brand_terms, ensure_ascii=False),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    from geo.api.geo import _resolve_membership_from_request
    membership, _ = _resolve_membership_from_request(request)
    with request_log("check.full.generate", "full", clean_url,
                     user_id=current_user.id,
                     tier=membership.slug if membership else "free") as rec:
        rec["report_id"] = row.id
        schedule_full_diagnosis(row.id)

    return {"report_id": row.id, "status": "generating"}


@router.get("/check/full", response_model=FullDiagnosisList)
async def list_full_diagnosis(
    page: int = 1,
    size: int = 10,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        db.query(FullDiagnosisReportORM)
          .filter(FullDiagnosisReportORM.user_id == current_user.id,
                  FullDiagnosisReportORM.deleted_at.is_(None))
          .order_by(FullDiagnosisReportORM.created_at.desc())
    )
    total = q.count()
    rows = q.offset(max(0, (page - 1) * size)).limit(size).all()
    items = []
    for r in rows:
        scores = _loads(r.scores_json, {})
        items.append(FullDiagnosisSummary(
            id=r.id, url=r.url or "", brand_name=r.brand_name or "",
            status=r.status or "generating",
            total_score=scores.get("total"), grade=scores.get("grade"),
            created_at=r.created_at,
        ))
    return FullDiagnosisList(items=items, total=total, page=page, size=size)


@router.get("/check/full/{report_id}", response_model=FullDiagnosisOut)
async def get_full_diagnosis(
    report_id: int,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _get_own_row(db, report_id, current_user.id)

    # Lazy zombie check (a second safeguard beyond the startup reset, covering multi-instance setups).
    # Both criteria share a source with reset_stale_full_diagnosis: heartbeat stopped (updated_at) + running too long
    # (created_at). updated_at alone cannot catch "thread alive but stuck" — the heartbeat keeps refreshing.
    if row.status == "generating":
        now = datetime.utcnow()
        hb_dead = row.updated_at and now - row.updated_at > STALE_THRESHOLD
        overdue = row.created_at and now - row.created_at > HARD_DEADLINE
        if hb_dead or overdue:
            row.status = "failed"
            row.error = ("后端在诊断途中重启,后台任务被中断。请重新生成。" if hb_dead
                         else "诊断超过时限仍未完成,后台任务已被回收。请重新生成。")
            row.updated_at = now
            db.commit()
            db.refresh(row)
            # This lazy fallback marks failed directly, bypassing _mark_failed / startup reset,
            # a fourth path none of the three refund triggers reach. An interrupted report may have been charged before
            # the heavy-engine section started, and missing it means no refund. Idempotent + auto no-op when not charged, so repeated calls are harmless.
            _refund_full_diagnosis(report_id)

    # Generating poll responses deliberately omit the large subresults/scores/narrative fields (every 3s)
    return _row_to_out(row, full=(row.status != "generating"))


@router.post("/check/full/{report_id}/export-pdf")
async def export_full_diagnosis_pdf(
    report_id: int,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reuses report_export's Playwright screenshot pipeline. The path is fixed by the backend as
    /diagnosis/{id}, accepting no frontend parameters — no arbitrary-URL rendering surface; ownership is checked against this table."""
    row = _get_own_row(db, report_id, current_user.id)
    if row.status != "ready":
        raise AppException(status_code=400, message="报告尚未生成完成,无法导出")

    from geo.api.report_export import _capture_pdf, _SEM

    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token:
        raise AppException(status_code=401, message="missing token")

    lang = request.query_params.get("lang", "zh")
    theme = request.query_params.get("theme", "")
    # Times in the report are shown in the user's timezone; the headless browser runs on the server, so the timezone must come from the frontend.
    # Only IANA forms (Area/City) are allowed; anything else falls back to Beijing, so nothing uncontrolled gets into the URL.
    tz = (request.query_params.get("tz") or "").strip()
    if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){0,2}", tz):
        tz = "Asia/Shanghai"
    title = f"{row.brand_name} · 综合诊断报告" if lang.startswith("zh") else \
            f"{row.brand_name} · Diagnosis Report"

    async with _SEM:
        try:
            # ?export=1: the page expands collapsed sections accordingly (groups with collapsed per-item details would be
            # missing content in the PDF), see frontend reportKit.isPdfExport
            pdf = await _capture_pdf(f"/diagnosis/{row.id}?export=1&tz={quote(tz, safe='')}", token, lang, theme,
                                     current_user, title=title)
        except Exception as e:  # noqa: BLE001
            log.exception("[full-diagnosis] PDF rendering failed report=%s", row.id)
            hint = ""
            if "ERR_CONNECTION_REFUSED" in str(e) or "ERR_CONNECTION" in str(e):
                # Almost always 127.0.0.1 in the container points at the backend itself and cannot reach the frontend page
                from geo.api.report_export import _BASE_URL
                hint = (f"(无头浏览器打不开报告页:{_BASE_URL} 在渲染进程里不可达,"
                        f"容器部署请把 REPORT_EXPORT_BASE_URL 指向前端服务地址)")
            raise AppException(status_code=502, message=f"render failed: {e}{hint}")

    return Response(content=pdf, media_type="application/pdf")
