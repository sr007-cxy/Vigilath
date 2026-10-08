"""add sales agent sms channel tables

Sales Agent outreach extends from "only email can really send" to SMS, routing by the number's country code between CN template channels
(Tencent Cloud/Aliyun) and international free-text channels (Twilio/AWS SNS).

This migration **only adds** five sales_agent_sms_* tables and leaves the four production email tables untouched: the two sides' fields
only overlap on scope/credentials/account selection policy, and those three have already been extracted to service/channel_common.py,
so there is no need to merge at the table level.

Revision ID: e3b9c7d15a48
Revises: c8e4b7a2d931
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3b9c7d15a48"
down_revision: Union[str, None] = "c8e4b7a2d931"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create accounts first: settings.default_account_id and templates/deliveries all reference it.
    op.create_table(
        "sales_agent_sms_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="user", comment="配置作用域:global(运营台全局)/user(用户自有)"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, comment="所属用户 ID;scope=global 时为 NULL"),
        sa.Column("display_name", sa.String(length=128), nullable=False, server_default="", comment="通道展示名,用户在管理页看到的名字"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用;停用的通道不参与选号"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否为该 scope 的默认通道"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="tencent_cloud", comment="平台:tencent_cloud/aliyun/twilio/aws_sns/custom_http"),
        sa.Column("content_mode", sa.String(length=16), nullable=False, server_default="template", comment="内容形态:template(国内,只能发报备模板)/freeform(国际,可发自由文本)"),
        sa.Column("region_scope", sa.String(length=16), nullable=False, server_default="cn", comment="可发范围:cn/intl/all;按收件号码国家码路由的依据"),
        sa.Column("sign_name", sa.String(length=128), nullable=False, server_default="", comment="国内报备短信签名;国际通道留空"),
        sa.Column("sdk_app_id", sa.String(length=64), nullable=False, server_default="", comment="腾讯云 SmsSdkAppId 等应用 ID;不适用的平台留空"),
        sa.Column("api_key_id_encrypted", sa.Text(), nullable=False, server_default="", comment="SecretId/AccessKeyId/Twilio SID,Fernet 加密存储"),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=False, server_default="", comment="SecretKey/AuthToken,Fernet 加密存储"),
        sa.Column("api_base_url", sa.String(length=512), nullable=False, server_default="", comment="自定义 HTTP 网关地址;标准平台留空走默认端点"),
        sa.Column("from_number", sa.String(length=32), nullable=False, server_default="", comment="国际通道主叫号(E.164);国内通道留空"),
        sa.Column("provider_config_json", sa.Text(), nullable=False, server_default="{}", comment="平台附加配置 JSON;明文回显给前端,禁止放密钥"),
        sa.Column("last_tested_at", sa.DateTime(), nullable=True, comment="最近一次连通性自检时间"),
        sa.Column("last_test_status", sa.String(length=32), nullable=False, server_default="", comment="最近一次自检结果:ok/failed"),
        sa.Column("last_test_error", sa.Text(), nullable=False, server_default="", comment="最近一次自检失败原因原文"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 短信发送通道;与邮件账号表平行,多出 content_mode/region_scope 两根路由轴",
    )
    op.create_index("ix_sales_agent_sms_accounts_id", "sales_agent_sms_accounts", ["id"])
    op.create_index("ix_sales_agent_sms_accounts_user_id", "sales_agent_sms_accounts", ["user_id"])
    op.create_index("ix_sales_agent_sms_accounts_created_at", "sales_agent_sms_accounts", ["created_at"])
    op.create_index("idx_sales_agent_sms_accounts_scope_user", "sales_agent_sms_accounts", ["scope", "user_id", "enabled"])
    op.create_index("idx_sales_agent_sms_accounts_default", "sales_agent_sms_accounts", ["scope", "user_id", "is_default"])
    op.create_index("idx_sales_agent_sms_accounts_region", "sales_agent_sms_accounts", ["scope", "user_id", "region_scope", "enabled"])

    op.create_table(
        "sales_agent_sms_settings",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="user", comment="配置作用域:global/user"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, comment="所属用户 ID;scope=global 时为 NULL"),
        sa.Column("sending_enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="总开关;关闭时草稿只落库不外发"),
        sa.Column("receipt_enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否接收运营商状态回执与上行短信(短信没有追踪像素,靠回执与短链观测)"),
        sa.Column("default_account_id", sa.Integer(), sa.ForeignKey("sales_agent_sms_accounts.id"), nullable=True, comment="默认发送通道 ID"),
        sa.Column("sender_selection_policy", sa.String(length=32), nullable=False, server_default="default_only", comment="选号策略:default_only/round_robin"),
        sa.Column("daily_send_cap", sa.Integer(), nullable=False, server_default="0", comment="单日发送上限;0 表示不限,超出即 rejected 并给明确原因"),
        sa.Column("per_contact_cooldown_days", sa.Integer(), nullable=False, server_default="0", comment="同号码再次发送的冷却天数;0 表示不限"),
        sa.Column("updated_by", sa.String(length=128), nullable=False, server_default="", comment="最近修改人标识"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.UniqueConstraint("scope", "user_id", name="ux_sales_agent_sms_settings_scope_user"),
        comment="Sales Agent 短信全局/用户级设置;频控进表是因为短信按条计费且投诉即停签名",
    )
    op.create_index("ix_sales_agent_sms_settings_id", "sales_agent_sms_settings", ["id"])
    op.create_index("ix_sales_agent_sms_settings_user_id", "sales_agent_sms_settings", ["user_id"])
    op.create_index("ix_sales_agent_sms_settings_created_at", "sales_agent_sms_settings", ["created_at"])
    op.create_index("idx_sales_agent_sms_settings_scope_user", "sales_agent_sms_settings", ["scope", "user_id"])

    op.create_table(
        "sales_agent_sms_templates",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("sales_agent_sms_accounts.id"), nullable=False, comment="所属发送通道 ID;模板是按平台账号报备的"),
        sa.Column("template_code", sa.String(length=64), nullable=False, server_default="", comment="平台下发的模板 ID"),
        sa.Column("name", sa.String(length=128), nullable=False, server_default="", comment="模板展示名"),
        sa.Column("body", sa.Text(), nullable=False, server_default="", comment="带 {1}{2} 占位的报备原文;仅供展示与槽位校验,下发以平台侧为准"),
        sa.Column("param_slots_json", sa.Text(), nullable=False, server_default="[]", comment="变量槽定义 JSON:[{index,label,max_len}]"),
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="outreach", comment="用途:outreach/followup/test"),
        sa.Column("approved_status", sa.String(length=32), nullable=False, server_default="pending", comment="平台审核状态:pending/approved/rejected"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.UniqueConstraint("account_id", "template_code", name="ux_sales_agent_sms_templates_account_code"),
        comment="平台已报备短信模板的本地镜像;邮件没有对应物,国内短信只能发审核过的模板+变量槽",
    )
    op.create_index("ix_sales_agent_sms_templates_id", "sales_agent_sms_templates", ["id"])
    op.create_index("ix_sales_agent_sms_templates_account_id", "sales_agent_sms_templates", ["account_id"])
    op.create_index("ix_sales_agent_sms_templates_created_at", "sales_agent_sms_templates", ["created_at"])
    op.create_index("idx_sales_agent_sms_templates_account", "sales_agent_sms_templates", ["account_id", "enabled", "purpose"])

    op.create_table(
        "sales_agent_sms_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="发送方用户 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, comment="关联线索 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True, comment="收件企业 ID"),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("sales_agent_company_contacts.id"), nullable=True, comment="收件联系人 ID"),
        sa.Column("outreach_log_id", sa.Integer(), sa.ForeignKey("sales_agent_outreach_logs.id"), nullable=True, comment="关联触达草稿 ID"),
        sa.Column("sms_account_id", sa.Integer(), sa.ForeignKey("sales_agent_sms_accounts.id"), nullable=True, comment="实际使用的发送通道 ID"),
        sa.Column("report_share_id", sa.Integer(), sa.ForeignKey("sales_agent_report_shares.id"), nullable=True, comment="随短信下发的报告分享 ID"),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="general_outreach", comment="消息类型:general_outreach/report/followup/test"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="", comment="发送时使用的平台快照"),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False, server_default="", comment="平台返回的消息 ID;回执 webhook 按它反查"),
        sa.Column("sign_name", sa.String(length=128), nullable=False, server_default="", comment="发送时使用的签名快照"),
        sa.Column("template_code", sa.String(length=64), nullable=False, server_default="", comment="发送时使用的模板 ID;freeform 模式为空"),
        sa.Column("template_params_json", sa.Text(), nullable=False, server_default="[]", comment="模板变量取值 JSON 快照"),
        sa.Column("recipient_phone", sa.String(length=32), nullable=False, server_default="", comment="收件号码,E.164 带 + 号"),
        sa.Column("recipient_country", sa.String(length=8), nullable=False, server_default="", comment="收件号码 ISO 国家码,如 CN/US"),
        sa.Column("recipient_name", sa.String(length=128), nullable=False, server_default="", comment="收件人姓名快照"),
        sa.Column("content_preview", sa.Text(), nullable=False, server_default="", comment="实际下发文本;模板模式存渲染后的近似内容"),
        sa.Column("segments", sa.Integer(), nullable=False, server_default="0", comment="计费条数;由平台回执回填"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft", comment="状态:draft/sending/sent/delivered/failed/disabled/no_recipient/rejected"),
        sa.Column("failure_code", sa.String(length=64), nullable=False, server_default="", comment="平台错误码原文,便于对账"),
        sa.Column("failure_reason", sa.Text(), nullable=False, server_default="", comment="失败原因;必须写清楚是缺手机号、被频控还是平台拒绝"),
        sa.Column("send_attempted_at", sa.DateTime(), nullable=True, comment="发起发送时间"),
        sa.Column("sent_at", sa.DateTime(), nullable=True, comment="平台受理时间"),
        sa.Column("delivered_at", sa.DateTime(), nullable=True, comment="运营商回执送达时间"),
        sa.Column("failed_at", sa.DateTime(), nullable=True, comment="失败时间"),
        sa.Column("short_link_token", sa.String(length=96), nullable=False, comment="报告短链 token;短信版的打开追踪"),
        sa.Column("first_click_at", sa.DateTime(), nullable=True, comment="首次点击短链时间"),
        sa.Column("last_click_at", sa.DateTime(), nullable=True, comment="最近一次点击短链时间"),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0", comment="短链点击次数"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0", comment="收到的上行短信条数"),
        sa.Column("opted_out_at", sa.DateTime(), nullable=True, comment="对方回复退订的时间"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}", comment="附加元信息 JSON"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 短信投递记录;与邮件投递表平行,追踪靠回执+短链而非追踪像素",
    )
    op.create_index("ix_sales_agent_sms_deliveries_id", "sales_agent_sms_deliveries", ["id"])
    op.create_index("ix_sales_agent_sms_deliveries_user_id", "sales_agent_sms_deliveries", ["user_id"])
    op.create_index("ix_sales_agent_sms_deliveries_lead_id", "sales_agent_sms_deliveries", ["lead_id"])
    op.create_index("ix_sales_agent_sms_deliveries_company_id", "sales_agent_sms_deliveries", ["company_id"])
    op.create_index("ix_sales_agent_sms_deliveries_contact_id", "sales_agent_sms_deliveries", ["contact_id"])
    op.create_index("ix_sales_agent_sms_deliveries_outreach_log_id", "sales_agent_sms_deliveries", ["outreach_log_id"])
    op.create_index("ix_sales_agent_sms_deliveries_sms_account_id", "sales_agent_sms_deliveries", ["sms_account_id"])
    op.create_index("ix_sales_agent_sms_deliveries_report_share_id", "sales_agent_sms_deliveries", ["report_share_id"])
    op.create_index("ix_sales_agent_sms_deliveries_created_at", "sales_agent_sms_deliveries", ["created_at"])
    op.create_index("idx_sales_agent_sms_deliveries_user_status", "sales_agent_sms_deliveries", ["user_id", "status"])
    op.create_index("idx_sales_agent_sms_deliveries_company_created", "sales_agent_sms_deliveries", ["company_id", "created_at"])
    op.create_index("idx_sales_agent_sms_deliveries_draft", "sales_agent_sms_deliveries", ["outreach_log_id"])
    # Rate limiting scans by "number + time"; without this index every send is a full table scan.
    op.create_index("idx_sales_agent_sms_deliveries_phone_created", "sales_agent_sms_deliveries", ["user_id", "recipient_phone", "created_at"])
    op.create_index("idx_sales_agent_sms_deliveries_provider_msg", "sales_agent_sms_deliveries", ["provider", "provider_message_id"])
    op.create_index("ux_sales_agent_sms_deliveries_short_link", "sales_agent_sms_deliveries", ["short_link_token"], unique=True)

    op.create_table(
        "sales_agent_sms_events",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("sales_agent_sms_deliveries.id"), nullable=True, comment="关联投递记录 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, comment="关联线索 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True, comment="关联企业 ID"),
        sa.Column("event_type", sa.String(length=32), nullable=False, server_default="", comment="事件类型:send_attempt/sent/delivered/failed/clicked/replied/opted_out/send_disabled/no_recipient/rejected"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}", comment="事件附加信息 JSON"),
        sa.Column("occurred_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="事件发生时间"),
        comment="Sales Agent 短信事件流水;与邮件事件表同构",
    )
    op.create_index("ix_sales_agent_sms_events_id", "sales_agent_sms_events", ["id"])
    op.create_index("ix_sales_agent_sms_events_delivery_id", "sales_agent_sms_events", ["delivery_id"])
    op.create_index("ix_sales_agent_sms_events_user_id", "sales_agent_sms_events", ["user_id"])
    op.create_index("ix_sales_agent_sms_events_lead_id", "sales_agent_sms_events", ["lead_id"])
    op.create_index("ix_sales_agent_sms_events_company_id", "sales_agent_sms_events", ["company_id"])
    op.create_index("ix_sales_agent_sms_events_occurred_at", "sales_agent_sms_events", ["occurred_at"])
    op.create_index("idx_sales_agent_sms_events_delivery", "sales_agent_sms_events", ["delivery_id", "occurred_at"])
    op.create_index("idx_sales_agent_sms_events_user_type", "sales_agent_sms_events", ["user_id", "event_type"])


def downgrade() -> None:
    # Drop in reverse foreign-key dependency order, otherwise PG refuses to drop referenced tables.
    op.drop_table("sales_agent_sms_events")
    op.drop_table("sales_agent_sms_deliveries")
    op.drop_table("sales_agent_sms_templates")
    op.drop_table("sales_agent_sms_settings")
    op.drop_table("sales_agent_sms_accounts")
