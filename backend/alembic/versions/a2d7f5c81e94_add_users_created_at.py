"""Add created_at to users: admin user management needs the registration time

The workspace's user detail panel needs to answer "when did this person register", but since baseline users
has only identity columns and no time columns, so registration time was never persisted to DB.

Existing rows **stay NULL**; no time is fabricated: users has no corroborating column to infer registration time from
(the earliest row in detection_records / payment_sessions is only "first use", not registration),
and backfilling from it would produce data that looks precise but is made up. The frontend renders NULL as "—".

Revision ID: a2d7f5c81e94
Revises: d5f1a7b93c48
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "a2d7f5c81e94"
down_revision = "d5f1a7b93c48"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # Idempotent: the CN site production DB was historically stamped and drifted from migration files, so check before adding the column
    if _has_column("users", "created_at"):
        return
    op.add_column("users", sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "created_at"):
        op.drop_column("users", "created_at")
