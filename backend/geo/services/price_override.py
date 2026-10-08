"""Centralized management of two kinds of price overrides: CN site RMB pricing + QA test account discounts.

**CN site pricing** (`get_cn_price_cny`): tier prices are stored in USD in the `memberships` table,
and the CN site and global site **share the same database** (verified: identical system_identifier), so
changing the table would also change .com -- the CN site's RMB prices can only be overridden in code. This table is the shared source of the CN site's
"displayed price" and "amount actually charged via WeChat"; change prices only here.

Slugs not in the table fall back to conversion at `settings.USD_TO_CNY_RATE`, so new tiers never miss a price.

**QA test account discount** (`get_test_price_override`):
One whitelist of test emails, shared by every payment gateway (Stripe,
WeChat, future). Lets us run real end-to-end checkout flows without burning
real money, and keeps the backdoor visible in one place.

Fixed amounts, not a lookup table — simplest thing that works:
- USD → 60 cents ($0.60, above Stripe's $0.50 minimum)
- CNY → 100 fen (¥1.00)

Other currencies are not overridden; callers see None and fall back to the
membership's configured price.
"""
from typing import Optional


# Test accounts that get the minimum-viable price on any payment method.
# Lowercased; caller's email is lowercased before lookup.
_TEST_EMAILS = {
    "guotielong@hotmail.com",
}


# RMB pricing per tier for the CN site (REGION=cn), in yuan.
# Unrelated to the USD prices in the DB -- the CN site has independent pricing, not the result of a currency conversion.
# Both the displayed price and the amount charged via WeChat read from here, guaranteeing "what you see = what you pay".
_CN_PRICE_CNY = {
    "pro": 39.9,
    "starter": 299.0,
    "growth": 999.0,
}


def get_cn_price_cny(slug: Optional[str]) -> Optional[float]:
    """RMB price (yuan) of this tier on the CN site; returns None when not separately priced, for the caller to convert at the exchange rate."""
    if not slug:
        return None
    return _CN_PRICE_CNY.get(slug)


def get_test_price_override(email: Optional[str], currency: str) -> Optional[int]:
    """Return override amount in the smallest unit of `currency` (cents for
    USD, fen for CNY), or None if this caller/currency shouldn't be overridden.
    """
    if not email or email.lower() not in _TEST_EMAILS:
        return None
    c = (currency or "").lower()
    if c == "usd":
        return 60
    if c == "cny":
        return 100
    return None
