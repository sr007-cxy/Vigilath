"""add visible_after to dispatch_tasks + browser_jobs (transient backoff)

Revision ID: d7b2f4a8c019
Revises: c5e9a1f3b720
Create Date: 2026-06-30

When a transient failure (muted / could not grab a slot) returns a task to the queue, visible_after is set, and claim skips tasks not yet due, preventing hot loops.
Note: the shared appdb's alembic chain historically lagged behind; columns were actually added idempotently with ALTER TABLE ... ADD COLUMN IF NOT EXISTS,
and this file is only a record.
"""
from alembic import op
import sqlalchemy as sa

revision = "d7b2f4a8c019"
down_revision = "c5e9a1f3b720"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dispatch_tasks", sa.Column("visible_after", sa.DateTime(), nullable=True))
    op.add_column("browser_jobs", sa.Column("visible_after", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("browser_jobs", "visible_after")
    op.drop_column("dispatch_tasks", "visible_after")
