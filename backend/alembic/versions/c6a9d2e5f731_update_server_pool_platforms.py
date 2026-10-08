"""update self-hosted publish account pool platforms

Revision ID: c6a9d2e5f731
Revises: b5f8c1d4e620
Create Date: 2026-08-19
"""
from alembic import op


revision = "c6a9d2e5f731"
down_revision = "b5f8c1d4e620"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical platform rows are kept to protect existing publish records, but taken off the available catalog.
    op.execute("""
        UPDATE publish_platforms
        SET enabled = false, updated_at = CURRENT_TIMESTAMP
        WHERE code IN ('jianshu', 'juejin')
    """)
    # The original aggregation channel was removed; these three platforms move to the self-built account pool.
    op.execute("""
        UPDATE publish_platforms
        SET channel = 'server_pool', enabled = true,
            sort_order = CASE code
                WHEN 'douyin' THEN 60 WHEN 'xhs' THEN 70 WHEN 'wxgzh' THEN 80
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE code IN ('douyin', 'xhs', 'wxgzh')
    """)
    op.execute("""
        INSERT INTO publish_platforms
            (code, name_zh, channel, supports_article, title_limit, body_limit,
             image_min, image_max, needs_cover, content_format,
             identity_cookies_json, enabled, sort_order, created_at, updated_at)
        VALUES
            ('smzdm', '什么值得买', 'server_pool', true, 100, 0, 0, 20,
             false, 'html', '[]', true, 90, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('cnblogs', '博客园', 'server_pool', true, 200, 0, 0, 20,
             false, 'html', '[]', true, 100, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (code) DO UPDATE SET
            name_zh = EXCLUDED.name_zh,
            channel = 'server_pool',
            enabled = true,
            sort_order = EXCLUDED.sort_order,
            updated_at = CURRENT_TIMESTAMP
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE publish_platforms
        SET enabled = true, updated_at = CURRENT_TIMESTAMP
        WHERE code IN ('jianshu', 'juejin')
    """)
    op.execute("""
        UPDATE publish_platforms
        SET channel = 'aggregator', updated_at = CURRENT_TIMESTAMP
        WHERE code IN ('douyin', 'xhs', 'wxgzh')
    """)
    op.execute("DELETE FROM publish_platforms WHERE code IN ('smzdm', 'cnblogs')")
