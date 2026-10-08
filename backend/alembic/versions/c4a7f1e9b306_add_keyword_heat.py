"""add keyword_heat (keyword search popularity cache)

Revision ID: c4a7f1e9b306
Revises: b3f8c1d59e42
Create Date: 2026-08-06

Cache table for the heat map's second metric definition, "search popularity". Results collected from external sources (Baidu Index / Google Trends / Ocean Engine Suanshu)
and model estimates land here one row per keyword; the report API only reads the cache and makes no external calls on the request path.
Collection: geo/services/keyword_heat.py; scheduled job: backend/scripts/refresh_keyword_heat.py.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4a7f1e9b306"
down_revision = "b3f8c1d59e42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "keyword_heat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keyword", sa.String(), nullable=False),
        sa.Column("geo", sa.String(), nullable=False, server_default="cn"),
        sa.Column("core_word", sa.String(), nullable=False, server_default=""),
        sa.Column("heat", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_value", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("keyword", "geo", name="uq_keyword_heat_word_geo"),
    )
    op.create_index("idx_keyword_heat_expires", "keyword_heat", ["expires_at"])


def downgrade():
    op.drop_index("idx_keyword_heat_expires", table_name="keyword_heat")
    op.drop_table("keyword_heat")
