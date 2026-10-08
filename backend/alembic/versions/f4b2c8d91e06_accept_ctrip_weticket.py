"""accept both Ctrip login ticket cookie names

Revision ID: f4b2c8d91e06
Revises: d0c7e4a91b26
Create Date: 2026-09-15
"""

from alembic import op


revision = "f4b2c8d91e06"
down_revision = "d0c7e4a91b26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET identity_cookies_json = '["cticket", "weticket"]',
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'ctrip'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET identity_cookies_json = '["cticket"]',
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'ctrip'
    """)
