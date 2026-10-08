"""Publish batch ledger + draft query snapshot (monitoring query soft delete only changes queries_json, no migration needed)

Revision ID: f3a9c7e21b48
Revises: e2f7b1c9d503
Create Date: 2026-08-17 12:00:00.000000

Two things:

1. Create topic_content_gen_batches: the fact layer of "which queries were selected for each generation trigger".
   Before it there was no authoritative record of this: drafts only had a single-value source_query_text (user-editable),
   plus a uuid pointing to a row of plan.publishing_plan_json, and that JSON is rewritten wholesale on every
   PUT / reorder / renewal, so the link breaks as soon as the row uuid changes.
   trace_id reuses llm_usage's batch trace_id (see services/gen_batch.py),
   so "how much this batch cost" can be joined directly on llm_calls.trace_id.

2. Add gen_batch_id / gen_queries_json to topic_generated_docs: which batch this draft belongs to and
   which monitoring queries it actually covered. Both columns are nullable, and **existing drafts are not backfilled**: no such snapshot was kept
   when they were generated, and recomputing from the current config is a guess, not a ledger; writing it anyway would be forgery.
   The frontend shows "—" for existing drafts.

Monitoring query soft delete (adding deleted_at / deleted_by_id inside queries_json) adds keys within JSON;
missing = not deleted, requiring neither a migration nor a backfill.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a9c7e21b48"
down_revision: Union[str, None] = "e2f7b1c9d503"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topic_content_gen_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(),
                  sa.ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False),
        # = the trace_id of that llm_usage batch; joining llm_calls on it gives this batch's model cost
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        # Empty = no human trigger (cron / Siyuan / failure retry). **Does not fall back to the topic owner**: this is an operations ledger,
        # and writing "Zhang San triggered a batch at 03:00" while Zhang San was asleep would be a forged record
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("trigger_source", sa.String(length=24), nullable=False,
                  server_default="unknown"),
        sa.Column("plan_id", sa.Integer(),
                  sa.ForeignKey("ai_telemetry_topic_execution_plans.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("queries_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("doc_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_json", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("idx_tcgb_topic_created", "topic_content_gen_batches",
                    ["topic_id", "created_at"])
    op.create_index("idx_tcgb_trace", "topic_content_gen_batches", ["trace_id"])

    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("gen_batch_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("gen_queries_json", sa.Text(), nullable=True))
        batch_op.create_index("idx_tgd_gen_batch", ["gen_batch_id"])
        batch_op.create_foreign_key(
            "fk_tgd_gen_batch", "topic_content_gen_batches",
            ["gen_batch_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("topic_generated_docs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tgd_gen_batch", type_="foreignkey")
        batch_op.drop_index("idx_tgd_gen_batch")
        batch_op.drop_column("gen_queries_json")
        batch_op.drop_column("gen_batch_id")
    op.drop_index("idx_tcgb_trace", table_name="topic_content_gen_batches")
    op.drop_index("idx_tcgb_topic_created", table_name="topic_content_gen_batches")
    op.drop_table("topic_content_gen_batches")
