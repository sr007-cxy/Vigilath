"""merge heads b9d1f3a6c824 + b9d2f4a7c168 (eliminate dual alembic heads)

Revision ID: e2f7b1c9d503
Revises: b9d1f3a6c824, b9d2f4a7c168
Create Date: 2026-08-16

Two branches tried to fix the same dual head (c7f2a91d4e63 + c3d9b71e4a02) at the same time, each creating a
merge revision (the filing marking batch via a1c4e7f92b30, the Sales Agent profile batch via
b9d1f3a6c824), so the dual head was not eliminated but popped up again in a different place.

`alembic upgrade head` exits immediately with Multiple head revisions on multiple heads, which looks like the whole
backend failing to start (previous two occurrences: f7d3b9a2e6c1, a1c4e7f92b30). This migration only merges, with no DDL.

⚠️ Lesson: run `alembic heads` before changing alembic; someone else may be fixing the same head.
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "e2f7b1c9d503"
down_revision = ("b9d1f3a6c824", "b9d2f4a7c168")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
