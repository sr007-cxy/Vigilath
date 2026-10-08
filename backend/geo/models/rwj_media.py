"""Ruanwenjiang media resource mirror -- local resource library synced once a day.

Why persist to DB (dictated by platform constraints):
  - The resource pool is ~65k entries (websites 13111 / self-media 36864 / short video 15631 / Q&A 38 / ghostwriting 15),
    the list API caps pageSize at 50 and **has no lookup-by-resource_id parameter**, only page-and-match.
    Without a local copy we cannot answer "can we publish on Zhihu at all, how many accounts, roughly how much".
  - Publishing plans pick target platforms based on measured cited sources (services/source_plan.py), and for Zhihu/Sohu/
    Baijiahao/Toutiao we have no own accounts, only paid placement via Ruanwenjiang -- publishability checks need
    a local resource library; we cannot page through 1300 pages on every scheduling run.

**Price metric definition (hard constraint)**: all price columns in this table use the `ref_` prefix = reference price, only for scheduling filters,
candidate pool ranking and cost estimates. The Ruanwenjiang API docs explicitly require "re-fetch list prices before submitting, do not use
long-cached prices"; the order path (ruanwenjiang_publisher.verify_resource -> publish) must
keep re-checking prices live at the source. **This module must not be imported by ruanwenjiang_publisher.**
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer, SmallInteger, String, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from geo.database import Base


class RwjMediaResourceORM(Base):
    """One paid placement media resource (a media outlet/account under a given site and type).

    (provider, res_type, resource_id) is jointly unique -- each of the five business types has its own resource pool,
    resource_id is very likely auto-incremented per type and may collide across types, and no platform API proves it
    globally unique; the two sites (Ruanwenjiang/Meijieku) number independently, so **resource_id of same-named media
    across sites is not interchangeable**, hence provider must be part of the unique key.
    """
    __tablename__ = "rwj_media_resources"
    __table_args__ = (
        UniqueConstraint("provider", "res_type", "resource_id", name="uq_rwj_media_prov_type_rid"),
        Index("idx_rwj_media_type_active_price", "res_type", "is_active", "ref_price_cents"),
        Index("idx_rwj_media_type_active_rate", "res_type", "is_active", "publish_rate"),
        Index("idx_rwj_media_platform", "platform", "is_active"),
        Index("idx_rwj_media_channel", "res_type", "channel_name"),
        Index("idx_rwj_media_title_key", "title_key"),
        Index("idx_rwj_media_batch", "last_seen_batch"),
        Index("idx_rwj_media_provider", "provider", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Source site: rwj=Ruanwenjiang / mjk=Meijieku (keys of rwj_client.SITES)
    provider = Column(String(length=8), nullable=False, default="rwj", server_default="rwj")
    res_type = Column(String(length=16), nullable=False)      # Website/Wemedia/Shortvideo/Question/Ghostwrite
    resource_id = Column(Integer, nullable=False)             # Resource id on that site
    title = Column(String(length=255), nullable=False, default="")
    title_key = Column(String(length=255), nullable=False, default="")  # Normalized title, for fuzzy search
    remarks = Column(Text, nullable=True)

    # -- Normalized platform name on our side (Sohu/Zhihu/Baijiahao...); left empty if unrecognized, bucketed under the "Ruanwenjiang" portal bucket
    platform = Column(String(length=32), nullable=False, default="")
    channel_name = Column(String(length=64), nullable=False, default="")  # Raw field_1_name
    region_name = Column(String(length=64), nullable=False, default="")   # field_3_name
    include_name = Column(String(length=64), nullable=False, default="")  # Indexing/verification, taken per type

    # -- Reference price (the ref_ prefix is deliberate: the column name itself says this is not the transaction price)
    ref_price_cents = Column(Integer, nullable=True)          # Direct-publish reference price at the current membership level (cents)
    ref_price_level = Column(SmallInteger, nullable=True)     # Membership level this price corresponds to
    ref_price_l1_cents = Column(Integer, nullable=True)       # Three direct-publish price tiers, so a level change does not require a full table refresh
    ref_price_l2_cents = Column(Integer, nullable=True)
    ref_price_l3_cents = Column(Integer, nullable=True)
    ref_price_json = Column(Text, nullable=False, default="{}")   # All price_* fields as-is
    delivery_json = Column(Text, nullable=False, default="[]")    # price_type delivery method array as-is
    ref_price_synced_at = Column(DateTime(timezone=True), nullable=True)
    ref_price_changed_at = Column(DateTime(timezone=True), nullable=True)

    # -- Quality metrics
    publish_rate = Column(Float, nullable=True)               # Publish success rate %, for sorting
    publish_time = Column(String(length=64), nullable=False, default="")
    pc_weigh = Column(Integer, nullable=True)
    wap_weigh = Column(Integer, nullable=True)
    case_link = Column(String(length=512), nullable=False, default="")
    entrance_link = Column(String(length=512), nullable=False, default="")

    # -- All dimension tags: flattened into two JSON blobs (ids and Chinese names), not into columns --
    #    the platform makes no promise about field_N value shapes (may be multi-valued), raw JSON is safest;
    #    for actual dimension filtering use the three indexed columns platform / channel_name / region_name.
    field_ids_json = Column(Text, nullable=False, default="{}")
    field_names_json = Column(Text, nullable=False, default="{}")
    raw_json = Column(Text, nullable=False, default="{}")      # Full raw row JSON, so new platform fields need no re-fetch

    # -- Sync state
    is_active = Column(Boolean, nullable=False, default=True)
    miss_count = Column(SmallInteger, nullable=False, default=0)   # Consecutive full syncs where it was missing, guards against pagination drift false deletes
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_batch = Column(String(length=32), nullable=False, default="")
    delisted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RwjSyncRunORM(Base):
    """Batch record of one sync (one row per site x type), for the UI to show
    "last sync time / count / coverage"."""
    __tablename__ = "rwj_sync_runs"
    __table_args__ = (
        Index("idx_rwj_sync_type_started", "res_type", "started_at"),
        Index("idx_rwj_sync_provider_started", "provider", "started_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(length=32), nullable=False, default="")
    provider = Column(String(length=8), nullable=False, default="rwj", server_default="rwj")
    res_type = Column(String(length=16), nullable=False)
    status = Column(String(length=12), nullable=False, default="running")  # running/success/partial/failed
    account_level = Column(SmallInteger, nullable=True)       # Membership level snapshot for this round, used to pick the price tier in library search
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    total_reported = Column(Integer, nullable=False, default=0)   # total returned by the platform
    pages_fetched = Column(Integer, nullable=False, default=0)
    rows_seen = Column(Integer, nullable=False, default=0)
    inserted = Column(Integer, nullable=False, default=0)
    updated = Column(Integer, nullable=False, default=0)
    unchanged = Column(Integer, nullable=False, default=0)
    price_changed = Column(Integer, nullable=False, default=0)
    delisted = Column(Integer, nullable=False, default=0)
    coverage_pct = Column(Float, nullable=False, default=0.0)
    error = Column(Text, nullable=True)
