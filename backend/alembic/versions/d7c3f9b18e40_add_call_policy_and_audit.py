"""add call_policies + call_audit (unified policy for internal and external calls)

Revision ID: d7c3f9b18e40
Revises: f4a1c7e9d580
Create Date: 2026-08-20 12:00:00.000000

The external multi-tenant gateway (/v1) has long had a policy (engine whitelist + daily quota + RPM + queue priority),
while internal batch runs bypassed it entirely, neither limited nor recorded. This migration brings both into one layer:

- call_policies: permission tiers. The core is two execution dimensions: exec_mode (automation / direct API),
  run_mode (execute immediately / queue via scheduling); plus engine whitelist, daily count, RPM, concurrency, enqueue priority.
  The internal tier has unlimited=1, exempt from limits but still audited.
- call_audit: unified call audit, one table for internal and external, distinguished by channel.
- api_tenants.policy_key / agent_tokens.policy_key: tenants and tokens override the channel's default tier.

Default tiers are not seeded here (created lazily, see services/call_policy.ensure_seed), covering new and old environments uniformly,
and avoiding a data migration every time a default value changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7c3f9b18e40"
down_revision: Union[str, None] = "f4a1c7e9d580"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "call_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="web"),
        sa.Column("allow_actions", sa.Text(), nullable=False, server_default=""),
        sa.Column("deny_actions", sa.Text(), nullable=False, server_default=""),
        sa.Column("exec_mode", sa.String(length=16), nullable=False, server_default="any"),
        sa.Column("run_mode", sa.String(length=16), nullable=False, server_default="any"),
        sa.Column("engines", sa.Text(), nullable=False, server_default=""),
        sa.Column("daily_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rpm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("unlimited", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_call_policies_key", "call_policies", ["key"], unique=True)

    op.create_table(
        "call_audit",
        # A BIGINT primary key loses autoincrement on SQLite, hence with_variant to Integer
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("bj_date", sa.String(length=10), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="web"),
        sa.Column("policy_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("tid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("endpoint", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("engine", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("exec_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("run_mode", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("decision", sa.String(length=8), nullable=False, server_default="allow"),
        sa.Column("reason", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("cells", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index("idx_call_audit_account_ts", "call_audit", ["account_id", "ts"])
    op.create_index("idx_call_audit_policy_ts", "call_audit", ["policy_key", "ts"])
    op.create_index("idx_call_audit_channel_ts", "call_audit", ["channel", "ts"])
    op.create_index("idx_call_audit_day", "call_audit", ["bj_date"])
    op.create_index("idx_call_audit_request", "call_audit", ["request_id"])

    # Override columns: tenants / tokens can each specify a tier; if empty, use the channel's default tier
    op.add_column("api_tenants",
                  sa.Column("policy_key", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("agent_tokens",
                  sa.Column("policy_key", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("agent_tokens", "policy_key")
    op.drop_column("api_tenants", "policy_key")
    for ix in ("idx_call_audit_request", "idx_call_audit_day", "idx_call_audit_channel_ts",
               "idx_call_audit_policy_ts", "idx_call_audit_account_ts"):
        op.drop_index(ix, table_name="call_audit")
    op.drop_table("call_audit")
    op.drop_index("ix_call_policies_key", table_name="call_policies")
    op.drop_table("call_policies")
