"""Add a (run_id, engine, query) unique constraint to dispatch_tasks: guard against the race of multiple instances filling at once

test host and prod share one appdb, each running a telemetry. Currently only prod fills tasks, relying on
SCHEDULER_ENABLED=0 in the test host systemd unit; this env used to default to "1",
so anyone reinstalling the unit or missing the config once would make both sides fill at the same time.

The drip side already has an advisory lock (_DRIP_LOCK_KEY), but that only works on the same PG and requires all code
to take that path; the unique constraint is the last fallback: a real collision becomes an insert conflict rather than a silent extra
duplicate task (the same question asked twice, quota burned twice).

Zero duplicates in existing data confirmed before launch:
    SELECT COUNT(*) FROM (SELECT run_id, engine, query, COUNT(*) n
                          FROM dispatch_tasks GROUP BY 1,2,3 HAVING COUNT(*)>1) x;
    -> 0 groups

query is String(1024); PG's btree index entry limit is about 2704 bytes, and the three columns together will not exceed it.

Revision ID: e9d3a1f7c584
Revises: f2b1c9d8a046
"""
from alembic import op

revision = "e9d3a1f7c584"
down_revision = "f2b1c9d8a046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The early create-table migration f3a9c1d2e4b7 already created a unique index of the same name. Some historical databases may have
    # schema drift and lack it, so the repair intent is kept here, but it must be compatible with the full migration chain and existing DBs.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_dispatch_tasks_run_engine_query "
        "ON dispatch_tasks (run_id, engine, query)"
    )


def downgrade() -> None:
    # This index is owned by f3a9c1d2e4b7; downgrading this revision must not delete baseline structure.
    pass
