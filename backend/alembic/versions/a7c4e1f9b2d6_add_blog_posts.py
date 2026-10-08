"""add blog_posts (move the website blog into the DB)

Revision ID: a7c4e1f9b2d6
Revises: b3e8d1c6f492
Create Date: 2026-07-14 12:00:00.000000

The website blog moves from hard-coded frontend/src/data/blogPosts.ts to the shared appdb:
the test/prod site backends read the same table, and adding a post only needs one sync_blog_posts.py upsert,
no longer a separate frontend build+publish on each side.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c4e1f9b2d6'
down_revision: Union[str, None] = 'b3e8d1c6f492'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blog_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("cover", sa.String(), nullable=True),
        sa.Column("reading_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("zh_title", sa.String(), nullable=False),
        sa.Column("zh_excerpt", sa.Text(), nullable=False),
        sa.Column("zh_content", sa.Text(), nullable=False),
        sa.Column("en_title", sa.String(), nullable=False),
        sa.Column("en_excerpt", sa.Text(), nullable=False),
        sa.Column("en_content", sa.Text(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_blog_posts_slug", "blog_posts", ["slug"], unique=True)
    op.create_index("idx_blog_posts_date", "blog_posts", ["date"])


def downgrade() -> None:
    op.drop_index("idx_blog_posts_date", table_name="blog_posts")
    op.drop_index("idx_blog_posts_slug", table_name="blog_posts")
    op.drop_table("blog_posts")
