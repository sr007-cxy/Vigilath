"""Balance charging -- lazy account creation, conditional deduction, idempotent ledger.

**Serves only two things: batch runs and publishing. Audits are not charged** (audits use membership.monthly_check_quota).

Three iron rules (read these before changing anything here):

  1. **Deductions always use "conditional UPDATE + rowcount check"**, never read-modify-write.
     Follows the state-machine optimistic lock at geo/api/rwj_channel.py:724 -- the only real-money path in the repo does it that way.
     The counterexample is quota_service.py:40-71, a lockless read-modify-write that loses updates under concurrency;
     a few extra quota grants are harmless, but a wrong balance is real money -- do not copy it.

  2. **No commit; follow the caller's transaction** (like compliance_audit.record).
     Deduction and ledger write must land in the same commit -- deducting without a ledger entry, or a ledger entry without a deduction, are both incidents.
     ⚠️ The exact opposite of llm_usage.record_llm_call: that one uses a separate session so that "accounting failures do not affect
     business", while here a charge failure **must** affect business. Do not bring that pattern over.

  3. **Every entry needs an idem_key**, backstopped by the UNIQUE index on balance_ledger.
     A duplicate submission hits the unique key -> treated as already processed, not deducted again. SAVEPOINT ensures the balance is rolled back on a key collision,
     so "money deducted but no ledger entry" cannot happen.

All amounts are in **cents**.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from geo.models.balance import (
    ALL_PRICE_KEYS,
    PRICE_SOLUTION,
    DIRECTION_CREDIT,
    DIRECTION_DEBIT,
    KIND_GRANT,
    KIND_RECHARGE,
    KIND_REFUND,
    PRICE_BATCH_CELL,
    PRICE_COMPLIANCE_SCAN,
    PRICE_CONTENT_DOC,
    PRICE_CONTENT_IMAGE,
    PRICE_RETEST_PROBE,
    PRICE_SENTIMENT_DAILY,
    PRICE_SENTIMENT_ONCE,
    WELCOME_GRANT_CENTS,
    BalanceLedgerORM,
    BalancePriceORM,
    UserBalanceORM,
)

log = logging.getLogger(__name__)

# Env fallback for unit prices -- used when the table has no enabled row. Table > env.
# Unit is **credits** (= cents in the DB, 1 yuan = 100 credits, 1:1 no conversion).
# This is only a fallback; the authority is the balance_prices table -- change prices in the table, no release needed.
_PRICE_ENV = {
    PRICE_BATCH_CELL: ("GEO_PRICE_BATCH_CELL", 4),        # One term x one engine, 0.04 yuan
    PRICE_CONTENT_DOC: ("GEO_PRICE_CONTENT_DOC", 300),    # Per article, 3 yuan
    PRICE_CONTENT_IMAGE: ("GEO_PRICE_CONTENT_IMAGE", 50),       # Per image, 0.5 yuan
    PRICE_COMPLIANCE_SCAN: ("GEO_PRICE_COMPLIANCE_SCAN", 50),   # Per article compliance scan, 0.5 yuan
    PRICE_RETEST_PROBE: ("GEO_PRICE_RETEST_PROBE", 20),         # Per re-test probe, 0.2 yuan
    PRICE_SENTIMENT_ONCE: ("GEO_PRICE_SENTIMENT_ONCE", 1000),   # Per sentiment monitoring quick check, 10 yuan
    PRICE_SENTIMENT_DAILY: ("GEO_PRICE_SENTIMENT_DAILY", 500),  # Daily monitoring per day, 5 yuan
    # Per GEO strategy optimization plan, 10 yuan. Same tier as "sentiment quick check": both are "one heavy job producing one report".
    # This is a **conservative placeholder** -- like other unit prices, after launch adjust it in the admin price table against real cost
    # from llm_call_log, no code change needed.
    PRICE_SOLUTION: ("GEO_PRICE_SOLUTION", 1000),
}

_BJ = timezone(timedelta(hours=8))

# -- Spend source marker --------------------------------------------------------
# `note_json.via` is the single criterion for "which path this money was spent through": the agent's daily amount /
# daily count gates count by it, and later reconciliation uses it to tell "spent in chat" from "spent on the web".
#
# ★ Why a contextvar instead of a parameter on every charge() call: the places that actually spend money
#   (rwj_channel._publish_one, content_generator, sentiment_pipeline) do not know
#   who triggered them -- the same function runs the same line whether clicked on the web or called by the agent.
#   Letting the trigger side wrap a context at its own layer is far more reliable than threading via through a dozen layers.
#
# ⚠️ This was once **inert**: spend.py counted by via='agent', but nothing in the repo ever wrote it,
#   so both gates always returned 0 and never blocked anything. (Similar precedent: commit 4f717dd0
#   "count source empty, gate is inert".) When adding a gate, also confirm the count source is actually written.
_VIA: ContextVar[Optional[str]] = ContextVar("balance_via", default=None)


@contextmanager
def via(tag: Optional[str]):
    """Charges / credits made within this context carry via=tag in their note."""
    token = _VIA.set(tag or None)
    try:
        yield
    finally:
        _VIA.reset(token)


def current_via() -> Optional[str]:
    return _VIA.get()


def _note_json(note: Optional[dict]) -> Optional[str]:
    stamped = _stamp(note)
    return json.dumps(stamped, ensure_ascii=False) if stamped else None


def _stamp(note: Optional[dict]) -> Optional[dict]:
    """Stamp the current via into note. Does not override when the caller explicitly provides via."""
    tag = _VIA.get()
    if not tag:
        return note
    out = dict(note or {})
    out.setdefault("via", tag)
    return out


def _bj_date(now: Optional[datetime] = None) -> str:
    """Beijing date, computed at write time. Same metric definition as llm_call_log.bj_date (UTC+8)."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BJ).strftime("%Y-%m-%d")


# -- Results --------------------------------------------------------------------
OK = "ok"
INSUFFICIENT = "insufficient"      # Insufficient balance
DUPLICATE = "duplicate"            # Same idem_key already processed


@dataclass(frozen=True)
class BalanceResult:
    status: str                    # OK | INSUFFICIENT | DUPLICATE
    balance_cents: int = 0         # Balance after the operation (current value for DUPLICATE)
    amount_cents: int = 0
    ledger_id: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def charged(self) -> bool:
        """Money actually moved. DUPLICATE means it was charged before, which also counts as "paid" for the caller."""
        return self.status in (OK, DUPLICATE)


class _Insufficient(Exception):
    """Internal signal used to trigger SAVEPOINT rollback."""


# -- Unit prices ----------------------------------------------------------------
def get_price(db: Session, key: str) -> int:
    """Get the unit price (cents). Enabled rows in the table take priority, otherwise the env fallback.

    Unit prices live in config rather than code: after launch just edit the table in the admin, no code change or restart.
    """
    if key not in ALL_PRICE_KEYS:
        raise ValueError(f"unknown price key: {key}")
    row = db.get(BalancePriceORM, key)
    if row is not None and row.enabled:
        return int(row.price_cents or 0)
    env_name, default = _PRICE_ENV[key]
    try:
        return max(0, int(os.environ.get(env_name, default)))
    except (TypeError, ValueError):
        return int(default)


# -- Accounts -------------------------------------------------------------------
def get_or_create_account(db: Session, user_id: int) -> UserBalanceORM:
    """Get the account, creating the row and granting the sign-up bonus if missing. **Does not commit**.

    Why lazy granting instead of hooking into registration: there are 4 registration entry points (api/auth.py, api/oauth.py,
    api/admin_users.py, api/ai_telemetry.py), so hooking in would inevitably miss one; existing users would also need a backfill migration.
    Lazy granting unifies new and old users naturally, and zombie accounts that never logged in get no free money.
    """
    row = db.get(UserBalanceORM, user_id)
    if row is not None:
        return row

    now = datetime.utcnow()
    try:
        with db.begin_nested():             # SAVEPOINT: on a concurrent primary key collision only this small section rolls back
            row = UserBalanceORM(
                user_id=user_id,
                balance_cents=WELCOME_GRANT_CENTS,
                granted_cents=WELCOME_GRANT_CENTS,
                recharged_cents=0,
                spent_cents=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.add(BalanceLedgerORM(
                user_id=user_id,
                created_at=now,
                bj_date=_bj_date(now),
                direction=DIRECTION_CREDIT,
                kind=KIND_GRANT,
                amount_cents=WELCOME_GRANT_CENTS,
                balance_after_cents=WELCOME_GRANT_CENTS,
                ref_type="grant",
                ref_id=str(user_id),
                idem_key=f"grant:{user_id}",
                note_json=json.dumps({"说明": "注册赠送"}, ensure_ascii=False),
            ))
            db.flush()                       # autoflush=False, the unique constraint check must be triggered explicitly
    except IntegrityError:
        # Another concurrent request created it first -- just read it back
        db.expire_all()
        row = db.get(UserBalanceORM, user_id)
        if row is None:                      # Unreachable in theory
            raise
    return row


def get_balance(db: Session, user_id: int) -> int:
    return int(get_or_create_account(db, user_id).balance_cents or 0)


# -- Deduction ------------------------------------------------------------------
def charge(
    db: Session,
    *,
    user_id: int,
    kind: str,
    amount_cents: int,
    idem_key: str,
    topic_id: Optional[int] = None,
    ref_type: str = "",
    ref_id: str = "",
    trace_id: str = "",
    note: Optional[dict] = None,
) -> BalanceResult:
    """Deduct + write ledger, atomically. **Does not commit; the caller commits.**

    amount_cents <= 0 counts as success directly (free item), no ledger entry.
    """
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        return BalanceResult(status=OK, balance_cents=get_balance(db, user_id),
                             amount_cents=0)

    # Idempotency precheck: querying once first avoids most unnecessary SAVEPOINT overhead
    hit = db.query(BalanceLedgerORM).filter(
        BalanceLedgerORM.idem_key == idem_key).first()
    if hit is not None:
        return BalanceResult(status=DUPLICATE, amount_cents=amount_cents,
                             balance_cents=int(hit.balance_after_cents or 0),
                             ledger_id=hit.id)

    get_or_create_account(db, user_id)
    now = datetime.utcnow()

    try:
        with db.begin_nested():             # SAVEPOINT: deduction and ledger live and die together
            # ★ Conditional UPDATE: rowcount=0 when balance is insufficient, never goes negative.
            #   Same shape as rwj_channel.py:724, never read-modify-write.
            res = db.execute(
                text("UPDATE user_balances "
                     "SET balance_cents = balance_cents - :amt, "
                     "    spent_cents = spent_cents + :amt, "
                     "    updated_at = :now "
                     "WHERE user_id = :uid AND balance_cents >= :amt"),
                {"amt": amount_cents, "uid": user_id, "now": now},
            )
            if res.rowcount != 1:
                raise _Insufficient()

            after = db.execute(
                text("SELECT balance_cents FROM user_balances WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()

            row = BalanceLedgerORM(
                user_id=user_id,
                created_at=now,
                bj_date=_bj_date(now),
                direction=DIRECTION_DEBIT,
                kind=kind,
                amount_cents=amount_cents,
                balance_after_cents=int(after or 0),
                topic_id=topic_id,
                ref_type=ref_type,
                ref_id=str(ref_id or ""),
                trace_id=trace_id or "",
                idem_key=idem_key,
                note_json=_note_json(note),
            )
            db.add(row)
            db.flush()
    except _Insufficient:
        return BalanceResult(status=INSUFFICIENT, amount_cents=amount_cents,
                             balance_cents=get_balance(db, user_id))
    except IntegrityError:
        # Collided with a concurrent request on the same idem_key -- the SAVEPOINT already rolled back the deduction too
        db.expire_all()
        hit = db.query(BalanceLedgerORM).filter(
            BalanceLedgerORM.idem_key == idem_key).first()
        return BalanceResult(status=DUPLICATE, amount_cents=amount_cents,
                             balance_cents=int(hit.balance_after_cents or 0) if hit else 0,
                             ledger_id=hit.id if hit else None)

    return BalanceResult(status=OK, amount_cents=amount_cents,
                         balance_cents=int(after or 0), ledger_id=row.id)


def credit(
    db: Session,
    *,
    user_id: int,
    kind: str,
    amount_cents: int,
    idem_key: str,
    ref_type: str = "",
    ref_id: str = "",
    note: Optional[dict] = None,
) -> BalanceResult:
    """Credit (top-up / refund / manual adjustment). **Does not commit; the caller commits.**

    Top-up idem_key is f"wxpay:{out_trade_no}" -- the WeChat callback and frontend polling may arrive at the same time;
    the existing _fulfill_if_needed is lockless check-then-act; a duplicate membership upgrade only gives a few extra days,
    but **a duplicate balance credit is real money**, so the unique key must catch it here.
    """
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        return BalanceResult(status=OK, balance_cents=get_balance(db, user_id))

    hit = db.query(BalanceLedgerORM).filter(
        BalanceLedgerORM.idem_key == idem_key).first()
    if hit is not None:
        return BalanceResult(status=DUPLICATE, amount_cents=amount_cents,
                             balance_cents=int(hit.balance_after_cents or 0),
                             ledger_id=hit.id)

    get_or_create_account(db, user_id)
    now = datetime.utcnow()
    col = {KIND_RECHARGE: "recharged_cents", KIND_GRANT: "granted_cents"}.get(kind)

    try:
        with db.begin_nested():
            extra = f", {col} = {col} + :amt" if col else ""
            res = db.execute(
                text(f"UPDATE user_balances "
                     f"SET balance_cents = balance_cents + :amt{extra}, "
                     f"    updated_at = :now "
                     f"WHERE user_id = :uid"),
                {"amt": amount_cents, "uid": user_id, "now": now},
            )
            if res.rowcount != 1:
                raise _Insufficient()        # Account missing, unreachable in theory (just created above)

            after = db.execute(
                text("SELECT balance_cents FROM user_balances WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()

            row = BalanceLedgerORM(
                user_id=user_id,
                created_at=now,
                bj_date=_bj_date(now),
                direction=DIRECTION_CREDIT,
                kind=kind,
                amount_cents=amount_cents,
                balance_after_cents=int(after or 0),
                ref_type=ref_type,
                ref_id=str(ref_id or ""),
                idem_key=idem_key,
                note_json=_note_json(note),
            )
            db.add(row)
            db.flush()
    except _Insufficient:
        return BalanceResult(status=INSUFFICIENT, amount_cents=amount_cents)
    except IntegrityError:
        db.expire_all()
        hit = db.query(BalanceLedgerORM).filter(
            BalanceLedgerORM.idem_key == idem_key).first()
        return BalanceResult(status=DUPLICATE, amount_cents=amount_cents,
                             balance_cents=int(hit.balance_after_cents or 0) if hit else 0,
                             ledger_id=hit.id if hit else None)

    return BalanceResult(status=OK, amount_cents=amount_cents,
                         balance_cents=int(after or 0), ledger_id=row.id)


# -- Precheck (no deduction, for "is there enough to run") ------------------------------------
def can_afford(db: Session, user_id: int, amount_cents: int) -> bool:
    """Is there enough. For prechecks before batch run dispatch and before starting a content batch -- avoids half-finished work from running out of money midway."""
    if amount_cents <= 0:
        return True
    return get_balance(db, user_id) >= int(amount_cents)


def estimate_batch_cents(db: Session, cells: int) -> int:
    """Cost of one batch run. cells = number of prompts x number of engines."""
    return max(0, int(cells)) * get_price(db, PRICE_BATCH_CELL)


def estimate_docs_cents(db: Session, docs: int) -> int:
    """Cost of a batch of articles."""
    return max(0, int(docs)) * get_price(db, PRICE_CONTENT_DOC)


def estimate_images_cents(db: Session, images: int) -> int:
    """Cost of a batch of images."""
    return max(0, int(images)) * get_price(db, PRICE_CONTENT_IMAGE)


def estimate_compliance_cents(db: Session, docs: int) -> int:
    """Cost of a batch of compliance scans."""
    return max(0, int(docs)) * get_price(db, PRICE_COMPLIANCE_SCAN)


def sentiment_once_cents(db: Session) -> int:
    """Credits for one sentiment monitoring quick check."""
    return get_price(db, PRICE_SENTIMENT_ONCE)


def sentiment_daily_cents(db: Session) -> int:
    """Credits per day of daily monitoring (charged only once per account per Beijing date)."""
    return get_price(db, PRICE_SENTIMENT_DAILY)


def spent_today_cents(
    db: Session,
    user_id: int,
    *,
    kind: Optional[str] = None,
    via: Optional[str] = None,
) -> int:
    """Net spend today (Beijing date) -- the single criterion for the daily cap.

    Net metric definition: debits minus refunds **pointing to the same entry**. Summing only debits would count "charged then refunded"
    as spent -- two clicks, both failing and refunded, would eat the user's daily quota for nothing.
    Refund rows have kind KIND_REFUND (not the original kind), so they are paired by (ref_type, ref_id),
    which also avoids subtracting refunds of other categories when filtering by kind.

    via filters by source (money spent by the agent is tagged 'agent' in note_json.via). Uses LIKE instead of JSON
    functions -- SQLite and PG JSON syntax differ, and only an existence check is needed here.
    """
    day = _bj_date()
    q = db.query(BalanceLedgerORM).filter(
        BalanceLedgerORM.user_id == user_id,
        BalanceLedgerORM.bj_date == day,
    )
    if via:
        q = q.filter(BalanceLedgerORM.note_json.like(f'%"via": "{via}"%'))

    rows = q.all()
    charged: dict[tuple, int] = {}
    for row in rows:
        if row.direction != DIRECTION_DEBIT:
            continue
        if kind and row.kind != kind:
            continue
        key = (row.ref_type or "", row.ref_id or "")
        charged[key] = charged.get(key, 0) + int(row.amount_cents or 0)

    total = sum(charged.values())
    for row in rows:
        if row.direction != DIRECTION_CREDIT or row.kind != KIND_REFUND:
            continue
        key = (row.ref_type or "", row.ref_id or "")
        if key in charged:
            total -= int(row.amount_cents or 0)
    return max(0, total)
