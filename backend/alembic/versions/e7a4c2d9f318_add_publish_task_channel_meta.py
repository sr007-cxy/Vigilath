"""Ruanwenjiang paid media channel: add channel_meta_json / cost_cents to publish_tasks

Revision ID: e7a4c2d9f318
Revises: d2b6f4a8c135
Create Date: 2026-07-28 12:00:00.000000

One paid placement (one draft x one resource) = one publish_task, with no separate order table:
channel_meta_json holds the resource snapshot / draft_id / order identifier / quote, and cost_cents records the actual payment (in cents).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a4c2d9f318'
down_revision: Union[str, None] = 'd2b6f4a8c135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topic_doc_publish_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("channel_meta_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("cost_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("topic_doc_publish_tasks", schema=None) as batch_op:
        batch_op.drop_column("cost_cents")
        batch_op.drop_column("channel_meta_json")
