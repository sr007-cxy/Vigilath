"""GEO brand growth strategy solution generation service.

Entry point `schedule_solution_generation(topic_id, website_url, admin_id)` is triggered asynchronously via FastAPI BackgroundTasks
by the admin_review POST endpoint when admin clicks "生成战略方案" ("generate strategy solution").

Hybrid architecture (hard data + LLM copy):
  1. Hard data: run geo_checker.run_geo_check to get 25-category diagnosis + score + grade
  2. Clustering: group the 25 categories into 5 major themes (5 on-site technical weaknesses), computing severity per cluster
  3. LLM: feed BrandProfile + diagnosis_summary + template skeleton to DeepSeek
     to get JSON {diagnosis_summaries, seven_steps, keyword_tiers, vision}
  4. Assembly: LLM copy + hard data -> SolutionORM persisted to DB

Failure: any step fails -> status=failed + error_text; frontend polling gets the error + a retry button.

ENV:
    DEEPSEEK_API_KEY      optional, preferred -> direct
    OPENROUTER_API_KEY    optional, fallback -> DeepSeek via OpenRouter
    GEO_SOLUTION_TIMEOUT  optional, per-LLM-request timeout (seconds), default 180
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any

import requests

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, AiTelemetryTopicSolutionORM, BrandProfile,
)
from geo.services.llm_gateway import post_and_record
import uuid
from geo.services.llm_usage import usage_ctx

log = logging.getLogger(__name__)

# generating rows not updated for longer than this are treated as zombies -- the background thread is a daemon; on backend
# restart it gets killed by SIGTERM, DB status stays stuck at generating, and frontend polling never gets a result.
STALE_GENERATING_THRESHOLD = timedelta(minutes=10)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_SOLUTION_TIMEOUT", "180"))


# ─────────────── 25 categories -> 5 weakness clusters mapping ───────────────
# Based on the on-site technical diagnosis classification skeleton in two client PDFs:
#   1) AI protocols & crawl rules   2) Crawler access & performance   3) Structured data & semantics
#   4) URLs & meta tags             5) Content & security & media
#
# The 25 English category labels come straight from geo_checker.orchestrate.CHECK_REGISTRY; don't change them.
CLUSTER_DEFS: list[dict[str, Any]] = [
    {
        "key": "ai_protocol",
        "title_zh": "AI 专属协议与抓取规则",
        "categories": [
            "llms.txt",
            ".well-known Discovery",
            "AI Crawl Readiness",
            "AI-Specific Optimization",
            "Search Engine & AI Platform Registration",
        ],
    },
    {
        "key": "crawl_performance",
        "title_zh": "爬虫访问与页面性能",
        "categories": [
            "Technical Crawlability",
            "Mobile-Friendliness & Page Weight",
            "robots.txt",
            "sitemap.xml",
            "HTTPS",
        ],
    },
    {
        "key": "structured_data",
        "title_zh": "结构化数据与语义化",
        "categories": [
            "Structured Data",
            "Content Accessibility",
            "AI Answer Format Optimization",
            "Schema Breadcrumbs & Knowledge Panel",
            "Brand Entity in Knowledge Graphs",
        ],
    },
    {
        "key": "url_meta",
        "title_zh": "URL 与元标签规范",
        "categories": [
            "URL Normalization",
            "Meta Tags",
            "Multilingual Content Depth",
            "Multi-Page Sampling",
        ],
    },
    {
        "key": "content_safety",
        "title_zh": "内容、安全与媒体",
        "categories": [
            "Content Quality for AI",
            "Trust & Safety Signals",
            "Authority & Trust Signals",
            "Outbound Links & Media",
            "Social Signals",
            "Cross-Platform Content Distribution",
        ],
    },
]

SEVEN_STEPS_TEMPLATE = [
    {"step": 1, "name": "种子提示词搭建", "core_goal": "确立品牌 AI 引擎元定义"},
    {"step": 2, "name": "意图深度扩展",   "core_goal": "挖掘 AI 搜索真实用户需求"},
    {"step": 3, "name": "提示词聚类拓扑", "core_goal": "搭建品牌行业知识矩阵"},
    {"step": 4, "name": "结构化内容生成", "core_goal": "打造 AI 高抓取偏好内容"},
    {"step": 5, "name": "全链路多维分发", "core_goal": "搭建行业权威证人矩阵"},
    {"step": 6, "name": "数据遥测引用分析", "core_goal": "监控 AI 品牌曝光数据"},
    {"step": 7, "name": "强化反馈闭环",   "core_goal": "形成自迭代增长运营环"},
]

KEYWORD_TIERS_TEMPLATE = [
    {"tier": "brand_core",      "title_zh": "品牌核心词",  "description_hint": "品牌名 / 公司名 / 主力产品系列名"},
    {"tier": "product_model",   "title_zh": "产品 / 型号词", "description_hint": "具体产品型号、SKU、产品线名"},
    {"tier": "tech",            "title_zh": "技术词",      "description_hint": "AI 回答「这款产品 / 服务有什么技术优势」时检索的词"},
    {"tier": "scenario",        "title_zh": "场景词",      "description_hint": "用户在 AI 引擎中问「XXX 场所 / 行业用什么」时触发的词"},
    {"tier": "procurement",     "title_zh": "采购决策词",   "description_hint": "B2B 采购决策者真实提问的词,转化价值最高"},
    {"tier": "pain_point",      "title_zh": "痛点词",      "description_hint": "用户描述问题时使用的词,AI 引擎把问题与解决方案品牌匹配"},
]


# ─────────────── Entry point ───────────────


def schedule_solution_generation(
    *,
    topic_id: int,
    website_url: str,
    admin_id: int,
    operation_context_id: int | None = None,
) -> None:
    """fire-and-forget thread. A single LLM call takes 30-90s; keep it off the FastAPI event loop."""
    thread = threading.Thread(
        target=_run_safe,
        args=(topic_id, website_url, admin_id, operation_context_id),
        daemon=True,
    )
    thread.start()


def reset_stale_generating() -> int:
    """Called at startup -- marks generating rows not updated for over STALE_GENERATING_THRESHOLD
    as failed. Daemon threads get killed on backend restart; this fallback keeps the UI from spinning forever.
    Returns the number of rows reset."""
    cutoff = datetime.utcnow() - STALE_GENERATING_THRESHOLD
    db = SessionLocal()
    try:
        stale = (
            db.query(AiTelemetryTopicSolutionORM)
              .filter(AiTelemetryTopicSolutionORM.status == "generating",
                      AiTelemetryTopicSolutionORM.updated_at < cutoff)
              .all()
        )
        for row in stale:
            row.status = "failed"
            row.error = "后端在生成途中重启,后台任务被中断。请点重试重新生成。"
            row.updated_at = datetime.utcnow()
        if stale:
            db.commit()
            log.warning("reset %d stale generating solution rows: %s",
                        len(stale), [r.id for r in stale])
        return len(stale)
    finally:
        db.close()


def _run_safe(
    topic_id: int, website_url: str, admin_id: int,
    operation_context_id: int | None = None,
) -> None:
    # Accounting context: all model calls in this generation share one trace_id, so the page can drill down per run
    with usage_ctx(feature="solution", topic_id=topic_id, user_id=admin_id,
                   trace_id=uuid.uuid4().hex, ref_type="topic", ref_id=str(topic_id)):
        try:
            _run(topic_id, website_url, admin_id, operation_context_id)
        except Exception as e:  # noqa: BLE001
            log.exception("solution generation crashed for topic %d: %s", topic_id, e)
            _mark_failed(topic_id, str(e)[:1000])


def _run(
    topic_id: int, website_url: str, admin_id: int,
    operation_context_id: int | None = None,
) -> None:
    db = SessionLocal()
    try:
        t = db.get(AiTelemetryTopicORM, topic_id)
        if not t:
            log.warning("solution gen: topic %d not found", topic_id)
            return
        if operation_context_id is not None:
            from geo.models.organization import OperationContextORM
            from geo.services.access_control import revalidate_operation_context
            from geo.services.system_audit import append_event

            operation = db.get(OperationContextORM, operation_context_id)
            sol = (
                db.query(AiTelemetryTopicSolutionORM)
                .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
                .one_or_none()
            )
            try:
                revalidate_operation_context(db, operation_context_id)
            except Exception as exc:  # noqa: BLE001
                if sol is not None:
                    sol.status = "failed"
                    sol.error = "authorization revoked"
                    sol.updated_at = datetime.utcnow()
                append_event(
                    db,
                    operation_context=operation,
                    event_type="authorization_denied",
                    action="solution.generate.start",
                    object_type="solution",
                    object_id=(sol.id if sol is not None else None),
                    result="denied",
                    reason=str(exc),
                )
                db.commit()
                return

        # 1) Load brand profile
        try:
            profile_data = json.loads(t.profile_json or "{}")
        except Exception:  # noqa: BLE001
            profile_data = {}
        if not isinstance(profile_data, dict):
            profile_data = {}
        try:
            profile = BrandProfile(**profile_data)
        except Exception:  # noqa: BLE001
            profile = BrandProfile()

        # 2) Run geo_checker -- hard data (website optional: when empty skip technical diagnosis and generate only the 4 brand-profile sections)
        url = (website_url or "").strip()
        diagnosis = _run_diagnosis(url) if url else _empty_diagnosis()

        # 3) Call LLM -- assemble solution copy
        narrative, keywords, llm_model = _call_llm(profile, diagnosis, website_url)

        # 3.5) Snapshot monitored queries -- take selected=True items from topic.queries_json
        #      + cluster labels from topic.clusters_json, no LLM. Used by the "monitored query list" section of the report.
        queries_snapshot = _snapshot_selected_queries(t)

        # 4) Persist to DB -- upsert
        sol = (
            db.query(AiTelemetryTopicSolutionORM)
              .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
              .first()
        )
        if sol is None:
            sol = AiTelemetryTopicSolutionORM(topic_id=topic_id)
            db.add(sol)
        sol.status = "ready"
        sol.website_url = website_url
        sol.diagnosis_json = json.dumps(diagnosis, ensure_ascii=False)
        sol.narrative_json = json.dumps(narrative, ensure_ascii=False)
        sol.keywords_json = json.dumps(keywords, ensure_ascii=False)
        sol.brand_snapshot_json = json.dumps(profile.model_dump(), ensure_ascii=False)
        sol.queries_snapshot_json = json.dumps(queries_snapshot, ensure_ascii=False)
        sol.llm_model = llm_model
        sol.error = None
        sol.generated_by_admin_id = admin_id
        sol.updated_at = datetime.utcnow()
        db.commit()
        log.info("solution gen done for topic %d (score=%s)", topic_id, diagnosis.get("score"))
    finally:
        db.close()


def _mark_failed(topic_id: int, error: str) -> None:
    db = SessionLocal()
    try:
        sol = (
            db.query(AiTelemetryTopicSolutionORM)
              .filter(AiTelemetryTopicSolutionORM.topic_id == topic_id)
              .first()
        )
        if sol is None:
            return
        sol.status = "failed"
        sol.error = error
        sol.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


# ─────────────── Hard data: snapshot of the topic's selected queries ───────────────


def _snapshot_selected_queries(topic: AiTelemetryTopicORM) -> dict[str, Any]:
    """Extract selected=True items from topic.queries_json + cluster labels from clusters_json into a snapshot.

    queries_json supports multiple shapes (see the ai_telemetry.AiTelemetryTopicORM comment):
      - list[str] (legacy): all treated as selected=True, cluster_id=-1, seed=""
      - list[dict]: take selected (default True) / text / cluster_id / seed
    clusters_json shape: list[{cluster_id, label, size}].

    The returned dict is isomorphic to SolutionQueriesSnapshot, persisted to queries_snapshot_json.
    """
    queries_out: list[dict[str, Any]] = []
    try:
        raw = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        raw = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    queries_out.append({"text": text, "cluster_id": -1, "seed": ""})
                continue
            if not isinstance(item, dict):
                continue
            # legacy items without a selected field default to True (old topics' queries all take part in monitoring by default)
            if not bool(item.get("selected", True)):
                continue
            if item.get("deleted_at"):          # soft-deleted words don't enter the strategy solution's monitoring list (a forward-looking deliverable)
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            cid = item.get("cluster_id")
            cluster_id = int(cid) if isinstance(cid, int) else -1
            seed = str(item.get("seed") or "")
            queries_out.append({"text": text, "cluster_id": cluster_id, "seed": seed})

    clusters_out: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    try:
        cluster_raw = json.loads(topic.clusters_json or "[]")
    except Exception:  # noqa: BLE001
        cluster_raw = []
    if isinstance(cluster_raw, list):
        for c in cluster_raw:
            if not isinstance(c, dict):
                continue
            try:
                cid = int(c.get("cluster_id"))
            except (TypeError, ValueError):
                continue
            label = str(c.get("label") or "").strip()
            if not label:
                continue
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            clusters_out.append({"cluster_id": cid, "label": label})

    return {"clusters": clusters_out, "queries": queries_out}


# ─────────────── Hard data: geo_checker scan ───────────────


def _empty_diagnosis() -> dict[str, Any]:
    """Placeholder when website_url is empty -- makes _call_llm skip the technical-diagnosis prompt sections,
    and the report page frontend uses score=0 + empty clusters to skip rendering the diagnosis / execution flow sections."""
    return {
        "url": "",
        "score": 0,
        "grade": "",
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
        "info_count": 0,
        "clusters": [],
        "execution_layers": [],
        "all_checks": [],
    }


def _run_diagnosis(url: str) -> dict[str, Any]:
    """Run run_geo_check for the 25-category results -> convert to a SolutionDiagnosis dict."""
    from geo.services.geo_checker import run_geo_check

    result = run_geo_check(url, include_fix=True, tier="growth")
    checks_all: list[dict[str, Any]] = []
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for c in (result.checks or []):
        item = {
            "category": c.category or "",
            "status": c.status or "",
            "message": c.message or "",
            "fix": c.fix or None,
            # Carry the i18n entry keys too: audit conclusions are mostly in English; the frontend prefers Chinese entries by key,
            # falling back to message. Without these two fields the per-item checkup table is full of English.
            "message_key": getattr(c, "message_key", None),
            "message_params": getattr(c, "message_params", None),
            "fix_key": getattr(c, "fix_key", None),
            "fix_params": getattr(c, "fix_params", None),
        }
        checks_all.append(item)
        by_cat.setdefault(item["category"], []).append(item)

    clusters: list[dict[str, Any]] = []
    severity_rank = {"PASS": 0, "INFO": 0, "WARN": 1, "FAIL": 2}
    for cdef in CLUSTER_DEFS:
        cluster_checks: list[dict[str, Any]] = []
        worst = 0
        for cat in cdef["categories"]:
            for it in by_cat.get(cat, []):
                cluster_checks.append(it)
                worst = max(worst, severity_rank.get(it["status"], 0))
        severity = "high" if worst >= 2 else ("med" if worst == 1 else "low")
        # bullets: first 60 chars of FAIL/WARN messages, at most 6
        bullets: list[str] = []
        for it in cluster_checks:
            if it["status"] in ("FAIL", "WARN"):
                msg = (it["message"] or "").strip()
                if msg:
                    bullets.append(msg[:120])
            if len(bullets) >= 6:
                break
        clusters.append({
            "key": cdef["key"],
            "title_zh": cdef["title_zh"],
            "severity": severity,
            "summary": "",   # left for the LLM to fill
            "bullets": bullets,
            "checks": cluster_checks,
        })

    # Three-tier execution flow: derived automatically from cluster severity
    layer_map = {
        "ai_protocol": "technical_base",
        "crawl_performance": "technical_base",
        "url_meta": "technical_base",
        "structured_data": "content_core",
        "content_safety": "weight_push",
    }
    layer_titles = {
        "technical_base": "技术地基",
        "content_core":   "内容核心",
        "weight_push":    "权重推力",
    }
    layer_hints = {
        "technical_base": "补齐 AI 公认的「实体信任」",
        "content_core":   "把品牌优势转化为 AI 易抓取的「事实库」",
        "weight_push":    "在站外高权重平台建立全网语义背书",
    }
    layer_agg: dict[str, dict[str, Any]] = {
        k: {"key": k, "title_zh": v, "hint": layer_hints[k], "clusters": []}
        for k, v in layer_titles.items()
    }
    for c in clusters:
        layer_key = layer_map.get(c["key"], "technical_base")
        layer_agg[layer_key]["clusters"].append(c["title_zh"])
    execution_layers = list(layer_agg.values())

    summary = result.summary or {}

    return {
        "url": result.url or url,
        "score": int(getattr(result, "score", 0) or 0),
        "grade": getattr(result, "grade", "") or "",
        "pass_count": int(summary.get("pass_count", 0) or 0),
        "warn_count": int(summary.get("warn_count", 0) or 0),
        "fail_count": int(summary.get("fail_count", 0) or 0),
        "info_count": int(summary.get("info_count", 0) or 0),
        "clusters": clusters,
        "execution_layers": execution_layers,
        "all_checks": checks_all,
    }


# ─────────────── LLM: copy ───────────────


def _call_llm(
    profile: BrandProfile, diagnosis: dict[str, Any], website_url: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """One LLM call produces the solution copy: cluster summaries + seven tailored steps + keywords + vision.

    Returns (narrative, keywords, model_id);
      narrative = {cluster_summaries: {key: text}, seven_steps: [...], vision: [...]}
      keywords  = {tiers: [SolutionKeywordTier]}
    """
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        provider, model_id, api_key = "deepseek", DEEPSEEK_MODEL, ds_key
    elif or_key:
        provider, model_id, api_key = "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    else:
        raise RuntimeError("DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置,无法生成战略方案")

    system_prompt = _build_system_prompt(profile, diagnosis, website_url)
    user_prompt = _build_user_prompt(profile, diagnosis)

    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Solution Generator",
        }
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
    }
    r = post_and_record(url, payload, headers, provider=provider,
                        feature="solution", timeout=DEFAULT_TIMEOUT, model=model_id)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")

    parsed = _parse_json_loose(content)
    narrative, keywords = _normalize_llm_output(parsed, profile, diagnosis)
    return narrative, keywords, model_id


def _build_system_prompt(profile: BrandProfile, diagnosis: dict, website_url: str) -> str:
    has_diag = bool((diagnosis or {}).get("clusters")) or bool((website_url or "").strip())
    if has_diag:
        intro = "你的任务:基于「品牌资料 + 站内技术诊断」,产出一份可交付的「GEO 品牌增长战略方案」JSON。"
    else:
        intro = ("你的任务:基于「品牌资料」(此次未提供官网,跳过站内技术诊断),"
                 "产出一份可交付的「GEO 品牌增长战略方案」JSON;只输出品牌定位 / 七步 / 关键词 / 愿景,"
                 "不要编造诊断分数。")
    parts = [
        "你是 GEO(Generative Engine Optimization)品牌增长战略顾问。",
        intro,
        "",
        "## 品牌资料",
    ]
    if website_url:
        parts.append(f"- 官网:{website_url}")
    if profile.company_full_name:
        parts.append(f"- 品牌全称:{profile.company_full_name}")
    if profile.company_short_name:
        parts.append(f"- 品牌简称:{profile.company_short_name}")
    if profile.industry:
        parts.append(f"- 行业:{profile.industry}")
    if profile.core_business_lines:
        parts.append(f"- 核心业务:{', '.join(profile.core_business_lines)}")
    if profile.target_scenarios:
        parts.append(f"- 目标场景:{', '.join(profile.target_scenarios)}")
    if profile.service_geo:
        parts.append(f"- 服务地域:{profile.service_geo}")
    if profile.brand_diff_tags:
        parts.append(f"- 品牌差异化标签:{', '.join(profile.brand_diff_tags)}")
    if profile.core_service_overview:
        parts.append(f"- 核心服务概述:{profile.core_service_overview}")
    if profile.brand_slogan:
        parts.append(f"- Slogan:{profile.brand_slogan}")
    if profile.core_message:
        parts.append(f"- 核心信息:{profile.core_message}")

    if diagnosis.get("clusters"):
        parts.append("")
        parts.append("## 站内技术诊断概览")
        parts.append(f"- AI 可见度评分:{diagnosis.get('score', 0)}/100 ({diagnosis.get('grade', '')})")
        parts.append(f"- 通过:{diagnosis.get('pass_count', 0)} · 警告:{diagnosis.get('warn_count', 0)} · 失败:{diagnosis.get('fail_count', 0)}")
        parts.append("")
        parts.append("## 5 大短板簇(已聚类)")
        for c in diagnosis.get("clusters", []):
            bullets = "; ".join(c.get("bullets", []) or [])
            parts.append(f"- 【{c['title_zh']}】(严重度 {c['severity']}):{bullets or '无明显短板'}")

    parts.append("")
    parts.append("## 写作要求(严格遵守)")
    parts.append("1. 全部用中文,符合「GEO 品牌增长战略」语境,语气专业但不堆术语。")
    parts.append("2. 不要使用 Markdown 符号(# ** * - ` 等);所有「中文标点」用全角(,。!?:;「」)。")
    parts.append("3. 关键词体系里产品 / 技术 / 场景 / 痛点词,英文场景的品牌(海外业务)用英文,中文场景用中文,贴近品牌实际市场。")
    parts.append("4. 严格按 user 给的 JSON schema 输出,不要多字段不要漏字段,不要 ``` 围栏。")
    return "\n".join(parts)


def _build_user_prompt(profile: BrandProfile, diagnosis: dict) -> str:
    cluster_keys = [c["key"] for c in diagnosis.get("clusters", [])]
    seven_steps_names = [s["name"] for s in SEVEN_STEPS_TEMPLATE]
    keyword_tiers = KEYWORD_TIERS_TEMPLATE
    schema_hint = {
        "cluster_summaries": {k: "string,40-120字,描述本簇短板对品牌AI可见性的具体影响" for k in cluster_keys},
        "seven_steps": [
            {
                "step": "int 1-7",
                "name": "string,跟模板一致",
                "core_goal": "string,跟模板一致",
                "core_action": "string,80-200字,针对本品牌的具体执行内容,要点名品牌名/产品/场景",
                "output_value": "string,40-100字,核心产出 & 价值",
            } for _ in range(7)
        ],
        "keyword_tiers": [
            {
                "tier": "tier slug(brand_core/product_model/tech/scenario/procurement/pain_point)",
                "title_zh": "中文层名",
                "description": "string,30-80字,这一层关键词的定位",
                "keywords": ["list[str],8-20 个具体关键词"],
            } for _ in keyword_tiers
        ],
        "vision": [
            {"title": "成为行业权威", "body": "string,80-180字,围绕「AI 引擎回答 XXX 时品牌成为首选引用源」展开"},
            {"title": "降低获客成本", "body": "string,80-180字,围绕「自然流量减少付费依赖」+ 量化预期"},
            {"title": "建立全球信任", "body": "string,80-180字,围绕「在主流 AI 模型中构建品牌形象」"},
        ],
    }
    return (
        "请基于上面的品牌资料 + 诊断,产出严格符合下面 schema 的 JSON。\n\n"
        f"七步增长模型固定模板(请按这 7 步顺序填充,name/core_goal 与模板一致):\n"
        f"{json.dumps([{'step': s['step'], 'name': s['name'], 'core_goal': s['core_goal']} for s in SEVEN_STEPS_TEMPLATE], ensure_ascii=False)}\n\n"
        f"6 层关键词体系(请按这 6 层顺序填充,tier/title_zh 与模板一致):\n"
        f"{json.dumps([{'tier': t['tier'], 'title_zh': t['title_zh']} for t in keyword_tiers], ensure_ascii=False)}\n\n"
        f"输出 JSON Schema:\n{json.dumps(schema_hint, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON,不要前后文,不要 ``` 围栏。"
    )


def _normalize_llm_output(
    raw: dict, profile: BrandProfile, diagnosis: dict,
) -> tuple[dict, dict]:
    """Validate the LLM's JSON + apply fallbacks + split into narrative / keywords.

    narrative = {cluster_summaries, seven_steps, vision}
    keywords  = {tiers: [...]}
    """
    # cluster_summaries
    cluster_summaries_raw = raw.get("cluster_summaries") or {}
    if not isinstance(cluster_summaries_raw, dict):
        cluster_summaries_raw = {}
    cluster_summaries: dict[str, str] = {}
    for c in diagnosis.get("clusters", []):
        v = cluster_summaries_raw.get(c["key"])
        cluster_summaries[c["key"]] = str(v).strip() if v else ""

    # seven_steps -- must be exactly 7 items, aligned to the template
    seven_steps_raw = raw.get("seven_steps") or []
    if not isinstance(seven_steps_raw, list):
        seven_steps_raw = []
    seven_steps: list[dict] = []
    for i, tpl in enumerate(SEVEN_STEPS_TEMPLATE):
        item = seven_steps_raw[i] if i < len(seven_steps_raw) and isinstance(seven_steps_raw[i], dict) else {}
        seven_steps.append({
            "step": tpl["step"],
            "name": tpl["name"],
            "core_goal": tpl["core_goal"],
            "core_action": str(item.get("core_action") or "").strip(),
            "output_value": str(item.get("output_value") or "").strip(),
        })

    # vision -- 3 paragraphs
    vision_raw = raw.get("vision") or []
    if not isinstance(vision_raw, list):
        vision_raw = []
    vision_defaults = [
        {"title": "成为行业权威", "body": ""},
        {"title": "降低获客成本", "body": ""},
        {"title": "建立全球信任", "body": ""},
    ]
    vision: list[dict] = []
    for i, d in enumerate(vision_defaults):
        v = vision_raw[i] if i < len(vision_raw) and isinstance(vision_raw[i], dict) else {}
        vision.append({
            "title": str(v.get("title") or d["title"]).strip(),
            "body": str(v.get("body") or "").strip(),
        })

    # keyword_tiers -- 6 tiers
    tiers_raw = raw.get("keyword_tiers") or []
    if not isinstance(tiers_raw, list):
        tiers_raw = []
    tiers: list[dict] = []
    for i, tpl in enumerate(KEYWORD_TIERS_TEMPLATE):
        t = tiers_raw[i] if i < len(tiers_raw) and isinstance(tiers_raw[i], dict) else {}
        kw_list = t.get("keywords") or []
        if not isinstance(kw_list, list):
            kw_list = []
        kws = [str(k).strip() for k in kw_list if str(k).strip()][:30]
        tiers.append({
            "tier": tpl["tier"],
            "title_zh": tpl["title_zh"],
            "description": str(t.get("description") or "").strip(),
            "keywords": kws,
        })

    narrative = {
        "cluster_summaries": cluster_summaries,
        "seven_steps": seven_steps,
        "vision": vision,
    }
    keywords = {"tiers": tiers}
    return narrative, keywords


def _parse_json_loose(text: str) -> dict:
    """JSON parsing that tolerates ``` fences."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.lstrip("`").lstrip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except Exception:  # noqa: BLE001
        pass
    import re
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict):
                return v
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"failed to parse JSON from LLM output: {s[:200]}")
