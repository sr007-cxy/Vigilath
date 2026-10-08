"""Add kind / source_key / created_by to api_tenants: classify tenants by purpose + traceable origin

Cause: anyone could stuff rows into the tenants table (admin buttons, manual scripts, direct SQL) without any purpose marker;
test tenants (like ops-verify) were mixed with real customers in one table, and who created them could not be traced.

kind        customer = external customer / internal = own systems / test = integration testing
source_key  stable key for internal tenants (internal:batch etc.), unique: supports idempotent get-or-create
created_by  who created it (admin email / script name / auto)

Revision ID: a7d2e4f91c83
Revises: b9f4c1a7d602 (merge migration that brought the repo back to a single head)
"""
from alembic import op
import sqlalchemy as sa

revision = "a7d2e4f91c83"
down_revision = "b9f4c1a7d602"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return True          # Table not created yet (fresh DB) -> leave it to the create-table migration; skip here
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("api_tenants", "kind"):
        op.add_column("api_tenants", sa.Column(
            "kind", sa.String(), nullable=False, server_default="customer"))
    if not _has_column("api_tenants", "source_key"):
        op.add_column("api_tenants", sa.Column("source_key", sa.String(), nullable=True))
        op.create_index("ix_api_tenants_source_key", "api_tenants", ["source_key"], unique=True)
    if not _has_column("api_tenants", "created_by"):
        op.add_column("api_tenants", sa.Column("created_by", sa.String(), nullable=True))

    # Classify existing rows: tier=internal ones (e.g. 「自有系统」 "own systems") are own systems going through the external gateway
    op.execute("UPDATE api_tenants SET kind = 'internal' WHERE tier = 'internal'")


def downgrade() -> None:
    for col in ("created_by", "source_key", "kind"):
        if _has_column("api_tenants", col):
            if col == "source_key":
                op.drop_index("ix_api_tenants_source_key", table_name="api_tenants")
            op.drop_column("api_tenants", col)
