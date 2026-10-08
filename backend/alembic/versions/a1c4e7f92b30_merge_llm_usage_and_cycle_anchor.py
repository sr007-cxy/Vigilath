"""merge heads c7f2a91d4e63 + c3d9b71e4a02 (eliminate dual alembic heads)

Revision ID: a1c4e7f92b30
Revises: c7f2a91d4e63, c3d9b71e4a02
Create Date: 2026-08-16

Model usage accounting (c7f2a91d4e63) and the batch-run cycle anchor (c3d9b71e4a02) each branched off the main line
and were never merged back, creating dual heads for the second time. `alembic upgrade head` exits immediately on multiple heads with
Multiple head revisions: the service cannot start, and since the error happens during migration,
it looks like "the whole backend is down", with nothing to do with business code (previous occurrence: f7d3b9a2e6c1).

The two branches' DDL are unrelated; this migration only merges, with no DDL.
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "a1c4e7f92b30"
down_revision = ("c7f2a91d4e63", "c3d9b71e4a02")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
