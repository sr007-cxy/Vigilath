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
    # profile_json:6 大模块品牌资料表单(基础标识 / 品牌主体 / 服务核心 / 目标用户 / 品牌故事 / 创作边界)
    profile_json = Column(Text, nullable=False, default="{}")
    submission_status = Column(String, nullable=False, default="draft")
    # ↑ draft / pending / approved / rejected — 与 seed_prompts_json[].status 是两层:整张申请 + 单条种子词
    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # 主题日志:每次资料/种子/queries 变更追加一条 {at, actor_id, actor_role, field, before, after}
    topic_changelog_json = Column(Text, nullable=False, default="[]")
    # 泛化日志:种子词 → LLM 扩展 query 候选的调用记录 [{at, seed, model, expanded_count, raw_excerpt}]
    expansion_log_json = Column(Text, nullable=False, default="[]")

    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)             # success / failed / running

    # v3.1 — AI 自动生成排程(2026-05-17):每个 topic 一份。
    # cron 每天 02:00 起每小时扫一次,匹配 auto_generate_time 的 topic 各触发一次。
    auto_generate_enabled = Column(Boolean, nullable=False, default=False)
    auto_generate_time = Column(String(length=8), nullable=False, default="09:00")  # "HH:MM",24h, Asia/Shanghai
    auto_generate_count = Column(Integer, nullable=False, default=3)                 # 每次跑几条 query → 几篇稿
    auto_generate_last_run_at = Column(DateTime, nullable=True)                       # 上次自动跑批的 UTC

    # 修订号 — 每次任何字段改动(包括 admin 编辑 / 审核状态机迁移)都 +1。
    # 跟 topic_changelog_json 配对:changelog 第 N 条对应的就是 version=N+1 的快照。
    # 新建 topic 时 version=1;每次 _append_changelog 都 bump 一次。
    version = Column(Integer, nullable=False, default=1)

    # admin 给单 topic 配的扩展提示词,跑批组装 prompt 时拼到模板末尾。
    # 普通用户不可见;只 /workbench/accounts/:userId/topic 能编辑。
    prompt_extension = Column(Text, nullable=True)

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
    # v1.4(品牌增长页)— 品牌在 AI 答复中第几个被提到,1-based。未命中 / 未抽取时 NULL。
    # 用来聚 Top1/Top3/Top5 占比;mention_position 是段落位置(lead/body/tail),与 brand_rank 不同。
    brand_rank = Column(Integer, nullable=True)


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

    包含:资料 + 监测问题 + 主题/泛化日志的不变副本(后续即便 topic 被编辑也保留生成时形态)
    + 指向那次"通过即跑"的 run_id,运行进度由 _query_hits 表实时聚合.
    """
    __tablename__ = "ai_telemetry_topic_execution_plans"
    __table_args__ = (Index("idx_tep_topic", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    generated_by_reviewer_id = Column(Integer, nullable=True)

    # 项目总体状况快照:资料 + 监测问题数 + 引擎清单
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
    """v3(Phase D)— 基于资料 + 通过的监测问题,LLM 生成的内容文案稿.

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
    # [{platform: "抖音"|"小红书"|"视频号"|"公众号", media: "...", url?, marked_at, marked_by}]
    publish_targets_json = Column(Text, nullable=False, default="[]")
    # v3.1 — 内容来源(2026-05-17):'ai' = AI 自动 / admin 触发生成;'user' = 用户自带稿
    source = Column(String(length=8), nullable=False, default="ai", index=True)

    # URL 级 ROI 命中(2026-05-19):AI 答复 citations_json 里出现 publish_targets[].url 时,
    # 按引擎记 response_id 列表。空 `{}` = 还没被任何引擎引过。
    # 形态:{"deepseek": [response_id, ...], "doubao": [...], ...}
    cited_by_json = Column(Text, nullable=False, default="{}")


class TopicMediaORM(Base):
    """v3.2(2026-05-18)— 用户为某 topic 上传的图片 / 视频素材.

    资料上传弹窗里两类东西:
      - 文本类(.txt/.md/.docx/PDF):走 /profile/extract*,LLM 抽完直接回填资料表单,
        不持久化原文件;
      - 媒体类(.jpg/.png/.mp4/...):走本表 — 直接落盘 + 数据库登记一条,
        后续做内容生稿 / 发文时作为素材库引用。

    storage_path 是 data/topic_media/{topic_id}/{uuid}.{ext} 的相对路径;
    GET /media/{id}/blob 流式返回时拼回根目录读。
    """
    __tablename__ = "topic_media"
    __table_args__ = (
        Index("idx_topic_media_topic", "topic_id"),
        Index("idx_topic_media_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    filename = Column(String(length=255), nullable=False, default="")  # 用户上传时的原始文件名
    kind = Column(String(length=8), nullable=False, default="image")    # "image" | "video"
    mime = Column(String(length=128), nullable=False, default="")
    size = Column(Integer, nullable=False, default=0)                   # 字节数
    storage_path = Column(String(length=512), nullable=False, default="")
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    # 2026-05-20 — 用户当时从哪个种子提示词扩展出这条 query;空串表示 legacy 或没记录
    seed: str = ""


# ─────────────── Phase D — 品牌资料(6 大模块) ──────────────


class BrandProfile(BaseModel):
    """用户提交资料时填的 6 大模块表单.

    资料跟 topic 一一对应,序列化进 topic.profile_json.
    必填项见各字段 Field 注解(min_length / 默认值);submit-for-review 端点会再做完整校验.
    """
    # 一、资料基础标识
    profile_name: str = Field("", max_length=128)               # 资料名称
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
    core_credentials: list[str] = Field(default_factory=list, max_length=60)     # 核心荣誉 / 背书资质
    brand_diff_tags: list[str] = Field(default_factory=list, max_length=10)      # 品牌差异化标签(3-5 个)

    # 四、产品 / 服务核心信息
    core_service_overview: str = Field("", max_length=2000)                      # 核心服务概述
    service_features: list[str] = Field(default_factory=list, max_length=20)     # 服务核心特点
    service_process: list[str] = Field(default_factory=list, max_length=20)      # 服务关键流程 / 环节
    target_scenarios: list[str] = Field(default_factory=list, max_length=20)     # 服务覆盖场景 / 客户类型
    service_guarantees: list[str] = Field(default_factory=list, max_length=20)   # 服务交付保障(选填)

    # 四、品牌故事与情感素材(原五,2026-05-17 起删了「目标用户与痛点」节)
    brand_story: str = Field("", max_length=2000)                                # 品牌故事 / 成立初衷
    key_person_story: str = Field("", max_length=2000)                           # 核心人物故事(选填)
    case_stories: list[str] = Field(default_factory=list, max_length=60)         # 典型案例(选填)
    brand_values: str = Field("", max_length=1000)                               # 品牌价值观 / 服务理念

    # 五、补充素材与创作边界(原七)
    available_materials: list[str] = Field(default_factory=list, max_length=20)  # 可提供的素材类型(选填)
    brand_slogan: str = Field("", max_length=256)                                # Slogan / 宣传语(选填)
    core_message: str = Field("", max_length=1000)                               # 本次内容核心信息
    extra_notes: str = Field("", max_length=2000)                                # 其他补充说明(选填)


# 资料必填字段清单 — submit-for-review 校验用。
# 「内容创作方向」整节(creation_directions / copywriting_types / target_platforms /
# content_tones / content_redlines)2026-05-17 起从资料表单移除 — 这部分由
# 「内容发布策略」阶段决定,不再在资料里强制。schema 字段保留,老数据兼容。
# 2026-05-17 起 — 只把「一、基础标识」节的 6 个字段卡成必填,
# 其他模块(品牌主体 / 产品服务 / 品牌故事 / 补充素材)都改成选填:
# 用户上传的资料是后续生稿的素材库,鼓励多填但不强迫;LLM 拿到什么用什么.
PROFILE_REQUIRED_FIELDS: tuple[str, ...] = (
    "profile_name", "company_full_name", "company_short_name",
    "industry", "core_business_lines", "service_geo",
)


class TopicChangelogEntry(BaseModel):
    at: datetime
    actor_id: Optional[int] = None
    actor_role: str = "user"                # user / admin / system
    field: str
    before: Optional[str] = None
    after: Optional[str] = None
    note: Optional[str] = None
    # 这条 changelog 写入后 topic 的 version。老条目(migration 前的)没有,允许缺省
    version: Optional[int] = None


class ExpansionLogEntry(BaseModel):
    at: datetime
    seed: str = ""
    model: str = ""
    expanded_count: int = 0
    raw_excerpt: str = ""                   # 前 300 字截断


class SubmitForReviewPayload(BaseModel):
    """提交审核 — 校验:资料必填齐 + ≥1 个 pending/approved 种子 + ≤50 个 selected query."""
    pass


# 监测问题 selected 上限
MAX_SELECTED_QUERIES = 50
# 单次 seed 扩展候选上限(用户从这一批里再勾 ≤ MAX_SELECTED_QUERIES 个)
MAX_EXPANSION_CANDIDATES = 200


class MonitoredQueryItem(BaseModel):
    """用户勾选/取消勾选监测问题时的输入项."""
    text: str
    selected: bool
    # 2026-05-20 — 这条 query 当时是从哪个种子词扩展出来的(候选阶段就有),
    # 选中入库时一并持久化,后续报告 / Queries 明细按种子分组渲染。
    seed: str = ""


class SelectedQueriesPayload(BaseModel):
    items: list[MonitoredQueryItem] = Field(..., max_length=500)


class ProfileExtractPayload(BaseModel):
    """AI 智能填充 — 用户拖入文件或粘贴的原始文本."""
    text: str = Field(..., min_length=10, max_length=60000)


class ProfileExtractOut(BaseModel):
    profile: BrandProfile
    used_model: str
    # LLM 顺手给的种子提示词候选(3-8 条)。前端步骤 2 用它预填,用户可保留/删改。
    seed_suggestions: list[str] = Field(default_factory=list)


# ─────────────── Phase D — 执行计划书 / 内容文档 ──────────────


class TopicProgressCell(BaseModel):
    query: str
    engine: str
    status: str                          # pending / running / done
    hit: Optional[bool] = None
    last_checked_at: Optional[datetime] = None


class PublishPlanItem(BaseModel):
    """发文计划单行 — 一个监测问题 → 一篇内容 → 一天发一个平台.

    优先级:
      - high: 监测命中率 0%(AI 完全没提及品牌,要补内容)
      - med:  0 < 命中率 < 50%(部分命中,补强)
      - low:  >= 50%(已有覆盖,可不发或低频)
    """
    day: int                              # 第几天(0=今天)
    publish_date: str                     # YYYY-MM-DD
    query: str
    coverage_pct: float                   # AI 命中率 0-100
    priority: str                         # high / med / low
    doc_id: Optional[int] = None          # 关联的内容文档(已生成才有)
    doc_status: Optional[str] = None      # draft / pending_review / approved / rejected / published
    suggested_platforms: list[str] = Field(default_factory=list)


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
    # 发文计划 — 按 query 命中情况排优先级,每天 1 篇,关联已生成的内容文档
    publishing_plan: list[PublishPlanItem] = Field(default_factory=list)


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
    # v3.1 — 'ai' / 'user'
    source: str = "ai"
    # 2026-05-19 — URL 级 ROI 命中:{engine: [response_id]}
    cited_by: dict[str, list[int]] = Field(default_factory=dict)

    @classmethod
    def from_orm_row(cls, r: "TopicGeneratedDocORM") -> "GeneratedDocOut":
        try:
            targets = json.loads(r.publish_targets_json or "[]")
        except Exception:  # noqa: BLE001
            targets = []
        if not isinstance(targets, list):
            targets = []
        try:
            cited = json.loads(getattr(r, "cited_by_json", None) or "{}")
        except Exception:  # noqa: BLE001
            cited = {}
        if not isinstance(cited, dict):
            cited = {}
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
            source=getattr(r, "source", None) or "ai",
            cited_by=cited,
        )


class PublishTargetItem(BaseModel):
    platform: str = Field(..., min_length=1, max_length=32)
    media: str = Field("", max_length=128)
    url: str = Field("", max_length=512)


class PublishPayload(BaseModel):
    publish_targets: list[PublishTargetItem] = Field(..., min_length=1, max_length=20)


# v3.1 — 用户提交 / 编辑自己写的文章
class UserDocSubmitPayload(BaseModel):
    """用户提交自己写的稿件 — POST /api/content/topics/{topic_id}/docs."""
    title: str = Field(..., min_length=1, max_length=200)
    body_markdown: str = Field(..., min_length=1, max_length=100000)
    summary: str = Field("", max_length=500)
    # 关联监测问题(可选,影响后续效果归因);为空 = 自由稿
    source_query_text: str = Field("", max_length=1000)
    # "draft" = 仅保存,留在用户侧;"pending_review" = 直接进 admin 队列
    submit_for_review: bool = False


class UserDocUpdatePayload(BaseModel):
    """用户编辑自己的 draft / rejected 稿子."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    body_markdown: Optional[str] = Field(None, min_length=1, max_length=100000)
    summary: Optional[str] = Field(None, max_length=500)
    source_query_text: Optional[str] = Field(None, max_length=1000)


# v3.1 — AI 自动生成排程配置
class AutoGenerateConfigPayload(BaseModel):
    enabled: bool
    # "HH:MM",24h,Asia/Shanghai;后端校验格式
    time: str = Field("09:00", min_length=4, max_length=5)
    count: int = Field(3, ge=1, le=20)


class RejectDocPayload(BaseModel):
    reason: str = Field("", max_length=500)


class SelectDocsPayload(BaseModel):
    doc_ids: list[int] = Field(..., min_length=1, max_length=100)


class TopicMediaOut(BaseModel):
    """topic 的图片 / 视频素材 — 给前端 GET /topics/{id}/media 用."""
    id: int
    topic_id: int
    filename: str
    kind: str           # "image" | "video"
    mime: str
    size: int
    # 前端拉文件的 URL — 跟 /blob 路由对齐,不暴露 storage_path
    url: str
    uploaded_at: datetime


class TopicPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    target: str = Field("", max_length=128)
    target_aliases: list[str] = Field(default_factory=list, max_length=10)
    industry: str = Field("", max_length=128)
    queries: list[str] = Field(..., min_length=1, max_length=50)
    # 与 queries 同长的 cluster_id 数组,可选;长度不齐或缺省时全部按 0 处理
    query_cluster_ids: Optional[list[int]] = None
    clusters: Optional[list[ClusterMeta]] = None
    # 2026-05-20 — 与 queries 同长,每条 query 来自哪个种子提示词(picker 端"按种子分组"用)。
    # 缺省 / 长度不齐 → 后端忽略,沿用 queries_json 里已有的 seed(若有)。
    query_seeds: Optional[list[str]] = None
    engines: list[str] = Field(..., min_length=1, max_length=10)
    enabled: bool = True
    # Phase C — 创建 / 更新时,如果用户在编辑器里填了种子提示词,把它们附带提交;
    # 后端去重 + 追加到 seed_prompts_json(status=pending),保证种子词总会进审核流。
    # 字段名跟 TopicOut.seed_prompts(SeedPromptItem 列表)区分开 — 这里只是
    # 提交的纯文本,后端补 status/timestamp 写库.
    seed_drafts: Optional[list[str]] = Field(default=None, max_length=10)
    # Phase D — 同请求一起提交品牌资料(6 大模块);后端写 profile_json + 追加 changelog.
    # None / 缺失 = 这次保存不动 profile_json(向后兼容老 client).
    profile: Optional[BrandProfile] = None
    # v1.4 — admin 给该 topic 配的扩展提示词;跑批组装 query 时拼到末尾。
    # 普通用户编辑接口忽略此字段(API 层强制清空),只 admin 工作台能写。
    prompt_extension: Optional[str] = Field(default=None, max_length=2000)


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
    # 2026-05-20 — 跟 queries 同长的种子词数组;legacy / 未记录的位为 ""
    query_seeds: list[str] = Field(default_factory=list)
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
    version: int = 1                        # 修订号:每次 _append_changelog 自增,前端徽章 "v3"
    # v3.1 — AI 自动生成排程
    auto_generate_enabled: bool = False
    auto_generate_time: str = "09:00"
    auto_generate_count: int = 3
    auto_generate_last_run_at: Optional[datetime] = None
    # v1.4 — admin 配的扩展提示词;TopicOut 暴露给 admin 前端,普通用户前端不渲染。
    prompt_extension: Optional[str] = None
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
        query_seeds: list[str] = []
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
                    query_seeds.append(str(q.get("seed") or ""))
            elif isinstance(q, str):
                queries.append(q)
                cluster_ids.append(-1)
                statuses.append("approved")
                selected.append(True)
                query_seeds.append("")
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
            query_seeds=query_seeds,
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
            version=int(getattr(r, "version", 1) or 1),
            auto_generate_enabled=bool(getattr(r, "auto_generate_enabled", False) or False),
            auto_generate_time=str(getattr(r, "auto_generate_time", None) or "09:00"),
            auto_generate_count=int(getattr(r, "auto_generate_count", 3) or 3),
            auto_generate_last_run_at=getattr(r, "auto_generate_last_run_at", None),
            prompt_extension=getattr(r, "prompt_extension", None),
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
    brand_rank: Optional[int] = None


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


class PositionBreakdown(BaseModel):
    """雷达 5 维 + 右核心指标 4 卡的口径,基于 ResponseORM.brand_rank 聚合."""
    top1_pct: float = 0.0       # COUNT(brand_rank=1) / total_cells × 100
    top3_pct: float = 0.0       # COUNT(brand_rank<=3) / total_cells
    top5_pct: float = 0.0       # COUNT(brand_rank<=5) / total_cells
    visible_pct: float = 0.0    # COUNT(hit=True) / total_cells(= 可见占比)
    source_pct: float = 0.0     # COUNT(DISTINCT query WHERE hit=True) / total_queries


class PositionBreakdownOut(BaseModel):
    topic_id: int
    period_days: int
    industry: str
    total_cells: int
    total_queries: int
    breakdown: PositionBreakdown
    industry_baseline: Optional[PositionBreakdown] = None   # 样本不足时 NULL,前端不渲染


class IndustryBenchmarkOut(BaseModel):
    industry: str
    sample_size: int                # 参与聚合的 topic 数
    breakdown: Optional[PositionBreakdown] = None    # 样本 <3 时 NULL


class CompetitorSubstitutionItem(BaseModel):
    """C3 竞品分析"被替代证据" — 提了竞品但没提我的 query."""
    query: str
    competitor_name: str
    competitor_count: int        # 该竞品在该 query 历次答复里被提到的总次数
    sample_response_id: int      # 用于跳转到具体证据
    sample_snippet: str          # 短摘抄


class CompetitorSubstitutionOut(BaseModel):
    topic_id: int
    period_days: int
    competitor_filter: Optional[str]   # 单竞品筛选;None=全部
    items: list[CompetitorSubstitutionItem]   # 按 competitor_count 降序
    total: int


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
