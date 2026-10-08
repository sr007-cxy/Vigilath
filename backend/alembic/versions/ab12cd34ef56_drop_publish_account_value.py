"""drop publish account value

Revision ID: ab12cd34ef56
Revises: f2d9c6a4b731
Create Date: 2026-08-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "ab12cd34ef56"
down_revision = "f2d9c6a4b731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publish_accounts") as batch_op:
        batch_op.drop_column("account_value")


def downgrade() -> None:
    with op.batch_alter_table("publish_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("account_value", sa.String(), nullable=False, server_default="low")
        )
