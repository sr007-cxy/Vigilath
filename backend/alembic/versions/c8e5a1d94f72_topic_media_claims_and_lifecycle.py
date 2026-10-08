"""Add image selection leases, failure cooldown and AI image lifecycle to topic_media

Revision ID: c8e5a1d94f72
Revises: b6d4e8f13a20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e5a1d94f72"
down_revision = "b6d4e8f13a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("claim_token", sa.String(length=32), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("image_edit_fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_edit_retry_at", sa.DateTime(), nullable=True),
        sa.Column("source_media_id", sa.Integer(), nullable=True),
        sa.Column("generated_doc_id", sa.Integer(), nullable=True),
        sa.Column("generation_state", sa.String(length=16), nullable=True),
    ):
        op.add_column("topic_media", column)

    op.create_foreign_key(
        "fk_topic_media_source_media_id", "topic_media", "topic_media",
        ["source_media_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_topic_media_generated_doc_id", "topic_media", "topic_generated_docs",
        ["generated_doc_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "idx_topic_media_generation_state_uploaded", "topic_media",
        ["generation_state", "uploaded_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_topic_media_generation_state_uploaded", table_name="topic_media")

    for name in ("fk_topic_media_generated_doc_id", "fk_topic_media_source_media_id"):
        op.drop_constraint(name, "topic_media", type_="foreignkey")

    for name in (
        "generation_state", "generated_doc_id", "source_media_id",
        "image_edit_retry_at", "image_edit_fail_count",
        "claim_expires_at", "claim_token",
    ):
        op.drop_column("topic_media", name)
