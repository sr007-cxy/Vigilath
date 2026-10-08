"""One-click full diagnosis: full_diagnosis_reports table

Revision ID: a3f8d1c6e259
Revises: e7a4c2d9f318
Create Date: 2026-07-30 12:00:00.000000

Enter a URL + brand name once, and the backend runs all 7 audits + four-dimension scoring + LLM narrative,
producing a 9-chapter comprehensive report. Task state (generating/ready/failed) lives in the DB for multi-worker polling.
subresults_json can reach hundreds of KB (full visibility data); sqlite Text has no limit;
a future switch to MySQL would need MEDIUMTEXT.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f8d1c6e259'
down_revision: Union[str, None] = 'e7a4c2d9f318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "full_diagnosis_reports",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="generating"),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("brand_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("competitor_domains_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("subresults_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("scores_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("narrative_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("llm_model", sa.String(64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("full_diagnosis_reports")
