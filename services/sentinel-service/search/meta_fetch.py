"""浏览器版发布时间抓取(在独立 scrapling-venv 运行)——给验证码/JS 渲染站。

ZAKER(myzaker.com)等挂了长亭(Chaitin)验证码 WAF,纯 HTTP 只拿到挑战页;
StealthyFetcher 真浏览器 + 等待(~12s)可过验证码,再从渲染后的 HTML 抽发布时间。
被 search/pipeline.py 的 fetch_meta_date_browser 通过 subprocess 调用,stdout 输出一行 JSON。

用法: /opt/geo/scrapling-venv/bin/python meta_fetch.py <url>  → {"date": "YYYY-MM-DDTHH:MM"} 或 {"date": null}
"""
import json
import logging
import re
import sys
from datetime import date as _date

logging.disable(logging.INFO)

from scrapling.fetchers import StealthyFetcher

_META_RES = [
    re.compile(r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|'
               r'og:published_time|og:release_time|publishdate|pubdate|datePublished)["\'][^>]*?'
               r'content=["\']([^"\']+)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
]


def _dft(s):
    m = re.search(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})', s or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    tm = re.search(r'(\d{1,2}):(\d{2})', s)
    return f"{y:04d}-{mo:02d}-{d:02d}" + (f"T{int(tm.group(1)):02d}:{tm.group(2)}" if tm else "")


def _visible(html):
    head = html[:8000]
    m = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s+(\d{1,2}):(\d{2})', head)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}T{int(m.group(4)):02d}:{m.group(5)}"
    m = re.search(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日\s+(\d{1,2}):(\d{2})', head)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            t = _date.today()
            y = t.year - (1 if (mo, d) > (t.month, t.day) else 0)
            return f"{y:04d}-{mo:02d}-{d:02d}T{int(m.group(3)):02d}:{m.group(4)}"
    return None


def _extract(html):
    for rx in _META_RES:
        m = rx.search(html)
        if m:
            d = _dft(m.group(1))
            if d:
                return d
    v = _visible(html)
    if v:
        return v
    # ZAKER 等:发布日在 <span class="time">05-16</span>(MM-DD,锚定 class="time" 避开
    # 相关文章的 article-time 相对时间)。无年→当年,落未来则去年。
    m = re.search(r'class="time"[^>]*>\s*(\d{1,2})-(\d{1,2})\s*<', html)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            t = _date.today()
            y = t.year - (1 if (mo, d) > (t.month, t.day) else 0)
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True,
                                     wait=12000, timeout=120000)
        html = getattr(page, "html_content", "") or ""
    except Exception as e:
        print(json.dumps({"error": repr(e)[:150]})); return
    print(json.dumps({"date": _extract(html)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
