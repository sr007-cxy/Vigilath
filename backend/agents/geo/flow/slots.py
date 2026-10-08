"""Extract slots from a message.

Only recognize forms that can be identified **with certainty**; if unrecognized, leave empty — let the model handle it the old way. No model calls:
when clicking a card the user sends a sentence we built ourselves, and free input is mostly like 「行业词:X Y」 ("industry terms: X Y"),
so rules suffice and are deterministically testable (same trade-off as agents/sales_agent/flow/slots.py).
"""
from __future__ import annotations

import re
from typing import Any

# Card backfill format. IndustryTermsAsk in frontend/src/components/AgentChat/cards.tsx
# sends exactly `行业词:X Y Z` when 「用这些词开始诊断」 ("start diagnosis with these terms") is clicked — **both frontend and backend must recognize this sentence**,
# and changing either side requires changing the other at the same time.
_TERMS_RE = re.compile(r"(?:行业词|品类词|行业|品类)\s*[::]\s*(.+)")
_BRAND_RE = re.compile(r"(?:品牌|品牌名)\s*[::]\s*(.+)")

# Separators between terms: spaces / Chinese and English commas and enumeration commas / slashes and pipes
_SPLIT_RE = re.compile(r"[\s,,、;;/|]+")


def _split_terms(raw: str, limit: int = 5) -> list[str]:
    out, seen = [], set()
    for t in _SPLIT_RE.split(raw.strip()):
        t = t.strip().strip("「」\"'")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def extract(message: str) -> dict[str, Any]:
    """Return slots that can be identified with certainty. Unrecognized keys are absent, so the caller can tell whether anything was extracted."""
    text = str(message or "").strip()
    if not text:
        return {}
    found: dict[str, Any] = {}

    m = _TERMS_RE.search(text)
    if m:
        terms = _split_terms(m.group(1))
        if terms:
            found["industry_terms"] = terms

    m = _BRAND_RE.search(text)
    if m:
        brand = _split_terms(m.group(1), limit=1)
        if brand:
            found["brand"] = brand[0]

    return found
