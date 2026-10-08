"""skillpay_orders: SkillPay order fulfillment records (idempotent + refund trail)

Revision ID: f1a7c3e9d284
Revises: c2f6a9d13e70
Create Date: 2026-08-21

★ The repo has multiple heads, and `alembic upgrade head` is ambiguous. Upgrade to this target:
    alembic upgrade f1a7c3e9d284
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a7c3e9d284"
down_revision = "c2f6a9d13e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skillpay_orders",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("out_trade_no", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="CREATED"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("refund_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_skillpay_orders_out_trade_no", "skillpay_orders", ["out_trade_no"], unique=True)
    op.create_index("ix_skillpay_orders_account", "skillpay_orders", ["account_id", "created_at"])
    op.create_index("ix_skillpay_orders_status", "skillpay_orders", ["status"])
    op.create_index("ix_skillpay_orders_created", "skillpay_orders", ["created_at"])


def downgrade() -> None:
    for ix in ("ix_skillpay_orders_created", "ix_skillpay_orders_status",
               "ix_skillpay_orders_account", "ix_skillpay_orders_out_trade_no"):
        op.drop_index(ix, table_name="skillpay_orders")
    op.drop_table("skillpay_orders")
