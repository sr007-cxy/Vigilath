"""工具实现(给大模型,Pydantic AI @RunContext[AgentDeps])。

约定:
  - 读类直接调;写类先 usage_guardrail_check;接收资源 id 的先 assert_owns。
  - 入参严格 typed;非法时 raise ModelRetry(具体原因)让 DeepSeek 纠正重试(治 tool 漂)。
  - 工具只「读 / 提议写」;发布等副作用走 Method 层,模型无发布工具。

本文件先落通第一个真实工具 run_geo_checks,其余按 docs/实现设计-Agent.md §5 逐个补;
未实现的以 ModelRetry/NotImplemented 占位,注册即生效(drop-in)。
"""
from __future__ import annotations

import asyncio
import threading

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext

from geo.agent.deps import AgentDeps
from geo.agent.methods import usage_guardrail_check

# geo_checker 当前用模块级全局态(state._scores + _geo_checker_lock),并发会串扰。
# ★ W1 硬前置:会话作用域化。在此之前用进程级锁串行化,保证正确(牺牲并行)。
_geo_checks_lock = threading.Lock()


class TopicInfo(BaseModel):
    topic_id: int
    name: str
    target: str
    industry: str = ""


class GeoCheckResult(BaseModel):
    url: str
    raw: dict  # generate_score 的原始打分(总分 + 分项);TODO: 收敛成稳定 schema


def _account_topic(ctx: RunContext[AgentDeps]):
    """取当前账号的主题(MVP 限 1):优先 deps.topic_id,否则取该账号唯一主题。含归属校验。"""
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    db, acc = ctx.deps.db, ctx.deps.account_id
    if ctx.deps.topic_id:
        t = db.get(AiTelemetryTopicORM, ctx.deps.topic_id)
        if t is None or t.user_id != acc:
            raise ModelRetry("指定主题不存在或不属于当前账号。")
        return t
    return (
        db.query(AiTelemetryTopicORM)
        .filter(AiTelemetryTopicORM.user_id == acc)
        .order_by(AiTelemetryTopicORM.id.asc())
        .first()
    )


async def create_topic(ctx: RunContext[AgentDeps], name: str, url: str, industry: str = "") -> TopicInfo:
    """新建品牌主题(写)。MVP:**每账号限 1 个主题**,已存在则拒绝。

    name: 品牌/项目名;url: 目标站点;industry: 行业(可空)。
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    usage_guardrail_check(ctx.deps, "create_topic")
    db, acc = ctx.deps.db, ctx.deps.account_id
    existing = (
        db.query(AiTelemetryTopicORM).filter(AiTelemetryTopicORM.user_id == acc).first()
    )
    if existing is not None:
        raise ModelRetry(f"当前账号已有主题「{existing.name}」(MVP 限 1 个),请直接使用它,不要新建。")
    if not (name or "").strip() or not (url or "").strip():
        raise ModelRetry("name 和 url 都不能为空。")

    t = AiTelemetryTopicORM(user_id=acc, name=name.strip(), target=url.strip(), industry=industry or "")
    db.add(t)
    db.commit()
    db.refresh(t)
    ctx.deps.topic_id = t.id  # 同一对话后续工具直接复用
    return TopicInfo(topic_id=t.id, name=t.name, target=t.target, industry=t.industry or "")


async def get_topic(ctx: RunContext[AgentDeps]) -> TopicInfo | None:
    """读当前账号的主题(无则返回 null,引导用户 create_topic)。"""
    t = _account_topic(ctx)
    if t is None:
        return None
    return TopicInfo(topic_id=t.id, name=t.name, target=t.target, industry=t.industry or "")


async def run_geo_checks(ctx: RunContext[AgentDeps], categories: list[str] | None = None) -> GeoCheckResult:
    """跑 25 项 GEO 检查并打分(只读)。引擎集/调度由平台固定,不接受用户/模型指定。

    categories: 可选,限定检查项;空=全量。
    """
    from geo_checker.orchestrate import generate_score

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    url = (topic.target or "").strip()
    if not url:
        raise ModelRetry("主题缺少目标站点 URL,无法跑检查。")

    def _run() -> dict:
        with _geo_checks_lock:   # 串行化:规避 geo_checker 模块级全局态串扰(W1 会话化后可去)
            return generate_score(url, allowed_categories=categories)

    raw = await asyncio.to_thread(_run)
    return GeoCheckResult(url=url, raw=raw if isinstance(raw, dict) else {"result": raw})


# ── 其余工具(占位,按 §5 逐个补)──────────────────────────────
# 形如:
#   async def expand_prompts(ctx, seed_ids): usage_guardrail_check(ctx.deps, "expand_prompts");
#       ... 调 query_expander.expand_one_scene ...
# create_topic / ingest_material / set_seed_prompts / confirm_prompts /
# probe_visibility / probe_citation / trace_sources / analyze_competitor /
# get_report / draft_content_plan / confirm_template / draft_article / edit_article /
# get_publish_status / get_batch_results / ask_knowledge


# 注册到 Agent 的工具列表(agent.py 引用)。新增工具加到这里即 drop-in。
TOOLS = [
    create_topic,
    get_topic,
    run_geo_checks,
]
