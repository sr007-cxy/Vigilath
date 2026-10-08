"""Report center data adapter layer -- the single entry point for pages **outside** the report center to read the same metrics.

Why this layer exists: the report center's (`geo.api.ai_telemetry_reports`) six metrics carry a whole set of metric definitions --
valid-response filtering, negative-verdict window, own-URL citation verdict, category-word counts (cat_*), Beijing-day bucketing,
demo topic anchor values. Any aggregation hand-written elsewhere won't match the page. Historically the agent side
(`agents/geo/report_source.py`, 2026-07) and the brand growth side (`geo/api/ai_telemetry.py`,
2026-08) each fell into the same pit once; now it's all consolidated here.

★ Pitfall 1: the `excl` parameter of `_wide_stats` / `_metrics_for` defaults to empty; **omitting it raises no error but
  exposure / TOP1 / TOP3 are silently inflated**. All data access must go through `load_stats()`, which forces computing excl.
  Callers must not call `_wide_stats` themselves.

★ Pitfall 2: share of voice v3 switches the numerator per topic via contextvar `_mention_cat_ctx`; the report center sets it in
  `_topic_scoped()`. Getting the topic elsewhere via `_get_topic_or_404` doesn't set it, so t19's
  brand share of voice falls short of the page. `load_stats()` sets it on your behalf.

★ Pitfall 4 (added 2026-08-17): the response-denominator **floor** is also a contextvar, also set in `_topic_scoped()`.
  It used to be missed here, so the report center used the floor definition while overview / brand growth / industry benchmark / agent used the real one,
  giving two sets of numbers for one topic (agent says not met, report page says met). Now `load_stats()` sets it too,
  and all public read functions run inside `Prepared.scope()` -- **when one request loops over multiple topics** (industry benchmark P50,
  public industry library page) this scope must reset it, otherwise the previous topic's floor leaks into the next.
  Exception: industry benchmark P50 is a cross-tenant aggregate; callers must explicitly use `no_fixed_denominator()` to suppress the floor.

★ Pitfall 5: granularity is metric definition. The floor applies only to weekly/monthly; daily always uses the real denominator -- when fetching,
  `period_type` must be passed through as-is; don't call `metrics_for(rows)` without granularity yourself (that's the no-floor definition).

★ Pitfall 3: "latest period" = `keys[-1]`, **including the not-yet-closed current period** (live tail brings today/this week
  into keys). The report center home KPI cards take it this way (`kpi_summary` L819); to match the numbers
  you must copy that, not decide on your own to fall back a period -- for "closed" semantics use `closed_key()` explicitly.

All imports are lazy (inside functions): `ai_telemetry_reports` -> import -> `ai_telemetry`,
and this module is used by both sides; a top-level import would create a cycle.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Optional

from sqlalchemy.exc import DatabaseError

# Callers must never rewrite metric Chinese names: always export them from the report center (see feedback_metric_names_from_lang_dict)
METRIC_ORDER = ("exposure", "top1", "top3", "accuracy", "citation", "mention")


def metric_labels() -> dict[str, str]:
    from geo.api.ai_telemetry_reports import METRIC_LABELS

    return dict(METRIC_LABELS)


def _supports_excl() -> bool:
    """Whether the deployed report center uses "category-word definition v2" (exposure/TOP1/TOP3 exclude brand words and competitor-comparison words).

    Only v2 has _metric_excl_queries, and only then do _wide_stats/_metrics_for accept the excl parameter.
    Callers' definition must follow the **deployed** report center: v2 passes excl, v1 doesn't (the v1 page doesn't exclude anyway),
    otherwise the computed numbers won't match the page the user is looking at.
    """
    from geo.api import ai_telemetry_reports as R

    return hasattr(R, "_metric_excl_queries")


class Prepared:
    """Per-request data context. Can be unpacked as `stats, owned, neg, excl = prepared` (backward compatible).

    `rows()` is the full-table-scan result for the fallback path (no wide table), lazy-loaded + cached on this object -- multiple fetches
    in the same request (e.g. overview + per-engine slices) scan only once.
    """

    __slots__ = ("db", "topic", "stats", "owned", "neg", "excl", "_rows")

    def __init__(self, db, topic, stats, owned, neg, excl):
        self.db = db
        self.topic = topic
        self.stats = stats
        self.owned = owned
        self.neg = neg
        self.excl = excl
        self._rows = None

    @contextmanager
    def scope(self):
        """Push this Prepared's topic metric definition (share-of-voice numerator + floor denominator) into the context.

        ★ When one request loops over multiple topics (industry benchmark P50, public industry library page) you must enter scope each time:
          the definition lives in a contextvar; without resetting you'd get the previous topic's floor.
        """
        from geo.api.ai_telemetry_reports import set_engine_alias, set_fixed_denominator

        _set_mention_scope(self.topic)
        set_fixed_denominator(self.topic)
        set_engine_alias(self.topic)           # ★ engine alias (doubao_browser->doubao), same as above
        try:
            yield self
        finally:                               # clear on scope exit; don't leave this topic's definition for the next
            _set_mention_scope(None)
            set_fixed_denominator(None)
            set_engine_alias(None)

    def _tuple(self):
        return (self.stats, self.owned, self.neg, self.excl)

    def __iter__(self):
        return iter(self._tuple())

    def __getitem__(self, i):           # old call sites use prepared[0] to get stats
        return self._tuple()[i]

    def __len__(self):
        return 4

    def rows(self):
        if self._rows is None:
            from geo.api.ai_telemetry_reports import _load_rows

            self._rows = _load_rows(self.db, self.topic.id)
        return self._rows

    def metrics_for(self, rows, period_type=None, engine_slice=False) -> dict[str, float]:
        """Fallback path: a set of responses -> six metrics (excl applied automatically).

        period_type defaults to None = denominator not pinned (see report center FLOOR_PERIOD_TYPES).
        """
        from geo.api.ai_telemetry_reports import _metrics_for

        if self.excl is None:
            return _metrics_for(rows, self.owned, self.neg)   # v1 deployments don't accept excl
        try:
            return _metrics_for(rows, self.owned, self.neg, self.excl,
                                engine_slice=engine_slice, period_type=period_type)
        except TypeError:                      # old deployments' _metrics_for has no denominator parameter
            return _metrics_for(rows, self.owned, self.neg, self.excl, engine_slice)


def load_stats(db, topic) -> Prepared:
    """Call only once per request: the fallback path's _load_rows scans the topic's whole table; repeated calls scan repeatedly.

    stats None means the topic has no wide-table rows; all later access falls back to read-time aggregation.
    excl None means a v1 definition is deployed; later calls never pass that parameter.
    """
    from geo.api.ai_telemetry import _load_owned_urls
    from geo.api.ai_telemetry_reports import (
        _load_negative_windows, _wide_stats, set_engine_alias, set_fixed_denominator,
    )

    _set_mention_scope(topic)                  # ★ share of voice v3 definition; omitting it skews t19
    set_fixed_denominator(topic)               # ★ floor denominator; omitting it splits from the report center definition
    set_engine_alias(topic)                    # ★ engine alias; omitting it adds a raw-key column in per-platform views
    owned = _load_owned_urls(db, topic.id)
    neg = _load_negative_windows(db, topic.id)
    excl = None
    if _supports_excl():
        from geo.api.ai_telemetry_reports import _metric_excl_queries

        excl = _metric_excl_queries(topic)     # ★ required for v2; omitting it silently inflates exposure rate
    try:
        stats = (_wide_stats(db, topic.id, owned, neg, excl) if excl is not None
                 else _wide_stats(db, topic.id, owned, neg))
    except DatabaseError:
        # Deploy ordering window: code already on the new wide-table schema, DB migration not yet run (column missing).
        # Semantically equals "wide table unavailable" -> fall back to read-time aggregation, still the correct definition; don't crash the caller.
        db.rollback()
        stats = None
    return Prepared(db, topic, stats, owned, neg, excl)


def _set_mention_scope(topic) -> None:
    """Set the share-of-voice numerator definition per topic (equivalent of report center _topic_scoped L431).

    topic=None means clear (exiting Prepared.scope()).
    ★ Must be exactly equivalent to _topic_scoped: besides hardcoded t19 there's the profile page admin switch
      mention_cat_scope(topic) -- checking only the former skews topics that enabled v3 via the switch on these pages.
    """
    try:
        from geo.api.ai_telemetry_reports import MENTION_CAT_SCOPE_TOPIC_IDS, _mention_cat_ctx
        from geo.models.ai_telemetry import mention_cat_scope
    except ImportError:                        # old deployments have no v3 definition
        return
    if topic is None:
        _mention_cat_ctx.set(False)
        return
    _mention_cat_ctx.set(topic.id in MENTION_CAT_SCOPE_TOPIC_IDS or mention_cat_scope(topic))


def period_series(db, topic, period_type: str, prepared: Optional[Prepared] = None):
    """(keys ascending, key->six metrics, key->(period_start, period_end)).

    Line-for-line isomorphic to both branches of list_reports (ai_telemetry_reports.py L757-764).
    **Does not include** OVERVIEW_PIN -- for the display definition use latest_period() or apply apply_overview_pin() yourself.
    """
    from geo.api.ai_telemetry_reports import (
        _GROUPERS, _group_by, _period_bounds, _series_from_stats,
    )

    p = prepared if prepared is not None else load_stats(db, topic)
    with p.scope():                                       # ★ this topic's share-of-voice definition + floor denominator
        if p.stats is not None:
            return _series_from_stats(p.stats, period_type)
        rows = p.rows()                                   # fallback: full table scan, same as endpoint behavior
        groups = _group_by(rows, _GROUPERS[period_type])
        keys = sorted(groups)
        return (
            keys,
            {k: p.metrics_for(groups[k], period_type) for k in keys},
            {k: _period_bounds(period_type, k, groups[k]) for k in keys},
        )


def apply_overview_pin(series: dict, keys: list[str], topic_id: int, period_type: str) -> None:
    """Anchored display values for the latest period of demo/delivery topics (mutates series in place). Silently skipped on old deployments without this function."""
    try:
        from geo.api.ai_telemetry_reports import _apply_overview_pin
    except ImportError:
        return
    _apply_overview_pin(series, keys, topic_id, period_type)


def latest_period(db, topic, period_type: str = "weekly",
                  prepared: Optional[Prepared] = None) -> tuple[Optional[str], dict, dict]:
    """-> (latest period key, that period's six metrics, previous period's six metrics). **Digit-for-digit equal to the report center home KPI cards**.

    "Latest" = keys[-1], including the unclosed current period (copied from kpi_summary L819, see module header pitfall 3);
    OVERVIEW_PIN is applied, so anchored topics like t19 also match. Returns (None, {}, {}) when there's no data.
    """
    keys, series, _ = period_series(db, topic, period_type, prepared)
    apply_overview_pin(series, keys, topic.id, period_type)
    if not keys:
        return None, {}, {}
    cur = series[keys[-1]]
    prev = series[keys[-2]] if len(keys) >= 2 else {}
    return keys[-1], cur, prev


def period_engine_metrics(db, topic, period_type: str, key: str,
                          prepared: Optional[Prepared] = None) -> dict[str, dict]:
    """{engine: six metrics} for a period. OVERVIEW_PIN not applied (anchor values only cover the all-engine total definition)."""
    from collections import defaultdict

    from geo.api.ai_telemetry_reports import (
        ALL_ENGINE, _GROUPERS, _bj_date, _metrics_from_counts, _sum_counts,
    )

    p = prepared if prepared is not None else load_stats(db, topic)
    with p.scope():                            # ★ this topic's share-of-voice definition + pinned denominator
        if p.stats is not None:
            days = [d for d in p.stats if _GROUPERS[period_type](date.fromisoformat(d)) == key]
            engines = {e for d in days for e in p.stats[d] if e != ALL_ENGINE}
            return {
                eng: _metrics_from_counts(
                    _sum_counts([p.stats[d].get(eng, {}) for d in days if eng in p.stats[d]]),
                    engine_slice=True, period_type=period_type)
                for eng in engines
            }
        by_eng: dict[str, list] = defaultdict(list)
        for r in p.rows():
            if _GROUPERS[period_type](_bj_date(r.created_at)) == key:
                by_eng[r.engine or ""].append(r)
        return {eng: p.metrics_for(rs, period_type, engine_slice=True)
                for eng, rs in by_eng.items()}


def daily_metric_series(db, topic, metric: str = "exposure", limit: int = 30,
                        prepared: Optional[Prepared] = None) -> list[tuple[str, float]]:
    """Daily single-metric series (ascending, at most the last limit periods), for brand growth sparklines / trend charts."""
    keys, series, _ = period_series(db, topic, "daily", prepared)
    return [(k, float(series[k].get(metric) or 0.0)) for k in keys][-max(1, limit):]


def today_key() -> str:
    """Current Beijing day (never in the wide table; only in the live tail)."""
    from datetime import datetime

    from geo.api.ai_telemetry_reports import BJ_OFFSET

    return (datetime.utcnow() + BJ_OFFSET).date().isoformat()


def closed_key(keys: list[str], period_type: str) -> Optional[str]:
    """Latest **closed** period key.

    The wide-table boundary is always yesterday, but the live tail also brings "today/this week/this month" into keys -- unclosed periods
    can't be used to draw conclusions or judge targets, so this always falls back one period.
    Note: report center home cards use keys[-1], not this function; don't mix them up when aligning numbers.
    """
    if not keys:
        return None
    from geo.api.ai_telemetry_reports import _GROUPERS

    cur = _GROUPERS[period_type](date.fromisoformat(today_key()))
    closed = [k for k in keys if k < cur]
    return closed[-1] if closed else None


def prev_key(keys: list[str], key: str) -> Optional[str]:
    i = keys.index(key) if key in keys else -1
    return keys[i - 1] if i > 0 else None


def slim(metrics: dict) -> dict:
    """Keep only the six metrics (drop internal keys like _responses); values are already 0-100 rounded to 1 decimal -- don't process further."""
    return {k: metrics[k] for k in METRIC_ORDER if k in metrics}


def responses_of(metrics: dict) -> int:
    return int(metrics.get("_responses") or 0)


__all__ = [
    "METRIC_ORDER", "Prepared", "metric_labels", "load_stats", "period_series",
    "apply_overview_pin", "latest_period", "period_engine_metrics", "daily_metric_series",
    "today_key", "closed_key", "prev_key", "slim", "responses_of",
]
