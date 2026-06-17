"""Run a monitoring plan: search each query, dedupe, normalize, ingest into SQLite.

Output rows go into the same `posts` table the eastmoney crawler uses, so the
existing analyzer / brief / drafts / chat / report all work unchanged.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

from storage import (
    connect, init_schema, upsert_post,
    get_query_last_run, upsert_query_last_run,
)

from .baidu import baidu_search
from .cnbing import cnbing_search
from .ddg import ddg_search, throttle
from .sogou import sogou_search
from .searxng import searxng_search
from .weibo import weibo_search
from .zhihu import zhihu_search


# SearXNG 单引擎(server 端聚合多上游 + 处理反爬/IP池);自带引擎保留可回退。
DEFAULT_ENGINES = ("searxng",)


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


# 发布日期抽取:SearXNG 上游不给 publishedDate 时,从标题 / 摘要里尽力抽一个
# **绝对**日期(必须带 4 位年份),让前端时间轴 / "最近 N 天" 不至于全空。
# 只认绝对日期 — 相对时间("3 天前")缺锚点容易猜错,宁可留 None。
_DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"), (1, 2, 3)),       # 2026-06-15 / 2026/6/15
    (re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), (1, 2, 3)),  # 2026年6月15日
]
_EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_EN_DATE_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})",
    re.IGNORECASE,
)


def extract_publish_date(text: str | None) -> str | None:
    if not text:
        return None
    for rx, (yi, mi, di) in _DATE_PATTERNS:
        m = rx.search(text)
        if m:
            y, mo, d = int(m.group(yi)), int(m.group(mi)), int(m.group(di))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _EN_DATE_RE.search(text)
    if m:
        mo = _EN_MONTHS.get(m.group(1).lower()[:3])
        d, y = int(m.group(2)), int(m.group(3))
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


# URL 内嵌日期抽取(②级,高可靠):多数中文新闻站把发布日期写进 URL —
#   sina   finance.sina.com.cn/.../2026-04-05/...        →  /20YY-MM-DD/
#   eastmoney  /news/20260616150020...   /a/202605173739...  →  8 位 20YYMMDD 连号
#   qq  /rain/a/20260514A00FU400    10jqka  /20260409/c...  →  8 位连号
# 比从摘要里猜可靠得多;校验月/日合法、年份 2000-2099,取第一个命中。
_URL_DATE_RES = [
    re.compile(r"/(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:[-/_.]|$)"),  # /2026-06-15/  /2026/6/15
    re.compile(r"[/_-](20\d{2})(\d{2})(\d{2})\d*"),                   # /20260616...  8 位连号
]


def extract_date_from_url(url: str | None) -> str | None:
    if not url:
        return None
    for rx in _URL_DATE_RES:
        m = rx.search(url)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def normalize_result(r: dict, symbol: str) -> dict:
    url = r.get("href") or r.get("url") or ""
    title = _clean(r.get("title"))
    body = _clean(r.get("body"))  # SERP snippet — short but usually enough
    # 发布时间解析(逐级兜底,缺则下一级;都没有 → None=undated,绝不退回入库时间):
    #   ① 召回自带 publishedDate  →  ② URL 内嵌日期(高可靠)  →  ④ 标题/摘要抽取(低置信)
    pub = (r.get("publish_time")
           or extract_date_from_url(url)
           or extract_publish_date(f"{title or ''} {body or ''}"))
    return {
        "post_id": url_to_post_id(url),
        "source": domain_to_source(url),
        "symbol": symbol,
        "author": None,
        "title": title,
        "content": body,
        "publish_time": pub,
        "view_count": None,
        "reply_count": None,
        "url": url,
    }


def build_match_terms(plan: dict, symbol: str) -> list[str]:
    """监测目标的"必含词"集合:目标名 / 别名 / ticker / 同集团 targets。

    入库前用它做相关性闸门:命中结果的标题 / 摘要 / URL 至少要含其中一个,
    否则视为搜索噪声(同名实体、词典释义、泛词扩散,如「世纪互联」搜成「世纪」
    捞回的百度知道词条、同名酒店)丢弃。
    """
    raw = [plan.get("target"), symbol, plan.get("ticker"),
           *(plan.get("targets") or []), *(plan.get("aliases") or [])]
    terms: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if not isinstance(t, str):
            continue
        t = t.strip()
        # 太短的纯拉丁词(<3)做子串匹配易误命中;中文 2 字即可。
        if len(t) < 2 or (t.isascii() and len(t) < 3):
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            terms.append(t)
    return terms


def is_relevant(rec: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    hay = " ".join(
        x for x in (rec.get("title"), rec.get("content"), rec.get("url")) if x
    ).lower()
    return any(t.lower() in hay for t in terms)


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


def _call_engine(eng: str, query: str, max_results: int,
                 region: str, timelimit: str | None) -> tuple[str, list[dict]]:
    """Call a single engine, return (engine_name, results). Thread-safe."""
    try:
        if eng == "searxng":
            r = searxng_search(query, max_results=max_results,
                               timelimit=timelimit)
        elif eng == "ddg":
            r = ddg_search(query, max_results=max_results,
                           region=region, timelimit=timelimit)
        elif eng == "cnbing":
            r = cnbing_search(query, max_results=max_results,
                              timelimit=timelimit)
        elif eng == "baidu":
            r = baidu_search(query, max_results=max_results,
                             timelimit=timelimit)
        elif eng == "sogou":
            # 微信公众号专用通道:query 含 site:mp.weixin 自动走 weixin.sogou.com
            # resolve_redirect=True: sogou /link?url=… → 真实目标 URL,
            # 否则 normalize_result 会把所有 sogou 命中标成 "unknown"
            channel = "auto"
            r = sogou_search(query, max_results=max_results,
                             timelimit=timelimit, channel=channel,
                             resolve_redirect=True)
        else:
            print(f"  [search] unknown engine: {eng!r}", file=sys.stderr)
            return eng, []
    except Exception as e:
        print(f"  [{eng}] error: {e}", file=sys.stderr)
        r = []
    return eng, r


def _search_engines(query: str, engines: tuple[str, ...],
                    max_results: int, region: str,
                    timelimit: str | None) -> tuple[list[dict], dict[str, int]]:
    """Fan out to each engine IN PARALLEL, concat results."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: list[dict] = []
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=len(engines)) as pool:
        futures = {
            pool.submit(_call_engine, eng, query, max_results, region, timelimit): eng
            for eng in engines
        }
        for fut in as_completed(futures):
            eng_name, results = fut.result()
            counts[eng_name] = len(results)
            out.extend(results)
    return out, counts


# 登录墙 UGC 直采源:搜索引擎进不去(微博/知乎登录墙),用 cookie 直采。
# 一小撮"目标名 + 负面词"定向查询,不混进 SearXNG plan(避免对平台高频请求被风控)。
_DIRECT_NEG = ("欠薪", "讨薪", "维权", "做空", "裁员", "爆雷", "亏损", "诉讼")
_DIRECT_SOURCES = (
    ("weibo", "WEIBO_COOKIE", weibo_search),
    ("zhihu", "ZHIHU_COOKIE", zhihu_search),
)


def collect_direct_sources(plan: dict, symbol: str, conn, seen: set[str],
                           match_terms: list[str], max_results: int = 15,
                           sleep_s: float = 1.5, verbose: bool = True) -> dict:
    """微博 / 知乎等登录墙平台直采(搜索引擎盲区)。cookie(env)未配的源自动跳过。

    复用相同的相关性闸门 + dedup(seen)+ upsert;发布时间由各 adapter 结构化提供,
    天然带准确 publish_time。任一源 / 查询失败仅跳过,不影响其它。
    """
    primary = (plan.get("target") or symbol or "").strip()
    if not primary:
        return {}
    queries = [primary] + [f"{primary} {n}" for n in _DIRECT_NEG]
    per_source: dict[str, int] = {}
    for name, env_key, fn in _DIRECT_SOURCES:
        if not os.environ.get(env_key):
            continue
        got = 0
        for q in queries:
            try:
                rows = fn(q, max_results=max_results)
            except Exception as e:
                print(f"  [{name}] {q!r} error: {e}", file=sys.stderr)
                rows = []
            for r in rows:
                url = (r.get("href") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                rec = normalize_result(r, symbol)
                rec["source"] = name             # 统一来源名(weibo/zhihu)
                rec["author"] = r.get("author")  # 保留作者(normalize 默认置 None)
                if not is_relevant(rec, match_terms):
                    continue
                if upsert_post(conn, rec):
                    got += 1
                    per_source[name] = per_source.get(name, 0) + 1
            throttle(sleep_s)
        if verbose:
            print(f"  [direct:{name}] {got} new · {len(queries)} queries")
    return per_source


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

    match_terms = build_match_terms(plan, symbol)

    inserted = total = irrelevant = 0
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
            # 相关性闸门:标题 / 摘要 / URL 不含目标 / 别名 / ticker 即丢弃,
            # 拦掉同名实体、词典释义等搜索噪声(也省下后续 LLM 分析开销)。
            if not is_relevant(rec, match_terms):
                irrelevant += 1
                continue
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

    # 登录墙 UGC 直采(微博/知乎),与 SearXNG 召回合并入库
    direct = collect_direct_sources(plan, symbol, conn, seen, match_terms,
                                    sleep_s=sleep_s, verbose=verbose)
    for s, n in direct.items():
        per_source[s] = per_source.get(s, 0) + n
        inserted += n
        total += n

    conn.commit()
    return {
        "queries": len(queries),
        "inserted": inserted,
        "total": total,
        "irrelevant": irrelevant,
        "by_source": per_source,
        "by_engine": per_engine_total,
        "engines": list(eng_tuple),
    }
