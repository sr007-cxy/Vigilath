"""User balance billing — three tables: accounts / ledger / unit prices.

**Billing covers only two things: batch runs (by prompts × engines) and publishing (per article). Audits are not charged**;
audits keep using the membership.monthly_check_quota quota; the two systems coexist, each managing its own.

Why not charge by llm_call_log (this is the pivot of the whole design; read before changing):
  1. Batch-run engine calls through the browser-service account pool **never enter llm_call_log at all** — telemetry
     only records the two API paths Doubao ARK and OpenRouter proxy answers, so batch-run cost computed from it would be severely underestimated;
  2. llm_call_log.user_id is almost always NULL (the only place in the repo that really passes user_id is agent chat);
  3. llm_call_log is meant for cost observation, explicitly "accounting failures never affect business and never reuse the caller's session",
     whereas charging must share the business transaction and be reliable — the requirements are opposite.
So billing is by **business events**; llm_call_log keeps its original role and is only joined via trace_id afterwards to validate pricing.

Amounts are always stored as Integer **cents** (consistent with the repo-wide amount_cents / cost_cents convention), 10 yuan = 1000.

No foreign keys: same as llm_usage — telemetry-service is deployed independently, connects to the same DB, and has its own
mirror implementation writing these tables directly; FKs would let deletes on the main DB's users / topics block its writes.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from geo.database import Base

# ── Ledger direction ────────────────────────────────────────────────────────────
DIRECTION_CREDIT = "credit"    # Incoming: bonus / top-up / refund
DIRECTION_DEBIT = "debit"      # Outgoing: batch run / publishing

# ── Ledger kinds ────────────────────────────────────────────────────────────
KIND_GRANT = "grant"           # Signup bonus (lazily granted)
KIND_RECHARGE = "recharge"     # WeChat top-up
KIND_REFUND = "refund"         # Refund
KIND_ADJUST = "adjust"         # Manual adjustment (back office)
KIND_BATCH = "batch"           # Batch run: one entry per run_topic_once
# One-click full diagnosis: one entry per report. **Must be separate from KIND_BATCH** — diagnosis is also charged at the batch_cell unit price
# (cell = term × engine), but it is not a batch run: batch runs are by default still in the shadow period (GEO_BATCH_CHARGE_MODE=shadow)
# and charge nothing, so under the "monitoring batch run" label **only diagnosis entries exist**. Users see several
# "monitoring batch run" entries in their spending details but cannot find any batch run in task monitoring — the label does not match, not the money.
KIND_FULL_DIAGNOSIS = "full_diagnosis"
# GEO strategy optimization plan: one entry per plan. **Does not reuse KIND_FULL_DIAGNOSIS** — the comment above records
# the lesson of shared labels: users see "one-click full diagnosis" in spending details but cannot find the matching report in diagnosis records,
# the label does not match. The plan is a different deliverable, so it gets its own label.
KIND_SOLUTION = "solution"
KIND_CONTENT_DOC = "content_doc"  # Publishing: one entry per article
KIND_CONTENT_IMAGE = "content_image"    # Images: one entry per image
KIND_COMPLIANCE = "compliance"          # Compliance scan: one entry per article
KIND_PUBLISH_MEDIA = "publish_media"    # Paid media placement: one entry per placement task
KIND_RETEST = "retest"                  # Citation retest probe service fee
KIND_SENTIMENT_ONCE = "sentiment_once"    # Sentiment quick check: one entry per order
KIND_SENTIMENT_DAILY = "sentiment_daily"  # Daily sentiment monitoring: one entry per account per Beijing day

CREDIT_KINDS = (KIND_GRANT, KIND_RECHARGE, KIND_REFUND)
DEBIT_KINDS = (
    KIND_BATCH, KIND_FULL_DIAGNOSIS, KIND_SOLUTION,
    KIND_CONTENT_DOC, KIND_CONTENT_IMAGE,
    KIND_COMPLIANCE, KIND_PUBLISH_MEDIA, KIND_RETEST,
    KIND_SENTIMENT_ONCE, KIND_SENTIMENT_DAILY,
)
ALL_KINDS = CREDIT_KINDS + DEBIT_KINDS + (KIND_ADJUST,)

# ── Unit price keys ────────────────────────────────────────────────────────────
PRICE_BATCH_CELL = "batch_cell"      # Unit price per "one prompt × one engine"
PRICE_CONTENT_DOC = "content_doc"    # Unit price per draft
PRICE_CONTENT_IMAGE = "content_image"        # Per image
PRICE_COMPLIANCE_SCAN = "compliance_scan"    # Per compliance scan of an article
PRICE_RETEST_PROBE = "retest_probe"          # Per retest probe
# The two sentiment tiers are priced separately: the quick check is a one-off heavy job "give a name and run a full web round", while daily monitoring is a subscription-style
# daily round — applying the quick-check price to daily monitoring would make one brand per month cost more than the most expensive tier.
PRICE_SENTIMENT_ONCE = "sentiment_once"      # Per sentiment quick check
PRICE_SENTIMENT_DAILY = "sentiment_daily"    # Daily monitoring: per account per Beijing day
PRICE_SOLUTION = "solution"                  # Per GEO strategy optimization plan
ALL_PRICE_KEYS = (
    PRICE_BATCH_CELL, PRICE_CONTENT_DOC, PRICE_CONTENT_IMAGE,
    PRICE_COMPLIANCE_SCAN, PRICE_RETEST_PROBE,
    PRICE_SENTIMENT_ONCE, PRICE_SENTIMENT_DAILY, PRICE_SOLUTION,
)

# Signup bonus. **1 yuan = 100 credits = 100 cents**, so this number is both "cents" and "credits":
# 1000 = 10 yuan = 1000 credits.
WELCOME_GRANT_CENTS = 2000   # Signup bonus (cents). New field-promotion users need enough for one full diagnosis
                             # (15 terms × 5 engines × 20 = 1500), with margin up to 2000.


class UserBalanceORM(Base):
    """Account — one row per user.

    balance_cents is the single authoritative balance; the three cumulative columns are only for display and reconciliation, not decisions.
    Charges always use "conditional UPDATE + rowcount check" (see services/balance_service.py),
    **never read-modify-write** — quota_service's lock-free approach loses updates under concurrency.
    """

    __tablename__ = "user_balances"

    user_id = Column(Integer, primary_key=True)          # No FK, see module docstring

    balance_cents = Column(Integer, nullable=False, default=0)      # Current balance, the single authority
    granted_cents = Column(Integer, nullable=False, default=0)      # Cumulative bonuses
    recharged_cents = Column(Integer, nullable=False, default=0)    # Cumulative top-ups
    spent_cents = Column(Integer, nullable=False, default=0)        # Cumulative spending

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BalanceLedgerORM(Base):
    """Ledger — one row per entry, append-only.

    idem_key is unique: every charge/credit must carry one; the UNIQUE constraint guards against duplicate submissions.
      batch run  cell:{run_id}
      publishing doc:{doc_id}:{attempt_no}   ← includes attempt: the definition is "charge for every real generation",
                                          requeued regeneration must create a new entry; a bare doc_id would charge only once
      top-up     wxpay:{out_trade_no}
      bonus      grant:{user_id}

    balance_after_cents records the balance after the charge; reconciliation scripts recompute entry by entry from it.
    trace_id reuses llm_usage's usage_ctx trace_id — it can join "which model calls this charge corresponds to",
    the basis for P5 pricing calibration (charges vs real cost).
    """

    __tablename__ = "balance_ledger"
    __table_args__ = (
        Index("idx_bal_ledger_user_created", "user_id", "created_at"),
        Index("idx_bal_ledger_day", "bj_date"),
        Index("idx_bal_ledger_trace", "trace_id"),
        Index("idx_bal_ledger_idem", "idem_key", unique=True),
    )

    # SQLite's rowid alias only recognizes INTEGER PRIMARY KEY; BIGINT would lose autoincrement
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)

    user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)   # UTC
    # Beijing date, **computed at write time** — DB timezone functions differ between SQLite / PG at query time;
    # same definition as llm_call_log.bj_date (UTC+8)
    bj_date = Column(String(10), nullable=False)

    direction = Column(String(8), nullable=False)      # credit | debit
    kind = Column(String(24), nullable=False)          # One of ALL_KINDS
    amount_cents = Column(Integer, nullable=False)     # Always positive; see direction for the sign
    balance_after_cents = Column(Integer, nullable=False)

    topic_id = Column(Integer, nullable=True)
    ref_type = Column(String(16), nullable=False, default="")   # doc|run|payment
    ref_id = Column(String(64), nullable=False, default="")
    trace_id = Column(String(64), nullable=False, default="")

    idem_key = Column(String(128), nullable=False)
    # Billing details, shown to users. Batch runs record {question count, engine count, unit price}; publishing records {unit price, trigger source}.
    # **Never store body text or keys**
    note_json = Column(Text, nullable=True)


class BalancePriceORM(Base):
    """Unit price table — editable in the back office, no code change or restart.

    Price priority: enabled rows in this table > env fallback (GEO_PRICE_BATCH_CELL / GEO_PRICE_CONTENT_DOC).
    Start with conservative values; adjust after P5 compares against real cost using llm_call_log.
    """

    __tablename__ = "balance_prices"

    key = Column(String(32), primary_key=True)          # One of ALL_PRICE_KEYS
    price_cents = Column(Integer, nullable=False, default=0)
    enabled = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_by_id = Column(Integer, nullable=True)
