"""Balance: widen the kind column to 24 + seed four new unit prices

Revision ID: d4b1f8c62a97
Revises: d4b9e2a7c318
Create Date: 2026-08-25 12:00:00.000000

Two things:

1. **Widen balance_ledger.kind from String(16) to String(24).**
   Production is already writing "publish_paid_media" (18 characters); SQLite does not check length so it never blew up,
   but it would on the day we switch to PG. The code side also turns it into a constant KIND_PUBLISH_MEDIA("publish_media"),
   but the 18-character values remain in existing rows, so the column must be wide enough first.
   ⚠️ On SQLite batch_alter_table rebuilds the table. **Do not** declare those four indexes again in table_args:
   alembic batch mode reflects and rebuilds them itself, and declaring them by hand creates them twice, failing straight away with
   "index idx_bal_ledger_idem already exists". The verification script below checks that the four indexes
   (including the UNIQUE on idem_key) still exist after the rebuild.

2. **Seed four new unit prices.** Images / compliance / re-tests start at 0: charge() lets <=0 through and writes no ledger entry,
   so the hooks can go fully live first to verify "no missed charges, no double charges", then prices are switched on row by row in the admin backend, without a release.
   A single sentiment monitoring run is 500 (¥5): the real cost is about ¥2 (one Yuanbao session + one structured summary).

Idempotent: existing keys are not overwritten; rows whose prices were changed in the admin backend must not be reset by an upgrade.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b1f8c62a97"
down_revision: Union[str, None] = "d4b9e2a7c318"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_PRICES = [
    ("content_image", 0),
    ("compliance_scan", 0),
    ("retest_probe", 0),
    ("sentiment_once", 500),
]


def upgrade() -> None:
    with op.batch_alter_table("balance_ledger") as batch:
        batch.alter_column(
            "kind",
            existing_type=sa.String(length=16),
            type_=sa.String(length=24),
            existing_nullable=False,
        )

    conn = op.get_bind()
    existing = {
        r[0] for r in conn.execute(sa.text("SELECT key FROM balance_prices")).fetchall()
    }
    rows = [
        {"key": k, "price_cents": v, "enabled": 1, "updated_at": datetime.utcnow()}
        for k, v in _NEW_PRICES
        if k not in existing
    ]
    if rows:
        op.bulk_insert(
            sa.table(
                "balance_prices",
                sa.column("key", sa.String),
                sa.column("price_cents", sa.Integer),
                sa.column("enabled", sa.Integer),
                sa.column("updated_at", sa.DateTime),
            ),
            rows,
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM balance_prices WHERE key IN "
                "('content_image', 'compliance_scan', 'retest_probe', 'sentiment_once')")
    )
    # kind is not narrowed back to 16: existing rows have 18-character values, and narrowing would truncate or fail outright
