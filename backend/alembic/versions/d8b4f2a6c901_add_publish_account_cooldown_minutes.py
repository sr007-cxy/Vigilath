"""add per-account publish cooldown minutes

Revision ID: d8b4f2a6c901
Revises: c6a9d2e5f731
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "d8b4f2a6c901"
down_revision = "c6a9d2e5f731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_accounts", sa.Column("cooldown_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_accounts", "cooldown_minutes")
