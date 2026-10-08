"""Post-paid settlement for batch runs -- by closed Beijing dates, one charge per day.

**Why post-paid**: batch runs happen inside telemetry-service, which cannot import
`balance_service` (it declares its own ORM and deliberately does not depend on the backend package). Mirroring the charging iron rules
over there would be the riskiest move in this whole rework, so that side only keeps a read-only gate (`app/billing.py`),
and the real money is settled here.

**Metric definition**: one Q&A (called a cell in code, externally described as "prompt x engine") = "one prompt x one engine" that got a valid answer
(`error IS NULL`); multiple retry rows for the same Q&A count once. **Failures are not charged** (since 2026-09-16, same metric definition as `batch_prepay`; previously
"charged on submission, success or failure"). Burned engine capacity is our cost, not passed on to customers.

**Only settle closed days** (`bj_date < today`). Today is still being written; settling it would miss the second half of the day.

⚠️ `/run-now` does not write to the DB (`telemetry/app/runner.py:run_preview`), so one-click diagnosis,
sentiment monitoring quick checks and trial runs are never scanned by this settler -- no exclusion logic needed; do not get clever and add one.

⚠️ Re-tests reuse the same run row (`telemetry/app/retests.py`), so the cells they produce are also charged here as
`batch_cell`. Therefore `retest_service` **only charges the fixed probe service fee and never charges per cell a second time**.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.models.balance import KIND_BATCH, PRICE_BATCH_CELL
from geo.services import balance_service, topic_billing

log = logging.getLogger(__name__)

_BJ = timezone(timedelta(hours=8))


def mode() -> str:
    """`shadow` = compute only, no deduction, log only; `enforce` = really deduct. Defaults to shadow.

    The rollout order is deliberate: run shadow for a full week, reconcile the estimates here against `llm_call_log` by trace_id,
    then switch to enforce. Batch runs are the site's largest cost item; a wrong estimate means wrong bills in bulk.
    """
    v = (os.environ.get("GEO_BATCH_CHARGE_MODE", "shadow") or "").strip().lower()
    return "enforce" if v == "enforce" else "shadow"


def _bj_today() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def billing_start_date() -> str:
    """Billing start date (Beijing date, `YYYY-MM-DD`). Returns empty string if unset = billing not started yet.

    **This is the safety valve of the whole rollout.** However long the shadow period ran, that many days of cells accumulated in the DB;
    switching straight to enforce would settle the past seven days at once by `_lookback_days()` (default 7) --
    a daily user (20 terms x 5 engines = 100 cells/day x 20 credits) would be charged
    14000 credits in the same second, far above the sign-up bonus. With this gate, no lookback value can reach before the start date.

    We **do not back-charge** the shadow period: we chose not to collect that money (default shadow is by design),
    and users never agreed to an effective billing start date. The page says the same.
    """
    v = (os.environ.get("GEO_BATCH_BILLING_START_DATE", "") or "").strip()
    if not v:
        return ""
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        log.warning("[batch-billing] GEO_BATCH_BILLING_START_DATE=%r is not YYYY-MM-DD, treating as unset", v)
        return ""
    return v


def _lookback_days() -> int:
    """How many days to look back. Default 7 -- if the settler was down for days it can catch up; idempotency keys prevent double charging."""
    try:
        return max(1, int(os.environ.get("GEO_BATCH_SETTLE_LOOKBACK_DAYS", 7)))
    except (TypeError, ValueError):
        return 7


def settle_closed_days(db: Session, *, now: Optional[datetime] = None) -> dict:
    """Scan closed days, aggregate cell counts by (run_id, Beijing date) and charge. **Commits itself.**

    Idempotency key `batch:{run_id}:{bj_date}` -- re-running the same day only hits the unique key, no double charge.
    Note though: if more cells are produced later on an already settled day, that extra part **is not collected**.
    This is a deliberate trade-off -- compared with "per-cell accounting", undercharging a bit is far better than charging the same cell twice.
    """
    # 2026-09-09 -- once prepay is live this must **stand down**: the money was already charged per cell before the run
    # (geo/services/batch_prepay.py); scanning responses again would charge the same cell twice.
    # The two are mutually exclusive; switching only changes env (GEO_BATCH_PREPAY=0 goes back to post-paid).
    from geo.services import batch_prepay

    if batch_prepay.enabled():
        log.info("[batch-billing] prepay is enabled, skipping post-paid settlement (mutually exclusive)")
        return {"days": 0, "cells": 0, "charged_cents": 0, "skipped": 0,
                "insufficient": 0, "before_start": 0, "mode": "prepay",
                "price_cents": balance_service.get_price(db, PRICE_BATCH_CELL),
                "start_date": None}

    today = (now or datetime.now(_BJ)).strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_lookback_days()))
    enforce = mode() == "enforce"
    start = billing_start_date()

    # The responses table has no bj_date column -- derive from UTC created_at (+8h then take the date).
    # Same metric definition as balance_ledger.bj_date.
    rows = db.execute(text("""
        SELECT run_id,
               topic_id,
               strftime('%Y-%m-%d', datetime(created_at, '+8 hours')) AS bj_date,
               COUNT(DISTINCT engine || char(31) || query) AS cells
        FROM ai_telemetry_responses
        WHERE created_at >= :since AND (error IS NULL OR error = '')
        GROUP BY run_id, topic_id, bj_date
    """) if db.bind.dialect.name == "sqlite" else text("""
        SELECT run_id,
               topic_id,
               to_char(created_at + interval '8 hours', 'YYYY-MM-DD') AS bj_date,
               COUNT(DISTINCT (engine, query)) AS cells
        FROM ai_telemetry_responses
        WHERE created_at >= :since AND (error IS NULL OR error = '')
        GROUP BY run_id, topic_id, bj_date
    """), {"since": since}).all()

    price = balance_service.get_price(db, PRICE_BATCH_CELL)
    stat = {"days": 0, "cells": 0, "charged_cents": 0, "skipped": 0,
            "insufficient": 0, "before_start": 0, "mode": mode(),
            "price_cents": price, "start_date": start or None}

    for run_id, topic_id, bj_date, cells in rows:
        if not bj_date or bj_date >= today:
            continue                       # Today is still being written; settling would miss the second half
        if start and bj_date < start:
            stat["before_start"] += 1      # Before billing start date, not back-charged
            continue
        cells = int(cells or 0)
        if cells <= 0:
            continue
        stat["days"] += 1
        stat["cells"] += cells
        amount = price * cells
        if amount <= 0:
            continue

        topic = db.get(AiTelemetryTopicORM, topic_id)
        if topic is None or topic_billing.payer_id(db, topic) is None:
            stat["skipped"] += 1           # Topic gone / operator's own topic
            continue

        if not enforce:
            log.info("[batch-billing:shadow] run=%s topic=%s %s %d cells -> %d cents",
                     run_id, topic_id, bj_date, cells, amount)
            continue

        res = topic_billing.charge(
            db, topic, kind=KIND_BATCH, amount_cents=amount,
            idem_key=f"batch:{run_id}:{bj_date}",
            ref_type="run", ref_id=str(run_id),
            note={"cells": cells, "bj_date": bj_date, "price_cents": price},
        )
        if res is None:
            stat["skipped"] += 1
        elif res.status == balance_service.INSUFFICIENT:
            # Failing to collect post-paid cannot roll back batches already run -- record it, and telemetry's
            # read-only gate blocks this topic on the next tick so it stops burning capacity
            stat["insufficient"] += 1
            log.warning("[batch-billing] topic=%s %s insufficient balance, owes %d cents",
                        topic_id, bj_date, amount)
        elif res.status == balance_service.OK:
            stat["charged_cents"] += res.amount_cents

    db.commit()
    log.info("[batch-billing] %s", stat)
    return stat


def tick() -> dict:
    """Entry point for APScheduler: opens and closes its own session."""
    from geo.database import SessionLocal

    db = SessionLocal()
    try:
        return settle_closed_days(db)
    finally:
        db.close()
