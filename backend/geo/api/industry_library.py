"""Industry library API — public (anonymous) industry AI visibility data pages.

Industry = the category seed in whitelisted topic prompts (e.g. topic 9 example cleaning → 「商用清洁机器人」 ("commercial cleaning robots")):
  - All metrics use the category-term definition (brand terms forcibly excluded, same as the wide-table rollup / competitor comparison)
  - The brand list comes from response extraction (competitors_json), no manually maintained competitor table
  - Presented from an "industry report" perspective (no selected brand): industry voice / TOP1 brand / brand concentration /
    leading brand comparison (vs industry average) / citation source analysis / keyword heat / per-engine brand ranking
  - Leaderboard / heatmap / engine list reuse the report center's build_competitive_payload, so readings match the site

Public boundary:
  - Whitelist INDUSTRY_LIBRARY_TOPIC_IDS (comma-separated topic ids, default "9") —
    data comes from customer monitoring topics, so each industry must be approved before entering the public library
  - No auth, must withstand anonymous traffic: in-process TTL cache (INDUSTRY_LIBRARY_CACHE_TTL, default 1h)
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from geo.api.ai_telemetry import get_db
from geo.api.ai_telemetry_reports import (
    CompetitiveOut, SourceTypeShare, _comp_names, _load_rows,
    build_competitive_payload,
)
from geo.models.ai_telemetry import AiTelemetryTopicORM, metric_brand_terms
from geo_checker.analyzers.source_factor import classify_factor

router = APIRouter(prefix="/industry")


# ─────────────────── schemas ──────────────────────────────


class IndustrySummary(BaseModel):
    key: str                              # URL segment, i.e. the industry name (category seed)
    name: str
    brand_count: int                      # Brands on the leaderboard (monitored brand + extracted competitors)
    keyword_count: int                    # Number of category prompts
    engines: list[str]
    updated: str                          # Data as of (Beijing date)
    top_brands: list[str]                 # Top 3 on the leaderboard


class IndustryListOut(BaseModel):
    industries: list[IndustrySummary]
    # Sample scope. The industry library is **built one by one** (whitelist INDUSTRY_LIBRARY_TOPIC_IDS);
    # without stating it, callers assume "industry ranking = the whole industry" and distrust data showing only 6 brands.
    # Better to state the coverage up front than let users discover it.
    industry_count: int = 0
    sample_note: str = ""


# The leaderboard is truncated in ai_telemetry_reports.build_competitive_payload (`ranking[:8]`).
# This constant only feeds copy and does not participate in computation — keep this line in sync when changing that truncation.
RANKING_CAP = 8
_SAMPLE_NOTE = (
    "行业库按行业逐个建库,只覆盖已建库的行业;每个行业的排行榜最多 {cap} 个品牌"
    "(监测品牌 + 从 AI 应答里抽取到的竞品)。这是**行业**层面的真实跑批数据,"
    "不是任何单个品牌的表现。用户的行业不在清单里就如实说没建库,"
    "绝不拿别的行业的数字替代。"
).format(cap=RANKING_CAP)


class IndustryVisMetric(BaseModel):
    current: float
    delta: Optional[float] = None         # vs previous period
    series: list[float]
    series_keys: list[str]


class IndustryTop1Out(BaseModel):
    name: str
    coverage: IndustryVisMetric           # Overall exposure trend of the TOP1 brand


class IndustryBrandCard(BaseModel):
    """Leading brand comparison card: differences from the industry average (>0 = above average)."""
    name: str
    rank: int
    coverage: float
    coverage_vs_avg: float
    mention: float
    mention_vs_avg: float


class IndustryHeatWord(BaseModel):
    query: str
    heat: int
    source: str = "monitor"              # monitor = prompts (heat from audit data) / suggest = Baidu dropdown related terms


class IndustrySourceRow(BaseModel):
    domain: str
    count: int
    engines: list[str]
    sample_title: str = ""


class IndustrySourcesOut(BaseModel):
    total_citations: int
    type_share: list[SourceTypeShare]     # Source type share (classify_factor definition)
    top_sources: list[IndustrySourceRow]  # Top cited domains


class IndustryDataOut(BaseModel):
    key: str
    name: str
    keyword_count: int
    brand_count: int
    updated: str
    # Three industry overview cards
    heat: IndustryVisMetric               # Industry voice: total brand mentions in AI responses
    top1: IndustryTop1Out                 # Current TOP1 brand and its exposure trend
    cr3: IndustryVisMetric                # Brand concentration: share of industry voice held by the top three brands (%)
    # Leading brand comparison + insights
    industry_avg_coverage: float
    industry_avg_mention: float
    brands: list[IndustryBrandCard]       # Top 3 brand cards
    top1_gap: float                       # Exposure lead of TOP1 over TOP2 (percentage points)
    opportunity_keywords: int             # Prompts where the AI has not formed a stable brand recommendation
    # Citation source analysis
    sources: IndustrySourcesOut
    # Word cloud terms: prompts (real audit heat) + Baidu dropdown related terms (free expansion, weighted by suggestion order)
    heat_words: list[IndustryHeatWord]
    # Leaderboard / heatmap / engine list (same aggregation as the report center)
    competitive: CompetitiveOut
    # Sample scope, the same sentence as /list — anonymous callers (skill / agent) use it for disclosure
    sample_note: str = ""


# ─────────────────── Industry resolution ───────────────────────────────


def _whitelist_ids() -> list[int]:
    raw = os.getenv("INDUSTRY_LIBRARY_TOPIC_IDS", "9")
    return [int(p) for p in (s.strip() for s in raw.split(",")) if p.isdigit()]


def _category_queries(topic) -> tuple[Counter, int]:
    """(category seed counts, total category terms): excludes prompts whose seed / text contains brand terms."""
    terms = metric_brand_terms(topic)
    seeds: Counter = Counter()
    total = 0
    try:
        for q in json.loads(topic.queries_json or "[]"):
            text = (q.get("text") if isinstance(q, dict) else str(q or "")).strip()
            seed = ((q.get("seed") or "") if isinstance(q, dict) else "").strip()
            status = (q.get("status") or "approved") if isinstance(q, dict) else "approved"
            if not text or status != "approved":
                continue
            if isinstance(q, dict) and q.get("deleted_at"):     # Soft-deleted terms are not counted as category terms
                continue
            if any(t in text for t in terms) or any(t in seed for t in terms):
                continue
            seeds[seed or text] += 1
            total += 1
    except Exception:  # noqa: BLE001
        pass
    return seeds, total


def _industry_topics(db: Session) -> dict[str, list[AiTelemetryTopicORM]]:
    """Industry name → topic list. The industry name is the most common seed among the topic's category terms."""
    out: dict[str, list[AiTelemetryTopicORM]] = {}
    ids = _whitelist_ids()
    if not ids:
        return out
    topics = (
        db.query(AiTelemetryTopicORM)
          .filter(AiTelemetryTopicORM.id.in_(ids))
          .all()
    )
    for t in topics:
        seeds, _total = _category_queries(t)
        if not seeds:
            continue
        name = seeds.most_common(1)[0][0]
        out.setdefault(name, []).append(t)
    return out


# ─────────────────── Industry-definition aggregation ───────────────────────────
# The page has no "selected brand", so the header and insights cannot take one brand's perspective:
# voice = total brand mentions (the monitored brand counted by hit, other brands by extracted appearances);
# brand exposure rate = appearing responses ÷ valid responses (monitored brand uses hit, the same yardstick as the leaderboard).


def _category_rows(db: Session, topic) -> list:
    rows = [r for r in _load_rows(db, topic.id) if not r.error]
    terms = metric_brand_terms(topic)
    cat = [r for r in rows if not any(t in (r.query or "") for t in terms)]
    return cat or rows


def _bucket_stats(rows: list, brand: str, top_names: list[str]) -> dict:
    """Industry metrics of a group of responses: voice / appearances per brand / TOP1 exposure rate / CR3."""
    n = len(rows)
    hits = 0
    apps: Counter = Counter()
    for r in rows:
        if r.hit:
            hits += 1
        for name in _comp_names(r):
            apps[name] += 1

    def appear(name: str) -> int:
        return hits if name == brand else apps.get(name, 0)

    total = hits + sum(apps.values())
    top1_cov = round(appear(top_names[0]) / n * 100, 1) if n and top_names else 0.0
    cr3_apps = sum(appear(nm) for nm in top_names[:3])
    cr3 = round(cr3_apps / total * 100, 1) if total else 0.0
    return {"heat": total, "top1_cov": top1_cov, "cr3": cr3, "n": n,
            "hits": hits, "apps": apps}


def _series_of(buckets: list[dict], keys: list[str], field: str) -> IndustryVisMetric:
    series = [b[field] for b in buckets]
    cur = series[-1] if series else 0.0
    prev = series[-2] if len(series) >= 2 else None
    return IndustryVisMetric(
        current=cur,
        delta=round(cur - prev, 1) if prev is not None else None,
        series=[round(float(v), 1) for v in series], series_keys=keys,
    )


def _industry_sources(rows: list, limit: int = 12) -> IndustrySourcesOut:
    """Citation source analysis: domains / source type share cited by the AI in this industry's category responses."""
    agg: dict[str, dict] = {}
    total = 0
    for r in rows:
        try:
            cits = json.loads(r.citations_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        for c in cits:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            # Domains always come from the real hostname in the URL — the extraction layer's domain field sometimes holds
            # a Chinese site name (「搜狐」 (Sohu)), which breaks frontend favicons / platform normalization
            try:
                from urllib.parse import urlparse
                domain = (urlparse(c["url"]).hostname or "").lower()
            except Exception:  # noqa: BLE001
                domain = ""
            if not domain:
                domain = (c.get("domain") or "").strip().lower()
            if not domain:
                continue
            total += 1
            a = agg.setdefault(domain, {
                "count": 0, "engines": set(), "title": "", "url": c["url"],
            })
            a["count"] += 1
            a["engines"].add(r.engine)
            if not a["title"] and c.get("title"):
                a["title"] = str(c["title"])[:80]

    type_counts: dict[str, int] = defaultdict(int)
    top: list[IndustrySourceRow] = []
    for domain, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        try:
            stype = classify_factor(a["url"], title=a["title"], domain=domain, owned=False)
        except Exception:  # noqa: BLE001
            stype = "other"
        type_counts[stype] += a["count"]
        if len(top) < limit:
            top.append(IndustrySourceRow(
                domain=domain, count=a["count"],
                engines=sorted(a["engines"]), sample_title=a["title"],
            ))
    type_share = sorted([
        SourceTypeShare(source_type=t, count=n,
                        pct=round(n / total * 100, 1) if total else 0.0)
        for t, n in type_counts.items()
    ], key=lambda x: -x.count)
    return IndustrySourcesOut(total_citations=total, type_share=type_share, top_sources=top)


# ─────────────────── Baidu dropdown related terms (free word-cloud expansion) ───────
# Public suggestion.baidu.com endpoint, no login; only term text (dropdown terms have no heat value),
# weights decay by suggestion order, placed below prompt heat. Failures silently return [] and the word cloud falls back to prompts only.


def _baidu_suggest(word: str, timeout: float = 3.0) -> list[str]:
    try:
        resp = requests.get(
            "https://suggestion.baidu.com/su",
            params={"wd": word, "ie": "utf-8", "action": "opensearch"},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; VigilathBot/1.0)"},
        )
        data = resp.json()
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            return [str(w).strip() for w in data[1] if str(w).strip()]
    except Exception:  # noqa: BLE001
        pass
    return []


def _cloud_heat_words(topic, name: str, seeds: Counter, competitive: CompetitiveOut,
                      max_suggest: int = 12) -> list[IndustryHeatWord]:
    """Word cloud terms = top 10 prompts (real audit heat) + Baidu dropdown related terms (order-based weights)."""
    terms = metric_brand_terms(topic)
    out = [IndustryHeatWord(query=w.query, heat=w.heat, source="monitor")
           for w in competitive.heat_words]
    seen = {w.query for w in out}
    suggests: list[str] = []
    for seed in [name] + [s for s, _ in seeds.most_common(3) if s != name]:
        for w in _baidu_suggest(seed):
            if w in seen or w == seed or len(w) < 4:
                continue
            if any(t in w for t in terms):       # Brand-term definition: related terms also drop brands
                continue
            seen.add(w)
            suggests.append(w)
        if len(suggests) >= max_suggest:
            break
    base = out[-1].heat if out else 1000         # Placed below the minimum prompt heat
    for i, w in enumerate(suggests[:max_suggest]):
        heat = max(120, int(base * 0.9 / (1 + 0.18 * i)) // 10 * 10)
        out.append(IndustryHeatWord(query=w, heat=heat, source="suggest"))
    return out


# ─────────────────── Assembly + cache ────────────────────────────


CACHE_TTL = int(os.getenv("INDUSTRY_LIBRARY_CACHE_TTL", "3600"))
_cache: dict[str, tuple[float, IndustryDataOut]] = {}


def _industry_data(db: Session, name: str, topics: list[AiTelemetryTopicORM]) -> IndustryDataOut:
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    # With several topics in one industry, use the one with the most responses as the data source (industry library MVP: single-topic definition, merged aggregation later)
    topic = topics[0]
    if len(topics) > 1:
        from geo.models.ai_telemetry import AiTelemetryResponseORM
        counts = {
            t.id: db.query(AiTelemetryResponseORM.id)
                    .filter(AiTelemetryResponseORM.topic_id == t.id).count()
            for t in topics
        }
        topic = max(topics, key=lambda t: counts.get(t.id, 0))
    # ★ This does not go through the report center's _topic_scoped, so the definition context must be set here: if omitted, the public industry page's
    #   coverage/leaderboard would not share a source with the on-site competitor analysis page (fixed 2026-08-17)
    from geo.api.ai_telemetry_reports import set_fixed_denominator
    from geo.services.report_metrics_source import _set_mention_scope

    _set_mention_scope(topic)
    set_fixed_denominator(topic)
    competitive = build_competitive_payload(db, topic, "overview", exclude_brand=True)
    _seeds, keyword_count = _category_queries(topic)

    rows = _category_rows(db, topic)
    top_names = [r.name for r in competitive.ranking]
    brand = competitive.brand

    # Trend: available dates bucketed every 7 days (same rhythm as the competitive overview mini trend)
    by_day: dict[str, list] = defaultdict(list)
    from geo.api.ai_telemetry_reports import _bj_date
    for r in rows:
        by_day[_bj_date(r.created_at).isoformat()].append(r)
    day_keys = sorted(by_day)
    groups = [day_keys[i:i + 7] for i in range(0, len(day_keys), 7)]
    buckets = [_bucket_stats([r for d in g for r in by_day[d]], brand, top_names) for g in groups]
    bucket_keys = [g[0][5:] for g in groups]
    if groups:
        bucket_keys[-1] = groups[-1][-1][5:]

    # Leading brand comparison: Top 3 vs industry average (same yardstick as the leaderboard: coverage=composite)
    cur_bucket = buckets[-1] if buckets else None
    all_apps_total = (cur_bucket["hits"] + sum(cur_bucket["apps"].values())) if cur_bucket else 0

    def mention_share(nm: str) -> float:
        if not cur_bucket or not all_apps_total:
            return 0.0
        a = cur_bucket["hits"] if nm == brand else cur_bucket["apps"].get(nm, 0)
        return round(a / all_apps_total * 100, 1)

    covs = [r.composite for r in competitive.ranking]
    avg_cov = round(sum(covs) / len(covs), 1) if covs else 0.0
    mentions = {r.name: mention_share(r.name) for r in competitive.ranking}
    avg_mention = round(sum(mentions.values()) / len(mentions), 1) if mentions else 0.0
    brands = [IndustryBrandCard(
        name=r.name, rank=i + 1,
        coverage=r.composite, coverage_vs_avg=round(r.composite - avg_cov, 1),
        mention=mentions.get(r.name, 0.0),
        mention_vs_avg=round(mentions.get(r.name, 0.0) - avg_mention, 1),
    ) for i, r in enumerate(competitive.ranking[:3])]
    top1_gap = round(covs[0] - covs[1], 1) if len(covs) >= 2 else 0.0

    # Opportunity keywords: terms whose brand mention density in the current bucket is < 1 (AI answers have not formed a stable brand recommendation)
    opportunity = 0
    if groups:
        q_valid: Counter = Counter()
        q_apps: Counter = Counter()
        for d in groups[-1]:
            for r in by_day[d]:
                q_valid[r.query] += 1
                q_apps[r.query] += (1 if r.hit else 0) + len(_comp_names(r))
        opportunity = sum(1 for q, n in q_valid.items() if n and q_apps[q] / n < 1.0)

    data = IndustryDataOut(
        key=name, name=name,
        keyword_count=keyword_count,
        brand_count=len(competitive.ranking),
        updated=competitive.key,
        heat=_series_of(buckets, bucket_keys, "heat"),
        top1=IndustryTop1Out(
            name=top_names[0] if top_names else "",
            coverage=_series_of(buckets, bucket_keys, "top1_cov"),
        ),
        cr3=_series_of(buckets, bucket_keys, "cr3"),
        industry_avg_coverage=avg_cov,
        industry_avg_mention=avg_mention,
        brands=brands,
        top1_gap=top1_gap,
        opportunity_keywords=opportunity,
        sources=_industry_sources(rows),
        heat_words=_cloud_heat_words(topic, name, _seeds, competitive),
        competitive=competitive,
        sample_note=_SAMPLE_NOTE,
    )
    _cache[name] = (time.time(), data)
    return data


# ─────────────────── endpoints (anonymous) ──────────────────────


@router.get("/list", response_model=IndustryListOut)
def list_industries(db: Session = Depends(get_db)):
    items: list[IndustrySummary] = []
    for name, topics in sorted(_industry_topics(db).items()):
        try:
            d = _industry_data(db, name, topics)
        except HTTPException:               # Whitelisted topic has no batch-run data yet: not listed
            continue
        items.append(IndustrySummary(
            key=d.key, name=d.name,
            brand_count=d.brand_count, keyword_count=d.keyword_count,
            engines=d.competitive.engines, updated=d.updated,
            top_brands=[r.name for r in d.competitive.ranking[:3]],
        ))
    return IndustryListOut(industries=items, industry_count=len(items),
                           sample_note=_SAMPLE_NOTE)


@router.get("/{key}/data", response_model=IndustryDataOut)
def industry_data(key: str, db: Session = Depends(get_db)):
    registry = _industry_topics(db)
    if key not in registry:
        # ★ The 404 must include the list of built industries. **Deliberately no fuzzy matching** — 「工业清洁机器人」 ("industrial cleaning robots")
        #   landing on 「商用清洁机器人」 ("commercial cleaning robots") data is exactly the bug being fixed: the caller gets a perfectly
        #   normal-looking industry dataset with no signal that it answers a different industry than the user asked about.
        #   Return the list so the caller can say "this industry is not built yet; built ones are X".
        raise HTTPException(status_code=404, detail={
            "code": "INDUSTRY_NOT_IN_LIBRARY",
            "message": f"「{key}」这个行业还没有建库(行业库按行业逐个建库,不是全行业覆盖)。",
            "available": sorted(registry.keys()),
            "sample_note": _SAMPLE_NOTE,
            "note": "如实告诉用户这个行业没建库,并给出已建库清单;"
                    "**绝不**拿名字相近的别的行业的数字替代。",
        })
    return _industry_data(db, key, registry[key])
