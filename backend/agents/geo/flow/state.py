"""All slots of one diagnosis task. Pure data, no DB.

Mirrors LeadState in agents/sales_agent/flow/state.py.
Stored in the `diagnosis` sub-object of agent_conversations.context_json
(one session row may hold other flows too, so each flow gets its own key rather than being flattened).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FLOW_KEY = "diagnosis"

# Slot order is the question order; router.next_step depends on it
SLOT_ORDER = ("brand", "industry_terms")

_LIST_SLOTS = {"industry_terms", "brand_terms", "competitors"}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _as_str(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class DiagnosisState:
    """Everything known about "running one full diagnosis for a brand"."""

    brand: str = ""
    industry_terms: list[str] = field(default_factory=list)
    url: str = ""
    brand_terms: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    # The run already started. If present, stop asking for slots and check progress instead.
    report_id: int = 0
    # "" not started | "asking" waiting for user to fill slots | "running" started and running | "done" report produced
    stage: str = ""

    # ── Serialization ─────────────────────────────────────────────────────
    @classmethod
    def from_ctx(cls, ctx: dict[str, Any] | None) -> "DiagnosisState":
        flow = (ctx or {}).get(FLOW_KEY) or {}
        if not isinstance(flow, dict):
            flow = {}
        try:
            report_id = int(flow.get("report_id") or 0)
        except (TypeError, ValueError):
            report_id = 0
        return cls(
            brand=_as_str(flow.get("brand")),
            industry_terms=_as_list(flow.get("industry_terms"))[:5],
            url=_as_str(flow.get("url")),
            brand_terms=_as_list(flow.get("brand_terms"))[:5],
            competitors=_as_list(flow.get("competitors"))[:5],
            report_id=report_id,
            stage=_as_str(flow.get("stage")),
        )

    def to_ctx(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Write back to context_json. **Only write slots with values** — empty values don't overwrite, so stored ones aren't wiped.

        (Same as to_flow in sales_agent/flow/state.py: slots not mentioned in a turn shouldn't be cleared.)
        """
        flow = ctx.get(FLOW_KEY)
        if not isinstance(flow, dict):
            flow = {}
        for name in ("brand", "url", "stage"):
            v = getattr(self, name)
            if v:
                flow[name] = v
        for name in ("industry_terms", "brand_terms", "competitors"):
            v = getattr(self, name)
            if v:
                flow[name] = v
        if self.report_id:
            flow["report_id"] = self.report_id
        ctx[FLOW_KEY] = flow
        return ctx

    # ── Criteria ──────────────────────────────────────────────────────────
    def filled(self, slot: str) -> bool:
        return bool(getattr(self, slot, None))

    def missing(self) -> list[str]:
        return [s for s in SLOT_ORDER if not self.filled(s)]

    def merge(self, found: dict[str, Any]) -> list[str]:
        """Merge in extracted slots and return the names of slots that **really changed this turn**.

        List slots are **replaced as a whole**, not appended: when the user corrects "no, it's beef and mutton",
        appending would leave the old terms in, and the whole run would still query the AI with the wrong terms.
        """
        changed: list[str] = []
        for k, v in (found or {}).items():
            if not hasattr(self, k):
                continue
            new = _as_list(v)[:5] if k in _LIST_SLOTS else (
                int(v or 0) if k == "report_id" else _as_str(v))
            if not new:
                continue
            if new != getattr(self, k):
                setattr(self, k, new)
                changed.append(k)
        return changed

    def reset_task(self) -> None:
        """Change brand / start a new run: clear traces of the previous run; don't carry the old report_id into the new task."""
        self.industry_terms = []
        self.brand_terms = []
        self.competitors = []
        self.url = ""
        self.report_id = 0
        self.stage = ""
