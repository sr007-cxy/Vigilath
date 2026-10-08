"""The publish plan's "scheduling basis" -- keeps the judgments made at scheduling time so the plan can justify why it is laid out this way.

## Why this module exists

Scheduling actually computes a lot of basis: which channels AI cites most, converting shares into articles per day, which channels' shares
were too low and dropped as long tail, which articles the paid cap moved to own channels, whether a row's template came from a platform lookup hit or
rotation fallback... **but all of this used to be discarded after computing**, leaving only a "Zhihu / Zhihu long-form" conclusion. Operators could not reconcile,
and customers could not see what justified the plan's layout.

## Where it is stored (zero migration)

Both are existing free-form JSON columns, no new columns, no migration -- `overview_json.start_options`
already paved this way:

- **Plan level** -> the `plan_basis` key of `plan.overview_json` (see the structure in `build()`)
- **Row level**  -> the `why` key of each row in `publishing_plan_json` (see `row_why()`)

## Three rules

1. **Do not touch `note`**: that is the manually editable remark (the frontend row edit/bulk edit both change it); machine reasons must
   use a separate key, otherwise they overwrite each other.
2. **Size**: row-level `why` <= 1KB per row -- never stuff full domain lists or full candidate resource tables. A 150-row monthly plan
   adds about +60KB, acceptable.
3. **Degrade on manual edits**: when operators change platform/template/seed/question in the editor, the corresponding `why` must
   become `manual` (recording the machine's original suggestion). Otherwise it shows "scheduled to Zhihu by sources" while it was actually
   changed to Xiaohongshu by a person -- worse than no explanation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)

BASIS_VERSION = 1
# How many past reschedules to keep in the plan-level snapshot (one-line summaries only, for "how this plan became what it is today")
HISTORY_MAX = 5
# Max representative domains recorded per platform in row-level why (size rule)
WHY_DOMAINS_MAX = 3

# Machine fields that must be passed through verbatim on whole-table PUT -- the frontend does not send them back, and allowlist reconstruction would erase them
MACHINE_ROW_KEYS = ("why", "origin")


# -- Plan level -------------------------------------------------------------------

def crop_quota(quota: Optional[dict]) -> Optional[dict]:
    """Trim build_platform_quota's return value into a snapshot suitable for long-term retention.

    The three things dropped are either derivable or already stored elsewhere:
    - `slots`: derivable from breakdown
    - `slot_plan`: per-slot candidate resources are already in each row's `why.media`
    - `measured[].media`: the media list is "what can be placed now", changing daily with resource library syncs;
      keeping a stale copy would mislead -- live queries in the panel are more accurate
    """
    if not isinstance(quota, dict):
        return None
    measured = []
    for row in (quota.get("measured") or []):
        if not isinstance(row, dict):
            continue
        slim = {k: v for k, v in row.items() if k != "media"}
        doms = slim.get("domains")
        if isinstance(doms, list):
            slim["domains"] = doms[:WHY_DOMAINS_MAX]
        measured.append(slim)
    breakdown = []
    for row in (quota.get("breakdown") or []):
        if not isinstance(row, dict):
            continue
        slim = dict(row)
        doms = slim.get("domains")
        if isinstance(doms, list):
            slim["domains"] = doms[:WHY_DOMAINS_MAX]
        breakdown.append(slim)
    return {
        "measured": measured,
        "breakdown": breakdown,
        "unavailable": quota.get("unavailable") or [],
        "excluded": quota.get("excluded") or [],
        "paid_cap_moves": quota.get("paid_cap_moves") or [],
        "cold_start": bool(quota.get("cold_start")),
        "cost": quota.get("cost"),
        "notes": quota.get("notes") or [],
        "notes_struct": quota.get("notes_struct") or [],
        "thresholds": quota.get("thresholds") or {},
        "available": quota.get("available") or [],
        "paid_only": quota.get("paid_only") or [],
        "profile_sites": (quota.get("profile_sites") or [])[:10],
        "unmapped": (quota.get("unmapped") or [])[:10],
        "responses_total": quota.get("responses_total"),
    }


def crop_strategy(strategy: Optional[dict]) -> Optional[dict]:
    """Placement strategy snapshot -- keep only conclusions, drop recomputable details.

    Two things dropped:
    - `diagnosis.engines[].top_domains`: the domain ranking changes daily; live queries in the panel are more accurate
    - intermediates outside `cadence[].items`: the cadence table itself is the conclusion

    What remains is "which strategy it was scheduled by": engine priority (with weights, which the quota is weighted by),
    per-engine placement checklists, content forms, cadence, knowledge base version. If the knowledge base changes later, old plans' strategies
    still make sense -- which is why kb_version must be kept.
    """
    if not isinstance(strategy, dict):
        return None
    diag = strategy.get("diagnosis") or {}
    engines = []
    for row in (diag.get("engines") or []):
        if isinstance(row, dict):
            engines.append({k: v for k, v in row.items() if k != "top_domains"})
    return {
        "v": strategy.get("v"),
        "kb_version": strategy.get("kb_version"),
        "computed_at": strategy.get("computed_at"),
        "industry": strategy.get("industry") or {},
        "diagnosis": {
            "responses_total": diag.get("responses_total"),
            "own_cited_responses": diag.get("own_cited_responses"),
            "engines": engines,
        },
        "engine_priority": strategy.get("engine_priority") or [],
        "platform_plan": strategy.get("platform_plan") or [],
        "gaps": strategy.get("gaps") or [],
        "content_forms": strategy.get("content_forms") or [],
        "cadence": strategy.get("cadence") or [],
        "cold_start_order": strategy.get("cold_start_order") or [],
        "notes": strategy.get("notes") or [],
    }


def _headline(quota: Optional[dict]) -> str:
    """One-line summary for the "past reschedules" list: 知乎2 · 搜狐号1 · 官网博客2 (Zhihu 2 · Sohu 1 · website blog 2)"""
    rows = (quota or {}).get("breakdown") or []
    parts = [f"{r.get('platform')}{r.get('slots')}" for r in rows
             if isinstance(r, dict) and r.get("slots")]
    return " · ".join(parts) if parts else "按模板默认平台"


def build(*, trigger: str, actor_id: Optional[int] = None,
          options: Optional[dict] = None,
          quota: Optional[dict] = None,
          templates: Optional[list] = None,
          seed_source: Optional[dict] = None,
          strategy: Optional[dict] = None,
          prev: Optional[dict] = None) -> dict:
    """Assemble the plan-level basis snapshot.

    trigger: start (project start) / generate_items (reschedule by options) / refill (monthly renewal)
             / lazy_seed (lazy generation after the baseline run)
    strategy: placement strategy (only with platform_mode=strategy) -- it is upstream of the quota,
             stored in the same snapshot as the quota so the frontend can connect "strategy decided this -> plan scheduled this"
    prev:    the previous snapshot, used to carry over "past reschedules"
    """
    cropped = crop_quota(quota)
    history: list[dict] = []
    if isinstance(prev, dict):
        history = [h for h in (prev.get("history") or []) if isinstance(h, dict)]
        if prev.get("built_at"):
            history = [{
                "built_at": prev.get("built_at"),
                "trigger": prev.get("trigger"),
                "headline": _headline(prev.get("quota")),
            }] + history
    return {
        "v": BASIS_VERSION,
        "built_at": datetime.utcnow().isoformat(),
        "trigger": trigger,
        "actor_id": actor_id,
        "options": options or {},
        "seed_source": seed_source or {},
        "templates": templates or [],
        "quota": cropped,
        "strategy": crop_strategy(strategy),
        "history": history[:HISTORY_MAX],
    }


def persist(plan, basis: dict) -> None:
    """Write into the plan_basis key of plan.overview_json (merge, leaving the rest of overview untouched)."""
    try:
        overview = json.loads(plan.overview_json or "{}")
    except Exception:  # noqa: BLE001
        overview = {}
    if not isinstance(overview, dict):
        overview = {}
    overview["plan_basis"] = basis
    plan.overview_json = json.dumps(overview, ensure_ascii=False)


def load(plan) -> Optional[dict]:
    """Read back the plan-level basis; old plans / parse failures return None (the frontend uses degraded copy)."""
    try:
        overview = json.loads(plan.overview_json or "{}")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(overview, dict):
        return None
    basis = overview.get("plan_basis")
    return basis if isinstance(basis, dict) else None


def templates_digest(templates) -> list[dict]:
    """Template pool summary -- keeps only id/name/kind/applicable platforms, enough to answer "what was in the template pool then"."""
    out: list[dict] = []
    for t in (templates or []):
        try:
            plats = json.loads(getattr(t, "target_platforms_json", None) or "[]")
        except Exception:  # noqa: BLE001
            plats = []
        out.append({
            "id": getattr(t, "id", None),
            "name": getattr(t, "name", "") or "",
            "kind": getattr(t, "kind", "") or "",
            "platforms": [str(p) for p in plats if p] if isinstance(plats, list) else [],
        })
    return out


# -- Row level --------------------------------------------------------------------

def row_why(*, platform: Optional[dict] = None, template: Optional[dict] = None,
            combo: Optional[dict] = None, queries: Optional[dict] = None,
            seed: Optional[dict] = None, media: Optional[dict] = None) -> dict:
    """Assemble row-level why; empty sub-blocks are omitted, saving size and saving the frontend empty checks."""
    why: dict[str, Any] = {"v": BASIS_VERSION}
    for key, val in (("platform", platform), ("template", template), ("combo", combo),
                     ("queries", queries), ("seed", seed), ("media", media)):
        if val:
            why[key] = val
    return why


# Which row field changed -> which why sub-block is invalidated. Once a human edits, the machine reason no longer holds.
_FIELD_TO_WHY = {
    "platform": "platform",
    "template_id": "template",
    "seed": "seed",
    "queries": "queries",
    "media": "media",
}


def merge_machine_keys(new_row: dict, old_row: Optional[dict]) -> None:
    """On whole-table PUT, pass machine fields from old rows to new rows, and degrade manually edited parts to "manually specified".

    Without an old row (newly added) do nothing -- rows added by the frontend have no machine basis anyway.
    """
    if not isinstance(old_row, dict):
        return
    for key in MACHINE_ROW_KEYS:
        if key in old_row and old_row[key] is not None:
            new_row[key] = old_row[key]

    why = new_row.get("why")
    if not isinstance(why, dict):
        return
    why = json.loads(json.dumps(why))       # Deep copy, so the copy referenced by the old row is not modified
    for field, why_key in _FIELD_TO_WHY.items():
        before, after = old_row.get(field), new_row.get(field)
        if isinstance(before, list) or isinstance(after, list):
            changed = list(before or []) != list(after or [])
        else:
            changed = (before or None) != (after or None)
        if changed and why_key in why:
            prev = why[why_key]
            why[why_key] = {
                "reason": "manual",
                # Record what the machine originally suggested, so the frontend can say "at scheduling time the machine suggested Zhihu"
                "prev_reason": prev.get("reason") if isinstance(prev, dict) else None,
                "prev_value": before if not isinstance(before, list) else list(before),
            }
    new_row["why"] = why
