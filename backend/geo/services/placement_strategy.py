"""Placement strategy generator -- data source for the plan's "placement strategy" page, and upstream of publish schedule planning.

Architecture follows the four steps in `docs/03-系统设计/内容与发布/GEO媒体投放策略生成器.md`:

    Step 1 Brand diagnosis    -> how this brand currently looks on each AI engine (hit rate / who is cited)
    Step 2 Industry matching  -> differences in source structure for this industry across engines
    Step 3 Source gap         -> which of the sources required by the knowledge base we have not yet captured
    Step 4 Output strategy    -> engine priority + per-engine placement checklist + content forms + placement cadence

## Two hard rules

1. **Deterministic, no LLM**. Same reason as source_plan's platform quota: the strategy is upstream of scheduling;
   the same measured data + the same knowledge base version must always produce the same strategy, otherwise "generate strategy -> later plans
   change accordingly" would look different on every click. All sorting has a deterministic tie-break (ties by engine id
   in lexicographic order); no randomness and no time in scoring.
2. **No new aggregation**. The brand diagnosis metric definition must match the "source analysis" page -- reuse
   `source_strategy.build_source_stats()`, where excluding own articles and errored responses is already handled.

## How the strategy becomes a plan

`strategy_weighted_sources()` is the only channel: re-weight cited domains by engine priority and feed the weighted
top_sources to the existing `source_plan.build_platform_quota()`. The quota algorithm itself
(long-tail threshold / paid cap / largest remainder method) does not change a single line -- the strategy only changes **input weights**, not
allocation rules, so the "why it is scheduled this way" explanation still holds.

Checklist actions that cannot go into the publish schedule (encyclopedia entries, account verification, webmaster verification, company profiles) go through
`push_infra_actions()` to the optimization task center, taking no scheduling slots.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from geo.models.ai_telemetry import AiTelemetryTopicORM
from geo.services import placement_kb as kb

log = logging.getLogger(__name__)

STRATEGY_VERSION = 1

# Engine priority -> cap on the multiplier for citation counts: the highest-weighted engine x (1 + BOOST), the lowest x 1.
# Capped at 1.0 deliberately -- the strategy can only **bias** the measured distribution, never conjure platforms that were never cited.
ENGINE_BOOST = 1.0

# Base weight kept however high the hit rate: fully hit engines should not drop out of placement view entirely (content needs maintenance).
MIN_GAP = 0.15

# Three placement cadence phases (per doc section 4.4), weeks stretched by months
CADENCE_PHASES = [
    {"key": "t0", "weeks": "第 1-2 周", "focus": "顶级权威信源打地基",
     "tiers": ("T0",)},
    {"key": "t1", "weeks": "第 3-4 周", "focus": "高权威信源铺开 + 交叉印证",
     "tiers": ("T1",)},
    {"key": "t2", "weeks": "第 5-6 周", "focus": "普通信源补位,持续复测",
     "tiers": ("T2", "T3")},
]


# --- Step 1: brand diagnosis ---------------------------------------------------

def _topic_engines(t: AiTelemetryTopicORM, observed: list[str]) -> list[str]:
    """Engines to produce a strategy for on this topic: configured monitoring engines first, otherwise those actually run."""
    try:
        cfg = json.loads(t.engines_json or "[]")
    except Exception:  # noqa: BLE001
        cfg = []
    out = [str(e).strip().lower() for e in cfg if str(e).strip()] if isinstance(cfg, list) else []
    for e in observed:
        if e and e not in out:
            out.append(e)
    return out


def _diagnose(stats: dict, engines: list[str]) -> list[dict]:
    """How each engine looks now: how many rows run, how many hits, who it cites, which answer forms it favors."""
    by_engine = {str(r.get("engine") or ""): r for r in (stats.get("per_engine") or [])}
    ed = stats.get("engine_domain_counts") or {}
    out: list[dict] = []
    for e in engines:
        row = by_engine.get(e) or {}
        cits = ed.get(e) or {}
        out.append({
            "engine": e,
            "name": kb.profile_of(e).get("name") or e,
            "responses": int(row.get("responses") or 0),
            "hit_rate_pct": float(row.get("hit_rate_pct") or 0.0),
            "citations": sum(int(n) for n in cits.values()),
            # Keep only the top 5 representative domains -- the full ranking is on the "source analysis" page; this is the conclusion page
            "top_domains": [{"domain": d, "count": n} for d, n in
                            sorted(cits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
            "answer_formats": row.get("answer_formats") or {},
            "measured": bool(row),
        })
    return out


# --- Step 4.1: engine priority -------------------------------------------------

def _engine_priority(diagnosis: list[dict], industry: dict) -> list[dict]:
    """Engine priority ranking -- coverage x weakness x industry weight.

    score = engine's response share x miss ratio x industry weight

    What each factor means:
      - response share: how often this engine is asked on this topic, deciding the reach
      - miss ratio: the less it answers with our brand, the more we should place on this engine's sources
      - industry weight: Doubao matters more in beauty, DeepSeek more in technology
    Fully hit engines keep a MIN_GAP base weight so they do not drop out of view entirely.
    """
    total_resp = sum(d["responses"] for d in diagnosis) or 1
    rows: list[dict] = []
    for d in diagnosis:
        share = d["responses"] / total_resp
        gap = max(MIN_GAP, 1 - d["hit_rate_pct"] / 100)
        w_ind = kb.industry_weight(industry, d["engine"])
        rows.append({**d, "share_pct": round(share * 100, 1),
                     "score": round(share * gap * w_ind, 6),
                     "industry_weight": w_ind})
    # With no measured data at all, scores are all 0 -- fall back to engine id lexicographic order, at least deterministic
    top = max((r["score"] for r in rows), default=0.0)
    rows.sort(key=lambda r: (-r["score"], r["engine"]))
    out: list[dict] = []
    for i, r in enumerate(rows):
        weight = round(r["score"] / top, 4) if top > 0 else 0.0
        prof = kb.profile_of(r["engine"])
        out.append({
            "engine": r["engine"], "name": r["name"], "rank": i + 1,
            "weight": weight, "score": r["score"],
            "share_pct": r["share_pct"], "hit_rate_pct": r["hit_rate_pct"],
            "citations": r["citations"],
            "ecosystem": prof.get("ecosystem") or "",
            "exclusive": prof.get("exclusive") or "",
            "summary": prof.get("summary") or "",
            "generic": bool(prof.get("generic")),
            "industry_weight": r["industry_weight"],
            "rationale": _priority_rationale(r, industry),
        })
    return out


def _priority_rationale(r: dict, industry: dict) -> str:
    if not r["measured"]:
        return "这一轮还没跑出该引擎的应答,暂时没有实测依据,按知识库口径给建议。"
    parts = [f"占本主题应答的 {r['share_pct']}%,命中率 {r['hit_rate_pct']}%"]
    if r["hit_rate_pct"] < 30:
        parts.append("答得出品牌的次数很少,是短板")
    elif r["hit_rate_pct"] >= 70:
        parts.append("已经答得比较稳,重点转向维护")
    if r["industry_weight"] > 1:
        parts.append(f"{industry.get('name') or '该行业'}在这个引擎上权重更高")
    elif r["industry_weight"] < 1:
        parts.append(f"{industry.get('name') or '该行业'}在这个引擎上权重偏低")
    return ";".join(parts) + "。"


# --- Step 3 + 4.2: source gap and placement checklist ---------------------------

# How many cited domains to look at per engine when judging "which channels this engine already covers".
# Too few would misjudge lower-ranked channels as uncovered; looking at all would require mapping hundreds of domains.
OCCUPIED_DOMAINS_MAX = 50


def _occupied(db, stats: dict, engines: list[str]) -> dict[str, set]:
    """Which placeable channels each engine already covers (platform names mapped from cited domains).

    The judgment reuses the four states of source_plan.resolve_domain, no separate mapping.
    Uses full engine-level citation details rather than the displayed top5 -- Zhihu ranked 8th is also covered.
    """
    from geo.services.source_plan import RWJ, resolve_domain, rwj_domain_index

    idx = rwj_domain_index(db)
    ed = stats.get("engine_domain_counts") or {}
    out: dict[str, set] = {}
    for e in engines:
        counts = ed.get(e) or {}
        got: set = set()
        for domain, _n in sorted(counts.items(),
                                 key=lambda kv: (-kv[1], kv[0]))[:OCCUPIED_DOMAINS_MAX]:
            r = resolve_domain(db, domain, index=idx)
            if r["kind"] == "platform" and r["platform"]:
                got.add(r["platform"])
            elif r["kind"] == "rwj_site":
                got.add(RWJ)
            elif r["kind"] == "profile":
                got.add("__profile__")
        out[e] = got
    return out


def _platform_plan(priority: list[dict], occupied: dict[str, set],
                   available: set, paid_only: set) -> list[dict]:
    """Per-engine placement checklists -- split into "can go into the publish schedule" and "infrastructure needing a person" buckets."""
    out: list[dict] = []
    for p in priority:
        prof = kb.profile_of(p["engine"])
        got = occupied.get(p["engine"]) or set()
        publishable: list[dict] = []
        infra: list[dict] = []
        for item in prof.get("checklist") or []:
            if item.get("kind") == kb.KIND_PUBLISH:
                plat = item.get("platform") or ""
                publishable.append({
                    "key": item["key"], "label": item["label"],
                    "tier": item.get("tier") or "", "platform": plat,
                    "note": item.get("note") or "",
                    "investable": plat in available,
                    "paid": plat in paid_only,
                    "occupied": plat in got,
                })
            else:
                infra.append({
                    "key": item["key"], "label": item["label"],
                    "tier": item.get("tier") or "", "kind": item.get("kind") or "",
                    "note": item.get("note") or "",
                    "occupied": "__profile__" in got and item.get("kind") == kb.KIND_PROFILE,
                })
        out.append({
            "engine": p["engine"], "name": p["name"], "rank": p["rank"],
            "priority_list": list(prof.get("priority_list") or []),
            "publishable": publishable, "infra": infra,
        })
    return out


def _gaps(platform_plan: list[dict]) -> list[dict]:
    """Source gap: checklist items not yet covered -- exactly what this round should fill."""
    out: list[dict] = []
    for row in platform_plan:
        missing = [it["label"] for it in row["publishable"]
                   if not it["occupied"] and it["investable"]]
        blocked = [f"{it['label']}({it['platform']} 当前投不了)"
                   for it in row["publishable"] if not it["investable"]]
        infra = [it["label"] for it in row["infra"] if not it["occupied"]]
        out.append({
            "engine": row["engine"], "name": row["name"],
            "missing": missing, "blocked": blocked, "infra_missing": infra,
        })
    return out


# --- Step 4.3 / 4.4: content forms and placement cadence -------------------------

def _content_forms(priority: list[dict], diagnosis: list[dict]) -> list[dict]:
    by_engine = {d["engine"]: d for d in diagnosis}
    out: list[dict] = []
    for p in priority:
        prof = kb.profile_of(p["engine"])
        fmts = (by_engine.get(p["engine"]) or {}).get("answer_formats") or {}
        top_fmt = sorted(fmts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
        out.append({
            "engine": p["engine"], "name": p["name"], "rank": p["rank"],
            "forms": list(prof.get("content_forms") or []),
            "avoid": prof.get("content_avoid") or "",
            "recency": prof.get("recency") or "",
            # Measured evidence: the most common organizational form when this engine answers this topic's questions
            "observed_formats": [{"format": k, "count": v} for k, v in top_fmt],
        })
    return out


def _cadence(platform_plan: list[dict], per_day: int, months: int) -> list[dict]:
    """Placement cadence: three phases by source tier, each listing the actions and approximate article counts."""
    span = max(1, int(months or 1))
    out: list[dict] = []
    for phase in CADENCE_PHASES:
        items: list[dict] = []
        for row in platform_plan:
            for it in row["publishable"] + row["infra"]:
                if it.get("tier") in phase["tiers"] and not it.get("occupied"):
                    items.append({"engine": row["name"], "label": it["label"],
                                  "platform": it.get("platform") or "",
                                  "rank": row["rank"]})
        items.sort(key=lambda x: (x["rank"], x["engine"], x["label"]))
        weeks = phase["weeks"] if span == 1 else f"{phase['weeks']}(按 {span} 个月等比拉伸)"
        out.append({
            "key": phase["key"], "weeks": weeks, "focus": phase["focus"],
            "items": items[:12],
            # Roughly how many articles this phase (two weeks, stretched by months) can hold -- lets operators check whether the checklist fits
            "planned_docs": int(per_day or 0) * 14 * span,
        })
    return out


# --- Main entry point ----------------------------------------------------------

def build_placement_strategy(db, topic_id: int, per_day: int = 5,
                             months: int = 1,
                             stats: Optional[dict] = None) -> dict[str, Any]:
    """Generate this topic's placement strategy (deterministic: same input always gives the same output).

    stats: pass it in if the caller already computed build_source_stats, saving a full table scan;
    it must be the one with with_engine_domains=True.
    """
    from geo.services.source_plan import available_platforms
    from geo.services.source_strategy import build_source_stats

    t = db.get(AiTelemetryTopicORM, topic_id)
    if t is None:
        raise ValueError(f"topic {topic_id} not found")

    if stats is None or "engine_domain_counts" not in stats:
        stats = build_source_stats(db, topic_id, with_engine_domains=True)

    observed = sorted((stats.get("engine_domain_counts") or {}).keys())
    observed += [str(r.get("engine") or "") for r in (stats.get("per_engine") or [])]
    engines = _topic_engines(t, [e for e in observed if e])

    industry = kb.match_industry(t.industry or "", f"{t.name or ''} {t.target or ''}")
    diagnosis = _diagnose(stats, engines)
    priority = _engine_priority(diagnosis, industry)
    available, avail_notes, paid_only = available_platforms(db)
    occupied = _occupied(db, stats, engines)
    platform_plan = _platform_plan(priority, occupied, available, paid_only)

    notes: list[dict] = []
    if not any(d["measured"] for d in diagnosis):
        notes.append({
            "code": "no_measured",
            "text": "这个主题还没有跑出可用的应答数据,策略里的排序按知识库口径给,"
                    "跑一轮基线后重新生成会更准。",
            "level": "warn",
        })
    if not industry.get("matched"):
        notes.append({
            "code": "industry_unmatched",
            "text": "主题资料里没填行业(或没匹配上已整理的行业口径),"
                    "这一版没有做行业加权 —— 补上行业后重新生成会更贴。",
            "level": "info",
        })
    if industry.get("compliance"):
        notes.append({"code": "compliance", "text": industry["compliance"], "level": "warn"})
    for n in avail_notes:
        notes.append({"code": "channel", "text": n, "level": "info"})
    generic = [p["name"] for p in priority if p["generic"]]
    if generic:
        notes.append({
            "code": "generic_engine",
            "text": f"{'、'.join(generic)} 还没有整理专属信源口径,按通用规则给建议。",
            "level": "info",
        })

    return {
        "v": STRATEGY_VERSION,
        "kb_version": kb.KB_VERSION,
        "computed_at": datetime.utcnow().isoformat(),
        "target": (t.target or t.name or ""),
        "per_day": int(per_day or 5),
        "months": int(months or 1),
        "industry": {
            "key": industry.get("key") or "general",
            "name": industry.get("name") or "通用",
            "core": industry.get("core") or "",
            "matched": bool(industry.get("matched")),
            "compliance": industry.get("compliance") or "",
        },
        "diagnosis": {
            "responses_total": int(stats.get("responses_total") or 0),
            "own_cited_responses": int(stats.get("own_cited_responses") or 0),
            "engines": diagnosis,
        },
        "engine_priority": priority,
        "platform_plan": platform_plan,
        "gaps": _gaps(platform_plan),
        "content_forms": _content_forms(priority, diagnosis),
        "cadence": _cadence(platform_plan, int(per_day or 5), int(months or 1)),
        # Cold-start fallback order: without measured citation data, quotas are laid out in this order (knowledge base prior,
        # not fabricated citation data)
        "cold_start_order": cold_start_order(priority),
        "notes": notes,
    }


def cold_start_order(priority: list[dict]) -> list[str]:
    """Engine priority -> platform cold-start order (platforms preferred by high-priority engines first, deduped, order preserved)."""
    out: list[str] = []
    for p in priority:
        for plat in kb.profile_of(p["engine"]).get("platforms") or ():
            if plat not in out:
                out.append(plat)
    return out


# --- Strategy -> quota ---------------------------------------------------------

def strategy_weighted_sources(stats: dict, strategy: dict) -> list[dict]:
    """Weight cited domains by engine priority, producing top_sources to feed build_platform_quota.

    Weights only **bias**, never fabricate: a domain never cited stays 0 after weighting -- the strategy changes
    "among channels already cited, which deserves more articles", and never schedules a channel AI has never read.

    Multiplier = 1 + ENGINE_BOOST x engine weight (an engine with weight 1 gets x2, weight 0 gets x1).
    """
    ed = stats.get("engine_domain_counts") or {}
    mult = {p["engine"]: 1 + ENGINE_BOOST * float(p.get("weight") or 0.0)
            for p in (strategy.get("engine_priority") or [])}
    out: list[dict] = []
    for s in (stats.get("top_sources") or []):
        domain = str(s.get("domain") or "")
        if not domain:
            continue
        weighted = 0.0
        for engine, counts in ed.items():
            n = counts.get(domain)
            if n:
                weighted += n * mult.get(engine, 1.0)
        # When engine-level details are missing (old data), fall back to raw counts; must not zero out this domain
        count = int(round(weighted)) if weighted else int(s.get("count") or 0)
        out.append({**s, "count": count, "raw_count": int(s.get("count") or 0)})
    out.sort(key=lambda x: (-x["count"], x["domain"]))
    return out


def build_strategy_quota(db, topic_id: int, per_day: int,
                         months: int = 1) -> tuple[dict, dict]:
    """Compute "strategy + platform quota weighted by the strategy" in one go. Returns (strategy, quota).

    This is the only implementation path for platform_mode="strategy" -- start / reschedule / monthly renewal
    all go through it, so renewal never silently falls back to the plain source mode.
    """
    from geo.services.source_plan import build_platform_quota
    from geo.services.source_strategy import build_source_stats

    stats = build_source_stats(db, topic_id, with_engine_domains=True)
    strategy = build_placement_strategy(db, topic_id, per_day=per_day,
                                        months=months, stats=stats)
    quota = build_platform_quota(
        db, topic_id, per_day,
        top_sources=strategy_weighted_sources(stats, strategy),
        cold_start_order=strategy.get("cold_start_order") or None,
    )
    quota["strategy_weighted"] = True
    return strategy, quota


# --- Infrastructure checklist -> optimization task center ---------------------

ACTION_SOURCE = "placement_strategy"


def push_infra_actions(db, topic_id: int, strategy: dict) -> int:
    """Actions in the placement checklist that cannot go into the publish schedule -> geo_action_items, idempotent (created once per key).

    Same approach as source_strategy._push_media_recs_to_action_items: dedup by
    (topic, source, source_key), skip if it exists -- rescheduling a hundred times will not flood.
    Returns the number created.
    """
    from geo.models.ai_telemetry import GeoActionItemORM

    created = 0
    for row in (strategy.get("platform_plan") or []):
        rank = int(row.get("rank") or 9)
        priority = "P0" if rank == 1 else ("P1" if rank <= 3 else "P2")
        for item in (row.get("infra") or []):
            if item.get("occupied"):
                continue
            source_key = f"{row['engine']}:{item['key']}"[:128]
            exists = (
                db.query(GeoActionItemORM)
                  .filter(GeoActionItemORM.topic_id == topic_id,
                          GeoActionItemORM.source == ACTION_SOURCE,
                          GeoActionItemORM.source_key == source_key)
                  .first()
            )
            if exists is not None:
                continue
            body = [f"{row['name']}的投放策略里,这一项排不进发文表,需要人工去建。"]
            if item.get("tier"):
                body.append(f"信源级别:{item['tier']}")
            if item.get("note"):
                body.append(item["note"])
            db.add(GeoActionItemORM(
                topic_id=topic_id, source=ACTION_SOURCE, source_key=source_key,
                source_ref_json=json.dumps(
                    {"engine": row["engine"], "engine_name": row["name"], **item},
                    ensure_ascii=False),
                priority=priority,
                title=f"{row['name']} · {item['label']}"[:200],
                body_md="\n\n".join(body),
                suggested_action="other",
            ))
            created += 1
    return created
