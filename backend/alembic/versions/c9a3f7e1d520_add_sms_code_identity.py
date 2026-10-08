"""Add identity (WeChat openid) to sms_codes: change the rate-limit key for street promotion scenarios

Revision ID: c9a3f7e1d520
Revises: b7e4c1f92a08
Create Date: 2026-09-08

Why this column: verification code rate limiting used to have only two keys, "phone number + egress IP". At a street promotion booth
everyone shares one hotspot egress IP, and SMS_HOURLY_LIMIT_PER_IP=8 locks everyone out at the 9th person,
with nobody on site able to tell why. The H5 channel only gets an openid after WeChat web authorization,
which is a much stronger identity than IP, so when an openid is passed we count by it and ignore the IP.

nullable: old rows lack this column, and the PC QR-code channel may not pass it either; empty means "count by IP",
no backfill needed. The index serves the daily limit count by identity + created_at in send_code.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9a3f7e1d520"
down_revision: Union[str, None] = "b7e4c1f92a08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Idempotent: in some environments this table was created by create_all (models already declare this column),
    # and add_column on such a DB would raise DuplicateColumn and block the whole migration chain.
    if _has_column("sms_codes", "identity"):
        return
    op.add_column("sms_codes", sa.Column("identity", sa.String(length=64), nullable=True))
    op.create_index("ix_sms_codes_identity", "sms_codes", ["identity"])


def downgrade() -> None:
    if not _has_column("sms_codes", "identity"):
        return
    op.drop_index("ix_sms_codes_identity", table_name="sms_codes")
    op.drop_column("sms_codes", "identity")
