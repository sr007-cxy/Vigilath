"""Degradation labeling — when the main path is unavailable, how "this result is degraded" travels all the way to the user.

**The opposite of a fallback is not an error, it's fabricated data.** After a path fails, the most dangerous output isn't "couldn't run",
it's a report that "looks complete but every number is a guess" — the user takes it to a meeting and we can't afford that.

So four **mechanisms** are defined here (not reminders in the prompt; prompts can't stop the model):

  1. Degraded conclusions must carry `evidence_ref` (index into the evidence list); those without are **dropped** in `wrap()`,
     and counted in `dropped_unsourced` for truthful reporting — say how many were dropped, never quietly give less.
  2. Degraded results **produce no numeric metrics**: exposure rate / hit rate / five-dimension scores are all null + 「未检测」 ("not audited").
     Never back-infer scores from search results — that's the textbook way to fabricate data.
  3. When `data_quality != "full"`, **writing to any persisted metric table is forbidden**. Once degraded values are persisted to DB,
     every later report treats them as real data in calculations; the pollution is irreversible.
  4. The card layer must carry the `degraded` flag; the frontend renders a yellow top bar — visible even if the model omits it in the body.

The three fields have the same name across the whole chain (tool return -> SSE cards -> report JSON -> frontend); no per-place renaming.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

FULL = "full"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"

# Keys outside the numeric metric allowlist are all nulled in degraded results.
# These are metric definitions that "can only be computed by a real batch run"; search evidence can't derive them.
NUMERIC_METRIC_KEYS = (
    "露出率", "整体露出率", "TOP1露出率", "TOP3露出率", "内容中正率",
    "文章引用率", "品牌声量度", "命中率", "覆盖率", "五维评分", "总分",
    "exposure_rate", "top1_rate", "top3_rate", "positive_rate",
    "citation_rate", "voice_share", "hit_rate", "score",
)


@dataclass
class Degraded:
    level: str = DEGRADED
    missing: list[str] = field(default_factory=list)   # Which items failed to measure
    reason: str = ""                                   # Human-readable, shown to the user
    evidence_provider: str = ""                        # Who provided the evidence (tencent / yuanbao)


def upstream_down(exc_or_result: Any) -> Optional[Degraded]:
    """The **only** "is the main path down" criterion function.

    Don't write separate try/excepts in each tool — once the criterion scatters, you get "this path thinks it's down,
    that one thinks it isn't", and one outage yields reports with two metric definitions.

    It judges the **mechanism**, not exception text: text changes with upstream revisions, mechanisms don't.
    """
    import httpx

    if exc_or_result is None:
        return Degraded(reason="上游没有返回任何内容")
    if isinstance(exc_or_result, httpx.HTTPError):
        return Degraded(reason="检测服务暂时连不上")
    if isinstance(exc_or_result, Exception):
        return Degraded(reason=f"检测服务异常({type(exc_or_result).__name__})")
    if isinstance(exc_or_result, dict):
        # Upstream wraps failures in a normal return body (more common than half of the exceptions)
        if exc_or_result.get("error") or exc_or_result.get("failed"):
            return Degraded(reason=str(exc_or_result.get("error") or "上游返回失败"))
        if exc_or_result.get("valid_results") == 0:
            return Degraded(reason="一条有效结果都没拿到")
    if isinstance(exc_or_result, list) and not exc_or_result:
        return Degraded(reason="上游返回空列表")
    return None


def _strip_numeric(obj: Any) -> Any:
    """Null out all numeric metrics in degraded results. Recursive — metrics are often nested in substructures."""
    if isinstance(obj, dict):
        return {
            k: (None if k in NUMERIC_METRIC_KEYS else _strip_numeric(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_numeric(x) for x in obj]
    return obj


def wrap(result: dict, deg: Optional[Degraded],
         *, conclusions_key: str = "conclusions") -> dict:
    """Attach the three same-named fields to the result; when degraded, apply mechanisms 1 and 2 above."""
    out = dict(result or {})
    if deg is None:
        out.setdefault("data_quality", FULL)
        return out

    out = _strip_numeric(out)
    out["data_quality"] = deg.level
    out["degraded_notice"] = (
        (deg.reason or "部分数据没测成") +
        ("。以下结论只基于公开检索到的证据,不含 AI 引擎实测。"
         if deg.level == DEGRADED else "。这一项这次没有可用数据。")
    )
    out["degraded_missing"] = list(deg.missing)
    if deg.evidence_provider:
        out["evidence_provider"] = deg.evidence_provider

    # Drop unsourced conclusions outright and report truthfully how many were dropped
    items = out.get(conclusions_key)
    if isinstance(items, list):
        kept = [c for c in items
                if isinstance(c, dict) and c.get("evidence_ref") is not None]
        dropped = len(items) - len(kept)
        out[conclusions_key] = kept
        if dropped:
            out["dropped_unsourced"] = dropped
    return out


def is_degraded(result: Any) -> bool:
    return isinstance(result, dict) and result.get("data_quality") not in (None, FULL)
