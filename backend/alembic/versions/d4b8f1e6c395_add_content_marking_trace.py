"""add topic_generated_docs.gen_user_id / gen_client_ip / gen_trace_no (content marking traceability)

Revision ID: d4b8f1e6c395
Revises: a1c4e7f92b30
Create Date: 2026-08-16 12:00:00.000000

Implements implicit marking under the Measures for Labeling AI-Generated Synthetic Content: algorithm filing remediation list item 5.
Design in docs/09-软著与专利/备案补正/02-方案与代办台账.md §4.2.

All three columns are nullable: existing drafts are not backfilled (no IP was collected when they were generated, and fabricating one would be a forged ledger),
and the traceability page shows "not collected" for existing drafts. New drafts get values from services/content_marking.stamp().

Unique index on gen_trace_no: the entry point for reverse lookup by number on the traceability page; numbers contain doc.id and are naturally unique;
the unique index guards against someone later changing the numbering rule and silently getting collisions.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4b8f1e6c395"
down_revision: Union[str, None] = "a1c4e7f92b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        # Who triggered generation; automatic batch runs have no trigger user -> fall back to the topic owner
        batch_op.add_column(sa.Column("gen_user_id", sa.Integer(), nullable=True))
        # Triggering IP; 'system' = automatic batch run (behind nginx, take the first hop of X-Forwarded-For)
        batch_op.add_column(sa.Column("gen_client_ip", sa.String(length=64), nullable=True))
        # Content number VGL-<entity code>-<yyyymmdd>-<doc.id six digits>
        batch_op.add_column(sa.Column("gen_trace_no", sa.String(length=48), nullable=True))
        batch_op.create_index("idx_tgd_trace_no", ["gen_trace_no"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        batch_op.drop_index("idx_tgd_trace_no")
        batch_op.drop_column("gen_trace_no")
        batch_op.drop_column("gen_client_ip")
        batch_op.drop_column("gen_user_id")
