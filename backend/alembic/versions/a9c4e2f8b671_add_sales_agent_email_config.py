"""Sales Agent mailbox config and delivery tracking

Revision ID: a9c4e2f8b671
Revises: e1a7f4c92d63
Create Date: 2026-08-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a9c4e2f8b671"
down_revision: Union[str, None] = "e1a7f4c92d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "sales_agent_email_events",
    "sales_agent_email_deliveries",
    "sales_agent_email_settings",
    "sales_agent_email_accounts",
)


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _indexes(name: str) -> set[str]:
    if not _has_table(name):
        return set()
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(name) if i.get("name")}


def _create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("sales_agent_email_accounts"):
        op.create_table(
            "sales_agent_email_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("provider_type", sa.String(length=32), nullable=False, server_default="smtp"),
            sa.Column("api_provider", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("from_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("from_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("reply_to_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("smtp_host", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
            sa.Column("smtp_security", sa.String(length=16), nullable=False, server_default="ssl"),
            sa.Column("smtp_username", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("smtp_secret_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("api_key_encrypted", sa.Text(), nullable=False, server_default=""),
            sa.Column("api_base_url", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("provider_config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("last_tested_at", sa.DateTime(), nullable=True),
            sa.Column("last_test_status", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("last_test_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _create_index("ix_sales_agent_email_accounts_id", "sales_agent_email_accounts", ["id"])
    _create_index("ix_sales_agent_email_accounts_user_id", "sales_agent_email_accounts", ["user_id"])
    _create_index("idx_sales_agent_email_accounts_scope_user", "sales_agent_email_accounts", ["scope", "user_id", "enabled"])
    _create_index("idx_sales_agent_email_accounts_default", "sales_agent_email_accounts", ["scope", "user_id", "is_default"])

    if not _has_table("sales_agent_email_settings"):
        op.create_table(
            "sales_agent_email_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("sending_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("tracking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("default_account_id", sa.Integer(), sa.ForeignKey("sales_agent_email_accounts.id"), nullable=True),
            sa.Column("sender_selection_policy", sa.String(length=32), nullable=False, server_default="default_only"),
            sa.Column("updated_by", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("scope", "user_id", name="ux_sales_agent_email_settings_scope_user"),
        )
    _create_index("ix_sales_agent_email_settings_id", "sales_agent_email_settings", ["id"])
    _create_index("ix_sales_agent_email_settings_user_id", "sales_agent_email_settings", ["user_id"])
    _create_index("idx_sales_agent_email_settings_scope_user", "sales_agent_email_settings", ["scope", "user_id"])

    if not _has_table("sales_agent_email_deliveries"):
        op.create_table(
            "sales_agent_email_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True),
            sa.Column("contact_id", sa.Integer(), sa.ForeignKey("sales_agent_company_contacts.id"), nullable=True),
            sa.Column("outreach_log_id", sa.Integer(), sa.ForeignKey("sales_agent_outreach_logs.id"), nullable=True),
            sa.Column("email_account_id", sa.Integer(), sa.ForeignKey("sales_agent_email_accounts.id"), nullable=True),
            sa.Column("report_share_id", sa.Integer(), sa.ForeignKey("sales_agent_report_shares.id"), nullable=True),
            sa.Column("message_type", sa.String(length=32), nullable=False, server_default="general_outreach"),
            sa.Column("provider_type", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("provider_message_id", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("from_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("from_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("reply_to_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("recipient_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("recipient_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("subject", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("content_preview", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
            sa.Column("failure_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("send_attempted_at", sa.DateTime(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("tracking_token", sa.String(length=96), nullable=False),
            sa.Column("first_opened_at", sa.DateTime(), nullable=True),
            sa.Column("last_opened_at", sa.DateTime(), nullable=True),
            sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_report_visit_at", sa.DateTime(), nullable=True),
            sa.Column("last_report_visit_at", sa.DateTime(), nullable=True),
            sa.Column("report_visit_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _create_index("ix_sales_agent_email_deliveries_id", "sales_agent_email_deliveries", ["id"])
    _create_index("ix_sales_agent_email_deliveries_user_id", "sales_agent_email_deliveries", ["user_id"])
    _create_index("ix_sales_agent_email_deliveries_lead_id", "sales_agent_email_deliveries", ["lead_id"])
    _create_index("ix_sales_agent_email_deliveries_company_id", "sales_agent_email_deliveries", ["company_id"])
    _create_index("ix_sales_agent_email_deliveries_contact_id", "sales_agent_email_deliveries", ["contact_id"])
    _create_index("ix_sales_agent_email_deliveries_outreach_log_id", "sales_agent_email_deliveries", ["outreach_log_id"])
    _create_index("ix_sales_agent_email_deliveries_email_account_id", "sales_agent_email_deliveries", ["email_account_id"])
    _create_index("ix_sales_agent_email_deliveries_report_share_id", "sales_agent_email_deliveries", ["report_share_id"])
    _create_index("idx_sales_agent_email_deliveries_user_status", "sales_agent_email_deliveries", ["user_id", "status"])
    _create_index("idx_sales_agent_email_deliveries_company_created", "sales_agent_email_deliveries", ["company_id", "created_at"])
    _create_index("idx_sales_agent_email_deliveries_draft", "sales_agent_email_deliveries", ["outreach_log_id"])
    _create_index("ux_sales_agent_email_deliveries_tracking", "sales_agent_email_deliveries", ["tracking_token"], unique=True)

    if not _has_table("sales_agent_email_events"):
        op.create_table(
            "sales_agent_email_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("sales_agent_email_deliveries.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("occurred_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    _create_index("ix_sales_agent_email_events_id", "sales_agent_email_events", ["id"])
    _create_index("ix_sales_agent_email_events_delivery_id", "sales_agent_email_events", ["delivery_id"])
    _create_index("ix_sales_agent_email_events_user_id", "sales_agent_email_events", ["user_id"])
    _create_index("ix_sales_agent_email_events_lead_id", "sales_agent_email_events", ["lead_id"])
    _create_index("ix_sales_agent_email_events_company_id", "sales_agent_email_events", ["company_id"])
    _create_index("idx_sales_agent_email_events_delivery", "sales_agent_email_events", ["delivery_id", "occurred_at"])
    _create_index("idx_sales_agent_email_events_user_type", "sales_agent_email_events", ["user_id", "event_type"])


def downgrade() -> None:
    for table in _TABLES:
        if not _has_table(table):
            continue
        for name in sorted(_indexes(table), reverse=True):
            op.drop_index(name, table_name=table)
        op.drop_table(table)
