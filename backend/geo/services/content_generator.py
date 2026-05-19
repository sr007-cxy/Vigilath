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
import threading
from datetime import datetime

import requests

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryTopicORM, BrandProfile, TopicGeneratedDocORM,
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
    mark_auto_run: bool = False,
) -> None:
    """fire-and-forget thread.BackgroundTasks 已是 fire-and-forget,
    但为了不阻塞 FastAPI 的事件循环(LLM 单条 30-90s),再起一个 daemon thread.

    参数:
      max_docs            — 限制本次生成的稿件数;不传则按 env / 50 兜底
      queries_override    — 指定本次要写的 query 列表;不传则从 topic 拉 approved+selected
      mark_auto_run       — True 时写 auto_generate_last_run_at(cron / 立即生成入口用)
    """
    thread = threading.Thread(
        target=_run_generation_safe,
        args=(topic_id, plan_id, max_docs, queries_override, mark_auto_run),
        daemon=True,
    )
    thread.start()


def _run_generation_safe(
    topic_id: int, plan_id: int | None,
    max_docs: int | None, queries_override: list[str] | None,
    mark_auto_run: bool,
) -> None:
    try:
        _run_generation(topic_id, plan_id, max_docs, queries_override, mark_auto_run)
    except Exception as e:  # noqa: BLE001
        log.exception("content generation crashed for topic %d: %s", topic_id, e)


def _run_generation(
    topic_id: int, plan_id: int | None,
    max_docs_override: int | None, queries_override: list[str] | None,
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

        ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        # 优先级:DEEPSEEK_API_KEY > OPENROUTER_API_KEY > 都没有则记错
        if ds_key:
            provider, model_id = "deepseek", DEEPSEEK_MODEL
        elif or_key:
            provider, model_id = "openrouter", OPENROUTER_DEEPSEEK_MODEL
        else:
            provider, model_id = None, DEEPSEEK_MODEL

        for q in queries:
            # 2026-05-18:AI 生稿直接进 admin 审核队列。文章审核走 admin 单审,
            # 用户侧没有「送审」环节,落 draft 等于卡死。同步把 selected_for_review
            # 置 True,跟 admin /docs/select 选稿后的状态一致。
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
                title, body, summary = _generate_one(profile, q, provider, ds_key or or_key)
                doc.title = title
                doc.body_markdown = body
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
