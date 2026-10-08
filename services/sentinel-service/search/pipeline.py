"""Run a monitoring plan: search each query, dedupe, normalize, ingest into SQLite.

Output rows go into the same `posts` table the eastmoney crawler uses, so the
existing analyzer / brief / drafts / chat / report all work unchanged.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

from storage import (
    connect, init_schema, upsert_post,
    get_query_last_run, upsert_query_last_run,
)

from .baidu import baidu_search
from .cnbing import cnbing_search
from .ddg import ddg_search, throttle
from .sogou import sogou_search, weixin_search
from .mediacrawler import xhs_search, douyin_search
from .searxng import searxng_search
from .weibo import weibo_search
from .zhihu import zhihu_search
from .xueqiu import xueqiu_search


# SearXNG single engine (server side aggregates multiple upstreams + handles anti-bot/IP pool); built-in engines kept for fallback.
DEFAULT_ENGINES = ("searxng",)


# DDG occasionally returns titles where the `&` of an HTML numeric entity has
# been replaced with `||`, e.g. `||#19990;...`. Repair before persisting.
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


# Publish date extraction: when the SearXNG upstream gives no publishedDate, try to extract an
# **absolute** date (must include a 4-digit year) from the title / snippet so the frontend timeline / "last N days" is not all empty.
# Only absolute dates count - relative times ("3 days ago") lack an anchor and are easy to get wrong; better to leave None.
_DATE_PATTERNS = [
    (re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"), (1, 2, 3)),       # 2026-06-15 / 2026/6/15
    (re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), (1, 2, 3)),  # 2026年6月15日 (June 15, 2026)
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


# Date extraction from the URL (level 2, highly reliable): most Chinese news sites put the publish date in the URL -
#   sina   finance.sina.com.cn/.../2026-04-05/...        →  /20YY-MM-DD/
#   eastmoney  /news/20260616150020...   /a/202605173739...  ->  8 consecutive digits 20YYMMDD
#   qq  /rain/a/20260514A00FU400    10jqka  /20260409/c...  ->  8 consecutive digits
# Far more reliable than guessing from the snippet; validate month/day, year 2000-2099, take the first match.
_URL_DATE_RES = [
    re.compile(r"/(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:[-/_.]|$)"),  # /2026-06-15/  /2026/6/15
    re.compile(r"/(20\d{2})/(\d{2})(\d{2})(?:/|[?#]|$)"),             # /2026/0527/ (year/monthday, e.g. eeo)
    re.compile(r"[/_a-zA-Z-](20\d{2})(\d{2})(\d{2})\d*"),             # /20260616 or t20260618 (letter/separator prefix)
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


# ── #2 Junk/non-sentiment page filter (encyclopedias/quotes/stock forum lists/jobs/business registry/content farms) ──────────────
# These pages mention the target name but are not sentiment content; stamped with a recent timestamp they pollute the window. Blocked at the relevance gate before persisting.
_JUNK_DOMAINS = {
    "baike.baidu.com", "baike.so.com", "wenku.baidu.com", "wen.baidu.com",
    "wenku.so.com", "aiqicha.baidu.com", "ai.so.com", "zh.wikipedia.org",
    "en.wikipedia.org", "www.qcc.com", "www.jobui.com", "m.jobui.com",
    "www.199it.com", "www.catl.com", "www.microsoft.com", "azure.microsoft.com",
    "www.azure.cn", "docs.azure.cn", "docs.microsoft.com", "learn.microsoft.com",
    "www.office.com", "account.microsoft.com", "www.onlinedown.net",
}
_JUNK_URL_RE = re.compile(
    r"(quote\.eastmoney|/quote/|/quotes/|/list,|/tags/|guba\.eastmoney\.com/list|"
    r"weibo\.com/u/|/company_|/wiki/|wikipedia\.org|/tashuo/|360kuai\.com|/baiqi/|"
    r"19lou\.com|book118|doc88|woc88|onlinedown|"
    # Code hosting/doc sites (GitHub/Gitee/GitLab etc.): AI-daily/crawler repos often launder news into md files,
    # which get recalled as negative sentiment (e.g. github.com/…/Daily News_AI Builders….md) - not a sentiment source at all
    r"github\.(?:com|io)|githubusercontent|gist\.github|gitee\.com|gitlab\.com|bitbucket\.org|"
    # Tiger community (laohu8): details are /post/<id>; /personal profile pages and /hq quote community lists are not single sentiment posts
    r"laohu8\.com/(?:personal|m/hq|hq|stock|m/stock)|"
    # The company's own static marketing/product pages (not sentiment; also mislabeled as today by a dynamically generated "current time").
    # Only block root-level .html company pages + directories like solution/sustainability; keep /portal/article news.
    r"acmecloud\.example/[a-z_-]+\.html|acmecloud\.example/(?:solution|sustainability|product|about|culture|partner|honor|service)\b)",
    re.I)
# SEO content farms / article-spinning sites: random no-name domains rewrite news to farm rankings (not financial media).
# **Structural signature**: the same PHP article-spinning CMS generates `/mnews/…_article<digits>.htm|shtml` paths
# (e.g. gongshan.liansenbz.com, wateraffair.net) - legitimate media do not use such paths, so we block on it generically.
# These sites **often redirect mobile users to porn/malicious pages** (different content for PC/mobile UA); linking them in a brief is very harmful, so block them hard.
_FARM_RE = re.compile(
    r"(sdzhedu|xbanche|wfshengda|hfteng|shengda\.|zhedu\.|"
    r"liansenbz|wateraffair|ipmanx|"
    # Fixed path shapes of the article-spinning CMS, generalized on (legitimate media do not use these):
    r"/mnews/[^/]*_article\d|"      # …/mnews/xxx_article<digits>.htm
    r"/[a-z]+/blog/\d+_\d+)", re.I)  # …/march/blog/<digits>_<digits>.shtml (ipmanx style)
_JUNK_TITLE_RE = re.compile(
    r"(百科|词条|行情中心|股票价格_|股价行情|股价_|资产负债表|财务分析|数据报告-雪球|"
    r"招聘|工资待遇|工商信息|爱企查|注册.{0,4}指南|开通指南|品牌排行|十大品牌|"
    r"的微博_微博|_相关报道|相关报道/新闻|^最新资讯$|新闻中心|随时随地发现)")


def is_junk_page(url: str, title: str | None) -> bool:
    """Non-sentiment structural page (home/encyclopedia/quotes/list/jobs/business registry/content farm) -> True, dropped before persisting."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    # Bare domain/home page (empty path or only "/"): a navigation/index page, not an article, and the home page's "latest" time would be
    # taken as the publish time and leak into the window (e.g. mt.sohu.com titled "latest news", various official/portal home pages).
    if parsed.path in ("", "/"):
        return True
    # Filter out the Acme Cloud official site entirely (PR/product pages, not third-party sentiment), keeping only /portal/article news;
    # ir.acmecloud.example (investor relations, a different host) is unaffected.
    if host in ("www.acmecloud.example", "acmecloud.example", "m.acmecloud.example") and "/portal/article" not in parsed.path:
        return True
    if host in _JUNK_DOMAINS:
        return True
    if _JUNK_URL_RE.search(url or "") or _FARM_RE.search(url or ""):
        return True
    if title and _JUNK_TITLE_RE.search(title):
        return True
    return False


# ── ③ Fetch full-page meta to fill in the real publish time (for undated posts where "neither engine nor URL has a date") ──────────
_META_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_META_RES = [
    re.compile(r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|'
               r'og:published_time|og:release_time|publishdate|pubdate|pubtime|'
               r'weibo:\s*article:create_at|datePublished|date|apub:time)["\'][^>]*?'
               r'content=["\']([^"\']+)["\']', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\']'
               r'(?:article:published_time|og:published_time|datePublished|pubdate)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
]


def _date_from_text(s: str) -> str | None:
    m = re.search(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})', s or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    tm = re.search(r'(\d{1,2}):(\d{2})', s)
    return f"{y:04d}-{mo:02d}-{d:02d}" + (f"T{int(tm.group(1)):02d}:{tm.group(2)}" if tm else "")


def _visible_publish_date(html: str) -> str | None:
    """Visible Chinese publish time in the page header: '2026年05月19日 16:35' (with year) / '05月15日 12:35' (no year, with hour:minute).

    Only looks at the page header (first ~8000 chars; the publish time usually follows the title) to avoid dates mentioned in the body.
    Without a year, the current year is assumed; if that lands in the future, last year. Still stored as ISO (YYYY-MM-DDTHH:MM).
    """
    head = html[:8000]
    # **Must include "hour:minute"** - the publish header has it (2026年05月19日 16:35), while dates mentioned in the body (截至2026年3月31日, "as of 2026-03-31")
    # do not; this distinguishes them and avoids picking up dates from the body.
    m = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s+(\d{1,2}):(\d{2})', head)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}T{int(m.group(4)):02d}:{m.group(5)}"
    # No year but with hour:minute (NBD: 05月15日 12:35) -> current year, last year if it lands in the future
    m = re.search(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日\s+(\d{1,2}):(\d{2})', head)
    if m:
        from datetime import date as _d
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            today = _d.today()
            y = today.year - (1 if (mo, d) > (today.month, today.day) else 0)
            return f"{y:04d}-{mo:02d}-{d:02d}T{int(m.group(3)):02d}:{m.group(4)}"
    return None


def _looks_like_now(date_str: str | None) -> bool:
    """Whether the scraped "publish time" is approximately **now** (fetch time).

    Some sites (36Kr old /p/ pages, the Acme Cloud official site) put a dynamically generated current time in article:published_time,
    so every fetch returns now - that is render time, not publish time. Values with hour:minute in [now-10min, now+1h] are treated as forged.
    """
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str or "")
    if not m:
        return False          # a pure date does not count as a forged now
    try:
        dt = datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return False
    now = datetime.now()
    return now - timedelta(minutes=10) <= dt <= now + timedelta(hours=1)


def fetch_meta_date(url: str) -> str | None:
    """GET the article page and extract the real publish time from meta / JSON-LD / <time> / visible header time. Failure/none/suspected render time -> None."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10, allow_redirects=True,
                         headers={"User-Agent": _META_UA, "Accept-Language": "zh-CN,zh"})
        r.encoding = r.apparent_encoding or r.encoding   # decode Chinese pages correctly so year/month/day patterns match
        html_txt = r.text[:300000]
    except Exception:
        return None
    for rx in _META_RES:
        m = rx.search(html_txt)
        if m:
            d = _date_from_text(m.group(1))
            if d and not _looks_like_now(d):      # discard publish times forged from the "current time"
                return d
    # Fallback: Cailian Press/NBD etc. put the publish time in visible header text (year-month-day / month-day hour:minute)
    v = _visible_publish_date(html_txt)
    return v if v and not _looks_like_now(v) else None


# Sites that need a browser to pass captcha/JS before the date is available (plain HTTP only reaches the captcha page, e.g. ZAKER's Chaitin WAF)
_SCRAPLING_PY = os.environ.get("SCRAPLING_PYTHON", "/opt/geo/scrapling-venv/bin/python")
_META_FETCH_PY = os.path.join(os.path.dirname(__file__), "meta_fetch.py")
_BROWSER_DATE_DOMAINS = {"www.myzaker.com", "myzaker.com", "m.myzaker.com",
                         "www.toutiao.com", "toutiao.com", "m.toutiao.com"}
_BROWSER_CAP = 8   # the browser is slow (~15-30s each), cap per round


def fetch_meta_date_browser(url: str) -> str | None:
    """For captcha/JS sites, use the separate scrapling-venv browser (passes captcha, wait ~12s) and then extract the publish time."""
    if not url or not os.path.exists(_SCRAPLING_PY):
        return None
    try:
        p = subprocess.run([_SCRAPLING_PY, _META_FETCH_PY, url],
                           capture_output=True, text=True, timeout=160)
    except Exception:
        return None
    lines = (p.stdout or "").strip().splitlines()
    if p.returncode != 0 or not lines:
        return None
    try:
        return json.loads(lines[-1]).get("date")
    except Exception:
        return None


def resolve_undated_dates(conn, symbol: str, day: str, cap: int = 60,
                          workers: int = 8, verbose: bool = True) -> int:
    """For relevant posts ingested today with no date from engine or URL, fetch meta concurrently to fill in the real publish time. Returns the backfill count."""
    import concurrent.futures as _cf
    from storage.db import _aid                      # tenant boundary: every SQL must include it
    aid = _aid()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source, post_id, url FROM posts "
            "WHERE account_id = %s AND symbol = %s AND publish_time IS NULL "
            "AND substr(ingested_at, 1, 10) = %s AND url <> '' LIMIT %s",
            (aid, symbol, day, cap),
        )
        rows = [(r["source"], r["post_id"], r["url"]) for r in cur.fetchall()]
    if not rows:
        return 0
    got: dict[tuple, str] = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_meta_date, u): (s, p) for s, p, u in rows}
        for fut in _cf.as_completed(futs):
            try:
                d = fut.result()
            except Exception:
                d = None
            if d:
                got[futs[fut]] = d
    # Browser fallback: for posts still undated on captcha/JS sites (ZAKER etc.), use Scrapling to pass the captcha and fill the date (slow, capped)
    browser_n = 0
    for s, p, u in rows:
        if (s, p) in got or browser_n >= _BROWSER_CAP:
            continue
        if (urlparse(u).hostname or "").lower() in _BROWSER_DATE_DOMAINS:
            browser_n += 1
            d = fetch_meta_date_browser(u)
            if d:
                got[(s, p)] = d
    with conn.cursor() as cur:
        for (s, p), d in got.items():
            cur.execute("UPDATE posts SET publish_time = %s "
                        "WHERE account_id = %s AND source = %s AND post_id = %s "
                        "AND publish_time IS NULL",
                        (d, aid, s, p))
    if verbose:
        print(f"  [meta-date] 扫 {len(rows)} 条 undated → 抓到 {len(got)} 条"
              f"(其中浏览器 {browser_n} 次)")
    return len(got)


def normalize_result(r: dict, symbol: str) -> dict:
    url = r.get("href") or r.get("url") or ""
    title = _clean(r.get("title"))
    body = _clean(r.get("body"))  # SERP snippet — short but usually enough
    # Publish time resolution (**only reliable sources**): 1) publishedDate from recall -> 2) date embedded in URL (highly reliable).
    # No longer "guess the date from title/snippet text" - it mistakes recent dates mentioned in the body for the publish date, so old articles
    # (e.g. May WeChat articles) get counted into the last 24h. Not available -> None = undated, kept in DB but outside the freshness window.
    pub = r.get("publish_time") or extract_date_from_url(url)
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
    """The "must-contain" term set for the monitoring target: target name / aliases / ticker / same-group targets.

    Used as the relevance gate before persisting: the title / snippet / URL of a hit must contain at least one of them,
    otherwise it is treated as search noise (same-name entities, dictionary definitions, generic term drift, e.g. 「Acme Cloud」 tokenized into 「世纪」 ("century")
    pulling back Baidu Zhidao entries or same-name hotels) and dropped.
    """
    raw = [plan.get("target"), symbol, plan.get("ticker"),
           *(plan.get("targets") or []), *(plan.get("aliases") or [])]
    terms: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if not isinstance(t, str):
            continue
        t = t.strip()
        # Very short pure-Latin terms (<3) easily false-match as substrings; 2 Chinese chars are enough.
        if len(t) < 2 or (t.isascii() and len(t) < 3):
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            terms.append(t)
    return terms


def is_relevant(rec: dict, terms: list[str]) -> bool:
    # #2: non-sentiment structural pages such as encyclopedias/quotes/lists/jobs/business registry/content farms are judged irrelevant (dropped before persisting)
    if is_junk_page(rec.get("url") or "", rec.get("title")):
        return False
    if not terms:
        return True
    # Match only in title+body, **not the URL** - short terms like "acme" hit unrelated domain substrings
    # (e.g. the steel-pipe live stream edostb.acme.weizan.cn), causing false relevance.
    hay = " ".join(
        x for x in (rec.get("title"), rec.get("content")) if x
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
            # Dedicated WeChat Official Account channel: queries containing site:mp.weixin automatically go to weixin.sogou.com
            # resolve_redirect=True: sogou /link?url=… -> real target URL,
            # otherwise normalize_result would mark all sogou hits as "unknown"
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


# Login-walled UGC direct-collection sources: search engines cannot get in (Weibo/Zhihu login walls), so collect directly with cookies.
# A small set of targeted "target name + negative term" queries, not mixed into the SearXNG plan (avoids risk control from high-frequency requests).
_DIRECT_NEG = ("欠薪", "讨薪", "维权", "做空", "裁员", "爆雷", "亏损", "诉讼")
_DIRECT_SOURCES = (
    ("weibo", "WEIBO_COOKIE", weibo_search),
    ("zhihu", "ZHIHU_COOKIE", zhihu_search),
    ("xueqiu", None, xueqiu_search),     # no cookie needed (WAF handled by the Scrapling browser, heavier)
    ("xhs", "XHS_COOKIE", xhs_search),       # Xiaohongshu (MediaCrawler browser, heavier)
    ("douyin", "DOUYIN_COOKIE", douyin_search),  # Douyin (MediaCrawler browser, heavier)
    ("weixin", None, weixin_search),         # WeChat Official Accounts (Sogou WeChat keyword search, HTTP, no cookie needed)
)
# Browser sources (60-120s per query, very slow) / Sogou WeChat (captcha-prone at high frequency): cap the number of alias queries
_BROWSER_SOURCES = ("xueqiu", "xhs", "douyin")
# Per-source alias cap for the "company entity group" (main name + aliases): browser sources are used sparingly, fast sources (Weibo/Zhihu) are opened up
_ALIAS_CAP = {"xueqiu": 1, "xhs": 1, "douyin": 1, "weixin": 3}
_ALIAS_CAP_DEFAULT = 5   # fast HTTP sources such as weibo / zhihu


def _entity_queries(plan: dict, symbol: str) -> list[str]:
    """Company entity query group = main name + aliases (Acme Blue/Acme Labs/Acme Matrix/ACME etc.).

    Sentiment monitoring watches the whole picture of the company "Acme Cloud" (brand/products/subsidiaries), not just the main name -> direct sources also
    search by alias, catching content that mentions the brand/products without the literal main name. Aliases come from plan.aliases (user-maintained,
    same set as the relevance gate); hits still go through is_relevant + excludes, so same-name entities are blocked.
    """
    primary = (plan.get("target") or symbol or "").strip()
    out = [primary] if primary else []
    for a in (plan.get("aliases") or []):
        a = (a or "").strip()
        if a and len(a) >= 2 and a not in out:
            out.append(a)
    return out


def collect_direct_sources(plan: dict, symbol: str, conn, seen: set[str],
                           match_terms: list[str], max_results: int = 15,
                           sleep_s: float = 1.5, verbose: bool = True) -> dict:
    """Direct collection from login-walled platforms such as Weibo / Zhihu (search engine blind spot). Sources without a configured cookie (env) are skipped.

    Reuses the same relevance gate + dedup (seen) + upsert; the publish time is provided structurally by each adapter,
    so publish_time is naturally accurate. Any failing source / query is just skipped without affecting the others.
    """
    primary = (plan.get("target") or symbol or "").strip()
    if not primary:
        return {}
    entity_qs = _entity_queries(plan, symbol)   # main name + company aliases (whole-company view)
    per_source: dict[str, int] = {}
    for name, env_key, fn in _DIRECT_SOURCES:
        if env_key and not os.environ.get(env_key):
            continue
        # Entity group: main name + up to N aliases (sparing for browser/Sogou, opened up for fast sources)
        cap = _ALIAS_CAP.get(name, _ALIAS_CAP_DEFAULT)
        qs = entity_qs[:1 + cap]
        # Fast HTTP sources (Weibo/Zhihu) additionally get a main name x negative term matrix to actively widen negative sentiment coverage
        if name not in _BROWSER_SOURCES and name != "weixin":
            qs = qs + [f"{primary} {n}" for n in _DIRECT_NEG]
        got = 0
        for q in qs:
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
                rec["source"] = name             # unified source name (weibo/zhihu)
                rec["author"] = r.get("author")  # keep the author (normalize sets None by default)
                if not is_relevant(rec, match_terms):
                    continue
                if upsert_post(conn, rec):
                    got += 1
                    per_source[name] = per_source.get(name, 0) + 1
            throttle(sleep_s)
        if verbose:
            print(f"  [direct:{name}] {got} new · {len(qs)} queries")
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
            # Relevance gate: drop if title / snippet / URL does not contain the target / alias / ticker,
            # blocking search noise like same-name entities and dictionary definitions (also saves later LLM analysis cost).
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

    # Login-walled UGC direct collection (Weibo/Zhihu), merged with SearXNG recall and persisted
    direct = collect_direct_sources(plan, symbol, conn, seen, match_terms,
                                    sleep_s=sleep_s, verbose=verbose)
    for s, n in direct.items():
        per_source[s] = per_source.get(s, 0) + n
        inserted += n
        total += n

    # ③ For today's posts where "neither engine nor URL has a date", fetch full-page meta for the real publish time (rescues recent posts, not old news/static pages)
    try:
        from datetime import date as _date
        resolve_undated_dates(conn, symbol, _date.today().isoformat(), verbose=verbose)
    except Exception as e:
        print(f"  [meta-date] 跳过(异常):{e}", file=sys.stderr)

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
