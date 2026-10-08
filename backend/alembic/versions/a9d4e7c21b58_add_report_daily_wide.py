"""Report center daily wide table ai_telemetry_report_daily

Revision ID: a9d4e7c21b58
Revises: c5e8a2d94f17
Create Date: 2026-07-06 10:00:00.000000

One row per (topic, Beijing day, engine); engine='__all__' is the daily total row.
Count columns are authoritative (weekly/monthly reports sum counts over the period and recompute ratios); percentage columns are redundant for display.
The report center's 5 main endpoints read the wide table first; topics with no wide-table rows fall back to read-time aggregation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9d4e7c21b58'
down_revision: Union[str, None] = 'c5e8a2d94f17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_telemetry_report_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False, server_default="__all__"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top1_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top3_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("neg_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comp_mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exposure_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("top1_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("top3_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("accuracy_pct", sa.Float(), nullable=False, server_default="100"),
        sa.Column("citation_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mention_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="job"),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["topic_id"], ["ai_telemetry_topics.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("ai_telemetry_report_daily", schema=None) as batch_op:
        batch_op.create_index("uq_report_daily_cell", ["topic_id", "day", "engine"], unique=True)
        batch_op.create_index("idx_report_daily_topic", ["topic_id", "day"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_telemetry_report_daily_id"), ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("ai_telemetry_report_daily")
