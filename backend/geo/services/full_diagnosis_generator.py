"""One-click full diagnosis generation service.

Entry point `schedule_full_diagnosis(report_id)` is triggered by the POST generate endpoint in api/full_diagnosis.py,
running the full orchestration in a daemon thread:

  Wave 1 (parallel x4, about 1-2 minutes):
      base (25-category checkup) / aeo / crawlTest / authority
  Wave 2 (global BoundedSemaphore(2) throttling, heavy engine stage, about 3-6 minutes):
      visibility + entity parallel x2, then competitiveIntel serially
      (competitive and visibility share AI engine keys and browser sessions;
       running in parallel slows both, so it runs after)
  Finish: four-dimension score synthesis (pure rules) -> LLM narrative block (one DeepSeek call, degrades on failure)
      -> status=ready persisted.

Fault tolerance: each sub-item independently {status: ok|failed|skipped}; RuntimeError (missing API key)
is marked skipped; only when neither base nor visibility has data is the whole thing failed.
LLM failure does not block ready -- narrative_json stays empty, and the frontend shows narrative placeholders.

Zombie fallback: daemon threads get killed by backend restarts, leaving status stuck in generating.
STALE_THRESHOLD=25 minutes (total typically 6-10 minutes; solution's 10-minute threshold
would kill this task wrongly); startup runs reset_stale_full_diagnosis(), and the GET polling endpoint
also checks lazily.

ENV (same as solution_generator):
    DEEPSEEK_API_KEY      optional, preferred -> direct
    OPENROUTER_API_KEY    optional, fallback -> call DeepSeek via OpenRouter
    GEO_SOLUTION_TIMEOUT  optional, per-request LLM timeout (seconds), default 180
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import requests

from geo.database import SessionLocal
from geo.models.full_diagnosis import FullDiagnosisReportORM
from geo.utils.full_diagnosis_log import full_diag_log_event
from geo.services.full_diagnosis_queries import (build_manual_plan, build_query_plan,
                                                  plan_to_subresult)
from geo.services.llm_gateway import post_and_record
from geo.services.llm_usage import submit, usage_ctx
import uuid

log = logging.getLogger(__name__)

STALE_THRESHOLD = timedelta(minutes=25)

# Absolute cap: counted from created_at; exceeding it means dead, **regardless of updated_at**.
#
# Why this separate rule: the 60s heartbeat below unconditionally refreshes updated_at (its intent is to keep queue waiting
# from being wrongly killed by STALE_THRESHOLD); the side effect is that as long as the thread is alive, even if stuck on a call that never returns,
# updated_at is always fresh -> the 25-minute zombie fallback **can never fire**, and the report stays generating forever.
# 2026-08-25 report 95 was exactly this: queued batch run center tasks never reached a terminal state due to account pool lockup, run-now waited 90
# minutes while the heartbeat kept refreshing, the page showed only a spinner; reports 88-92 hung for 11 hours, cleared only by a backend restart.
# 90 minutes is chosen: the worst case for the heavy engine stage (queue wait up to TELEMETRY_JOB_WAIT_SEC=5400s) is within this line,
# while normal total time is 8-20 minutes, so no false kills.
HARD_DEADLINE = timedelta(minutes=int(os.environ.get("GEO_DIAG_HARD_DEADLINE_MIN", "90")))

# Fixed order of the 8 sub-items -- progress.steps renders in this order
SUB_MODES = ["base", "aeo", "crawlTest", "authority", "visibility", "entity",
             "competitiveIntel", "sentiment"]

# Sub-item -> user-facing wording (failure copy must not contain internal key names like base/visibility)
STEP_LABELS = {
    "base": "官网基础检测", "aeo": "问答优化检测", "crawlTest": "AI 爬虫可达性",
    "authority": "站外权威信号", "visibility": "跨引擎可见性", "entity": "品牌实体识别",
    "competitiveIntel": "竞品对比", "sentiment": "口碑与舆情",
}

# The four heavy engine items -- they queue on the batch run center to hit real engines, the sole source of slowness and hangs
HEAVY_MODES = ["visibility", "entity", "competitiveIntel", "sentiment"]


def _charge_full_diagnosis(report_id: int, user_id: Optional[int], *,
                           query_count: int) -> str:
    """Usage-based charging for one-click diagnosis. Returns empty string = allowed, non-empty = block reason (insufficient credits).

    **Metric definition**: cells = actual term count x engine count, unit price `batch_cell`. Engine count comes from
    `full_diagnosis_competitive._engines()` -- the same source as the set actually called,
    so disabling an engine in the environment (GEO_EXCLUDE_ENGINES) also reduces the charge.

    Idempotency key pins report_id: retries and resumed runs of the same report are charged only once.
    Operator accounts are not charged (decided by `topic_billing.user_payer_id`).

    A failed charge **does not fail the whole report** -- the site-side items have already run and are worth keeping for the user;
    the caller uses this to mark the whole heavy engine stage as skipped.
    """
    from geo.database import SessionLocal
    from geo.models.balance import KIND_FULL_DIAGNOSIS, PRICE_BATCH_CELL
    from geo.services import balance_service, topic_billing
    from geo.services.full_diagnosis_competitive import _engines

    if query_count <= 0:
        return ""
    db = SessionLocal()
    try:
        uid = topic_billing.user_payer_id(db, user_id)
        if uid is None or not topic_billing.enabled():
            return ""
        engines = max(1, len(_engines()))
        cells = int(query_count) * engines
        fee = cells * balance_service.get_price(db, PRICE_BATCH_CELL)
        if fee <= 0:
            return ""
        # kind is separately full_diagnosis: the unit price shares batch_cell, but **the line item must not be shared**.
        # Recorded as batch, that "monitoring batch run" ledger entry could never be matched to a batch run record in task monitoring.
        res = balance_service.charge(
            db, user_id=uid, kind=KIND_FULL_DIAGNOSIS, amount_cents=fee,
            idem_key=f"fulldiag:{report_id}",
            ref_type="full_diagnosis", ref_id=str(report_id),
            note={"queries": int(query_count), "engines": engines, "cells": cells,
                  "via": "full_diagnosis"},
        )
        if res.status == balance_service.INSUFFICIENT:
            db.commit()
            return (f"积分不足:本次诊断需要 {fee} 积分"
                    f"({query_count} 词 × {engines} 引擎),当前积分 {res.balance_cents}")
        db.commit()
        return ""
    finally:
        db.close()


def _refund_full_diagnosis(report_id: int) -> None:
    """Paid, but the heavy engine stage produced no visibility data -> refund the full amount.

    Amount and payee **come only from the original charge entry** (idem_key=`fulldiag:{report_id}`), never recomputed --
    refunding too much, too little or to the wrong person is real money. With no such charge entry (operator account, lite tier, insufficient credits
    so never charged) do nothing. The refund itself also pins an idempotency key (`fulldiag-refund:{report_id}`),
    so resumed runs / fallback reruns refund only once.
    """
    from geo.database import SessionLocal
    from geo.models.balance import (
        BalanceLedgerORM, DIRECTION_DEBIT, KIND_FULL_DIAGNOSIS, KIND_REFUND,
    )
    from geo.services import balance_service

    db = SessionLocal()
    try:
        charge = (
            db.query(BalanceLedgerORM)
              .filter(BalanceLedgerORM.idem_key == f"fulldiag:{report_id}",
                      BalanceLedgerORM.direction == DIRECTION_DEBIT,
                      BalanceLedgerORM.kind == KIND_FULL_DIAGNOSIS)
              .first()
        )
        if charge is None:
            return                              # Never charged, nothing to refund
        amount = int(charge.amount_cents or 0)
        if amount <= 0:
            return
        res = balance_service.credit(
            db, user_id=charge.user_id, kind=KIND_REFUND, amount_cents=amount,
            idem_key=f"fulldiag-refund:{report_id}",
            ref_type="full_diagnosis", ref_id=str(report_id),
            note={"reason": "重引擎段未取得数据,原额退回", "via": "full_diagnosis",
                  "charge_ledger_id": charge.id},
        )
        db.commit()
        if res.status != balance_service.DUPLICATE:
            full_diag_log_event(
                report_id, "diagnosis", "refunded", "ok",
                message="AI 可见性检测未出数据,已退还本次扣费",
                amount_cents=amount, user_id=charge.user_id,
            )
    except Exception as e:  # noqa: BLE001 -- a refund failure should not take down the whole report
        log.exception("full diagnosis refund failed for report %d: %s", report_id, e)
    finally:
        db.close()

# Audit scope tiers. lite only runs the four site-side items: parallel, no queue, no AI engines, results in about 1-2 minutes;
# full runs all eight, 8-20 minutes. The sales-agent lead report uses lite; users' own one-click diagnosis uses full.
SCOPE_MODES = {
    "full": list(SUB_MODES),
    "lite": ["base", "aeo", "crawlTest", "authority"],
}
DEFAULT_SCOPE = "full"

# Unselected sub-items get this status, distinct from skipped (missing key / no website) and failed (actually crashed) --
# the frontend shows "not audited this time" for it, never "audit failed"
NOT_SELECTED = "not_selected"
NOT_SELECTED_REASON = "本次为轻量检测,该项未包含在检测范围内"


def modes_for_scope(scope: str) -> list[str]:
    """Tier -> sub-item list. Unknown tiers are treated as full; better to run more than silently run less."""
    return list(SCOPE_MODES.get((scope or "").strip().lower(), SCOPE_MODES[DEFAULT_SCOPE]))


# skipped reason for the four site-side items when no website is given (the frontend uses this copy to distinguish "no website" from "audit failed")
NO_URL_REASON = "未提供官网地址,站点侧检测已跳过"


# When multiple users diagnose at once, the heavy engine stage (visibility/entity/competitive) runs at most 2 tasks concurrently,
# the rest queue. While queued, updated_at must keep being touched to avoid being wrongly killed by the stale fallback.
_HEAVY_SEM = threading.BoundedSemaphore(2)

# progress/subresults are read-modify-write on the same row; must be serialized when threads within a Wave finish concurrently
_ROW_LOCK = threading.Lock()


# --------------- Entry & fallback ---------------


def schedule_full_diagnosis(report_id: int) -> None:
    """fire-and-forget daemon thread -- same as solution_generator."""
    full_diag_log_event(
        report_id, "scheduler", "scheduled", "running",
        message="一键诊断任务已加入后台线程",
    )
    thread = threading.Thread(target=_run_safe, args=(report_id,), daemon=True)
    thread.start()


def reset_stale_full_diagnosis() -> int:
    """Clean up zombie generating rows. Returns the number of rows reset.

    Two criteria, both required:
      · updated_at stalled beyond STALE_THRESHOLD -- process killed/restarted, the thread has no heartbeat at all;
      · created_at beyond HARD_DEADLINE -- the thread is alive and the heartbeat keeps going, but it is stuck on a call
        that never returns. Looking only at updated_at, this kind can never be cleared (see the HARD_DEADLINE comment).
    """
    now = datetime.utcnow()
    cutoff = now - STALE_THRESHOLD
    hard_cutoff = now - HARD_DEADLINE
    db = SessionLocal()
    try:
        stale = (
            db.query(FullDiagnosisReportORM)
              .filter(FullDiagnosisReportORM.status == "generating")
              .filter((FullDiagnosisReportORM.updated_at < cutoff)
                      | (FullDiagnosisReportORM.created_at < hard_cutoff))
              .all()
        )
        for row in stale:
            row.status = "failed"
            row.error = (
                "诊断超过时限仍未完成,后台任务已被回收。请重新生成。"
                if row.updated_at and row.updated_at >= cutoff
                else "后端在诊断途中重启,后台任务被中断。请重新生成。"
            )
            row.updated_at = datetime.utcnow()
        stale_ids = [r.id for r in stale]
        if stale:
            db.commit()
            log.warning("reset %d stale full-diagnosis rows: %s",
                        len(stale), stale_ids)
        # Zombie rows do not go through _mark_failed, so the refund is done here: charged amounts are refunded in full (uncharged are skipped automatically)
        for rid in stale_ids:
            _refund_full_diagnosis(rid)
        return len(stale)
    finally:
        db.close()


def refund_stuck_full_diagnosis() -> int:
    """Fallback scan: reports already failed, charged, but never refunded get refunded once. Returns the number refunded.

    Real-time refunds have four trigger points (end of heavy engine stage / _mark_failed / startup reset / polling endpoint
    lazily marking failed), but all only take effect **at the moment of failing**. This scan cleans up historical rows that were already
    failed before that moment -- reports interrupted before the refund logic shipped (e.g. restart interruptions), and any that slipped through.

    Criterion: the report has a full_diagnosis charge entry, current status=failed, and no corresponding refund entry.
    Idempotent: actual refunds still go through _refund_full_diagnosis (each pins its refund idempotency key); already refunded are skipped automatically;
    runs on every startup, and once converged to 0 it is an empty scan with no side effects.
    """
    from geo.models.balance import BalanceLedgerORM, DIRECTION_DEBIT, KIND_REFUND

    db = SessionLocal()
    try:
        charged = {r[0] for r in db.query(BalanceLedgerORM.ref_id).filter(
            BalanceLedgerORM.ref_type == "full_diagnosis",
            BalanceLedgerORM.direction == DIRECTION_DEBIT).all() if r[0]}
        if not charged:
            return 0
        refunded = {r[0] for r in db.query(BalanceLedgerORM.ref_id).filter(
            BalanceLedgerORM.ref_type == "full_diagnosis",
            BalanceLedgerORM.kind == KIND_REFUND).all() if r[0]}
        pending = [int(x) for x in (charged - refunded) if str(x).isdigit()]
        if not pending:
            return 0
        # Only refund those currently failed (charges on ready reports are due and must not be refunded)
        targets = [r[0] for r in db.query(FullDiagnosisReportORM.id).filter(
            FullDiagnosisReportORM.id.in_(pending),
            FullDiagnosisReportORM.status == "failed").all()]
    finally:
        db.close()

    for rid in targets:
        _refund_full_diagnosis(rid)
    if targets:
        log.warning("swept & refunded %d stuck full-diagnosis reports: %s",
                    len(targets), targets)
    return len(targets)


def _run_safe(report_id: int) -> None:
    # Accounting context: all model calls in one diagnosis report (narrative layers A/B, competitor extraction...) share one
    # trace_id; the two ThreadPoolExecutor calls carry the context into threads via llm_usage.submit
    with usage_ctx(feature="full_diagnosis", trace_id=uuid.uuid4().hex,
                   ref_type="report", ref_id=str(report_id)):
        try:
            _run(report_id)
        except Exception as e:  # noqa: BLE001
            log.exception("full diagnosis crashed for report %d: %s", report_id, e)
            full_diag_log_event(
                report_id, "system", "crashed", "failed", logging.ERROR,
                message="一键诊断后台任务异常退出", error=str(e)[:500],
            )
            _mark_failed(report_id, str(e)[:1000])


def _mark_failed(report_id: int, error: str) -> None:
    full_diag_log_event(
        report_id, "diagnosis", "failed", "failed", logging.ERROR,
        message="一键诊断失败", error=error[:500],
    )
    db = SessionLocal()
    try:
        row = db.get(FullDiagnosisReportORM, report_id)
        if row is None:
            return
        row.status = "failed"
        row.error = error
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    # Report marked failed -> if charged, refund in full. Idempotent + uncharged skipped automatically, so repeated calls are harmless:
    # if the heavy engine stage already refunded, this hits the refund idempotency key and is a no-op.
    _refund_full_diagnosis(report_id)


# --------------- Progress persistence ---------------


def _touch(report_id: int) -> None:
    """Heartbeat -- prevents false stale kills while queued."""
    db = SessionLocal()
    try:
        row = db.get(FullDiagnosisReportORM, report_id)
        if row is not None and row.status == "generating":
            row.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _update_step(report_id: int, mode: str, status: str,
                 error: Optional[str] = None,
                 data: Optional[dict[str, Any]] = None,
                 stage: Optional[str] = None) -> None:
    """Flip sub-item status + incrementally persist results. Short transaction; _ROW_LOCK serializes read-modify-write."""
    with _ROW_LOCK:
        db = SessionLocal()
        try:
            row = db.get(FullDiagnosisReportORM, report_id)
            if row is None:
                return
            try:
                progress = json.loads(row.progress_json or "{}")
            except Exception:  # noqa: BLE001
                progress = {}
            steps = progress.get("steps") or []
            by_mode = {s.get("mode"): s for s in steps if isinstance(s, dict)}
            if mode in by_mode:
                by_mode[mode]["status"] = status
                if error:
                    by_mode[mode]["error"] = error[:300]
            progress["steps"] = steps
            progress["done"] = sum(1 for s in steps
                                   if s.get("status") in ("ok", "failed", "skipped"))
            progress["total"] = len(steps)
            if stage:
                progress["stage"] = stage
            row.progress_json = json.dumps(progress, ensure_ascii=False)

            if data is not None:
                try:
                    subresults = json.loads(row.subresults_json or "{}")
                except Exception:  # noqa: BLE001
                    subresults = {}
                subresults[mode] = data
                row.subresults_json = json.dumps(subresults, ensure_ascii=False)

            row.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()


# --------------- Sub-item fault-tolerance shell ---------------


def _run_sub(report_id: int, mode: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one sub-audit: ok / skipped (missing key) / failed. Results persisted immediately."""
    started = time.time()
    _update_step(report_id, mode, "running")
    full_diag_log_event(
        report_id, mode, "begin", "running",
        message="子检测开始",
    )
    try:
        data = fn()
        out = {"status": "ok", "data": data}
        _update_step(report_id, mode, "ok", data=out)
        full_diag_log_event(
            report_id, mode, "done", "ok",
            message="子检测完成",
            duration_ms=int((time.time() - started) * 1000),
        )
        return out
    except RuntimeError as e:
        # advanced_runners convention: missing/bad API key raises RuntimeError
        out = {"status": "skipped", "reason": str(e)[:300]}
        _update_step(report_id, mode, "skipped", error=str(e), data=out)
        full_diag_log_event(
            report_id, mode, "skipped", "skipped",
            message="子检测跳过",
            duration_ms=int((time.time() - started) * 1000),
            reason=str(e)[:300],
        )
        log.info("full diagnosis %d: %s skipped: %s", report_id, mode, e)
        return out
    except Exception as e:  # noqa: BLE001
        out = {"status": "failed", "error": str(e)[:300]}
        _update_step(report_id, mode, "failed", error=str(e), data=out)
        full_diag_log_event(
            report_id, mode, "failed", "failed", logging.WARNING,
            message="子检测失败",
            duration_ms=int((time.time() - started) * 1000),
            error=str(e)[:300],
        )
        log.warning("full diagnosis %d: %s failed: %s", report_id, mode, e)
        return out


def _ok(sub: Optional[dict[str, Any]]) -> bool:
    return bool(sub) and sub.get("status") == "ok" and isinstance(sub.get("data"), dict)


def _degraded_probe(report_id: int, brand: str, exc: Exception,
                    step_status: str, log_msg: str) -> dict:
    """Sub-item output when the batch run center is unavailable -- **degraded, not blank**.

    These two excepts used to hardcode `{"status": "skipped"}` / `{"status": "failed"}`,
    leaving those two chapters of the page report blank. The chat version already had a public web search fallback, the page version did not --
    the same outage gave users completely different things through the two entry points.

    ⚠️ **Step status is still written as skipped / failed, not degraded.**
    `_update_step`'s `progress["done"]` only recognizes the three terminal states ok/failed/skipped;
    an extra state would make the report never reach done and get stuck in generating -- this pipeline has already tripped on that once.
    The degradation contract lives in data (data_quality / degraded_notice / degraded_missing);
    the rendering layer recognizes those three fields, not the step status.

    ⚠️ Degraded results **never go into any persistent metrics table**, only into subresults -- once degraded values are treated as
    real data in later report calculations, the pollution is irreversible (mechanism 3 in degrade.py).
    """
    from agents.geo import degrade, websearch

    missing = ["AI 可见性实测", "竞品对位"]
    deg = degrade.Degraded(
        missing=missing,
        reason=f"跑批中心这次没跑成({str(exc)[:120]})",
    )

    evidence: list = []
    if websearch.configured() and brand:
        try:
            # search_many returns {available, evidence, queried, reason} -- without a key
            # or when unreachable, available=False and evidence is an empty list; **it never returns an empty list pretending it searched**
            found = websearch.search_many(
                [brand, f"{brand} 怎么样", f"{brand} 口碑"], cnt=6)
            evidence = list(found.get("evidence") or [])
            if evidence:
                deg.evidence_provider = "tencent"
        except Exception:  # noqa: BLE001 -- if the fallback itself fails, it must not drag the main flow down
            log.warning("[diag] degraded evidence collection failed report=%s", report_id, exc_info=True)

    out = degrade.wrap({
        "status": step_status,
        "reason": str(exc)[:300],
        "evidence": evidence,
        # Negative conclusions hold without real engine probes, but still need an evidence_ref to be kept
        "conclusions": ([{"text": f"公开检索里能找到 {len(evidence)} 条与「{brand}」相关的信息,"
                                  f"但这次没能实测 AI 引擎会不会引用它们。",
                          "evidence_ref": 0}] if evidence else []),
    }, deg)

    for m in ("visibility", "competitiveIntel"):
        _update_step(report_id, m, step_status, error=str(exc), data=out)
        full_diag_log_event(
            report_id, m, step_status, step_status,
            logging.WARNING if step_status == "failed" else logging.INFO,
            message=log_msg,
            reason=str(exc)[:300],
            degraded=1,
            evidence_count=len(evidence),
        )
    return out


def _csv(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        return ",".join(str(v) for v in values if v)
    return str(values or "")


def _rates_csv(rates: Any) -> str:
    if not isinstance(rates, dict):
        return ""
    return ",".join(f"{k}:{v}" for k, v in rates.items())


# --------------- Main orchestration ---------------


def _run(report_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.get(FullDiagnosisReportORM, report_id)
        if row is None:
            log.warning("full diagnosis: report %d not found", report_id)
            return
        url = (row.url or "").strip()
        brand = (row.brand_name or "").strip()
        user_id = row.user_id
        full_diag_log_event(
            report_id, "diagnosis", "begin", "running",
            message="一键诊断开始",
            user_id=user_id,
            brand=brand,
            has_url=bool(url),
        )

        def _json_list(raw: str) -> list[str]:
            try:
                v = json.loads(raw or "[]")
                return [str(x) for x in v] if isinstance(v, list) else []
            except Exception:  # noqa: BLE001
                return []

        competitors = _json_list(row.competitor_domains_json)
        industry_terms = _json_list(row.industry_terms_json)
        brand_terms = _json_list(row.brand_terms_json)
        # Manual term package (finalized consultant plan): non-empty -> the whole planning pipeline is skipped
        try:
            manual_items = json.loads(row.query_pack_json or "[]")
            manual_items = [x for x in manual_items if isinstance(x, dict) and x.get("text")]
        except Exception:  # noqa: BLE001
            manual_items = []
        # Initialize progress: only the sub-items selected by this tier (lite has 4 steps, not 8)
        selected = modes_for_scope(getattr(row, "scope", DEFAULT_SCOPE))
        row.progress_json = json.dumps({
            "stage": "checks", "done": 0, "total": len(selected),
            "steps": [{"mode": m, "status": "pending"} for m in selected],
        }, ensure_ascii=False)
        # Unselected sub-items are persisted immediately as not_selected -- only then does the report know the item was "not audited this time"
        skipped_subresults = {
            m: {"status": NOT_SELECTED, "reason": NOT_SELECTED_REASON}
            for m in SUB_MODES if m not in selected
        }
        if skipped_subresults:
            try:
                existing_subs = json.loads(row.subresults_json or "{}")
            except Exception:  # noqa: BLE001
                existing_subs = {}
            existing_subs.update(skipped_subresults)
            row.subresults_json = json.dumps(existing_subs, ensure_ascii=False)
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    from geo.services import advanced_runners

    subresults: dict[str, Any] = dict(skipped_subresults)
    heavy_selected = [m for m in HEAVY_MODES if m in selected]
    # Prompts with term-type annotations: text goes to the batch run center, annotations stay in this process for metric slicing,
    # not written to the topic DB and not validated by TopicOut, so no dependency on extending the global term-type enum.
    # The term package is planned by the model (template fallback) and takes 20-60s; Wave 1's four site-side items take 30-90s
    # and do not depend on it -- start a future so both run in parallel, join before Wave 2 needs terms, adding no total time.
    # Use llm_usage.submit instead of bare submit: thread pools do not inherit contextvars, and a direct submit
    # would make this model call lose its report_id attribution in accounting.
    # With a manual term package, no thread pool and no model call -- the terms are authoritative input and the planner must not intervene at all
    _plan_pool = None
    plan_future = None
    if not manual_items and heavy_selected:
        _plan_pool = ThreadPoolExecutor(max_workers=1)
        plan_future = submit(
            _plan_pool, build_query_plan,
            brand=brand, industry_terms=industry_terms, brand_terms=brand_terms,
            competitors=competitors, url=url, report_id=report_id,
        )

    # Heartbeat throughout: a single visibility/sentiment step can take minutes without updating timestamps,
    # and without a heartbeat it would be wrongly killed by the 25min zombie fallback (the old competitor channel failed this way on 2026-07-30)
    _hb_stop = threading.Event()

    def _heartbeat() -> None:
        while not _hb_stop.wait(60):
            _touch(report_id)

    threading.Thread(target=_heartbeat, daemon=True).start()
    try:
        full_diag_log_event(
            report_id, "diagnosis", "wave1_begin", "running",
            message="开始站点侧轻量检测",
            skipped=not bool(url),
        )
        # -- Wave 1: lightweight items needing no AI key, parallel x4 --
        def _base_runner() -> dict[str, Any]:
            # Reuse solution_generator's 25-category -> 5-cluster grouping (CLUSTER_DEFS),
            # returns {url, score, grade, *_count, clusters, execution_layers, all_checks}
            from geo.services.solution_generator import _run_diagnosis
            return _run_diagnosis(url)

        wave1: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            (m, fn) for m, fn in (
                ("base", _base_runner),
                ("aeo", lambda: advanced_runners.run_aeo_visibility(url)),
                ("crawlTest", lambda: advanced_runners.run_crawl_test(url)),
                ("authority", lambda: advanced_runners.run_authority_audit(url)),
            ) if m in selected
        ]
        if url:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {m: pool.submit(_run_sub, report_id, m, fn) for m, fn in wave1}
                for m, fut in futures.items():
                    subresults[m] = fut.result()
        else:
            # Website is optional: if missing, skip the site side entirely instead of fetching an empty URL (it would fail and the report
            # would show "audit failed" rather than "no website", which are different things)
            for m, _fn in wave1:
                out = {"status": "skipped", "reason": NO_URL_REASON}
                subresults[m] = out
                _update_step(report_id, m, "skipped", error=NO_URL_REASON, data=out)
                full_diag_log_event(
                    report_id, m, "skipped", "skipped",
                    message="未提供官网,站点侧检测跳过",
                    reason=NO_URL_REASON,
                )

        full_diag_log_event(
            report_id, "diagnosis", "wave1_done", "ok",
            message="站点侧轻量检测阶段结束",
        )

        # lite tier: no heavy engine items selected -> term package planning and Wave 2 are skipped entirely,
        # and the two _HEAVY_SEM slots are not queued for (queueing would give back the time saved)
        if heavy_selected:
            # -- Get the term package: the planning run in parallel during Wave 1 is joined here --
            # build_query_plan already converts all exceptions into template degradation, so the package obtained here is always usable
            if manual_items:
                query_plan = build_manual_plan(
                    manual_items, brand=brand, brand_terms=brand_terms,
                    competitors=competitors,
                )
            else:
                query_plan = plan_future.result()
                _plan_pool.shutdown(wait=False)
            term_items = query_plan.items
            term_queries = [q["text"] for q in term_items]
            scene_map = {q["text"]: q["scene"] for q in term_items}
            # Rewrite provenance: backfilled into query_hits on persist, so the report tells users which sentence this one was rewritten from
            origin_map = {q["text"]: (q.get("origin") or "") for q in term_items
                          if q.get("kind") in ("rewrite", "split")}
            subresults["queryPlan"] = plan_to_subresult(query_plan)
            full_diag_log_event(
                report_id, "diagnosis", "queries_prepared", "ok",
                message="诊断词组装完成",
                industry_terms_count=len(industry_terms),
                brand_terms_count=len(brand_terms),
                competitor_count=len(competitors),
                query_count=len(term_queries),
                source=query_plan.source,
                llm_model=query_plan.model,
                fallback_reason=query_plan.fallback_reason,
            )

            # -- Charge credits by usage -- before the heavy engine stage starts --
            # One-click diagnosis uses the batch run center's run-now preview, which **does not write ai_telemetry_responses**,
            # so batch_billing's daily settler never sees it (see its module docstring).
            # Charge on the spot by "actual term count x engine count", reusing the batch_cell unit price --
            # the same cell should not have two prices just because the entry point differs.
            charge_fail = _charge_full_diagnosis(
                report_id, user_id, query_count=len(term_queries),
            )
            if charge_fail:
                for m in heavy_selected:
                    out = {"status": "skipped", "reason": charge_fail}
                    subresults[m] = out
                    _update_step(report_id, m, "skipped", error=charge_fail, data=out)
                    full_diag_log_event(
                        report_id, m, "skipped", "skipped",
                        message="积分不足,重引擎段跳过", reason=charge_fail,
                    )
                heavy_selected = []

        if heavy_selected:
            # -- Wave 2: heavy engine stage, global throttling (the heartbeat thread already covers queue waiting) --
            full_diag_log_event(
                report_id, "diagnosis", "heavy_wait", "running",
                message="等待重引擎诊断限流槽",
            )
            _HEAVY_SEM.acquire()
            full_diag_log_event(
                report_id, "diagnosis", "heavy_begin", "running",
                message="开始 AI 可见性/实体/竞品/舆情诊断",
            )
            try:
                # Visibility + competitive share one batch run center run-now (the same batch of user-term queries x domestic engines):
                # per-engine mention rates and competitive analysis come from the same batch of answers, no longer using geo_checker's
                # overseas engine set (always 0% for Chinese brands, dragging down the score and misleading the display)
                def _engine_probe() -> dict[str, Any]:
                    from geo.services.full_diagnosis_competitive import run_engine_probe_cached
                    return run_engine_probe_cached(
                        url, brand_name=brand,
                        competitor_domains=competitors or None,
                        queries=term_queries, brand_terms=brand_terms or None,
                        scene_map=scene_map,
                        # Industry term = track; competitor annotation relies on it to block well-known companies from adjacent tracks
                        industry_terms=industry_terms or None,
                        # Pass the caller: the batch run center decides by the call_policies tier whether
                        # to go API direct or browser, run now or queue
                        user_id=user_id,
                    )

                def _probe_and_split() -> None:
                    _update_step(report_id, "visibility", "running")
                    _update_step(report_id, "competitiveIntel", "running")
                    try:
                        data = _engine_probe()
                        # Rewrite provenance and term package source are backfilled in place -- attached in the same process,
                        # so full_diagnosis_competitive's signature need not change
                        vis = data.get("visibility") or {}
                        vis["query_source"] = query_plan.source
                        for row in vis.get("query_hits") or []:
                            origin = origin_map.get(row.get("query") or "")
                            if origin:
                                row["origin"] = origin
                        subresults["visibility"] = {"status": "ok", "data": data["visibility"]}
                        subresults["competitiveIntel"] = {"status": "ok", "data": data["competitive"]}
                        _update_step(report_id, "visibility", "ok", data=subresults["visibility"])
                        _update_step(report_id, "competitiveIntel", "ok",
                                     data=subresults["competitiveIntel"])
                        visibility_data = data.get("visibility") or {}
                        competitive_data = data.get("competitive") or {}
                        full_diag_log_event(
                            report_id, "visibility", "done", "ok",
                            message="AI 可见性检测完成",
                            engines=_csv(visibility_data.get("engines")),
                            query_count=visibility_data.get("query_count"),
                            total_answers=visibility_data.get("total_answers"),
                            mention_count=visibility_data.get("mention_count"),
                            overall_rate=visibility_data.get("overall_rate"),
                            per_engine_rates=_rates_csv(visibility_data.get("per_engine_rates")),
                        )
                        full_diag_log_event(
                            report_id, "competitiveIntel", "done", "ok",
                            message="竞品分析检测完成",
                            engines=_csv(competitive_data.get("engines")),
                            query_count=competitive_data.get("query_count"),
                            total_results=competitive_data.get("total_results"),
                            valid_results=competitive_data.get("valid_results"),
                            elapsed_seconds=competitive_data.get("elapsed_seconds"),
                            competitor_count=len(competitors or []),
                        )
                    except RuntimeError as e:
                        out = _degraded_probe(report_id, brand, e, "skipped",
                                              "跑批中心检测跳过")
                        subresults["visibility"] = out
                        subresults["competitiveIntel"] = out
                    except Exception as e:  # noqa: BLE001
                        out = _degraded_probe(report_id, brand, e, "failed",
                                              "跑批中心检测失败")
                        subresults["visibility"] = out
                        subresults["competitiveIntel"] = out

                # Sentiment monitoring uses a live Yuanbao WebSearch quick check; only backfills this report's subresults, never writes sentinel tenant tables.
                # It does not depend on engine probe results; running it serially after the probe used to waste 2 extra minutes
                # (report #124: probe done at 06:08:55 -> sentiment done at 06:11:09), so all three start together.
                from geo.services.full_diagnosis_sentiment import run_sentiment_cached
                with ThreadPoolExecutor(max_workers=3) as pool:
                    probe_fut = pool.submit(_probe_and_split)
                    entity_fut = pool.submit(
                        _run_sub, report_id, "entity",
                        lambda: advanced_runners.run_entity_audit(brand))
                    # Sentiment monitoring includes one LLM summary; use the contextvar-aware submit to keep accounting attribution
                    sentiment_fut = submit(
                        pool, _run_sub, report_id, "sentiment",
                        lambda: run_sentiment_cached(user_id, brand,
                                                     brand_terms=brand_terms or None,
                                                     report_id=report_id,
                                                     industry_terms=industry_terms or None))
                    probe_fut.result()
                    subresults["entity"] = entity_fut.result()
                    subresults["sentiment"] = sentiment_fut.result()
            finally:
                _HEAVY_SEM.release()
                full_diag_log_event(
                    report_id, "diagnosis", "heavy_done", "ok",
                    message="重引擎诊断阶段结束",
                )

            # -- Paid but visibility produced no data -> full refund --
            # The charge corresponds to the engine batch runs (visibility / competitive). heavy_selected still non-empty
            # means the charge succeeded (it was cleared above on insufficient credits); if the visibility subresult is not ok
            # (batch run center skipped / crashed failed), the user paid and got nothing -> refund.
            # The site-side base may still succeed and the report ships as usual, but whether this batch run fee is refunded depends only on the batch run itself.
            if not _ok(subresults.get("visibility")):
                _refund_full_diagnosis(report_id)

        # -- Overall success/failure: all primary data items have no data -> no primary data in the report, failed --
        # Primary data items = those of base and visibility actually selected by this tier. lite does not run visibility;
        # the old criterion (failed only when both have no data) would mark every lightweight report failed.
        primary = [m for m in ("base", "visibility") if m in selected] or selected
        if not any(_ok(subresults.get(m)) for m in primary):
            base_err = (subresults.get("base") or {}).get("error") or (subresults.get("base") or {}).get("reason") or ""
            names = "、".join(STEP_LABELS.get(m, m) for m in primary)
            _mark_failed(report_id, f"{names}均未取得数据,无法生成报告。{base_err}".strip())
            return

        # -- Four-dimension scoring (pure rules) --
        full_diag_log_event(
            report_id, "diagnosis", "score_begin", "running",
            message="开始计算综合评分",
        )
        # Neutral-positive rate discounted by sentiment risk: the AI side only sees "what the answers say", not negative evidence not yet cited.
        # Placed here because it must wait until both visibility and sentiment are settled (see apply_sentiment_risk).
        try:
            from geo.services.full_diagnosis_metrics import apply_sentiment_risk
            _vd = (subresults.get("visibility") or {}).get("data") or {}
            if _vd.get("metrics"):
                apply_sentiment_risk(_vd["metrics"], subresults.get("sentiment"))
        except Exception as e:  # noqa: BLE001 -- failing to compute the discount must not take down the whole report
            log.warning("neutral-positive rate risk discount not applied: %s", e)

        scores = _compute_scores(subresults)
        full_diag_log_event(
            report_id, "diagnosis", "score_done", "ok",
            message="综合评分计算完成",
            total=scores.get("total"),
            grade=scores.get("grade"),
        )

        # -- LLM narrative block (degrades on failure: narrative stays empty, report still ready) --
        last_mode = selected[-1]
        _update_step(report_id, last_mode,
                     (subresults.get(last_mode) or {}).get("status") or "ok", stage="llm")
        narrative: dict[str, Any] = {}
        llm_model = ""
        llm_error = ""
        full_diag_log_event(
            report_id, "narrative", "llm_begin", "running",
            message="开始生成诊断报告解读",
        )
        try:
            narrative, llm_model = _call_llm_narrative(
                brand, url, scores, subresults,
                industry_terms=industry_terms, brand_terms=brand_terms)
            full_diag_log_event(
                report_id, "narrative", "llm_done", "ok",
                message="诊断报告解读生成完成",
                llm_model=llm_model,
            )
        except Exception as e:  # noqa: BLE001
            llm_error = f"AI 解读生成失败:{str(e)[:300]}"
            full_diag_log_event(
                report_id, "narrative", "llm_failed", "failed", logging.WARNING,
                message="诊断报告解读生成失败,报告仍会落库",
                error=str(e)[:300],
            )
            log.warning("full diagnosis %d: LLM narrative failed: %s", report_id, e)

        # -- Persist ready --
        db = SessionLocal()
        try:
            row = db.get(FullDiagnosisReportORM, report_id)
            if row is None:
                return
            row.status = "ready"
            row.subresults_json = json.dumps(subresults, ensure_ascii=False)
            row.scores_json = json.dumps(scores, ensure_ascii=False)
            row.narrative_json = json.dumps(narrative, ensure_ascii=False)
            row.llm_model = llm_model
            row.error = llm_error or None
            row.updated_at = datetime.utcnow()
            db.commit()
            full_diag_log_event(
                report_id, "diagnosis", "ready", "ok",
                message="一键诊断完成并已落库",
                total=scores.get("total"),
                grade=scores.get("grade"),
                llm_model=llm_model,
            )
            log.info("full diagnosis %d done (total=%s grade=%s)",
                     report_id, scores.get("total"), scores.get("grade"))
        finally:
            db.close()
        # Push after persisting; a push failure does not affect report status (the function swallows exceptions and only logs)
        from geo.services.full_diagnosis_notify import notify_report_done
        notify_report_done(report_id=report_id, user_id=user_id, brand=brand,
                           total=scores.get("total"), grade=scores.get("grade"))
    finally:
        _hb_stop.set()


# --------------- Five-dimension scoring ---------------

# The first four dimensions are the old weights (0.35/0.30/0.20/0.15) **multiplied exactly by 0.88**, not arbitrarily rounded.
# So when sentiment monitoring is missing, weighted/weight_sum redistribution restores the four-dimension ratio to 0.35/0.30/0.20/0.15,
# and the total matches the old four-dimension metric definition -- existing reports remain directly comparable.
# Measured on five thousand random score sets: 99.5% identical digit for digit; residuals only occur where the exact weighted value lands on a .5 rounding boundary
# (difference +/-1 point, due to floating point precision, cannot be eliminated).
# Counterexample on record: rounding to 0.32/0.25/0.18/0.13 breaks the ratio, and nearly half of totals mismatch; do not change it back.
# Sentiment monitoring gets only 0.12: the data source is social media scraping, and among the four skipped triggers "scraping and analysis both empty" is the most common;
# a higher weight would make the total for the same brand jump between two diagnoses.
DIMENSION_DEFS = [
    ("ai_visibility", 0.308),
    ("infrastructure", 0.264),
    ("authority", 0.176),
    ("competitive", 0.132),
    ("sentiment", 0.12),
]

# Single source of dimension keys -- previously dim_names / schema hint / normalization filter each had their own 4-tuple,
# and missing any one when adding a dimension raised no error (even if the LLM wrote it, it was silently filtered). From now on only change DIMENSION_DEFS.
DIMENSION_KEYS = tuple(k for k, _ in DIMENSION_DEFS)

DIMENSION_NAMES_ZH = {
    "ai_visibility": "AI搜索可见度",
    "infrastructure": "基建完善度",
    "authority": "权威背书",
    "competitive": "竞品对比优势",
    "sentiment": "舆情健康度",
}

GRADE_SCALE = [("A", 85), ("B", 70), ("C", 55), ("D", 40), ("F", 0)]

# Keys of the six metrics, consistent with the report center's ai_telemetry_reports._metrics_from_counts
_METRIC_KEYS = ("exposure", "top1", "top3", "accuracy", "citation", "mention")

# Chinese term-type names are taken verbatim from pages/Diagnosis/sceneLabels (frontend display layer); the LLM prompt
# gets the Chinese directly, so the model does not treat English keys like brand/compare as brand names or ordinary words
_SCENE_ZH = {
    "compare": "竞品对比词", "brand": "品牌词", "search": "搜索词",
    "qa": "问答词", "intent": "意图词", "scenario": "场景词", "unknown": "未分类",
}


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _clamp_rank_projection(mp: list[dict]) -> None:
    """Once the three exposure rates share a scale, TOP1 <= TOP3 <= overall exposure rate is an identity, and predictions must satisfy it too.

    The model now and then gives curves with TOP3 above the overall exposure rate; displaying them directly would contradict ourselves,
    so they are pressed back in place here (only rank items are lowered, the overall exposure rate is never raised -- we cannot invent growth for the model).
    """
    by_key = {m["key"]: m for m in mp}
    exp, t3, t1 = by_key.get("exposure"), by_key.get("top3"), by_key.get("top1")
    for field in ("after_30d", "after_90d"):
        if exp and t3:
            t3[field] = min(t3[field], exp[field])
        if t1:
            ceil = (t3 or exp or {}).get(field)
            if ceil is not None:
                t1[field] = min(t1[field], ceil)


def _grade(total: int) -> str:
    for g, floor in GRADE_SCALE:
        if total >= floor:
            return g
    return "F"


# --------------- Sentiment health ---------------

# Prior strength for small-sample shrinkage: when sample count equals K, half the score comes from measurement and half regresses to neutral
_SENTIMENT_SHRINK_K = 10
_SENTIMENT_NEUTRAL = 50.0
# Below this sample size mark estimated; the frontend adds an "estimate" badge
_SENTIMENT_MIN_CONFIDENT = 8

_POS_LABELS = {"bullish", "positive", "正面", "看涨", "利好"}
_NEG_LABELS = {"bearish", "negative", "负面", "看跌", "利空"}


# Weight in the sentiment score for entries outside the "recent" window (or of unknown time)
_HISTORICAL_WEIGHT = 0.5


def _sentiment_window(data: dict[str, Any]) -> int:
    try:
        return int(data.get("window_days") or 30)
    except (TypeError, ValueError):
        return 30


def _stance_of(post: dict[str, Any]) -> str:
    """Positive/neutral/negative judgment of a single discussion -- symmetric thresholds +/-0.2.

    The old metric definition was asymmetric (negative accepted 5 labels + score<-0.2, positive only bullish),
    so computing the positive/negative ratio directly would be systematically negative; hence re-judging here instead of using the existing count fields.
    """
    label = str(post.get("sentiment_label") or "").strip().lower()
    if label in _POS_LABELS:
        return "pos"
    if label in _NEG_LABELS:
        return "neg"
    score = post.get("sentiment_score")
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "neu"
    if s >= 0.2:
        return "pos"
    if s <= -0.2:
        return "neg"
    return "neu"


def _sentiment_score(data: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    """Sentiment health 0-100. Returns (score, detail); returns None for empty samples to enter the degradation chain.

    Formula: net positive rate mapped to 0-100 -> small samples shrunk toward neutral -> points deducted by risk share.
    Note: must not return a neutral score when no discussions were scraped -- 0 scraped does not mean neutral reputation.
    """
    posts = data.get("posts") or []
    if not isinstance(posts, list):
        posts = []
    # Historical events get half weight: a 2015 short-seller report really still affects the brand image in AI answers,
    # but scoring it equally with last week's news would distort the "current reputation" metric.
    pos = neu = neg = 0.0
    historical = 0
    for p in posts:
        if not isinstance(p, dict):
            continue
        w = 1.0 if str(p.get("recency") or "unknown") == "recent" else _HISTORICAL_WEIGHT
        if str(p.get("recency") or "") in ("historical", "unknown"):
            historical += 1
        st = _stance_of(p)
        if st == "pos":
            pos += w
        elif st == "neg":
            neg += w
        else:
            neu += w
    n = pos + neu + neg
    if n == 0:
        return None

    raw = (pos - neg) / n                      # Net positive rate in [-1, 1]
    base = _SENTIMENT_NEUTRAL + 50.0 * raw     # All positive 100 / all neutral 50 / all negative 0
    shrink = n / (n + _SENTIMENT_SHRINK_K)
    ratio_score = shrink * base + (1 - shrink) * _SENTIMENT_NEUTRAL

    # Risk deduction: share of medium/high risk, also constrained by the shrinkage factor (small samples are not penalized heavily)
    risk_dist = data.get("risk_dist") or []
    risky = 0
    if isinstance(risk_dist, list):
        for r in risk_dist:
            if isinstance(r, dict) and str(r.get("level", "")).lower() in ("medium", "high", "critical"):
                try:
                    risky += int(r.get("count") or 0)
                except (TypeError, ValueError):
                    pass
    if risky == 0:
        try:
            risky = int((data.get("kpi") or {}).get("high_risk") or 0)
        except (TypeError, ValueError):
            risky = 0
    # Risk counts are also freshness-weighted (risk_dist only has totals, so convert by the historical share of posts)
    if posts:
        hist_ratio = historical / len([p for p in posts if isinstance(p, dict)])
        risky = risky * (1 - hist_ratio) + risky * hist_ratio * _HISTORICAL_WEIGHT
    risk_rate = min(1.0, risky / n)
    penalty = min(25.0, 25.0 * risk_rate * shrink)

    score = _clamp(ratio_score - penalty)
    detail = {
        "formula": "舆情健康度 = (50 + 50 × 净正面率) 经小样本收缩后,再按中高风险占比扣分",
        "groups": [{
            "title": "讨论情感分布", "got": pos, "max": n,
            "items": [
                {"label": "正面", "got": pos, "max": n},
                {"label": "中性", "got": neu, "max": n},
                {"label": "负面", "got": neg, "max": n},
            ],
        }],
        "inputs": [
            {"label": "净正面率", "value": round(raw, 3), "unit": ""},
            {"label": "样本量", "value": round(n, 1), "unit": "条"},
            {"label": "其中历史事件", "value": historical, "unit": "条"},
            {"label": "中高风险条数", "value": round(risky, 1), "unit": "条"},
            {"label": "风险扣分", "value": round(penalty, 1), "unit": "分"},
        ],
        "notes": [],
    }
    if historical:
        detail["notes"].append(
            f"{historical} 条证据发生在近 {_sentiment_window(data)} 天之外(或时间不详),"
            f"按 {int(_HISTORICAL_WEIGHT * 100)}% 权重计入。")
    if n < _SENTIMENT_MIN_CONFIDENT:
        detail["notes"].append(
            f"样本仅 {round(n, 1)} 条(加权后),分数已向中性收缩"
            f"({round(shrink * 100)}% 来自实测),仅供参考。")
    return score, detail


def _authority_detail(auth: Optional[dict], ent: Optional[dict],
                      source: str) -> dict[str, Any]:
    """Displayable details of authority endorsement.

    Two points that "would embarrass us if stated literally" are handled here:
      · the google section is capped at 5 points when counted toward the total, but google.score in the response is the uncapped value
      · the authority directory section has a 1-point floor -> this dimension's theoretical minimum is 5, not 0
    section_scores is a new field in authority_audit; the 24h cache holds both the new and old
    shapes, and when the field is missing only the total is given, no breakdown -- better not to show than to show something wrong.
    """
    groups: list[dict[str, Any]] = []
    notes: list[str] = []
    approximate = False

    if _ok(auth):
        a = auth["data"]
        sec = a.get("section_scores")
        items: list[dict[str, Any]] = []
        if isinstance(sec, dict) and sec:
            labels = {"reviews": "第三方评价平台", "awards": "奖项与资质",
                      "google": "搜索引擎权威度", "mentions": "权威名录收录"}
            for k, label in labels.items():
                got = int(sec.get(k) or 0)
                item: dict[str, Any] = {"label": label, "got": got, "max": 5}
                if k == "google":
                    raw = (a.get("google") or {}).get("score_raw")
                    if raw is not None and int(raw) > got:
                        item["capped"] = True
                        item["raw"] = int(raw)
                        notes.append(f"搜索引擎权威度原始得分 {int(raw)},计入总分时按单项上限 5 分截断。")
                if k == "mentions" and got == 1:
                    item["floor"] = True
                    notes.append("权威名录收录一项设有 1 分保底,本维度的理论下限为 5 分而非 0 分。")
                items.append(item)
            if sum(i["got"] for i in items) != int(a.get("score") or 0):
                approximate = True
                items = []
        else:
            approximate = True
        groups.append({
            "title": "站外权威审计",
            "got": int(a.get("score") or 0), "max": int(a.get("max_score") or 20),
            "percent": a.get("percent"), "items": items,
        })

    if _ok(ent):
        e = ent["data"]
        zh = {
            "Entity Recognition": "实体识别度", "Entity Clarity": "实体清晰度",
            "Category Association": "品类关联度", "Competitive Position": "竞争位置",
            "Sentiment & Framing": "情感与叙述", "Content Gap": "内容缺口",
            "Knowledge Graph": "知识图谱", "Platform Footprint": "平台足迹",
        }
        scores = e.get("scores") or {}
        groups.append({
            "title": "品牌实体认知",
            "got": int(e.get("total_score") or 0), "max": int(e.get("max_score") or 160),
            "percent": e.get("percent"),
            "items": [{"label": zh.get(k, k), "got": int(v or 0), "max": 20}
                      for k, v in scores.items()],
        })

    if source == "authority+entity":
        formula = "权威背书 = 站外权威得分 × 六成 + 实体认知得分 × 四成"
        inputs = [
            {"label": "站外权威审计", "value": (auth or {}).get("data", {}).get("percent"),
             "unit": "分", "weight": 0.6},
            {"label": "品牌实体认知", "value": (ent or {}).get("data", {}).get("percent"),
             "unit": "分", "weight": 0.4},
        ]
    elif source == "authority":
        formula = "本次仅采集到站外权威数据,本维度按站外权威单独计分"
        inputs = [{"label": "站外权威审计",
                   "value": (auth or {}).get("data", {}).get("percent"), "unit": "分"}]
    else:
        formula = "本次仅采集到实体认知数据,本维度按实体认知单独计分"
        inputs = [{"label": "品牌实体认知",
                   "value": (ent or {}).get("data", {}).get("percent"), "unit": "分"}]

    return {"formula": formula, "inputs": inputs, "groups": groups,
            "notes": notes, "approximate": approximate}


def _compute_scores(sub: dict[str, Any]) -> dict[str, Any]:
    base = sub.get("base"); aeo = sub.get("aeo")
    auth = sub.get("authority"); ent = sub.get("entity")
    vis = sub.get("visibility"); comp = sub.get("competitiveIntel")
    senti = sub.get("sentiment")

    dims: dict[str, Optional[dict[str, Any]]] = {}

    # A. AI search visibility -- visibility.total_score (0-100); degrades to aeo.score
    if _ok(vis):
        v = vis["data"]
        rates = v.get("per_engine_rates") or {}
        ped = v.get("per_engine_detail") or {}
        insuff = v.get("insufficient_engines") or []
        notes = []
        if insuff:
            notes.append("以下引擎有效应答不到题数一半,判为数据不足、不计入整体率:" + "、".join(
                f"{e}({(ped.get(e) or {}).get('valid', 0)}/{(ped.get(e) or {}).get('slots', 0)})" for e in insuff))
        if v.get("brand_query_answers"):
            notes.append(f"{v['brand_query_answers']} 条送分题应答不计入露出率——题面自带品牌名(品牌词、竞品对比词,或问句里直接点名),答案必然提到(应答率 {v.get('brand_query_rate')}%)。")
        dims["ai_visibility"] = {
            "score": _clamp(v.get("total_score", 0)),
            "estimated": False, "source": "visibility",
            "detail": {
                "formula": "AI 搜索可见度 = 整体露出率 = 品类题有效应答中本品牌被推荐/入榜的比例"
                           "(顺带提及不算;题面自带品牌名的送分题不计入分母)",
                "inputs": [
                    {"label": "整体露出率", "value": v.get("overall_rate"), "unit": "%"},
                    {"label": "品类题被推荐次数", "value": v.get("mention_count"), "unit": "次"},
                    {"label": "品类题有效应答数", "value": v.get("total_answers"), "unit": "条"},
                ] + ([{"label": "品牌题应答率(不计入)", "value": v.get("brand_query_rate"), "unit": "%"}]
                     if v.get("brand_query_rate") is not None else []),
                "groups": [{
                    "title": "各引擎整体露出率", "got": 0, "max": 100,
                    "items": [{"label": k, "got": round(float(val)), "max": 100,
                               **({"note": "数据不足,不计入"} if k in insuff else {})}
                              for k, val in rates.items()],
                }] if rates else [],
                "notes": notes, "approximate": False,
            },
        }
    elif _ok(aeo):
        dims["ai_visibility"] = {
            "score": _clamp(aeo["data"].get("score", 0)),
            "estimated": True, "source": "aeo",
            "detail": {"formula": "本次未取得多引擎实测,按答案引擎优化得分代偿估算",
                       "inputs": [], "groups": [], "notes": [], "approximate": True},
        }
    else:
        dims["ai_visibility"] = None

    # B. Infrastructure completeness -- 0.7 x base.score + 0.3 x aeo.score
    def _infra_detail(formula: str) -> dict[str, Any]:
        groups = []
        if _ok(base):
            clusters = base["data"].get("clusters") or []
            groups.append({
                "title": "五大短板簇", "got": 0, "max": 0,
                "items": [{"label": c.get("title_zh", ""), "got": 0, "max": 0,
                           "note": {"high": "严重", "med": "中等"}.get(c.get("severity"), "轻微")}
                          for c in clusters],
            })
        inputs = []
        if _ok(base):
            b = base["data"]
            inputs += [
                {"label": "官网体检评分", "value": b.get("score"), "unit": "分", "weight": 0.7},
                {"label": "通过项", "value": b.get("pass_count"), "unit": "项"},
                {"label": "警告项", "value": b.get("warn_count"), "unit": "项"},
                {"label": "未通过项", "value": b.get("fail_count"), "unit": "项"},
            ]
        if _ok(aeo):
            inputs.append({"label": "答案引擎优化评分", "value": aeo["data"].get("score"),
                           "unit": "分", "weight": 0.3})
        return {"formula": formula, "inputs": inputs, "groups": groups,
                "notes": [], "approximate": False}

    if _ok(base) and _ok(aeo):
        dims["infrastructure"] = {
            "score": _clamp(0.7 * base["data"].get("score", 0) + 0.3 * aeo["data"].get("score", 0)),
            "estimated": False, "source": "base+aeo",
            "detail": _infra_detail("基建完善度 = 官网体检评分 × 七成 + 答案引擎优化评分 × 三成")}
    elif _ok(base):
        dims["infrastructure"] = {"score": _clamp(base["data"].get("score", 0)),
                                  "estimated": False, "source": "base",
                                  "detail": _infra_detail("本次仅采集到官网体检,按体检评分单独计分")}
    elif _ok(aeo):
        dims["infrastructure"] = {"score": _clamp(aeo["data"].get("score", 0)),
                                  "estimated": True, "source": "aeo",
                                  "detail": _infra_detail("本次仅采集到答案引擎优化,按其得分代偿估算")}
    else:
        dims["infrastructure"] = None

    # C. Authority endorsement -- 0.6 x authority.percent + 0.4 x entity.percent
    if _ok(auth) and _ok(ent):
        dims["authority"] = {
            "score": _clamp(0.6 * auth["data"].get("percent", 0) + 0.4 * ent["data"].get("percent", 0)),
            "estimated": False, "source": "authority+entity",
            "detail": _authority_detail(auth, ent, "authority+entity")}
    elif _ok(auth):
        dims["authority"] = {"score": _clamp(auth["data"].get("percent", 0)),
                             "estimated": False, "source": "authority",
                             "detail": _authority_detail(auth, None, "authority")}
    elif _ok(ent):
        dims["authority"] = {"score": _clamp(ent["data"].get("percent", 0)),
                             "estimated": True, "source": "entity",
                             "detail": _authority_detail(None, ent, "entity")}
    else:
        dims["authority"] = None

    # D. Competitive advantage -- mention rate relative to the leading competitor (60%) + engine coverage (40%)
    if _ok(comp):
        insight = comp["data"].get("competitor_insight") or {}
        ranking = comp["data"].get("brand_ranking") or {}
        your_rate = float(insight.get("your_rate") or 0)
        top_rate = float(insight.get("top_competitor_rate") or 0)
        mentioned = int(ranking.get("engines_mentioned") or 0)
        checked = int(ranking.get("engines_checked") or 0)
        rel = your_rate / max(your_rate, top_rate, 1.0) * 60.0
        cov = mentioned / max(checked, 1) * 40.0
        score = min(100.0, rel + cov)
        dims["competitive"] = {
            "score": _clamp(score), "estimated": False, "source": "competitiveIntel",
            "detail": {
                "formula": "竞品对比优势 = 相对头部竞品的提及率(满分 60)+ 引擎覆盖率(满分 40)",
                "inputs": [
                    {"label": "本品牌提及率", "value": your_rate, "unit": "%"},
                    {"label": "头部竞品提及率", "value": top_rate, "unit": "%"},
                    {"label": "相对提及率得分", "value": round(rel, 1), "unit": "分"},
                    {"label": "命中引擎数", "value": f"{mentioned}/{checked}", "unit": ""},
                    {"label": "引擎覆盖得分", "value": round(cov, 1), "unit": "分"},
                ],
                "groups": [], "notes": [], "approximate": False,
            },
        }
    elif _ok(vis):
        # visibility.scores.competitor is a 0-20 sub-score
        comp_sub = ((vis["data"].get("scores") or {}).get("competitor") or 0)
        dims["competitive"] = {
            "score": _clamp(comp_sub * 5), "estimated": True, "source": "visibility",
            "detail": {"formula": "本次未取得竞品情报,按可见性实测中的竞对格局子分代偿估算",
                       "inputs": [], "groups": [], "notes": [], "approximate": True},
        }
    else:
        dims["competitive"] = None

    # E. Sentiment health -- positive/negative structure of social media discussions + risk share
    dims["sentiment"] = None
    if _ok(senti):
        res = _sentiment_score(senti["data"])
        if res is not None:
            score, detail = res
            n = sum(1 for p in (senti["data"].get("posts") or []) if isinstance(p, dict))
            dims["sentiment"] = {"score": score, "estimated": n < _SENTIMENT_MIN_CONFIDENT,
                                 "source": "sentiment", "detail": detail}
    if dims["sentiment"] is None and _ok(ent):
        # Substitute metric definition: the tone AI engines use about this brand in the entity audit (not social media reputation)
        e = ent["data"]
        if str(e.get("sentiment") or "unknown") != "unknown":
            sub_score = (e.get("scores") or {}).get("Sentiment & Framing")
            if sub_score is not None:
                dims["sentiment"] = {
                    "score": _clamp(float(sub_score) / 20 * 100),
                    "estimated": True, "source": "entity",
                    "detail": {
                        "formula": "本次未取得社媒舆情,按智能引擎回答中的情感与叙述倾向代偿估算",
                        "inputs": [{"label": "引擎情感倾向", "value": e.get("sentiment"), "unit": ""}],
                        "groups": [], "approximate": True,
                        "notes": ["该分数衡量的是智能引擎回答里的口吻,不等同于社媒口碑。"],
                    },
                }

    # Total: weighted over dimensions with values; when dimensions are missing, weights are redistributed proportionally, never dragging the total down with 0
    weighted = 0.0
    weight_sum = 0.0
    out_dims: list[dict[str, Any]] = []
    missing: list[str] = []
    for key, weight in DIMENSION_DEFS:
        d = dims.get(key)
        if d is None:
            out_dims.append({"key": key, "score": None, "weight": weight,
                             "estimated": False, "source": "", "detail": None})
            missing.append(key)
            continue
        weighted += d["score"] * weight
        weight_sum += weight
        out_dims.append({"key": key, "weight": weight, **d})

    total = _clamp(weighted / weight_sum) if weight_sum > 0 else 0
    return {
        "total": total,
        "grade": _grade(total),
        "dimensions": out_dims,
        "formula": "综合评分 = 各维度得分 × 权重之和 ÷ 有数据维度的权重之和",
        "weight_sum": round(weight_sum, 4),
        "reweighted": bool(missing),
        "missing_dimensions": missing,
        "grade_scale": [[g, f] for g, f in GRADE_SCALE],
    }


# --------------- LLM narrative layer ---------------


def _sample_queries(sub: dict[str, Any], limit: int = 8) -> list[str]:
    """Pull a few queries actually asked from sub-results as category evidence.

    The industry term is user-entered and may itself be ambiguous (report #99 entered "<brand-d>羊肉哪家好" ("which <brand-d> mutton is good"),
    which the model easily reads as the dairy brand "<brand-d>"). The queries actually sent are unambiguous
    -- with a sentence like "recommend mutton brands with good reputation" in front of it, the category cannot be guessed wrong.
    """
    out: list[str] = []

    def walk(o: Any, depth: int = 0) -> None:
        if depth > 6 or len(out) >= limit:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("query", "question") and isinstance(v, str) and v.strip():
                    if v.strip() not in out:
                        out.append(v.strip())
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)

    walk(sub)
    return out[:limit]


def _summarize_for_llm(brand: str, url: str, scores: dict[str, Any],
                       sub: dict[str, Any],
                       industry_terms: list[str] | None = None,
                       brand_terms: list[str] | None = None) -> str:
    """Compress each sub-item into a fact list of <6k tokens for the LLM. skipped/failed sub-items are stated as no data.

    **Must include the industry term**: previously only the brand name was given, and the model could only guess the category from the name -- report #99's
    brand is "<brand-a>", selling beef and mutton; the model guessed goat milk powder, "milk powder" appeared 19 times in the narrative,
    "mutton" only 3 times. Yet all 15 queries actually asked in that report were about mutton, i.e. the data was right;
    what was wrong was that the narrative layer did not know the category at all.
    """
    parts: list[str] = [
        f"品牌:{brand}",
        (f"官网:{url}" if url else
         "官网:该品牌未提供官网(线下服务商 / 只有公众号或小程序)。"
         "本次不含官网体检、爬虫可达性等站点侧检测,"
         "不要写「优化官网 TDK / 加 schema」这类站点侧建议,"
         "改法要落在第三方平台内容、百科词条、问答社区、媒体报道这类站外资产上。"),
    ]
    # The category anchor comes right after the brand name and self-inference is explicitly forbidden -- placed early, the model is least likely to ignore it
    terms = [t for t in (industry_terms or []) if str(t).strip()]
    bterms = [t for t in (brand_terms or []) if str(t).strip()]
    if terms or bterms:
        line = "所属品类 / 用户填写的行业词:" + "、".join(terms[:8]) if terms else ""
        if bterms:
            line = (line + ";品牌词:" if line else "品牌词:") + "、".join(bterms[:8])
        parts.append(line)
        parts.append("**品类以上面这行为准,不要从品牌名去推断品类**。"
                     "名字里带的字("
                     "如「羊」「牛」「天」)不代表业务方向,凭它联想出的品类会让整份报告跑偏。")
    else:
        parts.append("所属品类:未提供。**不要猜品类** —— "
                     "叙述里只用下面实测数据中出现过的品类词,拿不准就不写具体品类。")
    qs = _sample_queries(sub)
    if qs:
        parts.append("本次实际向 AI 提出的问题(品类以这些为准,它们是实测证据):"
                     + " / ".join(qs))
    parts.append(f"综合评分:{scores.get('total')}/100(评级 {scores.get('grade')})")
    for d in scores.get("dimensions", []):
        name = DIMENSION_NAMES_ZH.get(d["key"], d["key"])
        if d.get("score") is None:
            parts.append(f"- 维度「{name}」:无数据")
        else:
            est = "(估算值)" if d.get("estimated") else ""
            weight = round(float(d.get("weight") or 0) * 100)
            parts.append(f"- 维度「{name}」:{d['score']}/100{est},权重 {weight}%")
            # Feed the synthesis metric definition too, so dimension_notes can cite "43 points = off-site 35x0.6 + entity 55x0.4"
            formula = ((d.get("detail") or {}).get("formula") or "").strip()
            if formula:
                parts.append(f"  · 计算口径:{formula}")
    if scores.get("missing_dimensions"):
        miss = "、".join(DIMENSION_NAMES_ZH.get(k, k) for k in scores["missing_dimensions"])
        parts.append(f"- 未采集维度:{miss}(其权重已按比例分摊给其余维度)")

    base = sub.get("base")
    if _ok(base):
        b = base["data"]
        parts.append(f"\n## 官网基建体检(25 类):{b.get('score')}/100 {b.get('grade')},"
                     f"通过 {b.get('pass_count')} 警告 {b.get('warn_count')} 失败 {b.get('fail_count')}")
        for c in (b.get("clusters") or []):
            bullets = "; ".join((c.get("bullets") or [])[:3])
            parts.append(f"- 【{c.get('title_zh')}】严重度 {c.get('severity')}:{bullets or '无明显短板'}")
        # Per-item details of the 25 categories -- previously only 3 bullets per each of five clusters were fed, so the model could not tell which item failed,
        # and the advice it wrote could only be empty phrases like "improve content quality". Take the top 20 by severity.
        checks = [c for c in (b.get("all_checks") or []) if isinstance(c, dict)]
        bad = ([c for c in checks if c.get("status") == "FAIL"]
               + [c for c in checks if c.get("status") == "WARN"])[:20]
        if bad:
            parts.append("- 未通过/警告的具体检查项:")
            for c in bad:
                msg = str(c.get("message") or "")[:100]
                fix = str(c.get("fix") or "").splitlines()[0][:80] if c.get("fix") else ""
                line = f"  · [{c.get('status')}] {c.get('category')}:{msg}"
                if fix:
                    line += f" | 建议:{fix}"
                parts.append(line)
    else:
        parts.append("\n## 官网基建体检:" + ("本次未提供官网,该项未检测;基建完善度维度无分,"
                                             "其权重已分摊给其余维度,不要编造官网体检结论"
                                             if not url else "无数据,不要编造"))

    aeo = sub.get("aeo")
    if _ok(aeo):
        a = aeo["data"]
        parts.append(f"\n## AEO 答案引擎优化:{a.get('score')}/100")
        for imp in (a.get("priority_improvements") or [])[:5]:
            parts.append(f"- 待改进:{str(imp)[:120]}")

    crawl = sub.get("crawlTest")
    if _ok(crawl):
        c = crawl["data"]
        blocked = list((c.get("robots") or {}).get("blocked") or []) \
            + list((c.get("waf") or {}).get("blocked") or [])
        blocked = [str(b) for b in blocked if str(b)]
        parts.append("\n## AI 爬虫可达性:"
                     + (f"被拦截的爬虫:{', '.join(blocked)[:200]}" if blocked else "主流 AI 爬虫均可抓取"))

    auth = sub.get("authority")
    if _ok(auth):
        parts.append(f"\n## 权威信号审计:{auth['data'].get('percent')}/100")

    vis = sub.get("visibility")
    if _ok(vis):
        v = vis["data"]
        rates = "; ".join(f"{k} {val}%" for k, val in (v.get("per_engine_rates") or {}).items())
        parts.append(f"\n## 多引擎 AI 可见性实测:{v.get('total_score')}/100,各引擎提及率:{rates}")
        tops = ", ".join(f"{t.get('domain')}({t.get('mentions')}次)"
                         for t in (v.get("top_competitors") or [])[:5])
        if tops:
            parts.append(f"- AI 回答中出现的竞对域名:{tops}")
        gaps = "; ".join(str(g)[:60] for g in (v.get("content_gaps") or [])[:8])
        if gaps:
            parts.append(f"- 内容缺口:{gaps}")
    else:
        parts.append("\n## 多引擎 AI 可见性实测:无数据,不要编造")

    ent = sub.get("entity")
    if _ok(ent):
        e = ent["data"]
        parts.append(f"\n## 品牌实体认知审计:{e.get('percent')}/100,AI 认知率 {e.get('recognition_rate')}%")

    comp = sub.get("competitiveIntel")
    if _ok(comp):
        insight = comp["data"].get("competitor_insight") or {}
        parts.append(f"\n## 竞品情报:本品牌提及率 {insight.get('your_rate')}%,"
                     f"头部竞品提及率 {insight.get('top_competitor_rate')}%")
        for c in (insight.get("competitors") or [])[:5]:
            if isinstance(c, dict):
                parts.append(f"- 竞品 {c.get('name') or c.get('brand') or ''}:提及率 {c.get('rate')}%")
    else:
        parts.append("\n## 竞品情报:无数据,不要编造竞品数字")

    senti = sub.get("sentiment")
    if _ok(senti):
        s = senti["data"]
        kpi = s.get("kpi") or {}
        window = s.get("window_days") or 30
        # Positive/negative counts use the symmetric stance metric definition, not the old positive_count/negative_count
        st = s.get("stance") or {}
        n = st.get("classified") or len(s.get("posts") or [])
        rng = s.get("evidence_range") or {}
        span = (f"{rng.get('from') or '?'} ~ {rng.get('to') or '?'}"
                if (rng.get("from") or rng.get("to")) else "时间不详")
        # Never write "X items monitored in the last N days" here -- Yuanbao WebSearch does not filter by time,
        # evidence spans more than a decade, and that sentence would make the model write a 2015 short-seller report as this month's sentiment.
        #
        # Average sentiment score (avg_sentiment) is sentiment polarity on a -1~1 scale, not a 0-100 score:
        #   -1 = very negative, 0 = neutral, +1 = very positive; the sign is direction, the absolute value is intensity.
        #   A signed symmetric scale is used so 0 naturally means neutral and positives and negatives cancel out directly,
        #   matching the metric definition of sentinel-service per-item analysis and industry sentiment analysis (Google NL / VADER, etc.).
        # It is produced by the model scoring all search results as a whole when Yuanbao WebSearch summarizes (see
        # sentiment_yuanbao_websearch._normalize_score), not a per-item average,
        # and does not take part in computing sentiment health (0-100). Given only the number, the model would write "-0.1"
        # bare into the health interpretation next to "41/100", which customers cannot understand -- so it is fed together with the scale and
        # textual tiers, and citations are required to include the scale description.
        avg = kpi.get("avg_sentiment")
        avg_text = ""
        if isinstance(avg, (int, float)):
            tone = ("明显正面" if avg >= 0.6 else "偏正面" if avg >= 0.2
                    else "基本中性" if avg > -0.2 else "偏负面" if avg > -0.6 else "明显负面")
            avg_text = (f",整体情感倾向 {avg:+.2f}(量程 -1~1:-1 极负面、0 中性、+1 极正面;"
                        f"即{tone})")
        parts.append(f"\n## 舆情探测(公开信息,证据时间范围 {span}):共 {n} 条,"
                     f"其中近 {window} 天内 {s.get('recent_count', 0)} 条、"
                     f"更早 {s.get('historical_count', 0)} 条、"
                     f"时间不详 {s.get('undated_count', 0)} 条;"
                     f"正面 {st.get('pos', '?')} 条,中性 {st.get('neu', '?')} 条,"
                     f"负面 {st.get('neg', s.get('negative_count'))} 条,"
                     f"中高风险 {kpi.get('high_risk', 0)} 条{avg_text}")
        parts.append("- 写作要求:提到任何具体事件都必须带上它的发生时间;"
                     "不得把上面标为「更早」或「时间不详」的事件说成近期/最新/本月;"
                     "不得凭空给出监测周期。")
        if avg_text:
            parts.append("- 引用整体情感倾向时必须同时写明量程(-1~1,0 为中性)和文字档位,"
                         "例如「整体情感倾向 -0.10(-1~1,0 为中性),基本中性」;"
                         "它不是 0-100 的分数,不得与舆情健康度得分混为一谈或相互换算。")
        for p in (s.get("negative_posts") or [])[:5]:
            when = p.get("publish_time") or "时间未知"
            parts.append(f"- 负面[{when}]:{p.get('title')}(来源 {p.get('source')}):"
                         f"{(p.get('summary') or '')[:80]}")
    else:
        parts.append("\n## 舆情探测:无数据,不要编造舆情结论")

    # Six metrics (only present when backend metric aggregation is ready); when the neutral-positive rate is unavailable, inventing numbers is explicitly forbidden
    vis = sub.get("visibility")
    metrics = ((vis or {}).get("data") or {}).get("metrics") if _ok(vis) else None
    if metrics:
        scope = metrics.get("metric_scope") or {}
        overall = (metrics.get("overall") or {}).get("metrics") or {}
        zh = {"exposure": "整体露出率", "top1": "TOP1露出率", "top3": "TOP3露出率",
              "accuracy": "内容中正率", "citation": "文章引用率", "mention": "品牌声量度"}
        vals = "、".join(f"{zh[k]} {overall[k]}%" for k in _METRIC_KEYS
                        if overall.get(k) is not None)
        parts.append(f"\n## 六项指标(整体口径):{vals or '无'}")
        # The metric definition must accompany the numbers: if the model cannot see the exclusion rules it will write
        # achievement paths contrary to the metric definition, like "maintain TOP3 exposure for brand terms and competitor-comparison terms"
        excl_zh = "、".join(_SCENE_ZH.get(s, s)
                           for s in (scope.get("exposure_only_scenes") or []))
        if excl_zh:
            parts.append(
                f"- 口径:整体露出率 / TOP1露出率 / TOP3露出率 只统计**品类词**,"
                f"{excl_zh}的应答已从这三项的分子与分母中整体剔除。"
                f"因此这三项的达成方式**不得**写成靠{excl_zh}去提升,只能写品类词侧的动作。")
        cat_hit, cat_valid = overall.get("_cat_hit"), overall.get("_cat_valid")
        if cat_valid:
            parts.append(
                f"- 分母:三项露出率同尺,分母均为品类词有效应答数 {int(cat_valid)} 条"
                f"(其中命中 {int(cat_hit or 0)} 条),TOP1 ≤ TOP3 ≤ 整体露出率 恒成立;"
                f"预测值也必须满足这个大小关系。")
            if int(cat_hit or 0) <= 3:
                parts.append("- 样本提示:品类词命中样本极小,不要把当前位次表现说成「已具备优势」"
                             "或「维持现状即可」,应按「样本不足、需先把露出率做起来」判读。")
        if scope.get("accuracy_status") not in (None, "ok") or overall.get("accuracy") is None:
            parts.append("- 内容中正率本次未取得,metrics_notes.accuracy 请写「本次未取得」,不要编数字。")
        if scope.get("citation_scope") == "none" or overall.get("citation") is None:
            parts.append("- 文章引用率本次未取得(该品牌未提供官网,没有自有域名可比对),"
                         "metrics_notes.citation 请写「本次未取得」并说明原因,不要编数字。")
        for row in (metrics.get("by_engine") or [])[:8]:
            m = row.get("metrics") or {}
            vs = "、".join(f"{zh[k]} {m[k]}%" for k in _METRIC_KEYS if m.get(k) is not None)
            parts.append(f"- 引擎 {row.get('engine')}:{vs}")
        for row in (metrics.get("by_scene") or [])[:8]:
            m = row.get("metrics") or {}
            cnt = row.get("query_count")
            vs = "、".join(f"{zh[k]} {m[k]}%" for k in _METRIC_KEYS if m.get(k) is not None)
            sc = row.get("scene")
            # Excluded term types must be flagged on the spot: within a slice the slice's own metric definition applies (it does not exclude itself);
            # without the flag the model would interpret it as part of the overall exposure rate
            mark = "(该词类不计入整体露出率/TOP1/TOP3,以下为词类自身口径)" \
                if row.get("exposure_excluded_from_global") else ""
            parts.append(f"- 词类 {_SCENE_ZH.get(sc, sc)}({cnt} 条){mark}:{vs}")

    return "\n".join(parts)


# The narrative block is split into two calls: all blocks needed by the eleven chapters in a single JSON would hit the output cap,
# and once truncated, _parse_json_loose gets half a JSON and returns {} directly, emptying the whole narrative layer.
# The two run in parallel, adding no total time, and a failure on one side does not affect the other.

_SCHEMA_A = {
    "executive_summary": "string,150-300字,品牌 AI 搜索可见性现状的执行摘要",
    "key_findings": [{"title": "string,15字内", "detail": "string,40-100字",
                      "severity": "high|mid|low"}],
    "risks": ["string,30-80字,风险提示,2-4条"],
    "dimension_notes": {k: "string,60-150字,该维度得分解读(要引用给出的分数与计算口径)"
                        for k in DIMENSION_KEYS},
    "scene_intent": {
        "summary": "string,80-200字,五类种子词覆盖与用户意图分布的整体判读",
        "by_scene": [{
            "scene": "compare|brand|search|qa|intent|scenario",
            "intent_desc": "string,30-60字,这类词代表用户处于什么决策阶段",
            "finding": "string,40-100字,本品牌在这类词上的实测表现(必须引用给出的数字)",
        }],
        "gaps": ["list[str],2-4条,覆盖薄弱或完全缺失的场景"],
    },
    "metrics_notes": {
        "exposure": "string,40-90字", "top1": "string,40-90字", "top3": "string,40-90字",
        "accuracy": "string,40-90字(标注为未取得时写「本次未取得」,不要编数字)",
        "citation": "string,40-90字", "mention": "string,40-90字",
        "weakest": "exposure|top1|top3|accuracy|citation|mention",
        "summary": "string,80-180字,六项指标整体判读 + 最该先修哪个及理由",
    },
    "infrastructure_note": {
        "summary": "string,100-200字",
        "top_issues": [{
            "cluster": "string,五簇中文名之一",
            "issue": "string,30-60字,具体是哪一项检查不通过",
            "fix": "string,40-100字,具体到改哪个文件/标签/字段,不接受「优化内容质量」这种空话",
            "severity": "high|mid|low",
        }],
        "crawler_note": "string,40-100字,智能爬虫可达性解读",
    },
    "authority_note": {
        "summary": "string,80-180字",
        "strong_signals": ["list[str],0-4条,已有的权威信号"],
        "missing_signals": ["list[str],2-5条,具体缺哪类背书(不要笼统说「缺少权威背书」)"],
        "actions": [{"action": "string,40字内", "channel": "string,具体渠道或平台名",
                     "eta_weeks": "int"}],
    },
    "sentiment_summary": {
        "risk_level": "低|中|高(无舆情数据时填 未知)",
        "summary": "string,60-150字,口碑现状综述",
        "positive_points": ["list[str],正面口碑要点,0-5条"],
        "negative_points": ["list[str],主要负面点,0-4条,无则空数组"],
        "suggestions": ["list[str],应对建议,2-4条"],
    },
}

_SCHEMA_B = {
    "personas": [{"name": "string,画像名", "intent": "string,搜索意图",
                  "scenario": "string,40-100字,场景描述",
                  "typical_queries": ["list[str],3-5条典型搜索问题"]}],
    "competitive_summary": "string,80-200字,竞争格局与优势逻辑叙述",
    "competitor_profiles": [{
        "name": "string,竞品名(只用给出的真实竞品,最多3个)",
        "strengths": ["list[str],2-4条该竞品优势"],
        "weaknesses": ["list[str],2-3条该竞品劣势"],
    }],
    "brand_strengths": ["list[str],本品牌优势3-5条"],
    "brand_weaknesses": ["list[str],本品牌劣势3-5条"],
    "quick_wins": [{"action": "string,30字内", "expected_impact": "string,30-80字",
                    "effort": "low|mid", "expected_gain": "int,预计提升总分1-10"}],
    "p1_actions": [{"action": "string,40字内", "reason": "string,40-120字",
                    "eta_weeks": "int", "expected_gain": "int,预计提升总分1-10"}],
    "p2_actions": [{"action": "string,40字内", "reason": "string,40-120字",
                    "eta_weeks": "int", "expected_gain": "int,预计提升总分1-10"}],
    "roadmap": [{"phase": "string,阶段名", "weeks": "string,如 1-2周",
                 "goals": ["list[str],2-4条目标"]}],
    "overall_assessment": {
        "verdict": "string,120-250字,现状总判",
        "stage": "起步期|建设期|竞争期|领先期",
        "benchmark": "string,60-120字,与同类的对比表述(无竞品数据时写「本次未取得竞品对比数据」)",
        "metric_projection": [{
            "key": "exposure|top1|top3|accuracy|citation|mention",
            "current": "float,必须等于给出的实测值",
            "after_30d": "float,exposure/top1/top3 三项须满足 top1 ≤ top3 ≤ exposure",
            "after_90d": "float,同上,且不得低于 after_30d",
            "driver": "string,30-60字,靠什么动作达成;exposure/top1/top3 的达成方式"
                      "只能写品类词侧动作,写「品牌词」「竞品对比词」即为错误",
        }],
    },
    "score_projection": {"current": "int", "after_30d": "int", "after_90d": "int",
                         "assumptions": "string,40-100字,预测前提"},
    "dimension_projection": {k: {"current": "int", "target": "int,90天后预期"}
                             for k in DIMENSION_KEYS},
}

# Compatibility with old references
_NARRATIVE_SCHEMA_HINT = {**_SCHEMA_A, **_SCHEMA_B}


def _llm_provider() -> tuple[str, str, str]:
    """Provider selection chain matches solution_generator: DeepSeek direct first, OpenRouter as fallback."""
    import os
    from geo.services.solution_generator import DEEPSEEK_MODEL, OPENROUTER_DEEPSEEK_MODEL

    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    raise RuntimeError("DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置")


def _narrative_thinking() -> bool:
    """GEO_DIAG_NARRATIVE_THINKING=1 turns on thinking for the narrative block's reasoning model; off by default."""
    return os.environ.get("GEO_DIAG_NARRATIVE_THINKING", "").strip().lower() in ("1", "true", "on", "yes")


def _chat_json(provider: str, model_id: str, api_key: str,
               system_prompt: str, user_prompt: str, temperature: float) -> dict:
    from geo.services.solution_generator import (
        _parse_json_loose, DEEPSEEK_BASE_URL, OPENROUTER_URL, DEFAULT_TIMEOUT,
    )
    if provider == "deepseek":
        api_url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        api_url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com", "X-Title": "GEO Full Diagnosis",
        }
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    if provider == "deepseek" and not _narrative_thinking():
        # The narrative block writes interpretation copy per schema and needs no reasoning; with thinking on for v4-flash in production,
        # the parallel A/B calls still take 4-5 minutes, the slowest part of the whole report (2026-09-14 report #124).
        payload["thinking"] = {"type": "disabled"}
    r = post_and_record(api_url, payload, headers, provider=provider,
                        feature="full_diagnosis", timeout=DEFAULT_TIMEOUT, model=model_id)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")
    return _parse_json_loose(content)


_SYSTEM_COMMON = "\n".join([
    "你是 GEO(Generative Engine Optimization)品牌诊断顾问。",
    "任务:基于下面给出的多项检测硬数据,产出「AI 搜索可见性综合诊断」的解读 JSON。",
    "",
    "## 写作要求(严格遵守)",
    "1. 全部用中文,专业但不堆术语;所有中文标点用全角。",
    "2. 不要使用 Markdown 符号(# ** * - ` 等)。",
    "3. 只基于给出的数据解读;标注「无数据」「本次未取得」的部分不要编造数字或竞品名。",
    "4. 严格按 user 给的 JSON schema 输出,不要多字段不要漏字段,不要 ``` 围栏。",
])


def _call_llm_narrative(brand: str, url: str, scores: dict[str, Any],
                        sub: dict[str, Any],
                        industry_terms: list[str] | None = None,
                        brand_terms: list[str] | None = None,
                        ) -> tuple[dict[str, Any], str]:
    """Two parallel calls produce all narrative blocks, merged then normalized.

    A single output with all blocks for the eleven chapters would hit the JSON cap, and truncation would empty the whole narrative layer,
    so it is split into "diagnosis interpretation" and "strategy and expectations" groups; a failure loses only that group.
    """
    provider, model_id, api_key = _llm_provider()
    facts = _summarize_for_llm(brand, url, scores, sub,
                               industry_terms=industry_terms, brand_terms=brand_terms)

    prompt_a = (
        facts + "\n\n输出 JSON Schema:\n"
        + json.dumps(_SCHEMA_A, ensure_ascii=False, indent=2)
        + "\n\nkey_findings 3-5 条;risks 2-4 条;infrastructure_note.top_issues 3-5 条。只输出 JSON。"
    )
    prompt_b = (
        facts + "\n\n输出 JSON Schema:\n"
        + json.dumps(_SCHEMA_B, ensure_ascii=False, indent=2)
        + "\n\nquick_wins 3 条;p1_actions 2-3 条;p2_actions 2-3 条;personas 3 个;"
          "roadmap 3 个阶段(即时/短期/长期)。"
          "score_projection 的 current 必须等于给出的综合评分,"
          "dimension_projection 各维 current 必须等于给出的维度分,"
          "overall_assessment.metric_projection 的 current 必须等于给出的实测值。"
          "competitor_profiles 只能写数据里给出的真实竞品名,最多 3 个。只输出 JSON。"
    )

    sys_a = _SYSTEM_COMMON + "\n5. 本次只产出「诊断解读」部分。"
    sys_b = _SYSTEM_COMMON + "\n5. 本次只产出「策略与预期」部分,预测要合理保守。"

    merged: dict[str, Any] = {}
    errs: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Use llm_usage.submit instead of pool.submit: thread pools do not inherit contextvars,
        # and a direct submit would make these two calls lose their report_id / user_id attribution in accounting.
        fut = {
            "A": submit(pool, _chat_json, provider, model_id, api_key, sys_a, prompt_a, 0.4),
            "B": submit(pool, _chat_json, provider, model_id, api_key, sys_b, prompt_b, 0.5),
        }
        for tag, f in fut.items():
            try:
                merged.update(f.result() or {})
            except Exception as e:  # noqa: BLE001
                errs.append(f"{tag}: {e}")
                log.warning("full diagnosis narrative call %s failed: %s", tag, e)

    if not merged:
        raise RuntimeError("；".join(errs) or "两次叙述调用均无有效输出")
    return _normalize_narrative(merged, scores, sub), model_id


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> Optional[float]:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _normalize_narrative(raw: dict, scores: dict[str, Any],
                         sub: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Lightweight validation + type fallback -- guarantees stable types for every block the frontend gets."""
    def _str(v: Any) -> str:
        return str(v).strip() if v else ""

    def _list_of_dict(v: Any) -> list[dict]:
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def _list_of_str(v: Any) -> list[str]:
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    # Measured values and availability of the six metrics -- used to override the model's current and to block "neutral-positive rate not computed but a number written"
    _metrics = ((sub or {}).get("visibility") or {}).get("data") or {}
    _metrics = _metrics.get("metrics") or {}
    _actual_metrics: dict[str, Optional[float]] = {}
    for k, v in ((_metrics.get("overall") or {}).get("metrics") or {}).items():
        if k in _METRIC_KEYS:
            _actual_metrics[k] = _float_or_none(v)
    _accuracy_unavailable = (
        (_metrics.get("metric_scope") or {}).get("accuracy_status") not in (None, "ok")
        or _actual_metrics.get("accuracy") is None
    )

    dim_notes_raw = raw.get("dimension_notes") or {}
    if not isinstance(dim_notes_raw, dict):
        dim_notes_raw = {}
    dim_notes = {k: _str(dim_notes_raw.get(k)) for k in DIMENSION_KEYS}

    proj_raw = raw.get("score_projection") or {}
    if not isinstance(proj_raw, dict):
        proj_raw = {}
    projection = None
    try:
        current = int(proj_raw.get("current", scores.get("total", 0)))
        after_30 = int(proj_raw.get("after_30d", 0))
        after_90 = int(proj_raw.get("after_90d", 0))
        if after_30 > 0 and after_90 > 0:
            projection = {
                "current": scores.get("total", current),  # Actual score is authoritative
                "after_30d": max(0, min(100, after_30)),
                "after_90d": max(0, min(100, after_90)),
                "assumptions": _str(proj_raw.get("assumptions")),
            }
    except (TypeError, ValueError):
        projection = None

    senti_raw = raw.get("sentiment_summary") or {}
    if not isinstance(senti_raw, dict):
        senti_raw = {}
    sentiment_summary = {
        "risk_level": _str(senti_raw.get("risk_level")) or "未知",
        "summary": _str(senti_raw.get("summary")),
        "positive_points": _list_of_str(senti_raw.get("positive_points"))[:6],
        "negative_points": _list_of_str(senti_raw.get("negative_points"))[:5],
        "suggestions": _list_of_str(senti_raw.get("suggestions"))[:5],
    }

    # Per-dimension predictions: current follows the actual score; drop the dimension if target is missing/unreasonable
    dim_proj_raw = raw.get("dimension_projection") or {}
    if not isinstance(dim_proj_raw, dict):
        dim_proj_raw = {}
    actual_dims = {d["key"]: d.get("score") for d in scores.get("dimensions", [])}
    dimension_projection: dict[str, Any] = {}
    for key in DIMENSION_KEYS:
        cur = actual_dims.get(key)
        item = dim_proj_raw.get(key) or {}
        try:
            target = int(item.get("target", 0))
        except (TypeError, ValueError):
            target = 0
        if cur is not None and 0 < target <= 100:
            dimension_projection[key] = {"current": cur, "target": max(target, int(cur))}

    competitor_profiles = []
    for p in _list_of_dict(raw.get("competitor_profiles"))[:3]:
        name = _str(p.get("name"))
        if not name:
            continue
        competitor_profiles.append({
            "name": name,
            "strengths": _list_of_str(p.get("strengths"))[:4],
            "weaknesses": _list_of_str(p.get("weaknesses"))[:4],
        })

    def _obj(key: str) -> dict:
        v = raw.get(key)
        return v if isinstance(v, dict) else {}

    # Chapter 04
    si = _obj("scene_intent")
    scene_intent = {
        "summary": _str(si.get("summary")),
        "by_scene": [
            {"scene": _str(x.get("scene")), "intent_desc": _str(x.get("intent_desc")),
             "finding": _str(x.get("finding"))}
            for x in _list_of_dict(si.get("by_scene"))[:6] if _str(x.get("scene"))
        ],
        "gaps": _list_of_str(si.get("gaps"))[:5],
    } if si else None

    # Chapter 06 -- force-clear this item's copy when the neutral-positive rate is unavailable, to stop the model inventing numbers
    mn = _obj("metrics_notes")
    metrics_notes = None
    if mn:
        metrics_notes = {k: _str(mn.get(k)) for k in _METRIC_KEYS}
        metrics_notes["weakest"] = _str(mn.get("weakest"))
        metrics_notes["summary"] = _str(mn.get("summary"))
        if _accuracy_unavailable:
            metrics_notes["accuracy"] = ""

    # Chapter 07
    inf = _obj("infrastructure_note")
    infrastructure_note = {
        "summary": _str(inf.get("summary")),
        "top_issues": [
            {"cluster": _str(x.get("cluster")), "issue": _str(x.get("issue")),
             "fix": _str(x.get("fix")), "severity": _str(x.get("severity")) or "mid"}
            for x in _list_of_dict(inf.get("top_issues"))[:6] if _str(x.get("issue"))
        ],
        "crawler_note": _str(inf.get("crawler_note")),
    } if inf else None

    # Chapter 08
    au = _obj("authority_note")
    authority_note = {
        "summary": _str(au.get("summary")),
        "strong_signals": _list_of_str(au.get("strong_signals"))[:5],
        "missing_signals": _list_of_str(au.get("missing_signals"))[:6],
        "actions": [
            {"action": _str(x.get("action")), "channel": _str(x.get("channel")),
             "eta_weeks": _int_or_none(x.get("eta_weeks"))}
            for x in _list_of_dict(au.get("actions"))[:5] if _str(x.get("action"))
        ],
    } if au else None

    # Chapter 11 -- current in the six-metric predictions is always overwritten with measured values
    oa = _obj("overall_assessment")
    overall_assessment = None
    if oa:
        mp = []
        for x in _list_of_dict(oa.get("metric_projection"))[:6]:
            key = _str(x.get("key"))
            if key not in _METRIC_KEYS:
                continue
            actual = _actual_metrics.get(key)
            if actual is None:
                continue     # No prediction for metrics without measurements, to avoid fabricating growth curves
            a30 = _float_or_none(x.get("after_30d"))
            a90 = _float_or_none(x.get("after_90d"))
            if a30 is None or a90 is None:
                continue
            mp.append({"key": key, "current": actual,
                       "after_30d": max(0.0, min(100.0, a30)),
                       "after_90d": max(0.0, min(100.0, a90)),
                       "driver": _str(x.get("driver"))})
        _clamp_rank_projection(mp)
        overall_assessment = {
            "verdict": _str(oa.get("verdict")),
            "stage": _str(oa.get("stage")),
            "benchmark": _str(oa.get("benchmark")),
            "metric_projection": mp,
        }

    return {
        "executive_summary": _str(raw.get("executive_summary")),
        "key_findings": _list_of_dict(raw.get("key_findings"))[:6],
        "risks": _list_of_str(raw.get("risks"))[:6],
        "dimension_notes": dim_notes,
        "personas": _list_of_dict(raw.get("personas"))[:4],
        "competitive_summary": _str(raw.get("competitive_summary")),
        "quick_wins": _list_of_dict(raw.get("quick_wins"))[:4],
        "p1_actions": _list_of_dict(raw.get("p1_actions"))[:4],
        "p2_actions": _list_of_dict(raw.get("p2_actions"))[:4],
        "roadmap": _list_of_dict(raw.get("roadmap"))[:4],
        "score_projection": projection,
        "dimension_projection": dimension_projection,
        "sentiment_summary": sentiment_summary,
        "competitor_profiles": competitor_profiles,
        "brand_strengths": _list_of_str(raw.get("brand_strengths"))[:6],
        "brand_weaknesses": _list_of_str(raw.get("brand_weaknesses"))[:6],
        "scene_intent": scene_intent,
        "metrics_notes": metrics_notes,
        "infrastructure_note": infrastructure_note,
        "authority_note": authority_note,
        "overall_assessment": overall_assessment,
    }
