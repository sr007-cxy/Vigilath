"""report_basis_changes -- detects "metric definition change" markers for trend charts.

Why this module exists: report center trend charts plot **ratios** (exposure rate = category-word hits / valid category-word responses),
but the base of numerator and denominator gets changed by ops actions -- adding/removing prompts, engines onboarded or stopped, metric definition upgrades.
At those points the curve jumps a level unrelated to optimization. Users see "it dropped a chunk" and read it as a score loss.
This module detects those points so the trend chart draws a vertical line: "metric definition changed here; don't compare the two sides directly".

**Takes no part in any metric computation** -- only reads already-computed wide-table counts and the topic changelog; no curve value changes.

Four marker kinds:
  queries  prompt word list add/remove          <- selected_queries / queries in topic_changelog_json
  scope    metric definition itself adjusted    <- changelog reclassify_queries + wide-table schema_version boundaries
  engines  engine set changed                   <- per-engine wide-table rows' engine set compared period over period
  denom    sample base jump (fallback)          <- adjacent periods' **displayed** denominator jumps; only when the first three recorded no cause

★ The former fifth kind, floor (response-denominator floor switch), was removed with the 2026-08-31 **pinned denominator**: that line marked
  the period where "actual runs crossed the floor and the denominator switched from floor to actual", but once pinned the denominator always equals the configured value,
  so that switch point no longer exists; drawing it would mark a metric definition change that never happened.

Returns **structured data, not sentences** -- all Chinese/English copy is rendered by frontend lang.ts
(see memory: missing i18n keys leak raw key names / Chinese copy must not mix in English terms).

All imports are lazy inside functions: ai_telemetry_reports -> this module; a top-level import would create a cycle.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

# Denominator jump rule: relative change >= 25% and absolute change >= 10 counts as a "jump"; otherwise it's normal fetch fluctuation
DENOM_JUMP_RATIO = 0.25
DENOM_JUMP_MIN = 10
# Max markers per chart -- when exceeded keep the most recent, so it doesn't look like a fence
MAX_MARKERS = 6

KINDS = ("queries", "scope", "engines", "denom")
_CHANGELOG_FIELD_KIND = {
    "selected_queries": "queries",     # after="selected_count=N"
    "queries": "queries",              # after="added=N"
    "reclassify_queries": "scope",     # scene re-tagging -> membership of the category-word metric definition changed
    "delete_queries": "queries",       # soft delete/restore prompts -> denominator base changed, curve jumps a level
}


def detect(db, topic, stats, period_type: str, keys: list[str]) -> list[dict]:
    """-> [{key, day, kind, before, after, engines_added, engines_removed}] (sorted by key ascending).

    keys  = period keys the endpoint will actually draw (markers only inside the visible window; changes outside don't bother the user)
    stats = return of _wide_stats() {day: {engine: counts}}; None = fell back to read-time aggregation,
            in which case only changelog markers are emitted (engine set/denominator need wide-table counts)
    """
    if not keys:
        return []
    key_set = set(keys)
    out: list[dict] = []
    out += _changelog_marks(topic, period_type, key_set)
    if stats is not None:
        out += _schema_boundary_marks(db, topic, period_type, key_set)
        if not _is_demo_topic(db, topic):
            # demo topics' wide tables are hand-made data (see memory: demo topic t19 single data source);
            # data-driven engine-set/denominator detection has no business meaning on fabricated rows and only adds spurious lines
            out += _engine_marks(stats, period_type, keys)
            # Floor value: if configured, the denominator is **pinned**. Pass it to denom detection so it looks at the **displayed denominator**
            # rather than raw actual runs -- once pinned the displayed denominator is constant, so no denom jump is ever emitted
            # (exactly what the old "skip entirely when a fixed denominator is configured" wanted, now done right rather than switched off);
            # for topics without a floor the displayed denominator is the actual runs and jump detection works as usual.
            out += _denom_marks(stats, period_type, keys, {m["key"] for m in out},
                                _fixed_floor(topic, period_type))
    # Dedupe by (key, kind) (e.g. two checkbox clicks in one day); sort by key ascending, keep the most recent when over the cap
    dedup: dict[tuple[str, str], dict] = {}
    for m in out:
        dedup[(m["key"], m["kind"])] = m
    marks = sorted(dedup.values(), key=lambda m: (m["key"], m["day"]))
    return marks[-MAX_MARKERS:]


def _mark(key: str, day: str, kind: str, *, before: Optional[float] = None,
          after: Optional[float] = None, engines_added: Optional[list[str]] = None,
          engines_removed: Optional[list[str]] = None) -> dict:
    return {
        "key": key, "day": day, "kind": kind,
        "before": before, "after": after,
        "engines_added": engines_added or [],
        "engines_removed": engines_removed or [],
    }


# ───────────────────────── changelog (word list / metric definition rules) ─────────────────────


def _int_after(text: Optional[str], prefix: str) -> Optional[int]:
    """Extract the number from a changelog after string: "selected_count=62" -> 62."""
    if not text:
        return None
    for part in str(text).split():
        if part.startswith(prefix):
            try:
                return int(part[len(prefix):])
            except ValueError:
                return None
    return None


def _changelog_marks(topic, period_type: str, key_set: set[str]) -> list[dict]:
    """topic_changelog_json -> word list / metric-definition rule change points.

    before is "selected_count of the previous entry of the same kind" -- the whole log must be traversed (not just the visible window),
    otherwise the first marker in the window has no pre-change base.
    """
    from geo.api.ai_telemetry_reports import _GROUPERS, _bj_date

    try:
        entries = json.loads(getattr(topic, "topic_changelog_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(entries, list):
        return []

    grouper = _GROUPERS[period_type]
    marks: list[dict] = []
    prev_selected: Optional[int] = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        kind = _CHANGELOG_FIELD_KIND.get(e.get("field") or "")
        if not kind:
            continue
        at = _parse_at(e.get("at"))
        if at is None:
            continue
        selected = _int_after(e.get("after"), "selected_count=")
        before, after = None, None
        if selected is not None:
            before, after = prev_selected, selected
            prev_selected = selected
        elif e.get("field") == "queries":
            added = _int_after(e.get("after"), "added=")
            if added:                      # added=0 is not a change
                before = prev_selected
                after = (prev_selected + added) if prev_selected is not None else None
        day = _bj_date(at)
        key = grouper(day)
        if key not in key_set:
            continue
        if kind == "queries":
            if before is None:
                continue                   # first word-list entry in the log = initial setup at project creation, not a "change"
            if before == after:
                continue                   # selected count unchanged (e.g. only order changed); not a metric definition change
        marks.append(_mark(key, day.isoformat(), kind, before=before, after=after))
    return marks


def _parse_at(raw) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


# ───────────────────────── Wide-table schema version boundary ──────────────────────────


def _schema_boundary_marks(db, topic, period_type: str, key_set: set[str]) -> list[dict]:
    """If the wide table has rows of an older metric-definition version -> place a "computation definition adjusted" marker on the current version's first day.

    Note _wide_stats filters out old-version rows entirely, so those days **disappear** from the curve rather than distort --
    the marker lands on the new version's first day, hinting "earlier history used a different metric definition".
    """
    from sqlalchemy import func

    from geo.api.ai_telemetry_reports import _GROUPERS
    from geo.models.ai_telemetry import (
        REPORT_DAILY_SCHEMA_VERSION, AiTelemetryReportDailyORM,
    )

    try:
        rows = (
            db.query(AiTelemetryReportDailyORM.schema_version,
                     func.min(AiTelemetryReportDailyORM.day))
              .filter(AiTelemetryReportDailyORM.topic_id == topic.id)
              .group_by(AiTelemetryReportDailyORM.schema_version)
              .all()
        )
    except Exception:  # noqa: BLE001  deploy ordering: columns/tables not migrated yet; don't crash the endpoint
        db.rollback()
        return []
    cur_first = next((d for v, d in rows if int(v or 0) == REPORT_DAILY_SCHEMA_VERSION), None)
    older = [d for v, d in rows if int(v or 0) < REPORT_DAILY_SCHEMA_VERSION and d]
    if not cur_first or not older or min(older) >= cur_first:
        return []
    key = _GROUPERS[period_type](date.fromisoformat(cur_first))
    if key not in key_set:
        return []
    return [_mark(key, cur_first, "scope")]


def _fixed_floor(topic, period_type: str) -> Optional[int]:
    """Effective floor value for this topic at this granularity; returns None when not applicable.

    The floor only applies at cross-period granularities (daily is a same-day snapshot); the definition must match the report center's
    ai_telemetry_reports.FLOOR_PERIOD_TYPES.
    """
    try:
        from geo.api.ai_telemetry_reports import FLOOR_PERIOD_TYPES
        from geo.models.ai_telemetry import fixed_response_count
    except ImportError:                        # old deployments have no floor definition
        return None
    if period_type not in FLOOR_PERIOD_TYPES:
        return None
    return fixed_response_count(topic) or None


def _is_demo_topic(db, topic) -> bool:
    """Whether this topic's wide table is hand-made data (source='demo')."""
    from geo.models.ai_telemetry import AiTelemetryReportDailyORM

    try:
        return db.query(
            db.query(AiTelemetryReportDailyORM)
              .filter(AiTelemetryReportDailyORM.topic_id == topic.id)
              .filter(AiTelemetryReportDailyORM.source == "demo")
              .exists()
        ).scalar() or False
    except Exception:  # noqa: BLE001
        db.rollback()
        return False


# ───────────────────────── Wide-table data driven (engine set / denominator) ──────────────────


def _by_period(stats: dict, period_type: str, keys: list[str]) -> dict[str, list[str]]:
    """Period key -> list of Beijing days in that period (ascending), keeping only keys in the visible window."""
    from geo.api.ai_telemetry_reports import _GROUPERS

    grouper = _GROUPERS[period_type]
    key_set = set(keys)
    out: dict[str, list[str]] = defaultdict(list)
    for day in stats:
        k = grouper(date.fromisoformat(day))
        if k in key_set:
            out[k].append(day)
    return {k: sorted(v) for k, v in out.items()}


def _engine_marks(stats: dict, period_type: str, keys: list[str]) -> list[dict]:
    """Compare engine sets period by period -> engine onboarded / stopped.

    Compares "current set vs the previous **stable** set", not vs the previous period -- a single-day total fetch failure for one engine is common,
    and using the previous period as baseline would draw a line both on the drop day and the recovery day. At daily granularity the new set
    must also "still hold in the next period" to count as stable; anything that doesn't last a day is discarded as fetch noise.
    """
    from geo.api.ai_telemetry_reports import ALL_ENGINE

    days_of = _by_period(stats, period_type, keys)
    ordered = [k for k in keys if k in days_of]
    sets: dict[str, set[str]] = {
        k: {e for d in days_of[k] for e in stats[d] if e != ALL_ENGINE}
        for k in ordered
    }

    def _persists(i: int) -> bool:
        if period_type != "daily" or i + 1 >= len(ordered):
            return True
        return sets[ordered[i + 1]] == sets[ordered[i]]

    marks: list[dict] = []
    stable = sets[ordered[0]] if ordered else set()
    for i in range(1, len(ordered)):
        cur = sets[ordered[i]]
        if cur == stable or not cur or not stable or not _persists(i):
            continue
        marks.append(_mark(
            ordered[i], days_of[ordered[i]][0], "engines",
            engines_added=sorted(cur - stable), engines_removed=sorted(stable - cur),
        ))
        stable = cur
    return marks


def _closed_key(keys: list[str], period_type: str) -> Optional[str]:
    """Latest **closed** period key (same meaning as report_metrics_source.closed_key).

    The wide-table boundary is always yesterday, but the live tail brings "today/this week/this month" into keys -- unclosed periods
    have inherently incomplete denominators and can't be used to judge jumps. This is implemented locally rather than importing the data adapter: this module runs
    on the report center's main path, and every extra module dependency is another "deploy missing a file -> whole report list 500"
    risk (it actually blew up once on 2026-08-07).
    """
    from datetime import datetime

    from geo.api.ai_telemetry_reports import BJ_OFFSET, _GROUPERS

    if not keys:
        return None
    cur = _GROUPERS[period_type]((datetime.utcnow() + BJ_OFFSET).date())
    closed = [k for k in keys if k < cur]
    return closed[-1] if closed else None


def _raw_denoms(stats: dict, days_of: dict, ordered: list[str]) -> dict[str, int]:
    """Per-period **actual** category-word denominator (no floor applied)."""
    from geo.api.ai_telemetry_reports import ALL_ENGINE

    return {
        k: sum(int(stats[d].get(ALL_ENGINE, {}).get("cat_valid_count") or 0)
               for d in days_of[k])
        for k in ordered
    }


def _denom_marks(stats: dict, period_type: str, keys: list[str],
                 taken: set[str], floor: Optional[int] = None) -> list[dict]:
    """Category-word denominator jumps between adjacent periods -- fallback for causes not explained by the first three kinds.

    Skips the first period of the series; skips the **unclosed current period** (live tail's today denominator is inherently incomplete, otherwise false alarms daily);
    not emitted when the same period already has another marker (the cause is already explained).

    When floor is non-empty it looks at the **displayed denominator**, which once pinned always equals floor -- identical every period,
    so no marker is ever emitted. That's correct: the denominator didn't change, so curve movement is real movement.
    """
    days_of = _by_period(stats, period_type, keys)
    ordered = [k for k in keys if k in days_of]
    closed = _closed_key(ordered, period_type)
    if closed is None:
        return []
    raw = _raw_denoms(stats, days_of, ordered)
    denom = {k: (floor if floor else v) for k, v in raw.items()}
    marks: list[dict] = []
    for i in range(1, len(ordered)):
        k, prev_k = ordered[i], ordered[i - 1]
        if k > closed or k in taken:
            continue
        prev, cur = denom[prev_k], denom[k]
        if prev <= 0:
            continue
        diff = abs(cur - prev)
        if diff < DENOM_JUMP_MIN or diff / prev < DENOM_JUMP_RATIO:
            continue
        marks.append(_mark(k, days_of[k][0], "denom", before=float(prev), after=float(cur)))
    return marks


__all__ = ["detect", "KINDS", "DENOM_JUMP_RATIO", "DENOM_JUMP_MIN", "MAX_MARKERS"]
