"""Phase D — 内容文案生成 service.

入口 `schedule_generation(topic_id, plan_id)` 在 admin 通过资料审核时由
admin_review.approve_topic 通过 FastAPI BackgroundTasks 异步触发。

流程:
  1. 加载 topic 的资料 + 通过的监测问题(approved 且 selected)
  2. 对每条监测问题,组装 prompt(资料中创作方向/文案类型/平台/调性/雷区/Slogan)
  3. 调用 LLM(DeepSeek) 拿输出(JSON {title, body, summary})
  4. 落 TopicGeneratedDocORM(status=draft)

模型用 DeepSeek-Chat.两条路:
  - 直连:配 DEEPSEEK_API_KEY,走 https://api.deepseek.com/chat/completions
  - 走 OpenRouter:配 OPENROUTER_API_KEY,model 用 deepseek/deepseek-chat
两个都没配 → 落库时记错误,不阻塞审核流程.

失败粒度:单条 query 失败不影响其它,失败的稿件会用 generation_error 记录原因.

ENV:
    DEEPSEEK_API_KEY      可选,优先使用 → 直连 DeepSeek
    OPENROUTER_API_KEY    可选,fallback → 通过 OpenRouter 调 DeepSeek 模型
    DEEPSEEK_BASE_URL     可选,默认 https://api.deepseek.com
    DEEPSEEK_MODEL        直连模型 id,可选,默认 deepseek-chat
    OPENROUTER_DEEPSEEK_MODEL 可选,默认 deepseek/deepseek-chat
    GEO_CONTENT_TIMEOUT   可选,单条 LLM 请求超时(秒),默认 180
    GEO_CONTENT_MAX_DOCS  可选,单次审核最多生成多少稿(默认 = 监测问题数,上限 50)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Optional

import requests

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicORM, BrandProfile,
    ContentTemplateORM, TopicGeneratedDocORM, TopicMediaORM,
)

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_CONTENT_TIMEOUT", "180"))


def schedule_generation(
    *,
    topic_id: int,
    plan_id: int | None = None,
    max_docs: int | None = None,
    queries_override: list[str] | None = None,
    plan_item_ids: list[str] | None = None,
    mark_auto_run: bool = False,
) -> None:
    """fire-and-forget thread.BackgroundTasks 已是 fire-and-forget,
    但为了不阻塞 FastAPI 的事件循环(LLM 单条 30-90s),再起一个 daemon thread.

    参数:
      max_docs            — 限制本次生成的稿件数;不传则按 env / 50 兜底
      queries_override    — 指定本次要写的 query 列表;不传则按 plan_item_ids /
                            plan.publishing_plan_json / topic.queries_json 三段择优
      plan_item_ids       — 指定本次只跑 publishing_plan_items 里的哪几行(单条重生用)
      mark_auto_run       — True 时写 auto_generate_last_run_at(cron / 立即生成入口用)
    """
    thread = threading.Thread(
        target=_run_generation_safe,
        args=(topic_id, plan_id, max_docs, queries_override, plan_item_ids, mark_auto_run),
        daemon=True,
    )
    thread.start()


def _run_generation_safe(
    topic_id: int, plan_id: int | None,
    max_docs: int | None, queries_override: list[str] | None,
    plan_item_ids: list[str] | None,
    mark_auto_run: bool,
) -> None:
    try:
        _run_generation(
            topic_id, plan_id, max_docs, queries_override,
            plan_item_ids, mark_auto_run,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("content generation crashed for topic %d: %s", topic_id, e)


def _resolve_provider() -> tuple[str | None, str, str]:
    """挑 LLM provider + 拿 api key.返回 (provider | None, model_id, api_key)."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, DEEPSEEK_MODEL, ""


def _run_generation(
    topic_id: int, plan_id: int | None,
    max_docs_override: int | None, queries_override: list[str] | None,
    plan_item_ids: list[str] | None,
    mark_auto_run: bool,
) -> None:
    db = SessionLocal()
    try:
        t = db.get(AiTelemetryTopicORM, topic_id)
        if not t:
            log.warning("content gen: topic %d not found", topic_id)
            return
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

        # 新路径优先:plan_id + plan.publishing_plan_json 有内容 → 按 plan item 派工.
        # 旧路径(queries_override / topic.queries_json) 仅在没 plan 数据时走.
        plan_items: list[dict] = []
        if plan_id is not None:
            plan = db.get(AiTelemetryTopicExecutionPlanORM, plan_id)
            if plan:
                try:
                    raw = json.loads(plan.publishing_plan_json or "[]")
                except Exception:  # noqa: BLE001
                    raw = []
                plan_items = [it for it in raw if isinstance(it, dict)]
                if plan_item_ids:
                    wanted = set(plan_item_ids)
                    plan_items = [it for it in plan_items if it.get("id") in wanted]

        if plan_items:
            _run_per_item(
                db, t, profile, topic_id, plan_id, plan_items, mark_auto_run,
            )
            return

        # ── 兼容旧路径:按整批 queries 跑 ──────────────────────────────────────────
        if queries_override is not None:
            queries = [q for q in queries_override if isinstance(q, str) and q.strip()]
        else:
            try:
                qarr = json.loads(t.queries_json or "[]")
            except Exception:  # noqa: BLE001
                qarr = []
            queries = [
                q["text"] for q in qarr
                if isinstance(q, dict) and q.get("text")
                and q.get("selected", True) and q.get("status") == "approved"
            ]
        if not queries:
            log.info("content gen: no queries to write for topic %d", topic_id)
            return
        env_cap = int(os.environ.get("GEO_CONTENT_MAX_DOCS", "50"))
        cap = max_docs_override if max_docs_override and max_docs_override > 0 else env_cap
        cap = min(cap, env_cap, len(queries))
        queries = queries[:cap]

        provider, model_id, api_key = _resolve_provider()

        for q in queries:
            doc = TopicGeneratedDocORM(
                topic_id=topic_id, execution_plan_id=plan_id,
                source_query_text=q, status="pending_review",
                selected_for_review=True,
                llm_model=model_id, source="ai",
            )
            if not provider:
                doc.generation_error = "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置"
                doc.title = f"[未生成] {q}"
                db.add(doc)
                continue
            try:
                title, body, summary = _generate_one(profile, q, provider, api_key)
                doc.title = title
                doc.body_markdown = _append_media_to_body(db, topic_id, q, body)
                doc.summary = summary
            except Exception as e:  # noqa: BLE001
                log.warning("content gen failed for q='%s' topic=%d: %s", q, topic_id, e)
                doc.generation_error = str(e)[:500]
                doc.title = f"[生成失败] {q}"
            db.add(doc)
        if mark_auto_run:
            t.auto_generate_last_run_at = datetime.utcnow()
        db.commit()
        log.info("content gen done for topic %d: %d docs", topic_id, len(queries))
    finally:
        db.close()


def _run_per_item(
    db, t: AiTelemetryTopicORM, profile: BrandProfile,
    topic_id: int, plan_id: int | None,
    plan_items: list[dict], mark_auto_run: bool,
) -> None:
    """按 publishing_plan_items 逐条出稿.

    一条 item → 一篇 doc。doc 按 (topic_id, plan_item_id) upsert:
    - 已有 doc → 覆写 title/body/summary,把 status 回 pending_review
    - 没有 doc → 新建
    模板缺失或不存在 → 直接走旧的 _generate_one(画像 prompt 兜底).
    """
    provider, model_id, api_key = _resolve_provider()

    # 批量取模板,减少 N 次查询
    tmpl_ids = {it.get("template_id") for it in plan_items if it.get("template_id")}
    tmpl_by_id: dict[int, ContentTemplateORM] = {}
    if tmpl_ids:
        for tmpl in (
            db.query(ContentTemplateORM)
              .filter(ContentTemplateORM.id.in_(list(tmpl_ids)))
              .all()
        ):
            tmpl_by_id[tmpl.id] = tmpl

    # 已有 doc 索引(plan_item_id → doc)
    existing_by_item: dict[str, TopicGeneratedDocORM] = {}
    for d in (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.plan_item_id.isnot(None))
          .all()
    ):
        if d.plan_item_id:
            existing_by_item[d.plan_item_id] = d

    n_ok = 0
    for it in plan_items:
        item_id = str(it.get("id") or "")
        q = str(it.get("query") or "").strip()
        seed = str(it.get("seed") or "").strip()
        # seed-based 行用 seed 当主题;legacy 行 fallback 到 query.
        topic_text = seed or q
        platform = str(it.get("platform") or "") or None
        tmpl_id = it.get("template_id")
        tmpl = tmpl_by_id.get(tmpl_id) if tmpl_id else None
        if not topic_text:
            continue

        doc = existing_by_item.get(item_id)
        if doc is None:
            doc = TopicGeneratedDocORM(
                topic_id=topic_id, execution_plan_id=plan_id,
                plan_item_id=item_id or None,
                template_id=tmpl.id if tmpl else None,
                platform=platform,
                source_query_text=topic_text, status="pending_review",
                selected_for_review=True,
                llm_model=model_id, source="ai",
            )
            db.add(doc)
        else:
            # 重生:清错 + 把 status 回 pending_review,等 LLM 跑完覆写正文
            doc.template_id = tmpl.id if tmpl else None
            doc.platform = platform
            doc.source_query_text = topic_text
            doc.generation_error = None
            doc.status = "pending_review"
            doc.selected_for_review = True
            doc.llm_model = model_id
            doc.source = "ai"

        if not provider:
            doc.generation_error = "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置"
            doc.title = f"[未生成] {topic_text}"
            continue
        try:
            if tmpl:
                title, body, summary = _generate_with_template(
                    profile, tmpl, topic_text, platform or "", provider, api_key,
                    seed=seed or None,
                )
            else:
                title, body, summary = _generate_one(profile, topic_text, provider, api_key)
            doc.title = title
            doc.body_markdown = _append_media_to_body(db, topic_id, topic_text, body)
            doc.summary = summary
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("content gen failed for item=%s topic=%d: %s",
                        item_id, topic_id, e)
            doc.generation_error = str(e)[:500]
            doc.title = f"[生成失败] {topic_text}"

    if mark_auto_run:
        t.auto_generate_last_run_at = datetime.utcnow()
    db.commit()
    log.info(
        "content gen done(per-item) topic %d: ok=%d / total=%d",
        topic_id, n_ok, len(plan_items),
    )


_MEDIA_APPEND_MAX = 3
_MEDIA_BLOCK_HEADER = "── 自动配图建议 ──"

def _match_topic_media(db, topic_id: int, query: str) -> list[TopicMediaORM]:
    """按 query 关键词模糊匹配 topic 已上传图片(filename 子串).

    召回策略:
      1. 把 query 拆成关键词(去标点 / 短词),依次在 filename 里找子串
      2. 命中即累计;同一图片只算一次,按 uploaded_at desc 取前 _MEDIA_APPEND_MAX
      3. 若 query 无任何命中,退到「最新 3 张图」兜底(让 admin 至少有可挑的)
    """
    rows = (
        db.query(TopicMediaORM)
          .filter(TopicMediaORM.topic_id == topic_id, TopicMediaORM.kind == "image")
          .order_by(TopicMediaORM.uploaded_at.desc())
          .all()
    )
    if not rows:
        return []
    q = (query or "").lower()
    # 简单切词:把常见标点 / 空白替换成空格,过滤 1 字短词(英文 1 字母 / 中文单字保留意义低)
    tokens = [w for w in re.split(r"[\s,。!?:;、,/\\()\[\]【】「」“”\"'·.…—-]+", q) if len(w) >= 2]
    if not tokens:
        return rows[:_MEDIA_APPEND_MAX]
    hits: list[TopicMediaORM] = []
    seen: set[int] = set()
    for r in rows:
        fn = (r.filename or "").lower()
        if any(t in fn for t in tokens):
            if r.id not in seen:
                hits.append(r); seen.add(r.id)
        if len(hits) >= _MEDIA_APPEND_MAX:
            break
    if hits:
        return hits
    # 兜底:即使 filename 不匹配,也给最新几张让 admin 决定
    return rows[:_MEDIA_APPEND_MAX]


def _format_media_block(topic_id: int, medias: list[TopicMediaORM]) -> str:
    """正文末尾追加的纯文本配图块.不用 Markdown(平台直接粘贴会出乱码)."""
    if not medias:
        return ""
    lines = [_MEDIA_BLOCK_HEADER]
    for i, m in enumerate(medias, 1):
        # /api/ai-telemetry/topics/{tid}/media/{mid}/blob 是受 owner 鉴权的接口,
        # admin 在审稿页能直接预览;真正发布到 公众号/小红书 需 admin 手动下载.
        lines.append(
            f"{i}. {m.filename or f'media-{m.id}'} "
            f"→ /api/ai-telemetry/topics/{topic_id}/media/{m.id}/blob"
        )
    return "\n\n" + "\n".join(lines)


def _append_media_to_body(db, topic_id: int, query: str, body: str) -> str:
    """业务封装:body 已经生成完,按 query 召回图片,附在末尾.
    召回失败或 0 张 → 原样返回.异常吞掉(配图属增强,不能拖稿生成失败).
    """
    try:
        medias = _match_topic_media(db, topic_id, query)
        block = _format_media_block(topic_id, medias)
        if block:
            return (body or "") + block
        return body
    except Exception:  # noqa: BLE001
        log.warning("append media failed topic=%d q=%r — skipped", topic_id, query, exc_info=True)
        return body


def _render_template(prompt_template: str, vars: dict[str, object]) -> str:
    """简易 mustache 替换:{{key}} → str(value).未匹配的占位符保留原样."""
    s = prompt_template or ""
    for k, v in vars.items():
        s = s.replace("{{" + k + "}}", str(v if v is not None else ""))
    return s


def _build_brand_block(profile: BrandProfile) -> str:
    """画像摘要 — 给模板里 {{brand_block}} 占位用。比 _build_system_prompt 紧凑些."""
    parts: list[str] = []
    if profile.brand_diff_tags:
        parts.append(f"品牌差异化:{', '.join(profile.brand_diff_tags)}")
    if profile.content_tones:
        parts.append(f"调性:{', '.join(profile.content_tones)}")
    if profile.content_redlines:
        parts.append(f"内容雷区(必须避开):{', '.join(profile.content_redlines)}")
    if profile.brand_slogan:
        parts.append(f"Slogan:{profile.brand_slogan}")
    if profile.core_message:
        parts.append(f"核心信息:{profile.core_message}")
    return "\n".join(parts)


def _generate_with_template(
    profile: BrandProfile, tmpl: ContentTemplateORM,
    query: str, platform: str,
    provider: str, api_key: str,
    seed: Optional[str] = None,
) -> tuple[str, str, str]:
    """模板路径 — system prompt 仍走品牌画像,user prompt 走模板渲染.

    seed 非空时(seed-based plan),模板里 {seed} 拿到种子文本,{query} 拿到 seed 副本以兼容
    旧模板;legacy plan 行 seed 为 None,两个变量都拿 query.
    """
    system_prompt = _build_system_prompt(profile)
    brand_block = _build_brand_block(profile)
    user_prompt = _render_template(tmpl.prompt_template, {
        "query": query,
        "seed": seed or query,
        "brand": profile.company_short_name or profile.company_full_name or "",
        "industry": profile.industry or "",
        "platform": platform or (profile.target_platforms[0] if profile.target_platforms else ""),
        "length_min": tmpl.length_min,
        "length_max": tmpl.length_max,
        "brand_block": brand_block,
    })
    return _call_llm(system_prompt, user_prompt, provider, api_key)


def _call_llm(
    system_prompt: str, user_prompt: str,
    provider: str, api_key: str,
) -> tuple[str, str, str]:
    """OpenAI-兼容 /chat/completions 调用 + JSON 解析 + Markdown 兜底清洗."""
    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        model = DEEPSEEK_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        url = OPENROUTER_URL
        model = OPENROUTER_DEEPSEEK_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Content Generator",
        }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")
    parsed = _parse_json_loose(content)
    title = _strip_md_inline(str(parsed.get("title") or ""))[:200]
    body = _strip_md_block(str(parsed.get("body") or ""))
    summary = _strip_md_inline(str(parsed.get("summary") or ""))[:500]
    if not title or not body:
        raise RuntimeError("LLM returned empty title/body")
    return title, body, summary


def _generate_one(profile: BrandProfile, query: str, provider: str, api_key: str) -> tuple[str, str, str]:
    """单条 query → (title, body_markdown, summary).

    provider:
      - "deepseek":  直连 https://api.deepseek.com/chat/completions,model=deepseek-chat
      - "openrouter": 走 https://openrouter.ai,model=deepseek/deepseek-chat
    两条路 schema 都是 OpenAI 兼容 /chat/completions.
    """
    system_prompt = _build_system_prompt(profile)
    # 2026-05-18:稿件直接用于公众号 / 小红书 / 抖音 / 视频号发文,前端用
    # whitespace-pre-wrap 直出。Markdown 符号(#/**/-) 在这些平台上会以原文
    # 显示成乱码,所以这里强制要求 LLM 输出**纯净排版文本**。
    user_prompt = (
        f"针对下面这个问题,写一篇符合资料调性、可以直接复制到公众号/小红书/抖音文案区发布的文章。\n"
        f"问题:{query}\n\n"
        f"输出严格 JSON,字段:\n"
        f'  "title": 文案标题(吸睛,≤30 字,纯文本不要带任何符号修饰),\n'
        f'  "summary": 200 字内的摘要(纯文本,用于卡片预览),\n'
        f'  "body": 文章正文(800-1500 字)。\n\n'
        f"正文排版强制要求(违反会导致稿件不可用):\n"
        f"  1. 严禁使用 Markdown / HTML 标记 — 不要出现 # ## ### **加粗** *斜体* `代码` > 引用 [链接](url) ![图片] 等任何符号;\n"
        f"  2. 严禁使用 - * 1. 等列表前缀;需要分点时用「一、二、三、」或「①②③」中文序号开头另起一段;\n"
        f"  3. 小标题独占一行、不加任何符号修饰,正文段落与小标题之间空一行;\n"
        f"  4. 段落与段落之间用一个空行分隔,段内不要硬换行;\n"
        f"  5. 所有标点用中文全角(,。!?:;「」),英文术语 / 数字保留原样即可。\n\n"
        f"只输出 JSON,不要包前后 ``` 围栏。"
    )
    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        model = DEEPSEEK_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:  # openrouter
        url = OPENROUTER_URL
        model = OPENROUTER_DEEPSEEK_MODEL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Content Generator",
        }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        # DeepSeek/OpenRouter 都支持 OpenAI 风格 response_format;DeepSeek 要求 user
        # message 里出现 "json" 字样,我们 prompt 末尾明确写了"只输出 JSON",满足要求
        "response_format": {"type": "json_object"},
    }
    r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"unexpected {provider} response shape: {e}")
    parsed = _parse_json_loose(content)
    title = _strip_md_inline(str(parsed.get("title") or ""))[:200]
    body = _strip_md_block(str(parsed.get("body") or ""))
    summary = _strip_md_inline(str(parsed.get("summary") or ""))[:500]
    if not title or not body:
        raise RuntimeError("LLM returned empty title/body")
    return title, body, summary


# ─────────── Markdown 兜底清洗 ───────────
# prompt 已经明令禁止 MD,但 LLM 偶尔会偷偷出 `**xxx**` / `# 标题` / `- 项`,
# 直接落到稿件里就会以原始符号显示在公众号 / 小红书。这里做一次轻量正则清理:
# 只剥**安全的**结构性符号,不动正文文字。粗暴 strip 不是目标——目标是「人眼
# 读起来跟纯文本一样」。

import re as _re

# 行首 # / ## / ### / ####...  → 留文字、独占一行(LLM 一般用作小标题)
_RE_HEADING = _re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
# 行首  -  /  *  /  •  /  · / 数字. / 数字) — 列表前缀,直接删掉,正文用「一、」中文序号
_RE_LIST_PREFIX = _re.compile(r"(?m)^\s{0,3}([\-\*•·]|\d+[.)、])\s+")
# 行首 > 引用
_RE_BLOCKQUOTE = _re.compile(r"(?m)^\s{0,3}>\s?")
# **加粗** / __加粗__ → 文字
_RE_BOLD = _re.compile(r"\*\*([^*\n]+?)\*\*|__([^_\n]+?)__")
# *斜体* / _斜体_ → 文字(注意要避免把孤立的 * 当列表前缀;前面已处理)
_RE_ITALIC = _re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)|(?<!_)_([^_\n]+?)_(?!_)")
# `代码` → 文字
_RE_INLINE_CODE = _re.compile(r"`([^`\n]+?)`")
# ```围栏``` 整段保留内容,只剥围栏
_RE_FENCE = _re.compile(r"```[a-zA-Z0-9]*\n?([\s\S]*?)```")
# [文字](链接) → 文字;![alt](链接) → alt
_RE_LINK = _re.compile(r"!?\[([^\]\n]*?)\]\([^)\n]*?\)")
# 表格分隔行 |---|---|
_RE_TABLE_SEP = _re.compile(r"(?m)^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _strip_md_inline(s: str) -> str:
    """单行用 — 标题 / 摘要,清行内符号 + 折叠多空格."""
    s = _RE_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_INLINE_CODE.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    return s.strip()


def _strip_md_block(s: str) -> str:
    """正文用 — 先剥块级符号(标题/列表/引用/围栏/表格分隔),再走行内清理.
    最后压缩 3+ 连续空行到 2 行,避免段落空白爆炸.
    """
    s = _RE_FENCE.sub(r"\1", s)
    s = _RE_HEADING.sub("", s)
    s = _RE_BLOCKQUOTE.sub("", s)
    s = _RE_LIST_PREFIX.sub("", s)
    s = _RE_TABLE_SEP.sub("", s)
    s = _RE_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_INLINE_CODE.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    s = _re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _build_system_prompt(profile: BrandProfile) -> str:
    """资料 → 系统提示词,挑跟"文案创作"相关的字段."""
    parts: list[str] = ["你是品牌内容文案专家,根据下面的品牌资料写文案稿:"]
    if profile.company_full_name:
        parts.append(f"- 品牌全称:{profile.company_full_name}")
    if profile.company_short_name:
        parts.append(f"- 品牌简称:{profile.company_short_name}")
    if profile.industry:
        parts.append(f"- 行业:{profile.industry}")
    if profile.service_geo:
        parts.append(f"- 服务地域:{profile.service_geo}")
    if profile.core_business_lines:
        parts.append(f"- 核心业务:{', '.join(profile.core_business_lines)}")
    if profile.brand_diff_tags:
        parts.append(f"- 品牌差异化标签:{', '.join(profile.brand_diff_tags)}")
    if profile.creation_directions:
        parts.append(f"- 创作方向:{', '.join(profile.creation_directions)}")
    if profile.copywriting_types:
        parts.append(f"- 文案类型偏好:{', '.join(profile.copywriting_types)}")
    if profile.target_platforms:
        parts.append(f"- 适配平台:{', '.join(profile.target_platforms)}")
    if profile.content_tones:
        parts.append(f"- 内容调性:{', '.join(profile.content_tones)}")
    if profile.content_redlines:
        parts.append(f"- 内容雷区(禁止):{', '.join(profile.content_redlines)}")
    if profile.brand_slogan:
        parts.append(f"- Slogan:{profile.brand_slogan}")
    if profile.core_message:
        parts.append(f"- 本次核心信息:{profile.core_message}")
    parts.append("")
    parts.append("严格遵守内容雷区,语气贴合调性,围绕本次核心信息展开。")
    return "\n".join(parts)


def _parse_json_loose(text: str) -> dict:
    """从 LLM 输出里抓 JSON,容忍 ``` 围栏或前后文本."""
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
    # 兜底:正则抓第一对 { } 块
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
