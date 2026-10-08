"""add topic_media.is_ai_generated / gen_model / gen_trace_no (image content marking)

Revision ID: e7a3c5d81f42
Revises: d4b8f1e6c395
Create Date: 2026-08-16 15:00:00.000000

Image side of the Measures for Labeling AI-Generated Synthetic Content: algorithm filing remediation list items 7 and 8.
Design in docs/09-软著与专利/备案补正/02-方案与代办台账.md §4.3 / §4.4.

is_ai_generated defaults to false: existing rows include both user-uploaded assets and AI images, and there is **no heuristic
backfill** here (guessing by the filename prefix "AI配图-" ("AI image-") would also mark user assets that happen to be named that way as AI-generated).
Back-labeling existing AI images is handled on demand by a one-off script, not in the migration: it must rewrite image files on disk,
which cannot be rolled back on failure and does not belong in a DDL transaction.

Unique index on gen_trace_no: the reverse lookup entry for image numbers VGI-<entity code>-<yyyymmdd>-<media.id six digits>.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a3c5d81f42"
down_revision: Union[str, None] = "d4b8f1e6c395"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topic_media", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "is_ai_generated", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("gen_model", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("gen_trace_no", sa.String(length=48), nullable=True))
        batch_op.create_index("idx_topic_media_trace_no", ["gen_trace_no"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("topic_media", schema=None) as batch_op:
        batch_op.drop_index("idx_topic_media_trace_no")
        batch_op.drop_column("gen_trace_no")
        batch_op.drop_column("gen_model")
        batch_op.drop_column("is_ai_generated")
