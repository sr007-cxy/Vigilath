"""Vigilath Siyuan P0: topic Siyuan switch + strong binding of drafts to vscl_action_id

Revision ID: f1a7c3d9e520
Revises: b3e8d1c7a925
Create Date: 2026-07-26 12:00:00.000000

Three columns needed for the P0 internally usable closed loop:
  ai_telemetry_topics.siyuan_enabled        - topic-level master switch for the Siyuan closed loop (default off)
  ai_telemetry_topics.siyuan_last_tick_at   - last siyuan_tick scheduling time
  topic_generated_docs.vscl_action_id       - explicit binding draft <- VSCL-RL action (+ index)

All are nullable/defaulted added columns, lossless for existing data; siyuan_tick is disabled by default (GEO_SIYUAN_ENABLED).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a7c3d9e520'
down_revision: Union[str, None] = 'b3e8d1c7a925'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_telemetry_topics",
        sa.Column("siyuan_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        "ai_telemetry_topics",
        sa.Column("siyuan_last_tick_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "topic_generated_docs",
        sa.Column("vscl_action_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        batch_op.create_index("idx_gen_doc_vscl_action", ["vscl_action_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        batch_op.drop_index("idx_gen_doc_vscl_action")
    op.drop_column("topic_generated_docs", "vscl_action_id")
    op.drop_column("ai_telemetry_topics", "siyuan_last_tick_at")
    op.drop_column("ai_telemetry_topics", "siyuan_enabled")
