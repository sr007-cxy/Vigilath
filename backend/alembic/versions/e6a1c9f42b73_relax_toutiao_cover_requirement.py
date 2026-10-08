"""relax Toutiao cover requirement

Revision ID: e6a1c9f42b73
Revises: f4b2c8d91e06
Create Date: 2026-09-20
"""

from alembic import op


revision = "e6a1c9f42b73"
down_revision = "f4b2c8d91e06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET needs_cover = false,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'toutiao'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET needs_cover = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'toutiao'
    """)
