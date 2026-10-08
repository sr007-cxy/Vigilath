"""Sentiment monitoring pipeline orchestration -- full monitor -> analyze -> brief flow for one account.

Shared by sentiment_scheduler (scheduled job) and manual /run-now triggers.
State machine updates + failure fallback + email delivery all live here.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import threading
from types import SimpleNamespace

from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.sentiment import (
    KeywordGroup, SentimentAccountORM, SentimentKnowledgeORM, SentimentRunLogORM,
    account_symbol, flatten_keyword_groups,
)
from geo.services import sentinel_client
from geo.services.sentinel_client import SentinelError

log = logging.getLogger(__name__)


# ── Dedicated batch run thread pool ───────────────────────────────────────────────────
# One batch run's timeout budget is monitor 1800s + analyze 1800s + brief 1200s -- worst case it holds
# a thread for over an hour. It used to hang directly off FastAPI's BackgroundTasks, and Starlette
# throws **synchronous** BackgroundTasks into anyio's default 40-thread pool -- the same pool shared by every
# `def` route in the backend. So each click of "save" queued another batch run, and once threads were exhausted **every** endpoint
# stalled together (users saw the save button spinning forever; even pages unrelated to sentiment slowed down).
#
# Use our own separate threads: batch runs queue behind each other and never touch API threads. Keep concurrency small --
# each batch run internally runs 8 crawlers against sentinel in parallel; 4 x 8 is already 32 concurrent outbound calls.
#
# Daemon threads + semaphore instead of ThreadPoolExecutor: the latter's workers are non-daemon
# and it registers an atexit join -- on deploy restart the process would hang "waiting for a crawl with 20 minutes left",
# until systemd/docker's stop timeout SIGKILLs it. Batch runs are discardable on restart anyway (the state machine resumes next round),
# and shouldn't slow down releases.
_PIPELINE_WORKERS = max(1, int(os.environ.get("SENTIMENT_PIPELINE_WORKERS", "4")))
_pipeline_slots = threading.Semaphore(_PIPELINE_WORKERS)


def _run_pipeline_slot(account_id: int, trigger: str, force_push: bool,
                       operation_context_id: int | None) -> None:
    with _pipeline_slots:          # runs over the concurrency limit queue here first
        try:
            run_pipeline_for_account(account_id, trigger, force_push, operation_context_id)
        except Exception:  # noqa: BLE001
            # Nobody catches exceptions in the thread; if we don't log them they vanish entirely
            log.exception("[sentiment] pipeline crashed: account=%s trigger=%s",
                          account_id, trigger)


def submit_pipeline(account_id: int, trigger: str = "manual",
                    force_push: bool = False,
                    operation_context_id: int | None = None) -> None:
    """Hand one batch run to the batch run threads and return immediately.

    The caller is FastAPI's BackgroundTasks -- it only handles "start a thread" (microseconds);
    the real tens of minutes run in batch run threads, not in anyio's 40-thread pool.

    ★ Must run after `db.commit()`: the pipeline opens a new session and reads that row by account_id;
      uncommitted it can't see it and runs an empty round with "account not found". BackgroundTasks naturally guarantees
      this order (it runs after the response is sent); don't change it to start a thread directly in the endpoint.
    """
    threading.Thread(
        target=_run_pipeline_slot,
        args=(account_id, trigger, force_push, operation_context_id),
        name=f"sentiment-pipeline-{account_id}",
        daemon=True,
    ).start()


def _load_knowledge(db: Session, account_id: int) -> dict[str, str]:
    rows = db.query(SentimentKnowledgeORM).filter_by(account_id=account_id).all()
    return {r.key: r.body for r in rows if r.body}


def _mark_running(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM):
    acc.last_run_status = "running"
    log_row.status = "running"
    db.commit()


# One batch run's timeout budget is monitor 1800 + analyze 1800 + brief 1200 seconds; if it hasn't wrapped up by then
# consider it dead. Defined here rather than in sentiment_scheduler: the re-entry guard criterion and zombie cleanup criterion must
# be the same one; writing it in two places will drift sooner or later (scheduler imports this constant; pipeline can't import
# scheduler -- that side imports this module's run_pipeline_for_account).
ZOMBIE_TIMEOUT_MIN = int(os.environ.get("SENTINEL_ZOMBIE_TIMEOUT_MIN", "60"))


def zombie_started_at(db: Session, account_id: int) -> Optional[datetime]:
    """When the account status is stuck at running, decide whether that round is already dead.

    Returns that round's start time = dead (can take over / reset); returns None = someone really is still running.

    The criterion uses **the run log's started_at**, not acc.last_run_at -- the latter records "last finished"
    and `_mark_running` never writes it. So an account that has never succeeded has last_run_at
    NULL, `last_run_at < cutoff` is always NULL in SQL, the row is never selected, and zombie cleanup **always misses it**;
    yet that's exactly the kind most likely to get stuck (crashing on the first round).

    Having running status without any running log -- the state machine broke halfway -- also counts as dead:
    there's no evidence anyone is really running, so the account shouldn't be locked forever.
    """
    row = (db.query(SentimentRunLogORM)
             .filter(SentimentRunLogORM.account_id == account_id,
                     SentimentRunLogORM.status == "running")
             .order_by(SentimentRunLogORM.started_at.desc())
             .first())
    if row is None or row.started_at is None:
        return datetime.utcnow()
    cutoff = datetime.utcnow() - timedelta(minutes=ZOMBIE_TIMEOUT_MIN)
    return row.started_at if row.started_at < cutoff else None


def _fail_stale_runs(db: Session, account_id: int) -> None:
    """Close out all run logs for this account still hanging at running as failed.

    Without closing out, next round zombie_started_at would still pick this old log, and `sentiment_run_logs`
    would keep a never-ending record lying around (the single most misleading thing when troubleshooting).
    """
    now = datetime.utcnow()
    rows = (db.query(SentimentRunLogORM)
              .filter(SentimentRunLogORM.account_id == account_id,
                      SentimentRunLogORM.status == "running")
              .all())
    for r in rows:
        r.status = "failed"
        r.error = "task interrupted (stale run taken over)"
        r.ended_at = now
        if r.started_at:
            r.duration_s = int((now - r.started_at).total_seconds())
    if rows:
        db.commit()


def _billable_user_id(db: Session, acc: SentimentAccountORM):
    """Who this monitoring account's charges go to. Ops' own monitoring accounts return None (no charge)."""
    from geo.models.user import UserORM

    if not acc.user_id:
        return None
    user = db.get(UserORM, int(acc.user_id))
    if user is None or (getattr(user, "role", "") or "user") in ("admin", "operator"):
        return None
    return int(acc.user_id)


def balance_service_get_daily_fee(db: Session) -> int:
    """Price per batch run (cents). Extracted only to pin the amount in the prompt message and the money actually charged in one place."""
    from geo.models.balance import PRICE_SENTIMENT_DAILY
    from geo.services import balance_service

    return balance_service.get_price(db, PRICE_SENTIMENT_DAILY)


def _can_afford_one_run(db: Session, acc: SentimentAccountORM) -> bool:
    from geo.models.balance import PRICE_SENTIMENT_DAILY
    from geo.services import balance_service

    fee = balance_service.get_price(db, PRICE_SENTIMENT_DAILY)
    if fee <= 0:
        return True
    uid = _billable_user_id(db, acc)
    if uid is None:
        return True
    # Already charged today, so no need to check balance -- the idempotency key makes this round free
    if _charged_today(db, acc):
        return True
    return balance_service.can_afford(db, uid, fee)


def _charged_today(db: Session, acc: SentimentAccountORM) -> bool:
    from geo.models.balance import BalanceLedgerORM
    from geo.services import balance_service

    day = balance_service._bj_date()
    # Accept both keys: daily monitoring used the `sentonce:` prefix before the 2026-08 price split; accounts already charged
    # under the old key on the switch day must not be charged a second time under the new key. Once those days pass only sentdaily can remain.
    keys = (f"sentdaily:{acc.id}:{day}", f"sentonce:{acc.id}:{day}")
    return db.query(BalanceLedgerORM.id).filter(
        BalanceLedgerORM.idem_key.in_(keys)).first() is not None


def _charge_one_run(db: Session, acc: SentimentAccountORM) -> None:
    """Charge once per batch run.

    The idempotency key pins "account x Beijing day" -- manual reruns, cron catch-ups and post-zombie-reset retries on the same day
    all hit the same key, so it's charged once a day. Price uses `sentiment_daily` (5 CNY/day), **not the quick-check tier**
    (10 CNY/run) -- charging the quick-check price daily would be 300 CNY per brand per month, more than the most expensive tier. This matters more than "charge per call": sentiment cron runs
    hourly, so per-run charging would bill daily users 24 times over.

    **A failed charge doesn't disable the account**; this round just doesn't proceed (the caller decides by the return value).
    """
    from geo.models.balance import KIND_SENTIMENT_DAILY, PRICE_SENTIMENT_DAILY
    from geo.services import balance_service

    fee = balance_service.get_price(db, PRICE_SENTIMENT_DAILY)
    uid = _billable_user_id(db, acc)
    if fee <= 0 or uid is None:
        return None
    if _charged_today(db, acc):          # already charged under the old key (price-split day); don't charge under the new key
        return None
    day = balance_service._bj_date()
    res = balance_service.charge(
        db, user_id=uid, kind=KIND_SENTIMENT_DAILY, amount_cents=fee,
        idem_key=f"sentdaily:{acc.id}:{day}",
        ref_type="sentiment_acc", ref_id=str(acc.id),
        note={"brand": acc.target or acc.ticker or "", "bj_date": day},
    )
    db.commit()
    return res


def _mark_success(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM, stats: dict):
    # The real charge is here -- charge only on success. A failed round shouldn't be billed to the user.
    _charge_one_run(db, acc)
    now = datetime.utcnow()
    stats_json = json.dumps(stats, ensure_ascii=False, default=str)
    acc.last_run_at = now
    acc.last_run_status = "success"
    acc.last_run_error = None
    acc.last_run_stats = stats_json
    log_row.ended_at = now
    log_row.status = "success"
    log_row.stats_json = stats_json
    if log_row.started_at:
        log_row.duration_s = int((now - log_row.started_at).total_seconds())
    db.commit()


def _mark_failed(db: Session, acc: SentimentAccountORM, log_row: SentimentRunLogORM, err: str):
    now = datetime.utcnow()
    truncated = err[:500] if err else ""
    acc.last_run_status = "failed"
    acc.last_run_error = truncated
    log_row.ended_at = now
    log_row.status = "failed"
    log_row.error = truncated
    if log_row.started_at:
        log_row.duration_s = int((now - log_row.started_at).total_seconds())
    db.commit()


def run_pipeline_for_account(account_id: int, trigger: str = "manual",
                             force_push: bool = False,
                             operation_context_id: int | None = None) -> dict:
    """Synchronously run monitor -> analyze -> brief.

    Called in BackgroundTasks or an APScheduler thread; the state machine is handled internally.
    Returns {status, stats}.
    """
    db: Session = SessionLocal()
    try:
        acc = db.get(SentimentAccountORM, account_id)
        if not acc:
            log.warning("run_pipeline: account %s not found", account_id)
            return {"status": "failed", "error": "account not found"}

        if not acc.active:
            log.info("run_pipeline: account %s is inactive, skip", account_id)
            return {"status": "skipped", "reason": "inactive"}

        if operation_context_id is not None:
            from geo.models.organization import OperationContextORM
            from geo.services.access_control import revalidate_operation_context
            from geo.services.system_audit import append_event
            operation = db.get(OperationContextORM, operation_context_id)
            try:
                revalidate_operation_context(db, operation_context_id)
            except Exception as exc:  # an authorization denial must not reach any external crawl/model call
                cancelled = SentimentRunLogORM(
                    account_id=account_id,
                    operation_context_id=operation_context_id,
                    trigger=trigger,
                    started_at=datetime.utcnow(),
                    ended_at=datetime.utcnow(),
                    status="cancelled",
                    error="authorization revoked",
                )
                db.add(cancelled)
                acc.last_run_status = "cancelled"
                acc.last_run_error = "authorization revoked"
                append_event(
                    db, operation_context=operation,
                    event_type="authorization_denied",
                    action="sentiment.run", object_type="sentiment_account",
                    object_id=account_id, result="denied", reason=str(exc),
                )
                db.commit()
                return {"status": "cancelled", "reason": "authorization_revoked"}

        # Re-entry guard -- skip if another pipeline is running.
        # **But running can't unconditionally mean "someone really is running"**: batch runs use daemon threads,
        # and a release restart / OOM / docker stop makes them vanish on the spot without the state machine writing failed, so the account
        # stays at running forever. Afterwards every entry is blocked here: cron skips, the save-side dedupe doesn't queue,
        # even the /run-now "escape hatch" returns skipped at this line -- while the frontend shows everything normal,
        # and briefs silently stop updating (it happened once in 2026-07, rescued by cleanup_zombies as fallback,
        # but that fallback only runs inside APScheduler, and CN production has no leader process, so effectively none).
        # So the criterion becomes "did the last round time out"; if so take over on the spot instead of skipping forever.
        if acc.last_run_status == "running":
            started = zombie_started_at(db, account_id)
            if started is None:
                log.info("run_pipeline: account %s already running, skip duplicate", account_id)
                return {"status": "skipped", "reason": "already running"}
            log.warning("run_pipeline: account %s previous round (started %s) not wrapped up after %d minutes, "
                        "judged dead, taking over this round", account_id, started, ZOMBIE_TIMEOUT_MIN)
            _fail_stale_runs(db, account_id)
            # Set status back to failed first: after takeover we may still return early below due to insufficient balance,
            # and then the account shouldn't keep showing "running" -- the page would show it running forever while nobody is.
            # If we do proceed, _mark_running a few lines below sets it back to running.
            acc.last_run_status = "failed"
            acc.last_run_error = "task interrupted (stale run taken over)"
            db.commit()

        # Billing pre-check: **check sufficiency first, charge for real at wrap-up** (see _mark_success).
        # The order can't be reversed -- charging before running means money is gone if the round crashes.
        # Insufficient balance skips this round but **doesn't disable the account**; after top-up the next round resumes automatically.
        if not _can_afford_one_run(db, acc):
            # **Must leave a trace**. This return happens before log_row is created, and the skip used to be completely silent:
            # not a single record in sentiment_run_logs, and the account page still showed the last success time and
            # "success". So "balance ran out on 8/30 and every round turns back here since" was invisible on the page and
            # in the logs -- users only saw "why did pushes suddenly stop", while everything looked normal.
            # Write a skipped run log + put the reason in last_run_error to make it visible.
            # last_run_status is deliberately untouched: the frontend RunStatus union type only accepts
            # pending/running/success/failed; a new value would break those chip checks.
            fee = balance_service_get_daily_fee(db)
            reason = f"余额不足,本轮跳过(每轮 {fee / 100:.2f} 元)"
            log.info("run_pipeline: account %s %s", account_id, reason)
            now = datetime.utcnow()
            db.add(SentimentRunLogORM(
                account_id=account_id, operation_context_id=operation_context_id,
                trigger=trigger, started_at=now, ended_at=now, duration_s=0,
                status="skipped", error=reason,
            ))
            acc.last_run_error = reason
            db.commit()
            return {"status": "skipped", "reason": "insufficient_balance"}

        log_row = SentimentRunLogORM(
            account_id=account_id, operation_context_id=operation_context_id, trigger=trigger,
            started_at=datetime.utcnow(), status="running",
        )
        db.add(log_row)
        _mark_running(db, acc, log_row)

        # Default to the old sentinel pipeline -- the Yuanbao WebSearch quick-check branch produces no brief and doesn't push to Feishu/WeCom/email;
        # after it was made the default on 2026-08-05 briefs silently stopped (run status still success, nothing looked wrong).
        # To use quick-check mode you must explicitly set SENTIMENT_PIPELINE_MODE=yuanbao_websearch and accept "no push".
        mode = os.environ.get("SENTIMENT_PIPELINE_MODE", "sentinel").strip().lower()
        if mode not in ("sentinel", "yuanbao_websearch", "yuanbao", "websearch"):
            log.warning("run_pipeline[%s]: unknown SENTIMENT_PIPELINE_MODE=%s, use sentinel",
                        account_id, mode)
            mode = "sentinel"

        # ── Before the long flow: copy every needed field into plain values, then commit to release the connection ──────
        # sessionmaker defaults to expire_on_commit=True. After _mark_running commits, the first read of
        # acc.xxx re-SELECTs -- **the transaction opened by that read is held until wrap-up**
        # in _mark_success/_mark_failed. That means this PG connection sits idle in transaction for the whole window of monitor + all crawlers + analyze
        # + brief (timeout budget 1800+1800+1200 seconds):
        #   - it occupies a slot in DB_POOL_SIZE (default 5 + overflow 10); a few concurrent rounds starve online requests;
        #   - appdb is shared with the test host; a long transaction stalls autovacuum and pins the xmin horizon in place.
        # Commit right after copying. **The long flow must not touch acc / log_row / db again** -- everything needed is in the
        # local variables below; at wrap-up _mark_success reloads those two rows (short transaction).
        user_id = acc.user_id
        target = acc.target
        intent = acc.intent
        # Real stock ticker (may be empty), only fed to the crawlers that "fetch by ticker"
        real_ticker = (acc.ticker or "").strip()
        # symbol for persistence/lookup -- stock ticker is optional; if empty it's the brand name.
        # sentinel `/run-monitor` internally also uses `ticker or target`; both sides must use the same metric definition.
        symbol = account_symbol(acc)
        aliases = json.loads(acc.aliases_json or "[]")
        excludes = json.loads(acc.excludes_json or "[]")
        media_allowlist = json.loads(acc.media_allowlist_json or "[]")
        weixin_album_urls = json.loads(acc.weixin_album_urls_json or "[]")
        newsnow_sources = json.loads(acc.newsnow_sources_json or "[]")
        notify_emails = json.loads(acc.notify_emails_json or "[]")
        groups_raw = json.loads(acc.keyword_groups_json or "[]")
        flat_keywords = json.loads(acc.keywords_json or "[]")
        knowledge = _load_knowledge(db, account_id)
        # The Yuanbao quick-check path also runs minutes of HTTP + LLM; don't carry ORM rows into it:
        # pass a session-detached snapshot (the full_diagnosis side already passes this duck type).
        snap = SimpleNamespace(
            id=account_id, user_id=user_id, target=target, ticker=acc.ticker,
            intent=intent,
            aliases_json=acc.aliases_json or "[]",
            keywords_json=acc.keywords_json or "[]",
            keyword_groups_json=acc.keyword_groups_json or "[]",
            excludes_json=acc.excludes_json or "[]",
        )
        db.commit()          # <- transaction ends here, connection returned to the pool; everything below is outbound calls

        if mode != "sentinel":
            try:
                from geo.services.sentiment_yuanbao_websearch import run_yuanbao_websearch_sentiment

                log.info("run_pipeline[%s]: yuanbao websearch begin", account_id)
                websearch = run_yuanbao_websearch_sentiment(snap, knowledge=knowledge)
                meta = websearch.get("_meta") if isinstance(websearch.get("_meta"), dict) else {}
                websearch_public = {k: v for k, v in websearch.items() if k != "_meta"}
                saved = sentinel_client.save_websearch_report(
                    account_id=account_id,
                    report={**websearch_public, "_meta": meta},
                )
                report_id = saved.get("report_id") or saved.get("id")
                stats = {
                    "mode": "yuanbao_websearch",
                    "websearch_report_id": report_id,
                    "websearch": websearch_public,
                    "telemetry": meta.get("telemetry") or {
                        "engine": "yuanbao",
                        "query_count": websearch_public.get("query_count", 0),
                        "valid_results": websearch_public.get("valid_results", 0),
                    },
                    "llm": meta.get("llm") or {"status": "unknown"},
                }
                _mark_success(db, acc, log_row, stats)
                log.info("run_pipeline[%s]: yuanbao websearch done", account_id)
                return {"status": "success", "stats": stats}
            except Exception as e:  # noqa: BLE001
                log.exception("run_pipeline[%s]: yuanbao websearch failed: %s", account_id, e)
                _mark_failed(db, acc, log_row, str(e))
                return {"status": "failed", "error": str(e), "category": "yuanbao_websearch"}

        # Prefer keywords flattened from structured groups; fall back to flat fields only if empty.
        # The competitor group (kind=competitor) is split out separately: its words are **entity words** (merged into aliases, searched and allowed like the subject),
        # and must not mix into ordinary keywords (that would become "subject AND competitor", only finding comparison articles). The brief side then classifies by subject/competitor.
        competitors: list[str] = []
        if groups_raw:
            groups = [KeywordGroup.model_validate(g) for g in groups_raw]
            comp_groups = [g for g in groups if g.kind == "competitor"]
            reg_groups = [g for g in groups if g.kind != "competitor"]
            keywords = flatten_keyword_groups(reg_groups)
            competitors = flatten_keyword_groups(comp_groups)
        else:
            keywords = flat_keywords
        # Competitor words merged into the entity set: recall covers "(subject OR competitor...)(earnings OR privatization OR compute...)" across all four dimensions
        monitor_aliases = aliases + [c for c in competitors if c not in aliases]

        stats: dict = {}
        try:
            log.info("run_pipeline[%s]: monitor begin", account_id)
            r1 = sentinel_client.run_monitor(
                account_id=account_id,
                target=target, ticker=real_ticker or None,
                intent=intent, aliases=monitor_aliases,
                keywords=keywords, excludes=excludes,
                media_allowlist=media_allowlist,
            )
            stats["monitor"] = r1

            # Direct crawlers -- all run *in parallel*, greatly shortening total pipeline time.
            # Each crawler has its own try/except; a failure doesn't affect others.
            log.info("run_pipeline[%s]: crawlers begin (parallel)", account_id)
            ticker = real_ticker

            # Each entry = (name, callable) -- callable takes no args; lambda captures args
            # newsnow uses aliases + keywords + target together as the title filter
            newsnow_filter_kw = list({target, *aliases, *keywords})

            crawler_tasks: list[tuple[str, callable]] = [
                ("eastmoney_news",
                 lambda: sentinel_client.crawl_eastmoney_news(account_id=account_id, keyword=target, pages=5)),
                # Legacy crawlers -- most broken by WAF/API changes, kept anyway; failures are caught and don't affect others
                ("sina",
                 lambda: sentinel_client.crawl_sina(account_id=account_id, keyword=target, pages=3)),
                ("tieba",
                 lambda: sentinel_client.crawl_tieba(account_id=account_id, keyword=target, pages=3)),
                ("cls",
                 lambda: sentinel_client.crawl_cls(account_id=account_id, keyword=target, pages=3)),
                ("gelonghui",
                 lambda: sentinel_client.crawl_gelonghui(account_id=account_id, keyword=target, pages=3)),
                ("wallstreetcn",
                 lambda: sentinel_client.crawl_wallstreetcn(account_id=account_id, keyword=target, pages=3)),
                ("yicai",
                 lambda: sentinel_client.crawl_yicai(account_id=account_id, keyword=target, pages=3)),
                ("36kr",
                 lambda: sentinel_client.crawl_36kr(account_id=account_id, keyword=target, pages=3)),
            ]
            # The group that fetches by stock ticker -- skip the whole group if no ticker (non-listed entity).
            # Using the brand name as a ticker against Eastmoney/Sina/Xueqiu only returns a pile of 404s/empty results,
            # wastes a round of timeouts, and fills crawler stats with errors.
            if ticker:
                crawler_tasks[:0] = [
                    ("eastmoney",
                     lambda: sentinel_client.crawl_eastmoney(account_id=account_id, symbol=ticker, pages=10)),
                    # Added 2026-05-08: 3 EastMoney endpoints (all use ticker)
                    ("eastmoney_ann",
                     lambda: sentinel_client.crawl_eastmoney_ann(account_id=account_id, ticker=ticker, pages=3)),
                    ("eastmoney_research",
                     lambda: sentinel_client.crawl_eastmoney_research(account_id=account_id, ticker=ticker, pages=2)),
                    ("eastmoney_industry",
                     lambda: sentinel_client.crawl_eastmoney_industry(account_id=account_id, ticker=ticker, pages=2)),
                    ("sina_stock",
                     lambda: sentinel_client.crawl_sina_stock(account_id=account_id, ticker=ticker, pages=1)),
                    ("xueqiu",
                     lambda: sentinel_client.crawl_xueqiu(account_id=account_id, symbol=ticker, pages=5)),
                ]

            # Added only when the account config is non-empty, to avoid hitting sentinel-service every run just to get skipped
            if weixin_album_urls:
                crawler_tasks.append((
                    "weixin_album",
                    lambda: sentinel_client.crawl_weixin_album(
                        account_id=account_id,
                        album_urls=weixin_album_urls,
                        hydrate_body=True,
                        max_articles_per_album=200,
                    ),
                ))
            if newsnow_sources:
                crawler_tasks.append((
                    "newsnow",
                    lambda: sentinel_client.crawl_newsnow(
                        account_id=account_id,
                        source_ids=newsnow_sources,
                        keywords=newsnow_filter_kw,
                        symbol=symbol,
                    ),
                ))
            crawlers_stats: dict = {}
            from concurrent.futures import ThreadPoolExecutor, as_completed
            # 13 crawlers; concurrency 8 is a reasonable cap (avoid hitting sentinel with too many HTTP requests at once,
            # and avoid sentinel-service hammering upstream sites into triggering risk control overall)
            with ThreadPoolExecutor(max_workers=8) as pool:
                future_to_name = {pool.submit(fn): name for name, fn in crawler_tasks}
                for fut in as_completed(future_to_name):
                    name = future_to_name[fut]
                    try:
                        r = fut.result()
                        crawlers_stats[name] = r
                        log.info("run_pipeline[%s]: %s +%s",
                                 account_id, name, r.get("inserted", 0))
                    except Exception as e:
                        log.warning("run_pipeline[%s]: %s failed: %s", account_id, name, e)
                        crawlers_stats[name] = {"error": str(e)}

            stats["crawlers"] = crawlers_stats

            log.info("run_pipeline[%s]: analyze begin", account_id)
            r2 = sentinel_client.run_analyze(account_id=account_id, ticker=symbol)
            stats["analyze"] = r2

            log.info("run_pipeline[%s]: brief begin", account_id)
            r3 = sentinel_client.run_brief(account_id=account_id, ticker=symbol,
                                           competitors=competitors)
            stats["brief"] = {
                "date": r3.get("date"),
                "path": r3.get("path"),
            }

            # Email delivery (when notify_emails is non-empty)
            if notify_emails:
                _push_brief_email(target, r3.get("body", ""), notify_emails)

            # Feishu / WeCom push: if the account has IM connected (bound conversation), push today's brief there
            # Note: IM connectors are bound by user_id (same as alerts._push_im), not SentimentAccount.id
            # Safeguard: push results are recorded in stats["push"] (written to the run log); failures are visible in sentiment_run_logs
            # The long flow ends here; using db again is safe (everything below is short transactions)
            stats["push"] = _push_brief_im(db, user_id, target, r3.get("body", ""),
                                           account_id=account_id, force=force_push)

            _mark_success(db, acc, log_row, stats)
            log.info("run_pipeline[%s]: done", account_id)
            return {"status": "success", "stats": stats}
        except SentinelError as e:
            log.warning("run_pipeline[%s]: sentinel failed: %s (%s)", account_id, e, e.category)
            _mark_failed(db, acc, log_row, str(e))
            return {"status": "failed", "error": str(e), "category": e.category}
        except Exception as e:
            log.exception("run_pipeline[%s]: crashed: %s", account_id, e)
            _mark_failed(db, acc, log_row, str(e))
            return {"status": "failed", "error": str(e), "category": "system"}
    finally:
        db.close()


def _pushed_today(db, account_id: int) -> bool:
    """Whether a brief was successfully pushed today (Beijing time).

    The run log's started_at is naive UTC, while "same day" must be the Beijing time users see,
    so take the Beijing day boundary first and convert back to UTC for comparison. It checks push.ok in stats_json --
    runs that succeeded but failed to push will still push on the next rerun.
    """
    from geo.models.sentiment import SentimentRunLogORM
    bj = timezone(timedelta(hours=8))
    day_start_bj = datetime.now(bj).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_bj.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        rows = (
            db.query(SentimentRunLogORM.stats_json)
              .filter(SentimentRunLogORM.account_id == account_id)
              .filter(SentimentRunLogORM.started_at >= day_start_utc)
              .all()
        )
    except Exception as e:  # noqa: BLE001 —— if the lookup fails treat as not pushed; better a duplicate than a miss
        log.warning("_pushed_today lookup failed, treating as not pushed (account=%s): %s", account_id, e)
        return False
    for (sj,) in rows:
        if not sj:
            continue
        try:
            if (json.loads(sj).get("push") or {}).get("ok") is True:
                return True
        except Exception:  # noqa: BLE001 —— a single bad row shouldn't affect the decision
            continue
    return False


def _push_brief_im(db, user_id: int, target: str, body_md: str,
                   account_id: int | None = None, force: bool = False) -> dict:
    """After the run, push today's brief to the Feishu / WeCom conversations bound to this user (connected people or groups).

    Reuses agent.alerts._push_im. **Safeguard**: returns the push result {"ok","sent","errors"} and writes it to the run log's
    stats["push"]; push failures (including import/syntax errors) log an explicit ERROR -- no longer silent; "no push today" is visible in run_logs at a glance.
    best-effort: push failure doesn't affect the pipeline's success status.
    """
    if not body_md:
        return {"ok": False, "reason": "empty_brief"}
    # Push only once per day: a scheduled run followed by a manual rerun (troubleshooting / verification) would send two copies to the group
    # (actual case on 2026-08-25). The criterion is "**successfully pushed** today", not "ran today" --
    # a morning failure followed by an afternoon rerun still pushes; only duplicate pushes are blocked. force=True bypasses this.
    if not force and account_id is not None and _pushed_today(db, account_id):
        log.info("run_pipeline: brief already pushed today, skipping duplicate push (account=%s)", account_id)
        return {"ok": False, "reason": "already_pushed_today"}
    # Switch: when SENTINEL_NO_PUSH is set, only generate the brief, don't push (push manually after human review)
    if os.getenv("SENTINEL_NO_PUSH", "").strip().lower() in ("1", "true", "yes"):
        log.warning("run_pipeline: SENTINEL_NO_PUSH is set, skipping IM push (user=%s)", user_id)
        return {"ok": False, "reason": "skipped_no_push"}
    try:
        import asyncio
        from agents.geo.alerts import _push_im
        date = datetime.utcnow().strftime("%Y-%m-%d")
        text = f"📊 **{target} · 今日舆情简报 {date}**\n\n{body_md}"
        res = asyncio.run(_push_im(db, user_id, text))
        ok = (res.get("sent", 0) > 0)
        if ok:
            log.info("run_pipeline: brief pushed to IM (user=%s, sent=%s, errors=%s)",
                     user_id, res.get("sent"), res.get("errors"))
        else:
            log.error("run_pipeline: brief IM push sent count is 0! (user=%s) errors=%s",
                      user_id, res.get("errors"))
        return {"ok": ok, **res}
    except Exception as e:  # noqa: BLE001 — import/syntax errors etc. must be logged explicitly too, never silent
        log.exception("run_pipeline: IM push crashed (user=%s): %s", user_id, e)
        return {"ok": False, "error": str(e)[:200]}


def _push_brief_email(target: str, body_md: str, recipients: list[str]) -> None:
    """Brief email delivery -- uses GEO's existing Resend channel.
    MVP simple implementation: put the Markdown directly into the email plaintext; P1 can add HTML rendering.
    """
    try:
        from geo.services.email_service import send_email  # type: ignore
    except ImportError:
        log.warning("email_service not available, skipping brief push")
        return

    subject = f"[舆情简报] {target} · {datetime.utcnow().strftime('%Y-%m-%d')}"
    for to in recipients:
        try:
            send_email(to=to, subject=subject, body=body_md)
        except Exception as e:  # noqa: BLE001
            log.warning("brief email to %s failed: %s", to, e)
