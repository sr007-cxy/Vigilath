"""Add tenant_id to dispatch_tasks: with the dispatch center standalone, internal batch runs also need tenant ownership

The three internal enqueue points for browser_jobs (runner/_job_enqueue_sync, main/_enqueue,
dispatch/jobs) already attach a kind=internal tenant; only dispatch_tasks, the **highest-volume**
main line, lacked this column: one batch run pours in hundreds of rows (2026-08-29, topic 20's Doubao column alone had 480),
none of which could be attributed to any tenant in the call log. For the dispatch center to become a first-class service, "who is running this task"
must use the same metric definition internally and externally, otherwise internal volume is invisible when counting/rate-limiting/sorting by tenant.

A string column is not a typo: it matches browser_jobs.tenant_id; tenant ids from the external gateway are also stored as
strings, and only same-typed columns let the dispatch center union a unified task view across both tables.

nullable=True with no server_default: existing rows stay NULL meaning "enqueued before wiring, ownership unknown",
more honest than faking a tenant; newly enqueued rows get it attached by drip.

Revision ID: d4a7f2c93b18
Revises: c8e4a17b3d92
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a7f2c93b18"
down_revision = "c8e4a17b3d92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dispatch_tasks", sa.Column("tenant_id", sa.String(), nullable=True))
    # Index: filtering tasks by tenant is a routine query in the dispatch center, and this is one of the most write-heavy tables in the system;
    # without an index every tenant filter is a full table scan.
    op.create_index("ix_dispatch_tasks_tenant_id", "dispatch_tasks", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_dispatch_tasks_tenant_id", table_name="dispatch_tasks")
    op.drop_column("dispatch_tasks", "tenant_id")
