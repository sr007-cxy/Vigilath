"""Card contract — which tools' return values are pushed as structured cards, and how to fish them out of a run.

Two reasons for a separate file, both learned the hard way:

★ **There can be only one CARD_TOOLS.** `embed/api.py` once had its own copy that missed `run_full_diagnosis`,
  so the external channel fell back to "the model paraphrasing a dict in text" when the industry term was missing — exactly what the pending card is meant to eliminate.
  A copy = divergence sooner or later, so both channels import the same constant.

★ **Extraction only looks at this turn.** See the comment on `turn_messages`.
"""
from __future__ import annotations

# Returns of these "data tools" are pushed to the frontend as structured cards for rendering.
# New cards must also be added to CARD_SPECS in frontend/src/components/AgentChat/cards.tsx,
# otherwise the backend pushes them and the frontend can't recognize them, as if never pushed.
#
# list_report_periods is not a card: it's a multi-period list that the existing two-column "label / value" card structure can't hold;
# let the model render it as a Markdown table (the chat window already has remark-gfm).
CARD_TOOLS = frozenset({
    # run_full_diagnosis is here so the "missing industry term" pending card can be rendered as a clickable form
    # — otherwise the model can only paraphrase in text and falls back to guessing the industry itself.
    "run_full_diagnosis",
    "get_full_diagnosis", "get_report_overview", "get_period_report",
    "get_query_coverage", "get_publish_status", "get_run_status",
    # Ops console (admin line): a few with fixed structure and high information density; textual paraphrase by the model would drop fields.
    # External channels can't reach these tools (build_embed_agent forces line=admin to geo),
    # so sharing one full set is safe — if a tool isn't called, there's no matching tool-return.
    "admin_topic_overview", "admin_get_plan", "admin_list_plan_items",
    "admin_source_insights", "admin_placement_strategy", "admin_list_publish_tasks",
})


def turn_messages(result):
    """Messages **newly produced** in this turn.

    ★ Never use `result.all_messages()`. It includes `message_history` (up to 50 messages on the self line),
      so cards / control_page directives / confirmation cards from dozens of past turns get **re-pushed every turn**,
      and the frontend executes them all: the page keeps navigating, old cards flood, old confirmation cards pop up again.
      The "messy stuff kept showing up" the user reported in the 2026-08-27 <brand-a> session was this.

    `new_messages()` is pydantic-ai's standard API; the `hasattr` fallback is so version drift reverts
    to the old behavior instead of crashing (consistent with agents/sales_agent/api.py).
    """
    fn = getattr(result, "new_messages", None)
    return fn() if callable(fn) else result.all_messages()


def tool_returns_of_turn(result):
    """All tool-return parts of this turn, yielded as (tool_name, content).

    flow/absorb.py also consumes this sequence — slots only trust facts actually returned by tools, not the model's paraphrase.
    """
    for m in turn_messages(result):
        for p in getattr(m, "parts", []):
            if getattr(p, "part_kind", "") == "tool-return":
                yield getattr(p, "tool_name", ""), p.content


def cards_from(result) -> list[dict]:
    """Data tool returns -> structured cards. If a tool is called several times only the last is kept."""
    by_tool: dict[str, dict] = {}
    for name, content in tool_returns_of_turn(result):
        if name in CARD_TOOLS:
            by_tool[name] = {"tool": name, "data": content}
    return list(by_tool.values())


def ui_actions_from(result) -> list:
    """control_page execution directives -> the frontend operates the page accordingly (fill forms / adjust filters / navigate / submit).

    This one fears replay the most: if historical navigate/submit directives are re-sent, the page navigates and submits by itself.
    """
    out: list = []
    for name, content in tool_returns_of_turn(result):
        if name == "control_page" and isinstance(content, dict):
            out.extend(content.get("ui_actions") or [])
    return out


def confirms_from(result) -> list[dict]:
    """Two-step confirmation previews -> the frontend renders a highlighted card with 「确认执行 / 取消」 ("Confirm / Cancel").

    **Strips confirm_token**: the model takes the token from its own context and the frontend never touches it;
    clicking 「确认执行」 ("Confirm") = sending a regular message (see the note in api.py).
    """
    out: list[dict] = []
    for _name, content in tool_returns_of_turn(result):
        if isinstance(content, dict) and content.get("needs_confirm"):
            out.append({k: v for k, v in content.items() if k != "confirm_token"})
    return out
