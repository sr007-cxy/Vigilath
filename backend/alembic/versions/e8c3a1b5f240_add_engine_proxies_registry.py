"""add engine_proxies registry + engine_sessions.proxy_id (proxy registry)

Revision ID: e8c3a1b5f240
Revises: d7b2f4a8c019
Create Date: 2026-07-02

Proxy registry: centrally registers reusable proxies; accounts reference them via engine_sessions.proxy_id, resolved into a string at check-out.
Note: the shared appdb's alembic chain historically lagged behind; tables were actually created idempotently with CREATE TABLE IF NOT EXISTS / ADD COLUMN
IF NOT EXISTS, and this file is only a record.
"""
from alembic import op
import sqlalchemy as sa

revision = "e8c3a1b5f240"
down_revision = "d7b2f4a8c019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "engine_proxies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("proxy_url", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True),
        sa.Column("last_exit_ip", sa.String(), nullable=True),
        sa.Column("last_country", sa.String(), nullable=True),
    )
    op.add_column("engine_sessions", sa.Column("proxy_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("engine_sessions", "proxy_id")
    op.drop_table("engine_proxies")
