"""Spending caps for paid media placement -- per order / per batch / per day.

**Why it is needed**: paid orders no longer have a second confirmation (if the balance is enough, it goes out). The confirmation dialog used to double as
the safeguard of "the user saw the amount with their own eyes"; with it removed and no spending cap at all, a single prompt injection,
a script bug or a misclick could drain the whole wallet in seconds, and paid placements **cannot be undone**.

A cap is not a confirmation; it asks the user nothing; it only bounds "the worst case" at a tolerable number.
Defaults are fairly generous (¥200 per order / ¥1000 per batch / ¥2000 per day); when something goes wrong, lower the env values
to tighten immediately, no release needed.

**The criterion is net spend**, not accumulated ledger entries -- money charged then refunded does not count as spent (see
balance_service.spent_today_cents), otherwise clicking a failing order twice would eat the day's quota for nothing.

⚠️ Per-order and per-day checks must live in `_publish_one`, not the endpoint: both the single and batch entry points
   pass through it, and putting them in endpoints would inevitably miss one. The batch total can only live in the endpoint (a single item cannot see the whole batch).
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.orm import Session

from geo.models.balance import KIND_PUBLISH_MEDIA
from geo.services import balance_service

# 0 = unlimited
_DEFAULTS = {
    "PAID_PUBLISH_MAX_ORDER_CENTS": 20000,     # Single placement task
    "PAID_PUBLISH_MAX_BATCH_CENTS": 100000,    # Total of one batch
    "PAID_PUBLISH_DAILY_CENTS": 200000,        # Net spend per user per day
}


def _limit(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, _DEFAULTS[name])))
    except (TypeError, ValueError):
        return _DEFAULTS[name]


def _yuan(cents: int) -> str:
    return f"{cents / 100:.2f}"


def check_order(db: Session, user_id: int, amount_cents: int,
                *, via: Optional[str] = None) -> Optional[dict]:
    """Per order + per day. Returns None if within limits, else a structured rejection (the caller translates it into 402)."""
    amount_cents = int(amount_cents or 0)

    cap = _limit("PAID_PUBLISH_MAX_ORDER_CENTS")
    if cap and amount_cents > cap:
        return {
            "code": "ORDER_LIMIT_EXCEEDED",
            "message": f"单笔投放上限 ¥{_yuan(cap)},这家需要 ¥{_yuan(amount_cents)}。"
                       f"如需投放更贵的媒体,请联系我们调整上限。",
            "limit_cents": cap,
            "amount_cents": amount_cents,
        }

    daily = _limit("PAID_PUBLISH_DAILY_CENTS")
    if daily:
        spent = balance_service.spent_today_cents(
            db, user_id, kind=KIND_PUBLISH_MEDIA, via=via)
        if spent + amount_cents > daily:
            return {
                "code": "DAILY_LIMIT_EXCEEDED",
                "message": f"今日投放已用 ¥{_yuan(spent)},单日上限 ¥{_yuan(daily)}。"
                           f"本单 ¥{_yuan(amount_cents)} 会超出,明天可继续。",
                "limit_cents": daily,
                "spent_today_cents": spent,
                "amount_cents": amount_cents,
            }
    return None


def check_batch(db: Session, user_id: int, total_cents: int, count: int,
                *, via: Optional[str] = None) -> Optional[dict]:
    """Total cap for one batch. Per-order and per-day are still enforced per item by check_order."""
    cap = _limit("PAID_PUBLISH_MAX_BATCH_CENTS")
    if cap and int(total_cents or 0) > cap:
        return {
            "code": "BATCH_LIMIT_EXCEEDED",
            "message": f"一次批量投放上限 ¥{_yuan(cap)},本次 {count} 家合计 "
                       f"¥{_yuan(int(total_cents))}。请分批提交。",
            "limit_cents": cap,
            "amount_cents": int(total_cents or 0),
            "count": count,
        }
    return None
