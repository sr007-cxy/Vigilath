"""Quota tracking & enforcement.

Two enforcement paths:
- Logged-in users: monthly quota comes from their Membership tier
  (`monthly_check_quota`, where 0 means unlimited).
- Anonymous callers: a fixed `ANONYMOUS_MONTHLY_QUOTA` per browser per
  calendar month, keyed by an opaque cookie (`geo_anon_id`) the backend sets
  on the first anonymous request. Easy to bypass by clearing cookies, but
  good enough to keep casual visitors honest.
"""
from datetime import datetime
from typing import Tuple

from geo.database import SessionLocal
from geo.models.membership import (
    AnonymousCheckUsageORM,
    Membership,
    UserCheckUsageORM,
)
from geo.utils.error_handler import AppException


# Free / unregistered limit per source IP per month. Matches the registered
# free tier (3 checks/月) so unregistered visitors aren't strictly better off
# than registered free users.
ANONYMOUS_MONTHLY_QUOTA = 3


def _current_year_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def get_usage(user_id: int, membership: Membership) -> Tuple[int, int, int, str]:
    """Return (quota, used, remaining, year_month). remaining = -1 for unlimited."""
    year_month = _current_year_month()
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
        return quota, used, remaining, year_month
    finally:
        db.close()


def check_and_increment_quota(user_id: int, membership: Membership) -> None:
    """Enforce the monthly quota for the given user / membership.

    Raises 429 AppException when exceeded. quota == 0 means unlimited and is
    always allowed.
    """
    quota = membership.monthly_check_quota or 0
    year_month = _current_year_month()
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
        if row is None:
            row = UserCheckUsageORM(user_id=user_id, year_month=year_month, used_count=1)
            db.add(row)
        else:
            row.used_count = used + 1
        db.commit()
    finally:
        db.close()


def get_anonymous_usage(client_id: str) -> Tuple[int, int, int, str]:
    """Return (quota, used, remaining, year_month) for an anonymous client."""
    year_month = _current_year_month()
    db = SessionLocal()
    try:
        row = (
            db.query(AnonymousCheckUsageORM)
            .filter(
                AnonymousCheckUsageORM.client_id == client_id,
                AnonymousCheckUsageORM.year_month == year_month,
            )
            .first()
        )
        used = row.used_count if row else 0
        quota = ANONYMOUS_MONTHLY_QUOTA
        remaining = max(quota - used, 0)
        return quota, used, remaining, year_month
    finally:
        db.close()


def check_and_increment_anonymous_quota(client_id: str) -> None:
    """Enforce the per-cookie monthly quota for anonymous callers.

    Raises 429 AppException when exceeded. The caller is responsible for
    minting / passing through the cookie value — quota_service only stores
    and counts.
    """
    if not client_id:
        # Defensive: an empty key would let everyone share one bucket, which
        # is worse than failing closed. The endpoint should always pass a
        # generated UUID, so reaching this branch is a programming error.
        raise AppException(
            status_code=500,
            message="anonymous quota called without client_id",
        )
    year_month = _current_year_month()
    db = SessionLocal()
    try:
        row = (
            db.query(AnonymousCheckUsageORM)
            .filter(
                AnonymousCheckUsageORM.client_id == client_id,
                AnonymousCheckUsageORM.year_month == year_month,
            )
            .first()
        )
        used = row.used_count if row else 0
        if used >= ANONYMOUS_MONTHLY_QUOTA:
            raise AppException(
                status_code=429,
                message=(
                    f"Anonymous monthly quota exceeded "
                    f"({used}/{ANONYMOUS_MONTHLY_QUOTA}). "
                    f"Please sign up or log in to continue."
                ),
            )
        if row is None:
            row = AnonymousCheckUsageORM(
                client_id=client_id,
                year_month=year_month,
                used_count=1,
            )
            db.add(row)
        else:
            row.used_count = used + 1
        db.commit()
    finally:
        db.close()
