"""**Prepay** for batch runs -- deduct this round's cell cost upfront before running, refund the unrun part on close.

Why it exists (rule set on 2026-09-09):

Batch runs used to be **post-paid** (`batch_billing.settle_closed_days`, settled next day, and still in shadow mode by default
computing without deducting); nothing checked the balance at run start -- `/run-topic` had zero checks, and the only read-only gate
`telemetry/app/billing.has_headroom` was only on drip and off by default. As a result anyone could create a draft
topic, expand a few hundred terms and burn all engine capacity that day without being charged a cent (measured: 849 terms x 5 engines =
4245 cells = 84900 credits, account balance 21000, 1011 cells already burned, 0 charge ledger entries).

So it became: **charge first, then run**.

Metric definition and the shape of money
--------------
· One prepay charge per run. Amount = cells to run this round x `batch_cell` unit price.
  One Q&A = "one prompt x one engine" asked once (called a cell in code).
· **Only charge for delivered Q&As** (decided by the user on 2026-09-16, replacing 09-09's "success or failure both count"): on close,
  Q&As without a valid answer are fully refunded. Failure causes are almost always on our side (refusals, sliders, no accounts,
  physical device hitting a login wall); charging customers who got nothing is indefensible -- topic 31 where Tongyi refused 3 of 12 times
  and still got charged 240 cents is the counterexample. Engine capacity was indeed burned, but that is our cost, not the customer's.
· If the charge fails, **do not create this round**: manual runs return 402, drip skips the topic and alerts. Running out of money halfway is
  much worse than not starting -- engine quota is already burned and the report is still incomplete.
· After the run closes, refund the difference by **delivered cell count** (`refund_finished_runs`): anything not enqueued, or enqueued but not run,
  is refunded. A drip cycle trickles over a week and may skip a batch of terms midway due to backlog protection / deterministic refusals.

Idempotency
----
Prepay `batch_prepay:{run_id}`, refund `batch_prepay_refund:{run_id}`, backstopped by
the unique index on `balance_ledger`. A repeated call hitting the key counts as already processed.

Relation to post-paid settlement
------------------
**Mutually exclusive**. When `enabled()` is true, `batch_billing.settle_closed_days` runs as a no-op --
enabling both would charge the same cell twice. Switching only changes env, no release.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from geo.models.balance import KIND_BATCH, PRICE_BATCH_CELL, BalanceLedgerORM
from geo.models.ai_telemetry import AiTelemetryTopicORM, active_query_texts
from geo.services import balance_service, topic_billing

log = logging.getLogger(__name__)

# Three outcomes of a prepay. `skipped` = should not be charged this time anyway (operator topic / master switch off / price 0);
# callers always treat it as allowed -- do not treat it as a failure, or operators' demo topics could not even run.
OK = "ok"
INSUFFICIENT = "insufficient"
SKIPPED = "skipped"


def enabled() -> bool:
    """Prepay master switch. **On by default** -- this is the 2026-09-09 rule, not a gradual rollout experiment.

    Turning it off (`GEO_BATCH_PREPAY=0`) restores post-paid settlement at the same time, so there is never a gap where neither side charges.
    """
    return (os.environ.get("GEO_BATCH_PREPAY", "1") or "").strip().lower() \
        not in ("0", "false", "no", "off")


def cells_for_topic(topic) -> int:
    """Number of Q&As for one full round of this topic = non-soft-deleted prompts x engines.

    Uses `active_query_texts` instead of parsing queries_json ourselves -- soft-deleted terms should not be charged.
    """
    import json as _json

    try:
        engines = _json.loads(getattr(topic, "engines_json", "") or "[]")
    except Exception:  # noqa: BLE001
        engines = []
    if not isinstance(engines, list):
        engines = []
    return len(active_query_texts(topic)) * len(engines)


def pending_cells_for_topic(db: Session, topic) -> int:
    """Number of Q&As when only running cells that "never succeeded" -- the round triggered by "generate" on the plan page is quoted by this.

    Same metric definition as telemetry `app/storage.py:new_pairs` (**intentionally duplicated**; that side is deployed separately
    and cannot import this): only (engine, query) pairs for which this topic never got a valid answer (no response row with empty error)
    count. Failed / refused ones count and will be retried; successful ones do not.
    """
    import json as _json

    queries = active_query_texts(topic)
    try:
        engines = _json.loads(getattr(topic, "engines_json", "") or "[]")
    except Exception:  # noqa: BLE001
        engines = []
    if not isinstance(engines, list) or not queries or not engines:
        return 0
    succeeded = {(r.engine, r.query) for r in db.execute(text(
        "SELECT DISTINCT engine, query FROM ai_telemetry_responses "
        "WHERE topic_id = :tid AND (error IS NULL OR error = '')"
    ), {"tid": int(topic.id)}).all()}
    return sum(1 for q in queries for e in engines if (e, q) not in succeeded)


def quote(db: Session, topic, cells: int) -> dict:
    """Quote only, no charge. For the frontend's "tell me how much before running"."""
    price = balance_service.get_price(db, PRICE_BATCH_CELL)
    cells = max(0, int(cells or 0))
    need = price * cells
    payer = topic_billing.payer_id(db, topic)
    return {
        "cells": cells,
        "price_cents": price,
        "need_cents": need,
        "balance_cents": balance_service.get_balance(db, payer) if payer else 0,
        "chargeable": bool(payer and topic_billing.enabled() and need > 0 and enabled()),
    }


def prepay(db: Session, topic, *, run_id: int, cells: int,
           reason: str = "") -> dict:
    """Charge this round's money before running. **Does not commit**; follows the caller's transaction.

    Returns `{"status": OK|INSUFFICIENT|SKIPPED, ...}`. Only INSUFFICIENT should block the batch run.
    """
    price = balance_service.get_price(db, PRICE_BATCH_CELL)
    cells = max(0, int(cells or 0))
    need = price * cells
    out = {"status": SKIPPED, "cells": cells, "price_cents": price,
           "need_cents": need, "balance_cents": 0, "run_id": int(run_id)}

    if not enabled():
        out["detail"] = "prepay disabled"
        return out

    payer = topic_billing.payer_id(db, topic)
    if payer is not None:
        out["balance_cents"] = balance_service.get_balance(db, payer)

    res = topic_billing.charge(
        db, topic, kind=KIND_BATCH, amount_cents=need,
        idem_key=_prepay_key(run_id),
        ref_type="run", ref_id=str(run_id),
        note={"cells": cells, "price_cents": price, "prepay": True,
              "reason": reason or ""},
    )
    if res is None:                        # Operator topic / master switch off / price 0 -- allow
        return out

    if res.status == balance_service.INSUFFICIENT:
        out["status"] = INSUFFICIENT
        out["balance_cents"] = res.balance_cents
        log.warning("[batch-prepay] topic=%s run=%s insufficient balance: need %d cents, have %d cents, %d cells not started",
                    getattr(topic, "id", None), run_id, need, res.balance_cents, cells)
        return out

    # Both OK and DUPLICATE count as charged -- DUPLICATE means the same run was prepaid twice, money already collected
    out["status"] = OK
    out["balance_cents"] = res.balance_cents
    out["duplicate"] = res.status == balance_service.DUPLICATE
    log.info("[batch-prepay] topic=%s run=%s prepaid %d cents (%d cells x %d), balance %d cents%s",
             getattr(topic, "id", None), run_id, need, cells, price,
             res.balance_cents, "(重复,未再扣)" if out["duplicate"] else "")
    return out


def refund_finished_runs(db: Session, *, lookback_days: int = 3) -> dict:
    """Closed runs: refund the prepay difference by the number of cells actually submitted. **Commits itself.**

    Scans rows that "have a prepay entry, no refund entry, and the run is terminal". Can catch up after being down for days;
    idempotency keys prevent double refunds.

    Why count `ai_telemetry_responses` instead of `dispatch_tasks`: responses are the only proof that
    "it was really asked", consistent with `batch_billing`'s metric definition; tasks that were queued
    but never claimed get their money refunded.
    Only counts **error IS NULL** cells (delivered cells): failed cells are not charged, see module header.
    """
    stat = {"scanned": 0, "refunded": 0, "refunded_cents": 0, "enabled": enabled()}
    if not enabled():
        return stat

    rows = db.execute(text("""
        SELECT l.ref_id, l.note_json, l.amount_cents, l.topic_id
        FROM balance_ledger l
        WHERE l.idem_key LIKE 'batch_prepay:%'
          AND l.direction = 'debit'
          AND l.created_at >= :since
    """), {"since": _since(lookback_days)}).all()

    for ref_id, note_json, amount_cents, topic_id in rows:
        try:
            run_id = int(ref_id)
        except (TypeError, ValueError):
            continue
        stat["scanned"] += 1

        # Already refunded -> skip (does not rely on charge's DUPLICATE, saving a SAVEPOINT)
        if db.query(BalanceLedgerORM.id).filter(
                BalanceLedgerORM.idem_key == _refund_key(run_id)).first():
            continue

        run = db.execute(text(
            "SELECT status FROM ai_telemetry_runs WHERE id = :r"
        ), {"r": run_id}).first()
        if run is None or (run[0] or "") == "running":
            continue                       # Still running; refunding early would require charging again later

        import json as _json
        try:
            note = _json.loads(note_json or "{}")
        except Exception:  # noqa: BLE001
            note = {}
        paid_cells = int(note.get("cells") or 0)
        price = int(note.get("price_cents") or 0)
        if paid_cells <= 0 or price <= 0:
            continue

        # Count by **delivered cells**: one cell = one question x one engine with at least one successful response row.
        # Not row count (dispatch tries a question up to 3 times and each failure writes a row; COUNT(*) would count 17 cells
        # as 51 -- on 09-14~16 topic 31 was net-charged 1020 cents per round with 0 results, three rounds in a row),
        # and not submission count (failed cells are not charged since 2026-09-16).
        actual = int(db.execute(text(
            "SELECT COUNT(*) FROM (SELECT DISTINCT engine, query "
            "FROM ai_telemetry_responses WHERE run_id = :r "
            "AND (error IS NULL OR error = '')) c"
        ), {"r": run_id}).scalar() or 0)
        unused = max(0, paid_cells - actual)
        if unused <= 0:
            continue

        topic = db.get(AiTelemetryTopicORM, topic_id) if topic_id else None
        if topic is None:
            continue
        back = min(unused * price, int(amount_cents or 0))
        topic_billing.refund(
            db, topic, amount_cents=back, idem_key=_refund_key(run_id),
            ref_type="run", ref_id=str(run_id),
            note={"prepay_refund": True, "paid_cells": paid_cells,
                  "actual_cells": actual, "unused_cells": unused,
                  "price_cents": price},
        )
        stat["refunded"] += 1
        stat["refunded_cents"] += back
        log.info("[batch-prepay] run=%s closed: prepaid %d cells, actually ran %d cells, refunding %d cents",
                 run_id, paid_cells, actual, back)

    db.commit()
    if stat["refunded"]:
        log.info("[batch-prepay] refund %s", stat)
    return stat


def tick() -> dict:
    """Entry point for APScheduler: opens and closes its own session."""
    from geo.database import SessionLocal

    db = SessionLocal()
    try:
        return refund_finished_runs(db)
    finally:
        db.close()


def _prepay_key(run_id: int) -> str:
    return f"batch_prepay:{int(run_id)}"


def _refund_key(run_id: int) -> str:
    return f"batch_prepay_refund:{int(run_id)}"


def _since(days: int):
    from datetime import datetime, timedelta

    return datetime.utcnow() - timedelta(days=max(1, int(days)))


def insufficient_message(info: dict) -> str:
    """A human-readable sentence. Saying only "insufficient balance" without the shortfall and current balance leaves users not knowing how much to top up
    (same reasoning as topic_billing.shortfall)."""
    return (f"余额不足,本轮监测未开跑:需预留 {info.get('need_cents', 0)} 积分"
            f"({info.get('cells', 0)} 次问答 × {info.get('price_cents', 0)},只按实际拿到答案的问答结算,"
            f"其余跑完自动退回),当前余额 {info.get('balance_cents', 0)} 积分。充值后可重新发起。")


if __name__ == "__main__":
    # ★ This section must stay at the **end of the file** -- the module executes top to bottom; placed in the middle it would run
    #   before the helper functions below (_since / _prepay_key) are bound, causing a NameError.
    #
    # Why a __main__: the refund hangs off sentiment_scheduler's APScheduler, and that
    # APScheduler only starts with `GEO_SCHEDULER_LEADER=1` -- **neither** CN production nor the test host sets it
    # (the comment in the prod crontab says so). So in production it can only be started by crontab:
    # `run.sh batch_prepay_refund python -m geo.services.batch_prepay`.
    # Without this, cells prepaid but never run could never be refunded -- that is genuinely overcharging users.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("[batch-prepay] %s", tick())
