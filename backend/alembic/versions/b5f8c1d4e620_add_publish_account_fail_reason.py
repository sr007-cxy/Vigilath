"""add publish account failure reason

Revision ID: b5f8c1d4e620
Revises: a4e7c2f9b581
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


revision = "b5f8c1d4e620"
down_revision = "a4e7c2f9b581"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_accounts", sa.Column("last_fail_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_accounts", "last_fail_reason")
