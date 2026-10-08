"""Absorb what the tools **actually did** this turn into slots. Pure function: input is a sequence of (tool_name, content).

Why read tool returns rather than what the model said: the model paraphrases, rewrites and misremembers brands
(on 2026-08-27 it mixed <brand-c>'s 5/8 progress with <brand-a>'s in the same window).
Tool returns are **facts that happened**, and slots only trust facts.
"""
from __future__ import annotations

from typing import Any, Iterable

from agents.geo.flow.router import ASKING, DONE, RUNNING
from agents.geo.flow.state import DiagnosisState


def absorb(state: DiagnosisState, tool_returns: Iterable[tuple[str, Any]]) -> bool:
    """Returns True if slots changed; the caller uses it to decide whether to persist to DB."""
    dirty = False
    for name, content in tool_returns:
        if not isinstance(content, dict):
            continue

        if name == "run_full_diagnosis":
            brand = str(content.get("brand") or "").strip()
            # Changing brand = changing task: first clear the previous run's industry terms / report_id,
            # otherwise "run XX again for me" would run with the previous brand's terms and order id.
            if brand and state.brand and brand != state.brand:
                state.reset_task()
                dirty = True
            if brand and brand != state.brand:
                state.brand = brand
                dirty = True

            status = str(content.get("status") or "")
            if status == "need_input":
                # The diagnosis **was not started**. The slots stay at "waiting for the user to provide terms", so the business line is locked to geo.
                if state.stage != ASKING:
                    state.stage = ASKING
                    dirty = True
            elif content.get("report_id"):
                dirty |= bool(state.merge({
                    "industry_terms": content.get("industry_terms") or state.industry_terms,
                    "report_id": content.get("report_id"),
                }))
                if state.stage != RUNNING:
                    state.stage = RUNNING
                    dirty = True

        elif name == "get_full_diagnosis":
            rid = content.get("report_id")
            # Only trust the status of **this run**. Finding another run doesn't change the slots — that's exactly the source of
            # "taking another brand's progress as this brand's".
            if not rid or (state.report_id and int(rid) != int(state.report_id)):
                continue
            status = str(content.get("status") or "")
            if status in ("ready", "failed") and state.stage != DONE:
                state.stage = DONE
                dirty = True
            elif status == "generating" and state.stage != RUNNING:
                state.report_id = int(rid)
                state.stage = RUNNING
                dirty = True

    return dirty
