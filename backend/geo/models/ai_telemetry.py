"""AI 遥测话题 — ORM + Pydantic schemas.

一个用户可建多个话题(品牌/竞品/行业),每个话题 = 一组 query × 一组 AI 引擎.
启用后由 telemetry-service 每天跑一次,结果落到 telemetry-service 侧的 MySQL 表.
此处只管"用户的话题配置"和"最近一次跑批的元数据回写".
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from geo.database import Base


# ─────────────────────────── ORM ──────────────────────────────


class AiTelemetryTopicORM(Base):
    """AI 遥测话题配置."""
    __tablename__ = "ai_telemetry_topics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    queries_json = Column(Text, nullable=False, default="[]")   # list[str]
    engines_json = Column(Text, nullable=False, default="[]")   # list[engine_id]
    enabled = Column(Boolean, nullable=False, default=True)

    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)             # success / failed / running

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiTelemetryRunORM(Base):
    """单次跑批的元数据(每天每话题一行)."""
    __tablename__ = "ai_telemetry_runs"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")   # running / success / failed
    error = Column(Text, nullable=True)


class AiTelemetryResponseORM(Base):
    """单条 (engine × query) 的回答 + 引用."""
    __tablename__ = "ai_telemetry_responses"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("ai_telemetry_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    engine = Column(String, nullable=False)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, default="")
    citations_json = Column(Text, nullable=False, default="[]")
    video_url = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ─────────────────────────── Schemas ──────────────────────────


VALID_ENGINES = {
    "deepseek", "doubao", "qwen", "wenxin", "yuanbao",
    "chatgpt", "claude", "gemini", "grok", "copilot",
}


class TopicPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    queries: list[str] = Field(..., min_length=1, max_length=10)
    engines: list[str] = Field(..., min_length=1, max_length=10)
    enabled: bool = True


class TopicOut(BaseModel):
    id: int
    name: str
    queries: list[str]
    engines: list[str]
    enabled: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, r: AiTelemetryTopicORM) -> "TopicOut":
        return cls(
            id=r.id,
            name=r.name,
            queries=json.loads(r.queries_json or "[]"),
            engines=json.loads(r.engines_json or "[]"),
            enabled=r.enabled,
            last_run_at=r.last_run_at,
            last_run_status=r.last_run_status,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


class RunNowCitation(BaseModel):
    url: str
    domain: str
    title: str = ""


class RunNowResult(BaseModel):
    engine: str
    query: str
    answer: str = ""
    citations: list[RunNowCitation] = Field(default_factory=list)
    error: Optional[str] = None


class RunOut(BaseModel):
    id: int
    topic_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    response_count: int = 0

    @classmethod
    def from_orm_row(cls, r, response_count: int = 0) -> "RunOut":
        return cls(
            id=r.id, topic_id=r.topic_id, status=r.status,
            started_at=r.started_at, finished_at=r.finished_at,
            error=r.error, response_count=response_count,
        )


class ResponseOut(BaseModel):
    id: int
    engine: str
    query: str
    answer: str
    citations: list[RunNowCitation]
    video_url: Optional[str]
    error: Optional[str]
    created_at: datetime


class KpiBlock(BaseModel):
    value: float
    delta_pct: Optional[float] = None    # 与上一周期相比,百分比;None 表示不可比
    sparkline: list[float] = Field(default_factory=list)  # 周期内每日序列


class TrendPoint(BaseModel):
    date: str   # YYYY-MM-DD
    values: dict[str, int]   # engine -> citation count for that day


class DomainCount(BaseModel):
    domain: str
    count: int
    pct: float                    # 0-100,本 domain 占总 citations 的百分比


class OwnedSplit(BaseModel):
    owned: int                    # 命中自家关键词的 citation 数
    other: int                    # 未命中的 citation 数
    owned_pct: float              # 0-100
    delta_pct: Optional[float] = None   # 与上一周期 owned_pct 相比


class OverviewOut(BaseModel):
    topic_id: int
    period_days: int
    brand_keywords: list[str]
    visibility: KpiBlock         # 0-100,品牌提及率 × 100
    citations: KpiBlock          # 引用总数
    growth: KpiBlock             # 引用增长率(= citations.delta_pct,单独成卡显示主数)
    engines_covered: KpiBlock    # 有 ≥1 成功 response 的引擎数
    engines_total: int
    trend: list[TrendPoint]      # 按天 bucket 的引用数,每天 dict[engine]=count
    engines: list[str]           # 趋势图要绘制的引擎(本期出现过的)
    top_domains: list[DomainCount]                  # 按引用次数降序前 10
    owned_split: OwnedSplit                         # 自家 vs 其他
    engine_domain_matrix: dict[str, dict[str, int]] # engine -> {domain: count},只含 top_domains
