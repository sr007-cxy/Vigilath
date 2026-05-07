"""HTTP 客户端 — backend → sentinel-service.

封装对 sentinel-service 5 个 RPC 的调用,统一处理超时、错误、API key 注入.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

SENTINEL_URL = os.environ.get("SENTINEL_SERVICE_URL", "http://localhost:8090")
PLATFORM_OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

# 不同操作的合理超时:
#   monitor 涉及 LLM 生成 plan + N 个搜索引擎调用 — 即使限定 backend + 走代理,
#           30+ query × 6-8s/query 加上 LLM 限速重试可能跑到 10-15 分钟
#   analyze 涉及 N 次 LLM 调用,免费 LLM(GLM glm-4.5-flash)RPM 限速 + 退避
#           重试,30 篇帖子撞几次限速就可能跑 20-30 分钟
#   brief   map-reduce LLM,GLM 限速下 5-15 分钟
#   respond 单次 LLM,30 秒
TIMEOUT_MONITOR = 1200   # 20 min
TIMEOUT_ANALYZE = 1800   # 30 min
TIMEOUT_BRIEF = 1200     # 20 min
TIMEOUT_RESPOND = 120    # 2 min
TIMEOUT_QUICK = 15


class SentinelError(RuntimeError):
    """sentinel-service 返回 status=failed 时抛."""

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
    """sentinel-service 返回 {status, data, error} → 抛 SentinelError 或 return data."""
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
    *, account_id: int, target: str, ticker: str,
    intent: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    excludes: Optional[list[str]] = None,
    media_allowlist: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_MONITOR,
) -> dict:
    """同步调用,定时任务专用(scheduler thread 内).
    返回 {plan_summary, stats}.
    """
    payload = {
        "account_id": account_id, "target": target, "ticker": ticker,
        "intent": intent, "aliases": aliases or [], "keywords": keywords or [],
        "excludes": excludes or [], "media_allowlist": media_allowlist or [],
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
    api_key: Optional[str] = None,
    timeout: int = TIMEOUT_BRIEF,
) -> dict:
    """返回 {path, body, date}."""
    payload = {
        "account_id": account_id, "ticker": ticker, "date": date,
        "batch_size": batch_size, "top_noteworthy": top_noteworthy,
    }
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-brief", json=payload, headers=_headers(api_key),
        ))


def crawl_eastmoney(
    *, account_id: int, symbol: str,
    pages: int = 2,
    timeout: int = 60,
) -> dict:
    """直接抓东方财富股吧,不依赖搜索引擎."""
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
    """直接抓雪球讨论帖."""
    payload = {"account_id": account_id, "symbol": symbol, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-xueqiu", json=payload, headers=_headers(),
        ))


def crawl_sina(
    *, account_id: int, keyword: str,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """抓新浪财经搜索新闻."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-sina", json=payload, headers=_headers(),
        ))


def crawl_eastmoney_news(
    *, account_id: int, keyword: str,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """东财资讯搜索(新闻/研报/公告)."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-eastmoney-news", json=payload, headers=_headers(),
        ))


def crawl_tieba(
    *, account_id: int, keyword: str,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """百度贴吧搜索."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-tieba", json=payload, headers=_headers(),
        ))


def crawl_cls(
    *, account_id: int, keyword: str,
    pages: int = 3,
    timeout: int = 60,
) -> dict:
    """财联社快讯+搜索."""
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/run-crawl-cls", json=payload, headers=_headers(),
        ))


def _crawl_generic(
    endpoint: str, *, account_id: int, keyword: str,
    pages: int = 3, timeout: int = 60,
) -> dict:
    payload = {"account_id": account_id, "keyword": keyword, "pages": pages}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        return _unwrap(client.post(
            f"{SENTINEL_URL}/{endpoint}", json=payload, headers=_headers(),
        ))


def crawl_gelonghui(*, account_id: int, keyword: str, pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-gelonghui", account_id=account_id, keyword=keyword, pages=pages, timeout=timeout)


def crawl_wallstreetcn(*, account_id: int, keyword: str, pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-wallstreetcn", account_id=account_id, keyword=keyword, pages=pages, timeout=timeout)


def crawl_yicai(*, account_id: int, keyword: str, pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-yicai", account_id=account_id, keyword=keyword, pages=pages, timeout=timeout)


def crawl_36kr(*, account_id: int, keyword: str, pages: int = 3, timeout: int = 60) -> dict:
    return _crawl_generic("run-crawl-36kr", account_id=account_id, keyword=keyword, pages=pages, timeout=timeout)


# 2026-05-08 新增 EastMoney 系:这 3 个使用 6 位 A 股 ticker(不是品牌名)
def crawl_eastmoney_ann(*, account_id: int, ticker: str, pages: int = 3, timeout: int = 60) -> dict:
    """A 股公告流(财报/复牌/增减持)。ticker 必须是 6 位代码,非 A 股自动跳过返回 0."""
    return _crawl_generic("run-crawl-eastmoney-ann", account_id=account_id, keyword=ticker, pages=pages, timeout=timeout)


def crawl_eastmoney_research(*, account_id: int, ticker: str, pages: int = 3, timeout: int = 60) -> dict:
    """个股券商研报(评级/目标价)。仅 A 股,需 6 位 ticker."""
    return _crawl_generic("run-crawl-eastmoney-research", account_id=account_id, keyword=ticker, pages=pages, timeout=timeout)


def crawl_eastmoney_industry(*, account_id: int, ticker: str, pages: int = 3, timeout: int = 60) -> dict:
    """行业研究报告(基于 ticker 反查行业)。仅 A 股,需 6 位 ticker."""
    return _crawl_generic("run-crawl-eastmoney-industry", account_id=account_id, keyword=ticker, pages=pages, timeout=timeout)


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


# ────────────────────── 数据查询(给前端用)────────────────────


def list_posts(account_id: int, ticker: str, limit: int = 50) -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/posts",
                       params={"ticker": ticker, "limit": limit})
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
    """聚合数据(KPI / 7d 趋势 / 风险分布 / 最新简报 / 高风险 Top5)."""
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/today",
                       params={"ticker": ticker, "days": days})
        r.raise_for_status()
        return r.json()


def health() -> dict:
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/health")
        r.raise_for_status()
        return r.json()
