"""Database lock wrappers for the publish claim protocol -- PG / SQLite dialect differences are centralized here.

🔴 **Passing on SQLite does not mean it is correct.**

  · `with_for_update(skip_locked=True)` is **silently ignored** by SQLAlchemy on SQLite --
    no error, just no lock. Concurrency bugs never reproduce locally.
  · `pg_advisory_xact_lock` errors out directly on SQLite, so it must only be issued after checking the dialect.

Production is PostgreSQL (docs/architecture.md:97), and `pg_advisory_xact_lock` is already used in production
(V-networks/worker-center/app/dispatch.py:332). Local development defaults to SQLite, so
F4/F5 concurrency correctness acceptance **must** run on a local PG -- see
`backend/scripts/verify_publish_claim.py`, which actively refuses to run on SQLite.

Written after telemetry-service's existing implementation, which already solved this once (original comment: "advisory xact locks
are released automatically at transaction end; claim is a sub-millisecond DB operation, so serialization overhead is negligible").
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from geo.database import engine as _db_engine

IS_PG = _db_engine.dialect.name == "postgresql"
IS_SQLITE = _db_engine.dialect.name == "sqlite"

# Namespace of account-level advisory locks. Uses PG's two-int4 form pg_advisory_xact_lock(ns, id) instead of
# hashtext('pubacct:' || id): one less string concatenation, one less hash collision surface, and clearer semantics
# (ns constant = "this is a publish account lock", the second argument is the account primary key).
_PUBACCT_LOCK_NS = 0x70756261      # "puba"


def lock_account(db: Session, account_db_id: int) -> None:
    """Take a transaction-level advisory lock on a single publish account -- prevents re-entry across transactions.

    This is the second of the claim protocol's three pieces (the first is FOR UPDATE SKIP LOCKED, the third is
    visible_after backoff). Missing any one of them allows duplicate publishing:

      · Row locks only: two concurrent claims may lock **different queue rows** pointing to **the same account**
        (when one account has multiple tasks), both pass the quota check -> the same account opened by two workers at once;
      · Advisory lock only: the queue row itself could be claimed twice.

    The lock is released automatically at transaction end, no explicit unlock needed; claim is a sub-millisecond DB operation, so serialization overhead is negligible.

    No-op on SQLite -- single writer + table-level locks make it naturally serial, but that also means **the concurrency
    correctness of this path cannot be verified on SQLite**; it is not "verified passing".
    """
    if IS_PG:
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, :id)"),
                   {"ns": _PUBACCT_LOCK_NS, "id": int(account_db_id)})


def assert_concurrency_testable() -> None:
    """Gatekeeper for concurrency correctness tests: raises if not PG.

    Used by backend/scripts/verify_publish_claim.py. It exists to turn the trap of "passed on SQLite
    so it must be right" into an explicit failure (design section 4 P0 item 1: false green).
    """
    if not IS_PG:
        raise RuntimeError(
            f"当前 DATABASE_URL 的方言是 {_db_engine.dialect.name},不是 postgresql。\n"
            "F4/F5 的并发领取验收**必须**跑在 PG 上:with_for_update(skip_locked=True)\n"
            "在 SQLite 上被 SQLAlchemy 静默忽略,并发 bug 永远不复现。\n"
            "请起一个本地 PG 再跑,例如:\n"
            "  docker run -d --name vigilath-pg -e POSTGRES_PASSWORD=dev "
            "-e POSTGRES_DB=appdb -p 5432:5432 postgres:16\n"
            "  export DATABASE_URL='postgresql+psycopg://postgres:dev@localhost:5432/appdb'"
        )
