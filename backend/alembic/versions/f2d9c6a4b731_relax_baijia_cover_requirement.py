"""relax baijia cover requirement

Revision ID: f2d9c6a4b731
Revises: e9c5a7b3d102
Create Date: 2026-08-26
"""
from alembic import op


revision = "f2d9c6a4b731"
down_revision = "e9c5a7b3d102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET image_min = 0,
            needs_cover = false,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'baijia'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET image_min = 1,
            needs_cover = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'baijia'
    """)
