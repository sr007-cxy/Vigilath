"""HTTP client -- backend -> sentinel-service.

Wraps calls to sentinel-service's 5 RPCs, uniformly handling timeouts, errors and API key injection.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

SENTINEL_URL = os.environ.get("SENTINEL_SERVICE_URL", "http://localhost:8090")
PLATFORM_OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

# Hardcoded fallback -- used only when the DB query fails (cold start / missing table / exception);
# the real source of truth is the sentiment_platforms table (seeded by migration 010).
# To change / add platforms don't edit this -- change the DB (or rerun the migration).
_PLATFORM_FALLBACK_MAP: dict[str, str] = {
    # finance (primary)
    "xueqiu":       "xueqiu.com",
    "eastmoney":    "guba.eastmoney.com",
    "caixin":       "caixin.com",
    "36kr":         "36kr.com",
    "sina_finance": "finance.sina.com.cn",
    "qq_finance":   "finance.qq.com",
    "163_finance":  "money.163.com",
    "ths":          "10jqka.com.cn",
    "wallstreetcn": "wallstreetcn.com",
    "yicai":        "yicai.com",
    "cls":          "cls.cn",
    "gelonghui":    "gelonghui.com",
    # finance (extended)
    "jrj":          "jrj.com.cn",
    "hexun":        "hexun.com",
    "jin10":        "jin10.com",
    "nbd":          "nbd.com.cn",
    "jingji21":     "21jingji.com",
    "stcn":         "stcn.com",
    "cs_com":       "cs.com.cn",
    "cnstock":      "cnstock.com",
    "p5w":          "p5w.net",
    "caijing":      "caijing.com.cn",
    "iyiou":        "iyiou.com",
    "futunn":       "futunn.com",
    "itiger":       "itiger.com",
    "zhitong":      "zhitongcaijing.com",
    # social
    "weibo":        "weibo.com",
    "weixin":       "mp.weixin.qq.com",
    "xiaohongshu":  "xiaohongshu.com",
    "zhihu":        "zhihu.com",
    # forum
    "tieba":        "tieba.baidu.com",
    "zhidao":       "zhidao.baidu.com",
    "hupu":         "hupu.com",
    "douban":       "douban.com",
    "v2ex":         "v2ex.com",
    "guokr":        "guokr.com",
    "csdn":         "csdn.net",
    "juejin":       "juejin.cn",
    # video
    "bilibili":     "bilibili.com",
    "douyin":       "douyin.com",
    "kuaishou":     "kuaishou.com",
    "toutiao":      "toutiao.com",
    "vqq":          "v.qq.com",
    "iqiyi":        "iqiyi.com",
    "ixigua":       "ixigua.com",
    # news
    "baidu_news":   "news.baidu.com",
    "ifeng":        "ifeng.com",
    "sina_main":    "sina.com.cn",
    "sohu":         "sohu.com",
    "chinanews":    "chinanews.com",
    "xinhuanet":    "xinhuanet.com",
    "people":       "people.com.cn",
    "huanqiu":      "huanqiu.com",
    "guancha":      "guancha.cn",
    "ce_cn":        "ce.cn",
    "thepaper":     "thepaper.cn",
    "jiemian":      "jiemian.com",
    "huxiu":        "huxiu.com",
    "tmtpost":      "tmtpost.com",
    "pingwest":     "pingwest.com",
    "ifanr":        "ifanr.com",
    "qbitai":       "qbitai.com",
    "jiqizhixin":   "jiqizhixin.com",
    # overseas
    "reddit":       "reddit.com",
    "twitter":      "twitter.com",
    "seekingalpha": "seekingalpha.com",
    "bloomberg":    "bloomberg.com",
    "reuters":      "reuters.com",
}

# 60s in-memory cache -- one pipeline run calls several RPCs; avoid hitting the DB each time.
# Admin platform changes take effect within 60s at most.
_PLATFORM_CACHE: Optional[tuple[dict[str, str], list[str]]] = None
_PLATFORM_CACHE_AT: float = 0.0
_PLATFORM_CACHE_TTL = 60.0
_PLATFORM_CACHE_LOCK = threading.Lock()


def _load_platform_map() -> tuple[dict[str, str], list[str]]:
    """Read (code->domain map, list of all enabled domains) from the DB.
    Falls back to _PLATFORM_FALLBACK_MAP when the DB is unreachable -- so sentinel-service still runs even in
    the extreme case of backend cold start / table corruption (using 27 built-in platforms)."""
    global _PLATFORM_CACHE, _PLATFORM_CACHE_AT
    now = time.time()
    if _PLATFORM_CACHE is not None and (now - _PLATFORM_CACHE_AT) < _PLATFORM_CACHE_TTL:
        return _PLATFORM_CACHE
    with _PLATFORM_CACHE_LOCK:
        if _PLATFORM_CACHE is not None and (now - _PLATFORM_CACHE_AT) < _PLATFORM_CACHE_TTL:
            return _PLATFORM_CACHE
        try:
            # Lazy import to avoid circular references between services/* <-> models/*.
            from geo.database import SessionLocal
            from geo.models.sentiment import SentimentPlatformORM
            db = SessionLocal()
            try:
                rows = db.query(SentimentPlatformORM).filter_by(enabled=True).all()
            finally:
                db.close()
            if not rows:
                # Table exists but not seeded yet -- hold with the fallback; admin runs the migration later
                log.warning("sentiment_platforms table is empty; using fallback (run migration 010)")
                m = dict(_PLATFORM_FALLBACK_MAP)
            else:
                m = {r.code: r.domain for r in rows}
        except Exception as e:
            log.warning("could not load sentiment_platforms from DB (%s); using fallback", e)
            m = dict(_PLATFORM_FALLBACK_MAP)
        result = (m, list(m.values()))
        _PLATFORM_CACHE = result
        _PLATFORM_CACHE_AT = now
        return result


def invalidate_platform_cache() -> None:
    """Call after admin edits the platform catalog so the next RPC takes effect immediately (optional; otherwise within 60s)."""
    global _PLATFORM_CACHE, _PLATFORM_CACHE_AT
    with _PLATFORM_CACHE_LOCK:
        _PLATFORM_CACHE = None
        _PLATFORM_CACHE_AT = 0.0


def _resolve_media_allowlist(items: Optional[list[str]]) -> list[str]:
    """codes -> domains.
    - items containing recognized codes -> translated to domains
    - items containing unrecognized strings (e.g. user-written 'xhslink.com') -> passed through as-is
    - items empty / None -> **expanded to all enabled platform domains**, so sentinel always gets a non-empty list
      and never falls back to its own PLATFORM_CATALOG -- the DB truly becomes the single source of truth.
    """
    code_map, all_domains = _load_platform_map()
    if not items:
        return list(all_domains)
    out: list[str] = []
    for s in items:
        s = (s or "").strip()
        if not s:
            continue
        out.append(code_map.get(s, s))
    return out

# Reasonable timeouts per operation:
#   monitor involves LLM plan generation + N search engine calls -- even limited to backend + via proxy,
#           30+ queries x 6-8s/query plus LLM rate-limit retries can run 10-15 minutes
#   analyze involves N LLM calls; free LLM (GLM glm-4.5-flash) RPM limits + backoff
#           retries, 30 posts hitting the limit a few times can run 20-30 minutes
#   brief   map-reduce LLM, 5-15 minutes under GLM rate limits
#   respond single LLM call, 30 seconds
TIMEOUT_MONITOR = 1800   # 30 min (many account aliases/competitors + slow browser-direct sources; 20min would time out)
TIMEOUT_ANALYZE = 1800   # 30 min
TIMEOUT_BRIEF = 1200     # 20 min
TIMEOUT_RESPOND = 120    # 2 min
TIMEOUT_QUICK = 15


class SentinelError(RuntimeError):
    """Raised when sentinel-service returns status=failed."""

    def __init__(self, message: str, category: str = "unknown", payload: Optional[dict] = None):
        super().__init__(message)
        self.category = category
        self.payload = payload or {}


def _headers(api_key: Optional[str] = None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = api_key or PLATFORM_OPENAI_KEY
    if key:
        h["X-OpenAI-Key"] = key
    return h


def _unwrap(resp: httpx.Response) -> dict:
    """sentinel-service returns {status, data, error} -> raise SentinelError or return data."""
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") == "failed":
        raise SentinelError(
            body.get("error", "unknown error"),
            category=body.get("category", "unknown"),
            payload=body,
        )
    return body.get("data", body)


# ─────────────────────────── RPC ──────────────────────────────


def run_monitor(
    # ticker optional (non-listed entities pass None) -- sentinel side uses `symbol = ticker or target`
    *, account_id: int, target: str, ticker: Optional[str] = None,
    intent: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    excludes: Optional[list[str]] = None,
    media_allowlist: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_MONITOR,
) -> dict:
    """Synchronous call, for scheduled jobs only (inside the scheduler thread).
    Returns {plan_summary, stats}.
    """
    payload = {
        "account_id": account_id, "target": target, "ticker": ticker,
        "intent": intent, "aliases": aliases or [], "keywords": keywords or [],
        "excludes": excludes or [],
        "media_allowlist": _resolve_media_allowlist(media_allowlist),
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-monitor", json=payload, headers=_headers(api_key),
        ))


def run_analyze(
    *, account_id: int, ticker: str,
    limit: Optional[int] = None,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_ANALYZE,
) -> dict:
    payload = {"account_id": account_id, "ticker": ticker, "limit": limit}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-analyze", json=payload, headers=_headers(api_key),
        ))


def run_brief(
    *, account_id: int, ticker: str,
    date: Optional[str] = None,
    batch_size: int = 20,
    top_noteworthy: int = 5,
    competitors: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_BRIEF,
) -> dict:
    """Returns {path, body, date}. competitors: competitor entity words; the brief uses them to split subject/competitor and add a competitor section."""
    payload = {
        "account_id": account_id, "ticker": ticker, "date": date,
        "batch_size": batch_size, "top_noteworthy": top_noteworthy,
        "competitors": competitors or [],
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-brief", json=payload, headers=_headers(api_key),
        ))


def run_monthly_brief(
    *, account_id: int, ticker: str, year: int, month: int,
    competitors: Optional[list[str]] = None,
    force: bool = False,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_BRIEF,
) -> dict:
    """Generate the monthly sentiment summary. Returns {skipped, path?, body, year, month}.
    Idempotent: if the month already exists, skipped=True with the stored body; force=True forces a rerun."""
    payload = {
        "account_id": account_id, "ticker": ticker,
        "year": year, "month": month,
        "competitors": competitors or [], "force": force,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-monthly-brief", json=payload, headers=_headers(api_key),
        ))


def crawl_eastmoney(
    *, account_id: int, symbol: str,
    pages: int = 2,
    timeout: int = 60,
) -> dict:
    """Fetch Eastmoney Guba directly, without a search engine."""
    payload = {"account_id": account_id, "symbol": symbol, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-eastmoney", json=payload, headers=_headers(),
        ))


def crawl_xueqiu(
    *, account_id: int, symbol: str,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """Fetch Xueqiu discussion posts directly."""
    payload = {"account_id": account_id, "symbol": symbol, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-xueqiu", json=payload, headers=_headers(),
        ))


def crawl_sina(
    *, account_id: int, keyword: str, symbol: Optional[str] = None,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """Fetch Sina Finance search news."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages,
               "symbol": symbol}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-sina", json=payload, headers=_headers(),
        ))


def crawl_eastmoney_news(
    *, account_id: int, keyword: str, symbol: Optional[str] = None,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """Eastmoney news search (news/research reports/announcements)."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages,
               "symbol": symbol}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-eastmoney-news", json=payload, headers=_headers(),
        ))


def crawl_tieba(
    *, account_id: int, keyword: str, symbol: Optional[str] = None,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """Baidu Tieba search."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages,
               "symbol": symbol}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-tieba", json=payload, headers=_headers(),
        ))


def crawl_cls(
    *, account_id: int, keyword: str, symbol: Optional[str] = None,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """Cailianshe flash news + search."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages,
               "symbol": symbol}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-cls", json=payload, headers=_headers(),
        ))


def _crawl_generic(
    endpoint: str, *, account_id: int, keyword: str, symbol: Optional[str] = None,
    pages: int = 3, timeout: int = 60,
) -> dict:
    # symbol = persistence key (the account's ticker or brand name). keyword is just "what to search with";
    # the two are not the same thing -- see the comment on sentinel-service `_stamped`.
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages,
               "symbol": symbol}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/{endpoint}", json=payload, headers=_headers(),
        ))


def crawl_gelonghui(*, account_id: int, keyword: str, symbol: Optional[str] = None,
                    pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-gelonghui", account_id=account_id, keyword=keyword, symbol=symbol, pages=pages, timeout=timeout)


def crawl_wallstreetcn(*, account_id: int, keyword: str, symbol: Optional[str] = None,
                       pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-wallstreetcn", account_id=account_id, keyword=keyword, symbol=symbol, pages=pages, timeout=timeout)


def crawl_yicai(*, account_id: int, keyword: str, symbol: Optional[str] = None,
                pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-yicai", account_id=account_id, keyword=keyword, symbol=symbol, pages=pages, timeout=timeout)


def crawl_36kr(*, account_id: int, keyword: str, symbol: Optional[str] = None,
               pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-36kr", account_id=account_id, keyword=keyword, symbol=symbol, pages=pages, timeout=timeout)


# Added 2026-05-08 EastMoney family: these 3 use a 6-digit A-share ticker (not the brand name)
def crawl_eastmoney_ann(*, account_id: int, ticker: str, symbol: Optional[str] = None,
                        pages: int = 3, timeout: int = 60) -> dict:
    """A-share announcement feed (earnings/resumption/stake changes). ticker must be a 6-digit code; non-A-shares are skipped automatically returning 0."""
    return _crawl_generic("run-crawl-eastmoney-ann", account_id=account_id, keyword=ticker, symbol=symbol, pages=pages, timeout=timeout)


def crawl_eastmoney_research(*, account_id: int, ticker: str, symbol: Optional[str] = None,
                             pages: int = 3, timeout: int = 60) -> dict:
    """Per-stock broker research reports (ratings/target prices). A-shares only, requires a 6-digit ticker."""
    return _crawl_generic("run-crawl-eastmoney-research", account_id=account_id, keyword=ticker, symbol=symbol, pages=pages, timeout=timeout)


def crawl_eastmoney_industry(*, account_id: int, ticker: str, symbol: Optional[str] = None,
                             pages: int = 3, timeout: int = 60) -> dict:
    """Industry research reports (industry reverse-looked-up from the ticker). A-shares only, requires a 6-digit ticker."""
    return _crawl_generic("run-crawl-eastmoney-industry", account_id=account_id, keyword=ticker, symbol=symbol, pages=pages, timeout=timeout)


def crawl_sina_stock(*, account_id: int, ticker: str, symbol: Optional[str] = None,
                     pages: int = 1, timeout: int = 30) -> dict:
    """Sina Finance per-stock news aggregation page (server-rendered HTML).
    Supports A-shares (6-digit codes auto-prefixed sh/sz) and US stocks (pure letters); HK stocks not yet supported.
    Returns about 30 recent news items per call; pages is ignored (this sina page has no pagination).
    """
    return _crawl_generic("run-crawl-sina-stock", account_id=account_id, keyword=ticker, symbol=symbol, pages=pages, timeout=timeout)


def crawl_weixin_album(
    *, account_id: int, album_urls: list[str],
    hydrate_body: bool = False,
    max_articles_per_album: Optional[int] = None,
    symbol: Optional[str] = None,
    timeout: int = 300,
) -> dict:
    """WeChat official account album enumeration (no cookie). timeout 300s -- large accounts with 500+ articles may take minutes.
    When album_urls is an empty list sentinel-service returns {skipped: ...} directly, no time spent.
    """
    payload = {
        "account_id": account_id,
        "album_urls": album_urls,
        "hydrate_body": hydrate_body,
        "max_articles_per_album": max_articles_per_album,
        "symbol": symbol,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-weixin-album", json=payload, headers=_headers(),
        ))


def crawl_newsnow(
    *, account_id: int, source_ids: list[str], symbol: str,
    keywords: Optional[list[str]] = None,
    base_url: Optional[str] = None,
    timeout: int = 120,
) -> dict:
    """Poll the self-hosted newsnow trending lists. Empty keywords = no filter, take everything.
    When source_ids is an empty list sentinel-service returns {skipped: ...} directly, no time spent.
    """
    payload = {
        "account_id": account_id,
        "source_ids": source_ids,
        "keywords": keywords or [],
        "symbol": symbol,
        "base_url": base_url,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-newsnow", json=payload, headers=_headers(),
        ))


def run_respond(
    *, account_id: int, ticker: str,
    source: Optional[str] = None,
    post_id: Optional[str] = None,
    topic: Optional[str] = None,
    situation: Optional[str] = None,
    knowledge: Optional[dict[str, str]] = None,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_RESPOND,
) -> dict:
    payload = {
        "account_id": account_id, "ticker": ticker,
        "source": source, "post_id": post_id, "topic": topic, "situation": situation,
        "knowledge": knowledge,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-respond", json=payload, headers=_headers(api_key),
        ))


def save_websearch_report(
    *, account_id: int,
    report: dict[str, Any],
    timeout: int = TIMEOUT_QUICK,
) -> dict:
    """Write the Yuanbao WebSearch quick-check report into sentinel-service's tenant sentiment store."""
    payload = {"account_id": account_id, "report": report}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/websearch-report", json=payload, headers=_headers(),
        ))


# ────────────────────── Data queries (for the frontend) ────────────────────


def list_posts(account_id: int, ticker: str, limit: int = 50,
               days: int = 1, offset: int = 0,
               start: str | None = None, end: str | None = None,
               sort_by: str = "newest") -> dict:
    """Article list + pagination + sorting.

    Time filter (based on ingested_at): start/end (YYYY-MM-DD, inclusive) take precedence, otherwise days.
    Pagination: limit + offset.
    Sorting: sort_by -- see the comment on the sentinel-service /accounts/{id}/posts endpoint.
    """
    params: dict = {
        "ticker": ticker, "limit": limit, "offset": offset,
        "days": days, "sort_by": sort_by,
    }
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/posts", params=params)
        r.raise_for_status()
        return r.json()


def list_briefs(account_id: int, ticker: str) -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/briefs",
                       params={"ticker": ticker})
        r.raise_for_status()
        return r.json()


def get_brief(account_id: int, brief_id: int) -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/briefs/{brief_id}")
        r.raise_for_status()
        return r.json()


def list_drafts(account_id: int, ticker: str) -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/drafts",
                       params={"ticker": ticker})
        r.raise_for_status()
        return r.json()


def get_today(account_id: int, ticker: str, days: int = 7) -> dict:
    """Aggregate data (KPIs / 7d trend / risk distribution / latest brief / high-risk Top5)."""
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/today",
                       params={"ticker": ticker, "days": days})
        r.raise_for_status()
        return r.json()


def get_websearch_latest(account_id: int, ticker: str | None = None) -> dict:
    """Read the latest Yuanbao WebSearch quick-check report from sentinel-service's tenant sentiment store."""
    params = {"ticker": ticker} if ticker else None
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/websearch-latest", params=params)
        r.raise_for_status()
        return r.json()


def list_newsnow_sources() -> dict:
    """newsnow recommended source catalog (static, used to render tab switching)."""
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/newsnow/sources")
        r.raise_for_status()
        return r.json()


def get_newsnow_hot(source: str, limit: int = 30) -> dict:
    """Current trending list for the given source (pure passthrough, not persisted)."""
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(
            f"{SENTINEL_URL}/newsnow/hot",
            params={"source": source, "limit": limit},
        )
        r.raise_for_status()
        return r.json()


def health() -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/health")
        r.raise_for_status()
        return r.json()
