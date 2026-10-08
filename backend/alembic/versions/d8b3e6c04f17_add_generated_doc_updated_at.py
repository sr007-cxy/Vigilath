"""Add updated_at to topic_generated_docs (regeneration overwrites in place and needs a separate update time)

Revision ID: d8b3e6c04f17
Revises: c4a7f1e9b306
Create Date: 2026-08-10

Regeneration is an upsert on the triple (plan_item_id, creative direction, copy type), so the doc id and
created_at stay the same; with only created_at, a draft rewritten today still sorts under the day it was originally created
in article management, and the list gives no sign it was touched.

Existing rows are backfilled with created_at (their body really did come from that moment); afterwards the ORM's
onupdate maintains it.
"""
from alembic import op
import sqlalchemy as sa


revision = "d8b3e6c04f17"
down_revision = "c4a7f1e9b306"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "topic_generated_docs",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE topic_generated_docs SET updated_at = created_at "
        "WHERE updated_at IS NULL"
    )


def downgrade():
    op.drop_column("topic_generated_docs", "updated_at")
