"""Flywheel P1-D -- source strategy: why models cite these articles + recommended placement media.

Two layers:
  1. build_source_stats(): pure aggregation, no LLM -- citations across all responses for the topic
     aggregated by domain (count / engines / title samples / source type), top domains per engine and
     answer_format distribution, source type shares, list of missed queries.
  2. LLM (DeepSeek direct / OpenRouter fallback, same pattern as solution_generator):
     input aggregated hard data -> output "citation pattern analysis (why cited / per-engine differences) + placement media
     recommendations + citation-friendly writing requirements". Writing requirements are injected into content generation (P1-E).

Entry schedule_source_strategy(topic_id) is triggered by an API endpoint and generated in a background daemon thread;
state machine idle -> generating -> ready / failed (frontend polling).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryResponseORM, AiTelemetryTopicORM, AiTelemetryTopicSourceStrategyORM,
)
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

PROMPT_VERSION = "source_strategy_v1"

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_SOURCE_STRATEGY_TIMEOUT", "180"))

# Zombie generating row reclaim threshold (status gets stuck when a backend restart kills the daemon thread)
STALE_GENERATING_THRESHOLD = timedelta(minutes=10)

TOP_DOMAINS = 30          # max external sources fed to the LLM
TITLES_PER_DOMAIN = 3     # title samples per domain
PER_QUERY_MAX = 15        # max per-question analysis items (missed first)


# ─── Industry-specific placement priors (section 4.1) ──────────────────────────────────────────────
# Cold-start default: publishable platforms that AI frequently "indexes and cites" for the industry. Real ranking still follows each topic's measured
# citation ranking (build_source_stats); this table only supplements same-industry candidates in LLM recommendations.
# Full version: docs/04-开发实现/内容与发布/发布/思源-可引用媒体投放先验-按行业.md.
GENERAL_CITABLE = [
    "知乎", "微信公众号", "百度百家号", "小红书图文", "今日头条号", "自有官网博客", "百度百科",
]
INDUSTRY_MEDIA_PRIOR: dict[str, dict] = {
    "工业制造/B2B/材料": {
        "keywords": ["工业", "制造", "b2b", "材料", "化工", "设备", "装备", "注塑",
                     "色母", "机械", "零部件", "供应链", "生产", "加工"],
        "platforms": ["行业垂直门户(慧聪/行业协会/专业媒体)", "知乎行业选购问答",
                      "微信公众号(行业号)", "百家号+搜狐号(B2B 资讯/榜单)"],
    },
    "消费品/美妆个护/母婴": {
        "keywords": ["美妆", "护肤", "个护", "母婴", "消费", "食品", "日化", "洗护",
                     "彩妆", "保健", "零食", "饮料"],
        "platforms": ["小红书", "知乎「值不值得买」", "B站测评专栏", "什么值得买/垂直评测站"],
    },
    "科技/SaaS/开发者": {
        "keywords": ["科技", "saas", "软件", "开发", "云", "ai", "人工智能", "数据",
                     "技术", "互联网", "工具", "平台"],
        "platforms": ["CSDN/掘金/思否", "知乎「技术选型」", "微信公众号(技术号)", "官网文档/博客"],
    },
    "医疗健康": {
        "keywords": ["医疗", "健康", "医药", "药", "诊疗", "医院", "保健品", "器械", "生物"],
        "platforms": ["权威科普站/医学百科/学会站", "知乎健康(严守广告法)", "公众号(资质号)"],
        "compliance": "医疗强合规:禁功效宣称、须资质与免责声明,推荐须过合规闸门。",
    },
    "金融/财税/法律": {
        "keywords": ["金融", "财税", "财务", "税", "法律", "保险", "投资", "银行", "证券", "基金"],
        "platforms": ["垂直门户", "知乎专业问答", "公众号(持牌机构号)"],
        "compliance": "金融/法律强合规:禁收益承诺,须持牌与免责声明。",
    },
    "教育培训": {
        "keywords": ["教育", "培训", "考证", "留学", "学习", "课程", "学校", "考试"],
        "platforms": ["知乎「求学/考证」问答", "小红书经验贴", "微信公众号", "垂直社区精华"],
    },
    "本地生活/餐饮零售": {
        "keywords": ["餐饮", "零售", "本地", "门店", "生活", "服务", "美食", "商超", "连锁"],
        "platforms": ["小红书", "大众点评内容", "抖音图文(非视频)", "本地公众号"],
    },
    "汽车/出行": {
        "keywords": ["汽车", "出行", "车", "新能源车", "充电", "出租", "网约", "配件"],
        "platforms": ["汽车垂直门户(懂车帝/汽车之家内容)", "知乎「购车」", "B站测评", "公众号"],
    },
}
_OVERSEAS_KEYWORDS = ["出海", "海外", "跨境", "global", "overseas", "外贸", "国际"]
OVERSEAS_CITABLE = [
    "Wikipedia(EN)", "Reddit(相关 subreddit)", "Medium/Substack", "Quora",
    "LinkedIn 文章", "官网博客", "G2/Capterra(SaaS)", "YouTube(字幕被引)",
]


def _industry_prior(industry: str, industry_class: str) -> Optional[dict]:
    """Match citable media priors by topic.industry / industry_class keywords; returns None if no match."""
    hay = f"{industry or ''} {industry_class or ''}".strip().lower()
    if not hay:
        return None
    overseas = any(k.lower() in hay for k in _OVERSEAS_KEYWORDS)
    matched: Optional[dict] = None
    for name, bucket in INDUSTRY_MEDIA_PRIOR.items():
        if any(kw in hay for kw in bucket["keywords"]):
            matched = {"name": name, **bucket}
            break
    if matched is None and not overseas:
        return None
    out = matched or {"name": "通用", "platforms": list(GENERAL_CITABLE)}
    if overseas:
        out = {**out, "overseas": OVERSEAS_CITABLE}
    return out


def _topic_target(t: AiTelemetryTopicORM) -> str:
    return (t.target or "").strip() or (t.name or "")


def build_source_stats(db, topic_id: int, *,
                       with_engine_domains: bool = False) -> dict[str, Any]:
    """Aggregate the topic's cited-source hard data (same metric definition as the source panel: own articles excluded).

    with_engine_domains=True additionally returns `engine_domain_counts`
    ({engine: {domain: count}}, complete, not truncated) -- the placement strategy weights cited sources per engine,
    and the top10 in per_engine isn't enough. Off by default: this data would be json.dumps'd whole into the
    LLM prompt, adding tens of KB for nothing.
    """
    from geo.api.ai_telemetry import _load_owned_urls, _norm_url
    from geo_checker.analyzers.source_factor import classify_factor

    owned = _load_owned_urls(db, topic_id)
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.error.is_(None))
          .all()
    )

    domain_agg: dict[str, dict] = {}
    engine_domains: dict[str, Counter] = defaultdict(Counter)
    engine_formats: dict[str, Counter] = defaultdict(Counter)
    engine_totals: dict[str, int] = defaultdict(int)
    engine_hits: dict[str, int] = defaultdict(int)
    query_hits: dict[str, int] = defaultdict(int)
    query_total: dict[str, int] = defaultdict(int)
    # Per-question drill-down: which domains this query's answers actually cited / in what format -- supports
    # analysis at the granularity of "why this question cites these articles"
    query_domains: dict[str, Counter] = defaultdict(Counter)
    query_formats: dict[str, Counter] = defaultdict(Counter)
    own_cited = 0

    for r in rows:
        engine_totals[r.engine] += 1
        query_total[r.query] += 1
        if r.hit:
            engine_hits[r.engine] += 1
            query_hits[r.query] += 1
        if r.answer_format:
            engine_formats[r.engine][r.answer_format] += 1
            query_formats[r.query][r.answer_format] += 1
        try:
            cits = json.loads(r.citations_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        cited_own_this = False
        for c in cits:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            nu = _norm_url(c["url"])
            if nu in owned:
                cited_own_this = True
                continue  # the external source ranking excludes own articles
            domain = (c.get("domain") or "").strip()
            if not domain:
                continue
            a = domain_agg.setdefault(domain, {
                "domain": domain, "count": 0, "engines": set(),
                "titles": [], "sample_url": c["url"],
            })
            a["count"] += 1
            a["engines"].add(r.engine)
            title = (c.get("title") or "").strip()
            if title and title not in a["titles"] and len(a["titles"]) < TITLES_PER_DOMAIN:
                a["titles"].append(title)
            engine_domains[r.engine][domain] += 1
            query_domains[r.query][domain] += 1
        if cited_own_this:
            own_cited += 1

    top_sources = []
    type_counts: Counter = Counter()
    for a in sorted(domain_agg.values(), key=lambda x: -x["count"]):
        try:
            stype = classify_factor(a["sample_url"], title=(a["titles"][0] if a["titles"] else ""),
                                    domain=a["domain"], owned=False)
        except Exception:  # noqa: BLE001
            stype = "other"
        type_counts[stype] += a["count"]
        if len(top_sources) < TOP_DOMAINS:
            top_sources.append({
                "domain": a["domain"], "source_type": stype, "count": a["count"],
                "engines": sorted(a["engines"]), "sample_titles": a["titles"],
            })

    per_engine = []
    for eng in sorted(engine_totals):
        total = engine_totals[eng]
        per_engine.append({
            "engine": eng,
            "responses": total,
            "hit_rate_pct": round(engine_hits[eng] / total * 100, 1) if total else 0.0,
            "top_domains": [{"domain": d, "count": n}
                            for d, n in engine_domains[eng].most_common(10)],
            "answer_formats": dict(engine_formats[eng].most_common()),
        })

    unhit_queries = sorted(q for q, n in query_total.items() if query_hits[q] == 0 and n > 0)

    # Per-question samples: **require citation data first** (without citations the LLM can't explain "why these were cited"),
    # then sort missed -> low hit rate. The order can't be reversed -- missed questions often happen to have no citation data,
    # and sorting by hits before filtering would filter the samples away (measured on the test host: 14 missed words left only 1).
    def _q_hit_pct(q: str) -> float:
        return round(query_hits[q] / query_total[q] * 100, 1) if query_total[q] else 0.0

    cited_queries = [q for q in query_total if query_domains[q]]
    per_query = [
        {
            "query": q,
            "hit_pct": _q_hit_pct(q),
            "top_domains": [{"domain": d, "count": n}
                            for d, n in query_domains[q].most_common(5)],
            "answer_formats": dict(query_formats[q].most_common(3)),
        }
        for q in sorted(cited_queries, key=lambda x: (query_hits[x] > 0, _q_hit_pct(x)))[:PER_QUERY_MAX]
    ]

    total_cits = sum(type_counts.values())
    engine_domain_counts = (
        {eng: dict(engine_domains[eng]) for eng in sorted(engine_domains)}
        if with_engine_domains else None
    )
    out_extra = ({"engine_domain_counts": engine_domain_counts}
                 if engine_domain_counts is not None else {})
    return {
        **out_extra,
        "responses_total": len(rows),
        "own_cited_responses": own_cited,
        "top_sources": top_sources,
        "source_type_share": [
            {"source_type": t, "count": n,
             "pct": round(n / total_cits * 100, 1) if total_cits else 0.0}
            for t, n in type_counts.most_common()
        ],
        "per_engine": per_engine,
        "per_query": per_query,
        "unhit_queries": unhit_queries[:30],
    }


def _strategy_prompt(target: str, stats: dict[str, Any]) -> tuple[str, str]:
    system = (
        "你是 GEO(生成式引擎优化)战略顾问。客户希望自己的品牌/内容被 AI 搜索引擎"
        "(DeepSeek、豆包、通义千问、文心一言、腾讯元宝等)的回答引用。"
        "下面给你该客户监测数据的聚合统计:各 AI 引擎实际引用了哪些外部信源"
        "(域名/类型/被引次数/标题样本)、每个引擎的信源与回答形态偏好、"
        "尚未命中的检索词。请只依据给出的数据分析,不要编造数据里没有的信源。"
    )
    user = f"""【客户检索词】{target}

【聚合数据】
{json.dumps(stats, ensure_ascii=False)}

【任务】
1. why_cited:分析「模型为何引用这些文章」—— 从信源类型占比、标题样本、回答形态分布里
   归纳 3-5 条引用模式(如:百科/官媒类权威背书、列表式盘点文、问答社区高赞、时效性资讯),
   每条带 pattern(≤20字)+ evidence(引用数据里的具体域名/数字作证据)+ implication(对客户意味着什么)。
2. engine_differences:每个引擎一条,说明该引擎的信源偏好差异(引用数据里该引擎的 top 域名与
   answer_formats 作证据)。
3. query_source_insights:对 per_query 里的每个问题逐条分析(这是最重要的输出):
   - why_these_sources:**这个问题**的回答为何引用这些文章——结合该问题的 top_domains
     与 answer_formats 解释(权威背书?形态匹配?时效?问答社区高赞?)
   - gap_plan:要让客户内容进入**这个问题**的引用池,应该在哪些媒体(优先该问题实际被引的
     域名或同类型平台)投什么形态的内容、写什么角度,一句话可执行
4. media_recommendations:推荐投放媒体,按优先级排序,最多 8 条。每条:
   platform(具体媒体/平台名,优先从被引数据里选真实出现过的域名,也可补充同类型高权重平台;
   若【聚合数据】含 industry_media_prior,可将其 platforms 作为该行业候选补充,但实测被引域名优先)、
   priority(P0/P1/P2)、reason(为什么——哪些引擎爱引它/覆盖哪些未命中问题,须与 gap_plan 呼应)、
   content_type(投什么形态的内容:问答/榜单盘点/深度评测/新闻稿等)。
5. content_guidelines:5-8 条「怎么写更容易被模型引用」的写作要求,供内容生成系统直接注入
   prompt 使用。必须具体可执行(结构、标题模式、数据引用、FAQ 块等),基于上面分析出的引用模式;
   引擎间偏好差异明显时,写成「投某引擎偏好平台时……」的条件式要求。

【硬约束】
- 严格输出 JSON,不要 markdown 包裹
- 中文输出;所有结论必须能在给出的数据里找到依据
- query_source_insights 的 query 字段必须与 per_query 里的原文完全一致(逐字),不许改写
- 未命中问题多的场景,media_recommendations 要优先覆盖未命中问题

【输出格式】
{{"why_cited":[{{"pattern":"...","evidence":"...","implication":"..."}}],
  "engine_differences":[{{"engine":"...","preference":"..."}}],
  "query_source_insights":[{{"query":"...","why_these_sources":"...","gap_plan":"..."}}],
  "media_recommendations":[{{"platform":"...","priority":"P0","reason":"...","content_type":"..."}}],
  "content_guidelines":["...", "..."]}}"""
    return system, user


def _call_llm(target: str, stats: dict[str, Any]) -> tuple[dict, list, str]:
    """Returns (analysis, recommendations, model_id). analysis contains why_cited /
    engine_differences / content_guidelines."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        provider, model_id, api_key = "deepseek", DEEPSEEK_MODEL, ds_key
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    elif or_key:
        provider, model_id, api_key = "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
        url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com", "X-Title": "GEO Source Strategy",
        }
    else:
        raise RuntimeError("DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置,无法生成信源策略")

    system, user = _strategy_prompt(target, stats)
    payload = {
        "model": model_id,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    r = post_and_record(url, payload, headers, provider=provider,
                        feature="source_strategy", timeout=DEFAULT_TIMEOUT, model=model_id)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    content = r.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM 输出不是 JSON object")
    analysis = {
        "why_cited": parsed.get("why_cited") or [],
        "engine_differences": parsed.get("engine_differences") or [],
        "query_source_insights": parsed.get("query_source_insights") or [],
        "content_guidelines": parsed.get("content_guidelines") or [],
    }
    recommendations = parsed.get("media_recommendations") or []
    return analysis, recommendations, model_id


def _generate(topic_id: int, operation_context_id: int | None = None) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AiTelemetryTopicSourceStrategyORM)
              .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id)
              .one_or_none()
        )
        if row is None:
            return
        if operation_context_id is not None:
            from geo.models.organization import OperationContextORM
            from geo.services.access_control import revalidate_operation_context
            from geo.services.system_audit import append_event

            operation = db.get(OperationContextORM, operation_context_id)
            try:
                revalidate_operation_context(db, operation_context_id)
            except Exception as exc:  # noqa: BLE001
                row.status = "failed"
                row.error = "authorization revoked"
                append_event(
                    db,
                    operation_context=operation,
                    event_type="authorization_denied",
                    action="source_strategy.generate.start",
                    object_type="source_strategy",
                    object_id=row.id,
                    result="denied",
                    reason=str(exc),
                )
                db.commit()
                return
        t = db.get(AiTelemetryTopicORM, topic_id)
        if t is None:
            row.status = "failed"
            row.error = "topic not found"
            db.commit()
            return
        try:
            stats = build_source_stats(db, topic_id)
            if not stats["top_sources"]:
                raise RuntimeError("该主题还没有可分析的外部信源(应答里没有引用数据)")
            # Industry-specific placement priors (section 4.1): match citable media candidates by topic industry, fed to the LLM as same-industry supplements.
            # The measured citation ranking is still the primary basis; priors only add candidates at cold start / with many misses.
            prior = _industry_prior(t.industry or "", t.industry_class or "")
            if prior:
                stats["industry_media_prior"] = prior
            analysis, recs, model_id = _call_llm(_topic_target(t), stats)
            row.source_stats_json = json.dumps(stats, ensure_ascii=False)
            row.analysis_json = json.dumps(analysis, ensure_ascii=False)
            row.recommendations_json = json.dumps(recs, ensure_ascii=False)
            row.llm_model = model_id
            row.prompt_version = PROMPT_VERSION
            row.status = "ready"
            row.error = None
            row.generated_at = datetime.utcnow()
            # Flywheel P2 side path: placement media recommendations flow into the optimization task center (idempotent; failure doesn't block the strategy)
            try:
                _push_media_recs_to_action_items(db, topic_id, recs)
            except Exception:  # noqa: BLE001
                log.warning("[source-strategy] action items failed topic=%d",
                            topic_id, exc_info=True)
        except Exception as e:  # noqa: BLE001
            log.exception("[source-strategy] topic=%d failed", topic_id)
            row.status = "failed"
            row.error = str(e)[:1000]
        db.commit()
    finally:
        db.close()


def _push_media_recs_to_action_items(db, topic_id: int, recs: list) -> None:
    """Media recommendations -> geo_action_items, idempotent by (topic, source_strategy, media:platform)."""
    from geo.models.ai_telemetry import GeoActionItemORM

    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        platform = str(rec.get("platform") or "").strip()
        if not platform:
            continue
        source_key = f"media:{platform}"[:128]
        exists = (
            db.query(GeoActionItemORM)
              .filter(GeoActionItemORM.topic_id == topic_id,
                      GeoActionItemORM.source == "source_strategy",
                      GeoActionItemORM.source_key == source_key)
              .first()
        )
        if exists is not None:
            continue
        priority = str(rec.get("priority") or "P1")
        db.add(GeoActionItemORM(
            topic_id=topic_id, source="source_strategy", source_key=source_key,
            source_ref_json=json.dumps(rec, ensure_ascii=False),
            priority=priority if priority in ("P0", "P1", "P2") else "P1",
            title=f"投放媒体建议:{platform}"[:200],
            body_md=(f"{rec.get('reason') or ''}\n\n"
                     f"建议内容形态:{rec.get('content_type') or '不限'}").strip(),
            suggested_action="add_plan_item",
        ))


def schedule_source_strategy(
    topic_id: int, operation_context_id: int | None = None,
) -> dict[str, Any]:
    """Upsert a generating row and start a background thread to generate. Returns the current status dict."""
    db = SessionLocal()
    try:
        row = (
            db.query(AiTelemetryTopicSourceStrategyORM)
              .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id)
              .one_or_none()
        )
        now = datetime.utcnow()
        if row is not None and row.status == "generating":
            # Zombie reclaim: a backend restart kills the daemon thread and the row gets stuck at generating
            if row.updated_at and now - row.updated_at < STALE_GENERATING_THRESHOLD:
                return {"status": "generating"}
        if row is None:
            row = AiTelemetryTopicSourceStrategyORM(topic_id=topic_id)
            db.add(row)
        row.operation_context_id = operation_context_id
        row.status = "generating"
        row.error = None
        db.commit()
    finally:
        db.close()
    threading.Thread(
        target=_generate,
        args=(topic_id, operation_context_id),
        daemon=True,
    ).start()
    return {"status": "generating"}


def _latest_ready_analysis(db, topic_id: int) -> dict:
    row = (
        db.query(AiTelemetryTopicSourceStrategyORM)
          .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id)
          .filter(AiTelemetryTopicSourceStrategyORM.status == "ready")
          .one_or_none()
    )
    if row is None:
        return {}
    try:
        analysis = json.loads(row.analysis_json or "{}")
        return analysis if isinstance(analysis, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def latest_content_guidelines(db, topic_id: int) -> list[str]:
    """For P1-E: get citation-friendly writing requirements from the topic's latest ready strategy; empty list if none."""
    out = _latest_ready_analysis(db, topic_id).get("content_guidelines") or []
    return [g for g in out if isinstance(g, str) and g.strip()]


def latest_query_gaps(db, topic_id: int) -> dict[str, str]:
    """For P1-E: per-question citation pool plan {query: gap_plan}. When generating a draft covering a question,
    append that question's own gap_plan to the citation-friendly writing requirements (question-level > global)."""
    out: dict[str, str] = {}
    for item in _latest_ready_analysis(db, topic_id).get("query_source_insights") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("query") or "").strip()
        plan = str(item.get("gap_plan") or "").strip()
        if q and plan:
            out[q] = plan
    return out
