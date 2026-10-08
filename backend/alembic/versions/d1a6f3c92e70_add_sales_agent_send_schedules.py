"""add sales agent scheduled-send tables

Scheduled email sending (docs/08-独立模块/SalesAgent/04-开发实现/计划-2026-09-11-定时发送邮件.md §3).

**Only adds four tables; not a single column of existing tables is touched, not even an index.** The daily quota query
(`WHERE email_account_id = ? AND sent_at >= ? AND status = 'sent'`) is served well enough by the delivery table's existing
`email_account_id` single-column index: scan cost only depends on one sender mailbox's cumulative deliveries,
phase-1 volume (200/day/account) is 73k rows a year, and the query only runs every 30 seconds. The trigger line is in §3:
add `(email_account_id, sent_at)` only when a single account exceeds 500k cumulative rows or p99 exceeds 100ms,
and then remember `CREATE INDEX CONCURRENTLY` + `op.get_context().autocommit_block()`
(alembic runs inside a transaction by default, see c7f2a91d4e63:16).

Conversely, none of the new tables' indexes can be skipped; they are all created with the tables: the plan table's `(status, scheduled_at)` is
scanned by the executor every 60 seconds; the item table's `(schedule_id, status)` is the only table that will grow to millions of rows.
The heartbeat table **has no index other than instance_key**: that row is updated every 60 seconds, and only without indexes can it use
PG's HOT update, reclaiming dead tuples in-page.

Revision ID: d1a6f3c92e70
Revises: e3b9c7d15a48
Create Date: 2026-09-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d1a6f3c92e70"
down_revision: Union[str, None] = "e3b9c7d15a48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_agent_send_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="所属用户 ID"),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("sales_agent_recommendation_campaigns.id"), nullable=True, comment="关联的推荐批次 ID,可空"),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="email", comment="发送渠道;一期只支持 email,预留 sms"),
        sa.Column("email_account_id", sa.Integer(), sa.ForeignKey("sales_agent_email_accounts.id"), nullable=True, comment="arm 时解析并钉死的发件箱;发件箱锁与日配额都以它为键,执行期间不再重选"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False, comment="计划发出第一封的时刻(UTC)"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai", comment="用户说「三点」时的时区;一期只支持 Asia/Shanghai,本列仅作留痕"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft", comment="draft/armed/running/done/cancelled/expired;armed→running 在发第一封之前翻"),
        sa.Column("throttle_seconds", sa.Integer(), nullable=False, server_default="30", comment="每封之间的最小间隔秒数"),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="200", comment="本计划自身的上限;真正的日闸按 (email_account_id, 发送日) 聚合、不带 user_id"),
        sa.Column("send_window", sa.String(length=32), nullable=False, server_default="09:00-18:00", comment="允许发送的时段,按 timezone 解释"),
        sa.Column("created_by_session_id", sa.Integer(), sa.ForeignKey("sales_agent_chat_sessions.id"), nullable=True, comment="建计划的会话 ID;回执写回该会话"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}", comment="用户原话、筛选条件、统计快照"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 定时发送计划主表:一行 = 一次「到点自动发这批信」的授权",
    )
    op.create_index("ix_sales_agent_send_schedules_id", "sales_agent_send_schedules", ["id"])
    op.create_index("ix_sales_agent_send_schedules_user_id", "sales_agent_send_schedules", ["user_id"])
    op.create_index("ix_sales_agent_send_schedules_campaign_id", "sales_agent_send_schedules", ["campaign_id"])
    op.create_index("ix_sales_agent_send_schedules_email_account_id", "sales_agent_send_schedules", ["email_account_id"])
    op.create_index("ix_sales_agent_send_schedules_created_by_session_id", "sales_agent_send_schedules", ["created_by_session_id"])
    op.create_index("ix_sales_agent_send_schedules_created_at", "sales_agent_send_schedules", ["created_at"])
    # The executor's every-60-seconds `status='armed' AND scheduled_at <= now()+5min` uses this.
    op.create_index("idx_sales_agent_send_schedules_due", "sales_agent_send_schedules", ["status", "scheduled_at"])

    op.create_table(
        "sales_agent_send_schedule_items",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("sales_agent_send_schedules.id"), nullable=False, comment="所属计划 ID"),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("sales_agent_lead_recommendations.id"), nullable=True, comment="目标线索 ID"),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("sales_agent_companies.id"), nullable=True, comment="目标企业 ID"),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("sales_agent_company_contacts.id"), nullable=True, comment="当前收件联系人;顺延后指向实际发成的那个"),
        sa.Column("outreach_log_id", sa.Integer(), sa.ForeignKey("sales_agent_outreach_logs.id"), nullable=True, comment="对应草稿 ID;顺延换收件人时草稿不变"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending_draft", comment="pending_draft/pending/sending/sent/failed/skipped/cancelled;pending_draft 对用户不可见,面板上与 pending 同显「待发送」"),
        sa.Column("skip_reason", sa.String(length=255), nullable=False, server_default="", comment="skipped 时必填:已退订/无有效邮箱/草稿生成失败/超日配额/命中抑制列表"),
        sa.Column("email_delivery_id", sa.Integer(), sa.ForeignKey("sales_agent_email_deliveries.id"), nullable=True, comment="最终那次投递记录 ID"),
        sa.Column("attempted_json", sa.Text(), nullable=False, server_default="[]", comment="顺延轨迹 [{contact_id,email,delivery_id,result,reason}],每试一个地址追加一条"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="已试地址数,上限 MAX_RECIPIENT_FALLBACK"),
        sa.Column("claimed_at", sa.DateTime(), nullable=True, comment="被发送线程认领的时刻;超 5 分钟没进展的回收成 pending"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="终态时刻"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        comment="Sales Agent 定时发送明细:每封一行,是幂等与并发认领的落点",
    )
    op.create_index("ix_sales_agent_send_schedule_items_id", "sales_agent_send_schedule_items", ["id"])
    op.create_index("ix_sales_agent_send_schedule_items_schedule_id", "sales_agent_send_schedule_items", ["schedule_id"])
    op.create_index("ix_sales_agent_send_schedule_items_lead_id", "sales_agent_send_schedule_items", ["lead_id"])
    op.create_index("ix_sales_agent_send_schedule_items_company_id", "sales_agent_send_schedule_items", ["company_id"])
    op.create_index("ix_sales_agent_send_schedule_items_outreach_log_id", "sales_agent_send_schedule_items", ["outreach_log_id"])
    # Every read/write on this table carries schedule_id, and it is the only new table that will grow to millions of rows.
    op.create_index("idx_sales_agent_send_schedule_items_sched_status", "sales_agent_send_schedule_items", ["schedule_id", "status"])

    op.create_table(
        "sales_agent_email_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, comment="归属用户 ID"),
        sa.Column("email", sa.String(length=255), nullable=False, comment="小写归一化后的完整邮箱地址"),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="manual", comment="unsubscribed/bounced_hard/complained/manual"),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="", comment="来源:退订链接/退信/人工加入"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="加入时间"),
        sa.UniqueConstraint("user_id", "email", name="ux_sales_agent_email_suppressions_user_email"),
        comment="Sales Agent 邮箱级抑制列表:contact 上的 consent_status 是 per-lead 的,同一邮箱换个 lead 就绕过去了",
    )
    op.create_index("ix_sales_agent_email_suppressions_id", "sales_agent_email_suppressions", ["id"])
    op.create_index("ix_sales_agent_email_suppressions_user_id", "sales_agent_email_suppressions", ["user_id"])
    op.create_index("ix_sales_agent_email_suppressions_created_at", "sales_agent_email_suppressions", ["created_at"])

    op.create_table(
        "sales_agent_scheduler_heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True, comment="主键 ID"),
        sa.Column("instance_key", sa.String(length=255), nullable=False, comment="hostname:pid;进程重启后 pid 变、旧行自然过期"),
        sa.Column("hostname", sa.String(length=255), nullable=False, server_default="", comment="主机名,便于人眼定位是哪个容器"),
        sa.Column("pid", sa.Integer(), nullable=False, server_default="0", comment="进程号"),
        sa.Column("last_beat_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="上一轮心跳时刻(UTC);超 5 分钟即告警。**故意不建索引**,保住 HOT update"),
        sa.Column("live_workers", sa.Integer(), nullable=False, server_default="0", comment="该进程当前在跑的发送线程数"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default="", comment="上一轮的异常摘要;异常不逃出线程,但要留痕"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), comment="该实例首次上报时间"),
        comment="Sales Agent 调度进程心跳,每进程一行。调度器挂掉的表现是「到点不发、没有报错」,这张表是唯一能发现它的地方",
    )
    op.create_index("ix_sales_agent_scheduler_heartbeats_id", "sales_agent_scheduler_heartbeats", ["id"])
    # Nothing besides the unique index: this row is updated every 60 seconds, 520k times a year,
    # and an index on updated columns prevents HOT update, causing index bloat instead (§3.5).
    op.create_index("ux_sales_agent_scheduler_heartbeats_key", "sales_agent_scheduler_heartbeats", ["instance_key"], unique=True)


def downgrade() -> None:
    op.drop_table("sales_agent_scheduler_heartbeats")
    op.drop_table("sales_agent_email_suppressions")
    op.drop_table("sales_agent_send_schedule_items")
    op.drop_table("sales_agent_send_schedules")
