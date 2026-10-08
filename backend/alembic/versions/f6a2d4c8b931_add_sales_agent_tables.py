"""add sales agent MVP tables

Standalone Sales Agent module tables, uniformly prefixed sales_agent_. Only adds tables, existing data unchanged.

Revision ID: f6a2d4c8b931
Revises: c9f1a4d7b230
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6a2d4c8b931"
down_revision: Union[str, None] = "c9f1a4d7b230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_agent_chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("public_id", sa.String(length=36), nullable=False, comment="前端暴露的会话 UUID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="Sales Agent 会话", comment="会话标题"),
        sa.Column("current_intent", sa.String(length=64), nullable=False, server_default="", comment="当前识别到的销售意图"),
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}", comment="会话上下文 JSON 字符串"),
        sa.Column("messages_json", sa.Text(), nullable=False, server_default="[]", comment="pydantic-ai 原始消息历史 JSON 字符串"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 聊天会话表",
    )
    op.create_index("ix_sales_agent_chat_sessions_id", "sales_agent_chat_sessions", ["id"])
    op.create_index("ix_sales_agent_chat_sessions_user_id", "sales_agent_chat_sessions", ["user_id"])
    op.create_index("idx_sales_agent_sessions_user_updated", "sales_agent_chat_sessions", ["user_id", "updated_at"])
    op.create_index("ux_sales_agent_sessions_public_id", "sales_agent_chat_sessions", ["public_id"], unique=True)

    op.create_table(
        "sales_agent_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sales_agent_chat_sessions.id"), nullable=False, comment="所属 Sales Agent 会话 ID"),
        sa.Column("role", sa.String(length=16), nullable=False, comment="消息角色:user/assistant/tool"),
        sa.Column("content", sa.Text(), nullable=False, server_default="", comment="消息正文"),
        sa.Column("intent", sa.String(length=64), nullable=False, server_default="", comment="识别到的销售意图"),
        sa.Column("tool_name", sa.String(length=128), nullable=False, server_default="", comment="关联工具名称"),
        sa.Column("tool_result_id", sa.String(length=128), nullable=False, server_default="", comment="关联工具结果 ID"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}", comment="消息元数据 JSON 字符串"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 聊天消息表",
    )
    op.create_index("ix_sales_agent_chat_messages_id", "sales_agent_chat_messages", ["id"])
    op.create_index("ix_sales_agent_chat_messages_session_id", "sales_agent_chat_messages", ["session_id"])
    op.create_index("idx_sales_agent_messages_session", "sales_agent_chat_messages", ["session_id", "created_at"])

    op.create_table(
        "sales_agent_customer_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表名"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID"),
        sa.Column("report_snapshot", sa.Text(), nullable=False, server_default="{}", comment="生成画像时的报告摘要快照 JSON 字符串"),
        sa.Column("service_scope_json", sa.Text(), nullable=False, server_default="[]", comment="服务范围 JSON 字符串"),
        sa.Column("industries_json", sa.Text(), nullable=False, server_default="[]", comment="目标行业 JSON 字符串"),
        sa.Column("regions_json", sa.Text(), nullable=False, server_default="[]", comment="目标地区 JSON 字符串"),
        sa.Column("company_size", sa.String(length=128), nullable=False, server_default="", comment="目标客户规模"),
        sa.Column("buyer_roles_json", sa.Text(), nullable=False, server_default="[]", comment="目标采购/决策角色 JSON 字符串"),
        sa.Column("exclude_rules_json", sa.Text(), nullable=False, server_default="[]", comment="排除规则 JSON 字符串"),
        sa.Column("typical_customers_json", sa.Text(), nullable=False, server_default="[]", comment="典型客户 JSON 字符串"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 客户画像表",
    )
    op.create_index("ix_sales_agent_customer_profiles_id", "sales_agent_customer_profiles", ["id"])
    op.create_index("ix_sales_agent_customer_profiles_user_id", "sales_agent_customer_profiles", ["user_id"])
    op.create_index("idx_sales_agent_profiles_user_created", "sales_agent_customer_profiles", ["user_id", "created_at"])

    op.create_table(
        "sales_agent_companies",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="企业名称"),
        sa.Column("industry", sa.String(length=128), nullable=False, server_default="", comment="所属行业"),
        sa.Column("region", sa.String(length=128), nullable=False, server_default="", comment="所在地区"),
        sa.Column("website", sa.String(length=512), nullable=False, server_default="", comment="企业官网"),
        sa.Column("employee_size", sa.String(length=128), nullable=False, server_default="", comment="员工规模"),
        sa.Column("source", sa.String(length=128), nullable=False, server_default="", comment="企业线索来源"),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="New", comment="企业状态"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 企业线索基础表",
    )
    op.create_index("ix_sales_agent_companies_id", "sales_agent_companies", ["id"])
    op.create_index("idx_sales_agent_companies_name_region", "sales_agent_companies", ["name", "region"])
    op.create_index("idx_sales_agent_companies_status", "sales_agent_companies", ["status"])

    op.create_table(
        "sales_agent_company_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=False, comment="所属企业 ID"),
        sa.Column("contact_name", sa.String(length=128), nullable=False, server_default="", comment="联系人姓名;MVP 主要保存公司级公开联系人,为空表示未知"),
        sa.Column("position", sa.String(length=128), nullable=False, server_default="", comment="联系人职位;MVP 可为空"),
        sa.Column("email", sa.String(length=255), nullable=False, server_default="", comment="公开邮箱"),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default="", comment="公开联系电话"),
        sa.Column("contact_form_url", sa.String(length=512), nullable=False, server_default="", comment="官网联系表单地址"),
        sa.Column("source", sa.String(length=128), nullable=False, server_default="", comment="联系方式来源类型"),
        sa.Column("source_url", sa.String(length=512), nullable=False, server_default="", comment="联系方式来源页面 URL"),
        sa.Column("captured_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="联系方式采集时间"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="medium", comment="联系方式置信度:high/medium/low"),
        sa.Column("is_reachable", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否可用于合规触达"),
        sa.Column("consent_status", sa.String(length=32), nullable=False, server_default="unknown", comment="同意/可触达状态:public/unknown/unsubscribed/blacklisted/do_not_contact"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 企业公开联系方式表",
    )
    op.create_index("ix_sales_agent_company_contacts_id", "sales_agent_company_contacts", ["id"])
    op.create_index("ix_sales_agent_company_contacts_company_id", "sales_agent_company_contacts", ["company_id"])
    op.create_index("idx_sales_agent_contacts_company", "sales_agent_company_contacts", ["company_id", "is_reachable"])

    op.create_table(
        "sales_agent_lead_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=False, comment="推荐企业 ID"),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("sales_agent_customer_profiles.id"), nullable=True, comment="关联客户画像 ID"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表名"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID"),
        sa.Column("report_snapshot", sa.Text(), nullable=False, server_default="{}", comment="推荐时使用的报告摘要快照 JSON 字符串"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0", comment="推荐分数"),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0", comment="推荐排序名次"),
        sa.Column("reason", sa.Text(), nullable=False, server_default="", comment="推荐理由"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="New", comment="线索状态"),
        sa.Column("status_reason", sa.String(length=64), nullable=False, server_default="", comment="最近一次状态变更原因"),
        sa.Column("status_note", sa.Text(), nullable=False, server_default="", comment="最近一次状态备注"),
        sa.Column("status_updated_by", sa.String(length=128), nullable=False, server_default="", comment="最近一次状态更新人"),
        sa.Column("status_updated_at", sa.DateTime(), nullable=True, comment="最近一次状态更新时间"),
        sa.Column("next_followup_at", sa.DateTime(), nullable=True, comment="下次跟进时间"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 线索推荐表",
    )
    op.create_index("ix_sales_agent_lead_recommendations_id", "sales_agent_lead_recommendations", ["id"])
    op.create_index("ix_sales_agent_lead_recommendations_user_id", "sales_agent_lead_recommendations", ["user_id"])
    op.create_index("ix_sales_agent_lead_recommendations_company_id", "sales_agent_lead_recommendations", ["company_id"])
    op.create_index("ix_sales_agent_lead_recommendations_profile_id", "sales_agent_lead_recommendations", ["profile_id"])
    op.create_index("idx_sales_agent_leads_user_status", "sales_agent_lead_recommendations", ["user_id", "status"])
    op.create_index("idx_sales_agent_leads_profile_rank", "sales_agent_lead_recommendations", ["profile_id", "rank"])

    op.create_table(
        "sales_agent_lead_activities",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, comment="所属线索 ID"),
        sa.Column("activity_type", sa.String(length=32), nullable=False, server_default="note", comment="活动类型:note/status_change/outreach/approval/compliance"),
        sa.Column("from_status", sa.String(length=32), nullable=False, server_default="", comment="变更前线索状态"),
        sa.Column("to_status", sa.String(length=32), nullable=False, server_default="", comment="变更后线索状态"),
        sa.Column("reason", sa.String(length=64), nullable=False, server_default="", comment="活动原因"),
        sa.Column("note", sa.Text(), nullable=False, server_default="", comment="活动备注"),
        sa.Column("actor", sa.String(length=128), nullable=False, server_default="", comment="操作人或系统标识"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 线索跟进活动表",
    )
    op.create_index("ix_sales_agent_lead_activities_id", "sales_agent_lead_activities", ["id"])
    op.create_index("ix_sales_agent_lead_activities_lead_id", "sales_agent_lead_activities", ["lead_id"])
    op.create_index("idx_sales_agent_activities_lead", "sales_agent_lead_activities", ["lead_id", "created_at"])

    op.create_table(
        "sales_agent_outreach_logs",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=False, comment="所属线索 ID"),
        sa.Column("channel", sa.String(length=32), nullable=False, comment="触达渠道:email/sms/phone_script/contact_form"),
        sa.Column("subject", sa.String(length=255), nullable=False, server_default="", comment="触达草稿主题"),
        sa.Column("content", sa.Text(), nullable=False, server_default="", comment="触达草稿正文"),
        sa.Column("send_status", sa.String(length=32), nullable=False, server_default="draft", comment="发送状态;MVP 不真实发送,仅记录 draft/pending_approval/rejected"),
        sa.Column("approved_by", sa.String(length=128), nullable=False, server_default="", comment="人工确认人"),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否需要人工二次确认"),
        sa.Column("has_unsubscribe", sa.Boolean(), nullable=False, server_default=sa.true(), comment="草稿是否包含退订/停止联系说明"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 触达草稿与确认记录表",
    )
    op.create_index("ix_sales_agent_outreach_logs_id", "sales_agent_outreach_logs", ["id"])
    op.create_index("ix_sales_agent_outreach_logs_lead_id", "sales_agent_outreach_logs", ["lead_id"])
    op.create_index("idx_sales_agent_outreach_lead_status", "sales_agent_outreach_logs", ["lead_id", "send_status"])

    op.create_table(
        "sales_agent_compliance_logs",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("action", sa.String(length=128), nullable=False, server_default="", comment="合规检查动作"),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="low", comment="风险等级:low/medium/high"),
        sa.Column("decision", sa.String(length=32), nullable=False, server_default="allowed", comment="处理决定:allowed/blocked/review_required"),
        sa.Column("reason", sa.Text(), nullable=False, server_default="", comment="合规判断原因"),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}", comment="合规事件详情 JSON 字符串"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 合规审计日志表",
    )
    op.create_index("ix_sales_agent_compliance_logs_id", "sales_agent_compliance_logs", ["id"])
    op.create_index("ix_sales_agent_compliance_logs_user_id", "sales_agent_compliance_logs", ["user_id"])
    op.create_index("idx_sales_agent_compliance_user_created", "sales_agent_compliance_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_sales_agent_compliance_user_created", table_name="sales_agent_compliance_logs")
    op.drop_index("ix_sales_agent_compliance_logs_user_id", table_name="sales_agent_compliance_logs")
    op.drop_index("ix_sales_agent_compliance_logs_id", table_name="sales_agent_compliance_logs")
    op.drop_table("sales_agent_compliance_logs")

    op.drop_index("idx_sales_agent_outreach_lead_status", table_name="sales_agent_outreach_logs")
    op.drop_index("ix_sales_agent_outreach_logs_lead_id", table_name="sales_agent_outreach_logs")
    op.drop_index("ix_sales_agent_outreach_logs_id", table_name="sales_agent_outreach_logs")
    op.drop_table("sales_agent_outreach_logs")

    op.drop_index("idx_sales_agent_activities_lead", table_name="sales_agent_lead_activities")
    op.drop_index("ix_sales_agent_lead_activities_lead_id", table_name="sales_agent_lead_activities")
    op.drop_index("ix_sales_agent_lead_activities_id", table_name="sales_agent_lead_activities")
    op.drop_table("sales_agent_lead_activities")

    op.drop_index("idx_sales_agent_leads_profile_rank", table_name="sales_agent_lead_recommendations")
    op.drop_index("idx_sales_agent_leads_user_status", table_name="sales_agent_lead_recommendations")
    op.drop_index("ix_sales_agent_lead_recommendations_profile_id", table_name="sales_agent_lead_recommendations")
    op.drop_index("ix_sales_agent_lead_recommendations_company_id", table_name="sales_agent_lead_recommendations")
    op.drop_index("ix_sales_agent_lead_recommendations_user_id", table_name="sales_agent_lead_recommendations")
    op.drop_index("ix_sales_agent_lead_recommendations_id", table_name="sales_agent_lead_recommendations")
    op.drop_table("sales_agent_lead_recommendations")

    op.drop_index("idx_sales_agent_contacts_company", table_name="sales_agent_company_contacts")
    op.drop_index("ix_sales_agent_company_contacts_company_id", table_name="sales_agent_company_contacts")
    op.drop_index("ix_sales_agent_company_contacts_id", table_name="sales_agent_company_contacts")
    op.drop_table("sales_agent_company_contacts")

    op.drop_index("idx_sales_agent_companies_status", table_name="sales_agent_companies")
    op.drop_index("idx_sales_agent_companies_name_region", table_name="sales_agent_companies")
    op.drop_index("ix_sales_agent_companies_id", table_name="sales_agent_companies")
    op.drop_table("sales_agent_companies")

    op.drop_index("idx_sales_agent_profiles_user_created", table_name="sales_agent_customer_profiles")
    op.drop_index("ix_sales_agent_customer_profiles_user_id", table_name="sales_agent_customer_profiles")
    op.drop_index("ix_sales_agent_customer_profiles_id", table_name="sales_agent_customer_profiles")
    op.drop_table("sales_agent_customer_profiles")

    op.drop_index("idx_sales_agent_messages_session", table_name="sales_agent_chat_messages")
    op.drop_index("ix_sales_agent_chat_messages_session_id", table_name="sales_agent_chat_messages")
    op.drop_index("ix_sales_agent_chat_messages_id", table_name="sales_agent_chat_messages")
    op.drop_table("sales_agent_chat_messages")

    op.drop_index("ux_sales_agent_sessions_public_id", table_name="sales_agent_chat_sessions")
    op.drop_index("idx_sales_agent_sessions_user_updated", table_name="sales_agent_chat_sessions")
    op.drop_index("ix_sales_agent_chat_sessions_user_id", table_name="sales_agent_chat_sessions")
    op.drop_index("ix_sales_agent_chat_sessions_id", table_name="sales_agent_chat_sessions")
    op.drop_table("sales_agent_chat_sessions")
