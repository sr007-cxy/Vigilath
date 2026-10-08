"""seed the remaining server-pool publish platforms."""

from alembic import op


revision = "b7c4e9a2d615"
down_revision = "ac23de45fa67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep existing operator-tuned rows unchanged; add only missing codes.
    op.execute("""
        INSERT INTO publish_platforms
            (code, name_zh, channel, supports_article, title_limit, body_limit,
             image_min, image_max, needs_cover, content_format,
             identity_cookies_json, enabled, sort_order, created_at, updated_at)
        VALUES
            ('zhihu', '知乎', 'server_pool', true, 100, 0, 0, 20,
             false, 'markdown', '["z_c0"]', true, 10,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('toutiao', '头条号', 'server_pool', true, 30, 0, 0, 20,
             true, 'html', '[]', true, 20,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('baijia', '百家号', 'server_pool', true, 30, 0, 0, 20,
             false, 'html', '[]', true, 30,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('sohu', '搜狐号', 'server_pool', true, 60, 0, 0, 20,
             false, 'html', '[]', true, 40,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('csdn', 'CSDN', 'server_pool', true, 100, 0, 0, 20,
             false, 'html', '[]', true, 50,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('douyin', '抖音', 'server_pool', false, 55, 0, 1, 35,
             true, 'plain', '[]', true, 60,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('xhs', '小红书', 'server_pool', true, 20, 1000, 1, 18,
             true, 'plain', '[]', true, 70,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            ('wxgzh', '公众号', 'server_pool', true, 64, 20000, 1, 9,
             true, 'html', '[]', true, 80,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    # Do not delete rows that may have existed before this migration.
    pass
