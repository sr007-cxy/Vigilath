"""Re-create sales_leads: the CN site production DB lacked the table, and /contact-sales returned 500 on every submit

baseline (43685ae506e6) created sales_leads, but the CN site production appdb evolved from elsewhere and
baseline was stamped without actually creating the table: on 2026-08-25 pg_tables was found to lack it,
so not a single lead from the product page's "contact sales" could be persisted.

Idempotent: skipped if the table exists; new DBs/test DBs are unaffected.

Revision ID: f3a9c1d84b26
Revises: d4b9e2a7c318
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a9c1d84b26"
down_revision = "d4b9e2a7c318"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("sales_leads"):
        return
    op.create_table(
        "sales_leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("tier_slug", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_leads_id", "sales_leads", ["id"], unique=False)


def downgrade() -> None:
    # Only roll back the table this migration created; in other environments it belongs to baseline and is not deleted here.
    if _has_table("sales_leads"):
        op.drop_index("ix_sales_leads_id", table_name="sales_leads")
        op.drop_table("sales_leads")
