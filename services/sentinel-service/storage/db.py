"""PostgreSQL storage for posts, LLM analyses, AI briefs and response drafts.

Multi-tenancy implementation: **one set of tables + an account_id column**, all under the fixed schema `sentinel`.

Before 2026-09-07 it was "one PG schema tenant_{N} per account_id", routed via `SET search_path`.
Why it was replaced: the data volume never needed per-tenant schemas, while the cost was real -
  · every request had to CREATE SCHEMA + run 17 DDL statements, all sharing one advisory lock with a constant key;
  · with more accounts there are dozens to hundreds of schemas, and backups, migrations and debugging all have to iterate over them;
  · even asking "how many items did the whole site fetch today" across accounts required a loop.
Isolation now relies on `account_id = %s`, and **every SQL statement must include it** - this is the only tenant boundary;
missing it in one place means customer A reads customer B's data. account_id is taken from the contextvar `current_account`
(set by service.py at the request entry) and raises if unset, never degrading into "query the whole table".

Why `sentinel` instead of `public`: public may still contain same-named tables from an earlier (single-tenant era),
so the new tables live in their own schema with account_id from creation, avoiding a pile of checks for adding columns to existing tables.

Background: this module was originally raw sqlite3, one .db file per account. When switching to PG:
- driver psycopg3, row_factory=dict_row keeps the `row["col"]` syntax unchanged
- `?` → `%s`
- `INSERT OR REPLACE` → `INSERT … ON CONFLICT (...) DO UPDATE SET …`
- `lastrowid` → `RETURNING id` + `fetchone()["id"]`
- `json_extract(col, '$[0]')` -> `(col::jsonb)->>0` (empty string/NULL handled by a CASE fallback)
- `AUTOINCREMENT` → `BIGSERIAL`
"""
from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row


# DATABASE_URL comes from the systemd EnvironmentFile / .env; no DB connection at import time,
# a missing value raises in connect(), so pytest collection does not blow up.
DATABASE_URL = os.environ.get("DATABASE_URL", "")


# Historical alias: the old storage/__init__.py re-exported this name; the new one no longer uses it, kept empty so
# upstream `from storage import DEFAULT_DB_PATH` does not blow up immediately. Can be removed after cleanup.
DEFAULT_DB_PATH: Path | None = None


# The fixed schema shared by all tenants. To rename, change only this (the migration script reads the same constant).
SCHEMA = os.environ.get("SENTINEL_SCHEMA", "sentinel")


# The account_id bound to the current request; set at service.py endpoint entry, reset on exit.
# Moved here from storage/__init__.py - db.py itself now needs it to build WHERE clauses,
# and keeping it at the upper layer would create a db -> __init__ circular import. __init__.py keeps re-exporting this name.
current_account: ContextVar[int | None] = ContextVar("current_account", default=None)


def _aid() -> int:
    """Current tenant id. **Raises if missing** - this is the only tenant boundary.

    Must never degrade into "no WHERE" or account_id=0: then a single call that forgot to set the contextvar
    would return the whole site's data mixed together without any error. Better a 500.
    """
    aid = current_account.get()
    if aid is None:
        raise RuntimeError(
            "current_account 未设置:storage 的每一次读写都必须绑定 account_id"
            "(service.py 的 _account_context / _account_db_context 负责 set)"
        )
    return int(aid)


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS posts (
        account_id    BIGINT NOT NULL,
        source        TEXT NOT NULL,
        post_id       TEXT NOT NULL,
        symbol        TEXT,
        author        TEXT,
        title         TEXT,
        content       TEXT,
        publish_time  TEXT,
        view_count    INTEGER,
        reply_count   INTEGER,
        like_count    INTEGER,
        share_count   INTEGER,
        url           TEXT,
        ingested_at   TEXT NOT NULL,
        -- account_id 必须在主键里:同一条微博/帖子会被两个客户各抓一次,
        -- 旧的 (source, post_id) 主键会让第二个客户的那条被 ON CONFLICT DO NOTHING 丢掉。
        PRIMARY KEY (account_id, source, post_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_posts_acct_symbol ON posts(account_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_posts_acct_pubtime ON posts(account_id, publish_time)",
    """
    CREATE TABLE IF NOT EXISTS analyses (
        account_id          BIGINT NOT NULL,
        source              TEXT NOT NULL,
        post_id             TEXT NOT NULL,
        symbol              TEXT,
        is_relevant         INTEGER NOT NULL,
        filter_reason       TEXT,
        summary             TEXT,
        sentiment_label     TEXT,
        sentiment_score     DOUBLE PRECISION,
        emotions_json       TEXT,
        topics_json         TEXT,
        entities_json       TEXT,
        stance              TEXT,
        intent              TEXT,
        factuality          TEXT,
        risk_level          TEXT,
        risk_signals_json   TEXT,
        influence_potential DOUBLE PRECISION,
        hidden_meaning      TEXT,
        citations_json      TEXT,
        reasoning           TEXT,
        model               TEXT NOT NULL,
        analyzed_at         TEXT NOT NULL,
        PRIMARY KEY (account_id, source, post_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_analyses_acct_symbol ON analyses(account_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_analyses_acct_risk ON analyses(account_id, risk_level)",
    "CREATE INDEX IF NOT EXISTS idx_analyses_acct_sentiment ON analyses(account_id, sentiment_label)",
    """
    CREATE TABLE IF NOT EXISTS briefs (
        id            BIGSERIAL PRIMARY KEY,
        account_id    BIGINT NOT NULL,
        symbol        TEXT NOT NULL,
        date          TEXT NOT NULL,
        kind          TEXT NOT NULL DEFAULT 'daily',
        body          TEXT NOT NULL,
        model         TEXT NOT NULL,
        generated_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_briefs_acct_symbol_date ON briefs(account_id, symbol, date)",
    """
    CREATE TABLE IF NOT EXISTS drafts (
        id                BIGSERIAL PRIMARY KEY,
        account_id        BIGINT NOT NULL,
        symbol            TEXT NOT NULL,
        source            TEXT,
        post_id           TEXT,
        topic             TEXT,
        variant           TEXT NOT NULL,
        body              TEXT NOT NULL,
        rationale         TEXT,
        predicted_effect  TEXT,
        cautions          TEXT,
        model             TEXT NOT NULL,
        generated_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_drafts_acct_symbol ON drafts(account_id, symbol)",
    """
    CREATE TABLE IF NOT EXISTS query_runs (
        account_id    BIGINT NOT NULL,
        symbol        TEXT NOT NULL,
        query         TEXT NOT NULL,
        last_run_at   TEXT NOT NULL,
        PRIMARY KEY (account_id, symbol, query)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS websearch_reports (
        id                   BIGSERIAL PRIMARY KEY,
        account_id           BIGINT NOT NULL,
        provider             TEXT NOT NULL DEFAULT 'yuanbao',
        target               TEXT NOT NULL,
        ticker               TEXT NOT NULL,
        generated_at         TEXT NOT NULL,
        query_count          INTEGER NOT NULL DEFAULT 0,
        valid_results        INTEGER NOT NULL DEFAULT 0,
        risk_level           TEXT NOT NULL DEFAULT 'none',
        sentiment_label      TEXT NOT NULL DEFAULT 'unknown',
        sentiment_score      DOUBLE PRECISION NOT NULL DEFAULT 0,
        summary              TEXT NOT NULL DEFAULT '',
        llm_model            TEXT,
        positive_points_json TEXT NOT NULL DEFAULT '[]',
        negative_points_json TEXT NOT NULL DEFAULT '[]',
        risks_json           TEXT NOT NULL DEFAULT '[]',
        suggestions_json     TEXT NOT NULL DEFAULT '[]',
        sources_json         TEXT NOT NULL DEFAULT '[]',
        queries_json         TEXT NOT NULL DEFAULT '[]',
        raw_rows_json        TEXT NOT NULL DEFAULT '[]',
        created_at           TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_websearch_acct_ticker_generated "
    "ON websearch_reports(account_id, ticker, generated_at DESC)",
)


# Creating tables only needs to happen once per process - tables are shared globally, no longer created per account.
_SCHEMA_READY = False
_SCHEMA_READY_LOCK = threading.Lock()


def connect(account_id: int | None = None) -> psycopg.Connection:
    """Get a PG connection and pin search_path to the fixed `sentinel` schema.

    The `account_id` parameter is **kept only for old callers and no longer affects the connection** - tenant isolation has moved from
    "one schema per account" to "every SQL statement carries account_id", and the connection itself treats all accounts the same.
    The real account_id is taken from the contextvar inside each query function (see `_aid`).

    DATABASE_URL must be set in env, otherwise raises.
    """
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL 未设置.systemd unit / .env 里加 "
            "DATABASE_URL=postgresql://appuser:***@host:port/appdb"
        )
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    with conn.cursor() as cur:
        # An unqualified `SELECT * FROM posts` resolves to sentinel.posts; public stays as a fallback
        # (old tables, if still present, will not be written by mistake - our tables are all under sentinel).
        cur.execute(f'SET search_path TO "{SCHEMA}", public')
    conn.commit()
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    """Create all business tables (idempotent), **actually run only once per process**.

    Previously every request ran 17 DDL statements here, wrapped in a transaction-level advisory lock with a constant key -
    all tenants and all parallel crawlers queued on that one lock. Now the tables are one global set; once created it is remembered in the process
    and later calls return immediately. A process restart clears it, so columns/indexes added in new versions are still applied after restart.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with conn.cursor() as cur:
        # Concurrent first creation can still collide (multiple workers cold-starting at once), so keep the advisory lock to serialize.
        # After acquiring the lock the DDL is idempotent; being a bit slow is fine - each process does it only once in its lifetime.
        cur.execute("SELECT pg_advisory_xact_lock(727274)")
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        cur.execute(f'SET search_path TO "{SCHEMA}", public')
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        # Historical ADD COLUMN compatibility: posts later gained like_count / share_count
        cur.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS like_count INTEGER")
        cur.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS share_count INTEGER")
        # briefs later gained kind (daily/monthly): distinguishes daily briefs from monthly summaries; old rows default to daily
        cur.execute("ALTER TABLE briefs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'daily'")
    conn.commit()

    with _SCHEMA_READY_LOCK:
        _SCHEMA_READY = True


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_post(conn: psycopg.Connection, rec: dict) -> bool:
    """Insert post if new; return True if inserted, False if it already existed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO posts (account_id, source, post_id, symbol, author, title, content,
                               publish_time, view_count, reply_count,
                               like_count, share_count,
                               url, ingested_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (account_id, source, post_id) DO NOTHING
            """,
            (
                _aid(),
                rec.get("source"),
                str(rec.get("post_id")),
                rec.get("symbol"),
                rec.get("author"),
                rec.get("title"),
                rec.get("content"),
                rec.get("publish_time"),
                rec.get("view_count"),
                rec.get("reply_count"),
                rec.get("like_count"),
                rec.get("share_count"),
                rec.get("url"),
                _now(),
            ),
        )
        return cur.rowcount == 1


def upsert_analysis(conn: psycopg.Connection, source: str, post_id: str,
                    symbol: str | None, analysis: dict, model: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analyses (
                account_id, source, post_id, symbol,
                is_relevant, filter_reason,
                summary, sentiment_label, sentiment_score,
                emotions_json, topics_json, entities_json,
                stance, intent, factuality,
                risk_level, risk_signals_json, influence_potential,
                hidden_meaning, citations_json, reasoning,
                model, analyzed_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (account_id, source, post_id) DO UPDATE SET
                symbol              = EXCLUDED.symbol,
                is_relevant         = EXCLUDED.is_relevant,
                filter_reason       = EXCLUDED.filter_reason,
                summary             = EXCLUDED.summary,
                sentiment_label     = EXCLUDED.sentiment_label,
                sentiment_score     = EXCLUDED.sentiment_score,
                emotions_json       = EXCLUDED.emotions_json,
                topics_json         = EXCLUDED.topics_json,
                entities_json       = EXCLUDED.entities_json,
                stance              = EXCLUDED.stance,
                intent              = EXCLUDED.intent,
                factuality          = EXCLUDED.factuality,
                risk_level          = EXCLUDED.risk_level,
                risk_signals_json   = EXCLUDED.risk_signals_json,
                influence_potential = EXCLUDED.influence_potential,
                hidden_meaning      = EXCLUDED.hidden_meaning,
                citations_json      = EXCLUDED.citations_json,
                reasoning           = EXCLUDED.reasoning,
                model               = EXCLUDED.model,
                analyzed_at         = EXCLUDED.analyzed_at
            """,
            (
                _aid(), source, str(post_id), symbol,
                1 if analysis.get("is_relevant") else 0,
                analysis.get("filter_reason"),
                analysis.get("summary"),
                analysis.get("sentiment_label"),
                analysis.get("sentiment_score"),
                json.dumps(analysis.get("emotions") or [], ensure_ascii=False),
                json.dumps(analysis.get("topics") or [], ensure_ascii=False),
                json.dumps(analysis.get("entities") or [], ensure_ascii=False),
                analysis.get("stance"),
                analysis.get("intent"),
                analysis.get("factuality"),
                analysis.get("risk_level"),
                json.dumps(analysis.get("risk_signals") or [], ensure_ascii=False),
                analysis.get("influence_potential"),
                analysis.get("hidden_meaning"),
                json.dumps(analysis.get("citations") or [], ensure_ascii=False),
                analysis.get("reasoning"),
                model, _now(),
            ),
        )


def insert_brief(conn: psycopg.Connection, symbol: str, date: str,
                 body: str, model: str, kind: str = "daily") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO briefs (account_id, symbol, date, kind, body, model, generated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (_aid(), symbol, date, kind, body, model, _now()),
        )
        row = cur.fetchone()
        return int(row["id"])


def brief_exists(conn: psycopg.Connection, symbol: str, date: str,
                 kind: str = "daily") -> bool:
    """Whether a brief of a given kind has been generated for a symbol on a date - used for monthly idempotency, preventing reruns and re-pushes."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM briefs WHERE account_id = %s AND symbol = %s "
            "AND date = %s AND kind = %s LIMIT 1",
            (_aid(), symbol, date, kind),
        )
        return cur.fetchone() is not None


def insert_draft(conn: psycopg.Connection, symbol: str,
                 source: str | None, post_id: str | None, topic: str | None,
                 variant: str, body: str, rationale: str | None,
                 predicted_effect: str | None, cautions: str | None,
                 model: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO drafts (account_id, symbol, source, post_id, topic, variant, body,
                                rationale, predicted_effect, cautions,
                                model, generated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (_aid(), symbol, source, post_id, topic, variant, body,
             rationale, predicted_effect, cautions, model, _now()),
        )
        row = cur.fetchone()
        return int(row["id"])


def posts_for_symbol(conn: psycopg.Connection, symbol: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM posts WHERE account_id = %s AND symbol = %s "
            "ORDER BY publish_time DESC",
            (_aid(), symbol),
        )
        return list(cur.fetchall())


def posts_missing_analysis(conn: psycopg.Connection, symbol: str,
                           limit: int | None = None) -> list[dict]:
    aid = _aid()
    sql = """
        SELECT p.* FROM posts p
        LEFT JOIN analyses a
          ON a.account_id = p.account_id AND a.source = p.source AND a.post_id = p.post_id
        WHERE p.account_id = %s AND p.symbol = %s AND a.post_id IS NULL
        ORDER BY p.publish_time DESC
    """
    params: tuple = (aid, symbol)
    if limit:
        sql += " LIMIT %s"
        params = (aid, symbol, limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


# sort_by allowlist -> SQL ORDER BY clause.
# All sorting happens in SQL; the frontend no longer sorts client-side, avoiding order mix-ups across pages.
# Time sorting only uses publish_time; undated (NULL) goes last, no longer falling back to ingested_at.
# cluster uses the first topic in topics_json; on PG it must be cast to jsonb, with an empty string/NULL fallback.
_SORT_SQL: dict[str, str] = {
    "newest":         "p.publish_time DESC NULLS LAST",
    "oldest":         "p.publish_time ASC NULLS LAST",
    "engagement":     ("(COALESCE(p.view_count,0) + COALESCE(p.reply_count,0) "
                       "+ COALESCE(p.like_count,0) + COALESCE(p.share_count,0)) DESC"),
    "shares":         "COALESCE(p.share_count, -1) DESC",
    "replies":        "COALESCE(p.reply_count, -1) DESC",
    "likes":          "COALESCE(p.like_count, -1) DESC",
    "views":          "COALESCE(p.view_count, -1) DESC",
    "cluster":        (
        "COALESCE("
        "  CASE WHEN a.topics_json IS NULL OR a.topics_json = '' OR a.topics_json = '[]' "
        "       THEN NULL "
        "       ELSE (a.topics_json::jsonb)->>0 END, '~') ASC, "
        "(COALESCE(a.influence_potential,0) * ABS(COALESCE(a.sentiment_score,0))) DESC"
    ),
    "mediaAZ":        "p.source ASC",
    "mediaZA":        "p.source DESC",
    "scoreSentiment": "ABS(COALESCE(a.sentiment_score, 0)) DESC",
    "scoreInfluence": ("(COALESCE(a.influence_potential,0) * "
                       "ABS(COALESCE(a.sentiment_score,0))) DESC"),
}


def analyses_for_symbol(conn: psycopg.Connection, symbol: str,
                        days: int | None = 1,
                        start: str | None = None,
                        end: str | None = None,
                        limit: int | None = None,
                        offset: int = 0,
                        sort_by: str = "newest",
                        ) -> list[dict]:
    """Article list (for the sentiment monitoring tab). See the sqlite version's docstring; semantics are the same.

    Only returns posts with is_relevant=1 - consistent with the metric definition of today_aggregation's KPIs / trend / risk pie /
    Top5 and the brief (which already filter), so the article list does not expose search noise judged irrelevant by analysis
    (same-name entities, dictionary definitions) to users.
    """
    where = ["a.account_id = %s", "a.symbol = %s", "a.is_relevant = 1"]
    params: list = [_aid(), symbol]

    if start or end:
        if start:
            where.append("p.publish_time >= %s")
            params.append(start)
        if end:
            where.append("p.publish_time <= %s")
            params.append(end)
    elif days and days > 0:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        where.append("p.publish_time >= %s")
        params.append(cutoff)

    order = _SORT_SQL.get(sort_by) or _SORT_SQL["newest"]
    tie = "p.ingested_at DESC, p.post_id ASC"
    sql = f"""
        SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
               p.reply_count, p.like_count, p.share_count,
               p.url, p.content, p.ingested_at
        FROM analyses a
        JOIN posts p
          ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}, {tie}
    """
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params.extend([int(limit), int(offset)])
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def analyses_count_for_symbol(conn: psycopg.Connection, symbol: str,
                              days: int | None = 1,
                              start: str | None = None,
                              end: str | None = None,
                              ) -> int:
    # Same metric definition as analyses_for_symbol: only count is_relevant=1 so the pagination total matches.
    where = ["a.account_id = %s", "a.symbol = %s", "a.is_relevant = 1"]
    params: list = [_aid(), symbol]

    if start or end:
        if start:
            where.append("p.publish_time >= %s")
            params.append(start)
        if end:
            where.append("p.publish_time <= %s")
            params.append(end)
    elif days and days > 0:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        where.append("p.publish_time >= %s")
        params.append(cutoff)

    sql = f"""
        SELECT count(*) AS c
        FROM analyses a
        JOIN posts p
          ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
        WHERE {' AND '.join(where)}
    """
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        return int(row["c"] if row else 0)


def analyses_for_day(conn: psycopg.Connection, symbol: str,
                     date: str, hours: int | None = None) -> list[dict]:
    """Fetch posts for the daily brief - only uses the **publish time** publish_time, with a **rolling last N hours** window (default 24h).

    Window = publish_time in [now - hours, now]:
      · with hour:minute (e.g. 2026-06-18T09:07) -> compared exactly against now-hours;
      · date only (e.g. 2026-06-18, day precision) -> loosely included if "publish day >= cutoff day" (exact time unknown).
    undated (publish_time NULL) is always **excluded**, never falling back to ingested_at.
    hours can be tuned with the SENTINEL_BRIEF_HOURS env var: 24 = last 24 hours / 72 = last 3 days / 168 = last week.
    """
    import os
    from datetime import datetime, timedelta
    if hours is None:
        try:
            hours = int(os.environ.get("SENTINEL_BRIEF_HOURS", "24"))
        except ValueError:
            hours = 24
    cutoff = datetime.now() - timedelta(hours=max(hours, 1))
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M")
    cutoff_date = cutoff.strftime("%Y-%m-%d")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
                   p.reply_count, p.url, p.content
            FROM analyses a
            JOIN posts p
              ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE a.account_id = %s AND a.symbol = %s
              AND p.publish_time IS NOT NULL
              AND (
                (length(p.publish_time) > 10 AND p.publish_time >= %s)
                OR (length(p.publish_time) <= 10 AND substr(p.publish_time, 1, 10) >= %s)
              )
            ORDER BY p.publish_time DESC
            """,
            (_aid(), symbol, cutoff_iso, cutoff_date),
        )
        return list(cur.fetchall())


def analyses_for_month(conn: psycopg.Connection, symbol: str,
                       year: int, month: int) -> list[dict]:
    """Fetch posts for the monthly report - relevant posts whose **publish time** falls in the given calendar month (YYYY-MM), the whole month.

    Key difference from the daily: **no 24h rolling window, no 3-day freshness gate** (the monthly covers the whole month by design).
    Only the year-month of publish_time is used (first 7 chars via substr, works for both date-only and datetime);
    undated (publish_time NULL) is always excluded, same metric definition as the daily, keeping trend numbers clean.
    Only is_relevant=1. Ordered by publish_time descending.
    """
    ym = f"{int(year):04d}-{int(month):02d}"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
                   p.reply_count, p.like_count, p.share_count, p.url, p.content
            FROM analyses a
            JOIN posts p
              ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE a.account_id = %s AND a.symbol = %s AND a.is_relevant = 1
              AND p.publish_time IS NOT NULL
              AND substr(p.publish_time, 1, 7) = %s
            ORDER BY p.publish_time DESC
            """,
            (_aid(), symbol, ym),
        )
        return list(cur.fetchall())


def competitor_analyses(conn: psycopg.Connection, symbol: str,
                        terms: list[str], hours: int | None = None) -> list[dict]:
    """Fetch competitor posts - **same metric definition as the main daily brief**: rolling last hours (default SENTINEL_BRIEF_HOURS=24),
    by **publish_time**, **excluding undated** (no padding with undated/old news), with the extra requirement of being relevant and having the title/
    body contain any competitor term. If there is no competitor news within 24h, returns empty (the competitor section stays blank, no old items). Ordered by publish_time descending."""
    if not terms:
        return []
    import os
    from datetime import datetime, timedelta
    if hours is None:
        try:
            hours = int(os.environ.get("SENTINEL_BRIEF_HOURS", "24"))
        except ValueError:
            hours = 24
    cutoff = datetime.now() - timedelta(hours=max(hours, 1))
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M")
    cutoff_date = cutoff.strftime("%Y-%m-%d")
    like = " OR ".join(["(p.title ILIKE %s OR p.content ILIKE %s)"] * len(terms))
    params: list = [_aid(), symbol]
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    params += [cutoff_iso, cutoff_date]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
                   p.reply_count, p.url, p.content
            FROM analyses a
            JOIN posts p
              ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE a.account_id = %s AND a.symbol = %s AND a.is_relevant = 1
              AND ({like})
              AND p.publish_time IS NOT NULL
              AND (
                (length(p.publish_time) > 10 AND p.publish_time >= %s)
                OR (length(p.publish_time) <= 10 AND substr(p.publish_time, 1, 10) >= %s)
              )
            ORDER BY p.publish_time DESC
            """,
            params,
        )
        return list(cur.fetchall())


def brand_post_lookup(conn: psycopg.Connection, source: str,
                      post_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.*, a.summary, a.sentiment_label, a.risk_level,
                   a.topics_json, a.intent, a.stance
            FROM posts p
            LEFT JOIN analyses a
              ON a.account_id = p.account_id AND a.source = p.source AND a.post_id = p.post_id
            WHERE p.account_id = %s AND p.source = %s AND p.post_id = %s
            """,
            (_aid(), source, str(post_id)),
        )
        return cur.fetchone()


def get_query_last_run(conn: psycopg.Connection, symbol: str,
                       query: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_run_at FROM query_runs "
            "WHERE account_id = %s AND symbol = %s AND query = %s",
            (_aid(), symbol, query),
        )
        row = cur.fetchone()
        return row["last_run_at"] if row else None


def upsert_query_last_run(conn: psycopg.Connection, symbol: str,
                          query: str, ts: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO query_runs (account_id, symbol, query, last_run_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (account_id, symbol, query)
                DO UPDATE SET last_run_at = EXCLUDED.last_run_at
            """,
            (_aid(), symbol, query, ts or _now()),
        )


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def insert_websearch_report(conn: psycopg.Connection, report: dict[str, Any]) -> int:
    """Write a Yuanbao WebSearch quick-check report into the current tenant schema, same as posts/briefs."""
    meta = report.get("_meta") if isinstance(report.get("_meta"), dict) else {}
    llm_meta = meta.get("llm") if isinstance(meta.get("llm"), dict) else {}
    llm_model = report.get("llm_model") or llm_meta.get("model")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO websearch_reports (
                account_id, provider, target, ticker, generated_at,
                query_count, valid_results, risk_level, sentiment_label,
                sentiment_score, summary, llm_model,
                positive_points_json, negative_points_json, risks_json,
                suggestions_json, sources_json, queries_json, raw_rows_json,
                created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                _aid(),
                str(report.get("provider") or "yuanbao"),
                str(report.get("target") or ""),
                str(report.get("ticker") or ""),
                str(report.get("generated_at") or _now()),
                int(report.get("query_count") or len(report.get("queries") or [])),
                int(report.get("valid_results") or 0),
                str(report.get("risk_level") or "none"),
                str(report.get("sentiment_label") or "unknown"),
                float(report.get("sentiment_score") or 0.0),
                str(report.get("summary") or ""),
                str(llm_model or "") or None,
                json.dumps(_json_list(report.get("positive_points")), ensure_ascii=False),
                json.dumps(_json_list(report.get("negative_points")), ensure_ascii=False),
                json.dumps(_json_list(report.get("risks")), ensure_ascii=False),
                json.dumps(_json_list(report.get("suggestions")), ensure_ascii=False),
                json.dumps(_json_list(report.get("sources")), ensure_ascii=False),
                json.dumps(_json_list(report.get("queries")), ensure_ascii=False),
                json.dumps(_json_list(report.get("raw_rows")), ensure_ascii=False),
                _now(),
            ),
        )
        row = cur.fetchone()
        return int(row["id"])


def _websearch_row_to_dict(row: dict) -> dict:
    d = dict(row)
    return {
        "id": d.get("id"),
        "report_id": d.get("id"),
        "mode": "yuanbao_websearch",
        "provider": d.get("provider") or "yuanbao",
        "target": d.get("target") or "",
        "ticker": d.get("ticker") or "",
        "generated_at": d.get("generated_at"),
        "risk_level": d.get("risk_level") or "none",
        "sentiment_label": d.get("sentiment_label") or "unknown",
        "sentiment_score": float(d.get("sentiment_score") or 0.0),
        "query_count": int(d.get("query_count") or 0),
        "valid_results": int(d.get("valid_results") or 0),
        "summary": d.get("summary") or "",
        "llm_model": d.get("llm_model"),
        "positive_points": _json_list(d.get("positive_points_json")),
        "negative_points": _json_list(d.get("negative_points_json")),
        "risks": _json_list(d.get("risks_json")),
        "suggestions": _json_list(d.get("suggestions_json")),
        "sources": _json_list(d.get("sources_json")),
        "queries": _json_list(d.get("queries_json")),
        "raw_rows": _json_list(d.get("raw_rows_json")),
        "created_at": d.get("created_at"),
    }


def latest_websearch_report(conn: psycopg.Connection, ticker: str | None = None) -> dict | None:
    """Read the latest WebSearch report from the current tenant schema. When ticker is given, narrow by the account's current ticker."""
    with conn.cursor() as cur:
        if ticker:
            cur.execute(
                """
                SELECT * FROM websearch_reports
                WHERE account_id = %s AND ticker = %s
                ORDER BY generated_at DESC, id DESC
                LIMIT 1
                """,
                (_aid(), ticker),
            )
        else:
            cur.execute(
                """
                SELECT * FROM websearch_reports
                WHERE account_id = %s
                ORDER BY generated_at DESC, id DESC
                LIMIT 1
                """,
                (_aid(),),
            )
        row = cur.fetchone()
        return _websearch_row_to_dict(row) if row else None


def ingest_jsonl_dir(conn: psycopg.Connection, data_dir: Path) -> tuple[int, int]:
    """Ingest every *.jsonl file in data_dir. Returns (inserted, skipped)."""
    inserted = skipped = 0
    for path in sorted(data_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if upsert_post(conn, rec):
                    inserted += 1
                else:
                    skipped += 1
    conn.commit()
    return inserted, skipped


def ingest_records(conn: psycopg.Connection, records: Iterable[dict]) -> int:
    n = 0
    for r in records:
        if upsert_post(conn, r):
            n += 1
    conn.commit()
    return n
