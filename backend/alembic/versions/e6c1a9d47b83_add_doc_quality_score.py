"""Multi-agent refined writing review results: add quality_score / quality_json to topic_generated_docs

Revision ID: e6c1a9d47b83
Revises: d3f8b1c6a742
Create Date: 2026-08-04 22:05:00.000000

The generation side now has a multi-agent pipeline (planning -> writing -> review -> revision, services/content_quality.py).
The review agent's output must be persisted to DB, otherwise:
  - ops cannot see on the content review page "which drafts fall short and where", and must read them one by one;
  - the template scorecard only has the lagging "citation rate" metric (which takes 7 days to settle), with no leading quality metric.

quality_score is a 5-dimension weighted total (question coverage 30 / citability 25 / fact density 15 / brand 15 / formatting 15),
and quality_json stores details: dims, uncovered (monitoring questions not covered), blocking, fixes, rounds, notes.
Both columns are nullable: empty when the pipeline is off (GEO_CONTENT_QUALITY=0) or the review agent fails.

Hand-written rather than autogenerate: geo/__init__.py does not register all models, so autogenerate would emit
drop_table for unregistered tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6c1a9d47b83'
down_revision: Union[str, None] = 'd3f8b1c6a742'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('topic_generated_docs') as b:
        b.add_column(sa.Column('quality_score', sa.Integer(), nullable=True))
        b.add_column(sa.Column('quality_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('topic_generated_docs') as b:
        b.drop_column('quality_json')
        b.drop_column('quality_score')
