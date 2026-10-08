"""Public quotes for paid media -- **the single source of markup across the whole site**.

Upstream (Ruanwenjiang / Meijieku) gives cost prices; public quote = cost x markup rate, with a floor.
Two settings:

- `RWJ_PRICE_MARKUP`     markup rate, default 1.2 (i.e. +20%)
- `RWJ_PRICE_MIN_CENTS`  quote floor, default 400 cents (4 CNY). Cheap media are still a few dimes after markup,
                         while labor and risk-control cost per order is far more, so there's a floor.

★ Every place that shows a price must go through here. With a second price source, sooner or later "the price shown" and
  "the money charged" won't match -- the most trust-damaging kind of bug.
"""
from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional


def markup() -> float:
    try:
        return float(os.environ.get("RWJ_PRICE_MARKUP") or 1.2)
    except ValueError:
        return 1.2


def min_cents() -> int:
    try:
        return int(os.environ.get("RWJ_PRICE_MIN_CENTS") or 400)
    except ValueError:
        return 400


def retail_cents(upstream_cents: Optional[int]) -> Optional[int]:
    """Cost price (cents) -> public quote (cents). None in, None out (an unknown price can't be made up).

    Round up to the cent: better to charge one cent more than lose money to rounding.
    """
    if upstream_cents is None:
        return None
    if upstream_cents < 0:
        return None
    marked = (Decimal(upstream_cents) * Decimal(str(markup()))).to_integral_value(rounding=ROUND_HALF_UP)
    return max(int(marked), min_cents())


def retail_yuan(upstream_cents: Optional[int]) -> Optional[float]:
    """Public quote in CNY. For frontend display."""
    c = retail_cents(upstream_cents)
    return None if c is None else round(c / 100, 2)


def retail_from_yuan_str(upstream_yuan: str | float | None) -> Optional[str]:
    """Entry point when upstream gives CNY (string, e.g. "35.00") -- the order preview path uses this.

    Still converts to cents before marking up, to keep floats away from money.
    """
    if upstream_yuan is None or str(upstream_yuan).strip() == "":
        return None
    try:
        cents = int((Decimal(str(upstream_yuan)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except Exception:  # noqa: BLE001
        return None
    r = retail_cents(cents)
    return None if r is None else f"{r / 100:.2f}"
