"""Add description (asset notes) and content_hash (content dedup) to topic_media

Completing the asset library flow (2026-09-16): draft recall and LLM image selection now use description instead of guessing file names;
uploads of the same image content under the same topic are deduped by content_hash, no longer stored twice or sent for external moderation twice.

Revision ID: a7c3e9d24f61
Revises: c4f7a2e91b58
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c3e9d24f61"
down_revision = "c4f7a2e91b58"
branch_labels = None
depends_on = None

_IDX = "idx_topic_media_topic_hash"


def _cols(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("topic_media")}


def upgrade() -> None:
    bind = op.get_bind()
    have = _cols(bind)
    if "description" not in have:
        op.add_column("topic_media", sa.Column("description", sa.String(length=500),
                                               nullable=False, server_default=""))
    if "content_hash" not in have:
        op.add_column("topic_media", sa.Column("content_hash", sa.String(length=64), nullable=True))
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("topic_media")}
    if _IDX not in idx:
        op.create_index(_IDX, "topic_media", ["topic_id", "content_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("topic_media")}
    if _IDX in idx:
        op.drop_index(_IDX, table_name="topic_media")
    have = _cols(bind)
    for col in ("content_hash", "description"):
        if col in have:
            op.drop_column("topic_media", col)
