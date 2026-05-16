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
    # v2(Phase C)新增 — 行业 / 业务定位.之前只是 suggest-queries 的临时参数,
    # 现在持久化到 topic 上,编辑时可回显
    industry = Column(Text, nullable=False, default="")

    # v2(Phase C)新增 — 种子提示词列表,审核固化只增不改
    # list[{text, status, submitted_at, approved_at?, rejected_at?, reviewer_id?}]
    seed_prompts_json = Column(Text, nullable=False, default="[]")

    # v2(Phase C)起 query 项 schema 扩展加 status / submitted_at / approved_at:
    # list[str] | list[{text, created_at, cluster_id?}] | list[{text, status, submitted_at, ...}]
    # v3(Phase D)起 query 项再加 `selected`:用户从扩展池里勾选为监测对象的 ≤50 个
    queries_json = Column(Text, nullable=False, default="[]")
    clusters_json = Column(Text, nullable=False, default="[]")  # list[{cluster_id, label, size}]
    engines_json = Column(Text, nullable=False, default="[]")   # list[engine_id]
    enabled = Column(Boolean, nullable=False, default=True)

    # v3(Phase D)审核工作流:整张申请的状态机 + 资料快照
    # profile_json:7 大模块品牌画像表单(画像基础 / 内容创作方向 / 品牌主体 / 服务核心 / 用户痛点 / 品牌故事 / 创作边界)
    profile_json = Column(Text, nullable=False, default="{}")
    submission_status = Column(String, nullable=False, default="draft")
    # ↑ draft / pending / approved / rejected — 与 seed_prompts_json[].status 是两层:整张申请 + 单条种子词
    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # 主题日志:每次画像/种子/queries 变更追加一条 {at, actor_id, actor_role, field, before, after}
    topic_changelog_json = Column(Text, nullable=False, default="[]")
    # 泛化日志:种子词 → LLM 扩展 query 候选的调用记录 [{at, seed, model, expanded_count, raw_excerpt}]
    expansion_log_json = Column(Text, nullable=False, default="[]")

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


class AiTelemetryTopicExecutionPlanORM(Base):
    """v3(Phase D)— 审核通过时生成的执行计划书快照.

    包含:画像 + 监测问题 + 主题/泛化日志的不变副本(后续即便 topic 被编辑也保留生成时形态)
    + 指向那次"通过即跑"的 run_id,运行进度由 _query_hits 表实时聚合.
    """
    __tablename__ = "ai_telemetry_topic_execution_plans"
    __table_args__ = (Index("idx_tep_topic", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    generated_by_reviewer_id = Column(Integer, nullable=True)

    # 项目总体状况快照:画像 + 监测问题数 + 引擎清单
    overview_json = Column(Text, nullable=False, default="{}")
    # 主题日志快照(生成那一刻的 topic_changelog_json 副本)
    topic_changelog_snapshot_json = Column(Text, nullable=False, default="[]")
    # 泛化日志快照(生成那一刻的 expansion_log_json 副本)
    expansion_log_snapshot_json = Column(Text, nullable=False, default="[]")
    # 监测问题清单快照(approved 且 selected 的 query 文本列表)
    monitored_queries_snapshot_json = Column(Text, nullable=False, default="[]")

    run_id = Column(Integer, ForeignKey("ai_telemetry_runs.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="generating")   # generating / ready / failed
    error = Column(Text, nullable=True)


class TopicGeneratedDocORM(Base):
    """v3(Phase D)— 基于画像 + 通过的监测问题,LLM 生成的内容文案稿.

    生命周期:draft → pending_review → approved/rejected → published(approved 后选发布平台 / 媒体)
    publish_targets_json 不真实调外部平台 OpenAPI,只记录"标注为已发布到哪里".
    """
    __tablename__ = "topic_generated_docs"
    __table_args__ = (
        Index("idx_tgd_topic", "topic_id"),
        Index("idx_tgd_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    execution_plan_id = Column(Integer, ForeignKey("ai_telemetry_topic_execution_plans.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    source_query_text = Column(Text, nullable=False, default="")
    title = Column(String, nullable=False, default="")
    body_markdown = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")          # 200 字内摘要 — 卡片用
    llm_model = Column(String, nullable=False, default="")
    generation_error = Column(Text, nullable=True)

    status = Column(String, nullable=False, default="draft")
    # ↑ draft(LLM 刚出)/ pending_review(admin 勾入审核)/ approved / rejected / published
    selected_for_review = Column(Boolean, nullable=False, default=False)
    review_decision_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reject_reason = Column(Text, nullable=True)
    # [{platform: "抖音"|"小红书"|"视频号"|"公众号", media: "...", marked_at, marked_by}]
    publish_targets_json = Column(Text, nullable=False, default="[]")


# ─────────────────────────── Schemas ──────────────────────────


VALID_ENGINES = {
    "deepseek", "doubao", "qwen", "wenxin", "yuanbao",
    "chatgpt", "claude", "gemini", "grok", "copilot",
}


class ClusterMeta(BaseModel):
    cluster_id: int
    label: str
    size: int


# Phase C — 审核固化:种子词 + query 的审核状态机
ReviewStatus = str  # "pending" | "approved" | "rejected"


class SeedPromptItem(BaseModel):
    text: str
    status: ReviewStatus = "pending"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None


class QueryItem(BaseModel):
    text: str
    cluster_id: Optional[int] = None
    # Phase C 审核固化:legacy 数据 migration 时默认 "approved"
    status: ReviewStatus = "approved"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    # Phase D 监测勾选:用户从扩展池里勾的 ≤50 个 → selected=True;legacy 数据默认 True
    selected: bool = True


# ─────────────── Phase D — 品牌画像(7 大模块) ──────────────


class BrandProfile(BaseModel):
    """用户提交资料时填的 7 大模块表单.

    画像跟 topic 一一对应,序列化进 topic.profile_json.
    必填项见各字段 Field 注解(min_length / 默认值);submit-for-review 端点会再做完整校验.
    """
    # 一、画像基础标识
    profile_name: str = Field("", max_length=128)               # 画像名称
    company_full_name: str = Field("", max_length=256)          # 公司 / 品牌全称
    company_short_name: str = Field("", max_length=64)          # 公司 / 品牌简称
    industry: str = Field("", max_length=128)                   # 所属行业 / 赛道
    core_business_lines: list[str] = Field(default_factory=list, max_length=20)  # 核心业务线(多选)
    service_geo: str = Field("", max_length=256)                # 服务地域

    # 二、内容创作方向(都是多选)
    creation_directions: list[str] = Field(default_factory=list, max_length=20)  # 创作方向
    copywriting_types: list[str] = Field(default_factory=list, max_length=20)    # 文案类型偏好
    target_platforms: list[str] = Field(default_factory=list, max_length=20)     # 适配平台
    content_tones: list[str] = Field(default_factory=list, max_length=20)        # 内容调性偏好
    content_redlines: list[str] = Field(default_factory=list, max_length=20)     # 内容雷区 / 禁止项(选填)

    # 三、品牌主体信息
    team_size: str = Field("", max_length=64)                                    # 公司 / 团队规模(选填)
    founded_year: str = Field("", max_length=64)                                 # 成立时间 / 从业年限
    core_credentials: list[str] = Field(default_factory=list, max_length=20)     # 核心荣誉 / 背书资质
    brand_diff_tags: list[str] = Field(default_factory=list, max_length=10)      # 品牌差异化标签(3-5 个)

    # 四、产品 / 服务核心信息
    core_service_overview: str = Field("", max_length=2000)                      # 核心服务概述
    service_features: list[str] = Field(default_factory=list, max_length=20)     # 服务核心特点
    service_process: list[str] = Field(default_factory=list, max_length=20)      # 服务关键流程 / 环节
    target_scenarios: list[str] = Field(default_factory=list, max_length=20)     # 服务覆盖场景 / 客户类型
    service_guarantees: list[str] = Field(default_factory=list, max_length=20)   # 服务交付保障(选填)

    # 五、目标用户与痛点
    target_audience: list[str] = Field(default_factory=list, max_length=20)      # 核心目标用户画像
    user_pain_points: list[str] = Field(default_factory=list, max_length=20)     # 用户核心痛点
    user_faqs: list[str] = Field(default_factory=list, max_length=20)            # 用户高频疑问 / 常见误区
    decision_factors: list[str] = Field(default_factory=list, max_length=20)     # 用户决策关键因素

    # 六、品牌故事与情感素材
    brand_story: str = Field("", max_length=2000)                                # 品牌故事 / 成立初衷
    key_person_story: str = Field("", max_length=2000)                           # 核心人物故事(选填)
    case_stories: list[str] = Field(default_factory=list, max_length=20)         # 典型案例(选填)
    brand_values: str = Field("", max_length=1000)                               # 品牌价值观 / 服务理念

    # 七、补充素材与创作边界
    available_materials: list[str] = Field(default_factory=list, max_length=20)  # 可提供的素材类型(选填)
    brand_slogan: str = Field("", max_length=256)                                # Slogan / 宣传语(选填)
    core_message: str = Field("", max_length=1000)                               # 本次内容核心信息
    extra_notes: str = Field("", max_length=2000)                                # 其他补充说明(选填)


# 7 大模块的必填字段清单 — submit-for-review 校验用
PROFILE_REQUIRED_FIELDS: tuple[str, ...] = (
    "profile_name", "company_full_name", "company_short_name",
    "industry", "core_business_lines", "service_geo",
    "creation_directions", "copywriting_types", "target_platforms", "content_tones",
    "founded_year", "core_credentials", "brand_diff_tags",
    "core_service_overview", "service_features", "service_process", "target_scenarios",
    "target_audience", "user_pain_points", "user_faqs", "decision_factors",
    "brand_story", "brand_values",
    "core_message",
)


class TopicChangelogEntry(BaseModel):
    at: datetime
    actor_id: Optional[int] = None
    actor_role: str = "user"                # user / admin / system
    field: str
    before: Optional[str] = None
    after: Optional[str] = None
    note: Optional[str] = None


class ExpansionLogEntry(BaseModel):
    at: datetime
    seed: str = ""
    model: str = ""
    expanded_count: int = 0
    raw_excerpt: str = ""                   # 前 300 字截断


class SubmitForReviewPayload(BaseModel):
    """提交审核 — 校验:画像必填齐 + ≥1 个 pending/approved 种子 + ≤50 个 selected query."""
    pass


# 监测问题 selected 上限
MAX_SELECTED_QUERIES = 50


class MonitoredQueryItem(BaseModel):
    """用户勾选/取消勾选监测问题时的输入项."""
    text: str
    selected: bool


class SelectedQueriesPayload(BaseModel):
    items: list[MonitoredQueryItem] = Field(..., max_length=500)


# ─────────────── Phase D — 执行计划书 / 内容文档 ──────────────


class TopicProgressCell(BaseModel):
    query: str
    engine: str
    status: str                          # pending / running / done
    hit: Optional[bool] = None
    last_checked_at: Optional[datetime] = None


class TopicExecutionPlanOut(BaseModel):
    id: int
    topic_id: int
    generated_at: datetime
    generated_by_reviewer_id: Optional[int]
    status: str                          # generating / ready / failed
    error: Optional[str] = None
    # 项目总体状况
    overview: dict
    # 主题日志(快照,只增不减)
    topic_changelog: list[TopicChangelogEntry]
    # 泛化日志(快照)
    expansion_log: list[ExpansionLogEntry]
    # 监测问题清单(快照,文本数组)
    monitored_queries: list[str]
    # 运行进度 — 实时聚合,run 完成后停止变化
    run_id: Optional[int] = None
    run_status: Optional[str] = None     # running / success / failed
    progress: list[TopicProgressCell] = Field(default_factory=list)
    progress_done: int = 0
    progress_total: int = 0


class GeneratedDocOut(BaseModel):
    id: int
    topic_id: int
    execution_plan_id: Optional[int]
    created_at: datetime
    source_query_text: str
    title: str
    body_markdown: str
    summary: str
    llm_model: str
    generation_error: Optional[str]
    status: str
    selected_for_review: bool
    review_decision_at: Optional[datetime]
    reviewer_id: Optional[int]
    reject_reason: Optional[str]
    publish_targets: list[dict]

    @classmethod
    def from_orm_row(cls, r: "TopicGeneratedDocORM") -> "GeneratedDocOut":
        try:
            targets = json.loads(r.publish_targets_json or "[]")
        except Exception:  # noqa: BLE001
            targets = []
        if not isinstance(targets, list):
            targets = []
        return cls(
            id=r.id, topic_id=r.topic_id, execution_plan_id=r.execution_plan_id,
            created_at=r.created_at,
            source_query_text=r.source_query_text or "",
            title=r.title or "", body_markdown=r.body_markdown or "",
            summary=r.summary or "", llm_model=r.llm_model or "",
            generation_error=r.generation_error,
            status=r.status, selected_for_review=bool(r.selected_for_review),
            review_decision_at=r.review_decision_at, reviewer_id=r.reviewer_id,
            reject_reason=r.reject_reason, publish_targets=targets,
        )


class PublishTargetItem(BaseModel):
    platform: str = Field(..., min_length=1, max_length=32)
    media: str = Field("", max_length=128)


class PublishPayload(BaseModel):
    publish_targets: list[PublishTargetItem] = Field(..., min_length=1, max_length=20)


class RejectDocPayload(BaseModel):
    reason: str = Field("", max_length=500)


class SelectDocsPayload(BaseModel):
    doc_ids: list[int] = Field(..., min_length=1, max_length=100)


class TopicPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    target: str = Field("", max_length=128)
    target_aliases: list[str] = Field(default_factory=list, max_length=10)
    industry: str = Field("", max_length=128)
    queries: list[str] = Field(..., min_length=1, max_length=50)
    # 与 queries 同长的 cluster_id 数组,可选;长度不齐或缺省时全部按 0 处理
    query_cluster_ids: Optional[list[int]] = None
    clusters: Optional[list[ClusterMeta]] = None
    engines: list[str] = Field(..., min_length=1, max_length=10)
    enabled: bool = True
    # Phase C — 创建 / 更新时,如果用户在编辑器里填了种子提示词,把它们附带提交;
    # 后端去重 + 追加到 seed_prompts_json(status=pending),保证种子词总会进审核流。
    # 字段名跟 TopicOut.seed_prompts(SeedPromptItem 列表)区分开 — 这里只是
    # 提交的纯文本,后端补 status/timestamp 写库.
    seed_drafts: Optional[list[str]] = Field(default=None, max_length=10)


class SeedPromptSubmitPayload(BaseModel):
    """甲方追加新种子词 — POST /topics/{id}/seed-prompts."""
    text: str = Field(..., min_length=1, max_length=256)


class TopicOut(BaseModel):
    id: int
    name: str
    target: str
    target_aliases: list[str]
    industry: str
    queries: list[str]
    query_cluster_ids: list[int]
    # Phase C — 跟 queries 同长的 status 数组,便于前端徽章渲染
    query_statuses: list[ReviewStatus]
    # Phase D — 跟 queries 同长的 selected 数组(用户勾选为监测问题的标志)
    query_selected: list[bool]
    clusters: list[ClusterMeta]
    seed_prompts: list[SeedPromptItem]
    engines: list[str]
    enabled: bool
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    # Phase D — 资料申请状态机
    profile: BrandProfile
    submission_status: str                  # draft / pending / approved / rejected
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    selected_query_count: int = 0           # 当前 selected=True 的 query 数,前端徽章用
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, r: AiTelemetryTopicORM) -> "TopicOut":
        # queries_json 兼容 4 种形态:
        #   v0: list[str]
        #   v1: list[{text, created_at}]
        #   v2 (intent cluster): list[{text, created_at, cluster_id}]
        #   v3 (Phase C 审核): list[{text, cluster_id?, status, submitted_at?, ...}]
        #   v4 (Phase D 监测勾选): list[{text, ..., selected: bool}]
        raw = json.loads(r.queries_json or "[]")
        queries: list[str] = []
        cluster_ids: list[int] = []
        statuses: list[str] = []
        selected: list[bool] = []
        for q in raw:
            if isinstance(q, dict):
                t = q.get("text") or ""
                if t:
                    queries.append(t)
                    cid = q.get("cluster_id")
                    cluster_ids.append(int(cid) if isinstance(cid, int) else -1)
                    statuses.append(q.get("status") or "approved")
                    # legacy 无 selected 字段:默认 True(老话题的 query 默认都参与监测)
                    selected.append(bool(q.get("selected", True)))
            elif isinstance(q, str):
                queries.append(q)
                cluster_ids.append(-1)
                statuses.append("approved")
                selected.append(True)
        clusters_raw = json.loads(r.clusters_json or "[]")
        clusters = [ClusterMeta(**c) for c in clusters_raw if isinstance(c, dict)]
        seeds_raw = json.loads(getattr(r, "seed_prompts_json", None) or "[]")
        seeds = [SeedPromptItem(**s) for s in seeds_raw if isinstance(s, dict)]
        try:
            profile_data = json.loads(getattr(r, "profile_json", None) or "{}")
        except Exception:  # noqa: BLE001
            profile_data = {}
        if not isinstance(profile_data, dict):
            profile_data = {}
        try:
            profile = BrandProfile(**profile_data)
        except Exception:  # noqa: BLE001
            profile = BrandProfile()
        return cls(
            id=r.id,
            name=r.name,
            target=r.target or "",
            target_aliases=json.loads(r.target_aliases_json or "[]"),
            industry=getattr(r, "industry", None) or "",
            queries=queries,
            query_cluster_ids=cluster_ids,
            query_statuses=statuses,
            query_selected=selected,
            clusters=clusters,
            seed_prompts=seeds,
            engines=json.loads(r.engines_json or "[]"),
            enabled=r.enabled,
            last_run_at=r.last_run_at,
            last_run_status=r.last_run_status,
            profile=profile,
            submission_status=getattr(r, "submission_status", None) or "draft",
            submitted_at=getattr(r, "submitted_at", None),
            approved_at=getattr(r, "approved_at", None),
            rejected_at=getattr(r, "rejected_at", None),
            reviewer_id=getattr(r, "reviewer_id", None),
            selected_query_count=sum(1 for s in selected if s),
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
