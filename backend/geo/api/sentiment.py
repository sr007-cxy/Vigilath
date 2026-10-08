"""Sentiment monitoring API routes.

Responsibilities:
1. Account CRUD (/api/sentiment/accounts/...)
2. Task status queries and manual triggers (/run-status, /run-now)
3. Data query proxy (fetch data from sentinel-service, add auth + tidy up)
4. Knowledge base CRUD
5. Draft generation

Scheduled automatic triggers live in services/sentiment_scheduler.py — the same
sentiment_pipeline orchestration, just called directly in APScheduler's own thread;
this file always uses submit_pipeline() to put work into the dedicated batch-run pool, not occupying API threads.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.api.auth import get_current_user
from geo.database import SessionLocal
from geo.models.sentiment import (
    KeywordGroup, SentimentAccountCreate, SentimentAccountORM, SentimentAccountOut,
    SentimentAccountUpdate, SentimentKnowledgeORM, SentimentRunLogORM,
    SentimentPlatformORM, SentimentPlatformOut,
    RunStatusOut, KnowledgeOut, _as_utc, account_symbol, flatten_keyword_groups,
)
from geo.models.balance import PRICE_SENTIMENT_DAILY, PRICE_SENTIMENT_ONCE
from geo.models.user import User, UserORM
from geo.services import balance_service, sentinel_client
from geo.services.sentinel_client import SentinelError
from geo.services.sentiment_pipeline import submit_pipeline
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    apply_enterprise_scope,
    require_resource_access,
    resource_not_found,
    create_operation_context,
    create_resource_operation_context,
)
from geo.services.system_audit import append_event as append_system_audit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sentiment")


# ─────────────────────────── DI helpers ────────────────────────


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_account_or_404(
    db: Session, account_id: int, actor: User, action: str = ACTION_WRITE,
) -> SentimentAccountORM:
    acc = db.get(SentimentAccountORM, account_id)
    if not acc:
        raise resource_not_found()
    require_resource_access(db, actor, acc, action)
    return acc


def _ensure_can_afford_sentiment(db: Session, user: User, price_key: str,
                                 what: str = "舆情监测") -> None:
    """Credits admission — allowed if there is enough for one run.

    Before 2026-08-25 this was a hard gate requiring "a real paid pro-or-higher subscription", combined with the quota's fake
    `cap = 999` implementation; the effect: those who paid had unlimited use, those who did not could create none.
    Now sentiment monitoring is **charged per run**, so admission naturally reduces to "are there enough credits for one run"; tiers no longer matter.

    `price_key` has two levels: quick check `sentiment_once` (10 yuan per order), daily monitoring `sentiment_daily`
    (5 yuan per day) — account creation pre-checks against the latter; do not block people at the door with the quick-check price.

    Ops (admin/operator) own accounts are neither charged nor blocked — internal topics and demo accounts live there.
    """
    if _is_staff_user(user):
        return
    need = balance_service.get_price(db, price_key)
    if need <= 0:
        return
    if balance_service.can_afford(db, user.id, need):
        return
    bal = balance_service.get_balance(db, user.id)
    raise HTTPException(402, {
        "code": "INSUFFICIENT_BALANCE",
        "message": f"{what}每次消耗 {need} 积分,当前积分 {bal},充值后即可开始。",
        "required_cents": need,
        "balance_cents": bal,
        "topup": {"web": "/account/balance"},
    })


def _is_staff_user(user) -> bool:
    return (getattr(user, "role", "") or "user") in ("admin", "operator")


def _enforce_account_quota(db: Session, user_id: int) -> None:
    """Cap on the number of brands monitored at the same time.

    With per-run charging this gate is no longer "selling tiers", only preventing scripts from bulk-creating accounts — every active account
    is charged daily on an ongoing basis, so runaway counts cost real money. Default 20, adjustable via env.
    """
    try:
        cap = max(1, int(os.environ.get("SENTIMENT_MAX_ACCOUNTS", 20)))
    except (TypeError, ValueError):
        cap = 20
    existing = db.query(SentimentAccountORM).filter_by(
        user_id=user_id, active=True,
    ).count()
    if existing >= cap:
        raise HTTPException(402, {
            "code": "ACCOUNT_QUOTA",
            "message": f"最多同时监控 {cap} 个品牌,请先停用不需要的。",
        })


def _has_meaningful_change(acc: SentimentAccountORM, payload: SentimentAccountUpdate) -> bool:
    """A change in keywords/intent/aliases is worth a rerun."""
    fields = ("target", "ticker", "intent")
    for f in fields:
        v = getattr(payload, f, None)
        if v is not None and getattr(acc, f) != v:
            return True
    list_fields = (
        ("keywords", "keywords_json"), ("excludes", "excludes_json"),
        ("aliases", "aliases_json"), ("media_allowlist", "media_allowlist_json"),
        ("weixin_album_urls", "weixin_album_urls_json"),
        ("newsnow_sources", "newsnow_sources_json"),
    )
    for in_name, db_name in list_fields:
        v = getattr(payload, in_name, None)
        if v is None:
            continue
        existing = json.loads(getattr(acc, db_name) or "[]")
        if set(v) != set(existing):
            return True
    if payload.keyword_groups is not None:
        new_terms = set(flatten_keyword_groups(payload.keyword_groups))
        existing_groups = json.loads(acc.keyword_groups_json or "[]")
        existing_terms = set(flatten_keyword_groups(
            [KeywordGroup.model_validate(g) for g in existing_groups]
        ))
        if new_terms != existing_terms:
            return True
    return False


def _resolve_keywords_for_storage(
    keywords: Optional[list[str]],
    keyword_groups: Optional[list[KeywordGroup]],
    fallback_keywords: list[str],
) -> list[str]:
    """Decide what keywords_json stores — keyword_groups first, flat keywords as fallback.

    Emptiness uses truthiness rather than `is not None`: Create's keyword_groups defaults to `[]`,
    so with `is not None` users who only filled flat keywords during onboarding would be flattened to
    an empty list — no keywords left, and no error.
    """
    if keyword_groups:
        return flatten_keyword_groups(keyword_groups)
    if keywords is not None:
        return keywords
    return fallback_keywords


# ─────────────────────────── Platform catalog ──────────────────────────


@router.get("/platforms", response_model=list[SentimentPlatformOut])
def list_platforms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Media platform catalog (global, read-only).
    Available to any logged-in user regardless of membership tier — this is metadata for rendering the whitelist selector and article page filters,
    exposing no private user data. Order: category default order → sort_order within the group.
    """
    category_rank = {
        "finance": 1, "social": 2, "forum": 3,
        "video": 4, "news": 5, "overseas": 6,
    }
    rows = (
        db.query(SentimentPlatformORM)
          .filter_by(enabled=True)
          .all()
    )
    rows.sort(key=lambda r: (category_rank.get(r.category, 99), r.sort_order, r.code))
    return [SentimentPlatformOut.from_orm_row(r) for r in rows]


# ─────────────────────────── Account CRUD ────────────────────────


# ─────────────────── One-off quick check (no account created) ────────────────────
#
# A separate line from "daily health monitoring", do not mix: this does not create a sentiment_account, write tenant tables,
# or enter batch runs; it runs with just a brand name, and results only go into a 24h cache (see services/sentiment_once.py).
#
# Previously only the agent tool could call it — web users could not reach it except by asking in the chat box. This adds an equivalent entry.


class OncePayload(BaseModel):
    brand: str
    keywords: list[str] = []
    # Category terms: used for **brand disambiguation**. Searching by brand name alone pulls in same-named entities from other industries;
    # brands whose names contain industry words ("GEO Analytics") are worse — what comes back is the whole industry's negatives.
    # The same parameter as industry_terms in the agent-side run_sentiment_once.
    industry_terms: list[str] = []


@router.post("/once")
def start_once(
    payload: OncePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a one-off quick check order. One round takes up to 360s, so it is "start + poll", not waited on inline."""
    from geo.services import sentiment_once

    brand = (payload.brand or "").strip()
    if not brand:
        raise HTTPException(422, {"code": "NO_BRAND", "message": "请先填写品牌名"})
    _ensure_can_afford_sentiment(db, current_user, PRICE_SENTIMENT_ONCE, "舆情快检")

    cat = payload.industry_terms or None
    st = sentiment_once.start(current_user.id, brand, payload.keywords or None, cat)
    if st.get("state") == "degraded":
        # When the cache is unavailable, **do not pretend to queue** (queued but results cannot be stored is the worst kind of bad);
        # run one round synchronously instead, with a hard cap
        st = sentiment_once.run_sync(current_user.id, brand, payload.keywords or None, cat)
    return {"brand": brand, **st}


@router.get("/once")
def get_once(
    brand: str,
    keywords: Optional[str] = None,
    industry_terms: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Poll this order's status: ready / running / failed / stale / none.

    ★ industry_terms must be **the same as passed at start** — it is part of the cache key; a mismatch is a different key,
      and you will never poll your own order.
    """
    from geo.services import sentiment_once

    def _split(raw: Optional[str]) -> Optional[list[str]]:
        return [k.strip() for k in (raw or "").split(",") if k.strip()] or None

    return {"brand": brand,
            **sentiment_once.status(current_user.id, brand, _split(keywords),
                                    _split(industry_terms))}


@router.get("/accounts", response_model=list[SentimentAccountOut])
def list_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = apply_enterprise_scope(
        db.query(SentimentAccountORM), db, current_user, SentimentAccountORM.user_id,
    )
    rows = query.order_by(SentimentAccountORM.created_at.desc()).all()
    return [SentimentAccountOut.from_orm_row(r) for r in rows]


@router.post("/accounts", response_model=SentimentAccountOut, status_code=201)
def create_account(
    payload: SentimentAccountCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an account, optionally triggering the first run immediately.

    Flow:
    1. Check the balance is enough for one run (sentiment monitoring is charged per run; membership tier no longer matters)
    2. Check the cap on concurrently monitored brands
    3. INSERT, state machine = pending (if run_now)
    4. Enqueue first_run via BackgroundTasks
    5. Return the account entity (frontend jumps to the workbench + enters the polling/manual-refresh flow)
    """
    owner_user_id = payload.enterprise_user_id or current_user.id
    owner_user = db.get(UserORM, owner_user_id)
    if owner_user is None:
        raise resource_not_found()
    _ensure_can_afford_sentiment(db, owner_user, PRICE_SENTIMENT_DAILY, "舆情日常监控")
    _enforce_account_quota(db, owner_user_id)

    # Uniqueness: one monitored entity per user.
    # The key is symbol (= stock ticker, or the brand name if empty), not ticker — since the stock ticker became optional,
    # two different unlisted brands both have an empty ticker, and comparing tickers would misjudge the second brand as a duplicate.
    target = payload.target.strip()
    ticker = (payload.ticker or "").strip()
    if not target:
        raise HTTPException(400, "品牌名不能为空")
    new_symbol = ticker or target
    existing = db.query(SentimentAccountORM).filter_by(user_id=owner_user_id).all()
    if any(account_symbol(a) == new_symbol for a in existing):
        raise HTTPException(409, f"「{new_symbol}」已在监测中,请勿重复添加")

    flat_keywords = _resolve_keywords_for_storage(
        payload.keywords, payload.keyword_groups, payload.keywords,
    )
    acc = SentimentAccountORM(
        user_id=owner_user_id,
        target=target,
        ticker=ticker,
        aliases_json=json.dumps(payload.aliases, ensure_ascii=False),
        intent=payload.intent,
        keywords_json=json.dumps(flat_keywords, ensure_ascii=False),
        keyword_groups_json=json.dumps(
            [g.model_dump(exclude_none=True) for g in (payload.keyword_groups or [])],
            ensure_ascii=False,
        ),
        excludes_json=json.dumps(payload.excludes, ensure_ascii=False),
        media_allowlist_json=json.dumps(payload.media_allowlist, ensure_ascii=False),
        notify_emails_json=json.dumps([str(e) for e in payload.notify_emails], ensure_ascii=False),
        weixin_album_urls_json=json.dumps(payload.weixin_album_urls, ensure_ascii=False),
        newsnow_sources_json=json.dumps(payload.newsnow_sources, ensure_ascii=False),
        run_hour=payload.run_hour,
        active=True,
        last_run_status="pending" if payload.run_now else None,
    )
    db.add(acc)
    db.flush()
    _, operation = create_operation_context(
        db, current_user, owner_user_id,
        action="sentiment.account.create",
        target_resource_type="sentiment_account",
        target_resource_id=acc.id,
    )
    append_system_audit(
        db, operation_context=operation, event_type="resource_created",
        action="sentiment.account.create", object_type="sentiment_account",
        object_id=acc.id, result="success",
    )
    db.commit()
    db.refresh(acc)

    if payload.run_now:
        log.info("[sentiment] queue first_run for account %s", acc.id)
        background_tasks.add_task(
            submit_pipeline, acc.id, "first_run", False, operation.id,
        )

    return SentimentAccountOut.from_orm_row(acc)


@router.get("/accounts/{account_id}", response_model=SentimentAccountOut)
def get_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return SentimentAccountOut.from_orm_row(acc)


@router.put("/accounts/{account_id}", response_model=SentimentAccountOut)
def update_account(
    account_id: int,
    payload: SentimentAccountUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change account config; if keywords/intent/aliases change, automatically enqueue a rerun."""
    acc = _get_account_or_404(db, account_id, current_user)
    if payload.target is not None:
        payload.target = payload.target.strip()
        if not payload.target:
            raise HTTPException(400, "品牌名不能为空")
    if payload.ticker is not None:
        payload.ticker = payload.ticker.strip()
    rerun = _has_meaningful_change(acc, payload)

    # Check uniqueness before assignment: checking after the change lets autoflush flush the dirty data first,
    # and hitting uq_user_target_ticker becomes a 500 with no explanation.
    new_target = payload.target if payload.target is not None else acc.target
    new_ticker = payload.ticker if payload.ticker is not None else acc.ticker
    new_symbol = (new_ticker or "").strip() or (new_target or "").strip()
    others = (
        db.query(SentimentAccountORM)
          .filter(SentimentAccountORM.user_id == acc.user_id,
                  SentimentAccountORM.id != acc.id)
          .all()
    )
    if any(account_symbol(a) == new_symbol for a in others):
        raise HTTPException(409, f"「{new_symbol}」已在监测中,请勿重复添加")

    if payload.target is not None: acc.target = payload.target
    # "" is a valid value (changing a listed entity back to unlisted); only None means "leave this column alone"
    if payload.ticker is not None: acc.ticker = payload.ticker
    if payload.aliases is not None: acc.aliases_json = json.dumps(payload.aliases, ensure_ascii=False)
    if payload.intent is not None: acc.intent = payload.intent
    if payload.excludes is not None: acc.excludes_json = json.dumps(payload.excludes, ensure_ascii=False)
    if payload.media_allowlist is not None:
        acc.media_allowlist_json = json.dumps(payload.media_allowlist, ensure_ascii=False)
    if payload.notify_emails is not None:
        acc.notify_emails_json = json.dumps([str(e) for e in payload.notify_emails], ensure_ascii=False)
    if payload.run_hour is not None: acc.run_hour = payload.run_hour
    if payload.weixin_album_urls is not None:
        acc.weixin_album_urls_json = json.dumps(payload.weixin_album_urls, ensure_ascii=False)
    if payload.newsnow_sources is not None:
        acc.newsnow_sources_json = json.dumps(payload.newsnow_sources, ensure_ascii=False)
    if payload.active is not None: acc.active = payload.active

    # When keyword_groups changes, recompute keywords_json from groups first;
    # otherwise if the client only sends keywords, keep the old logic.
    if payload.keyword_groups is not None:
        acc.keyword_groups_json = json.dumps(
            [g.model_dump(exclude_none=True) for g in payload.keyword_groups],
            ensure_ascii=False,
        )
        acc.keywords_json = json.dumps(
            flatten_keyword_groups(payload.keyword_groups), ensure_ascii=False,
        )
    elif payload.keywords is not None:
        acc.keywords_json = json.dumps(payload.keywords, ensure_ascii=False)

    # If a round is already queued (pending) or running, do not queue another:
    #   - the pending round **has not started**; when it starts it reads the latest config from this submission,
    #     so queuing another is pure duplicate work (and it would run a whole extra round afterwards, not merge);
    #   - even if queued during running, the reentrancy guard at the start of run_pipeline_for_account would skip it immediately.
    # Before this dedup, "change one term and click save" = queue a batch run, and clicking several times stacked several rounds.
    # Note /run-now deliberately does **not** dedupe — it is the escape hatch in case pending gets stuck.
    if rerun and acc.active and acc.last_run_status not in ("pending", "running"):
        acc.last_run_status = "pending"
        _, operation = create_resource_operation_context(
            db,
            current_user,
            acc,
            action="sentiment.run",
            target_resource_type="sentiment_account",
        )
        append_system_audit(
            db,
            operation_context=operation,
            event_type="task_created",
            action="sentiment.run",
            object_type="sentiment_account",
            object_id=acc.id,
            result="success",
        )
        background_tasks.add_task(
            submit_pipeline, acc.id, "manual", False, operation.id,
        )

    db.commit()
    db.refresh(acc)
    return SentimentAccountOut.from_orm_row(acc)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _get_account_or_404(db, account_id, current_user)
    db.delete(acc)
    db.commit()
    return None


# ─────────────────────────── Task status / manual trigger ────────────────


@router.get("/accounts/{account_id}/run-status", response_model=RunStatusOut)
def get_run_status(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Called on the frontend's "manual refresh" to check task progress."""
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return RunStatusOut(
        status=acc.last_run_status,
        last_run_at=_as_utc(acc.last_run_at),
        error=acc.last_run_error,
    )


@router.post("/accounts/{account_id}/run-now")
def run_now(
    account_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Called when the user clicks the "retry" or "run once now" button."""
    acc = _get_account_or_404(db, account_id, current_user)
    if acc.last_run_status == "running":
        raise HTTPException(409, "task already running, please wait")

    # Unified call gate (internal unlimited, external limited by tier); both write a call_audit row
    from geo.services.call_policy import CallDenied, CallerRef, enforce
    try:
        enforce(db, CallerRef(channel="web", account_id=current_user.id, user_id=current_user.id),
                "sentiment_run_now", endpoint="/api/sentiment/accounts/{id}/run-now")
    except CallDenied as e:
        raise HTTPException(e.code, e.reason)

    _, operation = create_resource_operation_context(
        db, current_user, acc,
        action="sentiment.run", target_resource_type="sentiment_account",
    )
    append_system_audit(
        db, operation_context=operation, event_type="task_created",
        action="sentiment.run", object_type="sentiment_account",
        object_id=acc.id, result="success",
    )
    acc.last_run_status = "pending"
    db.commit()
    background_tasks.add_task(
        submit_pipeline, account_id, "manual", False, operation.id,
    )
    return {"queued": True}


@router.get("/accounts/{account_id}/run-logs")
def list_run_logs(
    account_id: int,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Task execution history (for troubleshooting)."""
    _get_account_or_404(db, account_id, current_user, ACTION_READ)
    rows = (
        db.query(SentimentRunLogORM)
          .filter_by(account_id=account_id)
          .order_by(SentimentRunLogORM.id.desc())
          .limit(limit)
          .all()
    )
    return [
        {
            "id": r.id, "trigger": r.trigger, "started_at": r.started_at,
            "ended_at": r.ended_at, "status": r.status,
            "error": r.error, "duration_s": r.duration_s,
            "stats": json.loads(r.stats_json) if r.stats_json else None,
        } for r in rows
    ]


@router.get("/accounts/{account_id}/websearch-latest")
def get_websearch_latest(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the latest Yuanbao WebSearch sentiment quick-check result."""
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    report = _proxy(sentinel_client.get_websearch_latest, account_id, acc.symbol)
    return {
        **report,
        "status": acc.last_run_status,
        "error": acc.last_run_error,
        "last_run_at": _as_utc(acc.last_run_at),
    }


# ─────────────────────────── Data queries (proxying sentinel-service) ─


def _proxy(fn, *args, **kwargs):
    """Unified handling of sentinel-service errors.

    Three cases are distinguished, to avoid showing "upstream error" as "service unreachable":
      - SentinelError          → 502, sentinel business failure (status=failed)
      - httpx.HTTPStatusError  → 502, sentinel returned 4xx/5xx (endpoint bug / data issue),
                                  exposing the upstream status code + response snippet for troubleshooting
      - connection/timeout errors → 503, sentinel truly unreachable
    """
    try:
        return fn(*args, **kwargs)
    except SentinelError as e:
        raise HTTPException(502, f"sentinel error: {e}") from e
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300]
        log.error("sentinel-service %s on %s: %s",
                  e.response.status_code, e.request.url, body)
        raise HTTPException(
            502, f"sentinel upstream {e.response.status_code}: {body}"
        ) from e
    except (httpx.TimeoutException, httpx.TransportError) as e:
        log.exception("sentinel-service unreachable: %s", e)
        raise HTTPException(503, "sentinel service unavailable") from e
    except Exception as e:
        log.exception("sentinel-service proxy error: %s", e)
        raise HTTPException(502, f"sentinel proxy error: {e}") from e


# The 4 data proxies below all take a `ticker` query parameter — **accepted but unused**.
# symbol always comes from the server-side `acc.symbol` (= stock ticker, or the brand name if empty):
# what the client sends should not decide whose data can be read, and since the ticker became optional the frontend may well send an empty string,
# in which case sentinel would query symbol='' and always get 0 rows. The parameter is kept only for old-frontend compatibility.
@router.get("/accounts/{account_id}/posts")
def proxy_posts(
    account_id: int, ticker: Optional[str] = None,
    limit: int = 50, offset: int = 0, days: int = 1,
    start: Optional[str] = None, end: Optional[str] = None,
    sort_by: str = "newest",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Article list + paging + sorting.

    Time filter (on ingested_at): start/end (YYYY-MM-DD, inclusive) take precedence,
    otherwise days (1 = today, N>1 = last N days, 0 = all history).
    Sorting: sort_by (see the sentinel-service /posts endpoint).
    """
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return _proxy(
        sentinel_client.list_posts,
        account_id, acc.symbol,
        limit=limit, days=days, offset=offset, start=start, end=end,
        sort_by=sort_by,
    )


@router.get("/accounts/{account_id}/briefs")
def proxy_briefs(
    account_id: int, ticker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return _proxy(sentinel_client.list_briefs, account_id, acc.symbol)


@router.get("/accounts/{account_id}/briefs/{brief_id}")
def proxy_brief_detail(
    account_id: int, brief_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return _proxy(sentinel_client.get_brief, account_id, brief_id)


@router.get("/accounts/{account_id}/drafts")
def proxy_drafts(
    account_id: int, ticker: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return _proxy(sentinel_client.list_drafts, account_id, acc.symbol)


@router.get("/accounts/{account_id}/today")
def proxy_today(
    account_id: int, ticker: Optional[str] = None, days: int = 7,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tab 1 aggregate data — KPIs/trends/risk distribution/latest brief/Top 5."""
    acc = _get_account_or_404(db, account_id, current_user, ACTION_READ)
    return _proxy(sentinel_client.get_today, account_id, acc.symbol, days)


# ─────────────────── newsnow real-time trending (not bound to an account) ──────────────


@router.get("/newsnow/sources")
def proxy_newsnow_sources(
    current_user: User = Depends(get_current_user),
):
    """newsnow recommended source catalog (static). Requires login only, not bound to an account."""
    return _proxy(sentinel_client.list_newsnow_sources)


@router.get("/newsnow/hot")
def proxy_newsnow_hot(
    source: str, limit: int = 30,
    current_user: User = Depends(get_current_user),
):
    """Current trending list of the given source (for real-time news / today's hot topics)."""
    return _proxy(sentinel_client.get_newsnow_hot, source, limit)


# ─────────────────────────── Draft generation ────────────────────────


class GenerateDraftRequest(BaseModel):
    account_id: int
    source: Optional[str] = None
    post_id: Optional[str] = None
    topic: Optional[str] = None
    situation: Optional[str] = None


@router.post("/drafts/generate")
def generate_draft(
    payload: GenerateDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Synchronous call, waits for the LLM to return (5-10 seconds)."""
    acc = _get_account_or_404(db, payload.account_id, current_user)
    if not (payload.post_id or payload.topic):
        raise HTTPException(400, "post_id 或 topic 至少传一个")

    knowledge = {
        r.key: r.body
        for r in db.query(SentimentKnowledgeORM).filter_by(account_id=acc.id).all()
        if r.body
    }

    return _proxy(
        sentinel_client.run_respond,
        account_id=acc.id, ticker=acc.symbol,
        source=payload.source, post_id=payload.post_id,
        topic=payload.topic, situation=payload.situation,
        knowledge=knowledge or None,
    )


# ─────────────────────────── Knowledge base ────────────────────────


VALID_KNOWLEDGE_KEYS = {"brand_voice", "legal_redlines", "response_playbook"}


@router.get("/accounts/{account_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_account_or_404(db, account_id, current_user, ACTION_READ)
    rows = db.query(SentimentKnowledgeORM).filter_by(account_id=account_id).all()
    return [KnowledgeOut(key=r.key, body=r.body, updated_at=r.updated_at) for r in rows]


class KnowledgeUpdate(BaseModel):
    body: str


@router.put("/accounts/{account_id}/knowledge/{key}", response_model=KnowledgeOut)
def upsert_knowledge(
    account_id: int, key: str, payload: KnowledgeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if key not in VALID_KNOWLEDGE_KEYS:
        raise HTTPException(400, f"key must be one of {VALID_KNOWLEDGE_KEYS}")
    _get_account_or_404(db, account_id, current_user)

    row = db.query(SentimentKnowledgeORM).filter_by(account_id=account_id, key=key).first()
    if row:
        row.body = payload.body
        row.updated_at = datetime.utcnow()
    else:
        row = SentimentKnowledgeORM(
            account_id=account_id, key=key, body=payload.body,
            updated_at=datetime.utcnow(),
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return KnowledgeOut(key=row.key, body=row.body, updated_at=row.updated_at)


# ─────────────────────────── Health check ────────────────────────


@router.get("/health")
def health(current_user: User = Depends(get_current_user)):
    """Forward sentinel-service health status."""
    try:
        upstream = sentinel_client.health()
        return {"status": "ok", "sentinel": upstream}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "sentinel_error": str(e)}
