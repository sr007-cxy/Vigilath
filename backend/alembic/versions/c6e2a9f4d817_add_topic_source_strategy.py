"""add ai_telemetry_topic_source_strategy

Revision ID: c6e2a9f4d817
Revises: b3d9f2c7e815
Create Date: 2026-07-20 12:30:00.000000

Flywheel P1-D: source strategy table: aggregated hard data on cited sources + LLM "why it was cited / media placement recommendations".
At most one row per topic (unique), overwritten in place on regeneration, same pattern as topic_solutions.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e2a9f4d817"
down_revision: Union[str, None] = "b3d9f2c7e815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_telemetry_topic_source_strategy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(),
                  sa.ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="idle"),
        sa.Column("source_stats_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("analysis_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("recommendations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("llm_model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=32), nullable=False,
                  server_default="source_strategy_v1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("topic_id", name="uq_topic_source_strategy"),
    )
    op.create_index("idx_topic_source_strategy_topic",
                    "ai_telemetry_topic_source_strategy", ["topic_id"])


def downgrade() -> None:
    op.drop_index("idx_topic_source_strategy_topic",
                  table_name="ai_telemetry_topic_source_strategy")
    op.drop_table("ai_telemetry_topic_source_strategy")
