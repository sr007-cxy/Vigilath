"""Tencent Yuanbao WebSearch sentiment monitoring quick check.

The new default sentiment flow only uses the yuanbao engine via telemetry-service /run-now, then the backend
LLM does one structured summary; it doesn't touch sentinel-service's multi-platform crawler / analyze / brief.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from geo.models.sentiment import (
    KeywordGroup, SentimentAccountORM, account_symbol, flatten_keyword_groups,
)
from geo.services.telemetry_client import post_run_now

log = logging.getLogger(__name__)


RISK_LEVELS = {"none", "low", "medium", "high"}
SENTIMENT_LABELS = {"bullish", "bearish", "neutral", "mixed", "unknown"}


def _telemetry_url() -> str:
    return os.environ.get("TELEMETRY_SERVICE_URL", "http://localhost:8095").strip().rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("SENTIMENT_WEBSEARCH_TIMEOUT", "360"))


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    return value if isinstance(value, list) else []


def _str_list(values: list[Any], limit: int = 20) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        s = str(v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _truncate(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _account_terms(acc: SentimentAccountORM) -> dict[str, list[str]]:
    aliases = _str_list(_json_list(acc.aliases_json), 8)
    excludes = _str_list(_json_list(acc.excludes_json), 8)

    competitors: list[str] = []
    keywords: list[str] = []
    groups_raw = _json_list(acc.keyword_groups_json)
    if groups_raw:
        groups: list[KeywordGroup] = []
        for g in groups_raw:
            try:
                groups.append(KeywordGroup.model_validate(g))
            except Exception:  # noqa: BLE001
                continue
        competitors = flatten_keyword_groups([g for g in groups if g.kind == "competitor"])
        keywords = flatten_keyword_groups([g for g in groups if g.kind != "competitor"])
    if not keywords:
        keywords = _str_list(_json_list(acc.keywords_json), 20)

    return {
        "aliases": aliases,
        "keywords": _str_list(keywords, 20),
        "competitors": _str_list(competitors, 8),
        "excludes": excludes,
    }


def _build_queries(acc: SentimentAccountORM, terms: dict[str, list[str]]) -> list[str]:
    target = (acc.target or "").strip()
    ticker = (acc.ticker or "").strip()
    aliases = terms["aliases"]
    keywords = terms["keywords"]
    competitors = terms["competitors"]
    excludes = terms["excludes"]

    subject_bits = [target, *aliases[:2]]
    if ticker and ticker.lower() != target.lower():
        subject_bits.append(ticker)
    subject = " ".join(_str_list(subject_bits, 4)) or target or ticker
    # Category disambiguation: with only the brand name, same-named entities in other industries get pulled in
    # (report #99: "<brand-a>" sells beef and mutton, but results were all <namesake-company> goat-milk news).
    # Append it after the subject so every search query carries the category.
    category = str(getattr(acc, "category_hint", "") or "").strip()
    if category:
        subject = f"{subject} {category}"
    focus = " ".join(keywords[:4])
    exclude_hint = f" 排除 {' '.join(excludes[:3])}" if excludes else ""

    queries = [
        f"{subject} 最近一周 舆情 负面 争议 新闻{exclude_hint}",
        f"{subject} 用户评价 口碑 投诉 售后{exclude_hint}",
        f"{subject} 产品效果 真实评价 风险{exclude_hint}",
        f"{subject} 监管 处罚 诉讼 投诉 最近{exclude_hint}",
        f"{subject} 最新新闻 社交媒体 讨论 情绪{exclude_hint}",
    ]
    if focus:
        queries.append(f"{subject} {focus} 舆情 讨论 风险{exclude_hint}")
    if competitors:
        queries.append(f"{target} 和 {competitors[0]} 对比 用户评价 市场讨论{exclude_hint}")
    else:
        queries.append(f"{subject} 同类品牌 对比 用户评价 市场讨论{exclude_hint}")
    if ticker:
        queries.append(f"{ticker} 股吧 投资者情绪 财报 风险 最新讨论{exclude_hint}")
    else:
        queries.append(f"{subject} 品牌风险 消费者反馈 最新讨论{exclude_hint}")

    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:8]


_DATE_RE = re.compile(r"(19|20)\d{2}[-/.年](\d{1,2})([-/.月](\d{1,2}))?")
# Year-only forms (「2015年遭做空」 ("shorted in 2015")) -- must require the 「年」 ("year") character or a following non-digit,
# otherwise numbers like 「60.13亿元」 ("6.013 billion CNY") or 「2024条」 ("2024 items") would be taken as years
_YEAR_RE = re.compile(r"(19|20)\d{2}\s*年")


def _norm_date(value: Any) -> str:
    """Normalize assorted date strings to YYYY-MM-DD / YYYY-MM; leave empty if unrecognized.

    Leaving it empty is intentional: better to show "time unknown" than backfill with the batch run date as before --
    that was the only fabricated date in the report.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    m = _DATE_RE.search(s)
    if not m:
        y = _YEAR_RE.search(s)
        return y.group(0)[:4] if y else ""
    year = m.group(0)[:4]
    # Month/day must fall in valid ranges -- otherwise a year range like "2005-2010" would be read as year 2005 month 20
    try:
        mm = int(m.group(2) or 0)
    except ValueError:
        mm = 0
    if not 1 <= mm <= 12:
        return year
    try:
        dd = int(m.group(4) or 0)
    except ValueError:
        dd = 0
    if not 1 <= dd <= 31:
        return f"{year}-{mm:02d}"
    return f"{year}-{mm:02d}-{dd:02d}"


def _normalize_citations(citations: Any, query: str) -> list[dict[str, Any]]:
    if not isinstance(citations, list):
        return []
    out: list[dict[str, Any]] = []
    for i, c in enumerate(citations[:10]):
        if not isinstance(c, dict):
            continue
        url = str(c.get("url") or "").strip()
        domain = str(c.get("domain") or "").strip()
        if not domain and url:
            try:
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:  # noqa: BLE001
                domain = ""
        if not url and domain:
            url = f"https://{domain}"
        title = str(c.get("title") or domain or url or "来源").strip()
        if not url and not title:
            continue
        out.append({
            "title": title,
            "url": url,
            "domain": domain,
            "snippet": _truncate(c.get("snippet") or c.get("summary") or "", 240),
            "position": c.get("position") or c.get("rank") or (i + 1),
            "query": query,
            # date as given by SearchPro; field name aligned with publish_time on the sentinel line
            "publish_time": _norm_date(c.get("date") or c.get("publish_time")),
        })
    return out


def _normalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query") or "").strip()
        citations = _normalize_citations(row.get("citations"), query)
        out.append({
            "engine": str(row.get("engine") or "yuanbao"),
            "query": query,
            "answer": str(row.get("answer") or "").strip(),
            "citations": citations,
            "error": row.get("error") or None,
        })
    return out


def _dedupe_sources(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for c in row.get("citations") or []:
            key = (c.get("url") or f"{c.get('domain')}:{c.get('title')}").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            sources.append(c)
            if len(sources) >= limit:
                return sources
    return sources


def _evidence_block(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, row in enumerate(rows, 1):
        src_lines = []
        for j, c in enumerate((row.get("citations") or [])[:5], 1):
            when = c.get("publish_time") or "时间未知"
            src_lines.append(
                f"  来源{j} [{when}]: {c.get('title') or c.get('domain')} | "
                f"{c.get('url') or c.get('domain')} | {_truncate(c.get('snippet'), 120)}"
            )
        parts.append(
            f"[{i}] Query: {row.get('query')}\n"
            f"Answer: {_truncate(row.get('answer'), 1400)}\n"
            + "\n".join(src_lines)
        )
    return "\n\n".join(parts)


def _as_list(value: Any, limit: int = 6) -> list[str]:
    """Plain string list (for suggestions)."""
    if not isinstance(value, list):
        return []
    return _str_list(value, limit)


def _as_points(value: Any, limit: int = 6) -> list[dict[str, Any]]:
    """Key point list -> [{text, occurred_at, source_indexes}].

    The model sometimes still falls back to a plain string array (no matter how strict the prompt); that path degrades to
    occurred_at="" -- downstream displays "time unknown" and doesn't guess the date.
    """
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            text, when, refs = item.strip(), "", []
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("point") or item.get("title") or "").strip()
            when = _norm_date(item.get("occurred_at") or item.get("date") or item.get("time"))
            refs = item.get("source_indexes") or item.get("sources") or []
            if not isinstance(refs, list):
                refs = []
        else:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"text": text, "occurred_at": when,
                    "source_indexes": [x for x in refs if isinstance(x, int)][:5]})
        if len(out) >= limit:
            break
    return out


def _normalize_risks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:6]:
        if isinstance(item, str):
            title = item.strip()
            if title:
                out.append({"title": title, "detail": "", "severity": "medium",
                            "occurred_at": "", "source_indexes": []})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("risk") or "").strip()
        detail = str(item.get("detail") or item.get("reason") or item.get("description") or "").strip()
        severity = str(item.get("severity") or item.get("level") or "medium").lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        refs = item.get("source_indexes") or item.get("sources") or []
        if not isinstance(refs, list):
            refs = []
        if title or detail:
            out.append({
                "title": title or detail[:40],
                "detail": detail,
                "severity": severity,
                "occurred_at": _norm_date(item.get("occurred_at") or item.get("date")),
                "source_indexes": [x for x in refs if isinstance(x, int)][:5],
            })
    return out


def _normalize_score(value: Any, label: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = {
            "bullish": 0.45,
            "bearish": -0.45,
            "mixed": 0.0,
            "neutral": 0.0,
            "unknown": 0.0,
        }.get(label, 0.0)
    return round(max(-1.0, min(1.0, score)), 3)


def _summarize_with_llm(acc: SentimentAccountORM, terms: dict[str, list[str]],
                        rows: list[dict[str, Any]], knowledge: dict[str, str] | None) -> tuple[dict[str, Any], str]:
    from geo.services.full_diagnosis_generator import _chat_json, _llm_provider

    provider, model_id, api_key = _llm_provider()
    schema = {
        "risk_level": "one of:none,low,medium,high",
        "sentiment_label": "one of:bullish,bearish,neutral,mixed,unknown",
        "sentiment_score": "number,-1到1,-1极负面,0中性,1极正面",
        "summary": "string,120-220字,只基于证据概括;涉及具体事件必须写明发生年份",
        "positive_points": [{"text": "string,正面要点", "occurred_at": "YYYY-MM-DD 或 YYYY-MM 或 YYYY,不确定填空串",
                             "source_indexes": [1]}],
        "negative_points": [{"text": "string,负面/争议要点,最多5条", "occurred_at": "同上",
                             "source_indexes": [1]}],
        "risks": [
            {"title": "string", "detail": "string", "severity": "low|medium|high",
             "occurred_at": "同上", "source_indexes": [1]}
        ],
        "suggestions": ["string,建议动作,最多5条"],
    }
    facts = {
        "target": acc.target,
        "ticker": acc.ticker,
        "intent": acc.intent,
        "aliases": terms["aliases"],
        "keywords": terms["keywords"][:12],
        "competitors": terms["competitors"][:6],
        "excludes": terms["excludes"],
        "knowledge": {k: _truncate(v, 600) for k, v in (knowledge or {}).items() if v},
    }
    system_prompt = "\n".join([
        "你是品牌舆情分析师,负责把联网搜索证据整理成可执行的舆情快检 JSON。",
        "只允许基于给定的元宝 WebSearch answer 与 sources 判断,不要编造未出现的事件、数字或来源。",
        # Time is the most easily distorted field in this JSON: evidence mixes in decade-old news,
        # and without timestamps downstream writes it into reports as recent sentiment (Acme Cloud got burned in 2026-08)
        "每条要点/风险都要给 occurred_at:只能取自来源行括号里的日期,或正文明确写出的年月;"
        "推断不出来就填空串,**绝对不要**用今天的日期,也不要猜。",
        "risks 只写 negative_points 没覆盖到的事件;同一件事不要在两个列表里各写一遍。",
        "summary 里提到具体事件必须带发生年份,不得把陈年旧事描述成「近期」「最新」。",
        "如果证据不足,用 unknown/neutral/none 并在 summary 中说明本次证据有限。",
        "不要输出 Markdown,不要输出代码围栏,只输出 JSON。",
    ])
    user_prompt = (
        "账号信息:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n\n元宝 WebSearch 证据:\n" + _evidence_block(rows)
        + "\n\n请严格按这个 JSON Schema 输出:\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )
    return _chat_json(provider, model_id, api_key, system_prompt, user_prompt, 0.2), model_id


def _normalize_summary(raw: dict[str, Any], *, acc: SentimentAccountORM, terms: dict[str, list[str]],
                       queries: list[str], rows: list[dict[str, Any]], model_id: str,
                       telemetry_url: str) -> dict[str, Any]:
    risk_level = str(raw.get("risk_level") or "unknown").lower()
    if risk_level not in RISK_LEVELS:
        risk_level = "none"
    label = str(raw.get("sentiment_label") or "unknown").lower()
    if label not in SENTIMENT_LABELS:
        label = "unknown"
    valid_rows = [r for r in rows if not r.get("error") and (r.get("answer") or r.get("citations"))]
    sources = _dedupe_sources(valid_rows)
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        summary = _truncate(valid_rows[0].get("answer") if valid_rows else "本次未取得足够舆情证据。", 220)

    return {
        "mode": "yuanbao_websearch",
        "provider": "yuanbao",
        "target": acc.target,
        # Persistence metric definition -- this column is the lookup key for websearch_reports; the read side
        # (/websearch-latest) queries by acc.symbol, so writes must not use the empty raw ticker.
        "ticker": account_symbol(acc),
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "query_count": len(queries),
        "valid_results": len(valid_rows),
        "risk_level": risk_level,
        "sentiment_label": label,
        "sentiment_score": _normalize_score(raw.get("sentiment_score"), label),
        "summary": summary,
        "positive_points": _as_points(raw.get("positive_points"), 5),
        "negative_points": _as_points(raw.get("negative_points"), 5),
        "risks": _normalize_risks(raw.get("risks")),
        "suggestions": _as_list(raw.get("suggestions"), 5),
        "sources": sources,
        "queries": queries,
        "raw_rows": [
            {
                "engine": r.get("engine"),
                "query": r.get("query"),
                "answer": _truncate(r.get("answer"), 1800),
                "citations": (r.get("citations") or [])[:5],
                "error": r.get("error"),
            }
            for r in rows[:10]
        ],
        "_meta": {
            "telemetry": {
                "url": telemetry_url,
                "engine": "yuanbao",
                "query_count": len(queries),
                "valid_results": len(valid_rows),
            },
            "llm": {"model": model_id, "status": "ok"},
        },
    }


def run_yuanbao_websearch_sentiment(
    acc: SentimentAccountORM,
    *,
    knowledge: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a single-account Yuanbao WebSearch sentiment quick check and return a structured summary.

    Raising RuntimeError means this round failed; sentiment_pipeline writes the state machine and logs uniformly.
    """
    terms = _account_terms(acc)
    queries = _build_queries(acc, terms)
    if not queries:
        raise RuntimeError("舆情快检缺少可搜索的品牌词")

    telemetry_url = _telemetry_url()
    try:
        body: dict[str, Any] = {
            "name": f"sentiment-yuanbao:{acc.id}:{acc.target}",
            "queries": queries,
            "engines": ["yuanbao"],
        }
        # Same source as competitors: the execution path is decided by call_policies' tier; here we only express intent.
        # Yuanbao already uses the "Tencent search + synthesis" API, so the api tier won't filter it out (policy.api_capable
        # previously missed yuanbao, which would have made this 403 directly; fixed together).
        from geo.services.full_diagnosis_competitive import diag_call_policy
        body.update(diag_call_policy(getattr(acc, "user_id", None)))
        payload = post_run_now(
            telemetry_url, body,
            timeout=_timeout(), label=f"sentiment:{acc.id}",
        )
    except httpx.HTTPError as e:
        raise RuntimeError(f"跑批中心不可达({telemetry_url}):{e}") from e
    except ValueError as e:
        raise RuntimeError(f"跑批中心返回非 JSON:{e}") from e

    if not isinstance(payload, list):
        raise RuntimeError(f"跑批中心返回形状异常:{str(payload)[:120]}")

    rows = _normalize_rows(payload)
    valid_rows = [r for r in rows if not r.get("error") and (r.get("answer") or r.get("citations"))]
    if not valid_rows:
        errs = [str(r.get("error")) for r in rows if r.get("error")]
        msg = "; ".join(_str_list(errs, 3)) or "无有效 answer/citations"
        raise RuntimeError(f"元宝 WebSearch 无有效结果:{msg}")

    log.info("sentiment yuanbao websearch[%s]: %d/%d valid",
             acc.id, len(valid_rows), len(rows))
    raw_summary, model_id = _summarize_with_llm(acc, terms, valid_rows, knowledge)
    return _normalize_summary(
        raw_summary, acc=acc, terms=terms, queries=queries, rows=rows,
        model_id=model_id, telemetry_url=telemetry_url,
    )
