"""Add hourly usage counters to engine_sessions: change egress rate limiting from "daily total" to "rate"

Problem measured on 2026-08-30: egress only had a daily cap (ip_cap), which controls "how many per day" but not "how fast they are used".
The result was the worst combination: three hours from 02:00-04:00 burned the 25-request quota at maximum instantaneous density,
then the engine sat idle for the remaining 20 hours and could not dispatch a single job that day (that is how the 25 snap jobs dragged on into
"no session available, judged terminal").

And deepseek's risk control looks at **rate**: only dense requests from the same egress in a short time look like a bot. So hourly rate limiting
improves both ends: 3 per hour per IP -> 72 per day (2.4x the daily limit of 30), while peak density is actually lower.

The column shape matches the existing used_today / used_date exactly (counter + window key, auto-reset across windows),
introducing no new mechanism. The window key uses local time 'YYYY-MM-DD HH', aligned with used_date's local-day metric definition.

Revision ID: b4e7d2a91f38
Revises: e9d3a1f7c584
"""
from alembic import op
import sqlalchemy as sa

revision = "b4e7d2a91f38"
down_revision = "e9d3a1f7c584"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default 0: existing rows must not be NULL, otherwise every sum needs null checks
    op.add_column("engine_sessions",
                  sa.Column("used_hour", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("engine_sessions",
                  sa.Column("used_hour_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("engine_sessions", "used_hour_key")
    op.drop_column("engine_sessions", "used_hour")
