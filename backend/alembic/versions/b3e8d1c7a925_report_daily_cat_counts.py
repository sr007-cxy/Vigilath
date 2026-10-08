"""add ai_telemetry_report_daily cat_* category-query count columns (schema v2)

Revision ID: b3e8d1c7a925
Revises: a1f7c2d9b408
Create Date: 2026-07-20 20:00:00.000000

Exposure rate / TOP1 exposure rate / TOP3 exposure rate globally exclude brand queries/competitor comparison queries (scene_type in
{brand, compare}, see EXPOSURE_ONLY_SCENES in reports): these two query types name the brand
in the question itself, so a hit means exposure and position carries no information; including them inflates position metrics.

The wide table's existing hit/valid counts are also the denominators of neutral-positive rate / citation rate / share of voice and cannot be stripped in place,
so four cat_* columns (category-query metric definition) are added exclusively for the three position metrics. REPORT_DAILY_SCHEMA_VERSION
1->2; old rows are filtered by the read side and automatically fall back to per-row real-time aggregation; a full backfill is needed after upgrade:
  python -m geo.services.report_daily_rollup --all --backfill --force
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3e8d1c7a925"
down_revision: Union[str, None] = "a1f7c2d9b408"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = ("cat_valid_count", "cat_hit_count", "cat_top1_count", "cat_top3_count")


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_report_daily", schema=None) as batch_op:
        for col in _COLS:
            batch_op.add_column(
                sa.Column(col, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_report_daily", schema=None) as batch_op:
        for col in reversed(_COLS):
            batch_op.drop_column(col)
