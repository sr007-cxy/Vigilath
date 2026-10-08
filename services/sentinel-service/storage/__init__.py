"""Storage entrypoint: connect wrapper + re-exports of the db submodule.

Multi-tenant routing (replacing the historical 6 layers of monkey-patching):
- service.py calls `current_account.set(account_id)` at the request entry;
- business submodules write `from storage import connect; conn = connect()`, and the connection they get treats all
  accounts the same - isolation is **not on the connection** but on each SQL statement's `account_id = %s`
  (db.py's `_aid()` reads the same contextvar and raises if it is unset).

Before 2026-09-07 this was "one tenant_{N} schema per account + SET search_path";
now it is one set of tables + an account_id column, for reasons in the db.py header comment. The contextvar itself moved to db.py
(it is now an input for building WHERE clauses; keeping it at the upper layer would create a circular import), and this only re-exports it so
`from storage import current_account` keeps working.
"""
from typing import Optional

from .db import (
    current_account,
    DATABASE_URL,
    DEFAULT_DB_PATH,
    connect as _db_connect,
    init_schema,
    upsert_post,
    upsert_analysis,
    insert_brief,
    brief_exists,
    insert_draft,
    posts_for_symbol,
    posts_missing_analysis,
    analyses_for_symbol,
    analyses_count_for_symbol,
    analyses_for_day,
    analyses_for_month,
    competitor_analyses,
    brand_post_lookup,
    ingest_jsonl_dir,
    get_query_last_run,
    upsert_query_last_run,
    insert_websearch_report,
    latest_websearch_report,
)


def connect(arg=None):
    """Get a PG connection.

    Parameters are kept only for compatibility with historical callers (old code passes account_id or a Path/str db_path):
    connections are no longer split by account, and the search path is pinned to the `sentinel` schema. What actually decides which
    tenant is read/written is the account_id in each SQL statement - taken from the contextvar by db.py's `_aid()`.
    """
    aid: Optional[int] = arg if isinstance(arg, int) else None
    return _db_connect(aid)


__all__ = [
    "DATABASE_URL",
    "DEFAULT_DB_PATH",
    "current_account",
    "connect",
    "init_schema",
    "upsert_post",
    "upsert_analysis",
    "insert_brief",
    "brief_exists",
    "insert_draft",
    "posts_for_symbol",
    "posts_missing_analysis",
    "analyses_for_symbol",
    "analyses_count_for_symbol",
    "analyses_for_day",
    "analyses_for_month",
    "competitor_analyses",
    "brand_post_lookup",
    "ingest_jsonl_dir",
    "get_query_last_run",
    "upsert_query_last_run",
    "insert_websearch_report",
    "latest_websearch_report",
]
