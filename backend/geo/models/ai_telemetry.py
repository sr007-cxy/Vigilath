"""AI 遥测话题 — ORM + Pydantic schemas.

一个用户可建多个话题(品牌/竞品/行业),每个话题 = 一个检索词 + 一组 query × 一组 AI 引擎.
启用后由 telemetry-service 每天跑一次,结果落到同一 MySQL/SQLite 库的 _runs / _responses 表.

v1(命中追踪) — Topic.target/aliases + Response.hit/hit_excerpt/source_url + QueryHit 矩阵表
v1.1(LLM 诊断) — Response.competitors/citation_domains/answer_format + CellInsight 表
v1.2(周报)    — TopicBriefing 表
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)

from geo.database import Base


# ─────────────────────────── ORM ──────────────────────────────


class AiTelemetryTopicORM(Base):
    """AI 遥测话题配置.

    v1 新增 `target` 字段(被检测的检索词,如 "金诚同达律所")+ `target_aliases_json`(别名列表).
    `queries_json` 升级支持三种形态以做向后兼容:
      - list[str] (旧版,所有 query 视为 topic 创建时即存在)
      - list[{text, created_at}] (v1)
      - list[{text, created_at, cluster_id?}] (v2,picker 端聚类后保留簇归属)
    `clusters_json`:picker 端嵌入 + K-Means 出的簇元数据,跑批结果按 cluster_id 分组用。
    """
    __tablename__ = "ai_telemetry_topics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    # v1 新增 — 检索词与别名;迁移时从 sentiment account 回填
    target = Column(String, nullable=False, default="")
    target_aliases_json = Column(Text, nullable=False, default="[]")    # list[str]

    queries_json = Column(Text, nullable=False, default="[]")   # list[str] | list[{text, created_at, cluster_id?}]
    clusters_json = Column(Text, nullable=False, default="[]")  # list[{cluster_id, label, size}]
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
    """单条 (engine × query) 的回答 + 引用 + 命中判定 + LLM 抽取字段.

    v1 新增:hit / hit_excerpt / source_url(命中判定)
    v1.1 新增:competitors_json / citation_domains_json / answer_format(LLM 跑批后异步抽取)
    """
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

    # v1 命中判定
    hit = Column(Boolean, nullable=False, default=False, index=True)
    hit_excerpt = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)   # 公开 chat URL,如 yiyan.baidu.com/chat/xxx

    # v1.1 LLM 异步抽取
    competitors_json = Column(Text, nullable=True)         # [{name, count, snippet}]
    citation_domains_json = Column(Text, nullable=True)    # [domain, ...]
    answer_format = Column(String, nullable=True)          # listicle/single_recommendation/report/case_study/qa
    # v1.3 提及位置(检索排名简化版):lead(开头)/ body(中段)/ tail(末尾)/ unknown
    # 未命中 (hit=False) 时为 NULL
    mention_position = Column(String, nullable=True)


class AiTelemetryQueryHitORM(Base):
    """(query × engine) cell 维度的当前状态 + 首次命中.

    v1 引用追踪矩阵的存储面板. Response 落库时维护:
      - 找到 (topic_id, query, engine) cell,total_runs+=1
      - 若 hit=True:total_hits+=1,first_hit_at 为空则填,first_hit_response_id 同理
      - status 重算
    """
    __tablename__ = "ai_telemetry_query_hits"
    __table_args__ = (
        UniqueConstraint("topic_id", "query", "engine", name="uq_qh_cell"),
        Index("idx_qh_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    query = Column(Text, nullable=False)
    engine = Column(String, nullable=False)

    status = Column(String, nullable=False, default="pending")    # pending/running/done
    first_hit_at = Column(DateTime, nullable=True)
    first_hit_response_id = Column(Integer, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    total_runs = Column(Integer, nullable=False, default=0)
    total_hits = Column(Integer, nullable=False, default=0)


class AiTelemetryCellInsightORM(Base):
    """v1.1 — Cell 级 LLM 诊断 + 优化建议. 按需触发,按 window 缓存.

    主键含 window_end:同 cell 不同窗口可并存(历史回溯).LLM 调用昂贵,
    生命周期由 telemetry-service /cell-insight 端点管理:
      - 若 cache 同 window_end 已存在且 evidence_response_ids 没变 → 直接读
      - 否则调 LLM 生成新一行(prompt_version 加版本号方便 A/B)
    """
    __tablename__ = "ai_telemetry_cell_insights"
    __table_args__ = (
        UniqueConstraint("topic_id", "query", "engine", "window_end", "prompt_version",
                         name="uq_ci_cell_window"),
        Index("idx_ci_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    query = Column(Text, nullable=False)
    engine = Column(String, nullable=False)

    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)

    verdict = Column(String, nullable=False)              # hit_stable/hit_unstable/near_miss/no_signal/negative_mention
    summary = Column(Text, nullable=False, default="")
    competitors_top3_json = Column(Text, nullable=False, default="[]")     # [{name, count, snippet}]
    recommendations_json = Column(Text, nullable=False, default="[]")      # [{priority, title, action, why}]
    evidence_response_ids_json = Column(Text, nullable=False, default="[]")

    llm_model = Column(String, nullable=False, default="")
    prompt_version = Column(String, nullable=False, default="cell_v1")
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    feedback = Column(String, nullable=True)              # helpful/not_helpful/wrong


class AiTelemetryTopicBriefingORM(Base):
    """v1.2 — Topic 级周报. scheduler 每周一 09:00 北京时间跑."""
    __tablename__ = "ai_telemetry_topic_briefings"
    __table_args__ = (
        UniqueConstraint("topic_id", "period_end", "prompt_version", name="uq_tb_period"),
        Index("idx_tb_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    body_md = Column(Text, nullable=False, default="")
    kpi_snapshot_json = Column(Text, nullable=False, default="{}")
    top_actions_json = Column(Text, nullable=False, default="[]")

    delivered_email_at = Column(DateTime, nullable=True)
    feedback_score = Column(Integer, nullable=True)       # 1-5 星

    llm_model = Column(String, nullable=False, default="")
    prompt_version = Column(String, nullable=False, default="briefing_v1")
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ─────────────────────────── Schemas ──────────────────────────


VALID_ENGINES = {
    "deepseek", "doubao", "qwen", "wenxin", "yuanbao",
    "chatgpt", "claude", "gemini", "grok", "copilot",
}


class ClusterMeta(BaseModel):
    cluster_id: int
    label: str
    size: int


class TopicPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    target: str = Field("", max_length=128)
    target_aliases: list[str] = Field(default_factory=list, max_length=10)
    queries: list[str] = Field(..., min_length=1, max_length=50)
    # 与 queries 同长的 cluster_id 数组,可选;长度不齐或缺省时全部按 0 处理
    query_cluster_ids: Optional[list[int]] = None
    clusters: Optional[list[ClusterMeta]] = None
    engines: list[str] = Field(..., min_length=1, max_length=10)
    enabled: bool = True


class TopicOut(BaseModel):
    id: int
    name: str
    target: str
    target_aliases: list[str]
    queries: list[str]
    query_cluster_ids: list[int]
    clusters: list[ClusterMeta]
    engines: list[str]
    enabled: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, r: AiTelemetryTopicORM) -> "TopicOut":
        # queries_json 兼容三种形态:list[str] / list[{text, created_at}] / list[{text, created_at, cluster_id}]
        raw = json.loads(r.queries_json or "[]")
        queries: list[str] = []
        cluster_ids: list[int] = []
        for q in raw:
            if isinstance(q, dict):
                t = q.get("text") or ""
                if t:
                    queries.append(t)
                    cid = q.get("cluster_id")
                    # 缺失 → -1(未分类),前端用 ≥0 判定是否有有效簇
                    cluster_ids.append(int(cid) if isinstance(cid, int) else -1)
            elif isinstance(q, str):
                queries.append(q)
                cluster_ids.append(-1)
        clusters_raw = json.loads(r.clusters_json or "[]")
        clusters = [ClusterMeta(**c) for c in clusters_raw if isinstance(c, dict)]
        return cls(
            id=r.id,
            name=r.name,
            target=r.target or "",
            target_aliases=json.loads(r.target_aliases_json or "[]"),
            queries=queries,
            query_cluster_ids=cluster_ids,
            clusters=clusters,
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
    source_url: Optional[str] = None
    error: Optional[str]
    created_at: datetime
    hit: bool = False
    hit_excerpt: Optional[str] = None
    mention_position: Optional[str] = None


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


class ClusterBreakdownItem(BaseModel):
    cluster_id: int
    label: str
    query_count: int          # 这一簇里的 query 数(topic 维度,不分时段)
    response_count: int       # 本期成功 response 数(query × engine,error 不算)
    mention_count: int        # response 里有品牌词的数
    mention_rate: float       # mention_count / response_count(0 → 0.0)
    citation_count: int       # response.citations 累计


class IntentBreakdownOut(BaseModel):
    topic_id: int
    period_days: int
    brand_keywords: list[str]
    clusters: list[ClusterBreakdownItem]   # 按 size 降序
    uncategorized: ClusterBreakdownItem    # cluster_id 无匹配 / 老话题无聚类信息时的兜底桶


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


# ─────────────────── v1 引用追踪 schemas ────────────────────


class QueryHitCell(BaseModel):
    """矩阵中一个 cell."""
    query: str
    engine: str
    status: str                          # pending/running/done
    first_hit_at: Optional[datetime]
    first_hit_response_id: Optional[int]
    last_checked_at: Optional[datetime]
    total_runs: int
    total_hits: int


class EngineFirstHit(BaseModel):
    """时间线 — 一个引擎首次命中的日期(任一 query 命中即算)."""
    engine: str
    first_hit_at: Optional[datetime]
    first_hit_query: Optional[str]
    days_after_start: Optional[int]     # = (first_hit_at - topic.created_at).days,空 = 还没命中


class TrackingMatrixOut(BaseModel):
    topic_id: int
    target: str
    target_aliases: list[str]
    queries: list[str]
    engines: list[str]
    started_at: datetime                # topic.created_at
    cells: list[QueryHitCell]
    timeline: list[EngineFirstHit]
    total_runs: int                     # 该 topic 历史 run 总数
    total_cells: int                    # = len(queries) × len(engines)
    hit_cells: int                      # status=done 且 total_hits>=1
    hit_cells_pct: float                # = hit_cells / total_cells × 100


# ─────────────────── v1 / v1.1 drawer 详情 ──────────────────


class CellEvidence(BaseModel):
    """drawer 里展示的"历次答复"项."""
    response_id: int
    run_id: int
    created_at: datetime
    engine: str
    query: str
    hit: bool
    hit_excerpt: Optional[str]
    source_url: Optional[str]
    answer: str
    citations: list[RunNowCitation]
    mention_position: Optional[str] = None


class CellInsightRec(BaseModel):
    priority: str                       # P0/P1/P2
    title: str
    action: str
    why: str


class CompetitorMention(BaseModel):
    name: str
    count: int
    snippet: str


class CellInsightOut(BaseModel):
    id: int
    topic_id: int
    query: str
    engine: str
    window_start: datetime
    window_end: datetime
    verdict: str
    summary: str
    competitors_top3: list[CompetitorMention]
    recommendations: list[CellInsightRec]
    answer_format: Optional[str] = None
    citation_domains: list[str] = Field(default_factory=list)
    evidence_response_ids: list[int]
    llm_model: str
    prompt_version: str
    generated_at: datetime
    feedback: Optional[str]


class CellDrawerOut(BaseModel):
    """drawer 完整内容 — cell 状态 + 历次答复 + LLM 诊断(如果有)."""
    cell: QueryHitCell
    evidence: list[CellEvidence]
    insight: Optional[CellInsightOut]    # None = 还没生成(用户点"分析"才出)


# ─────────────────── v1.2 周报 ──────────────────


class BriefingAction(BaseModel):
    priority: str                       # P0/P1/P2
    title: str
    why: str
    how: str


class BriefingOut(BaseModel):
    id: int
    topic_id: int
    period_start: datetime
    period_end: datetime
    body_md: str
    kpi_snapshot: dict
    top_actions: list[BriefingAction]
    delivered_email_at: Optional[datetime]
    feedback_score: Optional[int]
    llm_model: str
    prompt_version: str
    generated_at: datetime


class FeedbackPayload(BaseModel):
    feedback: str = Field(..., pattern="^(helpful|not_helpful|wrong)$")


class BriefingFeedbackPayload(BaseModel):
    score: int = Field(..., ge=1, le=5)


# ─────────────────── v1.3 SAIV / 竞品份额 ──────────────────────


class CompetitorShareEntry(BaseModel):
    name: str
    count: int
    pct: float                          # 该竞品 / 全行业(品牌 + 全部竞品) 提及次数 × 100


class PositionDist(BaseModel):
    """命中位置分布 — 检索排名简化版."""
    lead: int = 0
    body: int = 0
    tail: int = 0
    unknown: int = 0


class ShareOfVoiceOut(BaseModel):
    """声量份额 — 品牌 vs 竞品 在最近 window 内被 AI 提及次数的对比.

    SAIV(Share of AI Voice) = brand_count / (brand_count + sum(competitors.count)) × 100.
    一个 ResponseORM:hit=True 贡献 brand_count + 1;
                     competitors_json 里每条 {name, count} 贡献到对应 name 的累计.
    """
    topic_id: int
    target: str
    period_days: int                    # 统计窗口
    brand_count: int                    # 品牌在所有 Response 答复里被提到的次数
    competitors_count_total: int        # 所有竞品提及的总次数
    saiv_pct: float                     # 0-100,品牌占比
    competitors: list[CompetitorShareEntry]   # 按 count 降序 top 10
    position_dist: PositionDist         # 命中位置分布(lead/body/tail/unknown)
    optimal_rate_pct: float             # AI 答案优选率 = sum(total_hits) / sum(total_runs) × 100
    total_runs: int                     # 该 topic 累计 run 数
    sample_size: int                    # 用于聚合的 Response 行数(含 hit=False)
