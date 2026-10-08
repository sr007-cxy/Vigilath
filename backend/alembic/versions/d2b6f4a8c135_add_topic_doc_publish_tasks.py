"""Flywheel P1 (§5.3): publish task table topic_doc_publish_tasks (URL backfill state machine)

Revision ID: d2b6f4a8c135
Revises: f1a7c3d9e520
Create Date: 2026-07-27 12:00:00.000000

One placement task of one draft on one media outlet/account + the URL backfill state machine. Core invariant:
no published_url means it cannot enter published; only published registers re-tests and enters the reward window.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2b6f4a8c135'
down_revision: Union[str, None] = 'f1a7c3d9e520'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topic_doc_publish_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.Integer(), nullable=False),
        sa.Column("vscl_action_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("media_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("channel_type", sa.String(length=16), nullable=False, server_default="manual_account"),
        sa.Column("publish_mode", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending_url"),
        sa.Column("planned_publish_at", sa.DateTime(), nullable=True),
        sa.Column("actual_published_at", sa.DateTime(), nullable=True),
        sa.Column("published_url", sa.String(length=1024), nullable=True),
        sa.Column("url_canonical", sa.String(length=1024), nullable=True),
        sa.Column("assigned_to_user_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("proof_json", sa.Text(), nullable=True),
        sa.Column("retest_probe_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["topic_id"], ["ai_telemetry_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doc_id"], ["topic_generated_docs.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("topic_doc_publish_tasks", schema=None) as batch_op:
        batch_op.create_index("idx_publish_task_topic_status", ["topic_id", "status"], unique=False)
        batch_op.create_index("idx_publish_task_doc", ["doc_id"], unique=False)
        batch_op.create_index("idx_publish_task_url_canonical", ["url_canonical"], unique=False)
        batch_op.create_index("idx_publish_task_assignee", ["assigned_to_user_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_topic_doc_publish_tasks_id"), ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("topic_doc_publish_tasks")
