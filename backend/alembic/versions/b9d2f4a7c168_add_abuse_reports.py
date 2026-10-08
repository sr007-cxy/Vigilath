"""add abuse_reports (report/complaint tickets)

Revision ID: b9d2f4a7c168
Revises: e7a3c5d81f42
Create Date: 2026-08-16 17:00:00.000000

Algorithm filing remediation list item 13: report/complaint entry and handling closed loop.
Design in docs/09-软著与专利/备案补正/02-方案与代办台账.md §4.6.

Anonymous submissions are allowed, so reporter_user_id is nullable without a foreign key (reporters are mostly not our users,
and a foreign key would cascade-delete reports when a user is deleted; those are handling evidence that must be kept on file).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d2f4a7c168"
down_revision: Union[str, None] = "e7a3c5d81f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "abuse_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_no", sa.String(length=48), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False, server_default="other"),
        sa.Column("target_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("target_trace_no", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("attachment_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("attachment_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("reporter_user_id", sa.Integer(), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="received"),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("handling", sa.Text(), nullable=False, server_default=""),
        sa.Column("reply", sa.Text(), nullable=False, server_default=""),
        sa.Column("handler_id", sa.Integer(), nullable=True),
        sa.Column("handler_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_abuse_reports_id", "abuse_reports", ["id"])
    op.create_index("idx_abuse_report_no", "abuse_reports", ["report_no"], unique=True)
    op.create_index("idx_abuse_status", "abuse_reports", ["status"])
    op.create_index("idx_abuse_created", "abuse_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_abuse_created", table_name="abuse_reports")
    op.drop_index("idx_abuse_status", table_name="abuse_reports")
    op.drop_index("idx_abuse_report_no", table_name="abuse_reports")
    op.drop_index("ix_abuse_reports_id", table_name="abuse_reports")
    op.drop_table("abuse_reports")
