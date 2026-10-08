"""add ai_telemetry_topics.brand_tier / industry_class (report center brand tags)

Revision ID: c5e8a2d94f17
Revises: e8c3a1b5f240, f7d3b9a2e6c1
Create Date: 2026-07-05 10:00:00.000000

Report center "tag your own account's brand":
  brand_tier      "" / new (new brand) / mid (mid-size brand) / leader (industry leader)
  industry_class  "" / regular (regular industry) / special (special industry: healthcare, beauty, finance)
The report page matches the same-tier exposure rate reference range accordingly; "" = not set.

Also serves as a merge revision: converges the current dual heads (e8c3a1b5f240 engine proxy table /
f7d3b9a2e6c1 previous merge), avoiding upgrade head errors from multiple heads.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e8a2d94f17'
down_revision: Union[str, Sequence[str], None] = ('e8c3a1b5f240', 'f7d3b9a2e6c1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("brand_tier", sa.String(length=16),
                      nullable=False, server_default=""),
        )
        batch_op.add_column(
            sa.Column("industry_class", sa.String(length=16),
                      nullable=False, server_default=""),
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.drop_column("industry_class")
        batch_op.drop_column("brand_tier")
