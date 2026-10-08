"""Sogou sogou.com search - free, no API key needed, deepest Chinese index.

Two endpoints:
- www.sogou.com/web         - general web search, supports site: filtering directly
- weixin.sogou.com/weixin   - dedicated WeChat Official Account search (deepest index of mp.weixin.qq.com)

Public interface aligned with baidu/cnbing/ddg: sogou_search(q, max_results, timelimit) -> [{title, href, body}].

Why add this:
- DDG is rate-limited on EC2; baidu needs cookies; cnbing hits CAPTCHAs overseas
- Sogou's Chinese index is broad (covers Weibo/WeChat/Xueqiu/Toutiao/Zhihu)
- WeChat Official Account search is Sogou's official exclusive entry; no other engine matches it

Note: hrefs are sogou.com/link?url=... redirect links, which is what first-level callers get;
for the real URL set resolve_redirect=True (slow, needs an extra GET per result).
"""
from __future__ import annotations
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
import requests
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

from .cookie_pool import CookiePool

_CST = timezone(timedelta(hours=8))
_WX_TS_RE = re.compile(r"timeConvert\('(\d{10})'\)")

_TIMEOUT = 12
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _is_blocked(html: str) -> bool:
    return "请输入验证码" in html or "captcha" in html.lower() or "robot.html" in html


def _build_session(cookie: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    if cookie:
        s.headers["Cookie"] = cookie
    return s


def _resolve_redirect(href: str, session: requests.Session,
                      base: str = "https://www.sogou.com") -> str:
    """sogou /link?url=... redirect -> real URL. Returns the input unchanged on failure.

    base: host used to join relative links (WeChat results must use https://weixin.sogou.com, otherwise they jump to the home page).
    The WeChat redirect page assembles the URL in multiple JS pieces (url += '...'), so all pieces must be joined.
    """
    if not href.startswith("/link?") and "sogou.com/link?" not in href:
        return href
    full = href if href.startswith("http") else base + href
    try:
        r = session.head(full, timeout=8, allow_redirects=False)
        loc = r.headers.get("Location")
        if loc and loc != "/":
            return loc
        # JS redirect: after GET, extract from meta refresh / window.location / url+= pieces
        r = session.get(full, timeout=8, allow_redirects=False)
        # WeChat: url += 'http://mp.weixin...'; url += '&...';  multi-piece concatenation
        frags = re.findall(r"url\s*\+=\s*'([^']*)'", r.text)
        if frags:
            real = "".join(frags)
            if real.startswith("http"):
                return real
        m = re.search(r'window\.location\.replace\("([^"]+)"', r.text)
        if m:
            return m.group(1)
        m = re.search(r'URL=([^"\']+)', r.text)
        if m:
            return m.group(1)
    except requests.RequestException:
        pass
    return full


def _parse_sogou_web(html: str) -> list[dict]:
    """Parse the sogou.com/web results page."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen = set()
    # Main result container: div.vrwrap (new) / div.results > div.vrwrap
    for div in soup.select("div.vrwrap, div.results .vrwrap"):
        h3 = div.select_one("h3.vr-title, h3.vrTitle")
        a = (h3.select_one("a[href]") if h3 else None) or div.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        title = (a.get_text(strip=True) or "")
        # Snippet
        body_el = div.select_one(".str-info, .ft, .str_info")
        body = body_el.get_text(" ", strip=True) if body_el else ""
        results.append({"title": title[:200], "href": href, "body": body[:300]})
    return results


def _parse_sogou_weixin(html: str) -> list[dict]:
    """Parse the weixin.sogou.com/weixin results page."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen = set()
    for li in soup.select("ul.news-list li, .news-box ul li"):
        h3 = li.select_one("h3 a, .txt-box h3 a")
        if not h3:
            continue
        href = (h3.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        title = h3.get_text(strip=True)
        body_el = li.select_one(".txt-info, p.txt-info")
        body = body_el.get_text(" ", strip=True) if body_el else ""
        # Publish time: <span class="s2"><script>timeConvert('UNIX')</script></span>
        pub = None
        m = _WX_TS_RE.search(li.decode())
        if m:
            try:
                pub = datetime.fromtimestamp(int(m.group(1)), _CST).isoformat(timespec="minutes")
            except Exception:
                pub = None
        # Official Account name: <span class="all-time-y2">account name</span>
        acc_el = li.select_one(".s-p .all-time-y2, .all-time-y2")
        author = acc_el.get_text(strip=True) if acc_el else None
        results.append({"title": title[:200], "href": href, "body": body[:300],
                        "publish_time": pub, "author": author})
    return results


def sogou_search(query: str, max_results: int = 10,
                 timelimit: str | None = None,
                 *, channel: str | None = None,
                 resolve_redirect: bool = False) -> list[dict]:
    """Search and return [{title, href, body}].

    channel:
      - None / 'web'  -> www.sogou.com/web (general, supports site:)
      - 'weixin'      -> weixin.sogou.com/weixin (WeChat Official Accounts only; site:mp.weixin.qq.com is stripped from the query automatically)
      - 'auto'        -> use weixin if the query contains site:mp.weixin, otherwise web

    timelimit: not directly supported by Sogou general web search; the WeChat endpoint supports sort=date.
    resolve_redirect: whether to resolve /link?url= into the real URL (slow, default False).
    """
    if channel == "auto":
        channel = "weixin" if "site:mp.weixin.qq.com" in query else "web"

    if channel == "weixin":
        # WeChat endpoint: strip the site: prefix, since this endpoint only searches mp.weixin anyway
        q = re.sub(r"site:mp\.weixin\.qq\.com\s*", "", query).strip()
        params = {"type": "2", "query": q, "ie": "utf8"}
        if timelimit:
            params["sort"] = "date"  # newest first approximates timelimit 'd'/'w'
        url = "https://weixin.sogou.com/weixin"
    else:
        params = {"query": query, "num": max(min(max_results, 50), 10)}
        url = "https://www.sogou.com/web"

    # Rotate the cookie pool per request; also try once without a cookie. If a captcha page is returned, mark it dead and move to the next.
    cpool = CookiePool("sogou")
    attempts = list(cpool.try_cookies()) or [(None, None)]
    session = html = None
    for idx, cookie in attempts:
        session = _build_session(cookie)
        try:
            r = session.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  [sogou] request failed: {e}", file=sys.stderr)
            continue
        r.encoding = "utf-8"
        if _is_blocked(r.text):
            if idx is not None:
                cpool.mark_dead(idx, "CAPTCHA/验证页")
            continue
        if idx is not None:
            cpool.mark_ok(idx)
        html = r.text
        break
    if html is None:
        hint = "" if cpool.count() else " (set SOGOU_COOKIE env var to bypass)"
        print(f"  [sogou] CAPTCHA / verification page, no usable cookie{hint}", file=sys.stderr)
        return []

    if channel == "weixin":
        results = _parse_sogou_weixin(html)
    else:
        results = _parse_sogou_web(html)

    if resolve_redirect:
        base = "https://weixin.sogou.com" if channel == "weixin" else "https://www.sogou.com"
        for r_ in results[:max_results]:
            r_["href"] = _resolve_redirect(r_["href"], session, base)
            time.sleep(0.2)  # throttle

    return results[:max_results]


def weixin_search(query: str, max_results: int = 15,
                  timelimit: str | None = None) -> list[dict]:
    """WeChat Official Account keyword direct collection (Sogou WeChat dedicated entry) - used by pipeline._DIRECT_SOURCES.

    Returns [{title,href,body,publish_time,author}]: href resolved to the real permanent mp.weixin.qq.com link
    (resolve_redirect), publish_time from the results page's timeConvert timestamp, author = Official Account name.
    Keyword search over all Official Account articles (not subscribed specific accounts), exactly "using the keywords in the DB". Failure/captcha -> [].
    """
    try:
        return sogou_search(query, max_results=max_results, timelimit=timelimit,
                            channel="weixin", resolve_redirect=True)
    except Exception as e:
        print(f"  [weixin] error: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    # python -m search.sogou 'site:weibo.com Acme Cloud'
    q = " ".join(sys.argv[1:]) or "site:xueqiu.com Acme Cloud"
    rows = sogou_search(q, max_results=10, channel="auto")
    print(f"[{len(rows)}] {q}")
    for x in rows[:5]:
        print(f"  · {x['title'][:60]}")
        print(f"    {x['href'][:100]}")
        print(f"    {x['body'][:80]}")
