"""engine_rate_policies: move engine-level rate-limit parameters from code/env into the DB

Before this change the values were scattered in three places: _DEFAULT_* dicts in code, env ENGINE_IP_* overrides,
and the per-account daily_cap column. Each tuning meant editing env + restarting the backend, and editing test host / prod
separately was easy to miss: on 2026-08-30 we actually saw "daily limit 80 on test host, still 25 on prod".

Once in the DB: edited in the admin backend, effective immediately (60s cache), and both environments share one copy (shared appdb).

Value precedence: this table (non-NULL fields) > env > code defaults. NULL = no override, keep using the lower layer's value,
so to tune one item there is no need to fill the whole row.

Initial data follows the strategy finalized on 2026-08-30:
  deepseek  per-account 600s / egress 400s / 9 per hour / 80 per day   (high risk, documented safe line 25-30/day)
  qwen      per-account 300s / egress 360s / 10 per hour / 120 per day (medium risk, documented 40-50/day)
  wenxin    same as qwen

Revision ID: c1f8b3d24e97
Revises: b4e7d2a91f38
"""
from alembic import op
import sqlalchemy as sa

revision = "c1f8b3d24e97"
down_revision = "b4e7d2a91f38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_rate_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("engine", sa.String(), nullable=False),
        # All nullable: NULL = this item does not override the lower-layer default
        sa.Column("session_min_gap_sec", sa.Integer(), nullable=True),
        sa.Column("ip_min_gap_sec", sa.Integer(), nullable=True),
        sa.Column("ip_hourly_cap", sa.Integer(), nullable=True),
        sa.Column("ip_daily_cap", sa.Integer(), nullable=True),
        sa.Column("per_account_daily_cap", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("uq_engine_rate_policies_engine", "engine_rate_policies",
                    ["engine"], unique=True)

    # Seed initial values. Use SQL rather than ORM: migrations should not depend on the model's current shape.
    op.execute("""
        INSERT INTO engine_rate_policies
            (engine, session_min_gap_sec, ip_min_gap_sec, ip_hourly_cap,
             ip_daily_cap, enabled, note, updated_by)
        VALUES
            ('deepseek', 600, 400,  9,  80, true,
             '风险高:禁言按IP簇发、阶梯到26天。文档安全线 25-30/IP/天,当前 9*24=216 已激进,盯 captcha_count', 'migration'),
            ('qwen',     300, 360, 10, 120, true,
             '风险中:文档 40-50/IP/天、<=10/小时', 'migration'),
            ('wenxin',   300, 360, 10, 120, true,
             '风险中:文档 40-50/IP/天、<=10/小时', 'migration')
    """)


def downgrade() -> None:
    op.drop_index("uq_engine_rate_policies_engine", table_name="engine_rate_policies")
    op.drop_table("engine_rate_policies")
