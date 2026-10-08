"""Add run_id / topic_id to browser_jobs: internal batch runs enqueue via the external gateway

Internal batch runs used to INSERT into dispatch_tasks directly, bypassing the /v1 gateway entirely: quotas, balances, billing and
audit never applied to them, and the ops page's "call log" (which only reads browser_jobs) never
showed batch runs. After the change, drip enqueues via the gateway into browser_jobs, but batch-run results must still
go through the hit detection pipeline (detect_hit / save_response -> ai_telemetry_responses), so
browser_jobs must carry the two foreign-key semantics of the sentiment monitoring domain.

Both columns are nullable:
  - jobs from external tenants are always NULL, exactly as today;
  - batch-run jobs have both set, and dispatch._result_job routes them to hit detection accordingly.

**No foreign keys**. The two FKs on dispatch_tasks existed from table creation; browser_jobs is an externally facing
hot table, and adding FKs for two nullable columns would take locks on a hot production table for limited constraint value (there is
only one write point, drip, and run_id comes from a just-flushed RunORM). Indexes are still created: completion checks count
outstanding by run_id and backlog by topic_id, and both queries are hot paths once batch runs are switched on.

No separate constraint for idempotency: batch-run enqueueing reuses browser_jobs' existing idempotency_key unique index,
with keys like `batch:<run_id>:<engine>:<sha1(query)[:16]>`, the same semantics as
uq_dispatch_tasks_run_engine_query(run_id, engine, query) on dispatch_tasks: when multiple instances fill at once
it becomes an insert conflict rather than a silent duplicate task.

Revision ID: c4f7a2e91b58
Revises: d5a3e8c71f42
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a2e91b58"
down_revision: Union[str, None] = "d5a3e8c71f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("browser_jobs")}


def _indexes(bind) -> set:
    return {i["name"] for i in sa.inspect(bind).get_indexes("browser_jobs")}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _cols(bind)
    # Idempotent: this migration set runs only once on the appdb shared by test host / prod, but both sides' code must start,
    # and rerunning on environments where columns were added manually must not blow up (same approach as d4a7f2c93b18).
    if "run_id" not in cols:
        op.add_column("browser_jobs", sa.Column("run_id", sa.Integer(), nullable=True))
    if "topic_id" not in cols:
        op.add_column("browser_jobs", sa.Column("topic_id", sa.Integer(), nullable=True))
    idx = _indexes(bind)
    if "ix_browser_jobs_run_id" not in idx:
        op.create_index("ix_browser_jobs_run_id", "browser_jobs", ["run_id"])
    if "ix_browser_jobs_topic_id" not in idx:
        op.create_index("ix_browser_jobs_topic_id", "browser_jobs", ["topic_id"])


def downgrade() -> None:
    bind = op.get_bind()
    idx = _indexes(bind)
    if "ix_browser_jobs_topic_id" in idx:
        op.drop_index("ix_browser_jobs_topic_id", table_name="browser_jobs")
    if "ix_browser_jobs_run_id" in idx:
        op.drop_index("ix_browser_jobs_run_id", table_name="browser_jobs")
    cols = _cols(bind)
    if "topic_id" in cols:
        op.drop_column("browser_jobs", "topic_id")
    if "run_id" in cols:
        op.drop_column("browser_jobs", "run_id")
