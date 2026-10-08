"""Add an audit query language column to full_diagnosis_reports

zh / en; empty string = not chosen by the user (falls back to `_lang()` at generation time, following REGION).
Same family as scope: the orchestration layer's `_run()` only reads parameters from the DB, so it must be persisted; reuse/dedup is also isolated
by it, otherwise resubmitting the same form in another language would hit the old generating row and return a report in the old language.

Existing rows all get an empty string: the meaning is "unspecified", handled with the environment default at generation time, so reruns of
existing reports do not change.

Revision ID: c2b8f4a91d37
Revises: f3a1c85d2b74
"""
from alembic import op
import sqlalchemy as sa

revision = "c2b8f4a91d37"
down_revision = "f3a1c85d2b74"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if _has_column("full_diagnosis_reports", "query_lang"):
        return
    op.add_column(
        "full_diagnosis_reports",
        sa.Column("query_lang", sa.String(8), nullable=False,
                  server_default=""),
    )


def downgrade() -> None:
    if _has_column("full_diagnosis_reports", "query_lang"):
        op.drop_column("full_diagnosis_reports", "query_lang")
