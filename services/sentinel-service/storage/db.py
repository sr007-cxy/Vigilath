"""SQLite storage for posts, LLM analyses, AI briefs and response drafts."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "yuqing.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    source        TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    symbol        TEXT,
    author        TEXT,
    title         TEXT,
    content       TEXT,
    publish_time  TEXT,
    view_count    INTEGER,
    reply_count   INTEGER,
    url           TEXT,
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (source, post_id)
);
CREATE INDEX IF NOT EXISTS idx_posts_symbol ON posts(symbol);
CREATE INDEX IF NOT EXISTS idx_posts_pubtime ON posts(publish_time);

CREATE TABLE IF NOT EXISTS analyses (
    source              TEXT NOT NULL,
    post_id             TEXT NOT NULL,
    symbol              TEXT,
    is_relevant         INTEGER NOT NULL,
    filter_reason       TEXT,
    summary             TEXT,
    sentiment_label     TEXT,
    sentiment_score     REAL,
    emotions_json       TEXT,
    topics_json         TEXT,
    entities_json       TEXT,
    stance              TEXT,
    intent              TEXT,
    factuality          TEXT,
    risk_level          TEXT,
    risk_signals_json   TEXT,
    influence_potential REAL,
    hidden_meaning      TEXT,
    citations_json      TEXT,
    reasoning           TEXT,
    model               TEXT NOT NULL,
    analyzed_at         TEXT NOT NULL,
    PRIMARY KEY (source, post_id)
);
CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON analyses(symbol);
CREATE INDEX IF NOT EXISTS idx_analyses_risk ON analyses(risk_level);
CREATE INDEX IF NOT EXISTS idx_analyses_sentiment ON analyses(sentiment_label);

CREATE TABLE IF NOT EXISTS briefs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    date          TEXT NOT NULL,
    body          TEXT NOT NULL,
    model         TEXT NOT NULL,
    generated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_briefs_symbol_date ON briefs(symbol, date);

CREATE TABLE IF NOT EXISTS drafts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
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
);
CREATE INDEX IF NOT EXISTS idx_drafts_symbol ON drafts(symbol);

CREATE TABLE IF NOT EXISTS query_runs (
    symbol        TEXT NOT NULL,
    query         TEXT NOT NULL,
    last_run_at   TEXT NOT NULL,
    PRIMARY KEY (symbol, query)
);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """SQLite 连接,启 WAL + 30s busy timeout.

    背景:monitor 阶段事务很长(几十个 query × upsert_post + upsert_query_last_run
    都在同一个 conn 上,直到外层 commit),期间若 analyze / scheduler / 另一个
    手动 run-now 重叠,默认配置会立即抛 "database is locked".

    修复(SQLite 并发标配):
    - timeout=30:sqlite3.connect 自带的 busy timeout — 撞锁时等最多 30s 而不是
      立刻抛.
    - PRAGMA journal_mode=WAL:写者不阻塞读者,前端轮询 /today /posts 不会被
      monitor 长事务卡住.WAL 是 db 级持久属性,设一次后续所有连接都受用.
    - PRAGMA synchronous=NORMAL:WAL 下可以放宽到 NORMAL(默认 FULL 太保守),
      显著降低 fsync 频率,提升写吞吐;crash 风险 = 最近一次 checkpoint 之前的
      事务可能丢,对舆情数据可接受.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_post(conn: sqlite3.Connection, rec: dict) -> bool:
    """Insert post if new; return True if inserted, False if it already existed."""
    cur = conn.execute(
        """
        INSERT INTO posts (source, post_id, symbol, author, title, content,
                           publish_time, view_count, reply_count, url, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source, post_id) DO NOTHING
        """,
        (
            rec.get("source"),
            str(rec.get("post_id")),
            rec.get("symbol"),
            rec.get("author"),
            rec.get("title"),
            rec.get("content"),
            rec.get("publish_time"),
            rec.get("view_count"),
            rec.get("reply_count"),
            rec.get("url"),
            _now(),
        ),
    )
    return cur.rowcount == 1


def upsert_analysis(conn: sqlite3.Connection, source: str, post_id: str,
                    symbol: str | None, analysis: dict, model: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO analyses (
            source, post_id, symbol,
            is_relevant, filter_reason,
            summary, sentiment_label, sentiment_score,
            emotions_json, topics_json, entities_json,
            stance, intent, factuality,
            risk_level, risk_signals_json, influence_potential,
            hidden_meaning, citations_json, reasoning,
            model, analyzed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source, str(post_id), symbol,
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


def insert_brief(conn: sqlite3.Connection, symbol: str, date: str,
                 body: str, model: str) -> int:
    cur = conn.execute(
        "INSERT INTO briefs (symbol, date, body, model, generated_at) VALUES (?,?,?,?,?)",
        (symbol, date, body, model, _now()),
    )
    return cur.lastrowid


def insert_draft(conn: sqlite3.Connection, symbol: str,
                 source: str | None, post_id: str | None, topic: str | None,
                 variant: str, body: str, rationale: str | None,
                 predicted_effect: str | None, cautions: str | None,
                 model: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO drafts (symbol, source, post_id, topic, variant, body,
                            rationale, predicted_effect, cautions,
                            model, generated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (symbol, source, post_id, topic, variant, body,
         rationale, predicted_effect, cautions, model, _now()),
    )
    return cur.lastrowid


def posts_for_symbol(conn: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM posts WHERE symbol = ? ORDER BY publish_time DESC",
        (symbol,),
    ))


def posts_missing_analysis(conn: sqlite3.Connection, symbol: str,
                           limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT p.* FROM posts p
        LEFT JOIN analyses a ON a.source = p.source AND a.post_id = p.post_id
        WHERE p.symbol = ? AND a.post_id IS NULL
        ORDER BY p.publish_time DESC
    """
    params: tuple = (symbol,)
    if limit:
        sql += " LIMIT ?"
        params = (symbol, limit)
    return list(conn.execute(sql, params))


def analyses_for_symbol(conn: sqlite3.Connection, symbol: str,
                        days: int | None = 1,
                        start: str | None = None,
                        end: str | None = None,
                        limit: int | None = None,
                        offset: int = 0,
                        ) -> list[sqlite3.Row]:
    """文章列表(舆情 Tab 用).

    时间过滤,基于 publish_time(文章实际发表时间)。publish_time IS NULL
    的记录在有时间范围时不计入(没有时间无法判定);days=0 全部历史则不过滤。
    三选一:
        - start/end:ISO 时间戳,闭区间。任意一端 None 表示该侧不限。
                     start/end 同时 None 时回退到 days 语义。
        - days: 1=今日(默认), N>1=最近 N 天, 0/None=全部历史

    排序: max(publish_time, ingested_at) DESC.
    分页: limit/offset。limit None = 不限。
    """
    where = ["a.symbol = ?"]
    params: list = [symbol]

    # 时间过滤改为基于 publish_time(文章实际发表时间)而不是 ingested_at —
    # publish_time IS NULL 的帖子被排除(无法定位到时间段);"全部历史" 不过滤,仍包含。
    # ISO 8601 字符串可直接 lex 比较,保留小时/分钟精度。
    if start or end:
        where.append("p.publish_time IS NOT NULL")
        if start:
            where.append("p.publish_time >= ?")
            params.append(start)
        if end:
            where.append("p.publish_time <= ?")
            params.append(end)
    elif days and days > 0:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        where.append("p.publish_time IS NOT NULL")
        where.append("p.publish_time >= ?")
        params.append(cutoff)

    sql = f"""
        SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
               p.reply_count, p.url, p.content
        FROM analyses a
        JOIN posts p ON p.source = a.source AND p.post_id = a.post_id
        WHERE {' AND '.join(where)}
        ORDER BY max(coalesce(p.publish_time, ''), p.ingested_at) DESC
    """
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
    return list(conn.execute(sql, tuple(params)))


def analyses_count_for_symbol(conn: sqlite3.Connection, symbol: str,
                              days: int | None = 1,
                              start: str | None = None,
                              end: str | None = None,
                              ) -> int:
    """analyses_for_symbol 的 COUNT(*) — 给前端分页 total 用。"""
    where = ["a.symbol = ?"]
    params: list = [symbol]

    # 同 analyses_for_symbol — 按 publish_time 过滤,NULL 不计入有时间范围的查询。
    if start or end:
        where.append("p.publish_time IS NOT NULL")
        if start:
            where.append("p.publish_time >= ?")
            params.append(start)
        if end:
            where.append("p.publish_time <= ?")
            params.append(end)
    elif days and days > 0:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        where.append("p.publish_time IS NOT NULL")
        where.append("p.publish_time >= ?")
        params.append(cutoff)

    sql = f"""
        SELECT count(*) AS c
        FROM analyses a
        JOIN posts p ON p.source = a.source AND p.post_id = a.post_id
        WHERE {' AND '.join(where)}
    """
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row["c"] if row else 0)


def analyses_for_day(conn: sqlite3.Connection, symbol: str,
                     date: str) -> list[sqlite3.Row]:
    """日度简报数据源.

    匹配规则:**用 ingested_at 当 "今日"** —— 我们今天看到的,就是今天的舆情。
    不再单看 publish_time;crawler 拿回的多数是历史帖,publish_time 可能
    是几天/几月前,但今天才发现该计入今天。ingested_at 是 NOT NULL 字段,
    精确反映 "我们何时看到这条数据"。
    """
    return list(conn.execute(
        """
        SELECT a.*, p.title, p.author, p.publish_time, p.view_count,
               p.reply_count, p.url, p.content
        FROM analyses a
        JOIN posts p ON p.source = a.source AND p.post_id = a.post_id
        WHERE a.symbol = ?
          AND substr(p.ingested_at, 1, 10) = ?
        ORDER BY coalesce(p.publish_time, p.ingested_at) DESC
        """,
        (symbol, date),
    ))


def brand_post_lookup(conn: sqlite3.Connection, source: str,
                      post_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT p.*, a.summary, a.sentiment_label, a.risk_level,
               a.topics_json, a.intent, a.stance
        FROM posts p
        LEFT JOIN analyses a ON a.source = p.source AND a.post_id = p.post_id
        WHERE p.source = ? AND p.post_id = ?
        """,
        (source, str(post_id)),
    ).fetchone()
    return row


def get_query_last_run(conn: sqlite3.Connection, symbol: str,
                       query: str) -> str | None:
    row = conn.execute(
        "SELECT last_run_at FROM query_runs WHERE symbol = ? AND query = ?",
        (symbol, query),
    ).fetchone()
    return row["last_run_at"] if row else None


def upsert_query_last_run(conn: sqlite3.Connection, symbol: str,
                          query: str, ts: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO query_runs (symbol, query, last_run_at)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol, query) DO UPDATE SET last_run_at = excluded.last_run_at
        """,
        (symbol, query, ts or _now()),
    )


def ingest_jsonl_dir(conn: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
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


def ingest_records(conn: sqlite3.Connection, records: Iterable[dict]) -> int:
    n = 0
    for r in records:
        if upsert_post(conn, r):
            n += 1
    conn.commit()
    return n
