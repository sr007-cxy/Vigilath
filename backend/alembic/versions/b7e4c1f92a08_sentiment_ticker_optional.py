"""Sentiment monitoring accounts: stock ticker becomes optional, uniqueness key becomes (user_id, target, ticker)

Revision ID: b7e4c1f92a08
Revises: c9a7f4b3e1d8
Create Date: 2026-09-07 00:00:00.000000

The stock ticker was always "optional": the UI has always said optional, but on the schema side `uq_user_ticker`
treated (user_id, ticker) as the account identity. Once the ticker may be empty, a second unlisted brand's
ticker is also an empty string, and this constraint throws IntegrityError on INSERT; the user sees a
500 with no explanation at all.

So the identity key is relaxed to (user_id, target, ticker):
  - the old key is a subset of the new one (same ticker implies same group), so existing data cannot violate the new constraint; zero backfill;
  - two different brands with empty tickers no longer displace each other;
  - adding the same brand + same ticker twice is still blocked.

The ticker column stays NOT NULL: empty values are always written as empty strings, without introducing NULL as a third "not filled" meaning
(NULLs are not equal to each other in unique indexes, which would disable the constraint entirely). server_default '' is added here.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7e4c1f92a08"
down_revision: Union[str, None] = "c9a7f4b3e1d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "sentiment_accounts"
OLD_UQ = "uq_user_ticker"
NEW_UQ = "uq_user_target_ticker"


def _uq_names(bind) -> set[str]:
    insp = sa.inspect(bind)
    if TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_unique_constraints(TABLE) if c.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    names = _uq_names(bind)

    if bind.dialect.name == "postgresql":
        # On PG ALTER directly, avoiding a full table rebuild in batch mode
        if OLD_UQ in names:
            op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {OLD_UQ}")
        if NEW_UQ not in names:
            op.create_unique_constraint(NEW_UQ, TABLE, ["user_id", "target", "ticker"])
        op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN ticker SET DEFAULT ''")
    else:
        # SQLite: unique constraints can only be changed via a batch table rebuild
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            if OLD_UQ in names:
                batch_op.drop_constraint(OLD_UQ, type_="unique")
            if NEW_UQ not in names:
                batch_op.create_unique_constraint(NEW_UQ, ["user_id", "target", "ticker"])


def downgrade() -> None:
    """Downgrade has a precondition: existing data must not have two rows with the same (user_id, ticker) (including two empty tickers),
    otherwise the old constraint cannot be recreated; this is the inherent cost of relaxing a constraint, and we do not silently drop data to compensate."""
    bind = op.get_bind()
    if TABLE not in sa.inspect(bind).get_table_names():
        return
    names = _uq_names(bind)
    if bind.dialect.name == "postgresql":
        if NEW_UQ in names:
            op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT {NEW_UQ}")
        if OLD_UQ not in names:
            op.create_unique_constraint(OLD_UQ, TABLE, ["user_id", "ticker"])
        op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN ticker DROP DEFAULT")
    else:
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            if NEW_UQ in names:
                batch_op.drop_constraint(NEW_UQ, type_="unique")
            if OLD_UQ not in names:
                batch_op.create_unique_constraint(OLD_UQ, ["user_id", "ticker"])
