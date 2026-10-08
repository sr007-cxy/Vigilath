"""add engine_session proxy_override (per-account bound proxy string)

Revision ID: c5e9a1f3b720
Revises: a3d8e5f27c19
Create Date: 2026-06-30

Each account binds a full proxy string (http://user:pass@host:port); when non-empty browser-service
uses it first (overriding egress/global), enabling a 1-account-1-IP topology. Stored in plaintext (same as storage_state).
"""
from alembic import op
import sqlalchemy as sa

revision = "c5e9a1f3b720"
down_revision = "a3d8e5f27c19"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("engine_sessions", sa.Column("proxy_override", sa.String(), nullable=True))


def downgrade():
    op.drop_column("engine_sessions", "proxy_override")
