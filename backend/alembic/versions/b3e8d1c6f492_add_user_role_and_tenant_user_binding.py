"""add users.role + api_tenants.user_id

Revision ID: b3e8d1c6f492
Revises: d7f2a9c4e816
Create Date: 2026-07-13 12:00:00.000000

Multi-role permission system + gateway tenant portal:
- users.role (admin/operator/user/tenant, default user) becomes the single source of truth for permissions;
  is_admin is kept as a compatibility column maintained in sync; existing is_admin=1 rows are backfilled with role='admin'.
- api_tenants.user_id binds a platform account; once bound, that account can log in to the web tenant portal.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e8d1c6f492'
down_revision: Union[str, None] = 'd7f2a9c4e816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(), nullable=False, server_default="user"),
        )
        batch_op.create_index("ix_users_role", ["role"])

    # Backfill existing admins (boolean bind parameter, safe for both SQLite/PG dialects)
    op.execute(
        sa.text("UPDATE users SET role = 'admin' WHERE is_admin = :t").bindparams(t=True)
    )

    with op.batch_alter_table("api_tenants", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_api_tenants_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("api_tenants", schema=None) as batch_op:
        batch_op.drop_index("ix_api_tenants_user_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ix_users_role")
        batch_op.drop_column("role")
