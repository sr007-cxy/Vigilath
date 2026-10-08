"""Response-level flattened detail table ai_telemetry_response_detail (parsing layer)

Revision ID: e6b2f8d43a91
Revises: a9d4e7c21b58
Create Date: 2026-07-06 16:00:00.000000

Middle layer of the three-layer structure: responses (raw JSON) -> response_detail (parsed details, ~60 flattened columns)
-> report_daily (daily rollup). Competitors/citations/judgments are all split into real columns, queryable directly with SQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6b2f8d43a91'
down_revision: Union[str, None] = 'a9d4e7c21b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        # Dimensions
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("ts_bj", sa.DateTime(), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("scene_type", sa.String(length=16), nullable=False, server_default="unknown"),
        # Judgments
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("brand_rank", sa.Integer(), nullable=True),
        sa.Column("is_top1", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_top3", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("mention_position", sa.String(length=8), nullable=True),
        sa.Column("is_negative", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sentiment", sa.String(length=8), nullable=False, server_default="unknown"),
        sa.Column("answer_format", sa.String(length=32), nullable=True),
        # Competitors
        sa.Column("competitor_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comp_names_text", sa.Text(), nullable=False, server_default=""),
    ]
    for i in range(1, 6):
        cols.append(sa.Column(f"comp{i}_name", sa.String(length=128), nullable=True))
        cols.append(sa.Column(f"comp{i}_count", sa.Integer(), nullable=True))
    cols += [
        # Citations
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cites_owned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("owned_cite_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cite_domains_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("cite_urls_text", sa.Text(), nullable=False, server_default=""),
    ]
    for i in range(1, 6):
        cols.append(sa.Column(f"cite{i}_url", sa.Text(), nullable=True))
        cols.append(sa.Column(f"cite{i}_type", sa.String(length=32), nullable=True))
        cols.append(sa.Column(f"cite{i}_owned", sa.Boolean(), nullable=True))
    cols += [
        # Other
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("hit_excerpt", sa.Text(), nullable=True),
        sa.Column("answer_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="job"),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["response_id"], ["ai_telemetry_responses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["ai_telemetry_topics.id"], ondelete="CASCADE"),
    ]
    op.create_table("ai_telemetry_response_detail", *cols)
    with op.batch_alter_table("ai_telemetry_response_detail", schema=None) as batch_op:
        batch_op.create_index("uq_response_detail_resp", ["response_id"], unique=True)
        batch_op.create_index("idx_response_detail_topic_day", ["topic_id", "day"], unique=False)
        batch_op.create_index("idx_response_detail_topic_engine", ["topic_id", "engine"], unique=False)
        batch_op.create_index(batch_op.f("ix_ai_telemetry_response_detail_id"), ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("ai_telemetry_response_detail")
