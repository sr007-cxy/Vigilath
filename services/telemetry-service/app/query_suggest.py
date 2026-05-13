"""根据 seed 主题生成 query 候选 — 用户在 AI 遥测建话题前不再手填.

设计要点(2026-05-13 v3):
- 候选来源混合两路:
    1. DeepSeek LLM 合成(主力,提供"用户口语化问题"语料)
    2. Baidu sugrec + Bing osjson autocomplete(补真实搜索行为快照,中文场景下
       google / DDG 返回 0 不接)
  两路并行(asyncio.gather),autocomplete 不增加 wall-clock 延迟。
- 4 维 composite 评分(0-100):seed_relevance / retrieval_potential /
  commercial_intent / uniqueness。LLM 输出长度/熵都满分,human_like/entropy
  砍掉避免无信息维度稀释。
- target 非空时:LLM 走 GEO-aware prompt + _drop_target_mentions 兜底过滤,
  并对 autocomplete 结果一并应用过滤(autocomplete 高频出 target 字面)。
- 单 provider 失败不致命:autocomplete 整路失败只让 LLM 候选直出。

ENV:
- DEEPSEEK_API_KEY    必填
- DEEPSEEK_BASE_URL   可选,默认 https://api.deepseek.com
- DEEPSEEK_MODEL      可选,默认 deepseek-chat
"""
from __future__ import annotations

import asyncio
import math
import os
import re
from collections import Counter
from typing import Optional

import httpx


DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_SUGGEST_TIMEOUT = 6.0
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_SYSTEM_MSG = (
    "You generate realistic user search prompts for evaluating how AI assistants "
    "respond about a given topic. Output one prompt per line, no numbering, no bullets, no commentary. "
    "Always write every prompt in the SAME LANGUAGE as the seed query."
)

_BULLET_RE = re.compile(r"^[•\-\*\d\.\)\]\s>]+")
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'-]*|[一-鿿]")
_CJK_RE = re.compile(r"[一-鿿]")


# ─── autocomplete 扇出模板(CJK / EN 分路)──────────────────

_CJK_SUFFIXES = [
    "费用", "价格", "收费", "推荐", "排名", "对比",
    "评价", "案例", "流程", "注意事项", "怎么选", "哪个好",
]
_CJK_QUESTION_PREFIXES = [
    "什么是", "如何", "怎么", "怎样", "为什么",
    "哪里有", "哪个", "哪种", "能否", "是否",
]
_CJK_COMMERCIAL_PREFIXES = [
    "最好的", "最佳", "推荐的", "知名的", "排名靠前的",
]

_EN_QUESTION_PREFIXES = [
    "what is", "how does", "how to", "why is", "when to use",
    "is", "are", "can", "should", "which",
]
_EN_COMMERCIAL_PREFIXES = [
    "best", "top", "cheap", "free", "alternatives to",
    "vs", "comparison", "review of",
]


# ─── 评分用词表 ────────────────────────────────────

_COMMERCIAL_TERMS_EN = {
    "best", "top", "cheap", "cheapest", "free", "alternative", "alternatives",
    "vs", "versus", "compared", "comparison", "review", "reviews", "rating",
    "price", "pricing", "cost", "buy", "purchase", "discount", "recommend",
    "recommendation", "recommended", "leader", "leading",
}
_CJK_COMMERCIAL_MARKERS = (
    "最好", "最佳", "推荐", "排名", "排行",
    "对比", "比较", "评价", "评测", "测评",
    "价格", "费用", "收费", "价位",
    "哪个好", "怎么选", "排行榜", "性价比",
)

_QUESTION_WORDS_EN = {
    "what", "how", "why", "when", "where", "who", "which",
    "is", "are", "can", "should", "does", "do", "will", "would",
}
_CJK_QUESTION_MARKERS = (
    "什么", "怎么", "怎样", "如何", "为什么",
    "哪里", "哪个", "哪种", "哪些", "能否", "是否",
    "吗", "呢", "?", "?",
)

_VERBS_EN = {
    "do", "does", "is", "are", "use", "uses", "using", "build", "make", "made",
    "compare", "choose", "pick", "find", "get", "learn", "track", "monitor",
    "measure", "deploy", "configure", "set", "integrate", "optimize",
}

_STOP_WORDS_EN = {
    "a", "an", "the", "of", "for", "to", "in", "on", "with",
    "and", "or", "but", "my", "your", "our", "their", "this", "that",
}
_CJK_STOP_CHARS = set("的了是在和或吗呢啊呀这那个就也都还有为以及但")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


# ─── LLM prompt 构造(GEO-aware / generic 两套)────────────


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


# ─── DeepSeek 调用 ─────────────────────────────────


class DeepSeekError(Exception):
    """DeepSeek 调用失败 — caller 翻译成 4xx/5xx."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


async def _fetch_llm(seed: str, raw_count: int, target: str, aliases: list[str],
                    industry: str) -> list[str]:
    """调 DeepSeek 拿候选;失败抛 DeepSeekError(LLM 是主力,这一路挂了整个 endpoint 应该 5xx)."""
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
        "max_tokens": min(8000, raw_count * 35),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    try:
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
    return _parse_lines(text)


# ─── Autocomplete(Baidu / Bing)────────────────────


async def _suggest_baidu(client: httpx.AsyncClient, query: str) -> list[str]:
    try:
        r = await client.get(
            "https://www.baidu.com/sugrec",
            params={"prod": "pc", "wd": query},
            timeout=_SUGGEST_TIMEOUT,
            headers={"User-Agent": _UA},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get("g"), list):
            return [item.get("q", "") for item in data["g"]
                    if isinstance(item, dict) and item.get("q")]
    except (httpx.HTTPError, ValueError):
        pass
    return []


async def _suggest_bing(client: httpx.AsyncClient, query: str) -> list[str]:
    params = {"query": query}
    if _has_cjk(query):
        params["mkt"] = "zh-CN"
    try:
        r = await client.get(
            "https://api.bing.com/osjson.aspx",
            params=params,
            timeout=_SUGGEST_TIMEOUT,
            headers={"User-Agent": _UA},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            return [s for s in data[1] if isinstance(s, str)]
    except (httpx.HTTPError, ValueError):
        pass
    return []


def _fan_out_queries(seed: str) -> list[str]:
    """seed → 一批扩展 query 喂 autocomplete。CJK / EN 分两条模板路径。"""
    queries = [seed]
    if _has_cjk(seed):
        for suffix in _CJK_SUFFIXES:
            queries.append(f"{seed}{suffix}")
        for prefix in _CJK_QUESTION_PREFIXES:
            queries.append(f"{prefix}{seed}")
        for prefix in _CJK_COMMERCIAL_PREFIXES:
            queries.append(f"{prefix}{seed}")
    else:
        for prefix in _EN_QUESTION_PREFIXES:
            queries.append(f"{prefix} {seed}")
        for prefix in _EN_COMMERCIAL_PREFIXES:
            queries.append(f"{prefix} {seed}")
    return queries


_SUGGEST_CONCURRENCY = 8  # 同时打 Baidu/Bing 的并发上限,避免触发反爬


async def _fetch_suggest(seed: str) -> list[tuple[str, str]]:
    """并行打 Baidu + Bing autocomplete。返回 [(text, "suggest:baidu"), ...]。

    用 Semaphore 限流 8 并发,避免高频请求被 rate limit。
    任何 provider/请求失败都吞掉(autocomplete 是补充源,不致命)。
    """
    fan = _fan_out_queries(seed)
    sem = asyncio.Semaphore(_SUGGEST_CONCURRENCY)

    async def _gated(source: str, coro_fn, client, q):
        async with sem:
            return source, await coro_fn(client, q)

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient() as client:
        tasks = []
        for q in fan:
            tasks.append(_gated("suggest:baidu", _suggest_baidu, client, q))
            tasks.append(_gated("suggest:bing", _suggest_bing, client, q))
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            continue
        source, lines = res
        if not isinstance(lines, list):
            continue
        for s in lines:
            norm = s.strip()
            if 4 <= len(norm) <= 200:
                key = norm.lower()
                if key not in seen:
                    seen.add(key)
                    out.append((norm, source))
    return out


# ─── Target 兜底过滤(autocomplete + LLM 统一过)─────


def _drop_target_mentions(items: list[tuple[str, str]],
                          target: str, aliases: list[str]) -> list[tuple[str, str]]:
    """子串大小写不敏感过滤 — 候选含 target / aliases 字面的整条丢掉。

    target 空时直通(向后兼容)。
    """
    if not target.strip():
        return items
    terms = [target.strip().lower()]
    terms.extend(a.strip().lower() for a in aliases if a.strip())
    terms = [t for t in terms if t]
    out: list[tuple[str, str]] = []
    for text, source in items:
        low = text.lower()
        if any(t in low for t in terms):
            continue
        out.append((text, source))
    return out


# ─── 4 维 composite 评分 ───────────────────────────


def _seed_core_terms(seed: str) -> list[str]:
    """CJK seed 拆 2 字 chunk(融资律师→[融资,律师]);EN 拆 content tokens。"""
    seed = (seed or "").strip()
    if not seed:
        return []
    if _has_cjk(seed):
        cjk_only = "".join(c for c in seed if not c.isspace())
        chunks: list[str] = []
        i = 0
        while i + 1 < len(cjk_only):
            chunks.append(cjk_only[i:i + 2])
            i += 2
        if i < len(cjk_only):
            if chunks:
                chunks[-1] = chunks[-1] + cjk_only[i]
            else:
                chunks.append(cjk_only[i])
        return [c for c in chunks if c]
    tokens = _tokenize(seed)
    content = [t for t in tokens if t not in _STOP_WORDS_EN]
    return content or tokens


def _score_seed_relevance(text: str, seed_terms: list[str]) -> float:
    if not text or not seed_terms:
        return 0.0
    text_l = text.lower()
    hits = sum(1 for t in seed_terms if t.lower() in text_l)
    return hits / len(seed_terms)


def _score_retrieval_potential(text: str) -> float:
    """含问句词 / 动词 / ≥2 实词。LLM 候选大多满分,autocomplete 偏低。"""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    score = 0.0
    if (any(t in _QUESTION_WORDS_EN for t in tokens)
            or any(m in text for m in _CJK_QUESTION_MARKERS)):
        score += 0.5
    if any(t in _VERBS_EN for t in tokens):
        score += 0.2
    content = [t for t in tokens
               if t not in _STOP_WORDS_EN and t not in _CJK_STOP_CHARS]
    if len(content) >= 2:
        score += 0.3
    return min(1.0, score)


def _score_commercial_intent(text: str) -> float:
    tokens = _tokenize(text)
    hits = sum(1 for t in tokens if t in _COMMERCIAL_TERMS_EN)
    hits += sum(1 for m in _CJK_COMMERCIAL_MARKERS if m in text)
    if hits == 0:
        return 0.0
    return 0.6 if hits == 1 else 1.0


def _score_uniqueness(text: str, accepted_token_sets: list[set[str]]) -> float:
    tokens = set(_tokenize(text))
    if not tokens or not accepted_token_sets:
        return 1.0
    max_j = 0.0
    for prev in accepted_token_sets:
        union = tokens | prev
        if not union:
            continue
        j = len(tokens & prev) / len(union)
        if j > max_j:
            max_j = j
    return max(0.0, 1.0 - max_j)


def _score_candidate(text: str, seed_terms: list[str],
                     accepted_token_sets: list[set[str]]) -> int:
    """4 维加权,seed_relevance 阻尼,出 0-100 int。

    权重设计:LLM 输出长度/熵都满分,human_like/entropy 无信息量已砍。
    """
    seed_rel = _score_seed_relevance(text, seed_terms) if seed_terms else 1.0
    retrieval = _score_retrieval_potential(text)
    commercial = _score_commercial_intent(text)
    uniqueness = _score_uniqueness(text, accepted_token_sets)
    base = (
        seed_rel * 0.35
        + retrieval * 0.25
        + commercial * 0.20
        + uniqueness * 0.20
    ) * 100
    # seed_relevance 阻尼,但 seed_relevance 已经在 base 里了,这里不再重复乘
    return int(round(base))


# ─── Public entry ─────────────────────────────────


async def suggest_queries(
    seed: str, count: int = 200, *,
    target: str = "", aliases: Optional[list[str]] = None, industry: str = "",
    include_autocomplete: bool = True,
    include_clusters: bool = True,
) -> tuple[list[dict], list[dict]]:
    """seed → (candidates, clusters_meta)。

    candidates = [{text, score, sources, cluster_id}, ...],按 score 降序。
    clusters_meta = [{cluster_id, label, size, medoid_index}, ...] 按 size 降序;
                    include_clusters=False 或候选 <4 时返回空列表。

    并行调:
      - DeepSeek(主力,GEO-aware prompt + 自身去重)
      - Baidu sugrec + Bing osjson(补真实搜索词;include_autocomplete=False 可关)
    打分后跑 sentence-transformers 嵌入 + K-Means(silhouette auto-K 3-8)。

    target 非空时:LLM 多要 ~1.3x 量,合并后统一过 _drop_target_mentions 兜底。
    失败时抛 DeepSeekError(no_key / invalid_seed / http_4xx / network / empty)。
    """
    seed = (seed or "").strip()
    if not seed:
        raise DeepSeekError("invalid_seed", "seed 不能为空")
    count = max(5, min(count, 300))
    target = (target or "").strip()
    aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
    industry = (industry or "").strip()

    raw_count = min(300, int(count * 1.3)) if target else count

    # 两路并行:LLM 必须成功(主力),autocomplete 失败吞掉
    llm_task = _fetch_llm(seed, raw_count, target, aliases, industry)
    if include_autocomplete:
        results = await asyncio.gather(
            llm_task, _fetch_suggest(seed), return_exceptions=True,
        )
        llm_res, sug_res = results[0], results[1]
    else:
        llm_res = await llm_task
        sug_res = []

    # LLM 出错直接抛(主力);autocomplete 出错只 log 不影响
    if isinstance(llm_res, Exception):
        raise llm_res
    llm_lines: list[str] = llm_res
    sug_pairs: list[tuple[str, str]] = sug_res if isinstance(sug_res, list) else []

    # 合并 + 去重(归一化小写),记录每条 text 来自哪几个 source
    merged: dict[str, dict] = {}
    for text in llm_lines:
        key = text.strip().lower()
        if not key:
            continue
        merged.setdefault(key, {"text": text.strip(), "sources": []})
        merged[key]["sources"].append("llm:deepseek")
    for text, source in sug_pairs:
        key = text.strip().lower()
        if not key:
            continue
        merged.setdefault(key, {"text": text.strip(), "sources": []})
        if source not in merged[key]["sources"]:
            merged[key]["sources"].append(source)

    # target 过滤(LLM + suggest 统一过)
    if target:
        pairs = [(m["text"], "_") for m in merged.values()]
        kept_texts = {t.lower() for t, _ in _drop_target_mentions(pairs, target, aliases)}
        merged = {k: v for k, v in merged.items() if v["text"].lower() in kept_texts}

    if not merged:
        raise DeepSeekError("empty", "候选全被过滤或 LLM 返回为空,换个 seed 重试")

    # 评分(uniqueness 依赖输入顺序,按 sources 优先级排:先 LLM 后 suggest)
    seed_terms = _seed_core_terms(seed)
    items = list(merged.values())
    items.sort(key=lambda m: (0 if "llm:deepseek" in m["sources"] else 1, m["text"]))
    accepted: list[set[str]] = []
    scored: list[dict] = []
    for m in items:
        score = _score_candidate(m["text"], seed_terms, accepted)
        scored.append({"text": m["text"], "score": score, "sources": m["sources"]})
        accepted.append(set(_tokenize(m["text"])))

    scored.sort(key=lambda c: c["score"], reverse=True)
    scored = scored[:count]

    # 聚类:嵌入 + K-Means。失败吞掉(聚类是锦上添花,不致 endpoint 5xx)
    clusters_meta: list[dict] = []
    if include_clusters and len(scored) >= 4:
        try:
            from .clustering import cluster_candidates
            texts = [c["text"] for c in scored]
            labels, clusters_meta = await cluster_candidates(texts)
            for c, lab in zip(scored, labels):
                c["cluster_id"] = int(lab)
        except Exception:  # noqa: BLE001 — 模型加载/推理失败都不让 endpoint 5xx
            clusters_meta = []
            for c in scored:
                c["cluster_id"] = 0
    else:
        for c in scored:
            c["cluster_id"] = 0

    return scored, clusters_meta
