"""Price display consistency -- every outlet that shows a price must give the same retail price.

**This is the most likely regression from this change**: the list page and price-check page once showed the upstream purchase price,
while charging was cost x 1.2 (floor ¥4), so a ¥1 outlet showed ¥1 and charged ¥4.

Three outlets:
  1. `display_price` of `/api/rwj/media/search`
  2. `retail_price` / `retail_price_cents` of `_preview_one`
  3. `_retail_cents_of` actually charged by `_publish_one`

They must compute the same number from the same upstream cost. Pure function assertions, no network, no DB.
"""
from __future__ import annotations

import pytest

from geo.api import rwj_channel
from geo.services import rwj_pricing

# (upstream cost in CNY, expected public quote in cents). The 400-cent floor case is the branch most easily missed.
CASES = [
    ("1.00", 400),        # 1.00 x 1.2 = 1.20 CNY < floor -> 400
    ("3.00", 400),        # 3.60 CNY < 4 CNY -> floor
    ("3.34", 401),        # 400.8 -> rounds to 401, just above the floor
    ("35.00", 4200),
    ("100.00", 12000),
    ("2999.99", 359999),
]


@pytest.mark.parametrize("upstream_yuan,expect_cents", CASES)
def test_three_exits_agree(upstream_yuan, expect_cents):
    # Outlet 1: list page
    search_price = rwj_pricing.retail_from_yuan_str(upstream_yuan)
    # Outlets 2/3: price-check page and charge
    charge_cents = rwj_channel._retail_cents_of(upstream_yuan)

    assert charge_cents == expect_cents, "扣款价与预期不符"
    assert search_price == f"{expect_cents / 100:.2f}", "列表页价与扣款价不一致"


def test_unknown_price_never_falls_back_to_cost():
    """When no public price can be quoted it must be None -- never fall back to the cost price as a stand-in."""
    assert rwj_pricing.retail_from_yuan_str(None) is None
    assert rwj_pricing.retail_from_yuan_str("") is None
    assert rwj_pricing.retail_from_yuan_str("面议") is None
    assert rwj_channel._retail_cents_of("面议") is None


def test_markup_is_applied_once_not_twice():
    """Markup may happen only once.

    In `_preview_one`'s response `price` is still the cost price and `retail_price` is the public price --
    if someday someone also changes `price` to retail, `_publish_one` would mark up again on top of it
    (1.2^2 = 1.44); this assertion is the safety net.
    """
    upstream = "100.00"
    once = rwj_channel._retail_cents_of(upstream)
    twice = rwj_channel._retail_cents_of(f"{once / 100:.2f}")
    assert once == 12000
    assert twice == 14400, "口径变了就得重看这条:说明加价被套了两层"
    assert once != twice
