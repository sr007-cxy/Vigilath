"""Monthly sentiment monitoring summary generator.

Complements the daily brief (generate.py): the daily is a 24h rolling window + 3-day freshness, covering "what happened today";
the monthly aggregates the **full calendar month**, covering "monthly trend + key events + month-over-month change", for marketing reviews and reporting.

Design principles (inheriting the competitor module's lesson of "deterministic stitching + separate LLM calls"):
- Hard numbers (totals / sentiment distribution / weekly trend / MoM / top entities and topics) are all computed **deterministically** in code;
  the LLM only gets the aggregates + top-N posts to write the narrative, never counting data itself;
- The weekly trend table is rendered as a markdown table in code (PDF export cannot use flex, tables are most stable), embedded verbatim by the LLM;
- Six sections stitched deterministically, rather than one reduce producing the whole text and dropping sections.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date as _date
from pathlib import Path

from storage import (connect, init_schema, analyses_for_month, insert_brief)
from llm_client import chat_create, has_openai, has_qwen
from .generate import (_post_id, _row_to_full, _noteworthy_score,
                       REDUCE_MODEL, REDUCE_MAX_TOKENS)
from storage.llm_usage import set_feature

TOP_TOPICS = 15
TOP_ENTITIES = 15
TOP_NOTEWORTHY = 15
COMPETITOR_LOOKUP = 30


# ── Utilities ────────────────────────────────────────────────────────────────────
def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _week_of_month(publish_time: str) -> int | None:
    """'YYYY-MM-DD...' -> week of the month (by date: 1-7 = week 1, 8-14 = week 2 ...)."""
    try:
        day = int((publish_time or "")[8:10])
    except (ValueError, TypeError):
        return None
    return (day - 1) // 7 + 1 if day else None


def _matches_terms(r: dict, terms: list[str]) -> bool:
    hay = ((r.get("title") or "") + " " + (r.get("content") or "")).lower()
    return any(t.lower() in hay for t in terms)


def _entity_name(e) -> str | None:
    if isinstance(e, dict):
        return e.get("name") or e.get("entity")
    return e if isinstance(e, str) else None


# ── Deterministic aggregation ───────────────────────────────────────────────────────────────
def _aggregate(rows: list[dict]) -> dict:
    """Compute deterministic stats from a month's relevant posts."""
    rel = [r for r in rows if r["is_relevant"]]
    n = len(rel)
    sent = Counter(r["sentiment_label"] or "unknown" for r in rel)
    risk = Counter(r["risk_level"] or "none" for r in rel)
    scores = [float(r["sentiment_score"]) for r in rel if r["sentiment_score"] is not None]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    neg_ratio = round(sent.get("bearish", 0) / n, 3) if n else 0.0

    topic_counts: Counter[str] = Counter()
    entity_counts: Counter[str] = Counter()
    for r in rel:
        for t in json.loads(r["topics_json"] or "[]"):
            if t:
                topic_counts[str(t)] += 1
        for e in json.loads(r["entities_json"] or "[]"):
            name = _entity_name(e)
            if name:
                entity_counts[name] += 1

    # Weekly trend
    weeks: dict[int, dict] = {}
    for r in rel:
        w = _week_of_month(r.get("publish_time") or "")
        if w is None:
            continue
        b = weeks.setdefault(w, {"vol": 0, "neg": 0, "ssum": 0.0, "sn": 0})
        b["vol"] += 1
        if r["sentiment_label"] == "bearish":
            b["neg"] += 1
        if r["sentiment_score"] is not None:
            b["ssum"] += float(r["sentiment_score"])
            b["sn"] += 1
    weekly = [
        {"week": w, "volume": b["vol"], "negatives": b["neg"],
         "avg_score": round(b["ssum"] / b["sn"], 3) if b["sn"] else 0.0}
        for w, b in sorted(weeks.items())
    ]

    # High-risk day timeline
    high_days: Counter[str] = Counter()
    for r in rel:
        if r["risk_level"] == "high" and r.get("publish_time"):
            high_days[r["publish_time"][:10]] += 1

    return {
        "month_volume": n,
        "sentiment_counts": dict(sent),
        "risk_counts": dict(risk),
        "avg_sentiment_score": avg_score,
        "negative_ratio": neg_ratio,
        "top_topics": topic_counts.most_common(TOP_TOPICS),
        "top_entities": entity_counts.most_common(TOP_ENTITIES),
        "weekly_trend": weekly,
        "high_risk_days": sorted(high_days.items()),
        "_topic_set": set(topic_counts),   # internal use only (MoM emerging/faded); removed before sending to the LLM
    }


def _mom_delta(cur: dict, prev: dict | None) -> dict:
    """This month vs last month. If last month is empty (first month) -> has_baseline=False and each item is None."""
    def pct(c: float, p: float):
        return round((c - p) / p * 100, 1) if p else None

    cur_topics = cur.get("_topic_set") or set()
    prev_topics = (prev.get("_topic_set") if prev else None) or set()
    if not prev:
        return {"has_baseline": False,
                "high_risk_cur": cur["risk_counts"].get("high", 0),
                "emerging_topics": [], "faded_topics": []}
    return {
        "has_baseline": True,
        "volume_prev": prev["month_volume"],
        "volume_change_pct": pct(cur["month_volume"], prev["month_volume"]),
        "avg_score_prev": prev["avg_sentiment_score"],
        "avg_score_shift": round(cur["avg_sentiment_score"] - prev["avg_sentiment_score"], 3),
        "neg_ratio_prev": prev["negative_ratio"],
        "neg_ratio_shift": round(cur["negative_ratio"] - prev["negative_ratio"], 3),
        "high_risk_prev": prev["risk_counts"].get("high", 0),
        "high_risk_cur": cur["risk_counts"].get("high", 0),
        "emerging_topics": sorted(cur_topics - prev_topics)[:10],
        "faded_topics": sorted(prev_topics - cur_topics)[:10],
    }


def _weekly_table_md(weekly: list[dict]) -> str:
    """Render the weekly trend as a markdown table (deterministic, embedded verbatim by the LLM so the numbers are not rewritten)."""
    if not weekly:
        return "_本月无可分周的发布数据_"
    lines = ["| 周次 | 讨论量 | 负面数 | 平均情感分 |", "|---|---|---|---|"]
    for w in weekly:
        lines.append(f"| 第{w['week']}周 | {w['volume']} | {w['negatives']} | {w['avg_score']} |")
    return "\n".join(lines)


# ── LLM sections ─────────────────────────────────────────────────────────────────
MONTHLY_REDUCE_SYSTEM = """你是资深舆情分析师,为公司**市场部**撰写"月度舆情汇总",用于月度复盘和向上汇报。

输入字段:
- month_stats:整月确定值统计(总量、情感分布、风险分布、平均情感分、负面占比、top_topics、top_entities、high_risk_days)
- mom:环比上月(has_baseline=false 表示首月无可比基线)
- weekly_trend_table:已渲染好的周走势 markdown 表格,**请在第二部分原样嵌入,不要改动其中任何数字**
- posts:整月最值得关注的帖子查询表,引用时只能从这里取 title / url / author

生成 Markdown,严格如下结构:

# {symbol} 舆情月报 · {year}年{month}月

## 一、月度概览
- 用 month_stats 给的数:整月讨论量、情感占比(看多/看空/中性)、平均情感分、负面占比
- 若 mom.has_baseline=true,给出环比上月(量 ±%、情感分漂移、负面占比变化),用中文写清方向
- 一句话定性整月舆情基调

## 二、趋势变化
- **先原样嵌入 weekly_trend_table 表格**
- 再 2-4 句解读走势:哪周冲高/回落、负面集中在何时、月内是走强还是走弱
- 若有 mom.emerging_topics / faded_topics,点出"本月新冒出的话题"与"上月有本月消退的话题"

## 三、重点舆情事件
从 posts 里提炼整月 3-6 个**重点事件/议题**(不是逐条复述单帖,是归并成事件):
- 每个:一句话说清事件 + 影响/信号 + 主导情感(中文),引用 1 条代表帖 [标题](url)

## 四、风险回顾
- 用 month_stats.risk_counts 给中/高风险数量;若 high_risk_days 非空,列出高风险日期
- 若 mom.has_baseline,说明高风险数量较上月升/降
- 一句研判:风险是否在收敛,有无需要持续盯防的点
- 全月无中/高风险则如实写"本月未见中风险及以上信号"

## 五、复盘建议
面向市场部下月动作,给 1-3 条**具体可执行**建议(做什么+针对哪个发现+为何),按优先级:
- 要能落地(如"就 X 议题准备一版对外口径""把 Y 利好做成案例对外传播")
- ❌ 不写"持续关注""保持监测"这类空话;确实无需动作就写"下月无需特别动作"

(竞品动态为独立第六部分,由系统单独生成拼接,本段不要输出。)

## 写作要求
- 口语化但专业,面向市场部,突出"可复盘、可汇报"
- 所有数字只能用输入给的,**不得编造**任何统计或与历史的对比(mom 没给的就不写环比)
- 每处提到具体帖子/事件都要带可点击 `[标题](url)`,url 从 posts 取,不贴裸 url、不截断
- **情感/风险一律中文**:bullish→看多、bearish→看空、neutral→中性、mixed→复杂;low→低风险、medium→中风险、high→高风险。绝不出现英文词或中英混写
- 整篇控制在 800-1400 字
"""

MONTHLY_COMPETITOR_SYSTEM = """你是竞品情报分析师,为 {symbol} 的**月度**舆情汇总补充竞品动态模块。
输入 competitor_posts 是本月竞品相关帖(title/url/author/publish_time/sentiment/risk/summary)。
只输出 Markdown,从标题行 `## 六、竞品动态` 开始,不要别的客套。

按**竞品公司分组**(公司名从帖子标题/正文判断),每家一小节:
- **{公司名}**:一句话本月动态概括
- 覆盖能找到的维度(找不到的省略,不硬编):舆情风向 / 财报业绩 / 资本动作(并购·融资·股权) /
  市场需求信号(资本开支·新建数据中心·大客户签约·算力AI需求·产能扩张)
- 每条结论引用 1 条关键帖 [标题](url)(url 只能从 competitor_posts 取,不编造、不截断)
- 末尾一句**横向研判**:本月竞品整体动向对 {symbol} 的启示

要求:中文;情感/风险用中文(看多/看空/中性、低/中/高风险);简洁专业 200-500 字;不编造帖子或链接。"""


def _call_monthly_reduce(symbol, year, month, stats, mom, weekly_table, posts_lookup, model):
    payload = {
        "symbol": symbol, "year": year, "month": month,
        "month_stats": stats, "mom": mom,
        "weekly_trend_table": weekly_table, "posts": posts_lookup,
    }
    user = (
        f"为 {symbol} 撰写 {year}年{month}月 的月度舆情汇总。聚合数据如下:\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    set_feature("sentinel.brief.monthly")   # for billing: tag which feature this call belongs to
    resp = chat_create(
        model, max_completion_tokens=REDUCE_MAX_TOKENS,
        messages=[{"role": "system", "content": MONTHLY_REDUCE_SYSTEM},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


def _call_monthly_competitor(symbol, year, month, competitor_posts, model):
    payload = {"symbol": symbol, "year": year, "month": month,
               "competitor_posts": competitor_posts}
    user = (
        f"为 {symbol} 在 {year}年{month}月 生成竞品动态月度模块。竞品相关帖如下:\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    set_feature("sentinel.brief.monthly_competitor")   # for billing: tag which feature this call belongs to
    resp = chat_create(
        model, max_completion_tokens=REDUCE_MAX_TOKENS,
        messages=[{"role": "system",
                   "content": MONTHLY_COMPETITOR_SYSTEM.format(symbol=symbol)},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


def _empty_brief_md(symbol, year, month) -> str:
    return (f"# {symbol} 舆情月报 · {year}年{month}月\n\n"
            f"## 一、月度概览\n\n本月未采集到 {symbol} 的相关舆情(发布时间落在本月的相关帖为 0),"
            f"无可汇总的重点事件与趋势。\n")


# ── Entry point ────────────────────────────────────────────────────────────────────
def generate_monthly_brief(symbol: str, year: int, month: int,
                           out_dir: Path | None = None,
                           competitors: list[str] | None = None) -> Path:
    """Generate the monthly summary for {symbol} in year-month, store it (kind=monthly) + write to disk, and return the file path."""
    conn = connect()
    init_schema(conn)

    rows = analyses_for_month(conn, symbol, year, month)
    comp_terms = [c.strip() for c in (competitors or []) if c and c.strip()]

    if not any(r["is_relevant"] for r in rows):
        body = _empty_brief_md(symbol, year, month)
    else:
        if not (has_openai() or has_qwen()):
            sys.exit("no LLM key available. Set OPENAI_API_KEY (sk-...) or QWEN_API_KEY.")

        # Competitor split: competitor posts are removed from the main subject and form part six on their own (same metric definition as the daily)
        competitor_rows: list = []
        if comp_terms:
            competitor_rows = [r for r in rows if _matches_terms(r, comp_terms)]
            comp_ids = {_post_id(r) for r in competitor_rows}
            rows = [r for r in rows if _post_id(r) not in comp_ids]

        # MoM vs last month (competitors removed as well, same metric definition)
        py, pm = _prev_month(year, month)
        prev_rows = analyses_for_month(conn, symbol, py, pm)
        if comp_terms:
            prev_rows = [r for r in prev_rows if not _matches_terms(r, comp_terms)]

        cur_stats = _aggregate(rows)
        prev_stats = _aggregate(prev_rows) if any(r["is_relevant"] for r in prev_rows) else None
        mom = _mom_delta(cur_stats, prev_stats)
        weekly_table = _weekly_table_md(cur_stats["weekly_trend"])

        # Top-N key posts (deterministic influence x sentiment intensity ranking)
        ranked = sorted((r for r in rows if r["is_relevant"]),
                        key=_noteworthy_score, reverse=True)
        posts_lookup = [_row_to_full(r) for r in ranked[:TOP_NOTEWORTHY]]

        # Remove internal sets before sending to the LLM (not JSON serializable)
        for s in (cur_stats, prev_stats):
            if s:
                s.pop("_topic_set", None)

        body = _call_monthly_reduce(symbol, year, month, cur_stats, mom,
                                    weekly_table, posts_lookup, REDUCE_MODEL)

        # Competitor activity: separate LLM call, part six appended deterministically
        if competitor_rows:
            competitor_rows.sort(key=lambda r: (r.get("publish_time") or ""), reverse=True)
            comp_lookup = [_row_to_full(r) for r in competitor_rows[:COMPETITOR_LOOKUP]]
            comp_md = _call_monthly_competitor(symbol, year, month, comp_lookup, REDUCE_MODEL)
            if comp_md:
                body = body.rstrip() + "\n\n" + comp_md

    # The monthly report is stored under the first day of the month + kind=monthly, distinguished from dailies in the same table
    month_start = f"{int(year):04d}-{int(month):02d}-01"
    insert_brief(conn, symbol, month_start, body, REDUCE_MODEL, kind="monthly")
    conn.commit()

    out_dir = out_dir or Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"monthly_{symbol}_{int(year):04d}-{int(month):02d}.md"
    out_path.write_text(body, encoding="utf-8")
    print(f"[monthly] {symbol} {year}-{month:02d} → {out_path}")
    return out_path
