"""Prompt planning for one-click diagnosis -- the model produces terms, templates as fallback.

Why this layer exists: the original `_build_term_queries` was pure string templating with a fixed quota of 15,
templates like `f"{t}推荐"` / `f"预算有限选什么{t}"` ("{t} recommendations" / "what {t} to choose on a tight budget"). It assumed the user's industry term was a **short category word**;
real customers filled in **whole questions** per the consultant's plan, and templating produced broken sentences -- in one customer report
11 of 15 were ungrammatical: "预算有限选什么中国领先的第三方IDC服务商有哪些" ("on a tight budget what to choose which are China's leading third-party IDC providers").

The stopgap fix (`_is_sentence`) only solved "no broken sentences", not the fact that templates cannot **understand** the input.
The most valuable things in consultants' handwritten seed prompt plans are exactly four
that templates cannot do, now handed to the model:

  1. Split "three questions in one" -- with three questions in one line, the model only seriously answers the first
  2. **Rework knowledge questions for entity recall** -- for terms asking about principles (why/what is/what trend) the model has no reason
     to name vendors in the answer, so exposure rate is naturally near 0 while still taking up the denominator of the three core metrics, watering ourselves down.
     They must be changed to "which companies / who is doing / representative vendors / how to choose" phrasing
  3. Ensure there are competitor-comparison terms
  4. Term-type ratio -- keep enough on the category side, otherwise the denominator of the three core metrics is too small

When the model is unavailable / times out / outputs non-compliant results, fall back seamlessly to templates and the report still ships -- the template path is
`build_term_queries_template`, kept together with `_is_sentence`; do not delete.

**scene never trusts the model's labels** (see `_coerce_scene`): brand/compare are excluded from the
exposure rate/TOP1/TOP3 metric definition, so a careless mislabel by the model would tamper with the main metrics' denominator. Terms containing the brand name must go into the exclusion set,
those without must not; the model's freedom is only among the four types search/qa/intent/scenario.

ENV:
    GEO_DIAG_QUERY_LLM      0/false/off forces templates, on by default
    GEO_DIAG_QUERY_MODEL    override model, defaults to DEEPSEEK_MODEL (deepseek-v4-flash in production)
    GEO_DIAG_QUERY_TIMEOUT  per-call timeout seconds, default 120 (a reasoning model with thinking takes 60-120s for this JSON)
    GEO_DIAG_QUERY_THINKING 1/true/on turns on thinking for the reasoning model when producing terms, off by default. With it off the same
                            15-item JSON returns in a dozen seconds; the rules for splitting/rewriting/recall phrasing are all in
                            the prompt, and validation + template fallback still guard, without relying on model thinking
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from geo.api.ai_telemetry_reports import EXPOSURE_ONLY_SCENES
from geo.models.ai_telemetry import ALL_SCENES
from geo.services import llm_gateway
from geo.utils.full_diagnosis_log import full_diag_log_event

log = logging.getLogger(__name__)

# prompt / validation metric definition version -- goes into the cache key; **changing the prompt requires a bump**,
# otherwise old term packages are returned as-is within 24h and the change has no effect
QUERY_PLAN_SPEC = "v1"
# Single source of the 15-item quota (15 x 3-5 engines = 45-75 rows, already six to nine minutes)
TARGET_QUERY_COUNT = 15
# Minimum count on the category side (non brand/compare) -- below it the whole batch degrades
CATEGORY_MIN = 5
BRAND_MAX = 3
COMPARE_MAX = 3
# Valid count below this degrades the whole batch
TOTAL_MIN = 8

_DISABLED = {"0", "false", "off", "no"}

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get(
    "OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")


def _llm_enabled() -> bool:
    return os.environ.get("GEO_DIAG_QUERY_LLM", "1").strip().lower() not in _DISABLED


def _model() -> str:
    return os.environ.get("GEO_DIAG_QUERY_MODEL", "").strip() or DEEPSEEK_MODEL


def _thinking() -> str:
    on = os.environ.get("GEO_DIAG_QUERY_THINKING", "").strip().lower() in ("1", "true", "on", "yes")
    return "enabled" if on else "disabled"


def _timeout() -> int:
    # Measured: v4-flash takes 60-120s to produce this 2350-char JSON (reasoning overhead); 60s times out consistently.
    # It runs in parallel with Wave 1's four site-side items and adds no total time, so 120 is safe.
    try:
        return int(os.environ.get("GEO_DIAG_QUERY_TIMEOUT", "120"))
    except ValueError:
        return 120


@dataclass(frozen=True)
class QueryPlan:
    """The product of one prompt planning run. source decides how the source note in the report is worded."""
    items: list[dict]            # [{"text","scene","kind","origin"}]
    source: str                  # "llm" | "template"
    model: str = ""              # Actual model id used; empty on the template path
    fallback_reason: str = ""    # Degradation/patch reason; empty on a clean LLM success
    dropped: list[dict] = field(default_factory=list)  # [{"text","reason"}]


# --------------- Template path (the LLM's only fallback, do not delete) ---------------

# Criterion for a complete sentence: user terms with these markers are already a valid prompt and must not be templated
_SENTENCE_MARKS = ("?", "？", ",", "，", "有哪些", "哪些", "哪个", "哪家", "怎么样", "怎样",
                   "如何", "怎么", "推荐", "排第几", "是什么", "值得", "选哪", "谁在", "能做")


def _is_sentence(term: str) -> bool:
    """Whether the user entered a "complete question" or a "short category word".

    Templates are all designed for short words (f"{t}推荐" / f"预算有限选什么{t}"); applied to whole sentences they produce
    broken sentences like "预算有限选什么中国领先的第三方IDC服务商有哪些" -- report 48 failed this way:
    the user entered 5 whole questions per the seed prompt plan, and 11 of 15 prompts were ungrammatical.
    """
    t = (term or "").strip()
    if not t:
        return False
    return any(m in t for m in _SENTENCE_MARKS) or len(t) >= 14


def _brand_names(brand: str, brand_terms: list[str]) -> list[str]:
    """Brand name set used for term-type judgment = the brand name itself + user-entered brand terms.

    ★ Fixed 2026-09-23: it used to be `brand_terms or [brand]` -- **if brand terms were entered, the brand name itself was dropped**.
    Report #138's brand name was "<brand-i>" (Fortune) and the brand terms entered were "<brand-j>、鲁花" (Arawana, Luhua; the user entered them as competitors),
    so brands=[Arawana, Luhua], and "<brand-i>食用油怎么样" ("how is Fortune cooking oil") did not contain either -> judged search instead of
    brand -> giveaway questions naming the brand among the four got mixed into the category denominator, inflating both exposure rate and TOP1
    (TOP1 23.1%, only 6.7% after removing them).
    The brand name should always count, whether or not the user entered extra brand terms.
    """
    out: list[str] = []
    for x in [brand, *(brand_terms or [])]:
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    return out


def scene_for_text(text: str, brands: list[str], comps: Optional[list[str]] = None) -> str:
    """Judge term type by text content -- the template path and LLM path **share this one authoritative criterion**.

    Each path judging separately would split the denominator metric definition (the same term judged brand by template, search by the model).
    Category questions without the brand name must be judged search: brand/compare are excluded from exposure rate/TOP1/TOP3
    (asking about the brand always ranks it first, so rank carries no information); misjudging would remove from the main metrics' denominator.
    """
    hit_brand = any(b and b in text for b in brands)
    hit_comp = any(c and c in text for c in (comps or []))
    if hit_comp:
        return "compare"
    if not hit_brand:
        return "search"
    return "compare" if any(k in text for k in ("对比", "相比", "vs", "VS", "哪个好", "比怎么样")) \
        else "brand"


def build_term_queries_template(brand: str, industry_terms: list[str],
                                brand_terms: list[str],
                                competitors: Optional[list[str]] = None) -> list[dict[str, str]]:
    """User terms -> audit queries with term-type annotations (template path).

    User terms go two ways: complete sentences are asked as-is (that is what the user wants to ask anyway), only short category words
    go through the template expansion below. The as-is batch goes first -- the 15-item quota prioritizes what users wrote themselves.

    Each of the five seed term types gets a quota (total still 15; 5 engines x 15 = 75 rows already takes six to nine minutes):
      search 3 / Q&A 3 / intent 2 / scenario 3 / brand 2 / competitor comparison 2
    Scenario terms use generic scenario templates (budget/beginner/long-term); the diagnosis form has no brand profile,
    so service scenarios cannot be injected as in topic query expansion.
    Competitor-comparison terms use a no-name phrasing when there is no competitor input -- brand names must never be invented;
    when there is no short category word to build from, better to omit these two than build broken sentences.
    """
    ind_all = [x.strip() for x in industry_terms if x and x.strip()][:5]
    brands = _brand_names(brand, brand_terms)
    comps = [x.strip() for x in (competitors or []) if x and x.strip()]
    ind = [t for t in ind_all if not _is_sentence(t)]

    def _pick(seq: list[str], i: int) -> str:
        return seq[i % len(seq)] if seq else ""

    # User's original sentences take the quota first; template expansion only fills the remaining slots
    plan: list[tuple[str, str]] = [
        (t, scene_for_text(t, brands, comps)) for t in ind_all if _is_sentence(t)
    ]
    for i, t in enumerate(ind[:3]):
        plan.append((f"{t}推荐", "search"))
    for i, t in enumerate(ind[:3]):
        plan.append((f"{t}哪个品牌好", "qa"))
    for i, t in enumerate(ind[:2]):
        plan.append((f"如何挑选{t}", "intent"))
    if ind:
        base = ind[0]
        plan += [(f"预算有限选什么{base}", "scenario"),
                 (f"新手第一次用{base}选哪个", "scenario"),
                 (f"长期用{base}选哪个划算", "scenario")]
    for b in brands[:2]:
        plan.append((f"{b}怎么样", "brand"))
    if comps:
        b0 = brands[0] if brands else brand
        plan.append((f"{b0}和{_pick(comps, 0)}哪个好", "compare"))
        if len(comps) > 1:
            plan.append((f"{b0}对比{_pick(comps, 1)}有什么优势", "compare"))
    elif ind and (brands or brand):
        b0 = brands[0] if brands else brand
        plan += [(f"{b0}和同类{ind[0]}比怎么样", "compare"),
                 (f"{ind[0]}里{b0}排第几", "compare")]

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for text, scene in plan:
        if text and text not in seen:
            seen.add(text)
            out.append({"text": text, "scene": scene})
    return out[:TARGET_QUERY_COUNT]


# --------------- LLM path ---------------

_SYSTEM_PROMPT = """你是 GEO(生成式引擎优化)提示词规划专家。任务:把用户提交的品牌信息与行业词,规划成一份可直接投放的 AI 搜索提示词清单,用来测「用户这样问 AI 时,答案里会不会出现这个品牌」。

## 铁律

1. 每一条提示词都必须存在「答案里应该出现企业/品牌名」的落点。问原理、问定义、问趋势的纯知识题(为什么…、…有什么区别、…的发展趋势)绝不能原样保留 —— 模型没有理由在这类答案里点名任何厂商,这类词的露出率天然为 0,却会占掉核心指标的分母,等于自己给自己掺水。必须做「主体召回改造」:把「为什么/是什么/什么趋势」换成「哪些企业/谁在做/代表厂商/怎么选」句式。
   示例:「为什么AI推动数据中心向高密度和液冷发展」→「高功率密度和液冷数据中心,国内哪些服务商已经落地」;「未来超大规模智算中心的发展趋势是什么」→「超大规模智算中心谁在建,代表企业有哪些」。
2. 一条里塞了多个问句的输入必须拆开。「X是一家什么公司?主要做什么业务?核心竞争力是什么?」要拆成 3 条独立提示词 —— 合成一条问,模型只会认真回答第一问,后两个考察点会被稀释。
3. 只能使用用户给出的品牌名、品牌别名、竞品名。**绝对禁止凭空写出任何用户没有提供的公司名、产品名、机构名**。没有给出竞品时,竞品对比类问题用不点名句式(如「X和同类服务商比怎么样」)。
4. 用户填的行业词如果本身已经是一句通顺的问句,优先原样保留,不要改写、不要套模板。只有当它是短品类词(如「工业电梯」)时才扩写成完整问句。

## 词类(scene)定义

  search   搜索词:纯品类/需求检索,如「第三方IDC服务商推荐」
  qa       问答词:品类侧的疑问句,如「国内AIDC服务商有哪些」
  intent   意图词:带选型决策动作,如「大规模算力基础设施建设找哪家做」
  scenario 场景词:绑定具体场景/能力/人群,如「算电协同做得好的数据中心企业有哪些」
  brand    品牌词:问句里出现本品牌名
  compare  竞品对比词:同时出现本品牌与竞品,或明确的对比句式

**系统口径:brand 与 compare 两类被排除在「整体露出率 / TOP1露出率 / TOP3露出率」三个核心指标的分母之外**(问品牌答品牌恒第一,位次没有信息量),只计入中正率、引用率、声量度。所以品类侧(search/qa/intent/scenario)必须留够,否则核心三指标的分母太小、抖动会盖过真实趋势。

## 文本要求

  · 中文;每条 8-30 字;一条只问一件事;不加序号、引号、Markdown 符号
  · 各条之间不得语义重复
  · 必须通顺 —— 你的输出会原样拿去问 AI 引擎,病句会污染整份报告
"""


def _user_prompt(*, brand: str, brand_terms: list[str], competitors: list[str],
                 industry_terms: list[str], url: str, n: int) -> str:
    comp_line = "、".join(competitors) if competitors \
        else "(未提供,竞品对比词请用不点名句式)"
    terms = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(industry_terms))
    compare_rule = f"compare 2-{COMPARE_MAX} 条" if competitors else "compare 0-2 条(未提供竞品,只能用不点名句式)"
    return f"""品牌名:{brand}
品牌别名/品牌词:{'、'.join(brand_terms) or '(无)'}
竞品:{comp_line}
官网:{url or '(未提供)'}

用户提交的行业词/种子问题(逐条按铁律处理):
{terms}

## 配比要求(共 {n} 条)
  · 品类侧(search + qa + intent + scenario)合计 ≥ 9 条
  · brand 2-{BRAND_MAX} 条
  · {compare_rule}

严格输出下面的 JSON,不要 ``` 围栏,不要多余字段,不要任何解释性文字:
{{
  "queries": [
    {{
      "text":   "提示词正文,8-30字",
      "scene":  "search|qa|intent|scenario|brand|compare",
      "kind":   "user|split|rewrite|expand",
      "origin": "本条改写/拆分自哪句用户输入;kind=user 时填该原句,kind=expand 时填空字符串"
    }}
  ]
}}

kind 取值:
  user    用户原句通顺且合格,原样采用(text 必须与 origin 逐字相同)
  split   从一条多问句里拆出来的
  rewrite 知识题做了主体召回改造,或病句做了通顺化改写
  expand  由短品类词扩写出来的新问题

共 {n} 条。只输出 JSON。"""


def _llm_plan(*, brand: str, brand_terms: list[str], competitors: list[str],
              industry_terms: list[str], url: str) -> dict:
    """Call the model once for the raw term package. Exceptions are re-raised as-is, converted to degradation uniformly by build_query_plan."""
    raw = llm_gateway.chat_json(
        feature="full_diagnosis",
        call_kind="query_plan",
        model=_model(),
        openrouter_model=OPENROUTER_DEEPSEEK_MODEL,
        system=_SYSTEM_PROMPT,
        user=_user_prompt(brand=brand, brand_terms=brand_terms, competitors=competitors,
                          industry_terms=industry_terms, url=url, n=TARGET_QUERY_COUNT),
        temperature=0.3,   # Low temperature: two diagnoses of the same form should not produce jumpy term packages
        timeout=_timeout(),
        # v4-flash is a reasoning model; reasoning eats the budget first before the body gets any. Measured at 2000
        # the gateway returned empty content outright (not truncated, nothing at all, reported as "cannot parse as JSON:"
        # followed by blank), at 8000 occasional truncation. Give a full 16000, and make truncation raise explicitly instead of
        # masquerading as a JSON parse failure -- the two failure causes are handled completely differently.
        max_tokens=16000,
        raise_on_truncated=True,
        json_mode=True,
        retries=1,
        retry_backoff_sec=2.0,
        # 2026-09-14: in a 13-minute report the reasoning models at both ends took nearly half, mostly thinking tokens.
        # Thinking is off by default for term production (env can turn it back on); the 16000 budget above is still needed when thinking is on.
        thinking=_thinking(),
    )
    return {"raw": raw, "model": _model()}


# --------------- Validation ---------------

# "数据" ("data") followed by "中心" ("center") is a category word, not a company name (data centers are everywhere), excluded via negative lookahead --
# "国内数据中心托管服务商哪家口碑好" ("which domestic data center hosting provider has a good reputation") was once wrongly rejected entirely
_ORG_SUFFIX = (r"(科技|集团|股份|数据(?!中心)|网络|通信|软件|电子|医疗|智能"
               r"|银行|证券|公司|实业|控股)")
# Length narrowed to 2-6: real company names are short (<competitor-a>/秦淮数据/润泽科技 -- GDS / Chindata / Range Intelligent); allowing 12 would treat
# half-phrases cut from sentences like "提供GPU算力租赁的数据" ("data providing GPU compute rental") as company names and wrongly reject them
_ORG_CN = re.compile(rf"[一-龥A-Za-z0-9]{{2,6}}{_ORG_SUFFIX}")
# Split into segments by function words and punctuation first, otherwise the regex greedily swallows preceding text:
# "Acme Cloud和<competitor-b>" ("Acme Cloud and <competitor-b>") would match as one "company name", missing the real <competitor-b> (<competitor-b>)
_SPLIT_ORG = re.compile(r"[的和与及或在有是要能会把被为对比从跟同、,，。;;:：?？!！\s]+")
# English brands require an initial capital + lowercase after: all-caps technical acronyms like GPU / IDC / AIDC / API
# are everywhere, and matching on "starts with a capital" would judge them all as brand names
_ORG_EN_STRICT = re.compile(r"[A-Z][a-z]{2,15}")
_LEAD_NUM = re.compile(r"^\s*\d+\s*[.、)\]]\s*")
_JUNK = re.compile(r"[`*#\"'“”「」\r\n]+")


def _clean_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    s = _LEAD_NUM.sub("", text)
    s = _JUNK.sub("", s)
    return " ".join(s.split()).strip()


def _norm(text: str) -> str:
    return "".join(text.split()).lower()


def _hallucinated_org(text: str, allowed: list[str]) -> str:
    """Company names made up out of thin air -- the model's most dangerous failure, adding a nonexistent competitor to the report.

    Only covers "Chinese names with standard company suffixes + English names with an initial capital", deliberately designed as
    **better to miss than to wrongly reject**: a miss may add an ungiven company name to the report (the prompt
    already forbids it explicitly, so the probability is low), while a wrong rejection loses a good term and may push the whole batch into degradation.
    So the suffix table being incomplete ("新网" in "<competitor-c>" (<competitor-c>) is not in it) is intentional; do not enlarge it chasing recall --
    every enlargement wrongly rejects a batch. Returns the matched fragment, empty string means no problem.
    """
    for seg in _SPLIT_ORG.split(text):
        if not seg:
            continue
        for m in list(_ORG_CN.finditer(seg)) + list(_ORG_EN_STRICT.finditer(seg)):
            frag = m.group(0)
            if not any(frag in a or a in frag for a in allowed if a):
                return frag
    return ""


def _coerce_scene(text: str, llm_scene: Any, brands: list[str], comps: list[str]) -> str:
    """Forced scene judgment -- the most critical gate in this module for main-metric risk.

    The model's freedom on scene is limited to swapping among search/qa/intent/scenario (only affects
    by_scene slice display); those containing brand/competitor names must go into the exclusion set, those without must not,
    so however the model labels, it cannot change the denominator of exposure rate/TOP1/TOP3.
    """
    rule = scene_for_text(text, brands, comps)
    if rule in EXPOSURE_ONLY_SCENES:
        return rule                      # Contains brand/competitor name -> the rule decides
    if llm_scene in EXPOSURE_ONLY_SCENES or llm_scene not in ALL_SCENES:
        return rule                      # No brand name but wants to label brand/compare -> rejected
    return llm_scene


def _validate(raw: dict, *, brand: str, brands: list[str], comps: list[str],
              industry_terms: list[str]) -> tuple[list[dict], list[dict]]:
    """Validate item by item, returning (surviving items, dropped items). Raises ValueError on invalid structure for the upper layer to degrade."""
    queries = (raw or {}).get("queries")
    if not isinstance(queries, list):
        raise ValueError(f"queries 不是数组:{str(raw)[:120]}")

    allowed = [x for x in ([brand] + brands + comps + industry_terms) if x]
    items: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()

    for q in queries:
        if not isinstance(q, dict):
            dropped.append({"text": str(q)[:60], "reason": "not_object"})
            continue
        text = _clean_text(q.get("text"))
        if not text:
            dropped.append({"text": str(q.get("text"))[:60], "reason": "empty"})
            continue
        if len(text) < 6 or len(text) > 40:
            # No truncation -- truncating yields broken sentences, exactly what this is meant to fix
            dropped.append({"text": text[:60], "reason": f"length_{len(text)}"})
            continue
        key = _norm(text)
        if key in seen:
            dropped.append({"text": text, "reason": "duplicate"})
            continue
        if "{" in text or "}" in text or "http" in text.lower():
            dropped.append({"text": text, "reason": "template_residue"})
            continue
        frag = _hallucinated_org(text, allowed)
        if frag:
            dropped.append({"text": text, "reason": f"hallucinated_org:{frag}"})
            continue
        seen.add(key)
        kind = q.get("kind") if q.get("kind") in ("user", "split", "rewrite", "expand") else "expand"
        items.append({
            "text": text,
            "scene": _coerce_scene(text, q.get("scene"), brands, comps),
            "kind": kind,
            "origin": _clean_text(q.get("origin"))[:120],
        })
    return items, dropped


def _enforce_quota(items: list[dict]) -> list[dict]:
    """Trim excess brand/compare, then take up to 15 in the original order."""
    out: list[dict] = []
    n_brand = n_compare = 0
    for it in items:
        if it["scene"] == "brand":
            if n_brand >= BRAND_MAX:
                continue
            n_brand += 1
        elif it["scene"] == "compare":
            if n_compare >= COMPARE_MAX:
                continue
            n_compare += 1
        out.append(it)
        if len(out) >= TARGET_QUERY_COUNT:
            break
    return out


def _patch_missing(items: list[dict], *, brand: str, brands: list[str],
                   comps: list[str]) -> str:
    """When brand / compare are entirely missing, fill with templates -- built from the brand name, not industry sentences, so no broken sentences.

    Fewer than 15 items is **not filled**: 15 is a quota ceiling, not a floor; filling category terms with templates would bring back the broken-sentence risk.
    Returns a patch note; empty string means nothing was filled.
    """
    notes: list[str] = []
    b0 = brands[0] if brands else brand
    have = {it["scene"] for it in items}
    if b0 and "brand" not in have and len(items) < TARGET_QUERY_COUNT:
        items.append({"text": f"{b0}怎么样", "scene": "brand", "kind": "expand",
                      "origin": "", "reason": "模板补品牌词"})
        notes.append("brand_patched")
    if b0 and comps and "compare" not in have and len(items) < TARGET_QUERY_COUNT:
        items.append({"text": f"{b0}和{comps[0]}哪个好", "scene": "compare", "kind": "expand",
                      "origin": "", "reason": "模板补竞品对比词"})
        notes.append("compare_patched")
    return ",".join(notes)


# --------------- Public entry points ---------------


def _template_plan(brand: str, industry_terms: list[str], brand_terms: list[str],
                   competitors: Optional[list[str]], reason: str) -> QueryPlan:
    items = [
        {**q, "kind": "template", "origin": "", "reason": ""}
        for q in build_term_queries_template(brand, industry_terms, brand_terms, competitors)
    ]
    return QueryPlan(items=items, source="template", fallback_reason=reason)


def build_manual_plan(items: list[dict], *, brand: str, brand_terms: list[str],
                      competitors: Optional[list[str]] = None) -> QueryPlan:
    """Manual term package path -- terms finalized in the consultant's plan, used as-is.

    Unlike the LLM/template paths, **nothing is generated here**: no expansion, no splitting, no templating,
    no filling missing types, no quota trimming (`_enforce_quota` hard-trims to 15, which would wreck a 20-term plan).
    The only processing is the forced scene judgment -- the same gate as `_coerce_scene`: those containing brand/competitor names must go into
    brand/compare (excluded from the exposure rate/TOP1/TOP3 denominator), those without must not; a human mislabel
    would tamper with the main metrics' denominator, so the input scene is only trusted among search/qa/intent/scenario.
    """
    brands = _brand_names(brand, brand_terms)
    comps = [x.strip() for x in (competitors or []) if x and x.strip()]

    out: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        text = _clean_text((it or {}).get("text"))
        if not text:
            dropped.append({"text": str((it or {}).get("text"))[:60], "reason": "empty"})
            continue
        key = _norm(text)
        if key in seen:
            dropped.append({"text": text, "reason": "duplicate"})
            continue
        seen.add(key)
        out.append({
            "text": text,
            "scene": _coerce_scene(text, (it or {}).get("scene"), brands, comps),
            "kind": "manual",
            "origin": "",
        })
    return QueryPlan(items=out, source="manual", dropped=dropped)


def build_query_plan(*, brand: str, industry_terms: list[str], brand_terms: list[str],
                     competitors: Optional[list[str]] = None, url: str = "",
                     report_id: Optional[int] = None) -> QueryPlan:
    """The single public entry: switch -> cache -> model -> validation -> degrade to templates on failure. **Never raises**.

    The degradation path must stay intact: with no key / 402 insufficient balance / neolink timeout / invalid JSON,
    the report still ships, only the term package falls back to templates.
    """
    ind = [x.strip() for x in industry_terms if x and x.strip()][:5]
    brands = _brand_names(brand, brand_terms)
    comps = [x.strip() for x in (competitors or []) if x and x.strip()]

    def _fallback(reason: str) -> QueryPlan:
        plan = _template_plan(brand, ind, brands, comps, reason)
        log.info("full diagnosis query plan degraded to template: %s", reason)
        return plan

    if not _llm_enabled():
        return _fallback("disabled")
    if not ind:
        return _fallback("no_industry_terms")

    try:
        out = _cached_llm_plan(brand=brand, brands=brands, comps=comps, ind=ind, url=url)
        raw, model_id = out.get("raw") or {}, out.get("model") or ""
    except llm_gateway.LlmUnavailable:
        return _fallback("no_api_key")
    except llm_gateway.LlmError as e:
        return _fallback(f"llm_{getattr(e, 'kind', 'error')}")
    except Exception as e:  # noqa: BLE001
        # Include the original message: a bare ValueError cannot tell whether the model produced no JSON or was truncated
        return _fallback(f"llm_error:{type(e).__name__}:{str(e)[:160]}")

    try:
        items, dropped = _validate(raw, brand=brand, brands=brands, comps=comps,
                                   industry_terms=ind)
    except ValueError as e:
        return _fallback(f"invalid_shape:{str(e)[:60]}")

    category = [it for it in items if it["scene"] not in EXPOSURE_ONLY_SCENES]
    if len(items) < TOTAL_MIN:
        return _fallback(f"too_few:{len(items)}")
    if len(category) < CATEGORY_MIN:
        return _fallback(f"category_short:{len(category)}")

    items = _enforce_quota(items)
    note = _patch_missing(items, brand=brand, brands=brands, comps=comps)
    plan = QueryPlan(items=items, source="llm", model=model_id,
                     fallback_reason=note, dropped=dropped)
    if report_id is not None:
        full_diag_log_event(
            report_id, "queries", "plan_done", "ok",
            message="提示词规划完成",
            source=plan.source, llm_model=plan.model,
            query_count=len(plan.items), dropped_count=len(plan.dropped),
            rewritten_count=sum(1 for i in plan.items if i["kind"] in ("rewrite", "split")),
            fallback_reason=plan.fallback_reason,
        )
    return plan


def _cached_llm_plan(*, brand: str, brands: list[str], comps: list[str],
                     ind: list[str], url: str) -> dict:
    """Wrap in a 24h cache: repeated submissions / repeated rerun_full_diagnosis no longer burn money again,
    and same input -> same term package -> the downstream run_engine_probe_cached 24h cache keeps hitting too
    (otherwise every rerun would change terms and redo all 75 rows of engine calls)."""
    from geo.services.advanced_runners import _cached_run

    identity = {
        "url": url or brand,
        "brand": brand,
        "brand_terms": sorted(brands),
        # Order-sensitive, not sorted: industry term order affects the model's splitting and ratio results
        "industry_terms": ind,
        "competitors": sorted(comps),
        "spec": QUERY_PLAN_SPEC,
        "model": _model(),
        "n": TARGET_QUERY_COUNT,
    }
    return _cached_run("diag-query-plan", identity, lambda: _llm_plan(
        brand=brand, brand_terms=brands, competitors=comps,
        industry_terms=ind, url=url))


def plan_to_subresult(plan: QueryPlan) -> dict:
    """Wrap in the existing sub-item {status, data} shell, directly usable by the frontend subData().

    Note: **queryPlan must not be added to SUB_MODES** -- that is the 8-step progress bar; adding it would make
    the total 9 and always one step short.
    """
    counts = {"total": len(plan.items),
              "category": sum(1 for i in plan.items if i["scene"] not in EXPOSURE_ONLY_SCENES),
              "brand": sum(1 for i in plan.items if i["scene"] == "brand"),
              "compare": sum(1 for i in plan.items if i["scene"] == "compare"),
              "rewritten": sum(1 for i in plan.items if i["kind"] in ("rewrite", "split"))}
    return {"status": "ok", "data": {
        "source": plan.source,
        "model": plan.model,
        "spec": QUERY_PLAN_SPEC,
        "fallback_reason": plan.fallback_reason,
        "counts": counts,
        "items": plan.items,
        "dropped": plan.dropped,
    }}
