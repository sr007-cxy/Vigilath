"""add sales agent undo checkpoints

Revision ID: a7c9e1d4b2f6
Revises: c8e4a17b3d92, e4f9c2a71b83
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c9e1d4b2f6"
down_revision = ("c8e4a17b3d92", "e4f9c2a71b83")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_agent_undo_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("checkpoint_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="lead_flow"),
        sa.Column("label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("step", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["session_id"], ["sales_agent_chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_agent_undo_checkpoints_id", "sales_agent_undo_checkpoints", ["id"])
    op.create_index("ix_sales_agent_undo_checkpoints_session_id", "sales_agent_undo_checkpoints", ["session_id"])
    op.create_index("ix_sales_agent_undo_checkpoints_user_id", "sales_agent_undo_checkpoints", ["user_id"])
    op.create_index("ix_sales_agent_undo_checkpoints_source_message_id", "sales_agent_undo_checkpoints", ["source_message_id"])
    op.create_index("ix_sales_agent_undo_checkpoints_consumed_at", "sales_agent_undo_checkpoints", ["consumed_at"])
    op.create_index("ix_sales_agent_undo_checkpoints_created_at", "sales_agent_undo_checkpoints", ["created_at"])
    op.create_index("idx_sales_agent_undo_session_stack", "sales_agent_undo_checkpoints", ["session_id", "consumed_at", "id"])
    op.create_index("idx_sales_agent_undo_user_created", "sales_agent_undo_checkpoints", ["user_id", "created_at"])
    op.create_index("ux_sales_agent_undo_checkpoint_key", "sales_agent_undo_checkpoints", ["checkpoint_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_sales_agent_undo_checkpoint_key", table_name="sales_agent_undo_checkpoints")
    op.drop_index("idx_sales_agent_undo_user_created", table_name="sales_agent_undo_checkpoints")
    op.drop_index("idx_sales_agent_undo_session_stack", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_created_at", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_consumed_at", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_source_message_id", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_user_id", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_session_id", table_name="sales_agent_undo_checkpoints")
    op.drop_index("ix_sales_agent_undo_checkpoints_id", table_name="sales_agent_undo_checkpoints")
    op.drop_table("sales_agent_undo_checkpoints")
