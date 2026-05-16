"""Phase D — 内容文案生成 service.

入口 `schedule_generation(topic_id, plan_id)` 在 admin 通过画像审核时由
admin_review.approve_topic 通过 FastAPI BackgroundTasks 异步触发。

流程:
  1. 加载 topic 的画像 + 通过的监测问题(approved 且 selected)
  2. 对每条监测问题,组装 prompt(画像中创作方向/文案类型/平台/调性/雷区/Slogan)
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


def schedule_generation(*, topic_id: int, plan_id: int | None = None) -> None:
    """fire-and-forget thread.BackgroundTasks 已是 fire-and-forget,
    但为了不阻塞 FastAPI 的事件循环(LLM 单条 30-90s),再起一个 daemon thread.
    """
    thread = threading.Thread(
        target=_run_generation_safe, args=(topic_id, plan_id), daemon=True,
    )
    thread.start()


def _run_generation_safe(topic_id: int, plan_id: int | None) -> None:
    try:
        _run_generation(topic_id, plan_id)
    except Exception as e:  # noqa: BLE001
        log.exception("content generation crashed for topic %d: %s", topic_id, e)


def _run_generation(topic_id: int, plan_id: int | None) -> None:
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
            log.info("content gen: no approved+selected queries on topic %d", topic_id)
            return
        max_docs = min(int(os.environ.get("GEO_CONTENT_MAX_DOCS", "50")), len(queries))
        queries = queries[:max_docs]

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
            doc = TopicGeneratedDocORM(
                topic_id=topic_id, execution_plan_id=plan_id,
                source_query_text=q, status="draft",
                llm_model=model_id,
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
    user_prompt = (
        f"针对下面这个问题,写一篇符合画像调性的文案稿。\n"
        f"问题:{query}\n\n"
        f"输出严格 JSON,字段:\n"
        f'  "title": 文案标题(吸睛,≤30 字),\n'
        f'  "summary": 200 字内的摘要(用于卡片预览),\n'
        f'  "body": Markdown 正文(800-1500 字,有小标题/分段)。\n'
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
    title = str(parsed.get("title") or "")[:200]
    body = str(parsed.get("body") or "")
    summary = str(parsed.get("summary") or "")[:500]
    if not title or not body:
        raise RuntimeError("LLM returned empty title/body")
    return title, body, summary


def _build_system_prompt(profile: BrandProfile) -> str:
    """画像 → 系统提示词,挑跟"文案创作"相关的字段."""
    parts: list[str] = ["你是品牌内容文案专家,根据下面的品牌画像写文案稿:"]
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
    if profile.target_audience:
        parts.append(f"- 目标用户:{', '.join(profile.target_audience)}")
    if profile.user_pain_points:
        parts.append(f"- 用户痛点:{', '.join(profile.user_pain_points)}")
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
