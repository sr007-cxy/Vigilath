"""anon_usage + anon_quick_tasks: usage limits and async tasks for no-login features

Revision ID: b8e3f1a7d264
Revises: d4f1a8c37b52
Create Date: 2026-08-19

★ The repo currently has **multiple heads** (7), and `alembic upgrade head` reports ambiguous.
  Upgrade to this target on deploy: alembic upgrade b8e3f1a7d264
"""
from alembic import op
import sqlalchemy as sa

revision = "b8e3f1a7d264"
down_revision = "d4f1a8c37b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anon_usage",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("used_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_anon_usage_feat_dev", "anon_usage", ["feature", "device_id", "used_at"])
    op.create_index("ix_anon_usage_feat_ip", "anon_usage", ["feature", "ip", "used_at"])
    op.create_index("ix_anon_usage_used_at", "anon_usage", ["used_at"])

    op.create_table(
        "anon_quick_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("device_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_anon_quick_tasks_task_id", "anon_quick_tasks", ["task_id"], unique=True)
    op.create_index("ix_anon_quick_tasks_created", "anon_quick_tasks", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_anon_quick_tasks_created", table_name="anon_quick_tasks")
    op.drop_index("ix_anon_quick_tasks_task_id", table_name="anon_quick_tasks")
    op.drop_table("anon_quick_tasks")
    op.drop_index("ix_anon_usage_used_at", table_name="anon_usage")
    op.drop_index("ix_anon_usage_feat_ip", table_name="anon_usage")
    op.drop_index("ix_anon_usage_feat_dev", table_name="anon_usage")
    op.drop_table("anon_usage")
