"""Add an audit scope tier column to full_diagnosis_reports

full = run all eight (default, all existing rows get this); lite = only the four site-side checks.
When reports are read back it distinguishes "not audited this time" from "audit failed", and reuse/dedup is isolated by it too.

Revision ID: f3a1c85d2b74
Revises: b2f6a8d3c917
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a1c85d2b74"
down_revision = "b2f6a8d3c917"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if _has_column("full_diagnosis_reports", "scope"):
        return
    op.add_column(
        "full_diagnosis_reports",
        sa.Column("scope", sa.String(16), nullable=False,
                  server_default="full"),
    )


def downgrade() -> None:
    if _has_column("full_diagnosis_reports", "scope"):
        op.drop_column("full_diagnosis_reports", "scope")
