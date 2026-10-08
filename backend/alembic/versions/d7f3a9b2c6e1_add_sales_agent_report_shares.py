"""Add a target company report share table for Sales Agent

A standalone Sales Agent module table; only adds a public share mapping and does not change full_diagnosis_reports access control.

Revision ID: d7f3a9b2c6e1
Revises: e4b7c9d21a56
Create Date: 2026-08-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7f3a9b2c6e1"
down_revision: Union[str, None] = "e4b7c9d21a56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "sales_agent_report_shares"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _indexes(name: str) -> set[str]:
    if not _has_table(name):
        return set()
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(name) if i.get("name")}


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("public_token", sa.String(length=64), nullable=False, comment="Sales Agent 外部报告访问随机 token"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="创建分享的用户 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, comment="对应 Sales Agent 线索 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=False, comment="目标企业 ID"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="full_diagnosis_reports", comment="真实报告来源表"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="真实报告 ID"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active", comment="分享状态:active/revoked"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.Column("expires_at", sa.DateTime(), nullable=True, comment="过期时间;为空表示暂不过期"),
        comment="Sales Agent 目标企业诊断报告公开分享映射表",
    )
    op.create_index("ix_sales_agent_report_shares_id", _TABLE, ["id"])
    op.create_index("ix_sales_agent_report_shares_user_id", _TABLE, ["user_id"])
    op.create_index("ix_sales_agent_report_shares_lead_id", _TABLE, ["lead_id"])
    op.create_index("ix_sales_agent_report_shares_company_id", _TABLE, ["company_id"])
    op.create_index("ux_sales_agent_report_shares_token", _TABLE, ["public_token"], unique=True)
    op.create_index("idx_sales_agent_report_shares_lead", _TABLE, ["lead_id", "status"])
    op.create_index("idx_sales_agent_report_shares_user_created", _TABLE, ["user_id", "created_at"])


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    indexes = _indexes(_TABLE)
    for name in (
        "idx_sales_agent_report_shares_user_created",
        "idx_sales_agent_report_shares_lead",
        "ux_sales_agent_report_shares_token",
        "ix_sales_agent_report_shares_company_id",
        "ix_sales_agent_report_shares_lead_id",
        "ix_sales_agent_report_shares_user_id",
        "ix_sales_agent_report_shares_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
