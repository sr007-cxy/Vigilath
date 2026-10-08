"""Two topic-level automation switches: auto-approve drafts / auto-publish to paid media

Revision ID: f2c8a91d64b3
Revises: d8b1e4f37c92
Create Date: 2026-09-10

auto_approve_enabled (default True)
    Previously there was only the global env `GEO_CONTENT_REVIEW_ENABLED`; review off by default = successful generation goes straight to approved.
    That is, "auto-approve" has always been the actual behavior (this env is not set in production), just invisible to everyone
    and not distinguishable per topic. With the column, default True **matches current behavior exactly**, changing nothing for any existing topic;
    to restore manual review, turn it off for that one topic.

auto_publish_paid_enabled (default **False**)
    Paid media (Ruanwenjiang / Meijieku) orders placed automatically on the scheduled date. ★ This is the only switch that spends money on its own,
    so it defaults off: not a single existing topic starts auto-charging because of this migration; someone must turn it on explicitly.

Both columns have server_default: appdb is shared across environments, so old rows must have a value immediately; we cannot leave NULL for
`Boolean nullable=False` to blow up on read.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c8a91d64b3"
down_revision: Union[str, None] = "d8b1e4f37c92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_telemetry_topics"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Idempotent: in some environments this table was created by create_all (models already declare these two columns)
    if not _has_column(_TABLE, "auto_approve_enabled"):
        op.add_column(_TABLE, sa.Column(
            "auto_approve_enabled", sa.Boolean(),
            nullable=False, server_default=sa.true()))
    if not _has_column(_TABLE, "auto_publish_paid_enabled"):
        op.add_column(_TABLE, sa.Column(
            "auto_publish_paid_enabled", sa.Boolean(),
            nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for col in ("auto_publish_paid_enabled", "auto_approve_enabled"):
        if _has_column(_TABLE, col):
            op.drop_column(_TABLE, col)
