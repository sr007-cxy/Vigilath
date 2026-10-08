"""Quota tracking & enforcement.

Logged-in users only: monthly quota comes from their Membership tier
(`monthly_check_quota`, where 0 means unlimited). Anonymous callers are
unlimited by design — see `docs/02-需求分析/计费与支付/用户权益处理方案.md` §3.1.

Two gates, **both must pass**:

1. **Monthly quota**: tier entitlement, `monthly_check_quota` (0 = unlimited). Pay more, run more.
2. **Daily cap**: uniform 10/day across all three tiers; it's a **capacity guardrail, not an entitlement** -- audits consume the egress-limited
   engine pool (deepseek 25 egresses x 10/day = 250/day platform-wide); monthly quota can't stop
   "burning a month's quota in one day". So growth's "unlimited monthly runs" is really <= 300/month,
   and external copy must use this metric definition.

Daily buckets use the **Beijing day** (same metric definition as balance_ledger.bj_date); monthly buckets keep the historical UTC month,
unchanged -- changing it would misalign existing rows, and an 8-hour month boundary difference has no real effect on quotas.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

from geo.database import SessionLocal
from geo.models.membership import (
    Membership, UserCheckUsageDailyORM, UserCheckUsageORM,
)
from geo.utils.error_handler import AppException

_BJ = timezone(timedelta(hours=8))

# Daily cap. Uniform across three tiers, a capacity guardrail. When volume grows, adjust this env; no release needed.
DEFAULT_DAILY_CAP = 10


def daily_cap() -> int:
    """Max basic audits per day. <=0 means unlimited (for emergency gate opening)."""
    try:
        return int(os.environ.get("GEO_DAILY_CHECK_CAP", DEFAULT_DAILY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP


def _current_year_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _bj_date() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def get_usage(user_id: int, membership: Membership) -> Tuple[int, int, int, str]:
    """Return (quota, used, remaining, year_month). remaining = -1 for unlimited."""
    quota, used, remaining, year_month, _d = get_usage_full(user_id, membership)
    return quota, used, remaining, year_month


def get_usage_full(user_id: int, membership: Membership):
    """Monthly usage + daily usage. Returns (quota, used, remaining, year_month, daily).

    daily = {"cap": int, "used": int, "remaining": int, "bj_date": str}; cap 0 = unlimited.
    """
    year_month = _current_year_month()
    day = _bj_date()
    db = SessionLocal()
    try:
        row = (
            db.query(UserCheckUsageORM)
            .filter(
                UserCheckUsageORM.user_id == user_id,
                UserCheckUsageORM.year_month == year_month,
            )
            .first()
        )
        used = row.used_count if row else 0
        quota = membership.monthly_check_quota or 0
        remaining = -1 if quota == 0 else max(quota - used, 0)

        drow = (
            db.query(UserCheckUsageDailyORM)
            .filter(
                UserCheckUsageDailyORM.user_id == user_id,
                UserCheckUsageDailyORM.bj_date == day,
            )
            .first()
        )
        dused = drow.used_count if drow else 0
        cap = daily_cap()
        daily = {"cap": max(0, cap), "used": dused, "bj_date": day,
                 "remaining": -1 if cap <= 0 else max(cap - dused, 0)}
        return quota, used, remaining, year_month, daily
    finally:
        db.close()


def check_and_increment_quota(user_id: int, membership: Membership) -> None:
    """Enforce the monthly quota **and** the flat daily cap for this user.

    Raises 429 AppException when either is exceeded. Monthly quota == 0 means unlimited monthly runs,
    but **the daily cap still applies** -- it's a capacity guardrail that doesn't open up with the tier.

    Both counters increment together in one commit: incrementing only one causes "daily rolled but monthly didn't" bookkeeping errors.
    """
    quota = membership.monthly_check_quota or 0
    cap = daily_cap()
    year_month = _current_year_month()
    day = _bj_date()
    db = SessionLocal()
    try:
        row = (
            db.query(UserCheckUsageORM)
            .filter(
                UserCheckUsageORM.user_id == user_id,
                UserCheckUsageORM.year_month == year_month,
            )
            .first()
        )
        used = row.used_count if row else 0
        if quota != 0 and used >= quota:
            raise AppException(
                status_code=429,
                message=f"Monthly check quota exceeded ({used}/{quota}). Upgrade to continue.",
            )

        drow = (
            db.query(UserCheckUsageDailyORM)
            .filter(
                UserCheckUsageDailyORM.user_id == user_id,
                UserCheckUsageDailyORM.bj_date == day,
            )
            .first()
        )
        dused = drow.used_count if drow else 0
        if cap > 0 and dused >= cap:
            raise AppException(
                status_code=429,
                message=f"今日检测次数已用完({dused}/{cap} 次/天),明天 00:00 重置。",
            )

        if row is None:
            db.add(UserCheckUsageORM(user_id=user_id, year_month=year_month, used_count=1))
        else:
            row.used_count = used + 1
        if drow is None:
            db.add(UserCheckUsageDailyORM(user_id=user_id, bj_date=day, used_count=1))
        else:
            drow.used_count = dused + 1
        db.commit()
    finally:
        db.close()
