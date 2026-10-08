"""add llm_call_log + llm_usage_daily (model call accounting)

Revision ID: c7f2a91d4e63
Revises: b3c9e1f7a248
Create Date: 2026-08-13

Discovered on 2026-08-13 when the DeepSeek key balance ran out (402): the application layer kept no record of LLM API calls,
and the only total was on the third-party platform's usage page. These two tables fill that gap: one detail row per call (kept 90 days),
daily rollups kept forever.

ORM in geo/models/llm_usage.py, writes via geo/services/llm_usage.py,
rollup/cleanup via geo/services/llm_usage_rollup.py, page in geo/api/admin_llm_usage.py.

Note: this repo hand-writes migrations and does not run autogenerate (geo/__init__.py only registers some models,
so autogenerate would emit drop_table for unregistered tables).
If indexes are later added to these two tables, PG requires CONCURRENTLY, while alembic runs inside a transaction by default:
use with op.get_context().autocommit_block():.
"""
from alembic import op
import sqlalchemy as sa

revision = "c7f2a91d4e63"
down_revision = "b3c9e1f7a248"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_call_log",
        # SQLite's rowid alias only recognizes INTEGER PRIMARY KEY; BIGINT loses autoincrement
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bj_date", sa.String(length=10), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False, server_default="backend"),
        sa.Column("host", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("feature", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("endpoint_host", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("call_kind", sa.String(length=12), nullable=False, server_default="chat"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("error_kind", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("http_status", sa.Integer(), nullable=True),
        # Token columns are deliberately nullable with **no server_default**: NULL = not obtained, 0 = provider explicitly returned 0
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("token_source", sa.String(length=10), nullable=False, server_default="none"),
        sa.Column("requests_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("ref_type", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("call_group_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("est_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("extra_json", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("llm_call_log") as batch_op:
        batch_op.create_index("idx_llm_call_created", ["created_at"])
        batch_op.create_index("idx_llm_call_day_feature", ["bj_date", "feature"])
        batch_op.create_index("idx_llm_call_day_service", ["bj_date", "service"])
        batch_op.create_index("idx_llm_call_user", ["user_id"])
        batch_op.create_index("idx_llm_call_trace", ["trace_id"])

    op.create_table(
        "llm_usage_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bj_date", sa.String(length=10), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("call_kind", sa.String(length=12), nullable=False, server_default="chat"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("calls_with_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_without_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms_sum", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  nullable=False, server_default="0"),
        sa.Column("users_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="rollup"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("bj_date", "service", "feature", "provider", "model", "call_kind",
                            name="uq_llm_usage_daily_cell"),
    )
    with op.batch_alter_table("llm_usage_daily") as batch_op:
        batch_op.create_index("idx_llm_usage_daily_day", ["bj_date"])
        batch_op.create_index("idx_llm_usage_daily_feature", ["feature", "bj_date"])


def downgrade():
    op.drop_table("llm_usage_daily")
    op.drop_table("llm_call_log")
