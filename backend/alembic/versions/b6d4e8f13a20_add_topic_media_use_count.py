"""Add article image usage count to topic_media

Revision ID: b6d4e8f13a20
Revises: a7c3e9d24f61
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d4e8f13a20"
down_revision = "a7c3e9d24f61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topic_media",
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("topic_media", "use_count")
