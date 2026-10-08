"""Weibo direct-collection adapter - fetches real-time UGC via s.weibo.com (PC login session).

Search engines (Baidu/Bing/360) barely index personal Weibo posts, and Weibo in-site search requires login.
This adapter uses a PC Weibo account cookie (environment variable `WEIBO_COOKIE`, must include SUB+SUBP)
to hit s.weibo.com directly with **dual recall: comprehensive (popularity) + real-time (time)**, filling the UGC blind spot
(negative content such as wage disputes / rights protection / short selling / defaults). Post time is parsed structurally from the card - naturally solving
the "times do not match" problem.

Cookies expire (SUB's ALF timestamp lasts about a few weeks); when invalid this adapter returns [] (prints to stderr)
without dragging down the whole plan; to replace the cookie just update `WEIBO_COOKIE`.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta

import requests

from .cookie_pool import CookiePool

_BASE = "https://s.weibo.com/weibo"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TIMEOUT = 25

# s.weibo.com results page structure (measured): each result is in a card-wrap -
#   body: <p class="txt" node-type="feed_list_content" nick-name="<nickname>"> … </p>
#   time+link: <div class="from"> <a href="//weibo.com/UID/MID?…">05月15日 08:51</a> 来自 ("via") … </div>
_CARD_SPLIT = re.compile(r'<div class="card-wrap"')
_CONTENT_RE = re.compile(r'<p class="txt"[^>]*nick-name="([^"]*)"[^>]*>(.*?)</p>', re.S)
_FROM_RE = re.compile(
    r'<div class="from"\s*>\s*<a\s+href="(//weibo\.com/[^"]+)"[^>]*>\s*(.*?)\s*</a>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')


def _clean(html: str) -> str:
    return re.sub(r'\s+', ' ', _TAG_RE.sub('', html)).replace('​', '').strip()


def _parse_time(s: str, today: date) -> str | None:
    """Weibo relative/absolute time -> ISO. 'X分钟前' ("X min ago") / '今天 HH:MM' ("today HH:MM") -> today; 'M月D日' -> this year; 'YYYY-MM-DD' unchanged."""
    s = s.strip()
    if not s:
        return None
    if re.search(r'\d+\s*(秒|分钟|小时)前', s) or s.startswith('今天'):
        d = today
    elif s.startswith('昨天'):
        d = today - timedelta(days=1)
    else:
        m = re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日', s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.search(r'(\d{1,2})月(\d{1,2})日', s)        # 05月15日 (May 15) -> this year
        if m:
            return f"{today.year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.search(r'(20\d{2})-(\d{1,2})-(\d{1,2})', s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return None
    hm = re.search(r'(\d{1,2}):(\d{2})', s)
    if hm:
        return f"{d.isoformat()}T{int(hm.group(1)):02d}:{hm.group(2)}"
    return d.isoformat()


def _fetch(query: str, realtime: bool, cookie: str) -> tuple[str, dict]:
    """Returns (html, set_cookies); set_cookies is used for rolling-capture renewal."""
    params: dict[str, str] = {"q": query}
    if realtime:                       # real-time (by time) - catches low-reach UGC (wage disputes/rights protection)
        params["xsort"] = "time"
        params["suball"] = "1"
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA, "Cookie": cookie, "Referer": "https://s.weibo.com/"})
    r = session.get(_BASE, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.text, {c.name: c.value for c in session.cookies}


def _parse(html: str, today: date) -> list[dict]:
    out: list[dict] = []
    for card in _CARD_SPLIT.split(html)[1:]:
        if "wb-ad-tile" in card:          # skip ad cards (no real post time)
            continue
        cm = _CONTENT_RE.search(card)
        if not cm:
            continue
        text = _clean(cm.group(2))
        if not text:
            continue
        fm = _FROM_RE.search(card)
        url, pub = "", None
        if fm:
            url = "https:" + fm.group(1).split("?")[0]
            pub = _parse_time(_clean(fm.group(2)), today)
        out.append({
            "title": text[:48],
            "href": url,
            "body": text,
            "publish_time": pub,
            "author": cm.group(1).strip(),
        })
    return out


def weibo_search(query: str, max_results: int = 20,
                 timelimit: str | None = None) -> list[dict]:
    """Dual recall (comprehensive + real-time), deduped by post URL; returns [{title,href,body,publish_time,author}].

    Any transport error / invalid cookie -> returns the partial results collected so far (prints to stderr), never raises.

    Real-time (xsort=time) first, comprehensive second, **each path with its own max_results quota** - otherwise old viral posts
    from comprehensive would fill the quota first and the newest real-time UGC would never get in (sentiment monitoring cares most about the newest).

    Cookies go through CookiePool: rotate multiple cookies per request; if one is redirected to the login page, mark it dead and switch to the next automatically.
    """
    today = date.today()
    pool = CookiePool("weibo")
    if pool.count() == 0:
        print("  [weibo] WEIBO_COOKIE 未设置 —— 需 PC 微博账号 cookie(含 SUB+SUBP)", file=sys.stderr)
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for realtime in (True, False):       # real-time first (newest), then comprehensive (popularity)
        html = None
        for idx, cookie in pool.try_cookies():   # per-request rotation; dead ones are switched automatically
            try:
                page, setck = _fetch(query, realtime, cookie)
            except Exception as e:
                print(f"  [weibo] fetch failed (realtime={realtime}): {e}", file=sys.stderr)
                continue                          # transport error != dead cookie, try the next one
            # Two forms of a dead cookie: 1) redirect to passport.weibo.com; 2) a tiny 「新浪通行证」 ("Sina Passport")
            # interstitial page (~600B, GBK, no feed marker). The old logic missed the latter -> silently treated it as "no results".
            has_feed = ("card-wrap" in page) or ("feed_list_content" in page)
            if "passport.weibo.com" in page or (not has_feed and len(page) < 3000):
                pool.mark_dead(idx, "被跳登录页/新浪通行证(SUB 过期)")
                continue
            pool.mark_ok(idx)   # large page but 0 results = cookie is fine, just no hits; do not mark dead
            pool.capture_refresh(idx, setck)    # rolling capture of the new SUB/ALF issued by Weibo
            html = page
            break
        if html is None:
            print(f"  [weibo] 本路(realtime={realtime})无可用 cookie", file=sys.stderr)
            continue
        cnt = 0
        for it in _parse(html, today):
            key = it["href"] or it["body"][:40]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(it)
            cnt += 1
            if cnt >= max_results:        # independent quota per path, so real-time results also get in
                break
    return out


if __name__ == "__main__":   # self-test: python -m search.weibo "Acme Cloud 欠薪" ("unpaid wages")
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "Acme Cloud"
    rows = weibo_search(q, max_results=12)
    print(f"query={q!r} → {len(rows)} 条")
    for r in rows:
        print(f"  {r['publish_time']!s:<17} @{r['author']}: {r['body'][:48]}  {r['href']}")
