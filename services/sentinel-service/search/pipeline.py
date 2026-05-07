"""Run a monitoring plan: search each query, dedupe, normalize, ingest into SQLite.

Output rows go into the same `posts` table the eastmoney crawler uses, so the
existing analyzer / brief / drafts / chat / report all work unchanged.
"""
from __future__ import annotations

import hashlib
import html
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

from storage import (
    connect, init_schema, upsert_post,
    get_query_last_run, upsert_query_last_run,
)

from .baidu import baidu_search
from .ddg import ddg_search, throttle


DEFAULT_ENGINES = ("ddg", "baidu")


# DDG occasionally returns titles where the `&` of an HTML numeric entity has
# been replaced with `||`, e.g. `||#19990;纪互联`. Repair before persisting.
_BROKEN_ENTITY_RE = re.compile(r"\|\|(#\d+;)")


def _clean(text: str | None) -> str | None:
    if not text:
        return text
    text = _BROKEN_ENTITY_RE.sub(r"&\1", text)
    return html.unescape(text)


# Map well-known hostnames to short, stable source names so the analyzer +
# report group results by platform regardless of which specific subdomain
# they came back on.
_HOST_TO_SOURCE = [
    ("xueqiu.com",          "xueqiu"),
    ("guba.eastmoney.com",  "eastmoney"),
    ("eastmoney.com",       "eastmoney"),
    ("weibo.com",           "weibo"),
    ("weibo.cn",            "weibo"),
    ("zhihu.com",           "zhihu"),
    ("douban.com",          "douban"),
    ("tieba.baidu.com",     "tieba"),
    ("zhidao.baidu.com",    "zhidao"),
    ("news.baidu.com",      "baidu_news"),
    ("36kr.com",            "36kr"),
    ("caixin.com",          "caixin"),
    ("bilibili.com",        "bilibili"),
    ("toutiao.com",         "toutiao"),
    ("xiaohongshu.com",     "xiaohongshu"),
    ("xhslink.com",         "xiaohongshu"),
    ("kuaishou.com",        "kuaishou"),
    ("mp.weixin.qq.com",    "weixin"),
    ("weixin.qq.com",       "weixin"),
    ("sina.com",            "sina"),
    ("163.com",             "163"),
    ("qq.com",              "qq"),
    ("reddit.com",          "reddit"),
    ("twitter.com",         "twitter"),
    ("x.com",               "twitter"),
    ("youtube.com",         "youtube"),
    ("medium.com",          "medium"),
    ("seekingalpha.com",    "seekingalpha"),
    ("bloomberg.com",       "bloomberg"),
    ("ft.com",              "ft"),
    ("wsj.com",             "wsj"),
    ("nytimes.com",         "nyt"),
]


def url_to_post_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def domain_to_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for needle, name in _HOST_TO_SOURCE:
        if host == needle or host.endswith("." + needle):
            return name
    return host or "unknown"


def normalize_result(r: dict, symbol: str) -> dict:
    url = r.get("href") or r.get("url") or ""
    return {
        "post_id": url_to_post_id(url),
        "source": domain_to_source(url),
        "symbol": symbol,
        "author": None,
        "title": _clean(r.get("title")),
        "content": _clean(r.get("body")),  # SERP snippet — short but usually enough
        "publish_time": None,               # DDG doesn't expose dates reliably
        "view_count": None,
        "reply_count": None,
        "url": url,
    }


def _gap_to_timelimit(last_run_at: str | None, floor: str = "d") -> str:
    """Pick the narrowest DDG bucket that still covers the gap since last_run_at.

    Buckets: d=24h, w=7d, m=31d, y=365d. `floor` is used when there is no prior
    run or when the gap is smaller than the floor.
    """
    if not last_run_at:
        return floor
    try:
        prev = datetime.fromisoformat(last_run_at)
    except ValueError:
        return floor
    gap_h = (datetime.now() - prev).total_seconds() / 3600.0
    order = ["d", "w", "m", "y"]
    bucket = "y" if gap_h > 24 * 31 else "m" if gap_h > 24 * 7 else "w" if gap_h > 24 else "d"
    return bucket if order.index(bucket) >= order.index(floor) else floor


def _search_engines(query: str, engines: tuple[str, ...],
                    max_results: int, region: str,
                    timelimit: str | None) -> tuple[list[dict], dict[str, int]]:
    """Fan out to each engine, concat results, return (results, per-engine counts)."""
    out: list[dict] = []
    counts: dict[str, int] = {}
    for eng in engines:
        try:
            if eng == "ddg":
                r = ddg_search(query, max_results=max_results,
                               region=region, timelimit=timelimit)
            elif eng == "baidu":
                r = baidu_search(query, max_results=max_results,
                                 timelimit=timelimit)
            else:
                print(f"  [search] unknown engine: {eng!r}", file=sys.stderr)
                continue
        except Exception as e:
            print(f"  [{eng}] error: {e}", file=sys.stderr)
            r = []
        counts[eng] = len(r)
        out.extend(r)
    return out, counts


def run_plan(plan: dict, symbol: str,
             max_results_per_query: int = 10,
             timelimit: str | None = None,
             auto_widen: bool = True,
             region: str = "wt-wt",
             sleep_s: float = 1.5,
             engines: tuple[str, ...] | list[str] | None = None,
             verbose: bool = True) -> dict:
    """Execute every query in `plan`, dedupe by URL, ingest into SQLite.

    If `auto_widen` is True, `timelimit` acts as a floor and the actual bucket
    sent to DDG is widened per-query to cover the gap since `last_run_at`.
    If False, `timelimit` is passed through unchanged (None = no limit).
    """
    queries = plan.get("queries") or []
    if not queries:
        sys.exit("plan has no queries")

    eng_tuple = tuple(engines) if engines else DEFAULT_ENGINES
    floor = timelimit if timelimit in ("d", "w", "m", "y") else "d"

    conn = connect()
    init_schema(conn)

    inserted = total = 0
    seen: set[str] = set()
    per_source: dict[str, int] = {}
    per_engine_total: dict[str, int] = {e: 0 for e in eng_tuple}

    for i, q in enumerate(queries, 1):
        query = q.get("query")
        if not query:
            continue

        last = get_query_last_run(conn, symbol, query) if auto_widen else None
        effective_tl = _gap_to_timelimit(last, floor=floor) if auto_widen else timelimit

        results, eng_counts = _search_engines(
            query, eng_tuple,
            max_results=max_results_per_query,
            region=region, timelimit=effective_tl,
        )
        for e, n in eng_counts.items():
            per_engine_total[e] = per_engine_total.get(e, 0) + n

        added = 0
        for r in results:
            url = (r.get("href") or r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            rec = normalize_result(r, symbol)
            if upsert_post(conn, rec):
                inserted += 1
                added += 1
                per_source[rec["source"]] = per_source.get(rec["source"], 0) + 1
            total += 1

        upsert_query_last_run(conn, symbol, query)

        if verbose:
            short = (query[:78] + "…") if len(query) > 80 else query
            eng_breakdown = "+".join(f"{e}:{eng_counts.get(e, 0)}" for e in eng_tuple)
            tl_note = f" tl={effective_tl or 'none'}" if auto_widen else ""
            print(f"  [{i:>2}/{len(queries)}] {len(results):>2} results "
                  f"({eng_breakdown}){tl_note} · {added:>2} new · {short}")
        throttle(sleep_s)

    conn.commit()
    return {
        "queries": len(queries),
        "inserted": inserted,
        "total": total,
        "by_source": per_source,
        "by_engine": per_engine_total,
        "engines": list(eng_tuple),
    }
