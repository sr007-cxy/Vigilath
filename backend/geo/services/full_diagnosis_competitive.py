"""Competitive intelligence for one-click diagnosis -- via the batch run center (telemetry-service /run-now) channel.

Replaces the engine orchestration built into geo_checker.modes.competitive_intel (direct API + browser-service,
two rounds of 11 serial queries, measured 25-30 minutes per item): the batch run center schedules uniformly -- Doubao via the ARK Bot API
without a browser, deepseek via pooled warm sessions, overseas engines via OpenRouter, parallel across engines.
Per-item time drops to 2-6 minutes.

Query building and all four analyzers reuse the existing geo_checker implementations; the output dict shape is identical to
competitive_intel(return_data=True) -- score synthesis / frontend rendering / LLM
summaries need zero changes.

One fix compared with the old mode: target_brand uses the real brand name entered by the user (e.g. "<brand-b>" ("<brand-b>")),
instead of deriving it from the domain (brand-b) -- Chinese engine answers only contain the Chinese brand name, so the old derivation
dropped the mention rate to 0.

ENV:
    TELEMETRY_SERVICE_URL           batch run center URL (same source as ai_telemetry.py)
    GEO_DIAG_COMPETITIVE_ENGINES    comma-separated engine names, default "deepseek,doubao"
    GEO_DIAG_COMPETITIVE_TIMEOUT    overall timeout in seconds, default 600
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from geo.services.telemetry_client import post_run_now

log = logging.getLogger(__name__)

TELEMETRY_SERVICE_URL = os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095")
# All 5 engines supported in China (qwen/wenxin run serially through the browser pool, about 6-9 minutes for the whole item;
# to speed up, narrow back to deepseek,doubao via env)
DEFAULT_ENGINES = "deepseek,doubao,qwen,wenxin,yuanbao"


def excluded_engines() -> set:
    """Globally disabled engines (comma-separated session_key).

    When an engine's account pool has problems / is in a risk-control cooldown, there must be a switch to stop it without code changes;
    both engine paths of the diagnosis (the batch run center here + the entity audit's browser engines) read it."""
    raw = os.environ.get("GEO_EXCLUDE_ENGINES", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _engines() -> List[str]:
    raw = os.environ.get("GEO_DIAG_COMPETITIVE_ENGINES", DEFAULT_ENGINES)
    excl = excluded_engines()
    return [e.strip() for e in raw.split(",") if e.strip() and e.strip().lower() not in excl]


def _timeout() -> float:
    return float(os.environ.get("GEO_DIAG_COMPETITIVE_TIMEOUT", "600"))


# Engine display aliases: doubao_browser (physical device browser) and doubao_mobile (physical device mobile) have no entries in the frontend engine
# allowlist; without mapping, report column headers would print raw keys and miss logos. The mapping only changes display identity, not which channel runs.
# ★ The gate is stricter than the report center (ai_telemetry_reports.set_engine_alias): the denominator here is the actual row count,
#   with no pinned denominator as a backstop, so when "both channels are running at once" they must stay unmerged. Do not loosen it along with that one.
_DEFAULT_ENGINE_ALIAS = {
    "doubao_browser": "doubao", "doubao_mobile": "doubao",
    "wenxin_browser": "wenxin", "qwen_browser": "qwen",
    # yuanbao_browser (2026-09-21): Yuanbao physical device channel. The strict gate above matters especially here --
    # it and the main channel yuanbao **intentionally run at the same time** (primary + backup); merging them would dilute the hit rate.
    "yuanbao_browser": "yuanbao",
    # Overseas physical device channels merged with same-named models
    "gemini_browser": "gemini", "perplexity_browser": "perplexity",
    "chatgpt_browser": "chatgpt", "claude_browser": "claude",
}


def _engine_alias() -> Dict[str, str]:
    raw = os.environ.get("GEO_DIAG_ENGINE_ALIAS_JSON", "").strip()
    alias = dict(_DEFAULT_ENGINE_ALIAS)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                alias = {str(k): str(v) for k, v in parsed.items()}
        except Exception:  # noqa: BLE001
            log.warning("GEO_DIAG_ENGINE_ALIAS_JSON is not valid JSON, using default aliases")
    engines = set(_engines())
    # No mapping when both channels are enabled -- merging into one column would double the denominator and dilute the hit rate
    return {k: v for k, v in alias.items() if k in engines and v not in engines}


# -- Visibility metric definition (2026-09-15, report #130 Inman retrospective) ----------------------------
#  (1) Mention rate only counts category questions: brand-term / competitor-comparison questions that include the brand name (EXPOSURE_ONLY_SCENES, or the question
#     text directly contains the brand name) are giveaway questions whose answers necessarily mention the brand -- 4 such questions out of 13 inflated Doubao's
#     real exposure of 1/9 to 38.5%. They are reported separately as "brand question response rate", not counted in visibility.
#  (2) (Reverted 2026-09-23) weights were once discounted by rank. Reason for reverting: the same card shows side by side
#     "overall exposure rate / TOP1 / TOP3"; only the overall one was weighted and the other two not, three different scales,
#     producing unreadable combinations like TOP3 > overall exposure rate. Rank is now expressed by the TOP1 / TOP3 columns,
#     and exposure rate is back to the yes/no metric definition of "how many cells recommended it".
#  (3) An engine with valid responses for fewer than half the questions -> insufficient data, excluded from the overall rate (previously deepseek answered only 3 of 13 cells
#     yet still entered the average, quietly shrinking the denominator).
def is_exposure_only(scene: str) -> bool:
    """Whether this question is a "giveaway" -- the kind excluded from the exposure metric definition.

    **Giveaway = brand terms + competitor-comparison terms, just these two term types**; the only criterion is scene.
    scene is authoritatively decided by `full_diagnosis_queries.scene_for_text` when terms are produced
    (question contains brand name -> brand, contains competitor name or comparison phrasing -> compare); the model cannot change it.

    ★ Before 2026-09-23 there were three implementations, splitting the denominator into a 9-question and a 13-question version
      (report #138's "overall mention rate 73.3% / overall exposure rate 76.9% / own brand mention rate 76.9%").
      Now there is only this one, called from all three places.
      There was once a plan to re-check by question text here too, later removed: scene_for_text uses the same
      brand name set, so the re-check would always give the same answer, and another criterion would only add another drift point -- the real
      root cause was that the set omitted the brand name itself, fixed in _brand_names.
    """
    return scene in _exposure_only_scenes()


def _exposure_only_scenes():
    from geo.api.ai_telemetry_reports import EXPOSURE_ONLY_SCENES
    return EXPOSURE_ONLY_SCENES


RANK_WEIGHTS = {"top3": 1.0, "4-10": 0.6, "11+": 0.3, "unranked": 0.6}


def _rank_weight(rank) -> float:
    if rank is None:
        return RANK_WEIGHTS["unranked"]
    if rank <= 3:
        return RANK_WEIGHTS["top3"]
    if rank <= 10:
        return RANK_WEIGHTS["4-10"]
    return RANK_WEIGHTS["11+"]


def compute_visibility_rates(valid, *, query_texts, scene_map, match_terms, rank_matrix,
                             engines_used, hit_matrix=None, mentioned=None) -> Dict[str, Any]:
    """Compute per-engine/overall mention rates per the three metric definitions above. valid: responses with engine/query/answer;
    mentioned(answer)->bool decides a hit, defaulting to match_terms substring matching."""
    terms = [t for t in (match_terms or []) if t]
    if mentioned is None:
        def mentioned(answer: str) -> bool:
            low = (answer or "").lower()
            return any(t in low for t in terms)

    def _is_brand_query(q: str) -> bool:
        return is_exposure_only((scene_map or {}).get(q, ""))

    slots = len(query_texts) or 1
    per_engine: Dict[str, Dict[str, Any]] = {}
    for r in valid:
        st = per_engine.setdefault(r.engine, {"total": 0, "hit": 0, "weighted": 0.0,
                                              "brand_total": 0, "brand_hit": 0, "valid": 0})
        st["valid"] += 1
        hit = mentioned(r.answer)
        if hit_matrix is not None and r.query in hit_matrix:
            hit_matrix[r.query][r.engine] = hit
        if _is_brand_query(r.query):
            st["brand_total"] += 1
            st["brand_hit"] += 1 if hit else 0
            continue
        st["total"] += 1
        if hit:
            st["hit"] += 1
            # Since 2026-09-23 no rank discounting: rank is expressed separately by the TOP1 / TOP3 columns.
            # Folding it into exposure rate would break TOP1 <= TOP3 <= overall exposure rate, which must hold when shown side by side.
            st["weighted"] += 1.0
    for eng in engines_used or []:
        per_engine.setdefault(eng, {"total": 0, "hit": 0, "weighted": 0.0, "brand_total": 0, "brand_hit": 0, "valid": 0})
    insufficient = sorted(e for e, st in per_engine.items() if st["valid"] < slots / 2)
    per_engine_rates = {
        eng: round(st["weighted"] / st["total"] * 100, 1) if st["total"] else 0.0
        for eng, st in per_engine.items()
    }
    used = {e: st for e, st in per_engine.items() if e not in insufficient}
    total_hits = sum(st["hit"] for st in used.values())
    total_weighted = sum(st["weighted"] for st in used.values())
    total_answers = sum(st["total"] for st in used.values())
    overall_rate = round(total_weighted / total_answers * 100, 1) if total_answers else 0.0
    brand_total = sum(st["brand_total"] for st in used.values())
    brand_hit = sum(st["brand_hit"] for st in used.values())
    return {
        "per_engine_rates": per_engine_rates,
        "overall_rate": overall_rate,
        "mention_count": total_hits,
        "total_answers": total_answers,
        "rate_scope": "category_recommended",
        "rank_weights": None,          # No weighting since 2026-09-23; key kept for old report compatibility
        "per_engine_detail": {e: {"valid": st["valid"], "slots": slots, "category_answers": st["total"],
                                  "category_hits": st["hit"], "category_weighted": round(st["weighted"], 2),
                                  "brand_query_answers": st["brand_total"], "brand_query_hits": st["brand_hit"],
                                  "insufficient": e in insufficient}
                              for e, st in per_engine.items()},
        "insufficient_engines": insufficient,
        "brand_query_rate": round(brand_hit / brand_total * 100, 1) if brand_total else None,
        "brand_query_answers": brand_total,
    }



def _build_metrics_block(*, all_results, scene_map, brand, brand_terms, url,
                         rank_matrix, category: str = "",
                         prior_rows: Optional[List[Dict[str, Any]]] = None,
                         ) -> tuple[Dict[str, Any], List[str]]:
    """Aggregate one batch of sampled results into six metrics (eleven counters -> report center formulas).

    Rank / negative judgment / competitor mentions all come from one LLM batch annotation; when the LLM is unavailable rank falls back to
    regex, competitors fall back to rule extraction, and the neutral-positive rate is explicitly nulled (must not show as 100%).

    Returns (metrics block, deduplicated list of competitor names annotated by the LLM) -- the second item serves as the competitor ranking allowlist,
    since regex extraction treats category terms (steel structure / after-sales service / glass shaft) as competitors when the industry changes.

    category (the industry term entered by the user) must be passed down to annotation: when the allowlist only checks "is it a proper company name",
    well-known companies from adjacent tracks pass the gate -- in the Juxing Media report, #2 on the ranking was Hua & Hua, a brand
    strategy consultancy, and #4 was an e-commerce operations agency, neither a media marketing peer.
    """
    from geo.services import full_diagnosis_metrics as fm

    _domain = urlparse(url).netloc.replace("www.", "")
    hit_fn = fm.make_hit_fn(brand, brand_terms, _domain)
    cited_fn, owned_domains = fm.make_cited_fn(url, brand_terms)
    rows: List[fm.MetricRow] = []
    answers: List[str] = []
    for r in all_results:
        _scene = (scene_map or {}).get(r.query, "") or "unknown"
        row = fm.MetricRow(
            query=r.query, engine=r.engine,
            scene=_scene,
            error=r.error or None,
            exposure_only=is_exposure_only(_scene),
        )
        if not r.error:
            # First record "did it appear at all"; whether it counts toward exposure rate is decided by mention_type below
            row.mentioned = hit_fn(r.answer)
            row.cited = cited_fn(getattr(r, "citations", None) or [])
        rows.append(row)
        answers.append(r.answer or "")

    # Reuse cells already judged without calling the LLM again (2026-09-28).
    # Judgment temperature is already 0, but the gateway is still non-deterministic: recomputing the same batch three times gave
    # 46.0% / 46.0% / 46.7%, flipping the few borderline cells between "incidental mention <-> recommended".
    # Report regeneration, metric definition tweaks and periodic re-checks should not move the numbers on their own -- only truly new cells are judged.
    prior: Dict[tuple, Dict[str, Any]] = {}
    for x in prior_rows or []:
        if isinstance(x, dict) and x.get("engine") and x.get("query") is not None:
            prior[(x["engine"], x["query"])] = x
    reused = 0
    for row in rows:
        p = prior.get((row.engine, row.query))
        if not p or row.error:
            continue
        mt = str(p.get("mention_type") or "").strip().lower()
        row.mention_type = mt if mt in ("recommended", "passing", "none") else None
        row.rank = p.get("rank") if isinstance(p.get("rank"), int) else None
        st = p.get("stance")
        row.stance = st if st in ("negative", "neutral", "positive") else None
        row.competitors = [str(c).strip() for c in (p.get("competitors") or []) if str(c).strip()]
        row.hit = bool(p.get("hit"))
        row._reused = True          # noqa: SLF001 -- local marker, not persisted
        reused += 1
    if reused:
        log.info("full-diagnosis reused existing judgments for %d/%d cells, calling LLM only for new cells", reused, len(rows))

    # Annotation input is **all valid rows**: competitors are densest exactly in non-hit rows; feeding only hit rows would shrink the share-of-voice denominator
    pairs = [(i, rows[i], answers[i]) for i in range(len(rows))
             if not rows[i].error and not getattr(rows[i], "_reused", False)]
    judged: Dict[int, dict] = {}
    ok_batches = total_batches = 0
    judge_model = ""
    try:
        from geo.services.full_diagnosis_generator import _llm_provider
        _p, judge_model, _k = _llm_provider()
        judged, ok_batches, total_batches = fm.judge_rows(brand, pairs, category)
    except Exception as e:  # noqa: BLE001
        log.warning("full diagnosis judge unavailable: %s", e)

    for i, row, answer in pairs:
        it = judged.get(i)
        if it:
            # Tightened hit: only being recommended / listed counts as exposure.
            # The old metric definition counted "brand name appears in the answer", so incidental listings like "on the market there are A, B, Acme Cloud..."
            # also counted as exposure, making the numbers systematically optimistic.
            mt = str(it.get("mention_type") or "").strip().lower()
            row.mention_type = mt if mt in ("recommended", "passing", "none") else None
            rank = it.get("brand_rank")
            try:
                row.rank = int(rank) if rank is not None else None
            except (TypeError, ValueError):
                row.rank = None
            st = it.get("stance")
            row.stance = st if st in ("negative", "neutral", "positive") else None
            comps = it.get("competitors")
            row.competitors = [str(c).strip() for c in comps if str(c).strip()] \
                if isinstance(comps, list) else []
        # Decision chain: the model decides -> when the model did not label it, fall back to "appeared and has a rank" (meaning it appeared in a list)
        if row.mention_type is not None:
            row.hit = row.mentioned and row.mention_type == "recommended"
        else:
            row.hit = row.mentioned and (
                row.rank is not None or fm.regex_rank(answer, brand) is not None)
        if row.rank is None and row.hit:
            row.rank = fm.regex_rank(answer, brand)
        if row.rank is not None and row.query in rank_matrix:
            rank_matrix[row.query][row.engine] = row.rank

    if total_batches == 0 and reused:
        # All cells reused existing judgments -- no LLM batch does not mean judgment failed,
        # otherwise one recompute would null out neutral-positive rate/rank (missing this made report #146's neutral-positive rate None).
        accuracy_status, rank_source, mention_source = "ok", "llm", "llm"
    elif total_batches == 0:
        accuracy_status, rank_source, mention_source = "unavailable", "regex", "regex"
    elif ok_batches == total_batches:
        accuracy_status, rank_source, mention_source = "ok", "llm", "llm"
    elif ok_batches == 0:
        accuracy_status, rank_source, mention_source = "unavailable", "regex", "regex"
    else:
        accuracy_status, rank_source, mention_source = "partial", "mixed", "mixed"

    comp_names: List[str] = []
    seen_comp = set()
    for row in rows:
        for c in row.competitors or []:
            k = c.strip().lower()
            if k and k not in seen_comp:
                seen_comp.add(k)
                comp_names.append(c.strip())

    neg_scope = sum(1 for r in rows if r.hit and r.stance is not None)
    metrics = fm.build_metrics(
        rows=rows, brand=brand, owned_domains=owned_domains,
        accuracy_ok=accuracy_status in ("ok", "partial"),
        rank_source=rank_source, mention_source=mention_source,
        accuracy_status=accuracy_status, neg_scope_count=neg_scope,
        judge_model=judge_model,
        hit_terms=[t for t in ([brand] + list(brand_terms or [])) if t],
    )
    return metrics, comp_names


def diag_call_policy(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Get the execution policy for this one-click diagnosis call, merged into the /run-now payload.

    Diagnosis wants api + immediate (no queue, no browser, see want_exec_mode),
    but **the tier in call_policies has the final say** -- operators can override by switching the channel's tier to
    auto/queue, no code change. When the policy table is not set up (migration not run), fall back to the original behavior:
    send no policy fields, and the batch run center treats it as any.
    """
    try:
        from geo.database import SessionLocal
        from geo.services.call_policy import CallerRef, check

        db = SessionLocal()
        try:
            d = check(db, CallerRef(channel="web", account_id=user_id, user_id=user_id),
                      "run_now", endpoint="full_diagnosis",
                      want_run_mode="immediate", want_exec_mode="api")
        finally:
            db.close()
        if not d.allowed:
            log.warning("full-diagnosis denied by policy (%s), continuing with default tier: %s", d.policy.key, d.reason)
            return {}
        return {"run_mode": d.run_mode, "exec_mode": d.exec_mode,
                "priority": d.priority, "policy_key": d.policy.key,
                "channel": "web", "account_id": user_id, "user_id": user_id}
    except Exception as e:  # noqa: BLE001 -- a policy resolution failure must never take down the diagnosis
        log.warning("full-diagnosis policy resolution failed, continuing with default tier: %s", e)
        return {}


def run_engine_probe(
    url: str,
    brand_name: str = "",
    competitor_domains: Optional[List[str]] = None,
    queries: Optional[List[str]] = None,
    brand_terms: Optional[List[str]] = None,
    scene_map: Optional[Dict[str, str]] = None,
    user_id: Optional[int] = None,
    industry_terms: Optional[List[str]] = None,
    prior_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One run-now on the batch run center; the same batch of results produces two blocks:

    - visibility: per-engine brand mention rate (hit = brand name or any brand term appears in the answer),
      total score = overall mention rate. Domestic engines only -- the old geo_checker visibility mixed in overseas engines
      (Perplexity/ChatGPT/Claude are always 0% on Chinese questions), dragging the score down and misleading the display.
    - competitive: competitive intel produced by the four analyzers (same shape as competitive_intel).

    Failure semantics match the advanced_runners convention: batch run center unreachable / no results from any engine
    -> RuntimeError (upper layer marks skipped)."""
    from geo_checker.modes.competitive_intel import (
        _build_queries, _EngineResult, _make_citation,
    )
    from geo_checker.io import get_soup
    from geo_checker.analyzers.source_trace import analyze_source_trace
    from geo_checker.analyzers.source_preference import analyze_source_preference
    from geo_checker.analyzers.competitor_insight import analyze_competitor_insight

    # Website is optional: when url is empty, base_url / domain stay empty, and downstream follows the "no own domain" metric definition
    url = (url or "").strip()
    if url:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
            parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        domain = parsed.netloc.replace("www.", "")
    else:
        base_url = ""
        domain = ""
    brand = (brand_name or "").strip() or (domain.split(".")[0] if domain else "")
    if not brand:
        raise RuntimeError("品牌名与官网地址均为空,无法判定命中")

    # Category hint: same source as the old mode (first segment of the first 60 chars of the meta description)
    category = ""
    if base_url:
        try:
            _resp, soup = get_soup(base_url)
            if soup:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    desc = meta_desc["content"].strip()
                    if len(desc) > 10:
                        category = desc[:60].split(",")[0].split("，")[0].strip()
        except Exception:  # noqa: BLE001
            pass

    if queries:
        # Queries generated from user terms (industry-term ranking + brand-term reputation), not built-in templates
        query_texts = list(queries)
    else:
        built = _build_queries(brand, domain, category, competitor_domains)
        query_texts = [q["query"] for q in built]
    engines = _engines()

    start = time.time()
    try:
        payload: Dict[str, Any] = {"name": f"full-diagnosis:{brand}",
                                   "queries": query_texts, "engines": engines}
        payload.update(diag_call_policy(user_id))
        rows = post_run_now(
            TELEMETRY_SERVICE_URL, payload,
            timeout=_timeout(), label="full-diagnosis",
        )
    except httpx.HTTPError as e:
        raise RuntimeError(f"跑批中心不可达({TELEMETRY_SERVICE_URL}):{e}")
    if not isinstance(rows, list):
        raise RuntimeError(f"跑批中心返回形状异常:{str(rows)[:120]}")

    alias = _engine_alias()
    all_results: List[_EngineResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        citations = []
        for i, c in enumerate(row.get("citations") or []):
            if not isinstance(c, dict):
                continue
            c_url = (c.get("url") or "").strip()
            if not c_url and c.get("domain"):
                c_url = f"https://{c['domain']}"
            if not c_url:
                continue
            citations.append(_make_citation(
                c_url, title=c.get("title") or "",
                snippet=c.get("snippet") or "",
                position=c.get("position") or (i + 1),
            ))
        engine_key = str(row.get("engine") or "")
        all_results.append(_EngineResult(
            engine=alias.get(engine_key, engine_key),
            query=str(row.get("query") or ""),
            answer=row.get("answer") or "",
            citations=citations,
            error=row.get("error"),
        ))

    valid = [r for r in all_results if not r.error]
    if not valid:
        errs = {r.error for r in all_results if r.error}
        raise RuntimeError(f"跑批中心 {len(engines)} 个引擎均无有效结果:{'; '.join(list(errs)[:3])}")

    elapsed = time.time() - start
    log.info("full-diagnosis competitive via telemetry: %d/%d valid in %.0fs",
             len(valid), len(all_results), elapsed)

    # Question x engine hit/rank matrix (for the per-item detail table): {query: {engine: ...}}
    hit_matrix: Dict[str, Dict[str, bool]] = {q: {} for q in query_texts}
    rank_matrix: Dict[str, Dict[str, Any]] = {q: {} for q in query_texts}

    # -- Six metrics: aggregate eleven counters from the same batch of samples --
    # Must come before the competitor ranking -- this step's LLM batch annotation also produces a clean competitor list,
    # which the competitor ranking uses as an allowlist to block category terms extracted by regex
    metrics_block = None
    llm_competitors: List[str] = []
    try:
        metrics_block, llm_competitors = _build_metrics_block(
            all_results=all_results, scene_map=scene_map, brand=brand,
            brand_terms=brand_terms, url=base_url, rank_matrix=rank_matrix,
            prior_rows=prior_rows,
            # For the track, prefer the user-entered industry term (more accurate than the category guessed from site meta)
            category="、".join([t for t in (industry_terms or []) if str(t).strip()]) or category,
        )
    except Exception as e:  # noqa: BLE001
        # A metrics aggregation failure must not take down the whole visibility block -- the frontend six-metrics section takes the "not collected" path
        log.warning("full diagnosis metrics build failed: %s", e)

    # Hit terms: an empty string must never get in -- `"" in answer` is always True and would push exposure rate to 100%
    match_terms = [t.strip().lower() for t in ([brand] + (brand_terms or [])) if t and t.strip()]
    if domain:
        match_terms.append(domain.lower())

    # Competitor mention rate is only computed within the category metric definition: leaving giveaway questions in the denominator would inflate the own brand and deflate competitors,
    # reversing the ranking conclusion. The criterion is shared with visibility and the six metrics via is_exposure_only (the single copy).
    def _in_category(r) -> bool:
        return not is_exposure_only((scene_map or {}).get(r.query, ""))

    source_trace = analyze_source_trace(all_results, target_domain=domain)
    source_pref = analyze_source_preference(all_results, target_domain=domain)
    competitor = analyze_competitor_insight(
        all_results,
        target_domain=domain,
        target_brand=brand,
        competitor_domains=competitor_domains,
        allowed_names=llm_competitors,
        rate_scope=_in_category,
    )
    brand_ranking = None
    try:
        from geo_checker.analyzers.brand_ranking import aggregate_rankings
        brand_ranking = aggregate_rankings(all_results, brand)
    except Exception as e:  # noqa: BLE001
        log.warning("brand ranking aggregation failed: %s", e)

    engines_used = sorted(set(r.engine for r in all_results if not r.error))
    # -- Engines that produced no data must leave a trace, not silently drop a column --
    # Both kinds of absence have been seen (2026-09-22 reports #138 / #140):
    #   · the batch run center never returned this engine -- the execution tier filtered it out (Wenxin/Yuanbao long-standing)
    #   · returned but the whole column is error -- the Doubao ARK endpoint is always 429
    # The old metric definition computed engines_used only by "are there error-free rows", so the report simply lost a column with no frontend notice,
    # and readers could not tell "this platform did not mention me" from "this platform never ran".
    returned = {r.engine for r in all_results}
    failed_engines = []
    for eng in engines:
        if eng in engines_used:
            continue
        tried = [r for r in all_results if r.engine == eng]
        errs = [r.error for r in tried if r.error]
        failed_engines.append({
            "engine": eng,
            "attempted": len(tried),
            "reason": "filtered" if eng not in returned else "all_failed",
            "error": (errs[0][:200] if errs else
                      "跑批中心未返回该引擎(执行档位可能已将其筛除)"),
        })
    if failed_engines:
        log.warning("full-diagnosis %d engines produced no data: %s", len(failed_engines),
                    "; ".join(f"{f['engine']}({f['reason']})" for f in failed_engines))
    competitive = {
        "url": base_url,
        "domain": domain,
        "brand": brand,
        "engines": engines_used,
        "query_count": len(query_texts),
        "total_results": len(all_results),
        "valid_results": len(valid),
        "elapsed_seconds": round(elapsed, 1),
        "failed_engines": failed_engines,
        "source_trace": source_trace,
        "source_preference": source_pref,
        "competitor_insight": competitor,
        "brand_ranking": brand_ranking,
    }

    # -- visibility block --
    def _mentioned(answer: str) -> bool:
        low = (answer or "").lower()
        return any(t in low for t in match_terms)

    vis_rates = compute_visibility_rates(valid, query_texts=query_texts, scene_map=scene_map,
                                         match_terms=match_terms, rank_matrix=rank_matrix,
                                         engines_used=engines_used, hit_matrix=hit_matrix, mentioned=_mentioned)
    per_engine_rates = vis_rates["per_engine_rates"]
    overall_rate = vis_rates["overall_rate"]
    total_hits = vis_rates["mention_count"]
    total_answers = vis_rates["total_answers"]
    rate_scope = vis_rates["rate_scope"]
    # -- Unified metric definition (2026-09-23) --
    # Chapter 05's headline number and chapter 06's "overall exposure rate" must be **the same number**: same source, same denominator,
    # same hit judgment. The metrics layer is authoritative -- its hit is the LLM judging "recommended/listed", more accurate than substring matching
    # (report #138: "厨房食品品牌推荐有哪些" ("which kitchen food brands are recommended") only mentioned competitors, the substring layer misjudged it as a hit).
    # Rank is no longer folded into this number; it is expressed by the adjacent TOP1 / TOP3 columns -- folding it in would break
    # "TOP1 <= TOP3 <= overall exposure rate", which must hold when shown side by side.
    # When the LLM judgment fails for the whole batch, metrics_block is None, falling back to the old substring + rank-weighted algorithm as-is.
    if metrics_block:
        _ov = (metrics_block.get("overall") or {}).get("metrics") or {}
        _oc = (metrics_block.get("overall") or {}).get("counts") or {}
        if _ov.get("exposure") is not None:
            overall_rate = _ov["exposure"]
            per_engine_rates = {row["engine"]: row["metrics"]["exposure"]
                                for row in (metrics_block.get("by_engine") or [])}
            total_hits = int(_oc.get("cat_hit_count") or 0)
            total_answers = int(_oc.get("cat_valid_count") or 0)
            rate_scope = "category_recommended"
            # Sync the per-question hit matrix to the same judgment, otherwise the detail table and headline number disagree
            for _r in (metrics_block.get("rows") or []):
                if _r.get("query") in hit_matrix:
                    hit_matrix[_r["query"]][_r["engine"]] = bool(_r.get("hit"))
    visibility = {
        "url": base_url,
        "brand": brand,
        "engines": engines_used,
        "query_count": len(query_texts),
        "queries": query_texts,
        "per_engine_rates": per_engine_rates,
        "overall_rate": overall_rate,
        "mention_count": total_hits,
        "total_answers": total_answers,
        # Metric definition notes (since 2026-09-15): category questions, rank weighting, engines with insufficient data (see compute_visibility_rates)
        "rate_scope": rate_scope,
        **{k: vis_rates[k] for k in ("rank_weights", "per_engine_detail",
                                     "insufficient_engines", "brand_query_rate", "brand_query_answers")},
        # [{engine, attempted, reason: filtered|all_failed, error}] -- the frontend uses it to show "not returned this time"
        "failed_engines": failed_engines,
        # [{query, scene, hits: {engine: bool}, ranks: {engine: int|null}}] -- keeps the original query order
        "query_hits": [{"query": q, "scene": (scene_map or {}).get(q, ""),
                        "hits": hit_matrix.get(q) or {},
                        "ranks": rank_matrix.get(q) or {}} for q in query_texts],
        "metrics": metrics_block,
        # total_score for direct use by the four-dimension scoring (= overall mention rate)
        "total_score": round(overall_rate),
        "top_competitors": [
            # A Chinese competitor's domain is a pseudo-domain built from its name (e.g. 康力电梯.com, "Kangli Elevator"); prefer the real name for display
            {"domain": c.get("name") or c.get("domain") or "",
             "mentions": c.get("mention_count") or 0}
            for c in (competitor.get("competitors") or [])[:8]
        ],
    }

    # Align the "own brand mention rate" in the competitor ranking with chapter 05's overall exposure rate -- in one report the same brand
    # cannot carry two exposure numbers (report #146: 46.7% above, 52.0% below; the 8-cell difference is "mentioned but not
    # judged recommended", and readers would just assume one of them is wrong).
    # ⚠ Competitors still use the substring metric definition (LLM recommendation judgments for each would need a dozen times more annotation), so the own brand column
    #   is stricter than competitors -- the own brand's rank on the ranking will be conservative; a known trade-off.
    if competitor and visibility.get("overall_rate") is not None:
        competitor["your_rate_mention"] = competitor.get("your_rate")   # Original substring metric definition kept for the record
        competitor["your_rate"] = visibility["overall_rate"]
        competitor["your_rate_scope"] = "recommended_only"
        competitive["competitor_insight"] = competitor

    return {"visibility": visibility, "competitive": competitive}


def run_engine_probe_cached(
    url: str,
    brand_name: str = "",
    competitor_domains: Optional[List[str]] = None,
    queries: Optional[List[str]] = None,
    brand_terms: Optional[List[str]] = None,
    scene_map: Optional[Dict[str, str]] = None,
    user_id: Optional[int] = None,
    industry_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Entry point with 24h cache + silenced stdout (same wrapper as each runner in advanced_runners).

    Returns {"visibility": {...}, "competitive": {...}} -- both blocks share one run-now."""
    from geo.services.advanced_runners import _cached_run, _silent_call

    from geo.services.full_diagnosis_metrics import SPEC_VERSION

    identity = {
        "url": url,
        "brand": (brand_name or "").strip(),
        "competitor_domains": sorted(competitor_domains) if competitor_domains else None,
        "queries": queries or None,
        "brand_terms": sorted(brand_terms) if brand_terms else None,
        "engines": _engines(),
        # Term-type annotation and metric algorithm versions go into the key: changed annotation or metric definition must rerun,
        # otherwise the 24h cache would return results in the old shape directly
        "scene_map": scene_map or None,
        "spec": SPEC_VERSION,
        # Competitor extraction metric definition version: changing the allowlist/noise gate/mention rate denominator must bypass the 24h cache,
        # otherwise the old competitor ranking is returned as-is
        # v6: competitor judgment adds a "same track" criterion, industry term goes into the prompt -- a changed criterion must rerun
        # v7: the LLM list also goes through the noise gate (the category term "明星代言" ("celebrity endorsement") once topped the ranking at 31%)
        "comp_extract": "v7-lane-and-noise",
        # Engine routing version: from v2 Doubao goes through SearchPro + gateway synthesis (ARK always 429), and Wenxin/Yuanbao are no longer
        # filtered out at the api tier. Routing changed but the engines list did not; without it in the key, a rerun within 24h would return
        # the old "deepseek/qwen only" result as-is (2026-09-22 report #140 had exactly this shape).
        "engine_routes": "v2-doubao-search+wenxin-yuanbao-sync",
        "industry_terms": sorted(industry_terms) if industry_terms else None,
    }
    return _cached_run(
        "engine-probe",
        identity,
        lambda: _silent_call(
            run_engine_probe, url,
            brand_name=brand_name, competitor_domains=competitor_domains,
            queries=queries, brand_terms=brand_terms, scene_map=scene_map,
            industry_terms=industry_terms,
        ),
    )
