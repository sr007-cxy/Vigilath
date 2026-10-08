"""Credits pricing overhaul: new unit prices + daily limit table + tier quotas

Revision ID: f8b3d5c91a20
Revises: e5c2a48d1739
Create Date: 2026-08-25 15:30:00.000000

Corresponds to `docs/02-需求分析/计费与支付/需求文档-积分计价体系改版.md`. Three things:

1. **Repricing** (`balance_prices`). This **deliberately overwrites** existing rows, the opposite of
   d4b1f8c62a97's "existing keys are not overwritten": that was the first seeding and could not reset prices tuned in the admin backend;
   this is a site-wide price adjustment, and old admin values must be replaced. Adds `sentiment_daily`,
   splitting daily monitoring out of the quick-audit price (charging ¥10/run daily = ¥300/month/brand, pricier than the most expensive tier).

2. **`user_check_usage_daily`**: daily limit for basic audits (10/day across all three tiers). The monthly quota is
   a tier entitlement, the daily limit is a capacity guardrail: the engine pool only has so many runs a day, and a monthly quota cannot stop it being used up in one day.

3. **Tier quotas**: `pro` 20 -> 10, `starter` unlimited -> 100 (`growth` stays 0 = no monthly limit).
   `memberships` is only seeded when the table is empty (see membership_service._initialize_default_memberships),
   so existing rows can only be changed here; the count wording in features is also made consistent, otherwise the page says
   "unlimited audits" yet stops at 100.

⚠️ The credits metric definition also changes from "1 credit = ¥1" to "**¥1 = 100 credits**", but that is a **display-layer only**
change (the values were always in cents, just no longer divided by 100), and not a single byte in the DB needs to change, so there is no data migration here.
The balance users see will jump x100 overnight with unchanged purchasing power; an announcement is needed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f8b3d5c91a20'
down_revision: Union[str, None] = 'e5c2a48d1739'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# key -> (new value, old value). Unit is credits (= cents).
_PRICES = {
    "batch_cell": (4, 2),
    "content_doc": (300, 50),
    "content_image": (50, 0),
    "compliance_scan": (50, 0),
    "retest_probe": (20, 0),
    "sentiment_once": (1000, 500),
    "sentiment_daily": (500, None),      # None = newly added in this change, deleted on downgrade
}


def _set_price(conn, key: str, cents: int) -> None:
    now = datetime.utcnow()
    res = conn.execute(
        sa.text("UPDATE balance_prices SET price_cents = :p, enabled = 1, "
                "updated_at = :now WHERE key = :k"),
        {"p": cents, "k": key, "now": now},
    )
    if res.rowcount == 0:
        conn.execute(
            sa.text("INSERT INTO balance_prices (key, price_cents, enabled, updated_at) "
                    "VALUES (:k, :p, 1, :now)"),
            {"k": key, "p": cents, "now": now},
        )


def _swap_feature(conn, slug: str, old: str, new: str) -> None:
    """Only change this tier's features wording: a full-table replace would also change same-named sentences in other tiers."""
    conn.execute(
        sa.text("UPDATE memberships SET features = replace(features, :old, :new) "
                "WHERE slug = :slug"),
        {"old": old, "new": new, "slug": slug},
    )


def upgrade() -> None:
    op.create_table(
        "user_check_usage_daily",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bj_date", sa.String(length=10), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "bj_date"),
    )

    conn = op.get_bind()
    for key, (new, _old) in _PRICES.items():
        _set_price(conn, key, new)

    conn.execute(sa.text("UPDATE memberships SET monthly_check_quota = 10 WHERE slug = 'pro'"))
    conn.execute(sa.text("UPDATE memberships SET monthly_check_quota = 100 WHERE slug = 'starter'"))
    _swap_feature(conn, "pro", "每月 20 次检测", "每月 10 次检测(每日上限 10 次)")
    _swap_feature(conn, "starter", "无限检测次数", "每月 100 次检测(每日上限 10 次)")
    _swap_feature(conn, "growth", "无限检测次数", "不限月度检测次数(每日上限 10 次)")


def downgrade() -> None:
    conn = op.get_bind()
    _swap_feature(conn, "growth", "不限月度检测次数(每日上限 10 次)", "无限检测次数")
    _swap_feature(conn, "starter", "每月 100 次检测(每日上限 10 次)", "无限检测次数")
    _swap_feature(conn, "pro", "每月 10 次检测(每日上限 10 次)", "每月 20 次检测")
    conn.execute(sa.text("UPDATE memberships SET monthly_check_quota = 0 WHERE slug = 'starter'"))
    conn.execute(sa.text("UPDATE memberships SET monthly_check_quota = 20 WHERE slug = 'pro'"))

    for key, (_new, old) in _PRICES.items():
        if old is None:
            conn.execute(sa.text("DELETE FROM balance_prices WHERE key = :k"), {"k": key})
        else:
            _set_price(conn, key, old)

    op.drop_table("user_check_usage_daily")
