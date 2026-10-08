"""set existing publish accounts to unlimited daily caps

Revision ID: f7c2a8d91e43
Revises: e6a1c9f42b73
Create Date: 2026-09-23
"""

from alembic import op


revision = "f7c2a8d91e43"
down_revision = "e6a1c9f42b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE publish_accounts
        SET daily_cap = 0,
            warmup_daily_cap = 0,
            updated_at = CURRENT_TIMESTAMP
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE publish_accounts
        SET daily_cap = NULL,
            warmup_daily_cap = NULL,
            updated_at = CURRENT_TIMESTAMP
    """)
