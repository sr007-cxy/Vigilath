"""Compliance scan service -- pre-publish gate: deterministic lexicon matching + semantic review + mandatory disclaimer check + qualification check.

Design doc: docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md (v2) 5.2 / 4.2.

Key points:
  - Deterministic substring matching (terms number in the thousands, per-term find is enough; normalization: full-width -> half-width, lowercase,
    zero-width character handling), no dependency on an LLM;
  - Multi-lexicon / multi-level hits take the strictest (ban > evid > warn), keeping all sources;
    overlapping hits keep only the longest span (after "全网第一" ("No.1 on the web") hits, the contained "第一" ("No.1") is not reported separately);
  - **Semantic review layer (fixes substring false positives)**: substring matching cannot read meaning; "第一" in "第一步" ("first step") and
    "唯一" in "唯一标识" ("unique id") still hit ban terms. After a hit, check the context of each occurrence to judge a real violation --
    first run deterministic context exemption rules (zero cost), then send remaining ban / evid hits to the LLM one by one.
    Review **can only downgrade, never upgrade** (design doc line 74 "deterministic first": the LLM adds no new blocks);
    if the LLM is unavailable / parsing fails, keep the original verdict (fail-closed). Exempted hits are still fully recorded;
  - Verdict: ban hit or missing mandatory disclaimer -> returned (auto rejected); evid -> evidence_required
    (P0 only records it for manual review; mandatory claims write-off ships in P2); otherwise -> pass;
  - The four hook points share scan_and_apply(); the two publish exits additionally call check_topic_qualifications().

Lexicon cache: compiled results are cached by (enabled lexicon set, max updated_at); lexicon upgrades invalidate automatically.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicGeneratedDocORM
from geo.models.compliance import (
    ComplianceLexiconORM, ComplianceLexiconTermORM, ComplianceScanResultORM,
)
from geo.services import compliance_audit
from geo.services.llm_gateway import post_and_record

log = logging.getLogger(__name__)

# -- Industry tier derivation (semantics kept consistent with frontend Compliance.tsx tierOf()) ------------
_TIER_RES: list[tuple[str, re.Pattern]] = [
    ("beauty", re.compile(r"美容|美妆|护肤|化妆|医美|beauty|cosmetic|skincare", re.I)),
    ("medical", re.compile(r"医疗|健康|制药|医药|药械|牙科|理疗|medical|health|pharma", re.I)),
    ("finance", re.compile(r"金融|银行|保险|证券|基金|理财|支付|信贷|征信|financ|bank|insurance|invest", re.I)),
    ("health_food", re.compile(r"保健品|保健食品|蓝帽子|膳食营养", re.I)),
    ("food", re.compile(r"食品|生鲜|茶饮|滋补|酒水|饮料", re.I)),
    ("education", re.compile(r"教育|培训|留学|考证", re.I)),
    ("realty", re.compile(r"房产|地产|装修|养老", re.I)),
    ("maternal", re.compile(r"母婴|奶粉|婴幼儿|产后", re.I)),
]


def tier_of(industry: str) -> str:
    s = industry or ""
    for tier, pat in _TIER_RES:
        if pat.search(s):
            return tier
    return "generic"


def ymyl_tier_of(industry: str) -> int:
    """YMYL tier (1/2/3; 0=non-YMYL) -- decides how strict the review is, independent of the lexicon industry tier."""
    s = industry or ""
    if re.search(r"医美|医疗|美容|护肤|药|保健|减脂|牙科|理疗|股票|期货|虚拟货币|信贷|征信|保险", s):
        return 1
    if re.search(r"房产|地产|装修|留学|培训|考证|法律|律所|移民|母婴|奶粉|特医|奢侈品|月子|心理", s):
        return 2
    if re.search(r"财税|养老|理财|家政|婚恋|医疗器械|彩票|社保|户籍|拆迁", s):
        return 3
    return 0


# -- Gate 0: category eligibility (P0 lightweight version; configurable list moves to an admin API later) ----------
_FORBIDDEN_CATEGORIES: list[tuple[str, str]] = [
    (r"一段.{0,4}奶粉|婴儿配方.{0,4}一段", "一段婴儿配方奶粉禁止一切广告(《广告法》)"),
    (r"征信修复|征信洗白|无痕洗白", "征信修复类业务禁止投放"),
    (r"荐股|代客理财|保本理财", "无金融牌照主体禁止推广金融产品 / 荐股"),
]


# -- Article compliance scan master switch (off by default since 2026-08-05) ----------------------------
# The current lexicon rules are too strict; many normal articles were auto-rejected / stuck at the publish exit. Allow everything for now:
# the four hook points (generate / submit for review / review / publish) and the two publish exits all pass directly -- no scan, no
# scan_results, no audit writes. Once rules are relaxed, set env var GEO_COMPLIANCE_SCAN_ENABLED=1
# to restore all hook points at once.
#
# Note: only article scanning is off. In-flight monitoring (compliance_monitor scanning engine answers), qualification entry
# and lexicon management are unaffected.
def scan_enabled() -> bool:
    return (os.getenv("GEO_COMPLIANCE_SCAN_ENABLED", "0").strip().lower()
            in ("1", "true", "yes", "on"))


def _bypass_row(
    doc: TopicGeneratedDocORM,
    topic: AiTelemetryTopicORM,
    hook: str,
) -> ComplianceScanResultORM:
    """Pass result returned when scanning is off: a detached object, not persisted -- avoids leaving a fake "scanned and clean" record."""
    return ComplianceScanResultORM(
        doc_id=doc.id, topic_id=topic.id, hook=hook,
        lexicon_versions_json="{}", hits_json="[]",
        ban_count=0, warn_count=0, evid_count=0,
        missing_disclaimers_json="[]", verdict="pass",
    )


def check_admission(industry: str, name: str = "") -> tuple[bool, str]:
    """Category eligibility check -- called when creating a topic (intake). Returns (eligible, rejection reason)."""
    s = f"{industry or ''} {name or ''}"
    for pat, reason in _FORBIDDEN_CATEGORIES:
        if re.search(pat, s):
            return False, reason
    return True, ""


# -- Text normalization (keeps the same length as the original, so offsets can be used directly for frontend highlighting) --------------
_ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD}


def _normalize(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if o in _ZERO_WIDTH:
            out.append("\x00")          # Placeholder, length unchanged; squeeze matching is handled separately
            continue
        # Full-width -> half-width (NFKC folds width for single chars; multi-char expansions are kept as-is to preserve length)
        n = unicodedata.normalize("NFKC", ch)
        out.append(n.lower() if len(n) == 1 else ch.lower())
    return "".join(out)


def _squeeze(text: str) -> tuple[str, list[int]]:
    """Text with whitespace / zero-width placeholders removed + per-character offset map back to the original.

    Used to catch split-character evasion like "全球第 一" ("No. 1 globally" with an inserted space).
    """
    chars, idx_map = [], []
    for i, ch in enumerate(text):
        if ch == "\x00" or ch.isspace():
            continue
        chars.append(ch)
        idx_map.append(i)
    return "".join(chars), idx_map


# -- Lexicon compilation and cache -----------------------------------------------------
_LEVEL_RANK = {"ban": 3, "evid": 2, "warn": 1}


@dataclass
class _CompiledTerm:
    pattern: str            # Normalized match string
    term: str               # Original term text (for display)
    level: str
    category: str
    fix: str
    source: str             # lexicon code


@dataclass
class _CompiledLexicons:
    stamp: str
    versions: dict[str, str]
    terms: list[_CompiledTerm] = field(default_factory=list)


_cache: dict[str, _CompiledLexicons] = {}


def _load_compiled(db: Session, industry_tier: str) -> _CompiledLexicons:
    lexicons = (
        db.query(ComplianceLexiconORM)
          .filter(ComplianceLexiconORM.enabled.is_(True))
          .filter(ComplianceLexiconORM.tier.in_(["generic", industry_tier]))
          .all()
    )
    stamp = "|".join(sorted(f"{l.code}:{l.version}:{l.updated_at}" for l in lexicons))
    cached = _cache.get(industry_tier)
    if cached and cached.stamp == stamp:
        return cached

    compiled = _CompiledLexicons(
        stamp=stamp,
        versions={l.code: l.version for l in lexicons},
    )
    if lexicons:
        rows = (
            db.query(ComplianceLexiconTermORM)
              .filter(ComplianceLexiconTermORM.lexicon_id.in_([l.id for l in lexicons]))
              .filter(ComplianceLexiconTermORM.active.is_(True))
              .filter(ComplianceLexiconTermORM.status == "active")   # Candidate pool pending/rejected does not take part in scanning
              .all()
        )
        code_by_id = {l.id: l.code for l in lexicons}
        for r in rows:
            variants = [r.term]
            try:
                variants += [a for a in json.loads(r.aliases_json or "[]") if a]
            except Exception:  # noqa: BLE001
                pass
            for v in variants:
                compiled.terms.append(_CompiledTerm(
                    pattern=_normalize(v),
                    term=r.term, level=r.level, category=r.category,
                    fix=r.fix, source=code_by_id.get(r.lexicon_id, ""),
                ))
    _cache[industry_tier] = compiled
    return compiled


# -- Semantic review layer: deterministic context exemptions + per-occurrence LLM review --------------------------
#
# Substring matching has no sense of meaning: "第一" in "第一步 / 第一次 / 第一时间" ("first step / first time / right away") and "唯一" in
# "唯一标识" ("unique id") all hit ban terms and get the article rejected outright. This layer, after a hit, uses the context at the hit
# to judge whether it really constitutes a claim, **only downgrading, never upgrading**:
#   1. Rule exemptions: neutral phrasings judgeable at a glance, zero cost and latency, also saving LLM calls;
#   2. LLM review: ban / evid hits the rules cannot cover, asked per occurrence with context; when unsure, judge as violation.

# {term: (right-adjacent exemptions, left-adjacent exemptions)} -- exempt if the text immediately right/left of the hit matches.
# Inclusion standard: the phrasing **cannot possibly** constitute a claim (ordinal words, technical terms); anything with a hint of claim
# (第一名 / 第一品牌 / 第一梯队 / 唯一选择 -- "top rank / No.1 brand / first tier / only choice") is never included, left to the LLM per occurrence.
_CTX_EXEMPT: dict[str, tuple[Optional[re.Pattern], Optional[re.Pattern]]] = {
    "第一": (
        re.compile(r"^(步|次|时间|批|阶段|章|节|条|款|部分|部|季度|天|周|月|年|版|代|轮|期|人称|页|行|要务|反应|印象|视角|作者|讲|课)"),
        None,
    ),
    "唯一": (
        re.compile(r"^(标识|标志符|标识符|键|索引|编号|id|值|约束|性(?![强好]))", re.I),
        None,
    ),
    "首选": (re.compile(r"^(项|参数)"), None),
    "最新": (re.compile(r"^(章节|回复|评论)"), None),
}

_SEMANTIC_ENABLED = os.environ.get("GEO_COMPLIANCE_SEMANTIC_REVIEW", "1").lower() \
    not in ("0", "false", "no", "off")
_SEMANTIC_MAX_SPANS = int(os.environ.get("GEO_COMPLIANCE_SEMANTIC_MAX_SPANS", "24"))
# Publish exits also run review and cannot afford the generation side's 180s timeout: if stuck, keep the lexicon verdict
_SEMANTIC_TIMEOUT = int(os.environ.get("GEO_COMPLIANCE_SEMANTIC_TIMEOUT", "25"))
_SEMANTIC_CTX = 40          # Characters taken on each side of a hit to show the model

_SEMANTIC_SYSTEM = (
    "你是广告合规审核员。下面每条是一篇稿件里命中违禁词库的一处原文片段,"
    "【】标出命中的词。请只判断:该词在这句话里,是不是真的构成违规宣称。\n"
    "判 false(不违规)的典型:顺序 / 计数 / 时间用法(第一步、第一次、第一时间)、"
    "技术术语(唯一标识、唯一索引)、引用他人观点或客观陈述事实、否定式表述。\n"
    "判 true(违规)的典型:极限用语、绝对化承诺、功效 / 权威 / 销量宣称,"
    "以及任何用来抬高本品牌的表述。\n"
    "拿不准一律判 true。"
)


def _sentence_of(text: str, start: int, end: int) -> str:
    """Take the sentence containing the hit (degrading to a fixed-length window), marking the hit term with 【】."""
    left = max(0, start - _SEMANTIC_CTX)
    right = min(len(text), end + _SEMANTIC_CTX)
    for i in range(start - 1, left - 1, -1):
        if text[i] in "。!?!?;;\n":
            left = i + 1
            break
    for i in range(end, right):
        if text[i] in "。!?!?;;\n":
            right = i + 1
            break
    seg = text[left:start] + "【" + text[start:end] + "】" + text[end:right]
    return seg.replace("\x00", "").strip()


def _rule_exempt(text: str, start: int, end: int, term: str) -> Optional[str]:
    """Deterministic context exemption; returns the exemption reason on match, else None."""
    rule = _CTX_EXEMPT.get(term)
    if not rule:
        return None
    right_re, left_re = rule
    m = right_re.search(text[end:end + 8]) if right_re is not None else None
    if m:
        return f"「{term}{m.group(0)}」是顺序 / 技术性表述,不构成宣称"
    m = left_re.search(text[max(0, start - 8):start]) if left_re is not None else None
    if m:
        return f"「{m.group(0)}{term}」是顺序 / 技术性表述,不构成宣称"
    return None


def _llm_review_spans(spans: list[dict]) -> dict[int, str]:
    """Per-occurrence LLM review. Returns {span index: exemption reason}; those judged violations / failed are not included.

    Never raises -- when the caller gets an empty dict it keeps the lexicon verdict (fail-closed).
    """
    if not spans:
        return {}
    try:
        import requests

        from geo.services.content_generator import (
            DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
            OPENROUTER_DEEPSEEK_MODEL, OPENROUTER_URL, _resolve_provider,
        )

        provider, _model, api_key = _resolve_provider()
        if not provider:
            log.info("[compliance] no LLM key, skipping semantic review (keeping lexicon verdict)")
            return {}

        listing = "\n".join(
            f'{i}. 词=「{s["term"]}」 片段:{s["sentence"]}' for i, s in enumerate(spans))
        user_prompt = (
            f"{listing}\n\n"
            '输出严格 JSON:{"items":[{"i":序号,"violating":true/false,"reason":"20字内理由"}]},'
            "每条命中都要给结论。"
        )
        if provider == "deepseek":
            url, model = f"{DEEPSEEK_BASE_URL}/chat/completions", DEEPSEEK_MODEL
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        else:
            url, model = OPENROUTER_URL, OPENROUTER_DEEPSEEK_MODEL
            headers = {
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                "HTTP-Referer": "https://www.vigilath.com", "X-Title": "GEO Compliance Scanner",
            }
        r = post_and_record(url, {
            "model": model,
            "messages": [
                {"role": "system", "content": _SEMANTIC_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }, headers, provider=provider, feature="compliance.scan",
            timeout=_SEMANTIC_TIMEOUT, model=model)
        if r.status_code >= 400:
            log.warning("[compliance] semantic review %s %s: %s", provider, r.status_code, r.text[:200])
            return {}
        content = r.json()["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.S)
            data = json.loads(m.group(0)) if m else {}

        out: dict[int, str] = {}
        for it in data.get("items") or []:
            if not isinstance(it, dict) or it.get("violating") is not False:
                continue                      # Judged violation / missing field -> keep original verdict
            try:
                i = int(it.get("i"))
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(spans):
                out[i] = str(it.get("reason") or "语义复核判定为非违规表述")[:100]
        return out
    except Exception:  # noqa: BLE001
        log.exception("[compliance] semantic review failed, keeping lexicon verdict")
        return {}


def _apply_semantic_review(hits: list[dict], norm: str, raw: str, semantic: bool) -> None:
    """Mark exemptions on each hit in place: offsets keep only those still counted as violations, exempted ones go into exemptions.

    norm is the normalized body (for rule matching), raw is the original (for audit excerpts); both share the coordinate system
    of offsets (_normalize preserves length).
    """
    # Pass 1: rule exemptions
    pending: list[dict] = []          # Spans awaiting LLM review
    for h in hits:
        h.setdefault("exemptions", [])
        keep: list[list[int]] = []
        for s, e in h["offsets"]:
            reason = _rule_exempt(norm, s, e, h["term"])
            if reason:
                h["exemptions"].append({"offset": [s, e], "by": "rule", "reason": reason,
                                        "quote": _sentence_of(raw, s, e)})
            elif semantic and h["level"] in ("ban", "evid"):
                pending.append({"hit": h, "offset": [s, e], "term": h["term"],
                                "sentence": _sentence_of(raw, s, e)})
                keep.append([s, e])
            else:
                keep.append([s, e])
        h["offsets"] = keep

    # Pass 2: LLM review (only ban / evid hits that could trigger rejection, and capped)
    if _SEMANTIC_ENABLED and pending:
        spans = pending[:_SEMANTIC_MAX_SPANS]
        for i, reason in _llm_review_spans(spans).items():
            sp = spans[i]
            h = sp["hit"]
            if sp["offset"] in h["offsets"]:
                h["offsets"].remove(sp["offset"])
                h["exemptions"].append({"offset": sp["offset"], "by": "llm",
                                        "reason": reason, "quote": sp["sentence"]})

    for h in hits:
        h["suppressed"] = not h["offsets"]      # All occurrences exempted -> the entry is excluded from the verdict


# -- Scan core -----------------------------------------------------------------

def _find_all(haystack: str, needle: str) -> list[int]:
    starts, i = [], haystack.find(needle)
    while i != -1:
        starts.append(i)
        i = haystack.find(needle, i + 1)
    return starts


def scan_text(db: Session, text: str, industry: str,
              disclaimers: Optional[list[dict]] = None, *,
              semantic: bool = False) -> dict:
    """Run enabled lexicons + semantic review + mandatory disclaimer check on a piece of text.

    With semantic=True, ban / evid hits the rules cannot cover are sent to the LLM per occurrence (only downgrades, never upgrades);
    high-frequency callers like in-flight monitoring pass False and use only zero-cost rule exemptions.

    Returns {hits, exempted_hits, ban_count, warn_count, evid_count,
          missing_disclaimers, verdict, lexicon_versions}.
    hits are hits still counted as violations (offsets in original coordinates, for frontend highlighting); fully exempted entries go into
    exempted_hits; both keep exemption reasons in their exemptions field for audit.
    """
    compiled = _load_compiled(db, tier_of(industry))
    norm = _normalize(text or "")
    squeezed, idx_map = _squeeze(norm)

    # Collect raw hits first (start, end, ct)
    raw: list[tuple[int, int, _CompiledTerm]] = []
    for ct in compiled.terms:
        if not ct.pattern:
            continue
        for s in _find_all(norm, ct.pattern):
            raw.append((s, s + len(ct.pattern), ct))
        # Split-character evasion: hit in squeezed but not in the original (whitespace/zero-width inserted)
        for s in _find_all(squeezed, ct.pattern):
            o_start = idx_map[s]
            o_end = idx_map[s + len(ct.pattern) - 1] + 1
            if o_end - o_start > len(ct.pattern):     # Was actually split
                raw.append((o_start, o_end, ct))

    # Overlap suppression: sort by (start, -span), drop those fully covered by a longer span
    raw.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    kept: list[tuple[int, int, _CompiledTerm]] = []
    for s, e, ct in raw:
        covered = any(ks <= s and e <= ke and (ke - ks) > (e - s) for ks, ke, _ in kept)
        if not covered:
            kept.append((s, e, ct))

    # Aggregate: merge offsets for the same term; merge multi-lexicon sources on the same span, level takes the strictest (ban > evid > warn)
    by_term: dict[str, dict] = {}
    for s, e, ct in kept:
        h = by_term.get(ct.term)
        if h is None:
            h = by_term[ct.term] = {
                "term": ct.term, "level": ct.level, "category": ct.category,
                "fix": ct.fix, "sources": [ct.source], "offsets": [],
            }
        else:
            if _LEVEL_RANK[ct.level] > _LEVEL_RANK[h["level"]]:
                h["level"], h["category"] = ct.level, ct.category
                if ct.fix:
                    h["fix"] = ct.fix
            if ct.source not in h["sources"]:
                h["sources"].append(ct.source)
        if [s, e] not in h["offsets"]:
            h["offsets"].append([s, e])
    all_hits = sorted(by_term.values(), key=lambda h: h["offsets"][0][0])

    # Semantic review: judge per occurrence whether the hit is a real violation in its sentence, only downgrade (fixes substring false positives)
    _apply_semantic_review(all_hits, norm, text or "", semantic)
    hits = [h for h in all_hits if not h["suppressed"]]
    exempted = [h for h in all_hits if h["suppressed"]]

    counts = {"ban": 0, "warn": 0, "evid": 0}
    for h in hits:
        counts[h["level"]] += 1

    # Mandatory disclaimer check: each required_texts item must match at least one variant
    missing = []
    for d in disclaimers or []:
        variants = [v for v in (d.get("required_texts") or []) if v]
        if variants and not any(_normalize(v) in norm for v in variants):
            missing.append({"label": d.get("label") or variants[0][:20], "hint": variants[0]})

    if counts["ban"] > 0 or missing:
        verdict = "returned"
    elif counts["evid"] > 0:
        verdict = "evidence_required"
    else:
        verdict = "pass"

    return {
        "hits": hits,
        "exempted_hits": exempted,
        "ban_count": counts["ban"], "warn_count": counts["warn"], "evid_count": counts["evid"],
        "missing_disclaimers": missing,
        "verdict": verdict,
        "lexicon_versions": compiled.versions,
    }


# -- Topic compliance profile -----------------------------------------------------

def load_profile(topic: AiTelemetryTopicORM) -> dict:
    try:
        p = json.loads(getattr(topic, "compliance_profile_json", None) or "{}")
        return p if isinstance(p, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def check_topic_qualifications(topic: AiTelemetryTopicORM) -> tuple[bool, list[dict]]:
    """Gate 0 qualification check -- called at publish exits.

    Missing required items listed in required_qualification_types, or any registered qualification expired
    -> not publishable. No required items configured and none expired -> allowed (non-YMYL customers unaffected).
    """
    profile = load_profile(topic)
    quals = [q for q in profile.get("qualifications") or [] if isinstance(q, dict)]
    required = set(profile.get("required_qualification_types") or [])
    problems: list[dict] = []
    today = datetime.utcnow().date()

    have_types = set()
    for q in quals:
        qtype = str(q.get("type") or "")
        have_types.add(qtype)
        vu = str(q.get("valid_until") or "")[:10]
        if vu:
            try:
                if datetime.strptime(vu, "%Y-%m-%d").date() < today:
                    problems.append({"type": qtype, "ref": q.get("ref"), "problem": "expired",
                                     "valid_until": vu})
            except ValueError:
                problems.append({"type": qtype, "ref": q.get("ref"), "problem": "bad_valid_until",
                                 "valid_until": vu})
    for miss in sorted(required - have_types):
        problems.append({"type": miss, "problem": "missing"})
    return (not problems), problems


def qualification_status(topic: AiTelemetryTopicORM) -> list[dict]:
    """Qualification status list (for frontend profile bar badges): ok / expiring (within 30 days) / expired / missing."""
    profile = load_profile(topic)
    quals = [q for q in profile.get("qualifications") or [] if isinstance(q, dict)]
    required = set(profile.get("required_qualification_types") or [])
    today = datetime.utcnow().date()
    out, have = [], set()
    for q in quals:
        qtype = str(q.get("type") or "")
        have.add(qtype)
        status, vu = "ok", str(q.get("valid_until") or "")[:10]
        if vu:
            try:
                d = datetime.strptime(vu, "%Y-%m-%d").date()
                if d < today:
                    status = "expired"
                elif d <= today + timedelta(days=30):
                    status = "expiring"
            except ValueError:
                status = "expired"
        out.append({"type": qtype, "ref": q.get("ref") or "", "valid_until": vu, "status": status})
    for miss in sorted(required - have):
        out.append({"type": miss, "ref": "", "valid_until": "", "status": "missing"})
    return out


# -- Hook point entry: scan + persist to DB + side effects (auto reject) + audit trail -----------------

def scan_and_apply(
    db: Session,
    doc: TopicGeneratedDocORM,
    topic: AiTelemetryTopicORM,
    *,
    hook: str,
    actor_type: str = "system",
    actor_id: Optional[int] = None,
    actor_name: str = "",
    auto_reject: bool = True,
) -> ComplianceScanResultORM:
    """Shared by the four hook points: scan the doc body, persist scan_results, auto-reject on ban/missing disclaimer as needed, write audit log.

    Does not commit -- follows the caller's transaction. auto_reject=False is for publish exits: exits only block, never change state
    (the caller decides to skip already-published semantics).

    When the master switch is off (default) returns pass directly, no scan and no persistence.
    """
    if not scan_enabled():
        return _bypass_row(doc, topic, hook)

    profile = load_profile(topic)
    text = f"{doc.title or ''}\n{doc.body_markdown or ''}"
    result = scan_text(db, text, topic.industry or "", profile.get("disclaimers") or [],
                       semantic=True)

    # Persist all hits: entries fully exempted by semantic review are stored with suppressed=True,
    # so the frontend "hit details" still lists them (marked exempted); audit requires tracing why it was not blocked.
    stored_hits = result["hits"] + result["exempted_hits"]

    row = ComplianceScanResultORM(
        doc_id=doc.id, topic_id=topic.id, hook=hook,
        lexicon_versions_json=json.dumps(result["lexicon_versions"], ensure_ascii=False),
        hits_json=json.dumps(stored_hits, ensure_ascii=False),
        ban_count=result["ban_count"], warn_count=result["warn_count"],
        evid_count=result["evid_count"],
        missing_disclaimers_json=json.dumps(result["missing_disclaimers"], ensure_ascii=False),
        verdict=result["verdict"],
    )
    db.add(row)
    db.flush()          # Get row.id for the idempotency key

    # Billing. **Scan even if the charge fails** -- compliance is risk control and cannot be skipped for lack of money; on insufficient balance
    # record an unpaid entry in note for ops to collect. The idempotency key pins this one scan; rescanning the same article charges each time.
    from geo.models.balance import KIND_COMPLIANCE, PRICE_COMPLIANCE_SCAN
    from geo.services import topic_billing

    from geo.services import balance_service

    unpaid_cents = 0
    fee = topic_billing.price(db, PRICE_COMPLIANCE_SCAN)
    if fee > 0:
        res = topic_billing.charge(
            db, topic, kind=KIND_COMPLIANCE, amount_cents=fee,
            idem_key=f"comp:{doc.id}:{row.id}",
            ref_type="scan", ref_id=str(row.id),
            note={"hook": hook, "verdict": result["verdict"]},
        )
        if res is not None and res.status == balance_service.INSUFFICIENT:
            # Insufficient balance writes no ledger entry (no money moved); the debt is recorded in the audit log -- that is the only
            # proof on this path of "scanned but not paid", used by ops to collect
            unpaid_cents = fee
            log.warning("[billing] compliance scan insufficient balance doc=%s scan=%s owes %d cents -- scanned anyway",
                        doc.id, row.id, fee)

    compliance_audit.record(
        db, topic_id=topic.id, event_type="scan", object_type="doc", object_id=doc.id,
        actor_type=actor_type, actor_id=actor_id, actor_name=actor_name,
        detail={"hook": hook, "verdict": result["verdict"],
                "ban": result["ban_count"], "warn": result["warn_count"],
                "evid": result["evid_count"],
                "missing_disclaimers": [m["label"] for m in result["missing_disclaimers"]],
                "exempted": [f'{h["term"]}({h["exemptions"][0]["reason"]})'
                             for h in result["exempted_hits"] if h.get("exemptions")],
                "lexicon_versions": result["lexicon_versions"],
                **({"unpaid_cents": unpaid_cents} if unpaid_cents else {})},
    )

    if result["verdict"] == "returned" and auto_reject and doc.status != "published":
        ban_words = [h["term"] for h in result["hits"] if h["level"] == "ban"]
        miss_labels = [m["label"] for m in result["missing_disclaimers"]]
        reason_bits = []
        if ban_words:
            reason_bits.append("命中禁用词:" + "、".join(ban_words[:10]))
        if miss_labels:
            reason_bits.append("缺少强制标注:" + "、".join(miss_labels))
        doc.status = "rejected"
        doc.review_decision_at = datetime.utcnow()
        doc.reject_reason = ("[合规扫描自动退回] " + ";".join(reason_bits))[:500]
        compliance_audit.record(
            db, topic_id=topic.id, event_type="block", object_type="doc", object_id=doc.id,
            actor_type="system", actor_name="词库引擎",
            detail={"hook": hook, "reason": doc.reject_reason},
        )
    return row


def rescan_and_reopen(
    db: Session,
    doc: TopicGeneratedDocORM,
    topic: AiTelemetryTopicORM,
    *,
    actor_type: str = "admin",
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> dict:
    """Rescan to unlock a rejected article after it is edited -- gives rejected a way out.

    Rejected was a terminal dead end: after editing, the status stayed rejected and could neither be submitted for review nor published.
    This rescans once; if it passes it goes back to draft (reject_reason cleared, then submit / approve / publish as usual),
    otherwise it stays rejected with the rejection reason replaced by this scan's hits. Does not commit; follows the caller's transaction.

    Returns {reopened, verdict, reason, ban_terms, exempted}.
    """
    row = scan_and_apply(
        db, doc, topic, hook="manual", auto_reject=False,
        actor_type=actor_type, actor_id=actor_id, actor_name=actor_name,
    )
    hits = json.loads(row.hits_json or "[]")
    ban_words = [h["term"] for h in hits
                 if h.get("level") == "ban" and not h.get("suppressed")]
    exempted = [h["term"] for h in hits if h.get("suppressed")]
    miss = [m["label"] for m in json.loads(row.missing_disclaimers_json or "[]")]

    if row.verdict == "returned":
        bits = []
        if ban_words:
            bits.append("命中禁用词:" + "、".join(ban_words[:10]))
        if miss:
            bits.append("缺少强制标注:" + "、".join(miss))
        doc.status = "rejected"
        doc.review_decision_at = datetime.utcnow()
        doc.reject_reason = ("[合规复扫仍未通过] " + ";".join(bits))[:500]
        compliance_audit.record(
            db, topic_id=topic.id, event_type="block", object_type="doc", object_id=doc.id,
            actor_type=actor_type, actor_id=actor_id, actor_name=actor_name or "复扫",
            detail={"hook": "manual", "reason": doc.reject_reason},
        )
        return {"reopened": False, "verdict": row.verdict, "reason": doc.reject_reason,
                "ban_terms": ban_words, "exempted": exempted}

    doc.status = "draft"
    doc.reject_reason = None
    doc.review_decision_at = None
    doc.selected_for_review = False
    compliance_audit.record(
        db, topic_id=topic.id, event_type="release", object_type="doc", object_id=doc.id,
        actor_type=actor_type, actor_id=actor_id, actor_name=actor_name or "复扫",
        detail={"hook": "manual", "verdict": row.verdict,
                "reason": "复扫通过,退回稿件恢复为草稿", "exempted": exempted},
    )
    return {"reopened": True, "verdict": row.verdict,
            "reason": "复扫通过,已恢复为草稿", "ban_terms": [], "exempted": exempted}


def publish_gate(
    db: Session,
    doc: TopicGeneratedDocORM,
    topic: AiTelemetryTopicORM,
    *,
    hook: str,
    actor_id: Optional[int] = None,
    actor_name: str = "",
) -> tuple[bool, str]:
    """Publish exit checkpoint (hook points 3 / 4): forced rescan + gate 0 qualification check.

    Returns (can push, block reason). Audit log already written when blocked; does not commit.

    When the master switch is off (default) the whole exit passes directly -- qualification checks are skipped too.
    """
    if not scan_enabled():
        return True, ""

    ok_q, problems = check_topic_qualifications(topic)
    if not ok_q:
        detail = "、".join(f"{p.get('type')}({p.get('problem')})" for p in problems[:5])
        compliance_audit.record(
            db, topic_id=topic.id, event_type="block", object_type="doc", object_id=doc.id,
            actor_type="system", actor_name="资质校验",
            detail={"hook": hook, "reason": f"主体资质不齐/过期:{detail}", "problems": problems},
        )
        return False, f"主体资质不齐或已过期:{detail}"

    row = scan_and_apply(
        db, doc, topic, hook=hook, auto_reject=False,
        actor_type="system" if actor_id is None else "admin",
        actor_id=actor_id, actor_name=actor_name,
    )
    if row.verdict == "returned":
        hits = json.loads(row.hits_json or "[]")
        ban_words = [h["term"] for h in hits
                     if h.get("level") == "ban" and not h.get("suppressed")]
        miss = [m["label"] for m in json.loads(row.missing_disclaimers_json or "[]")]
        bits = []
        if ban_words:
            bits.append("命中禁用词:" + "、".join(ban_words[:10]))
        if miss:
            bits.append("缺少强制标注:" + "、".join(miss))
        reason = ";".join(bits) or "合规复扫未通过"
        compliance_audit.record(
            db, topic_id=topic.id, event_type="block", object_type="doc", object_id=doc.id,
            actor_type="system", actor_name="出口复扫",
            detail={"hook": hook, "reason": reason},
        )
        return False, reason
    compliance_audit.record(
        db, topic_id=topic.id, event_type="release", object_type="doc", object_id=doc.id,
        actor_type="system", actor_name="出口复扫",
        detail={"hook": hook, "verdict": row.verdict},
    )
    return True, ""


# -- External check soft signals (P2, design doc 5.7 internal first, then external) --------------------------------

CANDIDATE_LEXICON_CODE = "candidates"


def _candidate_lexicon(db: Session) -> ComplianceLexiconORM:
    """Lexicon that hosts the candidate pool: enabled=False never takes part in scanning, it only contains pending terms."""
    lex = (db.query(ComplianceLexiconORM)
             .filter(ComplianceLexiconORM.code == CANDIDATE_LEXICON_CODE).first())
    if lex is None:
        lex = ComplianceLexiconORM(
            code=CANDIDATE_LEXICON_CODE, name="词条候选池", tier="generic",
            rules_text="外检 / 人工 / 平台规则学习推荐的待采纳词条;采纳时移入目标词库。",
            enabled=False,
        )
        db.add(lex)
        db.flush()
    return lex


def feed_candidates(db: Session, risk_words: list[dict], *, source: str, note_prefix: str = "") -> int:
    """External check riskWords -> pending candidates (one-way feedback).

    Dedup metric definition: normalized comparison against term and aliases of all terms (including pending / rejected, to avoid repeated suggestions).
    Does not commit; follows the caller's transaction. Returns the number added.
    """
    if not risk_words:
        return 0
    existing: set[str] = set()
    for term, aliases_json in db.query(
            ComplianceLexiconTermORM.term, ComplianceLexiconTermORM.aliases_json).all():
        existing.add(_normalize(term))
        try:
            existing.update(_normalize(a) for a in json.loads(aliases_json or "[]") if a)
        except Exception:  # noqa: BLE001
            pass

    lex = _candidate_lexicon(db)
    added = 0
    for w in risk_words:
        word = str(w.get("word") or "").strip()
        if not word or len(word) > 64 or _normalize(word) in existing:
            continue
        # External check terms never go straight into ban (decision record 1): pending until an admin assigns a level
        db.add(ComplianceLexiconTermORM(
            lexicon_id=lex.id, term=word, level="warn", category="外检推荐",
            fix="", aliases_json="[]", status="pending", source=source,
            origin_note=(f"{note_prefix}riskTips={w.get('tips') or ''} "
                         f"riskLevel={w.get('level') or ''}")[:256],
        ))
        existing.add(_normalize(word))
        added += 1
    return added


def external_check_and_feed(
    db: Session,
    doc: TopicGeneratedDocORM,
    topic: AiTelemetryTopicORM,
    row: ComplianceScanResultORM,
) -> None:
    """External check appended at the submit-for-review hook: result written to scan_row.external_json + audit trail + riskWords into candidates.

    Never raises, never changes the internal gate verdict; external check disabled / degraded is also recorded. Committing is the caller's responsibility.

    No external check when the master switch is off (row is a detached object; writing external_json would be meaningless).
    """
    if not scan_enabled():
        return

    from geo.services import external_moderation

    profile = load_profile(topic)
    if profile.get("external_scan_enabled") is False:
        row.external_json = json.dumps({"status": "skipped", "reason": "topic_disabled"},
                                       ensure_ascii=False)
        return
    if not external_moderation.is_enabled():
        row.external_json = json.dumps({"status": "skipped", "reason": "no_credentials"},
                                       ensure_ascii=False)
        return

    result = external_moderation.moderate_text(
        f"{doc.title or ''}\n{doc.body_markdown or ''}")
    row.external_json = json.dumps(result, ensure_ascii=False)
    n_candidates = 0
    # Officially low risk equals not detected -- but physical device tests of ad_compliance_detection on the test host
    # give only low even for obvious Advertising Law superlatives, so in this scenario low ~= "Advertising Law term hint", not noise;
    # Advertising Law hits (tips containing "广告法" ("Advertising Law")) are included at low too; other labels still require medium/high
    strong_words = [w for w in result.get("risk_words") or []
                    if w.get("level") in ("medium", "high")
                    or "广告法" in str(w.get("tips") or "")]
    if strong_words:
        n_candidates = feed_candidates(
            db, strong_words, source="external",
            note_prefix=f"阿里外检 doc={doc.id} ")
    compliance_audit.record(
        db, topic_id=topic.id, event_type="external_scan", object_type="doc",
        object_id=doc.id, actor_type="external", actor_name="阿里内容安全",
        detail={"status": result.get("status"), "risk_level": result.get("risk_level"),
                "labels": result.get("labels"), "words": len(strong_words),
                "new_candidates": n_candidates,
                "request_ids": (result.get("request_ids") or [])[:3],
                "elapsed_ms": result.get("elapsed_ms")},
    )


def external_check_media(db: Session, media, *, base_url: str = "") -> dict:
    """Image external check hook (2026-08-18): result written to media.external_json / external_risk + audit trail.

    Same metric definition as the text-side external_check_and_feed: **never raises, never blocks the main path**.
    The caller is responsible for commit. Images do not feed lexicon candidates -- riskWords is a text concept; images give labels.

    Idempotent: rows that already have external_json are skipped (reused old images are not re-checked, saving call fees).
    """
    if getattr(media, "external_json", None):
        return {"status": "skipped", "reason": "already_scanned"}
    try:
        from geo.services import external_moderation
        result = external_moderation.moderate_media_row(media, base_url=base_url)
    except Exception as e:  # noqa: BLE001 -- the external check must never break image generation/upload
        log.warning("image external scan crashed media=%s: %s", getattr(media, "id", "?"), e)
        result = {"status": "degraded", "reason": str(e)[:120]}

    media.external_json = json.dumps(result, ensure_ascii=False)
    media.external_risk = (result.get("risk_level") or "") if result.get("status") == "ok" else ""

    try:
        compliance_audit.record(
            db, topic_id=media.topic_id, event_type="external_scan", object_type="media",
            object_id=media.id, actor_type="external", actor_name="阿里内容安全",
            detail={"status": result.get("status"), "risk_level": result.get("risk_level"),
                    "labels": result.get("labels"), "service": result.get("service"),
                    "request_ids": (result.get("request_ids") or [])[:3],
                    "elapsed_ms": result.get("elapsed_ms")},
        )
    except Exception:  # noqa: BLE001
        log.warning("image external scan audit failed media=%s", getattr(media, "id", "?"))
    return result
