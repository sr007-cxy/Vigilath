"""Extract slots from a sentence.

Only words in the word lists are recognized — if unrecognized, leave empty and fall back to the old per-step parsing. No model calls:
when clicking cards the user sends the raw option text, and free input mostly contains industry/region words, so rules suffice and are deterministically testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_LIMIT_RE = re.compile(r"(\d+)\s*家")


@dataclass(frozen=True)
class SlotCatalog:
    """Candidate word lists for each slot. Injected by the caller so flow/ doesn't depend back on service/."""

    industries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    company_sizes: tuple[str, ...] = ()


def _match(message: str, options: tuple[str, ...]) -> list[str]:
    """Words from the word list that appear in the sentence. Longer words first — 「口腔医疗」 ("dental care") shouldn't be grabbed by 「医疗健康」 ("healthcare")."""
    hits = [opt for opt in sorted(options, key=len, reverse=True) if opt and opt in message]
    return [h for i, h in enumerate(hits) if not any(h in longer for longer in hits[:i])]


def extract_slots(message: str, catalog: SlotCatalog) -> dict[str, Any]:
    """Return slots that can be recognized with certainty. Unrecognized keys are absent, so the caller can tell whether anything was extracted."""
    text = str(message or "").strip()
    if not text:
        return {}

    found: dict[str, Any] = {}

    industries = _match(text, catalog.industries)
    if industries:
        found["target_industries"] = ["不限行业"] if "不限行业" in industries else industries

    regions = _match(text, catalog.regions)
    if regions:
        found["target_regions"] = ["不限地区"] if "不限地区" in regions else regions

    sizes = _match(text, catalog.company_sizes)
    if sizes:
        found["target_company_size"] = "、".join(sizes)

    limit = _LIMIT_RE.search(text)
    if limit:
        try:
            value = int(limit.group(1))
        except ValueError:
            value = 0
        if value > 0:
            found["requested_limit"] = value

    return found
