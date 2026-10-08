"""Ruanwenjiang media resource mirror + sync batch records

Revision ID: c4a7e2b9d631
Revises: b7e2f9a4c518
Create Date: 2026-08-04 16:20:00.000000

Publishing plans must choose placement platforms based on measured cited sources, and platforms like Zhihu/Sohu/Baijiahao/Toutiao have
no accounts of our own, so publishing there relies on Ruanwenjiang's paid placement service. Its resource pool has about 65k entries and the list API cannot look up by id,
so a local mirror must be persisted before placeable-channel decisions and cost estimates are possible.

Hand-written rather than autogenerate: geo/__init__.py does not register compliance / engine_sessions /
agent / vscl_rl and other models, so autogenerate would emit drop_table for them.

Only creates tables without backfill; initial data comes from a manual run of `python -m geo.services.rwj_media_sync`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a7e2b9d631'
down_revision: Union[str, None] = 'b7e2f9a4c518'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rwj_media_resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("res_type", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("title_key", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("channel_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("region_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("include_name", sa.String(length=64), nullable=False, server_default=""),
        # Reference price: ref_ prefix = not the deal price; must re-verify the price against the source in real time before ordering
        sa.Column("ref_price_cents", sa.Integer(), nullable=True),
        sa.Column("ref_price_level", sa.SmallInteger(), nullable=True),
        sa.Column("ref_price_l1_cents", sa.Integer(), nullable=True),
        sa.Column("ref_price_l2_cents", sa.Integer(), nullable=True),
        sa.Column("ref_price_l3_cents", sa.Integer(), nullable=True),
        sa.Column("ref_price_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("delivery_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ref_price_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ref_price_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_rate", sa.Float(), nullable=True),
        sa.Column("publish_time", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("pc_weigh", sa.Integer(), nullable=True),
        sa.Column("wap_weigh", sa.Integer(), nullable=True),
        sa.Column("case_link", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("entrance_link", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("field_ids_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("field_names_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("miss_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("last_seen_batch", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("delisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("res_type", "resource_id", name="uq_rwj_media_type_rid"),
    )
    op.create_index("ix_rwj_media_resources_id", "rwj_media_resources", ["id"])
    op.create_index("idx_rwj_media_type_active_price", "rwj_media_resources",
                    ["res_type", "is_active", "ref_price_cents"])
    op.create_index("idx_rwj_media_type_active_rate", "rwj_media_resources",
                    ["res_type", "is_active", "publish_rate"])
    op.create_index("idx_rwj_media_platform", "rwj_media_resources", ["platform", "is_active"])
    op.create_index("idx_rwj_media_channel", "rwj_media_resources", ["res_type", "channel_name"])
    op.create_index("idx_rwj_media_title_key", "rwj_media_resources", ["title_key"])
    op.create_index("idx_rwj_media_batch", "rwj_media_resources", ["last_seen_batch"])

    op.create_table(
        "rwj_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("res_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="running"),
        sa.Column("account_level", sa.SmallInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("total_reported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rwj_sync_runs_id", "rwj_sync_runs", ["id"])
    op.create_index("idx_rwj_sync_type_started", "rwj_sync_runs", ["res_type", "started_at"])


def downgrade() -> None:
    op.drop_index("idx_rwj_sync_type_started", table_name="rwj_sync_runs")
    op.drop_index("ix_rwj_sync_runs_id", table_name="rwj_sync_runs")
    op.drop_table("rwj_sync_runs")
    for ix in ("idx_rwj_media_batch", "idx_rwj_media_title_key", "idx_rwj_media_channel",
               "idx_rwj_media_platform", "idx_rwj_media_type_active_rate",
               "idx_rwj_media_type_active_price", "ix_rwj_media_resources_id"):
        op.drop_index(ix, table_name="rwj_media_resources")
    op.drop_table("rwj_media_resources")
