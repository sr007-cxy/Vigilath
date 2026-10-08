"""Multi-agent refinement pipeline -- outline -> draft -> review -> revise, lifting drafts from "publishable" to "citable".

## Why single-shot generation is not enough

Originally one draft = one LLM call. The model had to juggle brand tone, platform formatting, covering N prompts,
and writing with substance. The result: three things done at once, each to 60% -- the typical symptom is "correct throughout but not a single sentence
is quotable": when answering a question, AI search engines grab **a self-contained 40-80 char answer**, while single-shot
drafts tend to dissolve the answer across the whole piece, so engines have nothing to extract and the GEO goal (being cited) fails.

## Four agents, each owning one job

  (1) Plan (outline)   gives the angle, a "directly quotable answer" sentence for each prompt, and the section skeleton
  (2) Write (draft)    writes the full text from the outline -- reuses the existing prompt pipeline (brand profile / templates / platform rules
                    / images / citation strategy all untouched), with the outline appended as a hard constraint
  (3) Review (review)  scores only, no rewriting: question coverage / quotability / fact density / brand compliance / formatting,
                    outputs an actionable revision list
  (4) Revise (revise)  only changes per the list, no rewriting unrelated paragraphs; reviewed again after (default at most 1 round)

Review scores and details are persisted (quality_score / quality_json) and shown directly on the content review page; operators look at low-scoring drafts first.

## Degradation strategy (no stage failing may bring down drafting)

  Plan fails -> write with an empty outline (same as the old path)
  Review fails -> keep the first draft, quality score None, note records the reason
  Revise fails -> keep the previous version
Only a **write** failure raises -- that means no draft can be produced, handed to gen_retry for requeueing.

## Cost

2-4 calls per draft (plan + write + review [+ revise + re-review]). If the first review passes, no revision,
so in steady state most drafts take 3 calls. The whole pipeline can be turned off with GEO_CONTENT_QUALITY=0, reverting to single-shot generation.

ENV:
    GEO_CONTENT_QUALITY            1 on (default) / 0 off -> revert to single-shot generation
    GEO_CONTENT_QUALITY_MIN_SCORE  passing score, default 80; revision skipped if the first review passes
    GEO_CONTENT_QUALITY_REVISIONS  max revision rounds, default 1
    GEO_CONTENT_QUALITY_OUTLINE    1 on (default) / 0 review+revise only (saves one call)
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Optional

from geo.models.ai_telemetry import BrandProfile

log = logging.getLogger(__name__)

# 2026-08-21 -- output budget of the outline / review agents. Previously hardcoded to 2000 / 1600, set for
# the old deepseek-chat; the model is now **deepseek-v4-flash on the neolink gateway, a reasoning
# model** -- measured in one generation: completion=13364 of which reasoning=11359 (85%);
# the chain of thought ate the budget first and finish_reason=length hit before a single word of output. 2000/1600 will always truncate
# slightly complex prompts, so both refinement steps errored on every draft (quality_score all empty).
# A sufficient budget is all that is needed: it stops after thinking, and unused budget is not billed.
_AGENT_MAX_TOKENS = int(os.environ.get("GEO_CONTENT_AGENT_MAX_TOKENS", "12000"))

DIMS = {
    "coverage": ("问题覆盖", 30),
    "quotability": ("可引用性", 25),
    "evidence": ("事实与信息量", 15),
    "brand": ("品牌一致与合规", 15),
    "format": ("排版与篇幅", 15),
}


def enabled() -> bool:
    return os.environ.get("GEO_CONTENT_QUALITY", "1").strip().lower() not in {"0", "false", ""}


def _outline_enabled() -> bool:
    return os.environ.get("GEO_CONTENT_QUALITY_OUTLINE", "1").strip().lower() not in {"0", "false", ""}


def min_score() -> int:
    try:
        return max(0, min(100, int(os.environ.get("GEO_CONTENT_QUALITY_MIN_SCORE", "80"))))
    except ValueError:
        return 80


def max_revisions() -> int:
    try:
        return max(0, min(3, int(os.environ.get("GEO_CONTENT_QUALITY_REVISIONS", "1"))))
    except ValueError:
        return 1


# -- (1) Plan agent -------------------------------------------------------

_OUTLINE_SYSTEM = (
    "你是 GEO(生成式引擎优化)内容策划。你的产出会交给写作者执笔,目标不是「读起来像好文章」,"
    "而是**让 AI 搜索引擎在回答指定问题时把这篇文章当作答案来源**。\n"
    "AI 引擎摘取的永远是「一段能脱离上下文独立成立的答案」,所以你必须为每条待覆盖的问题"
    "写出一句 40-80 字、自带主语和结论、不依赖前文的直答句。\n"
    "只输出 JSON,不要任何解释文字。"
)


def _outline_user(topic_text: str, cover_queries: list[str], profile: BrandProfile,
                  platform: str, type_label: str,
                  cite_guidelines: Optional[list]) -> str:
    brand = profile.company_short_name or profile.company_full_name or ""
    qs = "\n".join(f"  - {q}" for q in cover_queries) or f"  - {topic_text}"
    cites = "\n".join(f"  - {c}" for c in (cite_guidelines or [])[:6])
    return (
        f"主题:{topic_text}\n"
        f"品牌:{brand}{f' · 行业 {profile.industry}' if profile.industry else ''}\n"
        f"投放平台:{platform or '通用'}{f' · 体裁 {type_label}' if type_label else ''}\n"
        f"本文要覆盖的提示词:\n{qs}\n"
        + (f"该主题的引用策略要点(来自实测信源分析):\n{cites}\n" if cites else "")
        + "\n输出严格 JSON:\n"
        '  "angle": 本文切入角度(一句话,说清跟同题材文章的差异),\n'
        '  "reader": 目标读者与他此刻的疑问(一句话),\n'
        '  "direct_answers": [{"query": 提示词原文, "answer": 40-80 字直答句}],\n'
        '  "outline": [{"h": 小节标题, "points": [该节要讲的 2-4 个要点]}],\n'
        '  "facts_needed": [文中必须落到的具体事实/数据/场景,3-6 条,写不出真实数据就写'
        "「用可验证的行业公开口径表述」],\n"
        '  "avoid": [本文要避开的空话套话,2-4 条]\n'
        "要求:direct_answers 必须覆盖上面每一条提示词;outline 的第一节必须承载直答,"
        "不要开篇讲行业背景。只输出 JSON。"
    )


def _outline_hint_block(outline: dict) -> str:
    """Outline -> hard-constraint section appended to the writing prompt."""
    if not outline:
        return ""
    parts = ["\n\n【策划已定的写作骨架 —— 必须遵守】"]
    if outline.get("angle"):
        parts.append(f"切入角度:{outline['angle']}")
    if outline.get("reader"):
        parts.append(f"目标读者:{outline['reader']}")
    das = [d for d in (outline.get("direct_answers") or []) if isinstance(d, dict)]
    if das:
        parts.append(
            "必须原样(或仅做通顺微调)写进正文的直答句 —— 每句单独成段,"
            "放在对应小节的**开头第一段**,不要拆散、不要藏在铺陈里:")
        for d in das[:8]:
            q = str(d.get("query") or "").strip()
            a = str(d.get("answer") or "").strip()
            if a:
                parts.append(f"  · 问题「{q}」→ {a}")
    ol = [o for o in (outline.get("outline") or []) if isinstance(o, dict)]
    if ol:
        parts.append("小节骨架(顺序不要打乱,可以在节内自由发挥):")
        for i, o in enumerate(ol[:8], 1):
            pts = "、".join(str(x) for x in (o.get("points") or [])[:4])
            parts.append(f"  {i}. {o.get('h') or ''}{f' —— {pts}' if pts else ''}")
    facts = [str(x) for x in (outline.get("facts_needed") or [])[:6] if x]
    if facts:
        parts.append("必须落到的事实/数据(编不出来就用可验证的公开口径,严禁虚构具体数字):")
        parts.extend(f"  · {x}" for x in facts)
    avoid = [str(x) for x in (outline.get("avoid") or [])[:4] if x]
    if avoid:
        parts.append("必须避开的空话:" + "、".join(avoid))
    return "\n".join(parts)


# -- (2.5) Deterministic contact info gate (2026-08-10) --------------------------------
# The review agent is subjective LLM scoring and cannot stop things like "making up a 400 phone number" -- it also thinks
# brand articles should have contact info. So a regex does a deterministic check here: any phone / email /
# WeChat id in the body not in the brand profile's allowlist is treated as a hard defect and sent back for revision.
#
# URLs are only blocked in a CTA context (website / contact / consult / scan QR ...) -- links to cited sources are legitimate;
# a blanket rule would flag "according to the xxx report (link)" as an incident and push the revise agent to delete citability evidence.

_PHONE_RES = [
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),                      # Mobile number
    re.compile(r"(?<!\d)[48]00[-\s]?\d{3,4}[-\s]?\d{3,4}(?!\d)"),  # 400 / 800
    re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)"),            # Landline
]
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WECHAT_RE = re.compile(r"(?:微信号?|公众号|企业微信|QQ)\s*[::]?\s*([A-Za-z][-_A-Za-z0-9]{4,19})")
# URLs must not swallow Chinese/English punctuation, otherwise "…com,客服电话" ("...com, customer service phone") would be taken as part of the domain
_URL_STOP = r"\s，。、；;：:）)】」』\]>\"'"
_URL_RE = re.compile(
    r"(?:https?://[^" + _URL_STOP + r"]+|www\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^" + _URL_STOP + r"]*)")
# CTA terms must be phrases, not single words like "咨询" ("consulting") -- "据艾瑞咨询报告(链接)" ("according to the iResearch Consulting report (link)") would be misjudged as
# brand contact info, pushing the revise agent to delete a legitimate cited source.
_CTA_WORDS = ("官网", "联系我们", "联系方式", "咨询热线", "咨询电话", "在线咨询",
              "服务热线", "客服", "扫码", "预约", "立即", "详情请", "点击", "下单", "购买")


def _norm_phone(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def _norm_url(s: str) -> str:
    """Normalize to the **hostname** for comparison -- the allowlist holds https://www.x.com, and x.com/blog/a in the body
    is the same site; a different path must not be judged as fabricated."""
    u = (s or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].split("?")[0].strip(".,;:)]}」』")


def _contact_whitelist(profile: BrandProfile) -> dict:
    """Contact info filled in the profile -- normalized as the allowlist (same metric definition as _contact_values on the generation side)."""
    return {
        "phones": {_norm_phone(getattr(profile, "contact_phone", "") or "")} - {""},
        "emails": {(getattr(profile, "contact_email", "") or "").strip().lower()} - {""},
        "wechats": {(getattr(profile, "contact_wechat", "") or "").strip().lower()} - {""},
        "urls": {_norm_url(profile.website or "")} - {""},
    }


def _contact_violations(text: str, profile: BrandProfile) -> list[str]:
    """Returns a list of hard defect descriptions; empty = clean. text is the concatenated title + summary + body."""
    wl = _contact_whitelist(profile)
    out: list[str] = []
    seen: set[str] = set()

    def add(kind: str, val: str) -> None:
        key = f"{kind}:{val}"
        if key in seen:
            return
        seen.add(key)
        out.append(
            f"正文出现未经确认的{kind}「{val}」—— 品牌资料里没有这一项,必须整句删除,"
            f"不要改成别的号码/网址,也不要替换成示例值"
        )

    for rex in _PHONE_RES:
        for m in rex.findall(text or ""):
            if _norm_phone(m) not in wl["phones"]:
                add("联系电话", m.strip())
    for m in _EMAIL_RE.findall(text or ""):
        if m.strip().lower() not in wl["emails"]:
            add("邮箱", m.strip())
    for m in _WECHAT_RE.findall(text or ""):
        if m.strip().lower() not in wl["wechats"]:
            add("微信号 / 公众号", m.strip())
    # URLs: line by line; only on the same line as a CTA term does it count as "brand contact info", otherwise allowed as a cited source
    for line in (text or "").splitlines():
        if not any(w in line for w in _CTA_WORDS):
            continue
        for m in _URL_RE.findall(line):
            if _norm_url(m) not in wl["urls"]:
                add("网址", m.strip())
    return out[:6]


# -- (3) Review agent -----------------------------------------------------

_REVIEW_SYSTEM = (
    "你是苛刻的 GEO 内容审稿人。你的唯一标准是:这篇稿子投出去之后,AI 搜索引擎回答"
    "指定问题时会不会引用它。你只打分和开修订清单,**绝对不要重写正文**。\n"
    "打分要吝啬:通顺但没有可摘引答案的稿子,可引用性一律不及格。只输出 JSON。"
)


def _review_user(title: str, body: str, summary: str, topic_text: str,
                 cover_queries: list[str], profile: BrandProfile,
                 platform: str, len_spec: str, allow_md: bool,
                 cite_guidelines: Optional[list]) -> str:
    qs = "\n".join(f"  - {q}" for q in cover_queries) or f"  - {topic_text}"
    # Content no-go areas / prohibitions come from the profile (profile.content_redlines), same metric definition as the generation side
    banned = "、".join(str(x) for x in (profile.content_redlines or [])[:20])
    cites = "\n".join(f"  - {c}" for c in (cite_guidelines or [])[:6])
    contacts = "、".join(
        v for v in (
            (profile.website or "").strip(),
            (getattr(profile, "contact_phone", "") or "").strip(),
            (getattr(profile, "contact_wechat", "") or "").strip(),
            (getattr(profile, "contact_email", "") or "").strip(),
        ) if v
    )
    fmt_rule = ("允许 Markdown" if allow_md
                else "严禁任何 Markdown / 列表符号,分点要用「一、二、三、」或「①②③」")
    return (
        f"待审稿件\n标题:{title}\n摘要:{summary}\n正文:\n{body}\n\n"
        f"—— 评审依据 ——\n"
        f"主题:{topic_text}\n"
        f"必须覆盖的提示词:\n{qs}\n"
        f"平台:{platform or '通用'} · 篇幅要求:{len_spec} · 排版:{fmt_rule}\n"
        + (f"内容雷区 / 禁止项:{banned}\n" if banned else "")
        + (f"允许出现的联系方式(只有这些,其余一律算硬伤):{contacts}\n" if contacts
           else "本品牌未提供任何联系方式:正文出现电话 / 网址 / 微信 / 邮箱一律算硬伤\n")
        + (f"引用策略要点:\n{cites}\n" if cites else "")
        + "\n按这 5 个维度打分(每项 0-100),只输出 JSON:\n"
        '  "dims": {"coverage": 分, "quotability": 分, "evidence": 分, "brand": 分, "format": 分},\n'
        '  "uncovered": [没有被明确回答的提示词原文],\n'
        '  "blocking": [必须改否则不能发的问题,如虚构数据 / 出现禁用词 / 排版违规],\n'
        '  "fixes": [最多 5 条可执行修订指令,每条指明「改哪一段 → 怎么改」,不要泛泛而谈],\n'
        '  "verdict": "pass" 或 "revise"\n'
        "维度口径:coverage = 每条提示词是否都有明确答案;"
        "quotability = 是否存在能脱离上下文独立成立、可被 AI 直接摘引的 40-80 字答案段;"
        "evidence = 有没有具体场景/口径/可核查表述,空话越多分越低;"
        "brand = 是否符合品牌定位、没碰内容雷区、无夸大绝对化用语;"
        "format = 排版和篇幅是否达标。只输出 JSON。"
    )


def _weighted(dims: dict) -> Optional[int]:
    vals = {}
    for k in DIMS:
        v = dims.get(k)
        if isinstance(v, (int, float)) and 0 <= v <= 100:
            vals[k] = float(v)
    if not vals:
        return None
    total_w = sum(DIMS[k][1] for k in vals)
    return round(sum(DIMS[k][1] * vals[k] for k in vals) / total_w)


def _norm_review(raw: dict) -> dict:
    dims_raw = raw.get("dims") if isinstance(raw.get("dims"), dict) else {}
    dims = {k: dims_raw.get(k) for k in DIMS if isinstance(dims_raw.get(k), (int, float))}
    score = _weighted(dims_raw or {})
    lst = lambda key, n: [str(x)[:300] for x in (raw.get(key) or []) if x][:n]  # noqa: E731
    return {
        "score": score,
        "dims": dims,
        "uncovered": lst("uncovered", 8),
        "blocking": lst("blocking", 6),
        "fixes": lst("fixes", 5),
        "verdict": "revise" if str(raw.get("verdict") or "") == "revise" else "pass",
    }


# -- (4) Revise agent -----------------------------------------------------

_REVISE_SYSTEM = (
    "你是内容编辑。你会收到一篇稿子和一份修订清单。**只按清单改**,清单没提到的段落"
    "一个字都不要动,不要重写全文、不要改变原有排版风格。只输出 JSON。"
)


def _revise_user(title: str, body: str, summary: str,
                 review: dict, len_spec: str, allow_md: bool) -> str:
    fixes = "\n".join(f"  {i}. {f}" for i, f in enumerate(review.get("fixes") or [], 1))
    blocking = "\n".join(f"  · {b}" for b in (review.get("blocking") or []))
    uncovered = "、".join(review.get("uncovered") or [])
    fmt_rule = ("允许 Markdown" if allow_md
                else "严禁任何 Markdown / 列表符号,分点用「一、二、三、」或「①②③」")
    return (
        f"原稿\n标题:{title}\n摘要:{summary}\n正文:\n{body}\n\n"
        f"必须修掉的硬伤:\n{blocking or '  (无)'}\n"
        f"修订清单:\n{fixes or '  (无)'}\n"
        + (f"以下提示词在原稿里没有被明确回答,请各补一段 40-80 字的独立直答:{uncovered}\n"
           if uncovered else "")
        + f"\n篇幅仍按 {len_spec},排版规则:{fmt_rule}。\n"
        '输出严格 JSON:"title"、"summary"、"body"(改后的完整稿,不要只给差异)。只输出 JSON。'
    )


# -- Pipeline -------------------------------------------------------------

def run(
    *,
    draft_fn: Callable[[str], tuple[str, str, str]],
    provider: str,
    api_key: str,
    profile: BrandProfile,
    topic_text: str,
    cover_queries: Optional[list[str]] = None,
    platform: str = "",
    type_label: str = "",
    len_spec: str = "800-1500 字",
    allow_md: bool = False,
    cite_guidelines: Optional[list] = None,
) -> tuple[str, str, str, dict]:
    """Run the refinement pipeline once, returning (title, body, summary, quality).

    draft_fn(outline_hint) is provided by the caller -- internally it is the existing template or profile path,
    so not a single line of the brand tone / formatting / images / platform rules prompt logic needs to change.
    """
    from geo.services.content_generator import chat_json

    queries = [q for q in (cover_queries or []) if q]
    quality: dict = {"pipeline": "multi_agent", "rounds": 0, "notes": []}

    # (1) Plan
    outline: dict = {}
    if _outline_enabled():
        try:
            outline = chat_json(
                _OUTLINE_SYSTEM,
                _outline_user(topic_text, queries, profile, platform, type_label,
                              cite_guidelines),
                provider, api_key, max_tokens=_AGENT_MAX_TOKENS,
                feature="content_gen.outline",
            )
            quality["outline"] = {
                "angle": str(outline.get("angle") or "")[:300],
                "direct_answers": len(outline.get("direct_answers") or []),
                "sections": len(outline.get("outline") or []),
            }
        except Exception as e:  # noqa: BLE001
            outline = {}
            quality["notes"].append(f"策划 agent 失败,退回直接写作:{str(e)[:120]}")
            log.warning("[quality] outline failed topic_text=%r: %s", topic_text[:40], e)

    # (2) Write -- raise on failure, let gen_retry take over
    title, body, summary = draft_fn(_outline_hint_block(outline))

    # (3)(4) Review -> revise -> re-review
    best = (title, body, summary)
    review: Optional[dict] = None
    for rnd in range(max_revisions() + 1):
        try:
            review = _norm_review(chat_json(
                _REVIEW_SYSTEM,
                _review_user(best[0], best[1], best[2], topic_text, queries,
                             profile, platform, len_spec, allow_md, cite_guidelines),
                provider, api_key, max_tokens=_AGENT_MAX_TOKENS,
                feature="content_gen.review",
            ))
        except Exception as e:  # noqa: BLE001
            quality["notes"].append(f"评审 agent 失败:{str(e)[:120]}")
            log.warning("[quality] review failed: %s", e)
            review = None
            break

        # Deterministic gate: fabricated contact info is merged into blocking -- caught even if the review agent misses it
        contact_bad = _contact_violations("\n".join(best), profile)
        if contact_bad:
            review["blocking"] = (review["blocking"] + contact_bad)[:8]
            review["verdict"] = "revise"
            quality["contact_violations"] = contact_bad

        quality["score"] = review["score"]
        quality["dims"] = review["dims"]
        quality["uncovered"] = review["uncovered"]
        quality["blocking"] = review["blocking"]
        quality["fixes"] = review["fixes"]
        score = review["score"]
        need = (review["verdict"] == "revise" or review["blocking"]
                or (score is not None and score < min_score()))
        if not need or rnd >= max_revisions():
            if need:
                quality["notes"].append(
                    f"改了 {rnd} 轮仍未达标(分 {score} < {min_score()}),按现状出稿待人工处理")
            break

        # Revise once; on failure keep the previous version and stop
        try:
            fixed = chat_json(
                _REVISE_SYSTEM,
                _revise_user(best[0], best[1], best[2], review, len_spec, allow_md),
                provider, api_key, feature="content_gen.revise",
            )
            new_title = str(fixed.get("title") or "").strip()
            new_body = str(fixed.get("body") or "").strip()
            if not new_body:
                raise RuntimeError("修订 agent 回了空正文")
            best = (new_title or best[0], new_body,
                    str(fixed.get("summary") or "").strip() or best[2])
            quality["rounds"] = rnd + 1
        except Exception as e:  # noqa: BLE001
            quality["notes"].append(f"修订 agent 失败,保留上一版:{str(e)[:120]}")
            log.warning("[quality] revise failed: %s", e)
            break

    left = _contact_violations("\n".join(best), profile)
    if left:
        quality["contact_violations"] = left
        quality["notes"].append(
            f"仍带 {len(left)} 处未确认的联系方式(电话/网址/微信/邮箱),已标出待人工删除")
        log.warning("[quality] contact info hard defects not fully fixed: %s", left[:2])

    log.info("[quality] draft produced score=%s rounds=%s uncovered=%s notes=%s",
             quality.get("score"), quality.get("rounds"),
             len(quality.get("uncovered") or []), len(quality["notes"]))
    return best[0], best[1], best[2], quality


def dump(quality: Optional[dict]) -> Optional[str]:
    """quality dict -> JSON string for persistence (None / empty -> None)."""
    if not quality:
        return None
    try:
        return json.dumps(quality, ensure_ascii=False)[:4000]
    except Exception:  # noqa: BLE001
        return None
