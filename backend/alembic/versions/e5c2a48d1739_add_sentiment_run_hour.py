"""add sentiment_accounts.run_hour: what time to monitor each day (Beijing time)

Revision ID: e5c2a48d1739
Revises: d4b1f8c62a97
Create Date: 2026-08-25 14:50:00.000000

With sentiment monitoring billed per run, users must be able to decide "what time to run each day". NULL = unspecified, follow the global cron
pace (existing accounts are all NULL, behavior unchanged).

**This column only moves the time, it does not change the price**: the billing idempotency key is pinned to "account x Beijing day", so it is still charged once a day.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c2a48d1739'
down_revision: Union[str, None] = 'd4b1f8c62a97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sentiment_accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("run_hour", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sentiment_accounts", schema=None) as batch_op:
        batch_op.drop_column("run_hour")
