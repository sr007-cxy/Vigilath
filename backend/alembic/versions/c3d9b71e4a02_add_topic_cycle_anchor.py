"""add ai_telemetry_topics.cycle_anchor_weekday / cycle_anchor_hour

Revision ID: c3d9b71e4a02
Revises: a7c3e9f12b04
Create Date: 2026-08-14 10:00:00.000000

Topic-level configurable batch-run cycle anchor: previously the anchor was a single global env
(SCHEDULER_CRON_WEEKDAY / SCHEDULER_CRON_HOUR / SCHEDULER_CRON_MINUTE),
All weekly topics were forced to start their cycle on the same day (default Monday 02:05); to move one customer to a different day
meant editing env and restarting telemetry, and any change affected everyone.

Both columns are nullable; NULL = use the global env default; existing topics behave exactly as before.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d9b71e4a02"
down_revision: Union[str, None] = "a7c3e9f12b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        # 0=Monday .. 6=Sunday (same convention as Python weekday(), and consistent with SCHEDULER_CRON_WEEKDAY)
        batch_op.add_column(sa.Column("cycle_anchor_weekday", sa.SmallInteger(), nullable=True))
        # 0-23, local time (SCHEDULER_TIMEZONE_OFFSET, default +8)
        batch_op.add_column(sa.Column("cycle_anchor_hour", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.drop_column("cycle_anchor_hour")
        batch_op.drop_column("cycle_anchor_weekday")
