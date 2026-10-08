"""add observed draft URL to publish dispatch queue

Revision ID: e9c5a7b3d102
Revises: d8b4f2a6c901
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "e9c5a7b3d102"
down_revision = "d8b4f2a6c901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publish_dispatch_tasks",
        sa.Column("draft_url", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publish_dispatch_tasks", "draft_url")
