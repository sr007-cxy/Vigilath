"""add full_diagnosis_reports.query_pack_json

Manually specified query pack: when non-empty, the orchestration layer skips build_query_plan and uses these queries as-is.
Empty string/[] = old behavior (LLM planning + template fallback); existing rows are unaffected.

Revision ID: a4c7e1d93f52
Revises: f8c2d1a9b764
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a4c7e1d93f52'
down_revision: Union[str, None] = 'f8c2d1a9b764'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('full_diagnosis_reports') as batch:
        batch.add_column(sa.Column('query_pack_json', sa.Text(), nullable=False,
                                   server_default='[]'))


def downgrade() -> None:
    with op.batch_alter_table('full_diagnosis_reports') as batch:
        batch.drop_column('query_pack_json')
