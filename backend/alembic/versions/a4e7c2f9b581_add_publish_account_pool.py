"""add publish account pool (self-built account pool: accounts / platform catalog / groups / dispatch queue)

Revision ID: a4e7c2f9b581
Revises: c9f1a4d7b230
Create Date: 2026-08-11

Multi-platform multi-account distribution §5 step 1 (docs/03-系统设计/内容与发布/多平台多账号分发-开发设计方案.md).

Four new tables + one column added to topic_doc_publish_tasks:

  publish_accounts        Self-built account pool accounts. Fields copied from engine_sessions, with four changes:
                          storage_state stored encrypted / owner_worker_id bound for life, no account grabbing /
                          preferred_worker_id removed / plaintext proxy_override string removed.
  publish_platforms       Platform capability matrix config table. New platform = INSERT, no code change or restart.
  publish_account_groups  Account groups (only for group_id referential integrity).
  publish_dispatch_tasks  Execution queue (pull model). **Does not reuse** telemetry-service's
                          dispatch_tasks: quota dimensions differ (there per engine, here per
                          platform+account), and that table's share/reserve accounting once caused
                          a "false deadlock" incident, so no new dimensions get stuffed into it.
  topic_doc_publish_tasks.publish_account_id
                          + partial unique index (doc_id, platform, publish_account_id)
                          WHERE publish_account_id IS NOT NULL: the first layer of F5 idempotency.
                          Made partial so existing rows (always NULL) are completely unconstrained.

**Must live in the same DB and schema; do not split into a separate DB**: topic_doc_publish_tasks already has foreign keys
to ai_telemetry_topics / topic_generated_docs, and publish_dispatch_tasks needs a foreign key to
topic_doc_publish_tasks. PostgreSQL has no cross-database foreign keys, so splitting means giving up referential integrity; in
a scenario where "duplicate publishing is irreversible", that trades the most critical protection for isolation nobody needs.

**Hand-written rather than autogenerate**: geo/__init__.py historically did not register all models, so autogenerate would emit
drop_table for unregistered tables (existing revision e6c1a9d47b83 already documented this pitfall). This change also adds the
publish_* and engine_sessions models to geo/__init__.py, but the migration is still hand-written.
"""
from alembic import op
import sqlalchemy as sa

revision = "a4e7c2f9b581"
down_revision = "c9f1a4d7b230"
branch_labels = None
depends_on = None


def upgrade():
    # ── Create the groups table first: publish_accounts.group_id has a foreign key to it ──────────────
    op.create_table(
        "publish_account_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )

    # ── Platform capability matrix ────────────────────────────────────────────────
    # Numeric columns 0 = unlimited (following the engine_sessions.daily_cap convention).
    op.create_table(
        "publish_platforms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name_zh", sa.String(), nullable=False, server_default=""),
        sa.Column("channel", sa.String(), nullable=False, server_default="server_pool"),
        sa.Column("supports_article", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("title_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_max", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_cover", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_format", sa.String(), nullable=False, server_default="html"),
        sa.Column("identity_cookies_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_publish_platforms_code", "publish_platforms", ["code"], unique=True)

    # ── Account pool ──────────────────────────────────────────────────────
    op.create_table(
        "publish_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("account_handle", sa.String(), nullable=True),
        sa.Column("channel", sa.String(), nullable=False, server_default="server_pool"),
        # 🔴 Ciphertext column. The _enc suffix is deliberate: it makes "plaintext written into it" visible to the naked eye in code review.
        sa.Column("storage_state_enc", sa.Text(), nullable=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("auth_type", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ua_platform", sa.String(), nullable=True),
        # 🔴 Credential owner: determines whether re-authorization after logout needs a specific person present, i.e. whether "unattended" is real.
        sa.Column("credential_owner", sa.String(), nullable=False, server_default=""),
        sa.Column("account_value", sa.String(), nullable=False, server_default="low"),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_auth"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("warmup_until", sa.DateTime(), nullable=True),
        sa.Column("rate_limited_until", sa.DateTime(), nullable=True),
        sa.Column("unschedulable_until", sa.DateTime(), nullable=True),
        sa.Column("last_fail_type", sa.String(), nullable=True),
        sa.Column("last_fail_at", sa.DateTime(), nullable=True),
        sa.Column("fail_counts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("owner_worker_id", sa.Integer(), nullable=True),
        sa.Column("lease_worker_id", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("used_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_date", sa.String(), nullable=True),
        sa.Column("daily_cap", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("concurrency", sa.Integer(), nullable=False, server_default="1"),
        # Reuse the existing proxy store; no new proxy table. RESTRICT: a proxy row cannot be deleted while still bound to an account,
        # otherwise the account would silently lose its egress binding -> next publish uses the local IP -> remote-location risk control.
        sa.Column("proxy_id", sa.Integer(), nullable=True),
        sa.Column("egress", sa.String(), nullable=True),
        sa.Column("exit_ip", sa.String(), nullable=True),
        sa.Column("exit_ip_at", sa.DateTime(), nullable=True),
        sa.Column("identity_probe_json", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["group_id"], ["publish_account_groups.id"],
                                name="fk_publish_accounts_group", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proxy_id"], ["engine_proxies.id"],
                                name="fk_publish_accounts_proxy", ondelete="RESTRICT"),
        sa.UniqueConstraint("platform", "account_id",
                            name="uq_publish_accounts_platform_account"),
    )
    op.create_index("idx_publish_accounts_claim", "publish_accounts",
                    ["platform", "status", "owner_worker_id"])
    # Sibling cooldown: rate limiting is judged per IP, not per account, so accounts in the same group must be findable quickly by egress
    op.create_index("idx_publish_accounts_egress", "publish_accounts", ["proxy_id", "exit_ip"])

    # ── Dispatch queue ────────────────────────────────────────────────────
    op.create_table(
        "publish_dispatch_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("account_db_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("visible_after", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("claimed_by_worker_id", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        # 🔴 The only basis for submit_unknown reconciliation; must be persisted to DB before clicking publish
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("screenshot_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["task_id"], ["topic_doc_publish_tasks.id"],
                                name="fk_publish_dispatch_task", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_db_id"], ["publish_accounts.id"],
                                name="fk_publish_dispatch_account", ondelete="CASCADE"),
    )
    op.create_index("idx_publish_dispatch_claim", "publish_dispatch_tasks",
                    ["status", "platform", "visible_after", "id"])
    op.create_index("idx_publish_dispatch_account", "publish_dispatch_tasks",
                    ["account_db_id", "status"])
    op.create_index("idx_publish_dispatch_task", "publish_dispatch_tasks", ["task_id"])

    # ── Add account binding + idempotency constraint to the business task table ──────────────────────────────
    with op.batch_alter_table("topic_doc_publish_tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("publish_account_id", sa.Integer(), nullable=True))

    # Partial unique index: postgresql_where on PG, sqlite_where on SQLite. Existing rows
    # with publish_account_id IS NULL -> naturally excluded, no conflicts from historical data.
    op.create_index(
        "uq_publish_task_doc_platform_account", "topic_doc_publish_tasks",
        ["doc_id", "platform", "publish_account_id"], unique=True,
        postgresql_where=sa.text("publish_account_id IS NOT NULL"),
        sqlite_where=sa.text("publish_account_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_publish_task_doc_platform_account", table_name="topic_doc_publish_tasks")
    with op.batch_alter_table("topic_doc_publish_tasks", schema=None) as batch_op:
        batch_op.drop_column("publish_account_id")

    op.drop_index("idx_publish_dispatch_task", table_name="publish_dispatch_tasks")
    op.drop_index("idx_publish_dispatch_account", table_name="publish_dispatch_tasks")
    op.drop_index("idx_publish_dispatch_claim", table_name="publish_dispatch_tasks")
    op.drop_table("publish_dispatch_tasks")

    op.drop_index("idx_publish_accounts_egress", table_name="publish_accounts")
    op.drop_index("idx_publish_accounts_claim", table_name="publish_accounts")
    op.drop_table("publish_accounts")

    op.drop_index("ix_publish_platforms_code", table_name="publish_platforms")
    op.drop_table("publish_platforms")

    op.drop_table("publish_account_groups")
