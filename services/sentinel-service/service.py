"""Sentinel (yuqin) microservice - FastAPI wrapper.

The GEO backend calls this service over HTTP, and this service calls yuqin's 4 core functions.

Design points:
- Multi-tenant isolation: each account_id maps to a PG schema (`tenant_{N}`), routed via
  `SET search_path`; the storage package's contextvar `current_account` is bound at the request
  entry. The historical design was one SQLite file per account (switched to PG on 2026-05-21).
- API key injection: the client passes X-OpenAI-Key in a header; this service sets it in env and passes it through.
- Exceptions are wrapped into a dict and returned, never raised to the client (places where yuqin uses sys.exit are intercepted at the main entry).

Environment variables:
- SENTINEL_DATA_DIR    data directory (default ./data)
- SENTINEL_PORT        port (default 8090)
- OPENAI_API_KEY       platform fallback (used when the client does not pass one)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# Add the current directory to sys.path so yuqin modules can be imported directly
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ── Data directory (on-disk by-products: briefs / knowledge / etc.) ──────────────
# Main data (posts / analyses / briefs / drafts / query_runs) has moved to PG schemas;
# this directory only keeps on-disk by-products.
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", str(ROOT / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Display freshness window (days): today aggregates / Top5 / risk distribution / brief only show "content" from the last N days
# (by publish_time, not ingestion time), keeping old posts "crawled today but published in 2024" out of the display.
# Crawling still persists everything incrementally; only the display layer narrows. Default 3 days; set to 1 for "last 24 hours".
DISPLAY_MAX_AGE_DAYS = int(os.environ.get("SENTINEL_DISPLAY_MAX_AGE_DAYS", "3"))


def _account_db_path(account_id: int) -> Path:
    """Historical name: returns the `account_{N}/yuqing.db` path. **The main DB has moved to PG**; this only
    lets old callers borrow `.parent` to get the account's on-disk subdirectory (briefs / knowledge etc.).
    """
    p = DATA_DIR / f"account_{account_id}" / "yuqing.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Multi-tenant contextvar (defined centrally by the storage package; service.py only sets/resets it)
# This replaces the historical 6 layers of monkey-patching: the storage package-level wrapper reads the contextvar dynamically on every connect()
# call, so the snapshot problem of submodules copying the function reference via `from storage import connect`
# no longer exists.
from storage import current_account              # type: ignore  # noqa: E402

# yuqin business entry points
from search import (                              # type: ignore  # noqa: E402
    generate_monitoring_plan, run_plan,
)
from analyzer import analyze_symbol               # type: ignore  # noqa: E402
from brief import generate_brief, generate_monthly_brief  # type: ignore  # noqa: E402
from response import generate_drafts              # type: ignore  # noqa: E402
from response import draft as _draft_module       # type: ignore  # noqa: E402
from crawler.eastmoney import EastmoneyGubaClient # type: ignore  # noqa: E402
from crawler.xueqiu import XueqiuClient          # type: ignore  # noqa: E402
from crawler.sina_finance import SinaFinanceClient # type: ignore  # noqa: E402
from crawler.eastmoney_news import EastmoneyNewsClient # type: ignore  # noqa: E402
from crawler.baidu_tieba import BaiduTiebaClient     # type: ignore  # noqa: E402
from crawler.cls_finance import ClsFinanceClient      # type: ignore  # noqa: E402
from crawler.gelonghui import GelonghuiClient         # type: ignore  # noqa: E402
from crawler.wallstreetcn import WallstreetcnClient   # type: ignore  # noqa: E402
from crawler.yicai import YicaiClient                 # type: ignore  # noqa: E402
from crawler.kr36 import Kr36Client                   # type: ignore  # noqa: E402
from crawler.eastmoney_announcement import EastmoneyAnnouncementClient  # type: ignore  # noqa: E402
from crawler.eastmoney_research import EastmoneyResearchClient          # type: ignore  # noqa: E402
from crawler.eastmoney_industry import EastmoneyIndustryClient          # type: ignore  # noqa: E402
from crawler.sina_stock_news import SinaStockNewsClient                  # type: ignore  # noqa: E402
from crawler.weixin_album import WeixinAlbumClient                       # type: ignore  # noqa: E402
from crawler.newsnow_hub import NewsnowHubClient                         # type: ignore  # noqa: E402
from storage import connect as _yuqin_connect, init_schema, upsert_post  # type: ignore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sentinel")


# ── Utilities: runtime environment + exception wrapping ──────────────────────────────────
def _is_plausible_openai_key(key: Optional[str]) -> bool:
    """A valid OpenAI key always has the 'sk-' prefix. Filters out placeholder junk passed from the upstream backend
    (e.g. internal tokens like 'api-key-...-duze' misconfigured as OPENAI_API_KEY,
    which would override the real key in sentinel-service's own env and cause 401)."""
    return bool(key) and key.startswith("sk-")


@asynccontextmanager
async def _account_context(account_id: int, api_key: Optional[str]):
    """Set the current account + OpenAI API Key, cleaned up when the function returns.

    Only accepts values that look like a valid OpenAI key; empty / placeholder junk is ignored, thus keeping
    the OPENAI_API_KEY from the sentinel-service process's own env so llm_client follows
    the OpenAI -> Qwen fallback chain.
    """
    token = current_account.set(account_id)
    prev_key = os.environ.get("OPENAI_API_KEY")
    overrode = False
    if _is_plausible_openai_key(api_key):
        os.environ["OPENAI_API_KEY"] = api_key  # type: ignore[assignment]
        overrode = True
    elif api_key:
        log.warning(
            "ignoring upstream X-OpenAI-Key (not 'sk-' prefixed); "
            "falling back to service-local key chain",
        )
    try:
        yield
    finally:
        current_account.reset(token)
        if overrode:
            if prev_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = prev_key


def _stamped(records, symbol: Optional[str]):
    """Normalize every record's symbol to the account's canonical symbol.

    Why this stamp is required: analyze / brief / frontend data fetching all use `WHERE symbol = %s` (the account's
    ticker, or the brand name if unset), while keyword-based crawlers each write their own - sina_finance writes the search term,
    weixin_album writes the Official Account name. So **for accounts with a stock ticker, posts from these sources were never
    analyzed**: they sit under symbol='<brand name>' while analyze looks under symbol='ACME';
    the two never meet, and nothing errors (stats only show inserted +N, never "nobody analyzed it").

    With symbol=None the crawler's original value is kept - compatible with callers not yet upgraded.
    """
    for rec in records:
        if symbol:
            rec["symbol"] = symbol
        yield rec


def _wrap(fn, *args, **kwargs) -> dict:
    """Some yuqin functions use sys.exit; the process must not exit here.
    SystemExit is also wrapped as failed."""
    try:
        result = fn(*args, **kwargs)
        return {"status": "success", "data": result}
    except SystemExit as e:
        log.warning("sentinel SystemExit: %s", e)
        return {"status": "failed", "error": str(e), "category": "business"}
    except RuntimeError as e:
        log.warning("sentinel RuntimeError: %s", e)
        return {"status": "failed", "error": str(e), "category": "business"}
    except Exception as e:
        log.exception("sentinel crashed: %s", e)
        return {"status": "failed", "error": str(e), "category": "system"}


# ── Pydantic request models ──────────────────────────────────────────
class MonitorRequest(BaseModel):
    account_id: int
    target: str = Field(..., description="品牌名,例:Acme Cloud")
    ticker: Optional[str] = None
    intent: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, description="AND 修饰词")
    excludes: list[str] = Field(default_factory=list, description="预留,目前 yuqin 未直接支持")
    media_allowlist: list[str] = Field(default_factory=list, description="域名白名单")
    force_all: bool = False
    max_results_per_query: int = 10
    timelimit: Optional[str] = "d"
    auto_widen: bool = True
    region: str = "wt-wt"
    engines: list[str] = Field(default_factory=lambda: ["searxng"])
    # Defaults to SearXNG: server side aggregates multiple upstream engines + handles anti-bot/IP pool centrally.
    # Built-in engines cnbing/baidu/sogou/ddg are still kept; pass engines explicitly to fall back.


class AnalyzeRequest(BaseModel):
    account_id: int
    ticker: str
    limit: Optional[int] = None
    sleep_s: float = 0.0


class BriefRequest(BaseModel):
    account_id: int
    ticker: str
    date: Optional[str] = None
    batch_size: int = 20
    top_noteworthy: int = 5
    competitors: list[str] = Field(default_factory=list,
                                   description="竞品实体词,简报据此分主体/竞品并出竞品模块")


class MonthlyBriefRequest(BaseModel):
    account_id: int
    ticker: str
    year: int
    month: int
    competitors: list[str] = Field(default_factory=list,
                                   description="竞品实体词,月报据此分主体/竞品并出竞品模块")
    force: bool = False          # True = rerun even if already generated; idempotent skip by default


class RespondRequest(BaseModel):
    account_id: int
    ticker: str
    source: Optional[str] = None
    post_id: Optional[str] = None
    topic: Optional[str] = None
    situation: Optional[str] = None
    knowledge: Optional[dict[str, str]] = Field(
        default=None,
        description="{'brand_voice': str, 'legal_redlines': str, 'response_playbook': str}.None=用容器内默认文件",
    )


class CrawlEastmoneyRequest(BaseModel):
    account_id: int
    symbol: str
    pages: int = 2


class CrawlXueqiuRequest(BaseModel):
    account_id: int
    symbol: str
    pages: int = 3
    count: int = 50


class CrawlSinaRequest(BaseModel):
    account_id: int
    keyword: str
    pages: int = 3
    # Symbol to persist under. **Required for keyword-based sources** - see the _stamped comment.
    symbol: Optional[str] = None


class CrawlEastmoneyNewsRequest(BaseModel):
    account_id: int
    keyword: str
    pages: int = 3
    # Symbol to persist under. **Required for keyword-based sources** - see the _stamped comment.
    symbol: Optional[str] = None


class CrawlTiebaRequest(BaseModel):
    account_id: int
    keyword: str
    pages: int = 3
    # Symbol to persist under. **Required for keyword-based sources** - see the _stamped comment.
    symbol: Optional[str] = None


class CrawlClsRequest(BaseModel):
    account_id: int
    keyword: str
    pages: int = 3
    # Symbol to persist under. **Required for keyword-based sources** - see the _stamped comment.
    symbol: Optional[str] = None


class CrawlGenericRequest(BaseModel):
    """Generic search-type crawler request."""
    account_id: int
    keyword: str
    pages: int = 3
    # Symbol to persist under. **Required for keyword-based sources** - see the _stamped comment.
    symbol: Optional[str] = None


class CrawlWeixinAlbumRequest(BaseModel):
    """WeChat Official Account album crawl request."""
    account_id: int
    album_urls: list[str] = Field(default_factory=list,
                                  description="合集 URL 列表(__biz + album_id 完整 URL)")
    hydrate_body: bool = Field(False,
                               description="是否额外抓 mp.weixin 正文(每篇 +1-2s)")
    max_articles_per_album: Optional[int] = Field(
        None, description="单合集上限保护;None=全量")
    symbol: Optional[str] = Field(None, description="入库 symbol;见 _stamped")


class CrawlNewsnowRequest(BaseModel):
    """NewsNow trending aggregation crawl request."""
    account_id: int
    source_ids: list[str] = Field(
        default_factory=list,
        description="newsnow source 列表,如 ['weibo','zhihu','v2ex','toutiao']")
    keywords: list[str] = Field(
        default_factory=list,
        description="标题子串过滤;空 list = 全量入库不过滤")
    symbol: str = Field(..., description="入库 symbol(通常 = account.ticker)")
    base_url: Optional[str] = Field(
        None, description="覆盖 NEWSNOW_BASE_URL env;None=用容器默认")


class WebSearchReportRequest(BaseModel):
    """Request to persist a Yuanbao WebSearch quick-check report. The actual report fields are generated by the backend."""
    account_id: int
    report: dict[str, Any] = Field(default_factory=dict)


# ── FastAPI app ────────────────────────────────────────────────
app = FastAPI(
    title="Sentinel (yuqin) Service",
    description="LLM-native sentiment monitoring microservice",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "data_dir": str(DATA_DIR)}


@app.post("/run-monitor")
async def run_monitor(
    req: MonitorRequest,
    x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key"),
) -> dict:
    """Generate the plan + multi-engine crawl + write posts.

    Returns: {"status": "success"|"failed", "data": stats, "error": "..."}
    stats: {"queries": N, "inserted": N, "total": N, "by_source": {...}, "by_engine": {...}}
    """
    async with _account_context(req.account_id, x_openai_key):
        targets = [req.target] + req.aliases
        targets = [t for t in targets if t]

        def _do() -> dict:
            plan = generate_monitoring_plan(
                target=targets,
                ticker=req.ticker,
                intent=req.intent,
                media=req.media_allowlist or None,
                keywords=req.keywords or None,
                force_all=req.force_all,
            )
            symbol = req.ticker or req.target
            stats = run_plan(
                plan, symbol,
                max_results_per_query=req.max_results_per_query,
                timelimit=req.timelimit,
                auto_widen=req.auto_widen,
                region=req.region,
                engines=req.engines or None,
                verbose=False,
            )
            return {"plan_summary": _summarize_plan(plan), "stats": stats}

        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-analyze")
async def run_analyze(
    req: AnalyzeRequest,
    x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key"),
) -> dict:
    """Run LLM analysis on unanalyzed posts and write analyses."""
    async with _account_context(req.account_id, x_openai_key):
        def _do():
            return analyze_symbol(req.ticker, limit=req.limit, sleep_s=req.sleep_s)
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-brief")
async def run_brief(
    req: BriefRequest,
    x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key"),
) -> dict:
    """Generate the daily brief Markdown, persist it, and return the brief content."""
    async with _account_context(req.account_id, x_openai_key):
        # brief/generate.py writes MD to data/brief_*.md; we make it output to the account directory
        out_dir = _account_db_path(req.account_id).parent / "briefs"

        def _do():
            path = generate_brief(
                req.ticker,
                date=req.date,
                out_dir=out_dir,
                batch_size=req.batch_size,
                top_noteworthy=req.top_noteworthy,
                competitors=req.competitors or None,
            )
            body = Path(path).read_text(encoding="utf-8")
            return {"path": str(path), "body": body, "date": req.date}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-monthly-brief")
async def run_monthly_brief(
    req: MonthlyBriefRequest,
    x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key"),
) -> dict:
    """Generate the monthly sentiment monitoring summary Markdown and persist it (kind=monthly), returning the content. Idempotent by default: skipped if the month is already generated."""
    from storage import brief_exists                       # noqa: PLC0415
    month_start = f"{int(req.year):04d}-{int(req.month):02d}-01"
    async with _account_context(req.account_id, x_openai_key):
        out_dir = _account_db_path(req.account_id).parent / "briefs"

        def _do():
            if not req.force:
                conn = _yuqin_connect()
                init_schema(conn)
                if brief_exists(conn, req.ticker, month_start, kind="monthly"):
                    row = conn.execute(
                        "SELECT body FROM briefs WHERE account_id=%s AND symbol=%s "
                        "AND date=%s AND kind='monthly' "
                        "ORDER BY id DESC LIMIT 1", (req.account_id, req.ticker, month_start),
                    ).fetchone()
                    return {"skipped": True, "year": req.year, "month": req.month,
                            "body": (row["body"] if row else "")}
            path = generate_monthly_brief(
                req.ticker, req.year, req.month,
                out_dir=out_dir, competitors=req.competitors or None,
            )
            body = Path(path).read_text(encoding="utf-8")
            return {"skipped": False, "path": str(path), "body": body,
                    "year": req.year, "month": req.month}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-respond")
async def run_respond(
    req: RespondRequest,
    x_openai_key: Optional[str] = Header(None, alias="X-OpenAI-Key"),
) -> dict:
    """Generate three draft tiers and write drafts."""
    if not (req.post_id or req.topic):
        raise HTTPException(400, "post_id 或 topic 至少传一个")

    async with _account_context(req.account_id, x_openai_key):
        # Temporarily override KNOWLEDGE_DIR so yuqin's _load_knowledge() reads the account knowledge base
        # MVP: if the client passed knowledge, write it to a temp directory; otherwise use the container default knowledge/
        original_dir = _draft_module.KNOWLEDGE_DIR
        if req.knowledge:
            kdir = _account_db_path(req.account_id).parent / "knowledge"
            kdir.mkdir(parents=True, exist_ok=True)
            for key, body in req.knowledge.items():
                if key in ("brand_voice", "legal_redlines", "response_playbook"):
                    (kdir / f"{key}.md").write_text(body, encoding="utf-8")
            _draft_module.KNOWLEDGE_DIR = kdir

        def _do():
            return generate_drafts(
                req.ticker,
                source=req.source,
                post_id=req.post_id,
                topic=req.topic,
                situation=req.situation,
            )
        try:
            return await asyncio.to_thread(_wrap, _do)
        finally:
            _draft_module.KNOWLEDGE_DIR = original_dir


@app.post("/run-crawl-eastmoney")
async def run_crawl_eastmoney(req: CrawlEastmoneyRequest) -> dict:
    """Crawl the Eastmoney stock forum directly (no OpenAI needed)."""
    async with _account_context(req.account_id, None):
        def _do():
            client = EastmoneyGubaClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for page in range(1, req.pages + 1):
                records = list(client.fetch_list(req.symbol, page=page))
                for r in records:
                    if upsert_post(conn, r):
                        inserted += 1
                    total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-xueqiu")
async def run_crawl_xueqiu(req: CrawlXueqiuRequest) -> dict:
    """Crawl Xueqiu discussion posts directly (no OpenAI needed)."""
    async with _account_context(req.account_id, None):
        def _do():
            client = XueqiuClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in client.fetch_pages(req.symbol, pages=req.pages, count=req.count):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-sina")
async def run_crawl_sina(req: CrawlSinaRequest) -> dict:
    """Crawl Sina Finance search news."""
    async with _account_context(req.account_id, None):
        def _do():
            client = SinaFinanceClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in _stamped(client.search_pages(req.keyword, pages=req.pages), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-eastmoney-news")
async def run_crawl_eastmoney_news(req: CrawlEastmoneyNewsRequest) -> dict:
    """Eastmoney news search (news/research reports/announcements)."""
    async with _account_context(req.account_id, None):
        def _do():
            client = EastmoneyNewsClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in _stamped(client.search_pages(req.keyword, pages=req.pages), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-tieba")
async def run_crawl_tieba(req: CrawlTiebaRequest) -> dict:
    """Baidu Tieba search."""
    async with _account_context(req.account_id, None):
        def _do():
            client = BaiduTiebaClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in _stamped(client.search_pages(req.keyword, pages=req.pages), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-cls")
async def run_crawl_cls(req: CrawlClsRequest) -> dict:
    """Cailian Press flash news + search."""
    async with _account_context(req.account_id, None):
        def _do():
            client = ClsFinanceClient()
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in _stamped(client.fetch_telegraph(keyword=req.keyword, count=100), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            for rec in _stamped(client.search_pages(req.keyword, pages=req.pages), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted}
        return await asyncio.to_thread(_wrap, _do)


def _generic_crawl_endpoint(client_cls, source_name: str):
    """Factory function: create a generic search-type crawler endpoint."""
    async def handler(req: CrawlGenericRequest) -> dict:
        async with _account_context(req.account_id, None):
            def _do():
                client = client_cls()
                conn = _yuqin_connect()
                init_schema(conn)
                total = inserted = 0
                for rec in _stamped(client.search_pages(req.keyword, pages=req.pages),
                                    req.symbol):
                    if upsert_post(conn, rec):
                        inserted += 1
                    total += 1
                conn.commit()
                return {"total": total, "inserted": inserted}
            return await asyncio.to_thread(_wrap, _do)
    handler.__name__ = f"run_crawl_{source_name}"
    handler.__doc__ = f"抓取{source_name}."
    return handler


app.post("/run-crawl-gelonghui")(_generic_crawl_endpoint(GelonghuiClient, "gelonghui"))
app.post("/run-crawl-wallstreetcn")(_generic_crawl_endpoint(WallstreetcnClient, "wallstreetcn"))
app.post("/run-crawl-yicai")(_generic_crawl_endpoint(YicaiClient, "yicai"))
app.post("/run-crawl-36kr")(_generic_crawl_endpoint(Kr36Client, "kr36"))

# New EastMoney series (2026-05-08): announcements / stock research reports / industry research
# The keyword these endpoints receive is actually a 6-digit A-share code (passed through by the service layer)
app.post("/run-crawl-eastmoney-ann")(_generic_crawl_endpoint(EastmoneyAnnouncementClient, "eastmoney_ann"))
app.post("/run-crawl-eastmoney-research")(_generic_crawl_endpoint(EastmoneyResearchClient, "eastmoney_research"))
app.post("/run-crawl-eastmoney-industry")(_generic_crawl_endpoint(EastmoneyIndustryClient, "eastmoney_industry"))
app.post("/run-crawl-sina-stock")(_generic_crawl_endpoint(SinaStockNewsClient, "sina_stock"))


@app.post("/run-crawl-weixin-album")
async def run_crawl_weixin_album(req: CrawlWeixinAlbumRequest) -> dict:
    """Enumerate all WeChat Official Account articles by album URL (no cookie, no LLM)."""
    async with _account_context(req.account_id, None):
        def _do():
            if not req.album_urls:
                return {"total": 0, "inserted": 0, "skipped": "no album_urls configured"}
            client = WeixinAlbumClient(hydrate_body=req.hydrate_body)
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in _stamped(client.fetch_albums(
                req.album_urls, max_articles_per_album=req.max_articles_per_album,
            ), req.symbol):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted,
                    "albums": len(req.album_urls)}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/run-crawl-newsnow")
async def run_crawl_newsnow(req: CrawlNewsnowRequest) -> dict:
    """Poll the self-hosted newsnow trending aggregator (no LLM)."""
    async with _account_context(req.account_id, None):
        def _do():
            if not req.source_ids:
                return {"total": 0, "inserted": 0, "skipped": "no source_ids configured"}
            client = NewsnowHubClient(base_url=req.base_url)
            conn = _yuqin_connect()
            init_schema(conn)
            total = inserted = 0
            for rec in client.fetch_sources(
                req.source_ids, keywords=req.keywords, symbol=req.symbol,
            ):
                if upsert_post(conn, rec):
                    inserted += 1
                total += 1
            conn.commit()
            return {"total": total, "inserted": inserted,
                    "sources": len(req.source_ids),
                    "filtered": bool(req.keywords)}
        return await asyncio.to_thread(_wrap, _do)


@app.post("/websearch-report")
async def save_websearch_report(req: WebSearchReportRequest) -> dict:
    """Save a Yuanbao WebSearch sentiment quick-check report into the current account's tenant schema."""
    async with _account_context(req.account_id, None):
        def _do():
            from storage import insert_websearch_report  # type: ignore
            conn = _yuqin_connect()
            init_schema(conn)
            report_id = insert_websearch_report(conn, req.report or {})
            conn.commit()
            return {"id": report_id, "report_id": report_id}
        return await asyncio.to_thread(_wrap, _do)


@app.get("/accounts/{account_id}/websearch-latest")
def websearch_latest(account_id: int, ticker: Optional[str] = None) -> dict:
    """Read the latest Yuanbao WebSearch quick-check report from the current account's tenant schema."""
    with _account_db_context(account_id):
        from storage import latest_websearch_report  # type: ignore
        conn = _yuqin_connect()
        init_schema(conn)
        report = latest_websearch_report(conn, ticker=ticker)
        if not report:
            return {"available": False, "mode": None}
        return {"available": True, **report}


# ── newsnow real-time trending (read-only / not bound to an account / not persisted) ─────────────
# Used by the frontend "Live news / Today's trending" panel: a pure pass-through of the self-hosted newsnow JSON,
# does not consume keywords or write to the posts table, only shown to the user.
# Complements /run-crawl-newsnow: that path uses account.newsnow_sources +
# keyword filtering and writes posts into the analysis pipeline; this path is the "raw trending list".
_NEWSNOW_SOURCE_CATALOG: list[dict] = [
    # Recommended order ≈ available in China + public-reaction sources first
    {"id": "weibo",       "name_zh": "微博热搜",      "name_en": "Weibo",         "category": "social"},
    {"id": "zhihu",       "name_zh": "知乎热榜",      "name_en": "Zhihu",         "category": "social"},
    {"id": "douyin",      "name_zh": "抖音热榜",      "name_en": "Douyin",        "category": "social"},
    {"id": "kuaishou",    "name_zh": "快手热榜",      "name_en": "Kuaishou",      "category": "social"},
    {"id": "bilibili",    "name_zh": "B 站热搜",      "name_en": "Bilibili",      "category": "social"},
    {"id": "tieba",       "name_zh": "百度贴吧",      "name_en": "Tieba",         "category": "social"},
    {"id": "hupu",        "name_zh": "虎扑步行街",    "name_en": "Hupu",          "category": "social"},
    {"id": "douban",      "name_zh": "豆瓣热榜",      "name_en": "Douban",        "category": "social"},
    {"id": "toutiao",     "name_zh": "今日头条",      "name_en": "Toutiao",       "category": "news"},
    {"id": "baidu",       "name_zh": "百度热搜",      "name_en": "Baidu",         "category": "news"},
    {"id": "thepaper",    "name_zh": "澎湃新闻",      "name_en": "ThePaper",      "category": "news"},
    {"id": "ifeng",       "name_zh": "凤凰新闻",      "name_en": "iFeng",         "category": "news"},
    {"id": "ithome",      "name_zh": "IT 之家",       "name_en": "IThome",        "category": "tech"},
    {"id": "36kr",        "name_zh": "36 氪",         "name_en": "36Kr",          "category": "tech"},
    {"id": "huxiu",       "name_zh": "虎嗅",          "name_en": "Huxiu",         "category": "tech"},
    {"id": "juejin",      "name_zh": "掘金",          "name_en": "Juejin",        "category": "tech"},
    {"id": "ghxi",        "name_zh": "果核剥壳",      "name_en": "Ghxi",          "category": "tech"},
    {"id": "sspai",       "name_zh": "少数派",        "name_en": "Sspai",         "category": "tech"},
    {"id": "smzdm",       "name_zh": "什么值得买",    "name_en": "SMZDM",         "category": "tech"},
    {"id": "xueqiu",      "name_zh": "雪球热帖",      "name_en": "Xueqiu",        "category": "finance"},
    {"id": "gelonghui",   "name_zh": "格隆汇",        "name_en": "Gelonghui",     "category": "finance"},
    {"id": "wallstreetcn", "name_zh": "华尔街见闻",   "name_en": "WallstreetCN",  "category": "finance"},
    {"id": "cls",         "name_zh": "财联社",        "name_en": "CaiLianShe",    "category": "finance"},
    {"id": "jin10",       "name_zh": "金十数据",      "name_en": "Jin10",         "category": "finance"},
    {"id": "fastbull",    "name_zh": "法布财经",      "name_en": "FastBull",      "category": "finance"},
]


@app.get("/newsnow/sources")
def newsnow_sources() -> dict:
    """Return the newsnow recommended source catalog (static). The frontend uses it to render tab switching."""
    return {"count": len(_NEWSNOW_SOURCE_CATALOG), "items": _NEWSNOW_SOURCE_CATALOG}


@app.get("/newsnow/hot")
async def newsnow_hot(source: str, limit: int = 30) -> dict:
    """Fetch the current trending list for the given source (for Live news / Today's trending).
    - Not persisted, not analyzed, not filtered; a pure pass-through of newsnow JSON
    - When newsnow is unreachable, returns items=[] + error so the frontend degrades gracefully
    - limit defaults to 30, max 100
    """
    source = (source or "").strip().lower()
    if not source:
        raise HTTPException(400, "missing required query param: source")
    limit = max(1, min(int(limit), 100))

    def _do() -> dict:
        from datetime import datetime, timezone
        client = NewsnowHubClient()
        items = client.fetch_source(source)
        # Graceful degradation: newsnow returns no timestamps, so the frontend displays fetched_at = server time
        return {
            "source": source,
            "items": items[:limit] if items else [],
            "count": min(len(items), limit) if items else 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    return await asyncio.to_thread(_do)


# ── Data query endpoints (for the GEO backend to fetch data for frontend rendering) ──────────
@app.get("/accounts/{account_id}/posts")
def list_posts(account_id: int, ticker: str, limit: int = 50,
               offset: int = 0, days: int = 1,
               start: str | None = None, end: str | None = None,
               sort_by: str = "newest") -> dict:
    """Return posts JOIN analyses (for the Articles tab).

    Time filtering (based on ingested_at):
      - start/end (YYYY-MM-DD, inclusive) take precedence; either end may be omitted.
      - Otherwise by days: 1 = today (default), N>1 = last N days, 0 = all history.

    Sort sort_by (default newest) - supports:
      newest / oldest / engagement / replies / likes / shares / views /
      cluster / mediaAZ / mediaZA / scoreSentiment / scoreInfluence.
      Unknown values fall back to newest on the backend.

    Pagination: limit + offset. Returns:
      items[], count (this page), total (total rows matching the time filter), offset, days, start, end, sort_by.
    """
    from storage import analyses_for_symbol, analyses_count_for_symbol  # type: ignore
    with _account_db_context(account_id):
        conn = _yuqin_connect()
        init_schema(conn)
        total = analyses_count_for_symbol(conn, ticker, days=days, start=start, end=end)
        rows = analyses_for_symbol(
            conn, ticker, days=days, start=start, end=end,
            limit=limit, offset=offset, sort_by=sort_by,
        )
        out = [_row_to_dict(r) for r in rows]
    return {
        "count": len(out), "items": out, "total": int(total),
        "offset": int(offset), "limit": int(limit),
        "days": days, "start": start, "end": end,
        "sort_by": sort_by,
    }


@app.get("/accounts/{account_id}/briefs")
def list_briefs(account_id: int, ticker: str, kind: Optional[str] = None) -> dict:
    """Return the account's briefs (metadata only; body via /briefs/{id}).
    kind optionally filters (daily/monthly); if omitted, returns all with a kind field so the frontend can distinguish."""
    with _account_db_context(account_id):
        conn = _yuqin_connect()
        init_schema(conn)
        sql = ("SELECT id, symbol, date, kind, model, generated_at FROM briefs "
               "WHERE account_id = %s AND symbol = %s")
        params: list = [account_id, ticker]
        if kind:
            sql += " AND kind = %s"
            params.append(kind)
        sql += " ORDER BY date DESC, id DESC"
        rows = list(conn.execute(sql, tuple(params)))
        return {"count": len(rows), "items": [dict(r) for r in rows]}


@app.get("/accounts/{account_id}/briefs/{brief_id}")
def get_brief(account_id: int, brief_id: int) -> dict:
    with _account_db_context(account_id):
        conn = _yuqin_connect()
        row = conn.execute(
            "SELECT * FROM briefs WHERE account_id = %s AND id = %s",
            (account_id, brief_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "brief not found")
        return dict(row)


@app.get("/accounts/{account_id}/drafts")
def list_drafts(account_id: int, ticker: str) -> dict:
    with _account_db_context(account_id):
        conn = _yuqin_connect()
        init_schema(conn)
        rows = list(conn.execute(
            "SELECT * FROM drafts WHERE account_id = %s AND symbol = %s ORDER BY id DESC",
            (account_id, ticker),
        ))
        return {"count": len(rows), "items": [dict(r) for r in rows]}


@app.get("/accounts/{account_id}/today")
def today_aggregation(account_id: int, ticker: str, days: int = 7) -> dict:
    """Tab 1 data aggregation - one request gets everything needed to render: KPIs / 7-day trend / risk distribution / latest brief / high-risk Top5.

    Aggregated in sentinel-service so the frontend does not have to pull hundreds of posts and compute itself.
    """
    from collections import Counter
    from datetime import date, datetime, timedelta

    with _account_db_context(account_id):
        conn = _yuqin_connect()
        init_schema(conn)

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        # Content freshness floor: old posts whose publish date (publish_time) is earlier than this are not shown, even if crawled today.
        recency_cutoff = (date.today() - timedelta(days=DISPLAY_MAX_AGE_DAYS - 1)).isoformat()

        # Today's KPIs: posts x analyses (only is_relevant=1, consistent with the trend chart/risk pie/article list below)
        # Plus the freshness gate: COALESCE(publish_time, ingested_at) >= recency_cutoff - posts whose publish_time
        # is known and stale (e.g. a 2024 article crawled today) are excluded; undated ones fall back to ingestion time (all ingested today, counted as new).
        kpi_sql = """
            SELECT
                count(*) AS total,
                sum(CASE WHEN a.risk_level IN ('medium','high') THEN 1 ELSE 0 END) AS high_risk,
                avg(a.sentiment_score) AS avg_sentiment,
                count(DISTINCT p.source) AS active_sources
            FROM posts p JOIN analyses a
              ON a.account_id = p.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE p.account_id = %s AND p.symbol = %s
              AND a.is_relevant = 1
              AND substr(p.ingested_at, 1, 10) = %s
              AND COALESCE(p.publish_time, p.ingested_at) >= %s
        """
        kpi_row = conn.execute(kpi_sql, (account_id, ticker, today, recency_cutoff)).fetchone()
        prev_row = conn.execute(kpi_sql, (account_id, ticker, yesterday, recency_cutoff)).fetchone()

        # N-day stacked sentiment bars: bucketed strictly by publish_time (NULLs dropped).
        # Why: a single crawl can pull back half a year of history; bucketing by ingested_at would pile all history
        # onto the first ingestion day - a giant bar on the left, blank in the middle. Posts with NULL publish_time (mainly
        # search result pages where the publish date could not be parsed) cannot be assigned to a specific date, and falling back
        # to ingested_at would recreate the first-ingestion-day spike, so they are dropped. Today's
        # KPIs are still counted by ingested_at; the semantics differ, so the inconsistency is allowed.
        trend_rows = list(conn.execute("""
            SELECT
                substr(p.publish_time, 1, 10) AS d,
                a.sentiment_label AS sl,
                count(*) AS c
            FROM posts p JOIN analyses a
              ON a.account_id = p.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE p.account_id = %s AND p.symbol = %s AND a.is_relevant = 1
              AND p.publish_time IS NOT NULL
              AND substr(p.publish_time, 1, 10) >= %s
            GROUP BY d, sl
            ORDER BY d ASC
        """, (account_id, ticker, cutoff)))

        trend_dict: dict[str, dict] = {}
        for r in trend_rows:
            d = r["d"]
            if not d:
                continue
            bucket = trend_dict.setdefault(d, {"date": d[5:], "bullish": 0, "neutral": 0, "bearish": 0, "mixed": 0})
            label = (r["sl"] or "").lower()
            if label in bucket:
                bucket[label] = (bucket[label] or 0) + r["c"]
            else:
                bucket["neutral"] += r["c"]

        # Fill in N consecutive days (dates without data get 0)
        trend = []
        for i in range(days - 1, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            if d in trend_dict:
                trend.append(trend_dict[d])
            else:
                trend.append({"date": d[5:], "bullish": 0, "neutral": 0, "bearish": 0, "mixed": 0})

        # Risk distribution (within the freshness window): for the pie chart. Also narrowed by publish_time, no longer counting stale content.
        risk_rows = list(conn.execute("""
            SELECT a.risk_level AS lvl, count(*) AS c
            FROM analyses a JOIN posts p
              ON p.account_id = a.account_id AND a.source = p.source AND a.post_id = p.post_id
            WHERE a.account_id = %s AND a.symbol = %s AND a.is_relevant = 1
              AND COALESCE(p.publish_time, p.ingested_at) >= %s
            GROUP BY lvl
        """, (account_id, ticker, recency_cutoff)))
        RISK_COLORS = {"none": "#94a3b8", "low": "#fbbf24", "medium": "#fb923c", "high": "#ef4444"}
        risk_dist = []
        for lvl in ("none", "low", "medium", "high"):
            count = next((r["c"] for r in risk_rows if (r["lvl"] or "none") == lvl), 0)
            risk_dist.append({"level": lvl, "count": count, "color": RISK_COLORS[lvl]})

        # Latest brief
        brief = conn.execute(
            "SELECT * FROM briefs WHERE account_id = %s AND symbol = %s "
            "ORDER BY date DESC, id DESC LIMIT 1", (account_id, ticker),
        ).fetchone()
        latest_brief = dict(brief) if brief else None

        # High-risk Top 5 (same formula as yuqin chat agent top_posts)
        top_rows = list(conn.execute("""
            SELECT a.source, a.post_id, p.title, p.author, p.publish_time, p.url,
                   p.view_count, p.reply_count, p.content,
                   a.summary, a.sentiment_label, a.sentiment_score,
                   a.topics_json, a.entities_json, a.emotions_json,
                   a.stance, a.intent, a.factuality,
                   a.risk_level, a.risk_signals_json,
                   a.influence_potential, a.hidden_meaning, a.citations_json,
                   a.reasoning, a.is_relevant
            FROM analyses a JOIN posts p
              ON p.account_id = a.account_id AND p.source = a.source AND p.post_id = a.post_id
            WHERE a.account_id = %s AND a.symbol = %s AND a.is_relevant = 1
              AND COALESCE(p.publish_time, p.ingested_at) >= %s
            ORDER BY (coalesce(a.influence_potential,0)
                     * abs(coalesce(a.sentiment_score,0)) + 0.01) DESC,
                     coalesce(p.view_count,0) DESC
            LIMIT 5
        """, (account_id, ticker, recency_cutoff)))
        top_posts = [_row_to_dict(r) for r in top_rows]

        total_today = kpi_row["total"] or 0
        high_risk_today = kpi_row["high_risk"] or 0
        avg_sentiment_today = float(kpi_row["avg_sentiment"]) if kpi_row["avg_sentiment"] is not None else 0.0
        total_prev = prev_row["total"] or 0
        high_risk_prev = prev_row["high_risk"] or 0
        avg_sentiment_prev = float(prev_row["avg_sentiment"]) if prev_row["avg_sentiment"] is not None else 0.0

        def _pct(curr: float, prev: float) -> int:
            if prev == 0:
                return 0
            return round((curr - prev) / abs(prev) * 100)

        return {
            "ticker": ticker,
            "kpi": {
                "total_today": total_today,
                "high_risk": high_risk_today,
                "avg_sentiment": avg_sentiment_today,
                "active_sources": kpi_row["active_sources"] or 0,
                "trend_total_pct": _pct(total_today, total_prev),
                "trend_risk_pct": _pct(high_risk_today, high_risk_prev),
                # Sentiment scores are already in -1..+1; difference x 100 as percentage points is more intuitive
                "trend_sentiment_pct": round((avg_sentiment_today - avg_sentiment_prev) * 100),
            },
            "sentiment_trend": trend,
            "risk_dist": risk_dist,
            "latest_brief": latest_brief,
            "top_posts": top_posts,
        }


# ── helpers ────────────────────────────────────────────────────
from contextlib import contextmanager


@contextmanager
def _account_db_context(account_id: int):
    token = current_account.set(account_id)
    try:
        yield
    finally:
        current_account.reset(token)


def _summarize_plan(plan: dict) -> dict:
    """Trimmed plan for the client, avoiding sending the whole LLM output."""
    return {
        "target": plan.get("target"),
        "ticker": plan.get("ticker"),
        "platforms_count": len(plan.get("platforms") or []),
        "queries_count": len(plan.get("queries") or []),
    }


def _row_to_dict(r) -> dict:
    """row (psycopg dict_row is already a dict) -> dict, with JSON fields deserialized."""
    d = dict(r)
    for k in ("emotions_json", "topics_json", "entities_json", "risk_signals_json", "citations_json"):
        if k in d and d[k]:
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except Exception:
                pass
            del d[k]
    return d


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SENTINEL_PORT", "8090"))
    uvicorn.run("service:app", host="0.0.0.0", port=port, log_level="info")
