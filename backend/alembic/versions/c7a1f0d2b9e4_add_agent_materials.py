"""add agent_materials (Agent materials knowledge base)

Additive new table, no changes to existing tables: low risk. Used by the conversational Agent's ingest_material / ask_knowledge.

Revision ID: c7a1f0d2b9e4
Revises: f3a9c1d2e4b7
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a1f0d2b9e4"
down_revision: Union[str, None] = "f3a9c1d2e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_agent_materials_account_id", "agent_materials", ["account_id"])
    op.create_index("idx_agent_mat_acct_topic", "agent_materials", ["account_id", "topic_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_mat_acct_topic", table_name="agent_materials")
    op.drop_index("ix_agent_materials_account_id", table_name="agent_materials")
    op.drop_table("agent_materials")
