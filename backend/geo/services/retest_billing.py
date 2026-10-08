"""Post-paid settlement for citation re-tests -- the service fee is charged only **after the probe is actually enqueued**.

**Why not charge at registration** (the entire reason this module exists):
`retest_service.schedule_for_doc` only books an appointment `GEO_RETEST_DELAY_DAYS` days out.
When due, the telemetry side first checks whether the topic still runs (the line in `V-networks/worker-center/app/retests.py`
`topic disabled or empty queries/engines` -> `status='cancelled'`).
Self-serve accounts' topics default to tier=weekly but enabled is often off, so a large share of these appointments never happen.

The consequence of charging at registration, from the user's view, is one complete story:
  backfill a published link -> balance immediately drops by a "citation re-test" charge -> nothing happens seven days later -> in the statement
  that charge has no matching re-test record. User feedback verbatim: "credits were deducted automatically without being clearly told,
  and no real re-test was even done."

So it now becomes: **charge only once status is observed leaving scheduled**.
  - enqueued / done -> charge (this round of engine calls really went out)
  - cancelled / still scheduled -> no charge

The idempotency key stays `retest:{probe.id}` -- same key as the old pre-charge, so probes already charged historically
won't be charged a second time here.

Batch run cells are charged per cell separately by `batch_billing` (re-tests reuse the same run row), so here
**only the fixed probe service fee is charged, never a second per-cell charge**.

Entry point: `scripts/settle_batch_billing.py` (same crontab as batch run settlement; no new scheduled task).

⚠️ **The billing start date is this change's safety valve; don't remove it.** At the moment of switching to post-paid, the DB holds
existing probes that "under the old definition should have been charged at registration but actually weren't charged a cent" -- measured before the test host launch:
of 187 `done` probes in 30 days, **not one had a matching ledger entry** (unit price was still 0 at registration / master gate off).
Without this gate, the first tick after switching would back-charge all 187 at once, and users never agreed to a
billing start date already in effect. Same commandment, same reason as `batch_billing.billing_start_date()`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import AiTelemetryTopicORM, GeoRetestProbeORM
from geo.models.balance import KIND_RETEST, PRICE_RETEST_PROBE
from geo.services import balance_service, topic_billing

log = logging.getLogger(__name__)

_BJ = timezone(timedelta(hours=8))

# Only look back this many days of probes. Older ones were either settled long ago (blocked by the idempotency key) or aren't worth chasing.
LOOKBACK_DAYS = 30

# Status allowlist for charging -- only these two mean "the engine calls really went out".
BILLABLE_STATUS = ("enqueued", "done")


def billing_start_date() -> str:
    """Billing start date (Beijing day `YYYY-MM-DD`). **Unset = charge nothing**, only log.

    See the ⚠️ in the module docstring: not charging by default is deliberate. Environments switched to post-paid must set this date explicitly,
    meaning "re-tests from this day on are charged"; earlier existing ones are not retroactive.
    """
    v = (os.environ.get("GEO_RETEST_BILLING_START_DATE", "") or "").strip()
    if not v:
        return ""
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        log.warning("[retest-billing] GEO_RETEST_BILLING_START_DATE=%r is not YYYY-MM-DD, "
                    "treating as unset (no charge)", v)
        return ""
    return v


def _bj_date(dt: datetime) -> str:
    """UTC naive -> Beijing day. Same metric definition as balance_ledger.bj_date."""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


def settle_probes(db: Session, *, now: Optional[datetime] = None) -> dict:
    """Scan enqueued/done probes and charge the service fee. **Commits on its own.**"""
    now = now or datetime.utcnow()
    since = now - timedelta(days=LOOKBACK_DAYS)
    fee = int(balance_service.get_price(db, PRICE_RETEST_PROBE))
    start = billing_start_date()
    stat = {"scanned": 0, "charged": 0, "charged_cents": 0,
            "skipped": 0, "insufficient": 0, "before_start": 0,
            "fee_cents": fee, "start_date": start or None}
    if fee <= 0:
        log.info("[retest-billing] unit price is 0, no charge: %s", stat)
        return stat
    if not start:
        # No start date set = not yet decided when to start charging. **Count only, don't charge**, to get a look at the size first.
        pending = (db.query(GeoRetestProbeORM)
                   .filter(GeoRetestProbeORM.status.in_(BILLABLE_STATUS))
                   .filter(GeoRetestProbeORM.created_at >= since).count())
        stat["scanned"] = pending
        stat["before_start"] = pending
        log.info("[retest-billing] GEO_RETEST_BILLING_START_DATE not set, no charge (%d pending): %s",
                 pending, stat)
        return stat

    rows = (db.query(GeoRetestProbeORM)
            .filter(GeoRetestProbeORM.status.in_(BILLABLE_STATUS))
            .filter(GeoRetestProbeORM.created_at >= since)
            .order_by(GeoRetestProbeORM.id.asc())
            .all())
    for p in rows:
        stat["scanned"] += 1
        # Probes registered before the start date are never charged retroactively -- we weren't actually collecting this fee then
        if _bj_date(p.created_at or now) < start:
            stat["before_start"] += 1
            continue
        topic = db.get(AiTelemetryTopicORM, p.topic_id)
        if topic is None or topic_billing.payer_id(db, topic) is None:
            stat["skipped"] += 1        # topic gone / ops' own topic
            continue
        res = topic_billing.charge(
            db, topic, kind=KIND_RETEST, amount_cents=fee,
            idem_key=f"retest:{p.id}",          # same key as the old pre-charge: already charged won't be charged again
            ref_type="retest_probe", ref_id=str(p.id),
            note={"doc_id": p.doc_id, "probe_status": p.status,
                  "enqueued_at": str(p.enqueued_at or "")[:19]},
        )
        if res is None:
            stat["skipped"] += 1
        elif res.status == balance_service.INSUFFICIENT:
            # A failed post-paid charge doesn't roll back engine calls already made; just record it and move on
            stat["insufficient"] += 1
            log.warning("[retest-billing] probe=%d insufficient balance, owes %d cents", p.id, fee)
        elif res.status == balance_service.OK:
            stat["charged"] += 1
            stat["charged_cents"] += res.amount_cents

    db.commit()
    log.info("[retest-billing] %s", stat)
    return stat


def tick() -> dict:
    """Entry point for crontab / APScheduler: opens its own session and cleans up itself."""
    from geo.database import SessionLocal

    db = SessionLocal()
    try:
        return settle_probes(db)
    finally:
        db.close()
