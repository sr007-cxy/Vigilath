"""引用源追溯分析器 — Which sources are being cited by AI engines?

Aggregates all citations across engines and queries to build a complete
source map: who cites what, how often, and in what context.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from api_engine.base import EngineResult
from .source_classify import classify_source


def analyze_source_trace(
    results: List[EngineResult],
    target_domain: str = "",
) -> Dict[str, Any]:
    """Build a source trace from engine results.

    Returns a dict with:
    - sources: list of aggregated source entries
    - self_citations: citations to the target domain
    - missing_queries: queries where the target was not cited
    - total_sources: unique source count
    """
    # Aggregate by domain → {domain: {urls, engines, queries, count, type, articles}}
    source_map: Dict[str, dict] = defaultdict(lambda: {
        "platform": "",
        "urls": set(),
        "engines": set(),
        "queries": set(),
        "count": 0,
        "source_type": "",
        "articles": [],
    })

    self_citations = []
    queries_with_self = set()
    all_queries = set()

    for r in results:
        if r.error:
            continue
        all_queries.add(r.query)

        for c in r.citations:
            domain = c.domain
            entry = source_map[domain]
            entry["platform"] = domain
            entry["urls"].add(c.url)
            entry["engines"].add(r.engine)
            entry["queries"].add(r.query)
            entry["count"] += 1

            if not entry["source_type"]:
                entry["source_type"] = classify_source(c.url, target_domain)

            # Track individual articles (deduplicated by URL)
            if not any(a["url"] == c.url for a in entry["articles"]):
                entry["articles"].append({
                    "url": c.url,
                    "title": c.title or c.url,
                    "citations": 1,
                })
            else:
                for a in entry["articles"]:
                    if a["url"] == c.url:
                        a["citations"] += 1

            # Self-citation tracking
            if target_domain and target_domain in domain:
                queries_with_self.add(r.query)
                self_citations.append({
                    "url": c.url,
                    "engine": r.engine,
                    "query": r.query,
                    "title": c.title,
                })

    # Convert sets to lists for JSON serialization
    sources = []
    for domain, entry in sorted(source_map.items(), key=lambda x: -x[1]["count"]):
        sources.append({
            "platform": entry["platform"],
            "article_count": len(entry["articles"]),
            "total_citations": entry["count"],
            "engines": sorted(entry["engines"]),
            "queries": sorted(entry["queries"]),
            "source_type": entry["source_type"],
            "articles": entry["articles"],
        })

    missing_queries = sorted(all_queries - queries_with_self) if target_domain else []

    return {
        "sources": sources,
        "self_citations": self_citations,
        "missing_queries": missing_queries,
        "total_sources": len(sources),
        "total_citations": sum(s["total_citations"] for s in sources),
    }
