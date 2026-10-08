"""Soft delete for topic_generated_docs: deleted_at / deleted_by_id

Why not hard delete: drafts carry placement tasks, post-placement re-test probes and URL-level citation attribution (cited_by_json);
physically deleting them would wipe the whole evidence chain, which could never be reconciled afterwards. Deletion becomes a flag: the frontend never shows them,
and they can be restored when needed.

Additive nullable columns; existing rows all have deleted_at NULL = not deleted, zero impact.

Revision ID: b3c9e1f7a248
Revises: a7f4c2e8b915
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c9e1f7a248"
down_revision: Union[str, None] = "a7f4c2e8b915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("topic_generated_docs", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("topic_generated_docs", sa.Column("deleted_by_id", sa.Integer(), nullable=True))
    # Every list query filters deleted_at IS NULL, so give it an index
    op.create_index("idx_tgd_deleted", "topic_generated_docs", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("idx_tgd_deleted", table_name="topic_generated_docs")
    op.drop_column("topic_generated_docs", "deleted_by_id")
    op.drop_column("topic_generated_docs", "deleted_at")
