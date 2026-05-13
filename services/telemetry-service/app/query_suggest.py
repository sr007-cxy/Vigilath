"""根据 seed 主题用 DeepSeek 生成 query 候选 — 用户在 AI 遥测建话题前不再手填.

来源:tmp/geo_prompt.py 的 _expand_llm_deepseek + _llm_user_msg 子集,这里只保留
DeepSeek 直连(api.deepseek.com,OpenAI 兼容协议),不做 OpenAI / Anthropic 兜底 —
遥测候选场景跑得很轻,单 provider 失败就报错给前端比兜底链路更好排障。

ENV:
- DEEPSEEK_API_KEY  必填,缺则返回 400 让 caller 提示运维
- DEEPSEEK_BASE_URL 可选,默认 https://api.deepseek.com
"""
from __future__ import annotations

import os
import re
from typing import Optional

import httpx


DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_SYSTEM_MSG = (
    "You generate realistic user search prompts for evaluating how AI assistants "
    "respond about a given topic. Output one prompt per line, no numbering, no bullets."
)

_BULLET_RE = re.compile(r"^[•\-\*\d\.\)\]\s>]+")


def _has_cjk(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def _user_msg(seed: str, count: int) -> str:
    if _has_cjk(seed):
        return (
            f"请围绕主题「{seed}」生成 {count} 条用户可能向 AI 助手提问的中文搜索 prompt。"
            f"要求:语义相关但表达各异;混合问句形式(什么/如何/为什么/最好的/对比/替代方案)、"
            f"意图类型(信息型、对比型、交易型)和措辞风格;每条 6-30 个汉字;"
            f"全部用中文输出,不要混入英文;不要编号,不要项目符号,每行一条。"
        )
    return (
        f"Generate {count} semantically related but distinct prompts a real user "
        f"might send to an AI assistant when they care about: '{seed}'. "
        f"Mix question forms (what/how/why/best/vs/alternatives), intent types "
        f"(informational, comparison, transactional), and phrasing styles. "
        f"Keep each prompt 4-15 words. Write every prompt in English. "
        f"Do not number. One prompt per line."
    )


def _parse_lines(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = _BULLET_RE.sub("", line).strip().strip('"\'')
        if 4 <= len(line) <= 200 and line not in seen:
            seen.add(line)
            out.append(line)
    return out


class DeepSeekError(Exception):
    """DeepSeek 调用失败 — caller 翻译成 4xx/5xx."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


async def suggest_queries(seed: str, count: int = 20) -> list[str]:
    """调 DeepSeek 生成候选 query,返回去重后的字符串列表。

    失败时抛 DeepSeekError(无 key / 调用超时 / HTTP 4xx5xx / 返回为空)。
    """
    seed = (seed or "").strip()
    if not seed:
        raise DeepSeekError("invalid_seed", "seed 不能为空")
    count = max(5, min(count, 50))

    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise DeepSeekError("no_key", "DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_MSG},
            {"role": "user", "content": _user_msg(seed, count)},
        ],
        "temperature": 0.9,
        "max_tokens": min(4096, count * 25),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise DeepSeekError("network", str(e)) from e

    if r.status_code != 200:
        raise DeepSeekError(f"http_{r.status_code}", r.text[:200])
    try:
        data = r.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (ValueError, IndexError, KeyError) as e:
        raise DeepSeekError("parse", str(e)) from e

    lines = _parse_lines(text)
    if not lines:
        raise DeepSeekError("empty", "DeepSeek 返回空候选,换个 seed 重试")
    return lines[:count]
