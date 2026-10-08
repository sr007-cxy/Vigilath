"""Which card to issue next — pure function, unit-testable; no services, no model calls."""
from __future__ import annotations

from agents.sales_agent.flow.state import SLOT_ORDER, LeadState

READY = "ready"
CUSTOM_INDUSTRY = "custom_industry"
BRAND = "brand"


def next_step(state: LeadState) -> str:
    """Compute the next step from slots. Returns `ready` when all are filled.

    Difference from the old step chain: there "what the previous step was" determined the next; here "what's still missing" does —
    so when one sentence gives all the target conditions, the intermediate cards are skipped automatically.
    """
    # The brand comes before all slots: capabilities/ideal customers/size are attributes of **a particular brand**,
    # and only after asking which brand do we know whose profile to prefill from. `brand_required` is injected by the caller per
    # "active brands ≥ 2" — always False for single-brand accounts, so the whole branch is skipped, zero regression.
    if state.brand_required and not state.brand_id:
        return BRAND
    if state.custom_industry_pending and not state.target_industries:
        return CUSTOM_INDUSTRY
    for slot in SLOT_ORDER:
        if not state.filled(slot):
            return slot
    return READY


def should_rerun(state: LeadState, changed: list[str]) -> bool:
    """Conditions already complete and this turn changed a target slot -> rerun the recommendation rather than ask again."""
    return next_step(state) == READY and any(c.startswith("target_") or c == "requested_limit" for c in changed)
