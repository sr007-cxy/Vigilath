"""ai_telemetry_topics.schedule_tier server_default weekly → off

Revision ID: e3a9c5d17b42
Revises: c8e5a1d94f72
Create Date: 2026-09-21 12:00:00.000000

New topics are not included in periodic batch runs by default (manual mode): previously the default was weekly, and as soon as wizard step 3 was saved, the batch-run center
dispatched the whole matrix at the next hourly tick and pre-deducted credits. Now it only runs when clicking 「生成 GEO 策略
优化方案」 ("generate GEO strategy optimization plan") / 「新增跑批」 ("add batch run") on the plan page, and only runs cells that have not succeeded yet.

Only the column default changes; **existing rows are not backfilled**: running topics keep their current pace. The ORM-side default is changed too
(geo/models/ai_telemetry.py); changing server_default here only makes inserts that bypass the ORM consistent.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3a9c5d17b42"
down_revision: Union[str, None] = "c8e5a1d94f72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.alter_column(
            "schedule_tier", existing_type=sa.String(length=16), existing_nullable=False,
            server_default="off")


def downgrade() -> None:
    with op.batch_alter_table("ai_telemetry_topics", schema=None) as batch_op:
        batch_op.alter_column(
            "schedule_tier", existing_type=sa.String(length=16), existing_nullable=False,
            server_default="weekly")
