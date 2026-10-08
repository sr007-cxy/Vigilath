"""Add health fields to engine_sessions: login_ok / login_checked_at / last_success_at

The account pool used to judge "is this account usable" only by `expires_at`, an arbitrary 7-day TTL added at upload,
which does not reflect the real login session. Evidence on 2026-08-24: all 5 Doubao accounts were active and unexpired, yet the server returned
`user_id:0`; Qwen accounts dispatched opened as a 557-character anonymous page. The pool would happily keep dispatching dead accounts,
and since dead accounts fail fast, their use_count grows slowly, so `ORDER BY use_count ASC` dispatched them first.

- login_ok / login_checked_at: probe results (None = never probed); known dead accounts are excluded at dispatch
- last_success_at: the most recent real success. The table only had last_fail_type/last_fail_at,
  with no trace of "success" at all, so there was no way to tell whether an account was still working

Revision ID: d5e9b3a71c24
Revises: f1a7c3e9d284
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e9b3a71c24"
down_revision = "f1a7c3e9d284"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("engine_sessions", sa.Column("login_ok", sa.Boolean(), nullable=True))
    op.add_column("engine_sessions", sa.Column("login_checked_at", sa.DateTime(), nullable=True))
    op.add_column("engine_sessions", sa.Column("last_success_at", sa.DateTime(), nullable=True))
    # Dispatch filters by (engine, login_ok), so add a composite index
    op.create_index("ix_engine_sessions_engine_login_ok", "engine_sessions",
                    ["engine", "login_ok"])


def downgrade() -> None:
    op.drop_index("ix_engine_sessions_engine_login_ok", table_name="engine_sessions")
    op.drop_column("engine_sessions", "last_success_at")
    op.drop_column("engine_sessions", "login_checked_at")
    op.drop_column("engine_sessions", "login_ok")
