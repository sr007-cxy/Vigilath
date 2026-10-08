"""Add metadata to Sales Agent outreach drafts

Revision ID: f8c2d1a9b764
Revises: d7f3a9b2c6e1
Create Date: 2026-08-19
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f8c2d1a9b764"
down_revision: Union[str, None] = "d7f3a9b2c6e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "sales_agent_outreach_logs"
_COLUMN = "metadata_json"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_table(_TABLE) or _has_column(_TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=False, server_default="{}", comment="草稿元数据 JSON 字符串"))


def downgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
