"""add agent_notifications + agent_im_connectors.last_chat_id (proactive sentiment monitoring push)

New table + new column, existing data untouched. See docs/03-系统设计/Agent/对外开放-契约.md / best approach §12 proactive outreach.

Revision ID: d4a7e2f1c930
Revises: e2c4f8a1b360
Create Date: 2026-06-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7e2f1c930"
down_revision: Union[str, None] = "e2c4f8a1b360"   # Follows the browser_jobs chain to avoid a dual-head fork
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_im_connectors", sa.Column("last_chat_id", sa.String(length=256), nullable=False, server_default=""))
    op.create_table(
        "agent_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_notifications_dedup", "agent_notifications", ["dedup_key"], unique=True)
    op.create_index("ix_agent_notif_acct", "agent_notifications", ["account_id", "read_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_notif_acct", table_name="agent_notifications")
    op.drop_index("ix_agent_notifications_dedup", table_name="agent_notifications")
    op.drop_table("agent_notifications")
    op.drop_column("agent_im_connectors", "last_chat_id")
