"""Split agent_conversations by business line: add a scope column, unique key account_id -> (account_id, scope)

Why the split is required: the system prompt is serialized into messages_json (docs/03-系统设计/Agent/架构-现状.md §13),
and existing conversations always use the copy persisted at the time. When an operator uses the same account, chatting on the customer page first and then entering the ops workspace, a shared conversation
means the model still sees the old customer-line prompt with "paywall / only serve the current account", while the ops console's two-step confirmation rule
is not in it at all: "everything looks normal, but the safety mechanism is not in effect". The two lines' histories must be physically isolated.

Existing rows are all filled with self (they were customer self-service conversations to begin with), so uniqueness does not conflict.

Revision ID: a7f4c2e8b915
Revises: a6e9c2f8d431
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7f4c2e8b915"
down_revision: Union[str, None] = "a6e9c2f8d431"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="self"),
    )
    # The old unique constraint was created by Column(unique=True, index=True); on PG it is named ix_agent_conversations_account_id
    # (or possibly a UNIQUE CONSTRAINT of the same name). Try dropping both; whether they exist does not affect what follows.
    conn = op.get_bind()
    conn.exec_driver_sql("DROP INDEX IF EXISTS ix_agent_conversations_account_id")
    conn.exec_driver_sql(
        "ALTER TABLE agent_conversations DROP CONSTRAINT IF EXISTS ix_agent_conversations_account_id"
    )
    conn.exec_driver_sql(
        "ALTER TABLE agent_conversations DROP CONSTRAINT IF EXISTS agent_conversations_account_id_key"
    )
    # A non-unique index is still needed (looking up conversations by account_id is a hot path)
    op.create_index("ix_agent_conversations_account_id", "agent_conversations", ["account_id"])
    op.create_unique_constraint(
        "uq_agent_conv_acct_scope", "agent_conversations", ["account_id", "scope"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_agent_conv_acct_scope", "agent_conversations", type_="unique")
    op.drop_index("ix_agent_conversations_account_id", table_name="agent_conversations")
    # Clear admin-line conversations before downgrading, otherwise the account_id uniqueness will conflict
    op.get_bind().exec_driver_sql("DELETE FROM agent_conversations WHERE scope <> 'self'")
    op.create_index(
        "ix_agent_conversations_account_id", "agent_conversations", ["account_id"], unique=True
    )
    op.drop_column("agent_conversations", "scope")
