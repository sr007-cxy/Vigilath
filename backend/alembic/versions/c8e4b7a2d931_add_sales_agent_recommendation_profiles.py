"""add sales agent recommendation profile versions

The "recommendation profile for this run" saved by Sales Agent chat gets its own table. A recommendation profile is not a brand profile;
it does not write sales_agent_seller_profiles nor write back to ai_telemetry_topics.profile_json.
This migration only adds an append-only version table and a usage log table.

Revision ID: c8e4b7a2d931
Revises: c1f8b3d24e97
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8e4b7a2d931"
down_revision: Union[str, None] = "c1f8b3d24e97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_agent_recommendation_profile_versions",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("profile_key", sa.String(length=64), nullable=False, comment="同一份推荐画像跨版本稳定 key;不是品牌 key"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("seller_profile_id", sa.Integer(), sa.ForeignKey("sales_agent_seller_profiles.id"), nullable=True, comment="关联的主题品牌镜像 ID;为空表示通用推荐画像或未来聊天品牌草稿"),
        sa.Column("brand_ref_type", sa.String(length=32), nullable=False, server_default="none", comment="预留品牌来源类型:seller_profile/none/chat_brand_draft"),
        sa.Column("brand_ref_key", sa.String(length=128), nullable=False, server_default="", comment="预留品牌稳定引用 key,如 seller_profile:{id} 或 chat_brand:{uuid}"),
        sa.Column("brand_name_snapshot", sa.String(length=128), nullable=False, server_default="", comment="保存推荐画像时看到的品牌名快照;用于历史解释,不跟随品牌改名"),
        sa.Column("brand_context_json", sa.Text(), nullable=False, server_default="{}", comment="预留品牌上下文快照 JSON;未来聊天多品牌草稿可放基础资料"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1", comment="同一 profile_key 下递增版本号;新建/改名/归档都追加新版本"),
        sa.Column("event_type", sa.String(length=32), nullable=False, server_default="created", comment="版本事件类型:created/revised/renamed/archived"),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="", comment="用户可见的推荐画像名称"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="chat", comment="画像来源:chat/ai_assisted/manual"),
        sa.Column("service_capabilities_json", sa.Text(), nullable=False, server_default="[]", comment="本次推荐画像里的我方服务能力 JSON 快照"),
        sa.Column("service_scope_json", sa.Text(), nullable=False, server_default="[]", comment="本次推荐画像里的服务/产品方向 JSON 快照"),
        sa.Column("company_size", sa.String(length=128), nullable=False, server_default="", comment="我方企业/团队规模快照"),
        sa.Column("typical_customers_json", sa.Text(), nullable=False, server_default="[]", comment="适合服务的客户类型 JSON 快照"),
        sa.Column("target_industries_json", sa.Text(), nullable=False, server_default="[]", comment="本次想找的目标行业 JSON 快照;选择画像后要回显"),
        sa.Column("target_regions_json", sa.Text(), nullable=False, server_default="[]", comment="本次想找的目标地区 JSON 快照;选择画像后要回显"),
        sa.Column("target_company_size", sa.String(length=128), nullable=False, server_default="", comment="本次想找的目标企业规模快照;选择画像后要回显"),
        sa.Column("business_hint_json", sa.Text(), nullable=False, server_default="[]", comment="聊天里最小业务方向/服务产品提示 JSON 快照"),
        sa.Column("summary", sa.Text(), nullable=False, server_default="", comment="推荐画像摘要,用于选择卡说明;不作为品牌画像事实源"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="版本创建时间"),
        sa.UniqueConstraint("profile_key", "version", name="ux_sales_agent_rec_profile_key_version"),
        comment="Sales Agent 聊天保存的推荐画像版本表;append-only,不反写品牌画像",
    )
    op.create_index("ix_sales_agent_rec_profiles_id", "sales_agent_recommendation_profile_versions", ["id"])
    op.create_index("ix_sales_agent_rec_profiles_user_id", "sales_agent_recommendation_profile_versions", ["user_id"])
    op.create_index("ix_sales_agent_rec_profiles_seller_id", "sales_agent_recommendation_profile_versions", ["seller_profile_id"])
    op.create_index("ix_sales_agent_rec_profiles_created_at", "sales_agent_recommendation_profile_versions", ["created_at"])
    op.create_index("idx_sales_agent_rec_profiles_user_key_version", "sales_agent_recommendation_profile_versions", ["user_id", "profile_key", "version"])
    op.create_index("idx_sales_agent_rec_profiles_user_created", "sales_agent_recommendation_profile_versions", ["user_id", "created_at"])
    op.create_index("idx_sales_agent_rec_profiles_user_seller_created", "sales_agent_recommendation_profile_versions", ["user_id", "seller_profile_id", "created_at"])
    op.create_index("idx_sales_agent_rec_profiles_user_brand_ref_created", "sales_agent_recommendation_profile_versions", ["user_id", "brand_ref_type", "brand_ref_key", "created_at"])

    op.create_table(
        "sales_agent_recommendation_profile_usages",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("profile_key", sa.String(length=64), nullable=False, comment="被使用的推荐画像稳定 key"),
        sa.Column("profile_version_id", sa.Integer(), sa.ForeignKey("sales_agent_recommendation_profile_versions.id"), nullable=False, comment="被使用的推荐画像版本 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sales_agent_chat_sessions.id"), nullable=True, comment="触发使用的 Sales Agent 会话 ID"),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, comment="关联的推荐任务 ID;只选择未推荐时可为空"),
        sa.Column("used_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="使用时间"),
        comment="Sales Agent 推荐画像使用流水表;用 INSERT 记录最近使用,替代 last_used_at UPDATE",
    )
    op.create_index("ix_sales_agent_rec_usages_id", "sales_agent_recommendation_profile_usages", ["id"])
    op.create_index("ix_sales_agent_rec_usages_profile_key", "sales_agent_recommendation_profile_usages", ["profile_key"])
    op.create_index("ix_sales_agent_rec_usages_version_id", "sales_agent_recommendation_profile_usages", ["profile_version_id"])
    op.create_index("ix_sales_agent_rec_usages_user_id", "sales_agent_recommendation_profile_usages", ["user_id"])
    op.create_index("ix_sales_agent_rec_usages_session_id", "sales_agent_recommendation_profile_usages", ["session_id"])
    op.create_index("ix_sales_agent_rec_usages_campaign_id", "sales_agent_recommendation_profile_usages", ["campaign_id"])
    op.create_index("ix_sales_agent_rec_usages_used_at", "sales_agent_recommendation_profile_usages", ["used_at"])
    op.create_index("idx_sales_agent_rec_profile_usages_user_used", "sales_agent_recommendation_profile_usages", ["user_id", "used_at"])
    op.create_index("idx_sales_agent_rec_profile_usages_key_used", "sales_agent_recommendation_profile_usages", ["profile_key", "used_at"])


def downgrade() -> None:
    op.drop_index("idx_sales_agent_rec_profile_usages_key_used", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("idx_sales_agent_rec_profile_usages_user_used", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_used_at", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_campaign_id", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_session_id", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_user_id", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_version_id", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_profile_key", table_name="sales_agent_recommendation_profile_usages")
    op.drop_index("ix_sales_agent_rec_usages_id", table_name="sales_agent_recommendation_profile_usages")
    op.drop_table("sales_agent_recommendation_profile_usages")

    op.drop_index("idx_sales_agent_rec_profiles_user_brand_ref_created", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("idx_sales_agent_rec_profiles_user_seller_created", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("idx_sales_agent_rec_profiles_user_created", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("idx_sales_agent_rec_profiles_user_key_version", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("ix_sales_agent_rec_profiles_created_at", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("ix_sales_agent_rec_profiles_seller_id", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("ix_sales_agent_rec_profiles_user_id", table_name="sales_agent_recommendation_profile_versions")
    op.drop_index("ix_sales_agent_rec_profiles_id", table_name="sales_agent_recommendation_profile_versions")
    op.drop_table("sales_agent_recommendation_profile_versions")
