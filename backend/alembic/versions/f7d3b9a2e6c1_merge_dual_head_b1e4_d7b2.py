"""merge heads b1e4f7c20a93 + d7b2f4a8c019 (eliminate dual alembic heads)

Revision ID: f7d3b9a2e6c1
Revises: b1e4f7c20a93, d7b2f4a8c019
Create Date: 2026-07-02

The d7b2 branch (dispatch_tasks/browser_jobs.visible_after) branched off a3d8e5f27c19 and was never merged back into the main line,
forming dual heads with b1e4, causing `alembic upgrade head` to report Multiple head revisions and the main site to fail to start.
Both branches' DDL has actually been applied, so this merge migration only joins them, with no DDL (prod just stamps to this head).
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "f7d3b9a2e6c1"
down_revision = ("b1e4f7c20a93", "d7b2f4a8c019")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
