"""add geo_retest_probes

Revision ID: d8f1c3b7a925
Revises: c6e2a9f4d817
Create Date: 2026-07-20 13:00:00.000000

Flywheel P1-C: targeted post-placement re-test probes: registered on successful publish, re-tested via a dispatch insert after N days,
producing before/after metric comparisons (before/after/delta).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f1c3b7a925"
down_revision: Union[str, None] = "c6e2a9f4d817"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geo_retest_probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(),
                  sa.ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("doc_id", sa.Integer(),
                  sa.ForeignKey("topic_generated_docs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("queries_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("engines_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("doc_urls_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("baseline_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="scheduled"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_retest_probe_topic", "geo_retest_probes", ["topic_id"])
    op.create_index("idx_retest_probe_status", "geo_retest_probes", ["status"])


def downgrade() -> None:
    op.drop_index("idx_retest_probe_status", table_name="geo_retest_probes")
    op.drop_index("idx_retest_probe_topic", table_name="geo_retest_probes")
    op.drop_table("geo_retest_probes")
