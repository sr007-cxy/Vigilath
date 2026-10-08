"""add ai_telemetry_topics.schedule_tier + paused_until

Revision ID: a1f7c2d9b408
Revises: e9a4d1c8f236
Create Date: 2026-07-20 16:00:00.000000

Tiered batch-run scheduling: topic-level "daily / weekly / off / temporarily paused", replacing the stopgap of constraining scheduling via an env
whitelist (TELEMETRY_DRIP_ONLY_TOPICS); now configurable in the admin backend.

Default weekly: currently every enabled topic runs on a 7-day cycle, so existing behavior is unchanged.
Existing topics with enabled=False semantically mean "off", backfilled to tier='off',
ensuring the enabled and tier fields never contradict from the start (enabled is a derived cache of tier).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f7c2d9b408"
down_revision: Union[str, None] = "e9a4d1c8f236"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "schedule_tier", sa.String(length=16), nullable=False, server_default="weekly"))
        batch_op.add_column(sa.Column("paused_until", sa.DateTime(), nullable=True))
    # Align existing data: disabled topics = off (idempotent, safe to rerun; does not touch other business columns)
    op.execute("UPDATE ai_telemetry_topics SET schedule_tier='off' WHERE enabled = false")


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.drop_column("paused_until")
        batch_op.drop_column("schedule_tier")
