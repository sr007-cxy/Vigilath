"""The slot set of the find-customers flow.

Coexists in lockstep with the old `context_json["step"]`: `from_flow` reads the old structure and `to_flow` writes it back,
so both share the source. Delete the step field once it has no readers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Slot order is the question order; `router.next_step` depends on it
SLOT_ORDER = (
    "seller_capabilities",
    "service_scope",
    "typical_customers",
    "target_company_size",
    "target_industries",
    "target_regions",
)

_LIST_SLOTS = {"seller_capabilities", "service_scope", "business_hint", "typical_customers", "target_industries", "target_regions"}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class LeadState:
    """All slots of one find-customers task. Pure data, no DB."""

    seller_capabilities: list[str] = field(default_factory=list)
    service_scope: list[str] = field(default_factory=list)
    seller_team: str = ""
    typical_customers: list[str] = field(default_factory=list)
    target_company_size: str = ""
    target_industries: list[str] = field(default_factory=list)
    target_regions: list[str] = field(default_factory=list)
    requested_limit: int = 0
    # With an empty profile, first collect a minimal business direction, only as context for AI candidate generation, not a recommendation filter slot.
    business_hint: list[str] = field(default_factory=list)
    # The user clicked 「自定义行业」 ("custom industry") but hasn't entered a specific industry yet
    custom_industry_pending: bool = False
    # Which brand to recommend for this time. The brand is **a precondition, not a slot** — mixing it into SLOT_ORDER would muddy
    # the semantics of `missing()` / `to_flow()` (the brand isn't "a filter condition for this customer search").
    brand_id: int = 0
    # Injected by the caller per "active brands ≥ 2"; the router doesn't query the DB. Always False with only one brand,
    # so next_step bypasses the brand branch entirely — zero regression for single-brand accounts.
    brand_required: bool = False

    @classmethod
    def from_flow(cls, flow: dict[str, Any]) -> "LeadState":
        return cls(
            brand_id=_as_int(flow.get("brand_id")),
            seller_capabilities=_as_list(flow.get("seller_capabilities")),
            service_scope=_as_list(flow.get("service_scope")),
            seller_team=str(flow.get("seller_team") or ""),
            typical_customers=_as_list(flow.get("typical_customers")),
            target_company_size=str(flow.get("target_company_size") or ""),
            target_industries=_as_list(flow.get("target_industries")),
            target_regions=_as_list(flow.get("target_regions")),
            requested_limit=int(flow.get("requested_limit") or 0),
            business_hint=_as_list(flow.get("business_hint")),
            custom_industry_pending=str(flow.get("step") or "") == "custom_industry",
        )

    def to_flow(self, flow: dict[str, Any]) -> None:
        """Write back the old structure. Only slots with values are written — empty values don't overwrite, avoiding wiping saved profiles."""
        for slot in SLOT_ORDER:
            value = getattr(self, slot)
            if value:
                flow[slot] = value
        if self.seller_team:
            flow["seller_team"] = self.seller_team
        if self.requested_limit:
            flow["requested_limit"] = self.requested_limit
        if self.business_hint:
            flow["business_hint"] = self.business_hint
        # Stored into context_json -> the session remembers, and later turns no longer ask "which brand to recommend for this time"
        if self.brand_id:
            flow["brand_id"] = self.brand_id

    def filled(self, slot: str) -> bool:
        return bool(getattr(self, slot))

    def missing(self) -> list[str]:
        return [s for s in SLOT_ORDER if not self.filled(s)]

    def apply(self, updates: dict[str, Any]) -> list[str]:
        """Write the extracted slots in, returning the names of slots that really changed.

        Overwrites filled values — "change the region to East China" must be able to change it; this is the biggest behavioral difference from the old step chain.
        """
        changed: list[str] = []
        for slot, value in updates.items():
            if not hasattr(self, slot) or not value:
                continue
            new = _as_list(value) if slot in _LIST_SLOTS else value
            if getattr(self, slot) != new:
                setattr(self, slot, new)
                changed.append(slot)
        return changed
