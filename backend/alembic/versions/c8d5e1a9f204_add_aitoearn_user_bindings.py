"""add per-user AiToEarn account bindings

Revision ID: c8d5e1a9f204
Revises: b7c4e9a2d615, d5e9b3a71c24, f3a1c85d2b74
"""
from alembic import op
import sqlalchemy as sa


revision = "c8d5e1a9f204"
down_revision = ("b7c4e9a2d615", "d5e9b3a71c24", "f3a1c85d2b74")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aitoearn_account_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("upstream_account_id", sa.String(128), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_aitoearn_binding_user", "aitoearn_account_bindings", ["user_id"])
    op.create_index("uq_aitoearn_binding_upstream", "aitoearn_account_bindings", ["upstream_account_id"], unique=True)
    op.create_table(
        "aitoearn_auth_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("upstream_session_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_aitoearn_auth_attempt_user", "aitoearn_auth_attempts", ["user_id"])
    with op.batch_alter_table("topic_doc_publish_tasks") as batch_op:
        batch_op.add_column(sa.Column("aitoearn_binding_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_publish_task_aitoearn_binding", "aitoearn_account_bindings",
            ["aitoearn_binding_id"], ["id"], ondelete="SET NULL",
        )
    op.create_index(
        "uq_publish_task_doc_aitoearn_binding", "topic_doc_publish_tasks",
        ["doc_id", "aitoearn_binding_id"], unique=True,
        postgresql_where=sa.text("aitoearn_binding_id IS NOT NULL AND status != 'cancelled'"),
        sqlite_where=sa.text("aitoearn_binding_id IS NOT NULL AND status != 'cancelled'"),
    )


def downgrade() -> None:
    op.drop_index("uq_publish_task_doc_aitoearn_binding", table_name="topic_doc_publish_tasks")
    with op.batch_alter_table("topic_doc_publish_tasks") as batch_op:
        batch_op.drop_constraint("fk_publish_task_aitoearn_binding", type_="foreignkey")
        batch_op.drop_column("aitoearn_binding_id")
    op.drop_index("ix_aitoearn_auth_attempt_user", table_name="aitoearn_auth_attempts")
    op.drop_table("aitoearn_auth_attempts")
    op.drop_index("uq_aitoearn_binding_upstream", table_name="aitoearn_account_bindings")
    op.drop_index("ix_aitoearn_binding_user", table_name="aitoearn_account_bindings")
    op.drop_table("aitoearn_account_bindings")
