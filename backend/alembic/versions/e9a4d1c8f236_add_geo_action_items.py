"""add geo_action_items

Revision ID: e9a4d1c8f236
Revises: d8f1c3b7a925
Create Date: 2026-07-20 13:30:00.000000

Flywheel P2: optimization task center: daily report suggestions / re-test regressions / insight suggestions converge into a unified action item
queue, convertible to a plan item or a re-test with one click. Idempotent dedup key (topic_id, source, source_key).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9a4d1c8f236"
down_revision: Union[str, None] = "d8f1c3b7a925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geo_action_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(),
                  sa.ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("source_ref_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("priority", sa.String(length=4), nullable=False, server_default="P1"),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggested_action", sa.String(length=24), nullable=False, server_default="other"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("linked_plan_item_id", sa.String(length=64), nullable=True),
        sa.Column("linked_doc_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("topic_id", "source", "source_key", name="uq_action_item_src"),
    )
    op.create_index("idx_action_item_topic_status", "geo_action_items",
                    ["topic_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_action_item_topic_status", table_name="geo_action_items")
    op.drop_table("geo_action_items")
