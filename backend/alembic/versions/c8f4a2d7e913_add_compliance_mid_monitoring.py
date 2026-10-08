"""Compliance audit center P1 in-flight monitoring: three tables for alerts / tickets / monitor state.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md chapter 6.

  - compliance_alerts         answer-side violation alerts (detected by the lexicon layer + LLM semantic layer)
  - compliance_tickets        handling tickets (opened when an alert is confirmed; vehicle for the re-test closed loop)
  - compliance_monitor_state  monitoring cursor KV (response_watermark)

alerts.ticket_id has no FK (tickets.alert_id already has the reverse FK; both directions would be a circular dependency).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f4a2d7e913'
down_revision: Union[str, None] = 'b6e2d9a4c1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compliance_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("response_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("engine", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("detected_by", sa.String(length=16), nullable=False, server_default="lexicon"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("channel", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("handled_by_id", sa.Integer(), nullable=True),
        sa.Column("handled_by_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("found_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_comp_alert_topic_status", "compliance_alerts", ["topic_id", "status"])
    op.create_index("idx_comp_alert_response", "compliance_alerts", ["response_id"])
    op.create_index("idx_comp_alert_dedup", "compliance_alerts", ["dedup_key"], unique=True)

    op.create_table(
        "compliance_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_no", sa.String(length=16), nullable=False),
        sa.Column("alert_id", sa.Integer(),
                  sa.ForeignKey("compliance_alerts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("assignee_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("retest_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("clean_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overdue_notified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("opened_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_action_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_comp_ticket_topic", "compliance_tickets", ["topic_id"])
    op.create_index("idx_comp_ticket_no", "compliance_tickets", ["ticket_no"], unique=True)
    op.create_index("idx_comp_ticket_status", "compliance_tickets", ["status"])

    op.create_table(
        "compliance_monitor_state",
        sa.Column("key", sa.String(length=48), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("compliance_monitor_state")
    op.drop_index("idx_comp_ticket_status", table_name="compliance_tickets")
    op.drop_index("idx_comp_ticket_no", table_name="compliance_tickets")
    op.drop_index("idx_comp_ticket_topic", table_name="compliance_tickets")
    op.drop_table("compliance_tickets")
    op.drop_index("idx_comp_alert_dedup", table_name="compliance_alerts")
    op.drop_index("idx_comp_alert_response", table_name="compliance_alerts")
    op.drop_index("idx_comp_alert_topic_status", table_name="compliance_alerts")
    op.drop_table("compliance_alerts")
