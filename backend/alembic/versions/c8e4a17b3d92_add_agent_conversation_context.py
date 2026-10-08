"""Add context_json to agent_conversations: persist the diagnosis flow's slots to DB

Why slots are not stuffed into messages_json: that column is **for the model** and gets trimmed by time/count
(methods.load_message_history); slots are **for the code** and must not be trimmed. Mixing them
would keep making the model remember state like "industry terms still missing" for us; that is exactly how it was lost in the <brand-a> case on 2026-08-27.

Revision ID: c8e4a17b3d92
Revises: b6f1a3c9e274
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e4a17b3d92"
down_revision = "b6f1a3c9e274"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default is required: existing rows must not be NULL, otherwise load_flow needs null checks everywhere
    op.add_column(
        "agent_conversations",
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("agent_conversations", "context_json")
