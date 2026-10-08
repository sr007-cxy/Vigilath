"""Add two external image moderation columns to topic_media (Aliyun ImageModeration soft signals)

The image side of remediation list items 9 (third-party review) and 10 (manual review).
external_json stores the full response; external_risk is a redundant level for filtering the asset library list.

Revision ID: b8d4e6a2c917
Revises: c4e9b2a7f318
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d4e6a2c917"
down_revision = "c4e9b2a7f318"
branch_labels = None
depends_on = None


def _cols(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns("topic_media")}


def upgrade() -> None:
    bind = op.get_bind()
    have = _cols(bind)
    if "external_json" not in have:
        op.add_column("topic_media", sa.Column("external_json", sa.Text(), nullable=True))
    if "external_risk" not in have:
        op.add_column("topic_media", sa.Column("external_risk", sa.String(length=16), nullable=True))
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("topic_media")}
    if "idx_topic_media_ext_risk" not in idx:
        op.create_index("idx_topic_media_ext_risk", "topic_media", ["external_risk"])


def downgrade() -> None:
    bind = op.get_bind()
    idx = {i["name"] for i in sa.inspect(bind).get_indexes("topic_media")}
    if "idx_topic_media_ext_risk" in idx:
        op.drop_index("idx_topic_media_ext_risk", table_name="topic_media")
    have = _cols(bind)
    for col in ("external_risk", "external_json"):
        if col in have:
            op.drop_column("topic_media", col)
