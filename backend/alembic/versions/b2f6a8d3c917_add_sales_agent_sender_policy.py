"""Add Sales Agent sender selection policy

Revision ID: b2f6a8d3c917
Revises: a9c4e2f8b671
Create Date: 2026-08-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2f6a8d3c917"
down_revision: Union[str, None] = "a9c4e2f8b671"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table) if c.get("name")}


def upgrade() -> None:
    if not _has_table("sales_agent_email_settings"):
        return
    if "sender_selection_policy" not in _columns("sales_agent_email_settings"):
        op.add_column(
            "sales_agent_email_settings",
            sa.Column("sender_selection_policy", sa.String(length=32), nullable=False, server_default="default_only"),
        )
    op.execute("UPDATE sales_agent_email_settings SET sender_selection_policy = 'default_only' WHERE sender_selection_policy IS NULL OR sender_selection_policy = ''")


def downgrade() -> None:
    if _has_table("sales_agent_email_settings") and "sender_selection_policy" in _columns("sales_agent_email_settings"):
        op.drop_column("sales_agent_email_settings", "sender_selection_policy")
