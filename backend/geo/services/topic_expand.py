"""Core fan-out of topic seed words -> 4-dimension scene expansion (extracted from /expand-queries in api/ai_telemetry.py).

Shared by the `/topics/{id}/expand-queries` endpoint and the agent tool (expand_prompts in agents/geo/tools.py);
calls query_expander.expand_one_scene underneath. **Doesn't write to DB**, only returns candidates.

2026-09-22 -- the endpoint now calls this function directly (before, both sides were two copies of the same logic); product lists are also wired in:
  - expansion_fields_from_topic extracts the fields query expansion needs from the topic row + profile_json into one dict,
    producing the same keys as api._parse_suggest_args (the wizard path, before the topic exists)
  - seed hits a product in profile_json.products -> product mode (see the query_expander.SCENE_PROMPTS header comment)
  - after expansion route_brand_words runs as fallback, moving items with brand / product names in category scenes to brand
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from geo.models.ai_telemetry import (
    ALL_SCENES, EXPAND_SCENES, MAX_EXPANSION_CANDIDATES,
    find_product, profile_product_terms, profile_products,
)
from geo.services.query_expander import DEFAULT_TIMEOUT, expand_one_scene, route_brand_words


def expansion_fields_from_topic(topic: Any) -> dict:
    """topic row -> query expansion input dict (without seed / scenes / count). Key set matches api._parse_suggest_args."""
    target = (topic.target or "").strip()
    industry = topic.industry or ""
    try:
        aliases = [str(a).strip() for a in json.loads(topic.target_aliases_json or "[]") if str(a).strip()]
    except Exception:  # noqa: BLE001
        aliases = []

    service_geo = ""
    entity_type = ""
    profile_cases: list[str] = []
    core_credentials: list[str] = []
    brand_diff_tags: list[str] = []
    core_service_overview = ""
    products: list[dict] = []
    try:
        p = json.loads(topic.profile_json or "{}")
        if isinstance(p, dict):
            service_geo = str(p.get("service_geo") or "").strip()[:200]
            entity_type = str(p.get("entity_type") or "").strip()[:32]
            core_service_overview = str(p.get("core_service_overview") or "").strip()[:500]
            profile_cases = [str(s).strip()[:300] for s in (p.get("case_stories") or []) if str(s).strip()]
            core_credentials = [str(s).strip()[:300] for s in (p.get("core_credentials") or []) if str(s).strip()]
            brand_diff_tags = [str(s).strip()[:80] for s in (p.get("brand_diff_tags") or []) if str(s).strip()]
            products = profile_products(p)
    except Exception:  # noqa: BLE001
        pass
    return {
        "target": target, "aliases": aliases, "industry": industry,
        "service_geo": service_geo, "entity_type": entity_type,
        "profile_cases": profile_cases, "core_credentials": core_credentials,
        "brand_diff_tags": brand_diff_tags, "core_service_overview": core_service_overview,
        "products": products,
    }


def matched_product_out(products: list[dict], seed: str) -> dict | None:
    """Summary for the frontend of "which product the seed hit"; no hit -> None."""
    m = find_product(products, seed)
    if not m:
        return None
    return {
        "name": str(m.get("name") or "").strip(),
        "category": str(m.get("category") or "").strip(),
        "is_featured": bool(m.get("is_featured")),
    }


def brand_terms_for_routing(fields: dict) -> list[str]:
    """Brand word surfaces for route_brand_words: target (split on /) + aliases + product names / product aliases."""
    terms = [p.strip() for p in (fields.get("target") or "").split("/") if p.strip()]
    terms += list(fields.get("aliases") or [])
    terms += profile_product_terms({"products": fields.get("products") or []}, industry=fields.get("industry") or "")
    return terms


async def expand_with_fields(
    fields: dict,
    *,
    seed: str,
    scenes: list[str],
    count_per_scene: int,
) -> dict:
    """Fan out one seed using fields (produced by expansion_fields_from_topic / _parse_suggest_args).

    Returns {"seed", "total_count", "scenes": {scene: {"queries":[...], "model", "error"}}, "matched_product"}.
    """
    target = fields.get("target") or ""
    runnable: list[str] = []
    scene_results: dict[str, dict] = {}
    for s in scenes:
        if s == "brand" and not target:
            scene_results[s] = {"queries": [], "error": "brand 场景需要品牌名(target 为空)"}
        else:
            runnable.append(s)

    if runnable:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            tasks = [
                expand_one_scene(
                    scene=s, seed=seed, count=count_per_scene,
                    target=target, aliases=fields.get("aliases") or [],
                    industry=fields.get("industry") or "", service_geo=fields.get("service_geo") or "",
                    entity_type=fields.get("entity_type") or "",
                    profile_cases=fields.get("profile_cases") or [],
                    core_credentials=fields.get("core_credentials") or [],
                    brand_diff_tags=fields.get("brand_diff_tags") or [],
                    core_service_overview=fields.get("core_service_overview") or "",
                    products=fields.get("products") or [],
                    client=client,
                )
                for s in runnable
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for s, res in zip(runnable, results):
            scene_results[s] = {"queries": [], "error": str(res)[:300]} if isinstance(res, Exception) else res

    # Fallback re-tagging only when the brand scene is already part of this fan-out -- if the user didn't tick brand but an extra brand bucket appears,
    # the frontend tabs won't match; in that case items with brand words are dropped (they shouldn't be in category scenes anyway).
    route_brand_words(scene_results, brand_terms_for_routing(fields))
    if "brand" not in scenes:
        scene_results.pop("brand", None)

    total = sum(len((scene_results.get(s) or {}).get("queries") or []) for s in scenes)
    return {
        "seed": seed, "total_count": total, "scenes": scene_results,
        "matched_product": matched_product_out(fields.get("products") or [], seed),
    }


async def expand_topic_seed(
    topic: Any,
    seed: str,
    scenes: list[str] | None = None,
    count_per_scene: int | None = None,
) -> dict:
    """4-dimension (search/qa/intent/brand) expansion of one seed word (input validation + profile fields + fan-out)."""
    seed = (seed or "").strip()
    if not seed:
        raise ValueError("seed 不能为空")

    # Valid values checked against ALL_SCENES; default fan-out uses EXPAND_SCENES (see the comment at the enum)
    scenes = [s for s in (scenes or list(EXPAND_SCENES)) if s in ALL_SCENES] or list(EXPAND_SCENES)
    cap = max(5, MAX_EXPANSION_CANDIDATES // len(EXPAND_SCENES))
    try:
        cps = int(count_per_scene) if count_per_scene else cap
    except (TypeError, ValueError):
        cps = cap
    cps = max(5, min(cps, cap))

    return await expand_with_fields(
        expansion_fields_from_topic(topic), seed=seed, scenes=scenes, count_per_scene=cps,
    )
