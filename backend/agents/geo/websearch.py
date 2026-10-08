"""Public web search — the evidence source for the degradation chain.

**First, something easy to get wrong**: the repo's original "websearch fallback" (Yuanbao WebSearch)
isn't independent — it goes through telemetry-service's `/run-now`, so **it goes down with the batch run center**.
Using it as the "fallback when batch runs are unavailable" is a circular dependency; during an outage it returns nothing.

Division of labor among the three sources:

| Source | Independence | Used for |
|---|---|---|
| Tencent SearchPro (this module) | ✅ agent process connects to the public internet directly; unaffected by whoever fails | **first fallback of the degradation chain** |
| Yuanbao WebSearch | ❌ depends on telemetry | the "telemetry alive but engine queue jammed" tier |
| Engine-native web_search | separate API keys | only for the "how would the AI engine answer" dimension |

The implementation copies the call shape of `sales_agent/discovery._search_tencent_searchpro`, but
**doesn't import that module** — it's coupled to `write_sales_agent_log` (writes to the sales log),
and having a GEO-side search add a row to the sales ledger is wrong. Twenty lines of independent implementation are cleaner than reuse.

★ **No key / unreachable -> `available: False`, never an empty list.**
  An empty list would be treated upstream as "searched, found nothing", and the model would start concluding from "no negative news" —
  the first step of fabricating data. If it can't search, say so.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

SEARCHPRO_URL = "https://api.wsa.cloud.tencent.com/SearchPro"

_probe_cache: dict[str, tuple[float, bool]] = {}
_PROBE_TTL_S = 30


def _timeout() -> float:
    try:
        return max(3.0, float(os.environ.get("AGENT_WEBSEARCH_TIMEOUT_S", 15)))
    except (TypeError, ValueError):
        return 15.0


def _default_cnt() -> int:
    try:
        return max(1, min(30, int(os.environ.get("AGENT_WEBSEARCH_CNT", 10))))
    except (TypeError, ValueError):
        return 10


def max_queries() -> int:
    try:
        return max(1, min(10, int(os.environ.get("AGENT_WEBSEARCH_MAX_QUERIES", 5))))
    except (TypeError, ValueError):
        return 5


def configured() -> bool:
    return bool(os.environ.get("TENCENT_SEARCH_API_KEY", "").strip())


def probe() -> bool:
    """Availability probe, cached for 30 seconds. No key means False directly — don't send a request that's bound to fail."""
    if not configured():
        return False
    hit = _probe_cache.get("tencent")
    if hit and time.time() - hit[0] < _PROBE_TTL_S:
        return hit[1]
    ok = True                     # With a key it's considered available; real failures are reported truthfully by search()
    _probe_cache["tencent"] = (time.time(), ok)
    return ok


def _row(page: dict) -> Optional[dict[str, Any]]:
    url = str(page.get("url") or page.get("Url") or "").strip()
    title = str(page.get("title") or page.get("Title") or "").strip()
    if not url or not title:
        return None                # Entries without a link can't be drilled into; drop them
    snippet = str(page.get("passage") or page.get("snippet")
                  or page.get("Passage") or "").strip()
    date = str(page.get("date") or page.get("Date") or "").strip()
    host = ""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:  # noqa: BLE001
        pass
    return {
        "title": title[:200],
        "url": url,
        "snippet": snippet[:400],
        "source_domain": host,
        "published_at": date,
        "provider": "tencent",
    }


def search(query: str, *, cnt: Optional[int] = None) -> dict[str, Any]:
    """One query. Returns {available, evidence[], reason}.

    **Never translate a failure into an empty result** — when available=False the upper layer must say truthfully "can't search",
    rather than conclude from "nothing found".
    """
    key = os.environ.get("TENCENT_SEARCH_API_KEY", "").strip()
    if not key:
        return {"available": False, "evidence": [],
                "reason": "公网检索未配置(TENCENT_SEARCH_API_KEY)"}
    try:
        resp = requests.post(
            SEARCHPRO_URL,
            json={"Query": query, "Cnt": cnt or _default_cnt()},
            headers={"Authorization": f"Bearer {key}"},
            timeout=_timeout(),
        )
        resp.raise_for_status()
        data = resp.json().get("Response", {})
        if "Error" in data:
            return {"available": False, "evidence": [],
                    "reason": f"检索服务报错:{data['Error'].get('Message', 'unknown')}"}
        rows = []
        for raw in data.get("Pages", []) or []:
            page = raw
            if isinstance(raw, str):
                import json as _json
                try:
                    page = _json.loads(raw)
                except (TypeError, ValueError):
                    continue
            if isinstance(page, dict):
                r = _row(page)
                if r is not None:
                    rows.append(r)
        return {"available": True, "evidence": rows, "reason": ""}
    except Exception as e:  # noqa: BLE001
        log.warning("[websearch] search failed q=%r: %s", query[:60], e)
        return {"available": False, "evidence": [],
                "reason": f"公网检索暂时不可用({type(e).__name__})"}


def search_many(queries: list[str], *, cnt: Optional[int] = None) -> dict[str, Any]:
    """Merge and dedupe multiple queries (by url). Available if at least one succeeds."""
    qs = [q.strip() for q in (queries or []) if q and q.strip()][:max_queries()]
    if not qs:
        return {"available": False, "evidence": [], "queried": [], "reason": "没有可检索的问题"}

    seen: set[str] = set()
    out: list[dict] = []
    reasons: list[str] = []
    any_ok = False
    for q in qs:
        r = search(q, cnt=cnt)
        if r["available"]:
            any_ok = True
            for row in r["evidence"]:
                if row["url"] in seen:
                    continue
                seen.add(row["url"])
                out.append({**row, "query": q})
        elif r["reason"]:
            reasons.append(r["reason"])
    return {
        "available": any_ok,
        "evidence": out,
        "queried": qs,
        "reason": "" if any_ok else (reasons[0] if reasons else "公网检索没有返回结果"),
    }
