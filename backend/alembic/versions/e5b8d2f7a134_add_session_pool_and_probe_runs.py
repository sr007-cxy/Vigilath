"""add engine_sessions.pool + workbench_probe_runs (workspace engine live test)

Revision ID: e5b8d2f7a134
Revises: a7c4e1f9b2d6
Create Date: 2026-07-17

pool column: account pool purpose tag. Default 'default' (the existing sentiment monitoring audit pool); the 5 dedicated accounts for the workspace "engine live test"
are tagged 'workbench'. check-out filters by pool; old callers that do not pass it -> only get
the default pool, behavior completely unchanged; workbench accounts are never claimed by sentiment monitoring batch runs.

workbench_probe_runs table: single-execution records of engine live tests (submit-poll model). Browser automation
takes 30s~300s per run, and synchronous forwarding would be cut off by nginx's default 60s timeout, so state goes in the DB and the frontend polls.
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b8d2f7a134"
down_revision = "a7c4e1f9b2d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "engine_sessions",
        sa.Column("pool", sa.String(), nullable=False, server_default="default"),
    )

    op.create_table(
        "workbench_probe_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("engine", sa.String(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations_json", sa.Text(), nullable=True),
        sa.Column("video_engine", sa.String(), nullable=True),
        sa.Column("video_filename", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_probe_runs_creator_status", "workbench_probe_runs",
        ["created_by", "status"],
    )


def downgrade():
    op.drop_index("idx_probe_runs_creator_status", table_name="workbench_probe_runs")
    op.drop_table("workbench_probe_runs")
    op.drop_column("engine_sessions", "pool")
