"""One-click full diagnosis -- real computation of the six metrics.

The metric definition **directly imports the report center's `_metrics_from_counts`**, never copying the formula:
that is the single source; a copy would drift sooner or later, and two reports delivered to a customer with mismatched numbers is a credibility incident.

Data comes from run-now single-round sampling (one row per query x engine, with full answer text and citations);
the raw material is complete, the bottleneck is only losing granularity during aggregation. This does three things:

  1. Unified hit judgment -- the collection side had two (brand aliases + domain substring / brand literal only);
     chapter 05's overall mention rate and chapter 06's overall exposure rate must share a source, otherwise the two chapters contradict
  2. One LLM batch judgment solves three things at once: negative judgment for content neutral-positive rate, rank (TOP1/TOP3),
     and competitor mention counts (share-of-voice denominator). Input is **all valid rows**, not just hit rows --
     competitors are densest exactly in non-hit rows; feeding only hit rows would shrink the share-of-voice denominator and inflate the metric
  3. Aggregate the eleven counters sliced by engine / term type / engine x term type

Degradation red line: `_metrics_from_counts` returns 100 for neutral-positive rate when hits are 0
("no hits means neutral-positive"). When the whole batch judgment fails, it must be overwritten to None here;
copying the return value would show "not judged" as "100% neutral-positive".

Denominator red line: the three exposure rates must share a scale (see rank_over_valid in `_metrics`).
They are shown side by side on the same card; if TOP1/TOP3 used hit count as denominator,
two hits both ranked 2nd would be "TOP3 100%", directly contradicting the adjacent "overall exposure rate 4.8%".
With a shared scale, TOP1 <= TOP3 <= overall exposure rate always holds.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# v2: tightened hit metric definition -- only recommended/listed counts as exposure, incidental mentions do not
SPEC_VERSION = "diag_metrics_v2_recommended"
# v2: competitor judgment adds a "same track" criterion + feeds the category to the model (well-known companies from adjacent tracks no longer enter the ranking)
JUDGE_PROMPT_VERSION = "diag_judge_v2_same_lane"

METRIC_KEYS = ("exposure", "top1", "top3", "accuracy", "citation", "mention")

_JUDGE_BATCH = 8
_JUDGE_WORKERS = 3
_ANSWER_CLIP = 1200


@dataclass
class MetricRow:
    query: str
    engine: str
    scene: str
    error: Optional[str] = None
    # Giveaway questions: term type is brand/compare **or the question text includes the brand name**; excluded from the denominator of the three exposure rates.
    # The criterion is computed by the caller with full_diagnosis_competitive.is_exposure_only and passed in --
    # only this flag is honored here, no longer judging by scene ourselves (that was exactly the source of the two denominators before 2026-09-23,
    # "chapter 05: 9 category questions / chapter 06: 13").
    exposure_only: bool = False
    # hit = **recommended/listed** (counts toward exposure rate); mentioned = brand name appeared in the answer (including incidental listings)
    hit: bool = False
    mentioned: bool = False
    mention_type: Optional[str] = None   # recommended | passing | none | None (not judged)
    rank: Optional[int] = None
    stance: Optional[str] = None        # negative | neutral | positive | None (not judged)
    cited: bool = False
    competitors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "scene": self.scene, "engine": self.engine,
            "hit": self.hit, "mentioned": self.mentioned,
            "mention_type": self.mention_type,
            "rank": self.rank, "stance": self.stance,
            "cited": self.cited, "competitors": self.competitors,
            "error": self.error,
        }


# --------------- Hit judgment (single source) ---------------


def make_hit_fn(brand: str, brand_terms: Optional[list[str]],
                domain: str) -> Callable[[str], bool]:
    terms = [t.strip().lower() for t in ([brand] + list(brand_terms or [])) if t and t.strip()]
    if domain:
        terms.append(domain.lower())
    terms = [t for t in dict.fromkeys(terms) if t]

    def _hit(answer: str) -> bool:
        low = (answer or "").lower()
        return any(t in low for t in terms)

    return _hit


# --------------- Own domain judgment (article citation rate) ---------------

# Multi-label TLDs: com.cn / co.uk etc.; when taking the registered domain we cannot keep only the last two labels
_MULTI_TLDS = (
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "co.uk", "org.uk",
    "com.hk", "com.tw", "co.jp", "com.au", "co.kr",
)


def registrable_domain(host: str) -> str:
    h = (host or "").strip().lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    parts = h.split(".")
    if len(parts) <= 2:
        return h
    for suf in _MULTI_TLDS:
        if h.endswith("." + suf):
            return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def make_cited_fn(url: str, brand_terms: Optional[list[str]]) -> Callable[[list], bool]:
    """Diagnosis has no topic's own article library, so the metric definition degrades to "website registered domain cited".

    Differs from the report center's owned_published_urls metric definition; the data block marks citation_scope,
    and the frontend states it in the metric definition notes, so same-named metrics in the two reports are not mistaken for the same thing.
    """
    owned = set()
    host = urlparse(url if "://" in url else "https://" + url).netloc
    if host:
        owned.add(registrable_domain(host))
    for t in (brand_terms or []):
        s = str(t).strip().lower()
        if "." in s and " " not in s:
            owned.add(registrable_domain(s))
    owned = {d for d in owned if d}

    def _cited(citations: list) -> bool:
        for c in citations or []:
            u = c if isinstance(c, str) else (getattr(c, "url", None) or (c or {}).get("url"))
            if not u:
                continue
            try:
                h = urlparse(str(u)).netloc
            except Exception:  # noqa: BLE001
                continue
            if h and registrable_domain(h) in owned:
                return True     # Each response counts once, boolean like the report center
        return False

    return _cited, sorted(owned)


# --------------- LLM batch judgment ---------------

# Rank and competitor judgment principles follow the batch run center's extract_v3 metric definition (V-networks/worker-center/app/llm.py),
# ensuring diagnosis TOP1/share of voice uses the same yardstick as the report center.
_JUDGE_SYSTEM = "\n".join([
    "你是 AI 搜索结果的结构化标注员。给定若干条「问题 + 智能引擎回答」,逐条标注。",
    "",
    "## 标注规则",
    "1. brand_rank:目标品牌在回答里的推荐次序。看实体级别的推荐次序,不是字面出现次序;",
    "   罗列式回答按列表顺序,叙述式回答按被推荐的主次。品牌未被提及时填 null。",
    "2. competitors:回答里出现的、与目标品牌**同赛道**的其他品牌名。",
    "   同赛道 = 卖同一类东西、面向同一类客户、能被同一个采购需求替换掉。",
    "   只要主营业务不同就不算竞品,哪怕名气再大、哪怕出现在同一段推荐里 ——",
    "   例:媒介投放代理 ≠ 品牌战略咨询;广告代理 ≠ 电商代运营;营销服务 ≠ 艺人经纪。",
    "   拿不准是否同赛道就不要抽。也不要抽平台名、媒体名、通用词、品类词。没有就给空数组。",
    "3. mention_type:目标品牌在这条回答里是怎么出现的 —— 这条决定它算不算「露出」。",
    "   recommended = 被推荐、被列入推荐/排行名单、被当作可选方案之一给出,",
    "                 或问题本身就问这个品牌而回答给出了实质介绍;",
    "   passing     = 只是顺带出现 —— 举例说明、背景陈述、竞品旁边被带一句、",
    "                 出现在与提问意图无关的段落,读者不会因此把它当成一个选项;",
    "   none        = 根本没出现。",
    "   宁严勿宽:拿不准算 passing。",
    "4. stance:回答对目标品牌的态度。",
    "   negative = 存在贬损评价、风险提示、明确不推荐,或在与竞品比较中被判劣;",
    "   neutral  = 提及但无褒贬,或只是没被重点推荐;",
    "   positive = 被推荐或正面评价。",
    "   品牌未被提及时填 null,不要算作 negative。",
    "",
    "只输出 JSON,不要 ``` 围栏。",
])


def _judge_batch(rows: list[tuple[int, MetricRow, str]], brand: str,
                 provider: str, model_id: str, api_key: str,
                 category: str = "") -> dict[int, dict]:
    from geo.services.full_diagnosis_generator import _chat_json

    items = []
    for local_i, (_gid, row, answer) in enumerate(rows):
        items.append({"i": local_i, "query": row.query,
                      "answer": (answer or "")[:_ANSWER_CLIP]})
    # The track goes in the user section, not the system section: within one annotation batch only the category varies per order
    lane = f"目标品牌所属赛道:{category}\n" if category else ""
    user = (
        f"目标品牌:{brand}\n{lane}\n待标注条目:\n"
        + json.dumps(items, ensure_ascii=False)
        + '\n\n输出格式:{"items":[{"i":0,"mention_type":"recommended|passing|none",'
          '"stance":"neutral|positive|negative|null",'
          '"brand_rank":整数或null,"competitors":["品牌名"]}]}'
    )
    # temperature 0: judgment is a classification task and needs no creativity. It used to be 0.1; recomputing the same batch three times
    # gave 46.7% / 46.0% / 47.3% -- hit judgments flipped back and forth on borderline cells, making report numbers irreproducible.
    # 0 cannot guarantee full determinism (the gateway is still non-deterministic), but keeps jitter to a minimum.
    parsed = _chat_json(provider, model_id, api_key, _JUDGE_SYSTEM, user, 0.0)
    out: dict[int, dict] = {}
    for it in (parsed.get("items") or []):
        if not isinstance(it, dict):
            continue
        try:
            local = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        if not (0 <= local < len(rows)):
            continue
        out[rows[local][0]] = it
    return out


def judge_rows(brand: str, pairs: list[tuple[int, MetricRow, str]],
               category: str = "") -> tuple[dict[int, dict], int, int]:
    """Batch annotation. Returns (results, successful batches, total batches). Results are empty when all fail.

    category: the target brand's track (from the user-entered industry term). Without it the model can only judge competitors by "fame +
    appearing in the same recommendation paragraph", pulling in well-known companies from adjacent tracks -- in the Juxing Media
    (media/integrated marketing) report, brand strategy consultancy Hua & Hua and e-commerce operator Baozun
    were both extracted as competitors, and #2 on the ranking was not a peer."""
    from geo.services.full_diagnosis_generator import _llm_provider

    if not pairs:
        return {}, 0, 0
    provider, model_id, api_key = _llm_provider()
    batches = [pairs[i:i + _JUDGE_BATCH] for i in range(0, len(pairs), _JUDGE_BATCH)]
    merged: dict[int, dict] = {}
    ok = 0
    with ThreadPoolExecutor(max_workers=_JUDGE_WORKERS) as pool:
        futs = [pool.submit(_judge_batch, b, brand, provider, model_id, api_key, category)
                for b in batches]
        for f in futs:
            try:
                merged.update(f.result() or {})
                ok += 1
            except Exception as e:  # noqa: BLE001
                log.warning("diag metrics judge batch failed: %s", e)
    return merged, ok, len(batches)


# --------------- Counting and aggregation ---------------


def counts_from_rows(rows: list[MetricRow], exposure_only_scenes: set[str],
                     self_scope: bool = False) -> dict[str, int]:
    """Eleven counters, fields strictly aligned with the report center's COUNT_KEYS.

    Category question judgment uses row.exposure_only (computed by the caller); exposure_only_scenes is only kept
    for the self_scope check of by_scene slices.

    self_scope=True is for slicing by term type: if the brand-term / competitor-comparison slices excluded themselves per the global metric definition,
    cat_* would be all 0, and the page would show "brand term exposure rate 0%", an obvious contradiction. Slices do not exclude themselves.
    """
    c = {k: 0 for k in (
        "valid_count", "hit_count", "top1_count", "top3_count",
        "neg_hit_count", "cited_count", "comp_mention_count",
        "cat_valid_count", "cat_hit_count", "cat_top1_count", "cat_top3_count",
        # Neutral-positive rate metric definition (2026-09-27): the denominator is "cells where the brand name appeared", not "cells where it was recommended".
        # The old metric definition only counted recommended cells, and when a brand is recommended that text by definition is not negative
        # -- "XX is a core COFCO brand with great value" -- so the neutral-positive rate was always 100% for every brand;
        # reports #138/#140/#141 were all 100%, with no discrimination at all. Incidental mentions and negative contexts
        # were exactly what was kept out of the denominator, and those are what matter most.
        "men_count", "neg_men_count",
    )}
    for r in rows:
        if r.error:
            continue
        c["valid_count"] += 1
        if r.mentioned:
            c["men_count"] += 1
            if r.stance == "negative":
                c["neg_men_count"] += 1
        in_cat = self_scope or not r.exposure_only
        if in_cat:
            c["cat_valid_count"] += 1
        if r.hit:
            c["hit_count"] += 1
            if in_cat:
                c["cat_hit_count"] += 1
            if r.rank == 1:
                c["top1_count"] += 1
                if in_cat:
                    c["cat_top1_count"] += 1
            if r.rank is not None and r.rank <= 3:
                c["top3_count"] += 1
                if in_cat:
                    c["cat_top3_count"] += 1
            if r.stance == "negative":
                c["neg_hit_count"] += 1
        if r.cited:
            c["cited_count"] += 1
        c["comp_mention_count"] += len(set(r.competitors or []))
    return c


def _metrics(counts: dict[str, int], accuracy_ok: bool,
             citation_ok: bool = True) -> dict[str, Any]:
    from geo.api.ai_telemetry_reports import _metrics_from_counts

    # rank_over_valid: the three exposure rates share a scale (denominator is the number of valid category-term responses).
    # Diagnosis has no topic and no fixed response denominator; without this switch TOP1/TOP3 degrade into
    # the conditional probability "rank given a hit" -- if category terms hit only twice and both ranked 2nd,
    # the page would show the self-contradictory pair "overall exposure rate 4.8% / TOP3 exposure rate 100%".
    m = dict(_metrics_from_counts(counts, rank_over_valid=True))
    # Recompute neutral-positive rate: denominator = cells where the brand name appeared (including incidental mentions and negative contexts), not recommended cells.
    # Null when the denominator is 0 -- upstream returns 100 when hits are 0 ("no hits means neutral-positive"), a degraded value,
    # not a measurement; copying it would show "this column never mentioned the brand" as "100% neutral-positive"
    # (report #141's deepseek had 0 hits yet showed 100%).
    _men = int(counts.get("men_count") or 0)
    m["accuracy"] = (round((1 - int(counts.get("neg_men_count") or 0) / _men) * 100, 1)
                     if _men else None)
    if not accuracy_ok:
        # Red line: upstream returns 100 when hits are 0 ("no hits means neutral-positive");
        # copying it when judgment did not run would show "not judged" as "100% neutral-positive"
        m["accuracy"] = None
    if not citation_ok:
        # Without an own domain (no website given) we cannot tell "who was cited"; 0% would be read as "never cited"
        m["citation"] = None
    return m


def apply_sentiment_risk(metrics: dict[str, Any],
                         sentiment: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Fold sentiment risk into the content neutral-positive rate (2026-09-28).

        neutral-positive rate (risk-adjusted) = AI-side neutral-positive rate x (1 - medium/high risk evidence count / total evidence count)

    Why discount: the AI-side neutral-positive rate only looks at "what AI answers say"; report #146 measured 98.7% -- because the
    8 pieces of negative evidence (expired-oil penalty, food safety complaints) were not cited even once by AI this round (source overlap 0/508).
    The metric looks clean, but the risk is clearly there; a customer seeing 98.7% would think all is fine.

    Why "medium/high risk share" instead of "all negative share": the report's sentiment health is already
    "(50 + 50 x net positive rate) after shrinkage, then deduct by medium/high risk share" -- using the same deduction base
    unifies the metric definitions of the two metrics. Deducting by all negatives would include low-risk noise (like a lukewarm promotion),
    pushing #146 down to 26.9%, which distorts it instead.

    The discount is written into overall / by_engine / by_scene, and the factor and inputs are kept in metric_scope
    for reference. Without sentiment data it returns unchanged.
    """
    data = (sentiment or {}).get("data") if isinstance(sentiment, dict) else None
    if not isinstance(data, dict):
        return metrics
    dist = data.get("risk_distribution") or data.get("risk_dist") or []
    if not isinstance(dist, list) or not dist:
        return metrics
    total = sum(int(x.get("count") or 0) for x in dist if isinstance(x, dict))
    risky = sum(int(x.get("count") or 0) for x in dist
                if isinstance(x, dict) and str(x.get("level") or "").lower() in ("medium", "high"))
    if total <= 0:
        return metrics
    factor = max(0.0, 1.0 - risky / total)

    def _adj(block: dict[str, Any]) -> None:
        m = block.get("metrics") or {}
        if m.get("accuracy") is not None:
            m["accuracy"] = round(m["accuracy"] * factor, 1)

    _adj(metrics.get("overall") or {})
    for b in metrics.get("by_engine") or []:
        _adj(b)
    for b in metrics.get("by_scene") or []:
        _adj(b)
    metrics.setdefault("metric_scope", {})["risk_adjust"] = {
        "applied": True, "factor": round(factor, 4),
        "risky_count": risky, "evidence_total": total,
        "basis": "sentiment.risk_distribution(medium+high)",
    }
    return metrics


def build_metrics(*, rows: list[MetricRow], brand: str, owned_domains: list[str],
                  accuracy_ok: bool, rank_source: str, mention_source: str,
                  accuracy_status: str, neg_scope_count: int,
                  judge_model: str, hit_terms: list[str]) -> dict[str, Any]:
    from geo.api.ai_telemetry_reports import EXPOSURE_ONLY_SCENES

    excl = set(EXPOSURE_ONLY_SCENES)
    # No website given -> no own domain to compare against; null the whole article citation rate block instead of 0%
    citation_ok = bool(owned_domains)

    by_engine: list[dict[str, Any]] = []
    for eng in sorted({r.engine for r in rows if r.engine}):
        sub = [r for r in rows if r.engine == eng]
        cnt = counts_from_rows(sub, excl)
        by_engine.append({"engine": eng, "counts": cnt,
                          "metrics": _metrics(cnt, accuracy_ok, citation_ok)})

    by_scene: list[dict[str, Any]] = []
    for sc in sorted({r.scene for r in rows if r.scene}):
        sub = [r for r in rows if r.scene == sc]
        self_scope = sc in excl
        cnt = counts_from_rows(sub, excl, self_scope=self_scope)
        by_scene.append({
            "scene": sc,
            "query_count": len({r.query for r in sub}),
            "counts": cnt,
            "metrics": _metrics(cnt, accuracy_ok, citation_ok),
            "exposure_excluded_from_global": self_scope,
        })

    overall_counts = counts_from_rows(rows, excl)
    return {
        "spec_version": SPEC_VERSION,
        "metric_scope": {
            "exposure_only_scenes": sorted(excl),
            "citation_scope": "own_domain" if citation_ok else "none",
            "owned_domains": owned_domains,
            "rank_source": rank_source,
            "mention_source": mention_source,
            "accuracy_status": accuracy_status,
            "neg_scope_count": neg_scope_count,
            # Exposure metric definition: mentioned != recommended. Provide both numbers so the report can explain
            # "the brand name appeared N times, but only M times was it actually recommended"
            "mentioned_count": sum(1 for r in rows if not r.error and r.mentioned),
            "recommended_count": sum(1 for r in rows if not r.error and r.hit),
            "hit_rule": "recommended_only",
            "judge_prompt_version": JUDGE_PROMPT_VERSION,
            "judge_model": judge_model,
            "hit_terms": hit_terms,
        },
        "overall": {"counts": overall_counts,
                    "metrics": _metrics(overall_counts, accuracy_ok, citation_ok)},
        "by_engine": by_engine,
        "by_scene": by_scene,
        "rows": [r.as_dict() for r in rows],
    }


# --------------- Regex fallback for rank ---------------


def regex_rank(answer: str, brand: str) -> Optional[int]:
    """Fallback when the LLM is unavailable: find the brand's item by literal order in an ordered list."""
    if not answer or not brand:
        return None
    low = answer.lower()
    b = brand.lower()
    if b not in low:
        return None
    items: list[str] = []
    for line in answer.splitlines():
        s = line.strip()
        m = re.match(r"^(?:\d+[\.、\)]|[-*·])\s*(.+)$", s)
        if m:
            items.append(m.group(1).lower())
    for i, it in enumerate(items, start=1):
        if b in it:
            return i
    return None
