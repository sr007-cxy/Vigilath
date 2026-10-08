"""merge: f3a9c7e21b48 (publish batch ledger) + b8d4e6a2c917 (two external image moderation columns)

Both forked from e2f7b1c9d503; without closing them `alembic upgrade head` reports
Multiple head revisions and the service cannot start. This one only closes them, no DDL.

Revision ID: e4b7c9d21a56
"""
from __future__ import annotations

revision = "e4b7c9d21a56"
down_revision = ("b8d4e6a2c917", "f3a9c7e21b48")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
