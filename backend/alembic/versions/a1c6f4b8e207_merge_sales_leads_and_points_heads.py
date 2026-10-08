"""Merge two heads: ensure_sales_leads_table and batch_cell_20_points

Pure merge, no table changes. Same reason as `b9f4c1a7d602_merge_heads.py`: the deploy script
(deploy/deploy-systemd.sh:100) runs `alembic upgrade head`, and with two heads in the repo
the command is ambiguous and fails outright.

Both heads fork from `d4b9e2a7c318`: `f3a9c1d84b26` set its down_revision assuming "d4b9e2a7c318 is the repo's
only committed head", but the `d4b1f8c62a97` branch also hangs off the same parent,
creating a second head.

Revision ID: a1c6f4b8e207
Revises: f3a9c1d84b26, b4d7e9c2a851
"""

revision = "a1c6f4b8e207"
down_revision = ("f3a9c1d84b26", "b4d7e9c2a851")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
