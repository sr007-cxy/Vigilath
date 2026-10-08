"""agent_device_codes: device code authorization for skill installation

Revision ID: d4f1a8c37b52
Revises: e4b7c9d21a56
Create Date: 2026-08-18

Hangs off the then single head e4b7c9d21a56, keeping a single head: geo.service runs
`alembic upgrade head` on startup, and multiple heads would stop it from starting.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f1a8c37b52"
down_revision = "e4b7c9d21a56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_device_codes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("device_code", sa.String(length=64), nullable=False),
        sa.Column("user_code", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("client", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("client_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_device_codes_device_code", "agent_device_codes", ["device_code"], unique=True)
    op.create_index("ix_agent_device_codes_user_code", "agent_device_codes", ["user_code"], unique=True)
    op.create_index("ix_agent_device_codes_account_id", "agent_device_codes", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_device_codes_account_id", table_name="agent_device_codes")
    op.drop_index("ix_agent_device_codes_user_code", table_name="agent_device_codes")
    op.drop_index("ix_agent_device_codes_device_code", table_name="agent_device_codes")
    op.drop_table("agent_device_codes")
