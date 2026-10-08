"""User balance billing: three tables for accounts / ledger / unit prices

Revision ID: c4e9b2a7f318
Revises: e2f7b1c9d503
Create Date: 2026-08-17 16:00:00.000000

⚠️ down_revision hangs off e2f7b1c9d503, not the locally visible f3a9c7e21b48:
the latter (publish batch ledger) is an **untracked file** locally, never committed, and exists on neither origin nor test host;
hanging off it would break the alembic chain on the server with a KeyError, making `alembic current` unusable (already hit in practice).
e2f7b1c9d503 is the real head on origin and the version the test host appdb is currently at.

**For whoever comes next**: once f3a9c7e21b48 is committed, it and this migration will both hang off e2f7b1c9d503 -> dual heads,
and `alembic upgrade head` will fail with Multiple head revisions. Then add an empty merge migration following the a1c4e7f92b30 template
to close it (down_revision as a tuple); do not change either side's down_revision.

Billing only covers batch runs (monitoring questions x engines) and publishing (per article); audits are not charged and keep using membership quotas.

None of the three tables have foreign keys: telemetry-service is deployed separately, connects to the same DB, and has its own mirror implementation writing directly to
balance_ledger / user_balances; FKs would let its writes be blocked by deletes on the main DB's users / topics
(same reason as llm_call_log, see c7f2a91d4e63).

No backfill of existing data: the sign-up bonus is **granted lazily** (create the row + grant 1000 cents on first balance access),
so old users need no rows here. Writing them eagerly would also pay zombie accounts that never logged in.

balance_prices is seeded with two initial rows at conservative prices; formal pricing is tuned in the admin backend after running a while
and reconciling real costs against llm_call_log by trace_id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e9b2a7f318"
down_revision: Union[str, None] = "e2f7b1c9d503"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_balances",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("balance_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("granted_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recharged_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spent_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "balance_ledger",
        # SQLite's rowid alias only recognizes INTEGER PRIMARY KEY; BIGINT loses autoincrement
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bj_date", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after_cents", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("ref_type", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("idem_key", sa.String(length=128), nullable=False),
        sa.Column("note_json", sa.Text(), nullable=True),
    )
    op.create_index("idx_bal_ledger_user_created", "balance_ledger",
                    ["user_id", "created_at"])
    op.create_index("idx_bal_ledger_day", "balance_ledger", ["bj_date"])
    op.create_index("idx_bal_ledger_trace", "balance_ledger", ["trace_id"])
    # The crux of idempotency: every charge / credit carries idem_key, so duplicate submissions hit this index instead of charging twice
    op.create_index("idx_bal_ledger_idem", "balance_ledger", ["idem_key"], unique=True)

    op.create_table(
        "balance_prices",
        sa.Column("key", sa.String(length=32), primary_key=True),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )

    # Initial values: conservative prices, editable in the admin backend. batch_cell 2 cents/run, content_doc 50 cents/article.
    # At 50 questions x 5 engines = 250 runs, one batch run costs ¥5; the ¥10 bonus covers two rounds or 20 drafts.
    # ⚠️ Use Python datetime rather than sa.func.now(): bulk_insert binds SQL functions as parameters,
    #    and SQLite's DateTime type only accepts datetime objects, so func.now() raises TypeError straight away.
    now = datetime.utcnow()
    op.bulk_insert(
        sa.table(
            "balance_prices",
            sa.column("key", sa.String),
            sa.column("price_cents", sa.Integer),
            sa.column("enabled", sa.Integer),
            sa.column("updated_at", sa.DateTime),
        ),
        [
            {"key": "batch_cell", "price_cents": 2, "enabled": 1, "updated_at": now},
            {"key": "content_doc", "price_cents": 50, "enabled": 1, "updated_at": now},
        ],
    )


def downgrade() -> None:
    op.drop_table("balance_prices")
    op.drop_index("idx_bal_ledger_idem", table_name="balance_ledger")
    op.drop_index("idx_bal_ledger_trace", table_name="balance_ledger")
    op.drop_index("idx_bal_ledger_day", table_name="balance_ledger")
    op.drop_index("idx_bal_ledger_user_created", table_name="balance_ledger")
    op.drop_table("balance_ledger")
    op.drop_table("user_balances")
