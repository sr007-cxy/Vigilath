"""Add purpose to payment_sessions, allow null membership_id (supports top-up orders)

Revision ID: c2f6a9d13e70
Revises: b8e3f1a7d264
Create Date: 2026-08-19

★ The repo has multiple heads, and `alembic upgrade head` is ambiguous. Upgrade to this target:
    alembic upgrade c2f6a9d13e70
"""
from alembic import op
import sqlalchemy as sa

revision = "c2f6a9d13e70"
down_revision = "b8e3f1a7d264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_sessions",
                  sa.Column("purpose", sa.String(), nullable=False, server_default="membership"))
    op.create_index("ix_payment_sessions_purpose", "payment_sessions", ["purpose"])
    # Top-up orders have no plan: existing rows are all plan orders and unaffected
    op.alter_column("payment_sessions", "membership_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # There must be no top-up orders before downgrading, otherwise tightening membership_id fails: intentional, do not silently delete data
    op.alter_column("payment_sessions", "membership_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_payment_sessions_purpose", table_name="payment_sessions")
    op.drop_column("payment_sessions", "purpose")
