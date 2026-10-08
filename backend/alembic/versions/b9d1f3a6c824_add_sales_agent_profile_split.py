"""Add Sales Agent split profile/campaign/evidence tables

Revision ID: b9d1f3a6c824
Revises: c7f2a91d4e63, c3d9b71e4a02
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b9d1f3a6c824"
down_revision: Union[str, Sequence[str], None] = ("c7f2a91d4e63", "c3d9b71e4a02")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEADS_TABLE = "sales_agent_lead_recommendations"
_CAMPAIGN_INDEX = "idx_sales_agent_leads_campaign_rank"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _cols(name: str) -> set[str]:
    if not _has_table(name):
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(name)}


def _indexes(name: str) -> set[str]:
    if not _has_table(name):
        return set()
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(name) if i.get("name")}


def upgrade() -> None:
    op.create_table(
        "sales_agent_seller_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("legacy_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_customer_profiles.id"), nullable=True, comment="兼容旧客户画像 ID"),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="默认卖家服务画像", comment="画像名称"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID"),
        sa.Column("report_snapshot", sa.Text(), nullable=False, server_default="{}", comment="报告摘要快照 JSON"),
        sa.Column("service_scope_json", sa.Text(), nullable=False, server_default="[]", comment="卖家服务范围 JSON"),
        sa.Column("service_capabilities_json", sa.Text(), nullable=False, server_default="[]", comment="服务能力 JSON"),
        sa.Column("company_size", sa.String(length=128), nullable=False, server_default="", comment="卖家团队/公司规模"),
        sa.Column("typical_customers_json", sa.Text(), nullable=False, server_default="[]", comment="典型客户 JSON"),
        sa.Column("proof_points_json", sa.Text(), nullable=False, server_default="[]", comment="案例/资质/证据点 JSON"),
        sa.Column("default_buyer_roles_json", sa.Text(), nullable=False, server_default="[]", comment="默认买方角色 JSON"),
        sa.Column("default_exclude_rules_json", sa.Text(), nullable=False, server_default="[]", comment="默认排除规则 JSON"),
        sa.Column("summary", sa.Text(), nullable=False, server_default="", comment="卖家画像摘要"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 卖家服务画像表",
    )
    op.create_index("ix_sales_agent_seller_profiles_id", "sales_agent_seller_profiles", ["id"])
    op.create_index("ix_sales_agent_seller_profiles_user_id", "sales_agent_seller_profiles", ["user_id"])
    op.create_index("ix_sales_agent_seller_profiles_legacy_profile_id", "sales_agent_seller_profiles", ["legacy_profile_id"])
    op.create_index("idx_sales_agent_seller_profiles_user_updated", "sales_agent_seller_profiles", ["user_id", "updated_at"])

    op.create_table(
        "sales_agent_account_icps",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("seller_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_seller_profiles.id"), nullable=True, comment="卖家服务画像 ID"),
        sa.Column("legacy_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_customer_profiles.id"), nullable=True, comment="兼容旧客户画像 ID"),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="默认目标公司 ICP", comment="ICP 名称"),
        sa.Column("target_company_types_json", sa.Text(), nullable=False, server_default="[]", comment="目标公司类型 JSON"),
        sa.Column("target_industries_json", sa.Text(), nullable=False, server_default="[]", comment="目标行业 JSON"),
        sa.Column("target_regions_json", sa.Text(), nullable=False, server_default="[]", comment="目标地区 JSON"),
        sa.Column("target_company_size", sa.String(length=128), nullable=False, server_default="", comment="目标公司规模"),
        sa.Column("target_stage_json", sa.Text(), nullable=False, server_default="[]", comment="目标发展阶段 JSON"),
        sa.Column("observable_signals_json", sa.Text(), nullable=False, server_default="[]", comment="可观察信号 JSON"),
        sa.Column("pain_signals_json", sa.Text(), nullable=False, server_default="[]", comment="痛点信号 JSON"),
        sa.Column("budget_signals_json", sa.Text(), nullable=False, server_default="[]", comment="预算信号 JSON"),
        sa.Column("negative_icp_json", sa.Text(), nullable=False, server_default="[]", comment="负向 ICP JSON"),
        sa.Column("scoring_rubric_json", sa.Text(), nullable=False, server_default="{}", comment="评分规则 JSON"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual", comment="来源:manual/legacy/report"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 目标公司 ICP 表",
    )
    op.create_index("ix_sales_agent_account_icps_id", "sales_agent_account_icps", ["id"])
    op.create_index("ix_sales_agent_account_icps_user_id", "sales_agent_account_icps", ["user_id"])
    op.create_index("ix_sales_agent_account_icps_seller_profile_id", "sales_agent_account_icps", ["seller_profile_id"])
    op.create_index("ix_sales_agent_account_icps_legacy_profile_id", "sales_agent_account_icps", ["legacy_profile_id"])
    op.create_index("idx_sales_agent_account_icps_user_updated", "sales_agent_account_icps", ["user_id", "updated_at"])

    op.create_table(
        "sales_agent_buyer_personas",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("seller_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_seller_profiles.id"), nullable=True, comment="卖家服务画像 ID"),
        sa.Column("account_icp_id", sa.Integer(), sa.ForeignKey("sales_agent_account_icps.id"), nullable=True, comment="目标公司 ICP ID"),
        sa.Column("role_name", sa.String(length=128), nullable=False, server_default="", comment="角色名称"),
        sa.Column("titles_json", sa.Text(), nullable=False, server_default="[]", comment="常见职位 JSON"),
        sa.Column("decision_role", sa.String(length=64), nullable=False, server_default="influencer", comment="购买决策角色"),
        sa.Column("goals_json", sa.Text(), nullable=False, server_default="[]", comment="目标 JSON"),
        sa.Column("kpis_json", sa.Text(), nullable=False, server_default="[]", comment="KPI JSON"),
        sa.Column("pains_json", sa.Text(), nullable=False, server_default="[]", comment="痛点 JSON"),
        sa.Column("objections_json", sa.Text(), nullable=False, server_default="[]", comment="异议 JSON"),
        sa.Column("messaging_angles_json", sa.Text(), nullable=False, server_default="[]", comment="话术角度 JSON"),
        sa.Column("proof_needed_json", sa.Text(), nullable=False, server_default="[]", comment="所需证据 JSON"),
        sa.Column("content_preferences_json", sa.Text(), nullable=False, server_default="[]", comment="内容偏好 JSON"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100", comment="优先级"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 买方 Persona 表",
    )
    op.create_index("ix_sales_agent_buyer_personas_id", "sales_agent_buyer_personas", ["id"])
    op.create_index("ix_sales_agent_buyer_personas_user_id", "sales_agent_buyer_personas", ["user_id"])
    op.create_index("ix_sales_agent_buyer_personas_seller_profile_id", "sales_agent_buyer_personas", ["seller_profile_id"])
    op.create_index("ix_sales_agent_buyer_personas_account_icp_id", "sales_agent_buyer_personas", ["account_icp_id"])
    op.create_index("idx_sales_agent_buyer_personas_user_role", "sales_agent_buyer_personas", ["user_id", "role_name"])

    op.create_table(
        "sales_agent_recommendation_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sales_agent_chat_sessions.id"), nullable=True, comment="会话 ID"),
        sa.Column("seller_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_seller_profiles.id"), nullable=True, comment="卖家服务画像 ID"),
        sa.Column("account_icp_id", sa.Integer(), sa.ForeignKey("sales_agent_account_icps.id"), nullable=True, comment="目标公司 ICP ID"),
        sa.Column("legacy_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_customer_profiles.id"), nullable=True, comment="兼容旧客户画像 ID"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID"),
        sa.Column("request_text", sa.Text(), nullable=False, server_default="", comment="用户原始推荐需求"),
        sa.Column("target_industries_json", sa.Text(), nullable=False, server_default="[]", comment="本次目标行业 JSON"),
        sa.Column("target_regions_json", sa.Text(), nullable=False, server_default="[]", comment="本次目标地区 JSON"),
        sa.Column("target_company_size", sa.String(length=128), nullable=False, server_default="", comment="本次目标公司规模"),
        sa.Column("service_scope_override_json", sa.Text(), nullable=False, server_default="[]", comment="本次主推服务 JSON"),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="5", comment="本次推荐数量"),
        sa.Column("seed_companies_json", sa.Text(), nullable=False, server_default="[]", comment="本次种子企业 JSON"),
        sa.Column("contact_preference_json", sa.Text(), nullable=False, server_default="[]", comment="联系方式偏好 JSON"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="collecting_requirements", comment="任务状态"),
        sa.Column("search_available", sa.Boolean(), nullable=False, server_default=sa.true(), comment="公网搜索是否可用"),
        sa.Column("requested_limit", sa.Integer(), nullable=False, server_default="0", comment="请求数量"),
        sa.Column("actual_count", sa.Integer(), nullable=False, server_default="0", comment="实际数量"),
        sa.Column("shortfall", sa.Integer(), nullable=False, server_default="0", comment="缺口数量"),
        sa.Column("missing_config_json", sa.Text(), nullable=False, server_default="[]", comment="缺失配置 JSON"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 本次推荐任务表",
    )
    op.create_index("ix_sales_agent_recommendation_campaigns_id", "sales_agent_recommendation_campaigns", ["id"])
    op.create_index("ix_sales_agent_recommendation_campaigns_user_id", "sales_agent_recommendation_campaigns", ["user_id"])
    op.create_index("ix_sales_agent_recommendation_campaigns_session_id", "sales_agent_recommendation_campaigns", ["session_id"])
    op.create_index("ix_sales_agent_recommendation_campaigns_seller_profile_id", "sales_agent_recommendation_campaigns", ["seller_profile_id"])
    op.create_index("ix_sales_agent_recommendation_campaigns_account_icp_id", "sales_agent_recommendation_campaigns", ["account_icp_id"])
    op.create_index("ix_sales_agent_recommendation_campaigns_legacy_profile_id", "sales_agent_recommendation_campaigns", ["legacy_profile_id"])
    op.create_index("idx_sales_agent_campaigns_user_created", "sales_agent_recommendation_campaigns", ["user_id", "created_at"])

    if _has_table(_LEADS_TABLE) and "campaign_id" not in _cols(_LEADS_TABLE):
        with op.batch_alter_table(_LEADS_TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column("campaign_id", sa.Integer(), nullable=True, comment="推荐任务 ID"))
            batch_op.create_foreign_key("fk_sales_agent_leads_campaign_id", "sales_agent_recommendation_campaigns", ["campaign_id"], ["id"])
    if _has_table(_LEADS_TABLE) and _CAMPAIGN_INDEX not in _indexes(_LEADS_TABLE):
        op.create_index(_CAMPAIGN_INDEX, _LEADS_TABLE, ["campaign_id", "rank"])

    op.create_table(
        "sales_agent_lead_evidences",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, comment="推荐任务 ID"),
        sa.Column("lead_recommendation_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, comment="推荐线索 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True, comment="企业 ID"),
        sa.Column("evidence_type", sa.String(length=64), nullable=False, server_default="firmographic", comment="证据类型"),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="manual_seed", comment="来源类型"),
        sa.Column("source_url", sa.String(length=512), nullable=False, server_default="", comment="来源 URL"),
        sa.Column("source_report_table", sa.String(length=64), nullable=False, server_default="", comment="来源报告表"),
        sa.Column("source_report_id", sa.String(length=64), nullable=False, server_default="", comment="来源报告 ID"),
        sa.Column("evidence_text", sa.Text(), nullable=False, server_default="", comment="证据摘要"),
        sa.Column("scoring_category", sa.String(length=64), nullable=False, server_default="", comment="影响评分项"),
        sa.Column("score_delta", sa.Integer(), nullable=False, server_default="0", comment="分数贡献"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="medium", comment="置信度"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        comment="Sales Agent 推荐证据链表",
    )
    op.create_index("ix_sales_agent_lead_evidences_id", "sales_agent_lead_evidences", ["id"])
    op.create_index("ix_sales_agent_lead_evidences_user_id", "sales_agent_lead_evidences", ["user_id"])
    op.create_index("ix_sales_agent_lead_evidences_campaign_id", "sales_agent_lead_evidences", ["campaign_id"])
    op.create_index("ix_sales_agent_lead_evidences_lead_recommendation_id", "sales_agent_lead_evidences", ["lead_recommendation_id"])
    op.create_index("ix_sales_agent_lead_evidences_company_id", "sales_agent_lead_evidences", ["company_id"])
    op.create_index("idx_sales_agent_evidence_campaign", "sales_agent_lead_evidences", ["campaign_id", "company_id"])
    op.create_index("idx_sales_agent_evidence_lead", "sales_agent_lead_evidences", ["lead_recommendation_id", "created_at"])


def downgrade() -> None:
    if _has_table("sales_agent_lead_evidences"):
        op.drop_index("idx_sales_agent_evidence_lead", table_name="sales_agent_lead_evidences")
        op.drop_index("idx_sales_agent_evidence_campaign", table_name="sales_agent_lead_evidences")
        op.drop_index("ix_sales_agent_lead_evidences_company_id", table_name="sales_agent_lead_evidences")
        op.drop_index("ix_sales_agent_lead_evidences_lead_recommendation_id", table_name="sales_agent_lead_evidences")
        op.drop_index("ix_sales_agent_lead_evidences_campaign_id", table_name="sales_agent_lead_evidences")
        op.drop_index("ix_sales_agent_lead_evidences_user_id", table_name="sales_agent_lead_evidences")
        op.drop_index("ix_sales_agent_lead_evidences_id", table_name="sales_agent_lead_evidences")
        op.drop_table("sales_agent_lead_evidences")

    if _has_table(_LEADS_TABLE):
        if _CAMPAIGN_INDEX in _indexes(_LEADS_TABLE):
            op.drop_index(_CAMPAIGN_INDEX, table_name=_LEADS_TABLE)
        if "campaign_id" in _cols(_LEADS_TABLE):
            with op.batch_alter_table(_LEADS_TABLE, schema=None) as batch_op:
                batch_op.drop_constraint("fk_sales_agent_leads_campaign_id", type_="foreignkey")
                batch_op.drop_column("campaign_id")

    if _has_table("sales_agent_recommendation_campaigns"):
        op.drop_index("idx_sales_agent_campaigns_user_created", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_legacy_profile_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_account_icp_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_seller_profile_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_session_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_user_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_index("ix_sales_agent_recommendation_campaigns_id", table_name="sales_agent_recommendation_campaigns")
        op.drop_table("sales_agent_recommendation_campaigns")

    if _has_table("sales_agent_buyer_personas"):
        op.drop_index("idx_sales_agent_buyer_personas_user_role", table_name="sales_agent_buyer_personas")
        op.drop_index("ix_sales_agent_buyer_personas_account_icp_id", table_name="sales_agent_buyer_personas")
        op.drop_index("ix_sales_agent_buyer_personas_seller_profile_id", table_name="sales_agent_buyer_personas")
        op.drop_index("ix_sales_agent_buyer_personas_user_id", table_name="sales_agent_buyer_personas")
        op.drop_index("ix_sales_agent_buyer_personas_id", table_name="sales_agent_buyer_personas")
        op.drop_table("sales_agent_buyer_personas")

    if _has_table("sales_agent_account_icps"):
        op.drop_index("idx_sales_agent_account_icps_user_updated", table_name="sales_agent_account_icps")
        op.drop_index("ix_sales_agent_account_icps_legacy_profile_id", table_name="sales_agent_account_icps")
        op.drop_index("ix_sales_agent_account_icps_seller_profile_id", table_name="sales_agent_account_icps")
        op.drop_index("ix_sales_agent_account_icps_user_id", table_name="sales_agent_account_icps")
        op.drop_index("ix_sales_agent_account_icps_id", table_name="sales_agent_account_icps")
        op.drop_table("sales_agent_account_icps")

    if _has_table("sales_agent_seller_profiles"):
        op.drop_index("idx_sales_agent_seller_profiles_user_updated", table_name="sales_agent_seller_profiles")
        op.drop_index("ix_sales_agent_seller_profiles_legacy_profile_id", table_name="sales_agent_seller_profiles")
        op.drop_index("ix_sales_agent_seller_profiles_user_id", table_name="sales_agent_seller_profiles")
        op.drop_index("ix_sales_agent_seller_profiles_id", table_name="sales_agent_seller_profiles")
        op.drop_table("sales_agent_seller_profiles")
