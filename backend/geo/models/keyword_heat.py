"""Keyword search-heat cache -- persists collected results from external indexes / model estimates.

The heatmap originally only had "on-site AI hit heat" (hit counts within a window), which is exposure
strength we observe ourselves and does not reflect real market search demand. This table stores the second metric definition, "search heat",
written by scheduled collection in `geo.services.keyword_heat`; the report API only reads the cache -- the request path
never calls out, and a collection failure just leaves heat missing without affecting report numbers.

One row per keyword (keyword + geo unique). keyword stores the normalized form; the original form is kept in detail_json.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from geo.database import Base

# Collection source ids -- map 1:1 to frontend source labels (frontend copy lives in pages/Reports/lang.ts)
SOURCE_BAIDU_INDEX = "baidu_index"        # Baidu Index (real daily average search index)
SOURCE_GOOGLE_TRENDS = "google_trends"    # Google Trends (relative heat 0-100)
SOURCE_OCEANENGINE = "oceanengine"        # Ocean Engine Suanshu (Douyin-side content heat)
SOURCE_LLM = "llm"                        # Model estimate (DeepSeek)

ALL_SOURCES = (SOURCE_BAIDU_INDEX, SOURCE_GOOGLE_TRENDS, SOURCE_OCEANENGINE, SOURCE_LLM)


class KeywordHeatORM(Base):
    __tablename__ = "keyword_heat"
    __table_args__ = (
        UniqueConstraint("keyword", "geo", name="uq_keyword_heat_word_geo"),
        Index("idx_keyword_heat_expires", "expires_at"),
    )

    id = Column(Integer, primary_key=True)
    keyword = Column(String, nullable=False)          # Normalized form (lowercase / extra whitespace removed)
    geo = Column(String, nullable=False, default="cn")  # cn=domestic (Baidu Index) / global=overseas (Google Trends)
    # Core short term extracted from long-tail questions -- external indexes only have data for core terms; "XX哪个品牌好" ("which XX brand is best") returns nothing
    core_word = Column(String, nullable=False, default="")
    heat = Column(Integer, nullable=False, default=0)  # 0-100 normalized heat (comparable within the same source and batch)
    raw_value = Column(Integer, nullable=True)         # Raw source value (Baidu daily index / Google 0-100 / model 0-100)
    source = Column(String, nullable=False)            # One of ALL_SOURCES
    detail_json = Column(Text, nullable=True)          # Collection details: original term, curve, model name, degradation reason
    fetched_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)      # Still readable after expiry (marked stale); refreshed by the scheduled job
