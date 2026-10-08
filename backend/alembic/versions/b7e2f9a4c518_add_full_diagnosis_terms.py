"""One-click full diagnosis: industry term / brand term columns

Revision ID: b7e2f9a4c518
Revises: a3f8d1c6e259
Create Date: 2026-07-30 17:00:00.000000

The entry form now has users fill in industry terms + brand terms, and audit queries are generated from these two sets
(industry terms -> ranking questions, brand terms -> reputation questions), no longer using preset English queries.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2f9a4c518'
down_revision: Union[str, None] = 'a3f8d1c6e259'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("full_diagnosis_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("industry_terms_json", sa.Text(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("brand_terms_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("full_diagnosis_reports", schema=None) as batch_op:
        batch_op.drop_column("brand_terms_json")
        batch_op.drop_column("industry_terms_json")
