"""4 维场景扩展 — 单 seed 并行喂 4 套 LLM 模板,产出搜索 / 问答 / 意图 / 品牌 4 类长尾.

2026-05-28 起接管 `/expand-queries` 的扩展产出.老路径只调 telemetry-service 拿 1 份,
新路径在我们这边做 LLM 调用,4 维并行,每维独立 prompt 模板.

模板根据讯灵实测反推:
  - search:  品类/产品搜索意图 — XX厂家/XX供应商/推荐XX(纯关键词)
  - qa:      问答 / 推荐 / 对比 — 哪家XX好/XX怎么选(带问句词)
  - intent:  用户意图查询 — 如何选XX/XX攻略(带意图词)
  - brand:   品牌评估 — {target}怎么样/详细介绍/客户评价(主语必须是品牌名)

ENV(复用 content_generator 的 2 路 fallback):
  DEEPSEEK_API_KEY      优先,直连 DeepSeek
  OPENROUTER_API_KEY    fallback,通过 OpenRouter 调 DeepSeek
  GEO_EXPANSION_TIMEOUT 单条 LLM 请求超时(秒),默认 90
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from geo.models.ai_telemetry import ALL_SCENES, SceneType

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_EXPANSION_TIMEOUT", "90"))


# ─────────────────── 4 套 prompt 模板 ──────────────────

SCENE_PROMPTS: dict[SceneType, str] = {
    "search": """你是 SEO 长尾词扩展专家。
针对种子词「{seed}」扩展 {count} 个**产品搜索意图**的长尾关键词。

扩展模式参考:
- {seed}厂家 / {seed}供应商 / {seed}生产商 / {seed}制造商
- 推荐{seed}厂家 / 优质{seed}供应商 / 高性价比{seed}
- {seed}多少钱 / {seed}评测 / {seed}排行
- {service_geo}{seed}(若 service_geo 非空,可适当加地域前缀)

要求:
1. 每条 ≤ 30 字,纯关键词形态,不带问号、不带「吗 / 呢」等问句词
2. 不要重复;不要疑问句(归 qa 场景)
3. 行业上下文:{industry}
4. 主体是品类/产品,不是品牌名

只输出 JSON 对象,字段名 "queries",值是字符串数组,如:
{{"queries": ["XX厂家", "XX供应商", "..."]}}
不要前后包 ```json``` 围栏,不要任何额外说明文字。""",

    "qa": """你是 AI 问答词扩展专家。
针对种子词「{seed}」扩展 {count} 个**Q&A / 推荐 / 对比**类长尾问句。

扩展模式参考:
- 哪家{seed}好 / {seed}怎么选 / {seed}选哪家
- {seed}对比 / {seed}推荐排行 / 求推荐{seed}
- {seed}怎么样 / {seed}靠谱吗 / {seed}值不值
- {seed}选哪个 / {seed}哪个性价比高

要求:
1. 每条 ≤ 40 字,必须带「吗 / 呢 / 哪家 / 怎么 / 哪个」等问句词
2. 不要重复
3. 行业:{industry};地域:{service_geo}

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",

    "intent": """你是用户意图扩展专家。
针对种子词「{seed}」扩展 {count} 个**意图查询**(用户问怎么做、怎么用、怎么选)。

扩展模式参考:
- 如何选{seed} / 怎么挑{seed} / {seed}怎么用
- {seed}攻略 / {seed}教程 / {seed}使用指南
- 新手{seed}怎么入门 / {seed}操作步骤
- {seed}使用方法 / {seed}什么时候用

要求:
1. 每条 ≤ 40 字,必须带「如何 / 怎么 / 攻略 / 指南 / 教程 / 方法」等意图词
2. 不要重复;不要纯品类词(归 search 场景)
3. 行业:{industry}

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",

    "brand": """你是品牌评估词扩展专家。
针对品牌名「{target}」(seed 是「{seed}」,作为业务/品类上下文)扩展 {count} 个**品牌评估**长尾问句。

品牌全称:{target}
品牌别名(可用):{aliases}
业务上下文 seed:{seed}

扩展模式参考:
- {target}怎么样 / {target}详细介绍 / {target}基本信息
- {target}公司概况 / {target}客户评价如何 / {target}性价比怎么样
- {target}实力如何 / {target}靠不靠谱 / {target}口碑
- {target}有什么{seed}产品(把 seed 作为业务线带入)
- {service_geo}{target}(若 service_geo 非空,加地域前缀)

要求:
1. 每条 30~40 字,主语必须是品牌名({target} 或别名),不是品类词
2. 涵盖正反两面(评价 / 口碑 / 性价比 / 客户 / 实力 / 资质)
3. 不要重复

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",
}


def render_prompt(
    scene: SceneType,
    *,
    seed: str,
    count: int,
    target: str = "",
    aliases: list[str] | None = None,
    industry: str = "",
    service_geo: str = "",
) -> str:
    """渲染 scene 对应的 system+user 合并 prompt(简化:直接当 user 输入)."""
    tpl = SCENE_PROMPTS.get(scene) or SCENE_PROMPTS["search"]
    return tpl.format(
        seed=seed,
        count=int(count),
        target=(target or seed),
        aliases="、".join((aliases or [])[:5]) or "(无)",
        industry=industry or "(未指定)",
        service_geo=service_geo or "",
    )


# ─────────────────── LLM provider 选择(复用 content_generator 同款) ──────────────────


def _resolve_provider() -> tuple[str | None, str, str]:
    """挑 LLM provider + 拿 api key.返回 (provider | None, model_id, api_key)."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, DEEPSEEK_MODEL, ""


# ─────────────────── 单 scene 调用 ──────────────────


def _parse_json_loose(text: str) -> dict[str, Any]:
    """把 LLM 输出宽松解析为 dict.允许前后包 ```json``` 围栏 / 前后多余文字."""
    s = (text or "").strip()
    # strip code fences
    if s.startswith("```"):
        s = s.lstrip("`")
        # 去掉首行 'json'
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in ("", "json"):
                s = rest
        if "```" in s:
            s = s.split("```", 1)[0]
    s = s.strip()
    # 尝试 1:整体解析
    try:
        out = json.loads(s)
        if isinstance(out, dict):
            return out
    except Exception:  # noqa: BLE001
        pass
    # 尝试 2:抠出 {...}
    lo = s.find("{")
    hi = s.rfind("}")
    if 0 <= lo < hi:
        try:
            out = json.loads(s[lo:hi + 1])
            if isinstance(out, dict):
                return out
        except Exception:  # noqa: BLE001
            pass
    return {}


async def expand_one_scene(
    *,
    scene: SceneType,
    seed: str,
    count: int,
    target: str = "",
    aliases: list[str] | None = None,
    industry: str = "",
    service_geo: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """对单个 scene 调一次 LLM,返回 {"queries": [...], "model": "...", "error": "..."}.

    失败不抛 — 把 error 写进返回字典,留给上层 fan-out 决定怎么展示.
    """
    provider, model_id, api_key = _resolve_provider()
    if not provider:
        return {"queries": [], "error": "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置"}

    prompt = render_prompt(
        scene,
        seed=seed, count=count, target=target,
        aliases=aliases, industry=industry, service_geo=service_geo,
    )

    if provider == "deepseek":
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        model = model_id
    else:  # openrouter
        url = OPENROUTER_URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Query Expander",
        }
        model = model_id

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.6,
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code != 200:
            return {"queries": [], "model": model_id,
                    "error": f"LLM HTTP {r.status_code}: {r.text[:200]}"}
        data = r.json()
        content = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
        )
        parsed = _parse_json_loose(content)
        raw_q = parsed.get("queries")
        if not isinstance(raw_q, list):
            return {"queries": [], "model": model_id,
                    "error": "LLM did not return queries array"}
        # 清洗:去空 / 去重 / 截长
        seen: set[str] = set()
        queries: list[str] = []
        for x in raw_q:
            s = str(x or "").strip()
            if not s or s in seen:
                continue
            s = s[:60]  # safety cap
            seen.add(s)
            queries.append(s)
        return {"queries": queries, "model": model_id}
    except httpx.HTTPError as e:
        return {"queries": [], "model": model_id, "error": f"HTTP error: {e}"}
    except Exception as e:  # noqa: BLE001
        log.warning("expand_one_scene crash scene=%s seed=%s: %s", scene, seed, e)
        return {"queries": [], "model": model_id, "error": str(e)[:300]}
    finally:
        if owns_client:
            await client.aclose()


# ─────────────────── 启发式分类(给历史 query 打 scene 标签) ──────────────────

# 顺序敏感:意图词必须先于问答词命中,避免「怎么用」被归 qa(怎么 = 问答?其实是意图).
_INTENT_MARKERS: tuple[str, ...] = (
    "如何", "怎么用", "怎么做", "怎么选", "怎么挑", "怎么入门",
    "攻略", "教程", "指南", "操作步骤", "使用方法", "什么时候用",
)
_QA_MARKERS: tuple[str, ...] = (
    "哪家", "哪个", "哪种", "选哪", "怎么样", "怎样",
    "推荐", "排行", "排名", "对比", "比较", "好不好", "靠谱吗", "靠不靠谱",
    "性价比怎么", "值不值", "好用吗", "可以信任", "可信", "?",
    "吗",  # 兜底句尾"吗"
)


def classify_query(
    text: str,
    *,
    target: str = "",
    aliases: list[str] | None = None,
) -> SceneType:
    """启发式把一条 query 归到 4 维场景之一(给历史 query 打标用).

    优先级:
      1. 文本里含 target 或 alias 字面 → brand
      2. 含意图词(如何 / 攻略 / 教程 / 怎么选 ...) → intent
      3. 含问答词(哪家 / 怎么样 / 对比 / 吗 ...) → qa
      4. 其余 → search

    这个分类只看字面,不调 LLM —— 跑得快、确定性强,适合一次性 backfill 大批量 query.
    对个别歧义 case(怎么用既像 intent 也像 qa)会按优先级一刀切,接受少量误分.
    """
    s = (text or "").strip()
    if not s:
        return DEFAULT_SCENE

    # 1. brand:文本含目标品牌名 / 别名
    brand_terms: list[str] = []
    if target:
        brand_terms.append(target.strip())
    for a in aliases or []:
        a = (a or "").strip()
        if a:
            brand_terms.append(a)
    for term in brand_terms:
        if term and term in s:
            return "brand"

    # 2. intent
    for marker in _INTENT_MARKERS:
        if marker in s:
            return "intent"

    # 3. qa
    for marker in _QA_MARKERS:
        if marker in s:
            return "qa"

    # 4. 默认 search
    return "search"


__all__ = [
    "ALL_SCENES", "SceneType", "SCENE_PROMPTS",
    "render_prompt", "expand_one_scene", "DEFAULT_TIMEOUT",
    "classify_query",
]
