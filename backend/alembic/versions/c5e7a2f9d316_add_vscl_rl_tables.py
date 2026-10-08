"""VSCL-RL closed-loop reinforcement learning: two tables for arm statistics + action ledger

Revision ID: c5e7a2f9d316
Revises: e6b2f8d43a91
Create Date: 2026-07-06 20:00:00.000000

Persistence layer for Vigilath Siyuan closed-loop reinforcement learning (VSCL-RL):
  vscl_rl_arms    - arm statistics per (topic, query, creative direction, copy type) (pulls/reward sum)
  vscl_rl_actions - action episode ledger (planned/executed/rewarded/stale),
                    with context fingerprints and baseline snapshots, supporting reward settlement under snapshot consistency constraints
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e7a2f9d316'
down_revision: Union[str, None] = 'e6b2f8d43a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vscl_rl_arms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("arm_key", sa.String(length=40), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("direction", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("copywriting_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reward_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reward_norm_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reward_sq_sum", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_reward", sa.Float(), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["topic_id"], ["ai_telemetry_topics.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("vscl_rl_arms", schema=None) as batch_op:
        batch_op.create_index("uq_vscl_arm", ["topic_id", "arm_key"], unique=True)
        batch_op.create_index("idx_vscl_arm_topic", ["topic_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_vscl_rl_arms_id"), ["id"], unique=False)

    op.create_table(
        "vscl_rl_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("arm_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("context_fingerprint", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("baseline_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("doc_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("planned_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("reward", sa.Float(), nullable=True),
        sa.Column("reward_norm", sa.Float(), nullable=True),
        sa.Column("reward_detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rewarded_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["topic_id"], ["ai_telemetry_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["arm_id"], ["vscl_rl_arms.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("vscl_rl_actions", schema=None) as batch_op:
        batch_op.create_index("idx_vscl_action_topic_status", ["topic_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_vscl_rl_actions_id"), ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("vscl_rl_actions")
    op.drop_table("vscl_rl_arms")
