"""根据 seed 主题用 DeepSeek 生成 query 候选 — 用户在 AI 遥测建话题前不再手填.

设计要点(2026-05-13 v2):
- AI 遥测的 SOV / 命中率核心信号是「问不点名 target 的真实场景问题,看 AI
  答复里会不会自然提到 target」。所以传入 target / aliases 时:
    1. 用 GEO-aware prompt 明确禁止候选中出现 target / aliases 字面
    2. 多要 ~1.4x,LLM 偶尔违规时还能过滤掉、不至于不够数
    3. 服务端再过一道 _drop_target_mentions(子串大小写不敏感)兜底
- target 缺省(API 兼容旧调用方)时回退到原通用 prompt,不做过滤
- 单 provider 直连 api.deepseek.com,失败抛 DeepSeekError,caller 翻 4xx/5xx

ENV:
- DEEPSEEK_API_KEY  必填
- DEEPSEEK_BASE_URL 可选,默认 https://api.deepseek.com
- DEEPSEEK_MODEL    可选,默认 deepseek-chat
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
    "respond about a given topic. Output one prompt per line, no numbering, no bullets, no commentary. "
    "Always write every prompt in the SAME LANGUAGE as the seed query."
)

_BULLET_RE = re.compile(r"^[•\-\*\d\.\)\]\s>]+")


def _has_cjk(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def _user_msg_zh_geo(seed: str, count: int, target: str, aliases: list[str], industry: str) -> str:
    alias_part = "、".join(f"「{a}」" for a in aliases) if aliases else ""
    avoid = f"「{target}」" + (f"及其别名 {alias_part}" if alias_part else "")
    ind = f"(行业:{industry})" if industry else ""
    return (
        f"你在帮品牌「{target}」{ind} 做 GEO(生成式搜索优化)监测。"
        f"请围绕主题「{seed}」生成 {count} 条**潜在客户会向 AI 助手(ChatGPT / DeepSeek / 豆包等)提的真实问题**。\n"
        f"\n"
        f"硬性要求:\n"
        f"1. **绝对禁止**在 query 里出现 {avoid} — 我们要测的是 AI 会不会"
        f"「自然」提到它,query 里点名了就是作弊,这一类全部作废。\n"
        f"2. 覆盖三类用户决策阶段,数量大致均衡:\n"
        f"   - 认知期:「我需要这个吗」「这是什么」「适合谁」「有什么用」\n"
        f"   - 对比期:「A vs B」「怎么选」「有什么区别」「优劣对比」\n"
        f"   - 决策期:「预算多少」「性价比」「哪家最值」「推荐哪家」\n"
        f"3. **口语化**,像真实用户对 AI 助手说话,不是 SEO 关键词堆砌。\n"
        f"4. 每条 15-50 个汉字,不要太短(短 query 信号弱)。\n"
        f"5. 全部用中文输出,不要混入英文;不要编号,不要项目符号,不要解释,**每行一条**。"
    )


def _user_msg_en_geo(seed: str, count: int, target: str, aliases: list[str], industry: str) -> str:
    alias_part = ", ".join(f'"{a}"' for a in aliases) if aliases else ""
    avoid = f'"{target}"' + (f" or its aliases {alias_part}" if alias_part else "")
    ind = f" (industry: {industry})" if industry else ""
    return (
        f"You are helping the brand \"{target}\"{ind} run a GEO (generative-engine "
        f"optimization) audit. Generate {count} realistic prompts a potential customer "
        f"would send to an AI assistant (ChatGPT / DeepSeek / etc.) about the topic: '{seed}'.\n"
        f"\n"
        f"Hard requirements:\n"
        f"1. **Never mention {avoid}** in the prompt — we are measuring whether the AI "
        f"brings it up on its own; if the prompt names it, the test is cheating.\n"
        f"2. Roughly balance across three buyer stages:\n"
        f"   - Awareness: \"what is X\", \"do I need X\", \"who is it for\"\n"
        f"   - Comparison: \"A vs B\", \"how to choose\", \"differences\"\n"
        f"   - Decision: \"budget / pricing\", \"best value\", \"recommend one\"\n"
        f"3. Conversational tone, like a real user talking to an AI assistant — not SEO keywords.\n"
        f"4. 8-20 words each. No numbering, no bullets, no commentary. One prompt per line."
    )


def _user_msg_zh_generic(seed: str, count: int) -> str:
    return (
        f"请围绕主题「{seed}」生成 {count} 条用户可能向 AI 助手提问的中文搜索 prompt。"
        f"要求:语义相关但表达各异;混合问句形式(什么/如何/为什么/最好的/对比/替代方案)、"
        f"意图类型(信息型、对比型、交易型)和措辞风格;每条 6-30 个汉字;"
        f"全部用中文输出,不要混入英文;不要编号,不要项目符号,每行一条。"
    )


def _user_msg_en_generic(seed: str, count: int) -> str:
    return (
        f"Generate {count} semantically related but distinct prompts a real user "
        f"might send to an AI assistant when they care about: '{seed}'. "
        f"Mix question forms (what/how/why/best/vs/alternatives), intent types "
        f"(informational, comparison, transactional), and phrasing styles. "
        f"Keep each prompt 4-15 words. Write every prompt in English. "
        f"Do not number. One prompt per line."
    )


def _user_msg(seed: str, count: int, target: str, aliases: list[str], industry: str) -> str:
    cjk = _has_cjk(seed) or _has_cjk(target)
    if target.strip():
        return _user_msg_zh_geo(seed, count, target, aliases, industry) if cjk \
            else _user_msg_en_geo(seed, count, target, aliases, industry)
    return _user_msg_zh_generic(seed, count) if cjk else _user_msg_en_generic(seed, count)


def _parse_lines(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = _BULLET_RE.sub("", line).strip().strip('"\'')
        if 4 <= len(line) <= 200 and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _drop_target_mentions(lines: list[str], target: str, aliases: list[str]) -> list[str]:
    """子串大小写不敏感过滤 — LLM 偶尔违规时兜底。"""
    if not target.strip():
        return lines
    terms = [target.strip().lower()]
    terms.extend(a.strip().lower() for a in aliases if a.strip())
    terms = [t for t in terms if t]  # 去空
    out: list[str] = []
    for line in lines:
        low = line.lower()
        if any(t in low for t in terms):
            continue
        out.append(line)
    return out


class DeepSeekError(Exception):
    """DeepSeek 调用失败 — caller 翻译成 4xx/5xx."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


async def suggest_queries(
    seed: str, count: int = 200, *,
    target: str = "", aliases: Optional[list[str]] = None, industry: str = "",
) -> list[str]:
    """调 DeepSeek 生成候选 query。

    传 target 时走 GEO-aware prompt(避开 target / aliases),并多要 ~1.3x 再过滤;
    不传 target 时退化到通用 prompt(向后兼容)。

    失败时抛 DeepSeekError(no_key / invalid_seed / http_4xx / network / empty)。
    """
    seed = (seed or "").strip()
    if not seed:
        raise DeepSeekError("invalid_seed", "seed 不能为空")
    count = max(5, min(count, 300))
    target = (target or "").strip()
    aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
    industry = (industry or "").strip()

    # GEO-aware 时多要点儿,留过滤损耗余量;300 是 prompt+output 安全上限
    raw_count = min(300, int(count * 1.3)) if target else count

    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise DeepSeekError("no_key", "DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_MSG},
            {"role": "user", "content": _user_msg(seed, raw_count, target, aliases, industry)},
        ],
        "temperature": 0.9,
        # DeepSeek deepseek-chat max output 8192 tokens;中文 query 平均 ~30 token/条
        "max_tokens": min(8000, raw_count * 35),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    try:
        # 200 条候选大约 30-90s;给 180s 余量,backend 转发端给 200s
        async with httpx.AsyncClient(timeout=180.0) as client:
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
    if target:
        lines = _drop_target_mentions(lines, target, aliases)
    if not lines:
        raise DeepSeekError("empty", "候选全被过滤或 LLM 返回为空,换个 seed 重试")
    return lines[:count]
