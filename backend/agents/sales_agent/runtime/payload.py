"""Tool return trimming — cards need everything, the model only needs a summary.

The original gap: `recommend_companies`' entire return value was fed back verbatim into the model context. Measured on the same seed pool:
recommending 20 companies was 510k characters, recommending 100 was 1.13M characters, where `ranking` is a full copy of `leads`,
`discovery` echoes all 130 seeds, and `campaign.seed_companies` echoes them again —
the `leads` actually needed were only 46k. That's how the chat scene's 60s wall clock was exhausted in production on 2026-08-26
(the tool itself ran in only 0.4s; all the time went to feeding this blob to the model).

Two views of the same result:

- `card_payload`: for frontend cards and history messages; pure duplicates removed, leads / tables / question cards all kept.
- `model_view`: for the model, keeping only status, counts and match degree — the sentence the model needs to say doesn't need the evidence chain or
  company details; the list is rendered by the card.
"""
from __future__ import annotations

from typing import Any

# Fields the model really needs for this turn's reply. An allowlist rather than a blocklist: when the workflow adds fields later,
# by default they won't quietly leak back into the model context.
_MODEL_KEYS = (
    # Status and follow-ups
    "status", "needs_user_input", "message", "next_question", "missing_items",
    "verification_problems",
    # Counts
    "requested_limit", "actual_count", "count", "shortfall", "mode",
    "final_count", "initial_count", "report_matched_count",
    # Discovery capability
    "discovery_mode", "search_available", "missing_config", "can_expand", "blocked",
    # Match degree with the user's selected conditions — the model uses it to decide whether to state truthfully "not filtered by the conditions"
    "filter_applied", "requested_industries", "requested_regions",
    "industry_match_count", "region_match_count", "website_count",
)

# Duplicate blocks dropped from the card view.
_CARD_DROP_KEYS = ("ranking", "evidence_summary")

_MODEL_NOTE = "完整企业名单已经以卡片形式展示给用户,正文只写一句概述,不要逐条重复,也不要在正文里贴企业表格。"


def _question_card_stub(data: Any) -> dict[str, Any]:
    """Keep only the few fields the model needs to know "the frontend has rendered a question card"."""
    if not isinstance(data, dict):
        return {}
    return {k: data.get(k) for k in ("id", "field", "title") if data.get(k)}


def card_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Frontend card view: remove blocks duplicating the top level; leads / tables / question cards kept as-is.

    `ranking` duplicates the top-level leads/count/table verbatim (the frontend `recommendationData` reads
    `data.ranking.leads` first and falls back to the top level otherwise, so it renders fine without it); `discovery.companies`
    is a full echo of the 130 seeds; `campaign.seed_companies` is a third copy of the same seeds.
    """
    if not isinstance(result, dict):
        return {}
    payload = {k: v for k, v in result.items() if k not in _CARD_DROP_KEYS}
    discovery = payload.get("discovery")
    if isinstance(discovery, dict):
        payload["discovery"] = {k: v for k, v in discovery.items() if k not in ("companies", "campaign")}
    for key in ("campaign", "ranking"):
        block = payload.get(key)
        if isinstance(block, dict) and "seed_companies" in block:
            payload[key] = {k: v for k, v in block.items() if k != "seed_companies"}
    return payload


def model_view(result: dict[str, Any]) -> dict[str, Any]:
    """Model view: only allowlisted fields. Company details, evidence chains, seed pools and tables are never passed back.

    `markdown_table` is legacy output from before the a2ui company card launched; its three columns (company / website / public contact info)
    are a strict subset of the card. Passing it back only makes the model paste the table into the body, duplicating the card;
    the copy in `card_payload` stays, since the deterministic fallback reply (api.py::_fallback_output_from_cards) still uses it.
    """
    if not isinstance(result, dict):
        return {}
    view: dict[str, Any] = {k: result[k] for k in _MODEL_KEYS if k in result}
    question_card = _question_card_stub(result.get("question_card"))
    if question_card:
        view["question_card"] = question_card
    if result.get("leads"):
        view["note"] = _MODEL_NOTE
    return view
