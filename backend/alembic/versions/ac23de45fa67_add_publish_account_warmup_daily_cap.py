"""add publish account warmup daily cap

Revision ID: ac23de45fa67
Revises: ab12cd34ef56
Create Date: 2026-08-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "ac23de45fa67"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publish_accounts") as batch_op:
        batch_op.add_column(sa.Column("warmup_daily_cap", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publish_accounts") as batch_op:
        batch_op.drop_column("warmup_daily_cap")
