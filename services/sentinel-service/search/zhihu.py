"""Zhihu direct-collection adapter - fetches Q&A/article UGC via the search_v3 API (login session).

The Zhihu search API requires login (`z_c0`) + a device id (`d_c0`); search engines index Zhihu poorly,
while Zhihu has lots of **employee leaks / resignation reviews / in-depth industry posts** (high sentiment value). This adapter collects directly with a
Zhihu account cookie (environment variable `ZHIHU_COOKIE`, must include `z_c0` + `d_c0`).
Publish time comes from the item's structured `created_time` (unix) - naturally solving the time problem.

Invalid cookie (z_c0 expired) -> returns [] (prints to stderr) without dragging down the plan; to replace the cookie, update `ZHIHU_COOKIE`.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests

from .cookie_pool import CookiePool

_API = "https://www.zhihu.com/api/v4/search_v3"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TIMEOUT = 25
_CST = timezone(timedelta(hours=8))
_TAG_RE = re.compile(r'<[^>]+>')


def _clean(s: str | None) -> str:
    return re.sub(r'\s+', ' ', _TAG_RE.sub('', s or '')).strip()


def _ts_to_iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), _CST).isoformat(timespec="minutes")
    except Exception:
        return None


def _url_for(obj: dict) -> str:
    t = obj.get("type")
    oid = obj.get("id")
    if t == "answer":
        qid = ((obj.get("question") or {}).get("id"))
        if qid and oid:
            return f"https://www.zhihu.com/question/{qid}/answer/{oid}"
    if t == "article":
        return f"https://zhuanlan.zhihu.com/p/{oid}"
    if t == "question":
        return f"https://www.zhihu.com/question/{oid}"
    if t == "zvideo":
        return f"https://www.zhihu.com/zvideo/{oid}"
    return obj.get("url") or ""


def zhihu_search(query: str, max_results: int = 20,
                 timelimit: str | None = None) -> list[dict]:
    """Zhihu comprehensive search, returns [{title,href,body,publish_time,author}]. Returns [] on failure.

    Cookies go through CookiePool: rotate multiple cookies per request; if z_c0 expired (401/403), mark it dead and switch to the next.
    """
    pool = CookiePool("zhihu")
    if pool.count() == 0:
        print("  [zhihu] ZHIHU_COOKIE 未设置 —— 需含 z_c0 + d_c0 的知乎 cookie", file=sys.stderr)
        return []
    params = {"t": "general", "q": query, "offset": 0,
              "limit": min(max_results, 20), "correction": 1}
    data = None
    for idx, cookie in pool.try_cookies():
        headers = {
            "User-Agent": _UA, "Cookie": cookie,
            "Accept": "application/json, text/plain, */*",
            "x-api-version": "3.0.91",
            "Referer": ("https://www.zhihu.com/search?type=content&q="
                        + urllib.parse.quote(query)),
        }
        try:
            r = requests.get(_API, params=params, headers=headers, timeout=_TIMEOUT)
            if r.status_code in (401, 403):
                pool.mark_dead(idx, f"鉴权失败 {r.status_code}(z_c0 过期)")
                continue
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            print(f"  [zhihu] request failed: {e}", file=sys.stderr)
            continue                              # transport error != dead cookie, try the next one
        if isinstance(j, dict) and j.get("error"):
            pool.mark_dead(idx, f"API error:{str(j['error'])[:40]}")
            continue
        pool.mark_ok(idx)
        pool.capture_refresh(idx, {c.name: c.value for c in r.cookies})  # rolling capture of z_c0 refresh
        data = j
        break
    if data is None:
        print("  [zhihu] 无可用 cookie", file=sys.stderr)
        return []
    out: list[dict] = []
    for it in data.get("data", []):
        obj = it.get("object") or {}
        if not isinstance(obj, dict):
            continue
        title = _clean(obj.get("title") or (obj.get("question") or {}).get("name"))
        body = _clean(obj.get("excerpt") or obj.get("content"))
        if not (title or body):
            continue
        author = ((obj.get("author") or {}).get("name")) or None
        pub = _ts_to_iso(obj.get("created_time") or obj.get("updated_time"))
        out.append({
            "title": title or body[:48],
            "href": _url_for(obj),
            "body": body or title,
            "publish_time": pub,
            "author": author,
        })
        if len(out) >= max_results:
            break
    return out


if __name__ == "__main__":   # self-test: python - "Acme Cloud"
    q = sys.argv[1] if len(sys.argv) > 1 else "Acme Cloud"
    rows = zhihu_search(q, max_results=10)
    print(f"query={q!r} → {len(rows)} 条")
    for r in rows:
        print(f"  {r['publish_time']!s:<17} @{r['author']}: {r['title'][:30]} | {r['body'][:40]}  {r['href']}")
