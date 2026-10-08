"""add ai_telemetry_topic_briefings.period_type

Revision ID: b3d9f2c7e815
Revises: e5b8d2f7a134
Create Date: 2026-07-20 12:00:00.000000

Flywheel P1-A: the brief table gains a daily/weekly granularity distinction. All historical rows are weekly, and server_default='weekly'
completes the backfill; daily rows are written by telemetry-service with period_type='daily'
(prompt_version also differs, so the uq_tb_period unique constraint naturally does not conflict).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3d9f2c7e815"
down_revision: Union[str, None] = "e5b8d2f7a134"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topic_briefings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("period_type", sa.String(), nullable=False, server_default="weekly"),
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topic_briefings", schema=None) as batch_op:
        batch_op.drop_column("period_type")
