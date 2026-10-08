"""Xiaohongshu / Douyin direct-collection adapter - keyword search via MediaCrawler (isolated venv + subprocess).

In-site search on Xiaohongshu / Douyin requires a login session + signatures; neither plain requests nor search engines get in. MediaCrawler
(NanmiCoder, NON-COMMERCIAL LEARNING LICENSE, **internal use only**) uses Playwright to keep the login session and
runs JS in the page to obtain signatures, no reverse engineering needed. Installed at `/opt/geo/mediacrawler`, separate venv
`/opt/geo/mediacrawler-venv` (playwright chromium).

This adapter calls its **keyword search** via subprocess (--type search --lt cookie --headless true
--save_data_option json), reads data/<plat>/json/*contents*.json, normalizes to
{title,href,body,publish_time,author}, and feeds it into the pipeline's relevance gate/24h window/dedup/persistence.

Cookies come from env (XHS_COOKIE / DOUYIN_COOKIE, copied from a browser login session); skipped if unset (graceful degradation).
The browser is heavy, so only single entity-term queries are used. SORT_TYPE=time_descending (newest first, matching the 24h window) is set in
MediaCrawler's config/base_config.py (no CLI option for it).

Environment variables:
  MEDIACRAWLER_DIR    default /opt/geo/mediacrawler
  MEDIACRAWLER_PYTHON default /opt/geo/mediacrawler-venv/bin/python
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))
_MC_DIR = os.environ.get("MEDIACRAWLER_DIR", "/opt/geo/mediacrawler")
_MC_PY = os.environ.get("MEDIACRAWLER_PYTHON", "/opt/geo/mediacrawler-venv/bin/python")
_TIMEOUT = 240

# Our source name -> (MediaCrawler platform, cookie env)
_PLAT = {
    "xhs": ("xhs", "XHS_COOKIE"),
    "douyin": ("dy", "DOUYIN_COOKIE"),
}


def _ts_iso(ts, unit: str) -> str | None:
    """Timestamp -> ISO (CST, minute precision). unit='ms' (Xiaohongshu) / 's' (Douyin)."""
    if not ts:
        return None
    try:
        sec = int(ts) / 1000 if unit == "ms" else int(ts)
        if sec <= 0:
            return None
        return datetime.fromtimestamp(sec, _CST).isoformat(timespec="minutes")
    except Exception:
        return None


def _normalize(plat: str, items: list, max_results: int) -> list[dict]:
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if plat == "xhs":
            title = (it.get("title") or it.get("desc") or "")[:80]
            body = it.get("desc") or title
            href = it.get("note_url") or ""
            pub = _ts_iso(it.get("time"), "ms")
        else:  # dy
            title = (it.get("title") or it.get("desc") or "")[:80]
            body = it.get("desc") or title
            href = it.get("aweme_url") or ""
            pub = _ts_iso(it.get("create_time"), "s")
        if not (href and (title or body)):
            continue
        out.append({"title": title, "href": href, "body": body,
                    "publish_time": pub, "author": it.get("nickname")})
        if len(out) >= max_results:
            break
    return out


def _mc_search(source: str, query: str, max_results: int = 15,
               timelimit: str | None = None) -> list[dict]:
    plat, env = _PLAT[source]
    cookie = os.environ.get(env, "").strip()
    if not cookie:
        print(f"  [{source}] {env} 未设置,跳过", file=sys.stderr)
        return []
    if not os.path.exists(_MC_PY):
        print(f"  [{source}] MediaCrawler venv 不存在: {_MC_PY}", file=sys.stderr)
        return []
    json_dir = os.path.join(_MC_DIR, "data", plat, "json")
    shutil.rmtree(json_dir, ignore_errors=True)        # clear old results, read only this run's
    cmd = [_MC_PY, "main.py", "--platform", plat, "--lt", "cookie",
           "--type", "search", "--keywords", query, "--cookies", cookie,
           "--save_data_option", "json", "--get_comment", "false",
           "--get_sub_comment", "false", "--headless", "true",
           "--crawler_max_notes_count", str(max(max_results, 10))]
    try:
        p = subprocess.run(cmd, cwd=_MC_DIR, capture_output=True,
                           text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"  [{source}] 子进程超时 ({_TIMEOUT}s)", file=sys.stderr)
        return []
    if p.returncode != 0:
        print(f"  [{source}] exit {p.returncode}: {(p.stderr or '')[-300:]}", file=sys.stderr)
        # Do not return directly - output may have been partially written
    files = sorted(glob.glob(os.path.join(json_dir, "*contents*.json")))
    if not files:
        print(f"  [{source}] 无输出(cookie 失效/验证码/无结果): "
              f"{(p.stderr or '')[-200:]}", file=sys.stderr)
        return []
    items: list = []
    for fp in files:
        try:
            with open(fp) as f:
                data = json.load(f)
            items.extend(data if isinstance(data, list) else [data])
        except Exception as e:
            print(f"  [{source}] 解析 {fp} 失败: {e}", file=sys.stderr)
    return _normalize(plat, items, max_results)


def xhs_search(query: str, max_results: int = 15, timelimit: str | None = None) -> list[dict]:
    return _mc_search("xhs", query, max_results, timelimit)


def douyin_search(query: str, max_results: int = 15, timelimit: str | None = None) -> list[dict]:
    return _mc_search("douyin", query, max_results, timelimit)


if __name__ == "__main__":   # self-test: python -m search.mediacrawler xhs Acme Cloud
    src = sys.argv[1] if len(sys.argv) > 1 else "xhs"
    q = sys.argv[2] if len(sys.argv) > 2 else "Acme Cloud"
    rows = _mc_search(src, q, max_results=10)
    print(f"{src} {q!r} → {len(rows)} 条")
    for r in rows:
        print(f"  {r['publish_time']!s:<17} @{r['author']}: {r['body'][:48]}  {r['href']}")
