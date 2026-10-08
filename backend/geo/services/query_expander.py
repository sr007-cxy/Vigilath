"""4-dimension scene expansion -- feed one seed into 4 LLM templates in parallel, yielding 4 long-tail kinds: search / QA / intent / brand.

Since 2026-05-28 this takes over expansion output for `/expand-queries`. The old path called telemetry-service for 1 set;
the new path makes LLM calls on our side, 4 dimensions in parallel, each with its own prompt template.

Templates reverse-engineered from Xunling measurements:
  - search:  category/product search intent -- XX厂家/XX供应商/推荐XX ("XX manufacturer / XX supplier / recommend XX"; pure keywords)
  - qa:      Q&A / recommendation / comparison -- 哪家XX好/XX怎么选 ("which XX is best / how to choose XX"; with question words)
  - intent:  user intent queries -- 如何选XX/XX攻略 ("how to choose XX / XX guide"; with intent words)
  - brand:   brand evaluation -- {target}怎么样/详细介绍/客户评价 ("how is {target} / details / customer reviews"; subject must be the brand name)

ENV (reuses content_generator's 2-way fallback):
  DEEPSEEK_API_KEY      preferred, direct DeepSeek
  OPENROUTER_API_KEY    fallback, DeepSeek via OpenRouter
  GEO_EXPANSION_TIMEOUT per-LLM-request timeout (seconds), default 90
  GEO_EXPANSION_THINKING whether to enable thinking on the direct deepseek channel, default off (see comment in expand_one_scene)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from geo.models.ai_telemetry import ALL_SCENES, DEFAULT_SCENE, SceneType, featured_products, find_product
from geo.services.llm_usage import track

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_EXPANSION_TIMEOUT", "90"))
# 2026-09-22 -- production DEEPSEEK_MODEL is a reasoning model (deepseek-v4-flash); thinking tokens count toward the output budget.
# Per scene at most 50 items x 60 chars (safety cap) ~= 3000 tokens of JSON; 4000 is enough for the body. If unset the gateway uses
# the model default, and long thinking truncates the body with finish_reason=length so _parse_json_loose gets nothing.
MAX_TOKENS = int(os.environ.get("GEO_EXPANSION_MAX_TOKENS", "4000"))
_THINKING_ON = os.environ.get("GEO_EXPANSION_THINKING", "").strip().lower() in ("1", "true", "on", "yes")


# ─────────── Entity type -> expansion-word style directive (overrides the template's default manufacturing wording) ───────────
# The profile's entity_type decides which direction expansion words go. Empty / other -> nothing injected, template runs as-is.
ENTITY_TYPE_HINTS: dict[str, str] = {
    "service_tool": (
        "主体类型:【服务业】(SaaS / 软件 / 平台 / 在线服务 / 咨询)。"
        "把品类用「工具 / 软件 / 平台 / 系统 / 方案 / 服务」这类说法自然表达;"
        "别用「厂家 / 供应商 / 生产商 / 制造 / 批发 / OEM / 工厂」这类制造业词。"
    ),
    "manufacturer": (
        "主体类型:【制造业】(工厂 / 代工 / 设备 / 零部件)。"
        "贴合采购方找供应商的问法,可用「厂家 / 供应商 / 生产厂 / 设备 / 选型」这类说法。"
    ),
    "brand_owner": (
        "主体类型:【品牌方 / 零售商】(消费品 / 渠道 / 代理)。"
        "用消费者口吻:「哪个牌子好 / 什么品牌 / 正品 / 哪里买」;少用上游制造词。"
    ),
    "other": "",
}


# ─────────────────── 4 prompt templates ──────────────────

# 2026-05-28 -- profile injection strategy (based on Xunling AI distillation measurements):
#   search:  weak dependency -- only industry / service_geo (pure category SEO words)
#   qa:      weak dependency -- only industry / service_geo
#   intent:  medium dependency -- industry / service_geo / case_stories (case-tracing queries)
#   brand:   strong dependency -- target / aliases / industry / service_geo / case_stories
#                    / core_credentials / brand_diff_tags / core_service_overview
# 2026-09-22 -- product list injection (profile_json.products, see models.BrandProduct):
#   {own_products_block}  injected in all four scenes: tells the LLM these names are this brand's products, not competitors or categories
#   {product_mode_hint}   injected only when the seed hits a product (find_product): search / qa / intent build category words from the product's
#                         category and must never include the product name; the brand scene switches to product-evaluation phrasing
#   Both placeholders are empty strings when not applicable; topics without products render byte-identical to before (verify_brand_products.py snapshot-checks this)
SCENE_PROMPTS: dict[SceneType, str] = {
    "search": """你在为「AI 搜索可见性监测」生成提示词。这些词会拿去问 AI 搜索引擎(ChatGPT / 豆包 /
DeepSeek / 文心 等),用来看 AI 的回答里会不会推荐 / 列举某个品牌或公司 —— 用户随后优化内容,
争取让自己的品牌出现在这些回答里。所以要生成【会让 AI 给出品牌 / 公司 / 产品推荐清单】的词。

品类:「{seed}」(用它代表的【品类】来造词;若原文太长太书面,请换成更自然、口语、简短的品类说法,
不要把这串字整块照搬)
{own_products_block}{product_mode_hint}{entity_hint}
行业:{industry};地域:{service_geo}

扩展 {count} 条,**全部按「修饰词 + 品类」的推荐型短语**(下面是句式骨架,{{品类}} 用本主题的品类替换):
- 求推荐型:推荐{{品类}} / 求推荐{{品类}} / 推荐一下{{品类}} / {{品类}}推荐 / {{品类}}排名 / {{品类}}排行
- 夸赞型(每条换一个形容词,别只用「好」):靠谱的{{品类}} / 专业的{{品类}} / 有实力的{{品类}} /
  知名的{{品类}} / 口碑好的{{品类}} / 高性价比的{{品类}} / 服务好的{{品类}} / 资质齐全的{{品类}} /
  正规的{{品类}} / 比较好的{{品类}} / 诚信的{{品类}}
- 同义品类变体:把「{{品类}}」替换成贴切的同义说法(公司 / 企业 / 机构 / 平台 / 品牌 / 工具 / 软件 —— 按主体类型挑)

要求:
1. 每条 6~18 字,纯短语、不带问号;主体是品类,不是某个具体品牌名。
2. 【严禁】功能 / 价格 / 套餐 / 登录 / 注册 / 下载 / 官网 / 月费 / 年费 / API / 集成 / 试用 / 教程 / 操作指南
   这类功能点 / 导航 / 交易后缀 —— 问 AI 这些根本不会推荐品牌,属于无效词。
3. 【严禁】把品类原文当名词整块照搬再机械加后缀(反例:「推荐{seed}功能」「{seed}下载」)。
4. 不重复;若 service_geo 非空,可少量加地域(如 {service_geo}靠谱的{{品类}})。

只输出 JSON 对象,字段名 "queries",值是字符串数组,如:
{{"queries": ["推荐XX", "靠谱的XX", "口碑好的XX", "..."]}}
不要前后包 ```json``` 围栏,不要任何额外说明文字。""",

    "qa": """你在为「AI 搜索可见性监测」生成提示词(用途同 search 场景:让 AI 回答里给出品牌 / 产品推荐,
供用户优化后争取被推荐)。本场景专出【对比 / 选型 / 求推荐】类口语问句。

品类:「{seed}」(用自然、简短的品类说法,别整块照搬原文)
{own_products_block}{product_mode_hint}{entity_hint}
行业:{industry};地域:{service_geo}

扩展 {count} 条问句(下面是句式骨架,{{品类}} 用本主题的品类替换):
- 哪家{{品类}}好 / 哪个{{品类}}靠谱 / {{品类}}怎么选 / {{品类}}哪家专业 / 选哪个{{品类}}好
- {{品类}}哪家性价比高 / 求推荐靠谱的{{品类}} / {{品类}}哪家口碑好 / {{品类}}哪家有实力

要求:
1. 每条 8~22 字,真实口语问句,带「哪个 / 哪家 / 怎么选 / 推荐 / 靠谱 / 好不好」等词。
2. 每条都要能引出品牌 / 公司推荐;不要纯关键词(归 search 场景)。
3. 【严禁】功能 / 价格 / 登录 / 下载 / 官网 / 月费 / 试用 / 教程 这类后缀;严禁品类原文整块照搬加后缀。
4. 不重复。

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",

    "intent": """你在为「AI 搜索可见性监测」生成提示词(目的:让 AI 的回答里推荐到品牌 / 服务商 / 解决方案,
供用户优化后争取被推荐)。本场景出【带真实使用场景的第一人称口语问句】,像用户在跟 AI 聊天
描述自己的需求并求推荐 / 求方案。

品类:「{seed}」(用自然说法,别整块照搬原文)
{own_products_block}{product_mode_hint}{entity_hint}
行业:{industry};地域:{service_geo}
真实案例 / 业务经历(当作场景背景融进问句,**至少 30% 的 query 围绕这些展开**):
{profile_cases_block}

用户定位:处于【泛需求阶段】— 刚意识到要找这类东西,随手跟 AI 说一句话求推荐;
不是已经列好条件清单的采购专家。

扩展 {count} 条。多样性来自【不同的单一切入点】,每条只取下面一个维度:
- 要解决的事(为什么需要这个品类)
- 使用场景 / 地点 / 人群
- 一个在意点(怕不靠谱 / 想要省钱的 / 要有真实案例的…)
- 上面列的真实案例场景

要求:
1. 每条 10~25 字,第一人称口语,像真实用户随手打出来的一句话。
2. 【严禁】一句话堆叠多重条件(身份+场景+问题+限制+目标);每条最多嵌 1 个具体场景或 1 个约束,其余留白。
3. 开头句式至少 5 种(想… / 要… / 有没有… / 求推荐… / 找一个… / 谁知道…),严禁超过 20% 的 query 用同一句式开头。
4. 【严禁】机械枚举行业(在X行业 / 在Y领域 + 同一句式);只有当某行业确实是「{seed}」的典型客户时才可出现,且行业开头的 query 不超过 15%。
5. 场景必须是「{seed}」的真实使用方式;想象不出真实的人会这么问的,不要输出。
6. 句中或结尾要有"求推荐 / 帮我推荐 / 用什么好"的意味,这样 AI 才会给出品牌 / 服务商。
7. 案例型问句必须用上面列的真实案例,不要凭空编造;案例为空就不出案例型。
8. 【严禁】功能 / 价格 / 登录 / 下载 / 月费 这类后缀;不重复。

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",

    "brand": """你是品牌评估词扩展专家。
针对品牌名「{target}」(seed 是「{seed}」,作为业务/品类上下文)扩展 {count} 个**品牌评估**长尾问句。
{own_products_block}{product_mode_hint}{entity_hint}
品牌全称:{target}
品牌别名(可用):{aliases}
行业:{industry};地域:{service_geo}
品牌业务概述:{core_service_overview}
品牌差异化标签(直接引用 1-2 条做 query):{brand_diff_tags_block}
品牌核心资质 / 背书(可作为评估问句的依据):{core_credentials_block}
品牌真实案例(可作为业务线 / 历史成绩带入):{profile_cases_block}

扩展模式参考:
- {target}怎么样 / {target}详细介绍 / {target}基本信息
- {target}公司概况 / {target}客户评价如何 / {target}性价比怎么样
- {target}实力如何 / {target}靠不靠谱 / {target}口碑
- {service_geo}{target}(若 service_geo 非空,加地域前缀)
- **业务线 / 案例型**:{target}做过{{案例名}}吗 / {target}在{{业务领域}}方面有什么经验
- **资质型**:{target}有{{资质}}吗 / {target}的{{差异化标签}}怎么样

要求:
1. 每条 30~50 字,主语必须是品牌名({target} 或别名),不是品类词
2. 涵盖正反两面(评价 / 口碑 / 性价比 / 客户 / 实力 / 资质)
3. **至少 1/3 的 query 必须用上面列的真实案例名 / 资质名 / 差异化标签**,不要凭空编造
4. 不要重复

只输出 JSON 对象:{{"queries": ["..."]}};不要 ```json``` 围栏。""",
}


def _format_list_block(items: list[str] | None, *, max_items: int = 10,
                        per_item_max: int = 200) -> str:
    """Render a list as multi-line bullets in the prompt; empty list -> '(无)' ("(none)")."""
    items = [str(x or "").strip() for x in (items or []) if str(x or "").strip()]
    items = items[:max_items]
    if not items:
        return "(无 — 用户未在画像里填写,本次不要凭空编造)"
    return "\n".join(f"  - {s[:per_item_max]}" for s in items)


def _own_products_block(products: list[dict] | None, *, target: str, max_items: int = 12) -> str:
    """The "brand's own products" section; no products -> empty string. When non-empty it ends with a newline (placeholder shares a line with the next one in the template)."""
    ps = featured_products([p for p in (products or []) if isinstance(p, dict) and str(p.get("name") or "").strip()])
    if not ps:
        return ""
    lines: list[str] = []
    for p in ps[:max_items]:
        name = str(p.get("name") or "").strip()[:64]
        bits: list[str] = []
        cat = str(p.get("category") or "").strip()
        if cat:
            bits.append(f"品类:{cat[:64]}")
        al = [str(a).strip() for a in (p.get("aliases") or []) if str(a).strip()] if isinstance(p.get("aliases"), list) else []
        if al:
            bits.append("别名:" + "/".join(al[:5]))
        if p.get("is_featured"):
            bits.append("主推")
        lines.append(f"  - {name}" + (f"({';'.join(bits)})" if bits else ""))
    return (
        f"品牌「{target}」自有产品 / 型号(下面这些是本品牌自己的产品,**不是竞品、不是品类**;"
        "不要拿它们当品类造词,不要把它们当成别的品牌):\n" + "\n".join(lines) + "\n"
    )


def _product_mode_hint(scene: SceneType, matched: dict | None, *, seed: str, target: str) -> str:
    """Scene directive when the seed hits a product; no hit -> empty string. When non-empty it ends with a newline."""
    if not matched:
        return ""
    name = str(matched.get("name") or "").strip()[:64]
    cat = str(matched.get("category") or "").strip()[:64]
    sp = [str(x).strip() for x in (matched.get("selling_points") or []) if str(x).strip()] if isinstance(matched.get("selling_points"), list) else []
    tu = [str(x).strip() for x in (matched.get("target_users") or []) if str(x).strip()] if isinstance(matched.get("target_users"), list) else []
    if scene in ("search", "qa", "intent"):
        # 2026-09-22 -- this product may not rank for broad category words (e.g. "gamepad recommendations"); use its selling points / audience to build
        # niche category words (e.g. "Hall-trigger gamepad recommendations", "gamepads for console players") where it's the answer that should appear.
        facets = ""
        if sp or tu:
            facets = "该产品的差异点(用来造【细分品类词】:把卖点 / 人群当修饰词放在品类前,如「{卖点}的{品类}推荐」"\
                     "「适合{人群}的{品类}哪款好」;**至少一半**的词要带其中一个维度,其余出泛品类词):\n"
            if sp:
                facets += "  卖点 / 特性:" + "、".join(sp[:6])[:300] + "\n"
            if tu:
                facets += "  目标人群 / 场景:" + "、".join(tu[:6])[:200] + "\n"
        if cat:
            return (
                f"【产品模式】种子「{seed}」是{target}的产品「{name}」,它的品类是「{cat}」。"
                f"请围绕品类「{cat}」造词(上面的「品类」已按此替换),**严禁**在任何一条里出现「{name}」"
                "或其别名 —— 这类词只会让 AI 复述该产品,测不出品牌在品类推荐里的位置。\n" + facets
            )
        return (
            f"【产品模式】种子「{seed}」是{target}的产品「{name}」,不是品类。请先推断它所属的通用品类"
            f"(如「游戏手柄」「电动牙刷」),再围绕该品类造词;**严禁**在任何一条里出现「{name}」或其别名。\n" + facets
        )
    if scene == "brand":
        extra = ""
        if sp:
            extra += "  产品卖点(可各取 1 条做评估角度):" + "、".join(sp[:6])[:300] + "\n"
        if tu:
            extra += "  目标人群 / 场景:" + "、".join(tu[:6])[:200] + "\n"
        return (
            f"【产品模式】种子「{seed}」是{target}的产品「{name}」" + (f"(品类:{cat})" if cat else "") +
            "。本次至少一半的问句主语用该产品(其余仍用品牌名;下方「主语必须是品牌名」的要求里,产品名视同品牌名),句式参考:\n"
            f"  {target}{name}怎么样 / {name}值得买吗 / {name}适合谁 / {name}和上一代有什么区别 / "
            f"{name}和同价位竞品比怎么样 / {name}有什么缺点 / {name}用户评价\n" + extra
        )
    return ""


def render_prompt(
    scene: SceneType,
    *,
    seed: str,
    count: int,
    target: str = "",
    aliases: list[str] | None = None,
    industry: str = "",
    service_geo: str = "",
    entity_type: str = "",
    profile_cases: list[str] | None = None,
    core_credentials: list[str] | None = None,
    brand_diff_tags: list[str] | None = None,
    core_service_overview: str = "",
    products: list[dict] | None = None,
) -> str:
    """Render the prompt for the scene (used directly as user input).

    2026-05-28 -- differentiated injection by profile dependency:
      - search / qa: only seed + industry + service_geo
      - intent:      additionally injects profile_cases (case tracing)
      - brand:       injects all profile fields (target/aliases/cases/credentials/tags/overview)
    2026-09-22 -- products (dict list from profile_json.products) injected in all four scenes; when the seed hits a
      product it enters "product mode": search / qa / intent use that product's category (see the SCENE_PROMPTS header comment).
    """
    tpl = SCENE_PROMPTS.get(scene) or SCENE_PROMPTS["search"]
    matched = find_product(products or [], seed)
    # Common fields
    kw: dict[str, str] = {
        "seed": seed,
        "count": str(int(count)),
        "target": (target or seed),
        "aliases": "、".join((aliases or [])[:5]) or "(无)",
        "industry": industry or "(未指定)",
        "service_geo": service_geo or "",
        # Entity-type directive -- empty / other / unknown all degrade to empty string (template runs as-is)
        "entity_hint": ENTITY_TYPE_HINTS.get((entity_type or "").strip(), ""),
        "own_products_block": _own_products_block(products, target=target or seed),
        "product_mode_hint": _product_mode_hint(scene, matched, seed=seed, target=target or seed),
    }
    if matched and scene in ("search", "qa", "intent"):
        # Product mode: category words built from the product's category; if category is empty keep the seed (hint asks the LLM to infer it)
        cat = str(matched.get("category") or "").strip()
        if cat:
            kw["seed"] = cat
    # Inject extra fields per scene
    if scene == "intent":
        kw["profile_cases_block"] = _format_list_block(profile_cases)
    elif scene == "brand":
        kw["profile_cases_block"] = _format_list_block(profile_cases)
        kw["core_credentials_block"] = _format_list_block(core_credentials)
        kw["brand_diff_tags_block"] = (
            "、".join([t for t in (brand_diff_tags or []) if (t or "").strip()][:10])
            or "(无)"
        )
        kw["core_service_overview"] = (core_service_overview or "").strip()[:500] or "(无)"
    return tpl.format(**kw)


# ─────────────────── LLM provider selection (same as content_generator) ──────────────────


def _resolve_provider() -> tuple[str | None, str, str]:
    """Pick the LLM provider + get the api key. Returns (provider | None, model_id, api_key)."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, DEEPSEEK_MODEL, ""


# ─────────────────── Single scene call ──────────────────


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Loosely parse LLM output into a dict. Allows ```json``` fences / extra text before or after."""
    s = (text or "").strip()
    # strip code fences
    if s.startswith("```"):
        s = s.lstrip("`")
        # strip leading 'json' line
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in ("", "json"):
                s = rest
        if "```" in s:
            s = s.split("```", 1)[0]
    s = s.strip()
    # Attempt 1: parse the whole thing
    try:
        out = json.loads(s)
        if isinstance(out, dict):
            return out
    except Exception:  # noqa: BLE001
        pass
    # Attempt 2: extract {...}
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
    entity_type: str = "",
    # 2026-05-28 -- profile injection (used by intent / brand scenes, ignored by others)
    profile_cases: list[str] | None = None,
    core_credentials: list[str] | None = None,
    brand_diff_tags: list[str] | None = None,
    core_service_overview: str = "",
    products: list[dict] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Call the LLM once for a single scene; returns {"queries": [...], "model": "...", "error": "..."}.

    Never raises -- errors go into the returned dict for the upper fan-out to decide how to display.
    """
    provider, model_id, api_key = _resolve_provider()
    if not provider:
        return {"queries": [], "error": "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置"}

    prompt = render_prompt(
        scene,
        seed=seed, count=count, target=target,
        aliases=aliases, industry=industry, service_geo=service_geo,
        entity_type=entity_type,
        profile_cases=profile_cases,
        core_credentials=core_credentials,
        brand_diff_tags=brand_diff_tags,
        core_service_overview=core_service_overview,
        products=products,
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
        "max_tokens": MAX_TOKENS,
    }
    if provider == "deepseek" and not _THINKING_ON:
        # Production DEEPSEEK_MODEL is a reasoning model (deepseek-v4-flash). Query expansion is "generate words by pattern" and needs no reasoning;
        # with thinking on, intent (the most constrained template) writes a long chain of thought first; on 2026-09-22 one production expansion
        # had only 2 of 4 paths return within 90s (27s / 79s); the intent path hit the timeout with no hint on frontend or backend.
        # Same treatment as profile_extractor / full_diagnosis_queries: turn it off (the neolink gateway honors this field;
        # the OpenRouter branch doesn't send it -- models vary in how they treat it).
        body["thinking"] = {"type": "disabled"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        # Accounting: the async path can't use post_and_record (that's the sync requests version), so wrap with track.
        # asyncio copies contextvars natively, so no extra context passing is needed here.
        with track(f"query_expand.{scene}" if scene else "query_expand",
                   provider, model_id, endpoint=url) as _t:
            r = await client.post(url, headers=headers, json=body)
            _t.http_status = r.status_code
            if r.status_code == 200:
                _t.usage = r.json()
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
        # Clean: drop empty / dedupe / truncate long
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
        # Timeouts / connection failures used to go only into the returned dict without logging, so debugging "intent words missing" in production was guesswork -- log one line.
        log.warning("expand_one_scene http error scene=%s seed=%s model=%s timeout=%ss: %r",
                    scene, seed, model_id, DEFAULT_TIMEOUT, e)
        return {"queries": [], "model": model_id, "error": f"HTTP error: {e}"}
    except Exception as e:  # noqa: BLE001
        log.warning("expand_one_scene crash scene=%s seed=%s: %s", scene, seed, e)
        return {"queries": [], "model": model_id, "error": str(e)[:300]}
    finally:
        if owns_client:
            await client.aclose()


# ─────────────────── Heuristic classification (tag historical queries with a scene) ──────────────────

# Order-sensitive: **intent must match before QA**, so "怎么用" ("how to use") isn't classified as qa (怎么 looks like QA but is really intent).
# Keywords try to cover common Chinese search behavior exhaustively: product SEO queries / Q&A / how-to tutorials / brand evaluation.
_INTENT_MARKERS: tuple[str, ...] = (
    # explicit how-to words
    "如何", "怎么用", "怎么做", "怎么选", "怎么挑", "怎么入门", "怎么找",
    "怎么获取", "怎么操作", "怎么学", "怎么实现",
    # guide-type
    "攻略", "教程", "指南", "操作步骤", "使用方法", "什么时候用",
    "用法", "玩法", "做法",
    # choosing / getting started / process
    "挑选", "选购", "选型", "入门", "入手", "上手", "实操",
    "学会", "自学", "新手", "小白", "速成",
    # steps / process
    "步骤", "流程", "操作",
)
_QA_MARKERS: tuple[str, ...] = (
    # recommendation / comparison / choice
    "哪家", "哪个", "哪种", "哪款", "哪些", "选哪",
    "推荐", "排行", "排名", "TOP", "top",
    "对比", "比较", "vs", "VS", "vS", "Vs",
    "区别", "差异", "异同", "不同点",
    # reviews / reputation / pros and cons
    "怎么样", "怎样",
    "好不好", "好用吗", "靠谱吗", "靠不靠谱", "值不值", "值得吗",
    "可以信任", "可信", "可靠吗", "评价", "评测", "评估", "测评",
    "优缺点", "优劣", "利弊", "好处", "缺点", "优势", "劣势",
    # definition / "who" questions
    "是什么", "是啥", "啥是", "啥意思", "什么意思",
    "是谁", "找谁", "属于谁", "谁是", "谁来", "谁会", "谁能",
    # value-for-money / price questions
    "性价比怎么", "性价比好", "性价比高", "划算吗",
    # sentence-final question words (last, lowest priority)
    # Note: covers both ASCII half-width and Unicode full-width question marks, so Chinese queries ending in "？" aren't missed
    "?", "？", "吗", "呢",
)


# Strong comparison words: only when these appear **together with** a brand/competitor name is it a competitor-comparison word.
# Deliberately excludes standalone generic words like 「对比」「比较」 ("compare") -- they're already in _QA_MARKERS;
# promoting them to the compare layer would reclassify lots of existing QA words, and compare is in the exclusion set of the exposure-rate metric definition,
# so once reclassified, the report center's exposure rate / TOP1 / TOP3 denominators break immediately.
_COMPARE_MARKERS = ("哪个好", "哪个更好", "选哪个", "对比", "相比", "vs", "VS", "区别", "谁更")

# Scenario words: tied to a specific audience / occasion / budget. A generic word list has inherently limited recall;
# here we'd rather misclassify as intent than false-positive -- scenario words mainly come pre-tagged from expansion templates and diagnosis word generation.
_SCENARIO_MARKERS = ("适合", "人群", "产后", "学生", "上班族", "老人", "新手", "初学",
                     "预算", "小户型", "出差", "加班", "久坐", "第一次", "长期用",
                     "家用", "商用", "孕妇", "儿童", "小白")


def classify_query(
    text: str,
    *,
    target: str = "",
    aliases: list[str] | None = None,
    competitors: list[str] | None = None,
    product_terms: list[str] | None = None,
) -> SceneType:
    """Heuristically assign a query to one scene (for tagging historical queries).

    product_terms (2026-09-22) = product names / aliases from the profile (models.profile_product_terms),
    brand words just like target / aliases: 「<brand-e>手柄怎么样」 ("how is the <brand-e> gamepad") is a brand word, not a QA word.

    Priority (high to low):
      1. strong comparison word + brand/competitor name -> compare (very strict, see _COMPARE_MARKERS comment)
      2. text literally contains target / alias -> brand
      3. contains scenario words (适合 / 人群 / 预算 / 新手 ... ("suitable / audience / budget / beginner")) -> scenario
      4. contains intent words (如何 / 攻略 / 教程 / 怎么选 / 怎么用 ... ("how / guide / tutorial / how to choose / how to use")) -> intent
      5. contains QA words (哪家 / 怎么样 / 对比 / 是什么 / 评价 / 吗 ... ("which / how is / compare / what is / review / ?")) -> qa
      6. everything else (pure category / keywords) -> search

    This classification is literal only, no LLM -- fast and deterministic, suited for one-off bulk backfill of queries.
    Ambiguous cases (怎么用 ("how to use") looks like both intent and qa) are cut by priority, accepting a few misclassifications;
    users can rerun (after changing target / aliases) or edit single items manually.

    ⚠️ Rerunning this classifier on existing topics changes the word-class distribution and thus the report center's exposure-rate denominator.
    See the warning at the top of scripts/backfill_query_scene.py.
    """
    s = (text or "").strip()
    if not s:
        return DEFAULT_SCENE

    # Brand word surface (only matched when length >= 2, so short aliases like "a" "b" don't match every query)
    brand_terms: list[str] = []
    if target and len(target.strip()) >= 2:
        brand_terms.append(target.strip())
    for a in list(aliases or []) + list(product_terms or []):
        a = (a or "").strip()
        if a and len(a) >= 2:
            brand_terms.append(a)
    comp_terms = [c.strip() for c in (competitors or []) if c and len(c.strip()) >= 2]

    has_brand = any(t in s for t in brand_terms)
    has_comp = any(c in s for c in comp_terms)

    # 1. compare -- requires a "strong comparison word" AND "some brand name"; pure category comparison words stay qa
    if (has_brand or has_comp) and any(m in s for m in _COMPARE_MARKERS):
        return "compare"

    # 2. brand
    if has_brand:
        return "brand"

    # 3. scenario
    for marker in _SCENARIO_MARKERS:
        if marker in s:
            return "scenario"

    # 4. intent
    for marker in _INTENT_MARKERS:
        if marker in s:
            return "intent"

    # 5. qa
    for marker in _QA_MARKERS:
        if marker in s:
            return "qa"

    # 6. default search
    return "search"


def route_brand_words(scene_results: dict[str, dict], brand_terms: list[str]) -> dict[str, dict]:
    """Post-expansion fallback: move items in search / qa / intent whose text contains brand words (target / alias / product name) to brand.

    Templates already say "product names are strictly forbidden", but the LLM doesn't always comply; this keeps category scenes clean -- otherwise
    「<brand-e>手柄哪家好」 ("which <brand-e> gamepad is best") enters selected-queries as qa, counts in the exposure denominator, and treats our own product as a competitor.
    Modifies in place and returns the same dict; creates the brand bucket if missing. Dedupes by text (items already in brand aren't appended).
    """
    terms = [t.strip() for t in (brand_terms or []) if t and len(t.strip()) >= 2]
    if not terms:
        return scene_results
    lowered = [t.casefold() for t in terms]
    brand = scene_results.setdefault("brand", {"queries": [], "model": "", "error": ""})
    brand_qs: list[str] = list(brand.get("queries") or [])
    seen = {q.casefold() for q in brand_qs}
    moved = 0
    for scene in ("search", "qa", "intent"):
        info = scene_results.get(scene)
        if not info:
            continue
        keep: list[str] = []
        for q in info.get("queries") or []:
            ql = (q or "").casefold()
            if any(t in ql for t in lowered):
                if ql not in seen:
                    seen.add(ql)
                    brand_qs.append(q)
                moved += 1
            else:
                keep.append(q)
        info["queries"] = keep
    if moved:
        brand["queries"] = brand_qs
        log.info("route_brand_words: %d category-scene candidates contain brand/product words, moved to brand", moved)
    return scene_results


__all__ = [
    "ALL_SCENES", "SceneType", "SCENE_PROMPTS",
    "render_prompt", "expand_one_scene", "DEFAULT_TIMEOUT",
    "classify_query", "route_brand_words",
]
