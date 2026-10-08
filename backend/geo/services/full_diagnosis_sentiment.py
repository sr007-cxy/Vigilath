"""Sentiment monitoring chapter of one-click diagnosis -- a live quick check via Tencent Yuanbao WebSearch.

Creates (or reuses) a sentiment monitoring account for the diagnosed brand, calls the yuanbao engine of telemetry-service /run-now
 for web search, then the backend LLM produces a structured summary. This no longer calls
 the sentinel-service run-monitor / run-analyze / crawler pipeline, so one-click diagnosis does not
 trigger live multi-platform scraping of Weibo, Zhihu, Tieba, Xiaohongshu, etc.

Account source:
- If the user already has a formal sentiment monitoring account, reuse its aliases / keywords / excludes to generate search questions
- Without a formal account, only build a temporary in-memory object; no new sentiment_account, and no
  sentinel-service tenant schema / websearch_reports table creation

ENV:
    GEO_DIAG_SENTIMENT_DAYS      display/read window in days, default 30
    TELEMETRY_SERVICE_URL        batch run center URL, default http://localhost:8095
    SENTIMENT_WEBSEARCH_TIMEOUT  Yuanbao WebSearch per-call timeout seconds, default 360

Notes:
- The Tencent SearchPro key is still read by telemetry-service from TENCENT_SEARCH_API_KEY.
- One-click diagnosis results are only saved with full_diagnosis_reports.subresults_json, never written to
  tenant_{account_id}.websearch_reports; that table is only for formal sentiment monitoring accounts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from geo.services.sentiment_yuanbao_websearch import _norm_date
from geo.utils.full_diagnosis_log import full_diag_log_event

log = logging.getLogger(__name__)


RISK_COLORS = {"none": "#94a3b8", "low": "#fbbf24", "medium": "#fb923c", "high": "#ef4444"}
_SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
# Sentiment structure version -- goes into the cache key. v2: real event times + negative/risk dedup
SENTIMENT_SPEC = "v3-user-scoped"


def _window_days() -> int:
    """"Recent" judgment window -- **not the search window**.

    Yuanbao WebSearch does not filter by time, and the evidence returned spans more than a decade; this value is only used to split items into
    recent / historical (affecting scoring weight and copy wording), never discarding any evidence.
    """
    return int(os.environ.get("GEO_DIAG_SENTIMENT_DAYS", "30"))


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    return value if isinstance(value, list) else []


def _dedupe_str(values: list[Any], limit: int = 40) -> list[str]:
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


def _account_snapshot(acc: Any | None, brand: str, brand_terms: List[str],
                      industry_terms: Optional[List[str]] = None) -> Any:
    """Copy sentiment account fields for the long-running WebSearch; without a formal account only build an in-memory object.

    `category_hint` is for **brand disambiguation**: search terms used to be just the brand name, so same-named entities in different industries
    got pulled in -- report #99's "<brand-a>" (<brand-a>) sells beef and mutton, but all 12 pieces of evidence fetched were about <namesake-company>
    (goat milk) supply disruptions and soured fresh goat milk, so the whole sentiment chapter described a different company. With the category added
    the search term becomes "<brand-a> 牛羊肉 舆情 负面" ("<brand-a> beef/mutton sentiment negative"), which finds the right results.
    """
    hint = " ".join(_dedupe_str([t.strip() for t in (industry_terms or []) if t and t.strip()])[:3])
    if acc is None:
        keywords = _dedupe_str([t for t in brand_terms if t and t != brand])
        return SimpleNamespace(
            id=0,
            target=brand,
            # No formal account = nobody entered a stock ticker. Previously the brand name was put here,
            # so the quick check always included a useless search term "{brand} 股吧 投资者情绪 财报" ("{brand} stock forum investor sentiment earnings").
            ticker="",
            aliases_json="[]",
            intent=hint or None,
            category_hint=hint,
            keywords_json=json.dumps(keywords, ensure_ascii=False),
            keyword_groups_json="[]",
            excludes_json="[]",
        )

    extra_keywords = [t for t in brand_terms if t and t != acc.target and t != acc.ticker]
    keywords = _dedupe_str(_json_list(acc.keywords_json) + extra_keywords)
    return SimpleNamespace(
        id=acc.id,
        target=acc.target,
        ticker=acc.ticker,
        aliases_json=acc.aliases_json or "[]",
        # A formal account's intent was entered by the user, not overridden; the category goes through category_hint separately
        intent=acc.intent,
        category_hint=hint,
        keywords_json=json.dumps(keywords, ensure_ascii=False),
        keyword_groups_json=acc.keyword_groups_json or "[]",
        excludes_json=acc.excludes_json or "[]",
    )


def _get_account_snapshot(user_id: int, brand: str, brand_terms: List[str],
                          industry_terms: Optional[List[str]] = None) -> Any:
    from sqlalchemy import or_

    from geo.database import SessionLocal
    from geo.models.sentiment import SentimentAccountORM

    db = SessionLocal()
    try:
        # Either brand name or stock ticker matching counts as this account -- the ticker is optional,
        # and matching only by ticker would never find unlisted entities' accounts, degrading the quick check into an empty snapshot.
        acc = (
            db.query(SentimentAccountORM)
              .filter(SentimentAccountORM.user_id == user_id,
                      or_(SentimentAccountORM.target == brand,
                          SentimentAccountORM.ticker == brand))
              .first()
        )
        return _account_snapshot(acc, brand, brand_terms, industry_terms)
    finally:
        db.close()


def _score_for_label(label: str, fallback: Any = None) -> float:
    try:
        return round(max(-1.0, min(1.0, float(fallback))), 3)
    except (TypeError, ValueError):
        return {
            "bullish": 0.45,
            "bearish": -0.45,
            "mixed": 0.0,
            "neutral": 0.0,
            "unknown": 0.0,
        }.get(label, 0.0)


def _risk_score(severity: str) -> float:
    return {"high": -0.75, "medium": -0.55, "low": -0.3}.get(severity, -0.45)


def _recency_of(occurred_at: str) -> str:
    """Event time -> recent / historical / unknown (the window is only for judgment and scoring weight, not filtering)."""
    if not occurred_at:
        return "unknown"
    try:
        parts = [int(x) for x in occurred_at.split("-")]
    except ValueError:
        return "unknown"
    y = parts[0]
    m = parts[1] if len(parts) > 1 else 12
    d = parts[2] if len(parts) > 2 else 28
    try:
        when = datetime(y, m, d)
    except ValueError:
        return "unknown"
    return "recent" if (datetime.utcnow() - when).days <= _window_days() else "historical"


def _make_post(*, title: str, source: str, url: str = "", summary: str = "",
               label: str = "neutral", score: float = 0.0, risk_level: str = "none",
               occurred_at: str = "") -> dict[str, Any]:
    """One sentiment item.

    `publish_time` is only filled with the **real event time**, left empty if unavailable -- previously this was backfilled with the batch run date,
    and the frontend displayed it as the publish date, stamping every old story with "today"
    (in the 2026-08 Acme Cloud report a 2015 short-seller event was labeled as that day).
    """
    return {
        "title": title[:160],
        "source": source or "Yuanbao WebSearch",
        "url": url,
        "publish_time": occurred_at,
        "recency": _recency_of(occurred_at),
        "sentiment_label": label,
        "sentiment_score": score,
        "risk_level": risk_level,
        "summary": summary[:240],
    }


_STOP_CHARS = re.compile(r"[\s,,。;;::、()()「」\"'’”]+")


def _event_key(text: str) -> set[str]:
    """Event fingerprint: set of 2-char sliding windows. Used to recognize when a "negative point" and a "risk" describe the same thing."""
    t = _STOP_CHARS.sub("", (text or ""))[:60]
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def _same_event(a: str, b: str) -> bool:
    ka, kb = _event_key(a), _event_key(b)
    if not ka or not kb:
        return False
    return len(ka & kb) / min(len(ka), len(kb)) >= 0.55


def _point_fields(point: Any) -> tuple[str, str]:
    """Supports two shapes: the new {text, occurred_at} and the old plain string."""
    if isinstance(point, dict):
        return str(point.get("text") or "").strip(), str(point.get("occurred_at") or "").strip()
    return str(point or "").strip(), ""


def _posts_from_websearch(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one WebSearch report into a posts-compatible structure consumable by the old scorer.

    Two things must be completed here:
    1. **Event time only accepts real dates** (occurred_at labeled by the model, or the date of the source it cites); left empty if unavailable.
    2. **Dedupe negative_points and risks** -- the same model produces these two lists in parallel from the same evidence,
       often writing the same thing in each; without merging, negative counts and risk deductions double (the Acme Cloud case had
       5 negative points + 5 risks, actually only 5~6 distinct events).
    """
    base_label = str(report.get("sentiment_label") or "unknown").lower()
    base_score = _score_for_label(base_label, report.get("sentiment_score"))
    posts: list[dict[str, Any]] = []

    # Source date fallback: when the model did not label occurred_at, use the publish time of the source it cites
    src_dates: list[str] = [str(s.get("publish_time") or "").strip()
                            for s in (report.get("sources") or []) if isinstance(s, dict)]

    def _when(occurred_at: str, refs: Any, text: str = "") -> str:
        """Three-level criteria for event time; the earlier, the closer to "the event itself":

        1. occurred_at labeled by the model
        2. A year written in the point text -- like "2015年遭做空" ("short-sold in 2015"), more accurate than the source date
           (a 2025 article may well be talking about something from 2015)
        3. Publish time of the cited source
        """
        if occurred_at:
            return occurred_at
        from_text = _norm_date(text)
        if from_text:
            return from_text
        for i in refs if isinstance(refs, list) else []:
            if isinstance(i, int) and 1 <= i <= len(src_dates) and src_dates[i - 1]:
                return src_dates[i - 1]
        return ""

    for point in report.get("positive_points") or []:
        text, when = _point_fields(point)
        if not text:
            continue
        refs = point.get("source_indexes") if isinstance(point, dict) else None
        posts.append(_make_post(
            title=f"正面舆情:{text[:80]}", source="Yuanbao WebSearch",
            summary=text, label="bullish", score=0.55,
            risk_level="none", occurred_at=_when(when, refs, text),
        ))

    # Negative side: first merge the two lists into "events", then build posts
    events: list[dict[str, Any]] = []
    for point in report.get("negative_points") or []:
        text, when = _point_fields(point)
        if not text:
            continue
        refs = point.get("source_indexes") if isinstance(point, dict) else None
        # Negative points carry no severity themselves -- previously hardcoded as medium, so every one was counted as "medium/high risk"
        events.append({"title": f"负面/争议:{text[:80]}", "summary": text,
                       "severity": "low", "occurred_at": _when(when, refs, text)})

    for risk in report.get("risks") or []:
        if not isinstance(risk, dict):
            continue
        title = str(risk.get("title") or risk.get("risk") or "").strip()
        detail = str(risk.get("detail") or risk.get("reason") or "").strip()
        severity = str(risk.get("severity") or "medium").lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        if not (title or detail):
            continue
        when = _when(str(risk.get("occurred_at") or "").strip(),
                     risk.get("source_indexes"), f"{title} {detail}")
        merged = False
        for ev in events:
            if _same_event(ev["summary"], detail or title) or _same_event(ev["summary"], title):
                # Same event: take the higher severity, fill the missing time, use the risk's title (reads more like a conclusion)
                ev["severity"] = max(ev["severity"], severity, key=_SEVERITY_ORDER.get)
                ev["occurred_at"] = ev["occurred_at"] or when
                ev["title"] = f"风险:{title or detail[:80]}"
                ev["summary"] = detail or title or ev["summary"]
                merged = True
                break
        if not merged:
            events.append({"title": f"风险:{title or detail[:80]}",
                           "summary": detail or title, "severity": severity,
                           "occurred_at": when})

    for ev in events:
        posts.append(_make_post(
            title=ev["title"], source="Yuanbao WebSearch",
            summary=ev["summary"], label="bearish",
            score=_risk_score(ev["severity"]), risk_level=ev["severity"],
            occurred_at=ev["occurred_at"],
        ))

    if not posts:
        for src in (report.get("sources") or [])[:8]:
            if not isinstance(src, dict):
                continue
            title = str(src.get("title") or src.get("domain") or "元宝搜索来源").strip()
            posts.append(_make_post(
                title=title, source=str(src.get("domain") or "Yuanbao WebSearch"),
                url=str(src.get("url") or ""),
                summary=str(src.get("snippet") or report.get("summary") or ""),
                label=base_label, score=base_score,
                risk_level=str(report.get("risk_level") or "none"),
                occurred_at=str(src.get("publish_time") or "").strip(),
            ))

    if not posts:
        posts.append(_make_post(
            title=f"{report.get('target') or ''} 元宝 WebSearch 舆情摘要".strip(),
            source="Yuanbao WebSearch",
            summary=str(report.get("summary") or "本次未取得足够舆情证据。"),
            label=base_label, score=base_score,
            risk_level=str(report.get("risk_level") or "none"),
        ))

    return posts[:30]


_POS_LABELS = {"bullish", "positive", "正面", "看涨", "利好"}
_NEG_LABELS = {"bearish", "negative", "负面", "看跌", "利空"}


def _stance_counts(posts: list[dict]) -> dict:
    """Positive/neutral/negative three-class counts -- symmetric thresholds +/-0.2."""
    pos = neu = neg = 0
    for p in posts:
        label = str(p.get("sentiment_label") or "").strip().lower()
        if label in _POS_LABELS:
            pos += 1
            continue
        if label in _NEG_LABELS:
            neg += 1
            continue
        try:
            s = float(p.get("sentiment_score"))
        except (TypeError, ValueError):
            neu += 1
            continue
        if s >= 0.2:
            pos += 1
        elif s <= -0.2:
            neg += 1
        else:
            neu += 1
    return {"pos": pos, "neu": neu, "neg": neg,
            "classified": pos + neu + neg, "spec": "sym_v1"}


def _risk_dist(report: dict[str, Any], posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {"none": 0, "low": 0, "medium": 0, "high": 0}
    for p in posts:
        level = str(p.get("risk_level") or "none").lower()
        if level not in counts:
            level = "none"
        counts[level] += 1
    if sum(counts.values()) == 0:
        level = str(report.get("risk_level") or "none").lower()
        counts[level if level in counts else "none"] = 1
    return [{"level": k, "count": counts[k], "color": RISK_COLORS[k]}
            for k in ("none", "low", "medium", "high")]


def _trend_from_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket by **real event time**. Items without a date do not enter the trend -- same metric definition as the sentinel pipeline
    (display can say "time unknown", but the trend chart must never use a fallback time, otherwise the curve is fake)."""
    buckets: dict[str, dict[str, int]] = {}
    for p in posts:
        day = str(p.get("publish_time") or "").strip()
        if not day:
            continue
        b = buckets.setdefault(day, {"bullish": 0, "neutral": 0, "bearish": 0, "mixed": 0})
        label = str(p.get("sentiment_label") or "").lower()
        if label in _POS_LABELS:
            b["bullish"] += 1
        elif label in _NEG_LABELS:
            b["bearish"] += 1
        else:
            b["neutral"] += 1
    return [{"date": day, **counts} for day, counts in sorted(buckets.items())]


def _evidence_stats(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Evidence time range and freshness distribution -- report copy relies on it to tell the truth instead of hardcoding "last 30 days"."""
    dates = sorted(d for d in (str(p.get("publish_time") or "").strip() for p in posts) if d)
    counts = {"recent": 0, "historical": 0, "unknown": 0}
    for p in posts:
        counts[str(p.get("recency") or "unknown")] = \
            counts.get(str(p.get("recency") or "unknown"), 0) + 1
    return {
        "evidence_range": {"from": dates[0] if dates else "", "to": dates[-1] if dates else ""},
        "recent_count": counts.get("recent", 0),
        "historical_count": counts.get("historical", 0),
        "undated_count": counts.get("unknown", 0),
    }


def run_sentiment_probe(user_id: int, brand: str,
                        brand_terms: Optional[List[str]] = None,
                        report_id: int | None = None,
                        industry_terms: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run one live Yuanbao WebSearch sentiment quick check, returning the old-scorer-compatible structure."""
    from geo.services.sentiment_yuanbao_websearch import run_yuanbao_websearch_sentiment

    brand = (brand or "").strip()
    if not brand:
        raise RuntimeError("品牌名为空,无法跑舆情探测")
    terms = [t.strip() for t in (brand_terms or []) if t.strip()] or [brand]

    rid = int(report_id or 0)
    started = time.time()
    acc = _get_account_snapshot(user_id, brand, terms, industry_terms)
    full_diag_log_event(
        rid, "sentiment", "yuanbao_websearch_begin", "running",
        message="开始调用元宝 WebSearch 舆情探测",
        account_id=acc.id,
        account_source="official" if acc.id else "memory",
        brand=brand,
        brand_terms_count=len(terms),
        engine="yuanbao",
    )
    log.info("full-diagnosis sentiment[%s]: yuanbao websearch begin", acc.id)

    try:
        websearch = run_yuanbao_websearch_sentiment(acc)
    except Exception as e:  # noqa: BLE001
        full_diag_log_event(
            rid, "sentiment", "yuanbao_websearch_failed", "failed", logging.ERROR,
            message="元宝 WebSearch 舆情探测失败",
            account_id=acc.id,
            duration_ms=int((time.time() - started) * 1000),
            error=str(e)[:300],
        )
        raise RuntimeError(f"元宝 WebSearch 舆情探测失败:{str(e)[:200]}") from e

    meta = websearch.get("_meta") if isinstance(websearch.get("_meta"), dict) else {}
    report = {k: v for k, v in websearch.items() if k != "_meta"}

    posts = _posts_from_websearch(report)
    stance = _stance_counts(posts)
    risk_dist = _risk_dist(report, posts)
    negative_posts = [p for p in posts
                      if (p.get("sentiment_label") or "").lower() in _NEG_LABELS
                      or (p.get("sentiment_score") is not None
                          and p["sentiment_score"] < -0.2)]
    high_risk = sum(int(r.get("count") or 0) for r in risk_dist
                    if str(r.get("level") or "").lower() in {"medium", "high"})
    try:
        avg_sentiment = float(report.get("sentiment_score") or 0.0)
    except (TypeError, ValueError):
        avg_sentiment = 0.0

    generated_at = str(report.get("generated_at") or datetime.utcnow().isoformat())
    telemetry_meta = meta.get("telemetry") if isinstance(meta.get("telemetry"), dict) else {}
    llm_meta = meta.get("llm") if isinstance(meta.get("llm"), dict) else {}
    full_diag_log_event(
        rid, "sentiment", "yuanbao_websearch_done", "ok",
        message="元宝 WebSearch 舆情探测完成",
        account_id=acc.id,
        duration_ms=int((time.time() - started) * 1000),
        query_count=report.get("query_count") or telemetry_meta.get("query_count") or 0,
        valid_results=report.get("valid_results") or telemetry_meta.get("valid_results") or 0,
        risk_level=report.get("risk_level"),
        sentiment_label=report.get("sentiment_label"),
        llm_model=llm_meta.get("model"),
    )
    log.info("full-diagnosis sentiment[%s]: yuanbao websearch done", acc.id)

    return {
        "mode": "yuanbao_websearch",
        "provider": "yuanbao",
        "brand": brand,
        "account_id": acc.id,
        "window_days": _window_days(),
        "media": ["yuanbao_websearch"],
        "monitor_stats": {
            "engine": "yuanbao",
            "query_count": report.get("query_count") or 0,
            "valid_results": report.get("valid_results") or 0,
        },
        "kpi": {
            "total_today": len(posts),
            "high_risk": high_risk,
            "avg_sentiment": avg_sentiment,
            "active_sources": len({p.get("source") for p in posts if p.get("source")}),
        },
        "risk_dist": risk_dist,
        "sentiment_trend": _trend_from_posts(posts),
        "top_posts": posts[:10],
        "posts": posts,
        "negative_posts": negative_posts[:10],
        "positive_count": stance["pos"],
        "negative_count": stance["neg"],
        **_evidence_stats(posts),
        "stance": stance,
        "websearch": report,
        "generated_at": generated_at,
    }


def cache_key_for(brand: str, brand_terms: Optional[List[str]] = None,
                  user_id: Optional[int | str] = None,
                  industry_terms: Optional[List[str]] = None) -> str:
    """24h cache key for this round of quick check.

    ★ Extracted so the "one-click diagnosis sentiment chapter" and the "single sentiment quick check" (sentiment_once.py)
      **share the same key** -- one probe of the same brand and terms hits across both entry points, without burning Yuanbao calls twice.
      Each side assembling its own identity would inevitably drift, so only this one place remains.

    ⚠️ **Must include user_id**. Before 2026-08-25 this key was built only from (brand, terms, window), so
      a brand user A ran would be hit directly by user B within 24h -- a cross-account unauthorized read,
      and the sentiment text might contain negative posts only A cares about. With user_id, the cache is no longer shared across users,
      at the cost of more Yuanbao calls, a cost worth paying.
      The anonymous side passes f"anon:{device_id}".

      Changing this requires bumping SENTIMENT_SPEC at the same time, otherwise old keys keep hitting and the change has no effect.
    """
    from geo.services import cache_service

    identity = {
        "url": brand,   # Primary slot of the cache key
        "mode": "yuanbao_websearch",
        "brand_terms": sorted(brand_terms) if brand_terms else None,
        # Category goes into the key: it changes the search terms; without it, a changed category would still hit the old wrong result
        "industry_terms": sorted(industry_terms) if industry_terms else None,
        "days": _window_days(),
        "engine": "yuanbao",
        "scope": str(user_id) if user_id is not None else "anon",
        # Structure/metric definition version: changing post shape, time criteria or dedup rules must bypass the 24h cache,
        # otherwise new code returns old-structure results as-is (2026-08-20 report 65 failed exactly like this)
        "spec": SENTIMENT_SPEC,
    }
    return cache_service.make_cache_key(
        "sentiment-probe-yuanbao", brand, extra=identity,
    )


def run_sentiment_cached(user_id: int, brand: str,
                         brand_terms: Optional[List[str]] = None,
                         report_id: int | None = None,
                         industry_terms: Optional[List[str]] = None) -> Dict[str, Any]:
    """Entry point with 24h cache + silenced stdout (same wrapper convention as advanced_runners)."""
    from geo.services import cache_service
    from geo.services.advanced_runners import _ADVANCED_TTL_S, _silent_call

    cache_key = cache_key_for(brand, brand_terms, user_id, industry_terms)
    cached = cache_service.get_cached_report(cache_key)
    if cached is not None:
        full_diag_log_event(
            int(report_id or 0), "sentiment", "cache_hit", "ok",
            message="复用 24h 元宝 WebSearch 舆情缓存",
            brand=brand,
        )
        return cached

    full_diag_log_event(
        int(report_id or 0), "sentiment", "cache_miss", "running",
        message="未命中缓存,准备现场调用元宝 WebSearch",
        brand=brand,
    )
    result = _silent_call(run_sentiment_probe, user_id, brand,
                          brand_terms=brand_terms, report_id=report_id,
                          industry_terms=industry_terms)
    if isinstance(result, dict):
        cache_service.set_cached_report(cache_key, result, ttl_s=_ADVANCED_TTL_S)
    return result
