"""add Ctrip to the server-pool publish platform catalog

Revision ID: d0c7e4a91b26
Revises: c8d5e1a9f204
Create Date: 2026-09-15
"""

from alembic import op


revision = "d0c7e4a91b26"
down_revision = "c8d5e1a9f204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only add missing rows; do not overwrite limits ops has already adjusted in the admin console.
    op.execute("""
        INSERT INTO publish_platforms
            (code, name_zh, channel, supports_article, title_limit, body_limit,
             image_min, image_max, needs_cover, content_format,
             identity_cookies_json, enabled, sort_order, created_at, updated_at)
        VALUES
            ('ctrip', '携程', 'server_pool', true, 30, 0, 1, 20,
             true, 'plain', '["cticket"]', true, 110,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    # Do not delete platform rows that may already be bound to accounts or historical tasks.
    pass
