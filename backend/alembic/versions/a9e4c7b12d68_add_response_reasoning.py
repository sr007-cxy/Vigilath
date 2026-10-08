"""Add reasoning to ai_telemetry_responses / browser_jobs: store the engine's chain of thought in its own column

User request on 2026-09-16: collection must capture the engine's "deep thinking" section and hit detection must include it; for all engines.
Before this no engine captured that section (0 of 566 Qwen answers in the last 7 days contained thinking content, and deepseek explicitly had
thinking_enabled=False). Stored separately rather than appended to answer: the body is for customers and reports
count words/format on it, while the thinking process only participates in hit detection and is shown collapsed in the frontend.

NULL = not captured (old data / engine thinking off / capture failed), '' = captured but this answer had no thinking section.
Both tables add columns idempotently: appdb is one DB shared by test host / prod, run once, and both sides' code must start
(same approach as c4f7a2e91b58 / e2b7c4d91a56).

Revision ID: a9e4c7b12d68
Revises: c9a3f7e1d520
Create Date: 2026-09-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9e4c7b12d68"
down_revision: Union[str, None] = "c9a3f7e1d520"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("ai_telemetry_responses", "browser_jobs")


def _cols(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for t in _TABLES:
        cols = _cols(bind, t)
        if cols and "reasoning" not in cols:
            op.add_column(t, sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for t in _TABLES:
        if "reasoning" in _cols(bind, t):
            op.drop_column(t, "reasoning")
