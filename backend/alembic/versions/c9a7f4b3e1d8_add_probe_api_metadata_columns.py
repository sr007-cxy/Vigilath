"""add workbench_probe_runs api metadata columns (transport/provider/model/endpoint_host)

Revision ID: c9a7f4b3e1d8
Revises: b7c4e9a2d615
Create Date: 2026-09-02

The backend's overseas API branch (workspace engine live test) writes these four columns, but the ORM change had no corresponding migration.
All four columns are nullable with no server_default: old rows (CN browser live tests) stay NULL,
which the frontend uses to tell "was this run by browser or by API".
"""
from alembic import op
import sqlalchemy as sa

revision = "c9a7f4b3e1d8"
down_revision = "b7c4e9a2d615"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workbench_probe_runs", sa.Column("transport", sa.String(), nullable=True))
    op.add_column("workbench_probe_runs", sa.Column("provider", sa.String(), nullable=True))
    op.add_column("workbench_probe_runs", sa.Column("model", sa.String(), nullable=True))
    op.add_column("workbench_probe_runs", sa.Column("endpoint_host", sa.String(), nullable=True))


def downgrade():
    op.drop_column("workbench_probe_runs", "endpoint_host")
    op.drop_column("workbench_probe_runs", "model")
    op.drop_column("workbench_probe_runs", "provider")
    op.drop_column("workbench_probe_runs", "transport")
