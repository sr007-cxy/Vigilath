"""Re-queue failed article generation: add gen_attempts / gen_next_retry_at to topic_generated_docs

Revision ID: d3f8b1c6a742
Revises: c4a7e2b9d631
Create Date: 2026-08-04 21:10:00.000000

Generation failure used to be terminal: status went back to pending_review, the cause was written to generation_error, and nobody touched it again
(cron's automatic batch run takes a separate path through the query pool and never comes back for failed plan rows). These two columns turn the failed draft row
itself into a queue item: when gen_next_retry_at is due, services/gen_retry.retry_tick regenerates it,
gen_attempts counts retries, and it stops after exceeding GEO_GEN_RETRY_MAX.

No separate queue table: keeping queue items consistent with draft row state (manually regenerated / deleted / reviewed) is not worth maintaining twice.

Hand-written rather than autogenerate: geo/__init__.py does not register all models, so autogenerate would emit
drop_table for unregistered tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f8b1c6a742'
down_revision: Union[str, None] = 'c4a7e2b9d631'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('topic_generated_docs') as b:
        b.add_column(sa.Column('gen_attempts', sa.Integer(), nullable=False,
                               server_default='0'))
        b.add_column(sa.Column('gen_next_retry_at', sa.DateTime(), nullable=True))
    op.create_index('ix_topic_generated_docs_gen_next_retry_at',
                    'topic_generated_docs', ['gen_next_retry_at'])


def downgrade() -> None:
    op.drop_index('ix_topic_generated_docs_gen_next_retry_at',
                  table_name='topic_generated_docs')
    with op.batch_alter_table('topic_generated_docs') as b:
        b.drop_column('gen_next_retry_at')
        b.drop_column('gen_attempts')
