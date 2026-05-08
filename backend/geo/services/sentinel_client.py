"""HTTP 客户端 — backend → sentinel-service.

封装对 sentinel-service 5 个 RPC 的调用,统一处理超时、错误、API key 注入.
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

# Hardcoded fallback — 仅当 DB 查询失败时使用(冷启动 / 表缺失 / 异常),
# 真正的 source of truth 是 sentiment_platforms 表(由 migration 010 seed)。
# 改 / 加平台不要改这里 — 改 DB(或重跑 migration)。
_PLATFORM_FALLBACK_MAP: dict[str, str] = {
    # finance(主力)
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
    # finance(扩展)
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

# 60s 内存缓存 — pipeline 一次跑会调多个 RPC,避免每次都打 DB。
# admin 改了平台后最多 60s 生效。
_PLATFORM_CACHE: Optional[tuple[dict[str, str], list[str]]] = None
_PLATFORM_CACHE_AT: float = 0.0
_PLATFORM_CACHE_TTL = 60.0
_PLATFORM_CACHE_LOCK = threading.Lock()


def _load_platform_map() -> tuple[dict[str, str], list[str]]:
    """从 DB 读 (code→domain map, 全部 enabled 域名 list)。
    DB 不可达时回落到 _PLATFORM_FALLBACK_MAP — 这样 sentinel-service 即使在
    backend cold start / 表损坏的极端情况下仍能跑(用 27 个内置平台)。"""
    global _PLATFORM_CACHE, _PLATFORM_CACHE_AT
    now = time.time()
    if _PLATFORM_CACHE is not None and (now - _PLATFORM_CACHE_AT) < _PLATFORM_CACHE_TTL:
        return _PLATFORM_CACHE
    with _PLATFORM_CACHE_LOCK:
        if _PLATFORM_CACHE is not None and (now - _PLATFORM_CACHE_AT) < _PLATFORM_CACHE_TTL:
            return _PLATFORM_CACHE
        try:
            # 延迟 import 避免 services/* ↔ models/* 之间的循环引用。
            from geo.database import SessionLocal
            from geo.models.sentiment import SentimentPlatformORM
            db = SessionLocal()
            try:
                rows = db.query(SentimentPlatformORM).filter_by(enabled=True).all()
            finally:
                db.close()
            if not rows:
                # 表存在但还没 seed — 用 fallback 顶住,管理员稍后跑 migration
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
    """admin 改完平台目录后调一下,下一次 RPC 立即生效(可选,不调也最多 60s 后生效)。"""
    global _PLATFORM_CACHE, _PLATFORM_CACHE_AT
    with _PLATFORM_CACHE_LOCK:
        _PLATFORM_CACHE = None
        _PLATFORM_CACHE_AT = 0.0


def _resolve_media_allowlist(items: Optional[list[str]]) -> list[str]:
    """codes → domains。
    - items 含已识别 code → 翻成 domain
    - items 含未识别字符串(例如用户手写 'xhslink.com')→ 原样透传
    - items 空 / None → **展开为全部 enabled 平台域名**,这样 sentinel 永远拿到非空列表,
      不再回落到自己的 PLATFORM_CATALOG — DB 真正成为 single source of truth。
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


def crawl_sina_stock(*, account_id: int, ticker: str, pages: int = 1, timeout: int = 30) -> dict:
    """新浪财经个股新闻聚合页(server-rendered HTML)。
    A 股(6 位代码自动加 sh/sz)和美股(纯字母)都支持;港股暂不支持。
    单次返回约 30 条最近新闻,pages 被忽略(sina 这页无分页)。
    """
    return _crawl_generic("run-crawl-sina-stock", account_id=account_id, keyword=ticker, pages=pages, timeout=timeout)


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


def list_posts(account_id: int, ticker: str, limit: int = 50,
               days: int = 1) -> dict:
    """days: 1=今日(默认),0=全部历史,N>1=最近 N 天."""
    with httpx.Client(timeout=TIMEOUT_QUICK, trust_env=False) as client:
        r = client.get(f"{SENTINEL_URL}/accounts/{account_id}/posts",
                       params={"ticker": ticker, "limit": limit, "days": days})
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
