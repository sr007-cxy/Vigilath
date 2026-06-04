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


async def set_seed_prompts(ctx: RunContext[AgentDeps], prompts: list[str]) -> dict:
    """设定/覆盖当前主题的种子提示词(写)。

    prompts: 种子词列表(品类/品牌相关短语)。
    """
    import json
    usage_guardrail_check(ctx.deps, "set_seed_prompts")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    cleaned = [p.strip() for p in prompts if p and p.strip()]
    if not cleaned:
        raise ModelRetry("prompts 不能为空。")
    topic.seed_prompts_json = json.dumps(cleaned, ensure_ascii=False)
    ctx.deps.db.commit()
    return {"topic_id": topic.id, "seed_prompts": cleaned}


async def get_prompts(ctx: RunContext[AgentDeps]) -> dict:
    """读当前主题的种子词 + 已扩展的 query 列表(只读)。"""
    import json
    topic = _account_topic(ctx)
    if topic is None:
        return {"seed": [], "queries": []}
    return {
        "topic_id": topic.id,
        "seed": json.loads(topic.seed_prompts_json or "[]"),
        "queries": json.loads(topic.queries_json or "[]"),
    }


async def expand_prompts(ctx: RunContext[AgentDeps], seed: str, count_per_scene: int = 10) -> dict:
    """对一个种子词做 4 维场景扩展(search/qa/intent/brand),返回候选 query 供挑选(写*,不直接落库)。

    seed: 要扩展的种子词;count_per_scene: 每场景产出条数(5–50)。
    引擎/调度由平台固定,与扩展无关。挑好后用 set_selected_queries 落库。
    """
    from geo.services.topic_expand import expand_topic_seed

    usage_guardrail_check(ctx.deps, "expand_prompts")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    try:
        res = await expand_topic_seed(topic, seed, count_per_scene=count_per_scene)
    except ValueError as e:
        raise ModelRetry(str(e))

    flat: list[dict] = []
    seen: set[str] = set()
    for scene, info in (res.get("scenes") or {}).items():
        for q in (info.get("queries") or []):
            if q and q not in seen:
                seen.add(q)
                flat.append({"text": q, "scene": scene})
    return {"seed": res["seed"], "total": res["total_count"], "candidates": flat}


async def set_selected_queries(ctx: RunContext[AgentDeps], queries: list[str]) -> dict:
    """把选定的 query 落库到当前主题(写),供后续诊断/跑批。已存在的不重复加。"""
    import json
    from datetime import datetime

    usage_guardrail_check(ctx.deps, "set_selected_queries")
    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        raise ModelRetry("queries 不能为空。")
    existing = json.loads(topic.queries_json or "[]")
    by_text = {q["text"] for q in existing if isinstance(q, dict) and q.get("text")}
    now = datetime.utcnow().isoformat()
    added = 0
    for q in cleaned:
        if q not in by_text:
            existing.append({"text": q, "created_at": now, "status": "approved", "approved_at": now})
            by_text.add(q)
            added += 1
    topic.queries_json = json.dumps(existing, ensure_ascii=False)
    ctx.deps.db.commit()
    return {"topic_id": topic.id, "added": added, "query_count": len(existing)}


async def get_report(ctx: RunContext[AgentDeps]) -> dict:
    """读当前主题的诊断报告(solution:根因诊断 + 叙述)。只读。无 / 未就绪时给提示。"""
    import json
    from geo.models.ai_telemetry import AiTelemetryTopicSolutionORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    sol = (
        ctx.deps.db.query(AiTelemetryTopicSolutionORM)
        .filter(AiTelemetryTopicSolutionORM.topic_id == topic.id)
        .order_by(AiTelemetryTopicSolutionORM.id.desc())
        .first()
    )
    if sol is None or sol.status != "ready":
        return {"status": (sol.status if sol else "none"), "report": None, "hint": "诊断报告尚未生成或生成中"}
    return {
        "status": sol.status,
        "diagnosis": json.loads(sol.diagnosis_json or "{}"),
        "narrative": json.loads(sol.narrative_json or "{}"),
    }


async def get_batch_results(ctx: RunContext[AgentDeps]) -> dict:
    """读最近一次跑批的真实引擎可见性(每引擎命中率)。只读;引擎/调度平台固定,用户只看结果。"""
    from geo.models.ai_telemetry import AiTelemetryResponseORM, AiTelemetryRunORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    run = (
        ctx.deps.db.query(AiTelemetryRunORM)
        .filter(AiTelemetryRunORM.topic_id == topic.id)
        .order_by(AiTelemetryRunORM.id.desc())
        .first()
    )
    if run is None:
        return {"status": "none", "hint": "还没有跑批结果"}
    resps = ctx.deps.db.query(AiTelemetryResponseORM).filter(AiTelemetryResponseORM.run_id == run.id).all()
    by_engine: dict[str, dict] = {}
    for r in resps:
        e = by_engine.setdefault(r.engine, {"total": 0, "hits": 0})
        e["total"] += 1
        if r.hit:
            e["hits"] += 1
    return {"run_id": run.id, "status": run.status, "response_count": len(resps), "by_engine": by_engine}


async def get_publish_status(ctx: RunContext[AgentDeps]) -> dict:
    """读当前主题文章的发布进度(按 status 聚合:draft/pending_review/approved/published 等)。只读。"""
    from sqlalchemy import func

    from geo.models.ai_telemetry import TopicGeneratedDocORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    rows = (
        ctx.deps.db.query(TopicGeneratedDocORM.status, func.count())
        .filter(TopicGeneratedDocORM.topic_id == topic.id)
        .group_by(TopicGeneratedDocORM.status)
        .all()
    )
    agg = {s: int(c) for s, c in rows}
    return {"topic_id": topic.id, "by_status": agg, "published": agg.get("published", 0), "total": sum(agg.values())}


async def get_growth_summary(ctx: RunContext[AgentDeps]) -> dict:
    """读品牌增长数据(从最近一次跑批的真实引擎结果聚合):每引擎命中率、品牌位次 Top1/Top3、
    高频竞品、高频被引域名、提及位置分布。只读;引擎/调度平台固定。"""
    import json
    from collections import Counter

    from geo.models.ai_telemetry import AiTelemetryResponseORM, AiTelemetryRunORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")
    run = (
        ctx.deps.db.query(AiTelemetryRunORM)
        .filter(AiTelemetryRunORM.topic_id == topic.id)
        .order_by(AiTelemetryRunORM.id.desc())
        .first()
    )
    if run is None:
        return {"status": "none", "hint": "还没有跑批结果,无法给品牌增长数据"}
    resps = ctx.deps.db.query(AiTelemetryResponseORM).filter(AiTelemetryResponseORM.run_id == run.id).all()

    engines: dict[str, dict] = {}
    top1 = top3 = hit_total = 0
    competitors: Counter = Counter()
    domains: Counter = Counter()
    positions: Counter = Counter()
    for r in resps:
        e = engines.setdefault(r.engine, {"total": 0, "hits": 0})
        e["total"] += 1
        if r.hit:
            e["hits"] += 1
            hit_total += 1
            if isinstance(r.brand_rank, int):
                if r.brand_rank <= 1:
                    top1 += 1
                if r.brand_rank <= 3:
                    top3 += 1
            if r.mention_position:
                positions[r.mention_position] += 1
        for c in (json.loads(r.competitors_json or "[]") if r.competitors_json else []):
            if isinstance(c, dict) and c.get("name"):
                competitors[c["name"]] += int(c.get("count") or 1)
        for d in (json.loads(r.citation_domains_json or "[]") if r.citation_domains_json else []):
            if d:
                domains[d] += 1

    total = len(resps)
    return {
        "run_id": run.id,
        "response_count": total,
        "hit_rate": round(hit_total / total, 3) if total else 0,
        "by_engine": {e: {**v, "hit_rate": round(v["hits"] / v["total"], 3) if v["total"] else 0} for e, v in engines.items()},
        "brand_rank": {"top1": top1, "top3": top3, "hits": hit_total},
        "top_competitors": [{"name": n, "count": c} for n, c in competitors.most_common(8)],
        "top_citation_domains": [{"domain": d, "count": c} for d, c in domains.most_common(8)],
        "mention_positions": dict(positions),
    }


async def get_today_effect(ctx: RunContext[AgentDeps]) -> dict:
    """今天(Asia/Shanghai)投放效果:今天有多少**扩展问题**被引擎搜到(命中)、多少**种子词**被搜到。只读。

    口径:扫今天该主题命中(hit)的真实引擎结果,命中的 query 计入扩展问题;
    经 queries_json 的 seed 字段回溯到种子词,统计被搜到的种子数。
    """
    import json
    from datetime import datetime, timedelta

    from geo.models.ai_telemetry import AiTelemetryResponseORM

    topic = _account_topic(ctx)
    if topic is None:
        raise ModelRetry("当前账号还没有主题,请先 create_topic。")

    seeds_all = set(json.loads(topic.seed_prompts_json or "[]"))
    text2seed: dict[str, str] = {}
    for q in json.loads(topic.queries_json or "[]"):
        if isinstance(q, dict) and q.get("text"):
            text2seed[q["text"]] = q.get("seed") or ""
        elif isinstance(q, str):
            text2seed[q] = ""
    expanded_all = set(text2seed.keys())

    # 今天(Asia/Shanghai)起点 → UTC(created_at 存 UTC)
    sh_now = datetime.utcnow() + timedelta(hours=8)
    today_start_utc = sh_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)

    rows = (
        ctx.deps.db.query(AiTelemetryResponseORM.query)
        .filter(
            AiTelemetryResponseORM.topic_id == topic.id,
            AiTelemetryResponseORM.hit.is_(True),
            AiTelemetryResponseORM.created_at >= today_start_utc,
        )
        .all()
    )
    hit_q = {r[0] for r in rows}
    expanded_hit = len(hit_q & expanded_all)
    seeds_hit = {text2seed[t] for t in hit_q if text2seed.get(t)}

    return {
        "date": sh_now.strftime("%Y-%m-%d"),
        "expanded_hit_today": expanded_hit,
        "expanded_total": len(expanded_all),
        "seed_hit_today": len(seeds_hit),
        "seed_total": len(seeds_all),
        "distinct_queries_hit_today": len(hit_q),
    }


# ── 其余工具(按 docs/实现设计-Agent.md §5 逐个补)──────────────────
# 待接(多数需把现有 ai_telemetry 端点逻辑抽成可复用 service,再薄包装):
#   expand_prompts(query_expander)/ confirm_prompts(queries_json 固化)/
#   probe_*(异步 run 管线)/ trace_sources / analyze_competitor /
#   get_report(Solution)/ draft_content_plan(ExecutionPlan)/ confirm_template /
#   draft_article(content_generator)/ edit_article / get_publish_status /
#   get_batch_results(runs/responses)/ ingest_material + ask_knowledge(RAG)


# 注册到 Agent 的工具列表(agent.py 引用)。新增工具加到这里即 drop-in。
TOOLS = [
    create_topic,
    get_topic,
    set_seed_prompts,
    get_prompts,
    expand_prompts,
    set_selected_queries,
    run_geo_checks,
    get_report,
    get_batch_results,
    get_growth_summary,
    get_today_effect,
    get_publish_status,
]
