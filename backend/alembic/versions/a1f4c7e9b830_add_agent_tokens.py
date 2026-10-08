"""add agent_tokens (externally exposed 1-year account tokens)

New table, existing data untouched: low risk. See docs/03-系统设计/Agent/对外开放-契约.md §12.

Revision ID: a1f4c7e9b830
Revises: e3c9a7b1d520
Create Date: 2026-06-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c7e9b830"
down_revision: Union[str, None] = "e3c9a7b1d520"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tid", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("caps", sa.String(length=128), nullable=False, server_default="read"),
        sa.Column("origins", sa.Text(), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_tokens_tid", "agent_tokens", ["tid"], unique=True)
    op.create_index("ix_agent_tokens_account_id", "agent_tokens", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_account_id", table_name="agent_tokens")
    op.drop_index("ix_agent_tokens_tid", table_name="agent_tokens")
    op.drop_table("agent_tokens")
