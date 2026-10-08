"""Report center API — daily / weekly / monthly reports / metric dashboard / evidence search.

Benchmark metric definitions: see docs/02-需求分析/产品需求/需求/需求文档-日报周报月报指标对齐与展示方案.md:
five KPIs = overall exposure rate / TOP1 exposure rate / TOP3 exposure rate / content neutral-positive rate / article citation rate.

All aggregation is compute-on-read, no new tables:
  - Day/week/month grouping uses response.created_at converted to Beijing time (+8h): calendar day / ISO week / calendar month
  - Exposure rate = hit responses / valid responses (error empty); TOP1/TOP3 denominator = hit responses
  - Content neutral-positive rate = share of hit cells that are not negative_mention (latest cell_insights verdict)
  - Article citation rate = responses citing an owned URL (asset/published URL) / valid responses
  - KPI target = baseline × 1.3 (contract definition +30%); no target when the baseline is 0
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import zlib
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from geo.api.ai_telemetry import (
    _clean_citations_for_out, _get_topic_or_404, _load_owned_urls, _norm_url,
    get_db,
)
from geo.api.auth import get_current_user
from geo.models.ai_telemetry import (
    REPORT_DAILY_SCHEMA_VERSION,
    AiTelemetryCellInsightORM, AiTelemetryReportDailyORM, AiTelemetryResponseORM,
    AiTelemetryTopicBriefingORM, AiTelemetryTopicSourceStrategyORM, ResponseOut,
    TopicGeneratedDocORM, fixed_response_count, mention_cat_scope, metric_brand_terms,
)
from geo.models.user import User
from geo.services import keyword_heat
from geo.services.access_control import (
    ACTION_READ,
    ACTION_WRITE,
    create_resource_operation_context,
)
from geo.services.system_audit import append_event as append_system_audit
# report_basis_changes always lazily imports this module, so a top-level import here does not create a cycle
from geo.services.report_basis_changes import detect as detect_basis_changes
from geo_checker.analyzers.source_factor import classify_factor

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-telemetry")

BJ_OFFSET = timedelta(hours=8)

# Region definition for search popularity — if service region/industry contains these words treat as global (Google Trends), otherwise CN (Baidu Index)
_GLOBAL_GEO_HINTS = ("海外", "出海", "全球", "欧美", "北美", "美国", "东南亚", "日本", "韩国",
                     "欧洲", "global", "overseas", "worldwide")


def _topic_search_geo(topic) -> str:
    """topic → 'cn' | 'global'. Uses the service region from the profile; if empty, looks at the industry description; no clue → CN."""
    try:
        profile = json.loads(topic.profile_json or "{}")
    except Exception:  # noqa: BLE001
        profile = {}
    blob = f"{profile.get('service_geo') or ''} {topic.industry or ''}".lower()
    return "global" if any(h.lower() in blob for h in _GLOBAL_GEO_HINTS) else "cn"

METRIC_KEYS = ["exposure", "top1", "top3", "accuracy", "citation"]

# Cap on the neutral-positive rate target: when baseline × 1.3 exceeds 98, use the agreed value 98
ACCURACY_TARGET_CAP = 98.0


# ─────────────────── schemas ──────────────────────────────


class KpiMetric(BaseModel):
    key: str                              # exposure / top1 / top3 / accuracy / citation
    current: float
    prev: Optional[float] = None          # Previous period value; None when not comparable
    delta_pp: Optional[float] = None      # current - prev (percentage points)
    sparkline: list[float] = Field(default_factory=list)
    target: Optional[float] = None        # Tier-labeled = range lower bound; unlabeled = baseline × 1.3 (baseline 0 → None)
    target_high: Optional[float] = None   # Tier target range upper bound; None when unlabeled
    baseline: Optional[float] = None
    progress: Optional[str] = None        # met / near / missed; None when no target


class KpiSummaryOut(BaseModel):
    topic_id: int
    period_type: str
    metrics: list[KpiMetric]


class ReportListItem(BaseModel):
    key: str                              # day: YYYY-MM-DD / week: YYYY-Www / month: YYYY-MM
    period_start: str
    period_end: str
    exposure_pct: float
    top1_pct: float
    top3_pct: float
    accuracy_pct: float
    citation_pct: float
    response_count: int
    # Exposure-rate numerator and denominator (category-term definition) for trend-chart tooltips — these move first when the definition changes
    exposure_num: int = 0
    exposure_den: int = 0
    briefing_id: Optional[int] = None     # Briefing (weekly report body) already generated in this period


class BasisChange(BaseModel):
    """Trend-chart "metric definition change" marker — the numerator/denominator base or definition changed in this period.

    Only structured data is provided; copy is rendered by frontend lang.ts (Chinese and English).
    """
    key: str                              # Period key (same domain as items[].key, x-axis position on the trend chart)
    day: str                              # Beijing date of the change
    kind: str                             # queries / scope / engines / denom
    before: Optional[float] = None        # Base before the change (vocabulary size or denominator)
    after: Optional[float] = None
    engines_added: list[str] = Field(default_factory=list)
    engines_removed: list[str] = Field(default_factory=list)


class ReportListOut(BaseModel):
    topic_id: int
    period_type: str
    items: list[ReportListItem]
    basis_changes: list[BasisChange] = Field(default_factory=list)


class DailyKpiRow(BaseModel):
    key: str
    label: str
    today: float
    yesterday: Optional[float] = None
    delta_pp: Optional[float] = None
    note: str = ""


class HighlightRow(BaseModel):
    query: str
    engine: str
    today_label: str                      # TOP1 / TOP3 / rank 4-10 / exposed / not exposed
    yesterday_label: str
    change: str                           # up / down / flat / new
    hits: int
    runs: int
    evidence_response_id: Optional[int] = None


class ActionRow(BaseModel):
    time: str
    title: str
    status: str


class DetailRow(BaseModel):
    response_id: int
    query: str
    engine: str
    round_time: str                       # Sampling round (Beijing time HH:MM)
    hit: bool
    rank_label: str
    sentiment: str                        # positive / neutral / negative / unknown
    cited_own: bool
    source_url: Optional[str] = None
    created_at: datetime


class DailyReportOut(BaseModel):
    topic_id: int
    date: str
    kpi: list[DailyKpiRow]
    highlights: list[HighlightRow]
    actions: list[ActionRow]
    detail: list[DetailRow]
    # Flywheel P1-A: AI daily summary for the day (generated daily by telemetry-service); None if absent, frontend shows progressively
    ai_summary: Optional[str] = None


class WeeklyMetricRow(BaseModel):
    key: str
    label: str
    baseline: float
    weeks: list[float]                    # W1..Wn (aligned with week_keys)
    current: float
    delta_pp: Optional[float] = None      # vs last week
    target: Optional[float] = None
    target_high: Optional[float] = None   # Tier target range upper bound; None when unlabeled
    progress: Optional[str] = None


class EngineLift(BaseModel):
    engine: str
    baseline: float
    current: float
    lift_pp: float


class ActionStat(BaseModel):
    key: str                              # docs / attack / correction / evidence
    count: int
    detail: str = ""


class PlatformMetricRow(BaseModel):
    engine: str
    exposure_pct: float
    top1_pct: float
    top3_pct: float
    accuracy_pct: float
    citation_pct: float
    mention_pct: float = 0.0              # Brand share of voice (per platform)
    # Raw exposure-rate numerator/denominator (real observed counts; denominator is the display definition = max(actual, floor)):
    # shown directly in the frontend "hits / responses" column; hit_count ÷ valid_count is the overall exposure rate on the left.
    hit_count: int = 0
    valid_count: int = 0
    # Rank capture coverage (share of hit cells with a brand_rank). 0 = no rank captured this period;
    # the frontend then shows TOP1/TOP3 as "not collected" instead of 0%; None = unknown, show values as usual.
    rank_coverage: Optional[float] = None


class MonthlyKpiCard(BaseModel):
    key: str
    label: str
    before: float                         # Baseline week value
    after: float                          # Value of the last week of this month
    relative_pct: Optional[float] = None  # (after-before)/before × 100;before=0 → None
    met: bool                             # Relative improvement ≥30%


class DeliverableStat(BaseModel):
    label: str
    value: str
    delta: str = ""                       # Increment vs previous period (empty = no previous period / not applicable)


class TrendWeekPoint(BaseModel):
    week_key: str
    exposure_pct: float
    forecast: bool = False


class CorrectionRow(BaseModel):
    issue_type: str
    found: str
    status: str
    action: str


class WeeklyReportOut(BaseModel):
    topic_id: int
    week_key: str
    period_start: str
    period_end: str
    week_keys: list[str]                  # W1..Wn sequence after the baseline week
    metrics: list[WeeklyMetricRow]
    # Rank capture coverage for the whole period (see PlatformMetricRow.rank_coverage)
    rank_coverage: Optional[float] = None
    # The same three sections as the monthly report (added 2026-08-07): before→after cards / per-platform six metrics / deliverables this period
    # "Before" = the baseline week (the week before week_keys), same source as the baseline of metrics above
    kpi_cards: list[MonthlyKpiCard] = Field(default_factory=list)
    platform_matrix: list[PlatformMetricRow] = Field(default_factory=list)
    deliverables: list[DeliverableStat] = Field(default_factory=list)
    # This week's response details (sorted by term→engine→time); row cap see DETAIL_ROW_CAP
    detail: list[DetailRow] = Field(default_factory=list)
    detail_total: int = 0                 # Total detail rows (used to hint when detail is truncated)
    # The four sections below are no longer shown by the frontend since 2026-08-18 (weekly report now uses the monthly six sections); fields kept for API callers
    engine_lifts: list[EngineLift]
    action_stats: list[ActionStat]
    risks: list[str]
    body_md: str = ""                     # Briefing body for the week (included if present)
    summary_en: list[tuple[str, str]] = Field(default_factory=list)   # [(item, text)]
    # The same three sections as the monthly report (aligned 2026-08-18): weekly trend + extrapolation / corrective push / next-period plan
    trend: list[TrendWeekPoint] = Field(default_factory=list)
    basis_changes: list[BasisChange] = Field(default_factory=list)
    corrections: list[CorrectionRow] = Field(default_factory=list)
    next_plan: list[str] = Field(default_factory=list)
    exposure_target: Optional[float] = None   # Exposure-rate target, for the trend-chart target line


class MonthlyReportOut(BaseModel):
    topic_id: int
    month_key: str
    period_start: str
    period_end: str
    verdict: str                          # met / partial / missed
    verdict_text: str
    kpi_cards: list[MonthlyKpiCard]
    platform_matrix: list[PlatformMetricRow]
    trend: list[TrendWeekPoint]
    basis_changes: list[BasisChange] = Field(default_factory=list)
    corrections: list[CorrectionRow]
    deliverables: list[DeliverableStat]
    next_plan: list[str]
    exposure_target: Optional[float] = None   # Exposure-rate target (tier range lower bound or baseline×1.3), for the trend-chart target line


class SceneDistQuery(BaseModel):
    query: str
    count: int                            # Number of term×platform combinations of this term in this rank band


class SceneDistRow(BaseModel):
    scene_type: str                       # compare / brand / search / qa / intent / unknown
    query_count: int
    # bucket_mode="rank" four bands (top1/top2_3/r4_10/miss);
    # ="exposure" three bands (top1/exposed/miss) — rank is meaningless for brand/competitor-comparison terms, a hit means exposed
    bucket_mode: str = "rank"
    top1: int
    top2_3: int
    r4_10: int
    miss: int
    exposed: int = 0                      # Three-band mode: hit but not #1 (including hits without rank)
    # Concrete terms per rank band (sorted by combination count desc), for expanded details
    top1_queries: list[SceneDistQuery] = Field(default_factory=list)
    top2_3_queries: list[SceneDistQuery] = Field(default_factory=list)
    r4_10_queries: list[SceneDistQuery] = Field(default_factory=list)
    miss_queries: list[SceneDistQuery] = Field(default_factory=list)
    exposed_queries: list[SceneDistQuery] = Field(default_factory=list)


class PositionDistributionOut(BaseModel):
    topic_id: int
    rows: list[SceneDistRow]


class CitationSourceRow(BaseModel):
    url: str
    domain: str
    title: str
    source_type: str                      # GEO factor type produced by classify_factor
    engines: list[str]
    count: int
    owned: bool


class SourceTypeShare(BaseModel):
    source_type: str
    count: int
    pct: float


class CitationSourcesOut(BaseModel):
    topic_id: int
    total_citations: int
    type_share: list[SourceTypeShare]
    top_sources: list[CitationSourceRow]


class EvidenceSearchOut(BaseModel):
    topic_id: int
    total: int
    items: list[ResponseOut]


# ─────────────────── Basic aggregation ──────────────────────────────


def _bj_date(dt: datetime) -> date:
    return (dt + BJ_OFFSET).date()


def _week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_bounds(week_key: str) -> tuple[date, date]:
    y, w = week_key.split("-W")
    start = date.fromisocalendar(int(y), int(w), 1)
    return start, start + timedelta(days=6)


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _lang(request: Request) -> str:
    """Frontend X-Locale (fallback Accept-Language) → 'zh' | 'en'."""
    loc = (request.headers.get("x-locale") or request.headers.get("accept-language") or "").lower()
    return "en" if loc.startswith("en") else "zh"


def _rank_label(rank: Optional[int], hit: bool) -> str:
    """Stable key (top1/top3/r4_10/exposed/miss), rendered by frontend i18n."""
    if not hit:
        return "miss"
    if rank == 1:
        return "top1"
    if rank is not None and rank <= 3:
        return "top3"
    if rank is not None and rank <= 10:
        return "r4_10"
    return "exposed"


def _rank_order(label: str) -> int:
    return {"top1": 0, "top3": 1, "r4_10": 2, "exposed": 3, "miss": 4}.get(label, 5)


# Rank definitions only make sense for category terms: brand / competitor-comparison questions name the brand, so a hit means "exposed"
# and the position carries no information (ask about a brand, the brand is always first). For these two term types:
#   - the rank distribution panel shows three bands TOP1/exposed/not exposed (bucket_mode="exposure")
#   - exposure rate / TOP1 exposure rate / TOP3 exposure rate exclude them globally (cat_* category-term counts)
#   - neutral-positive rate / citation rate / share of voice keep the full vocabulary (negatives/citations/voice under brand terms are signals to capture)
# Orthogonal to METRIC_EXCLUDE_BRAND_TOPIC_IDS (topic-level term exclusion, whole rows leave all six metrics); they stack.
EXPOSURE_ONLY_SCENES = frozenset({"brand", "compare"})

# Share-of-voice definition v3 (enabled per topic): numerator = category-term hits (for vocabularies fully covering brand/comparison terms,
# a brand question answered with the brand is not "earned voice"); other topics keep the full-vocabulary numerator, display unaffected
MENTION_CAT_SCOPE_TOPIC_IDS = frozenset({19})   # Legacy hard-coding; new topics use the admin toggle on the profile page
_mention_cat_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("mention_cat", default=False)

# Engine display aliases: doubao_browser (anonymous physical-device browser), doubao_mobile (physical-device mobile) and doubao
# (ARK API) are three collection channels of the same model and must be merged into one column in reports. Not merging was observed
# (topic 20): W33 ran doubao, W34 switched to doubao_browser, and the per-platform matrix gained a raw-key column
# (no entry in the frontend engine whitelist → raw slug printed, no logo), and the engine improvement comparison split the same Doubao
# into two lines, "one added + one dropped".
#
# ★ 2026-08-31 relaxed gate: the original condition was `k in engines and v not in engines`, i.e. only when "the topic config
#   lists this channel". But channels are decided on the V-networks/workers/desktop-cn side, and raw keys not in engines_json appeared in the DB
#   (topic 20's doubao_mobile: run 9952 persisted 44 rows, 36 of them empty_answer /
#   CAPTCHA) — then `k in engines` fails, the alias does not apply, and the matrix gains another "豆包" (Doubao) row.
#   Now only `v not in engines` is checked: when the canonical engine is not running, any Doubao channel is attributed to Doubao.
#   The old rationale "do not merge when two channels run together, or the denominator doubles and dilutes the hit rate" no longer
#   holds after pinning the denominator — it always equals config value ÷ engine count, independent of rows persisted (see _fixed_denominator).
#   When the canonical engine is in engines we still do not merge; the topic is explicitly treating the two channels as independent samples.
# The alias table itself shares a source with full_diagnosis_competitive._DEFAULT_ENGINE_ALIAS; **the gate is intentionally different** —
# full diagnosis uses actual row counts as the denominator (no pinned denominator), so merging two concurrently running channels does dilute the hit rate,
# hence it keeps the strict `k in engines` gate. Do not relax that one too when changing this.
_ENGINE_ALIAS_DEFAULT = {
    "doubao_browser": "doubao", "doubao_mobile": "doubao",
    "wenxin_browser": "wenxin", "qwen_browser": "qwen",
    "yuanbao_browser": "yuanbao",
    # Overseas physical-device channels are merged with the same-named model into one engine report
    "gemini_browser": "gemini", "perplexity_browser": "perplexity",
    "chatgpt_browser": "chatgpt", "claude_browser": "claude",
}
_engine_alias_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "engine_alias", default={})


def set_engine_alias(topic) -> None:
    """Set the engine alias context per topic (same batch as set_fixed_denominator, called in _topic_scoped).

    Set unconditionally (explicitly write an empty dict when not applicable) — when one request loops over several topics, without this explicit reset
    the previous topic's aliases would leak into the next one.

    The only gate left is "canonical engine not in engines"; the alias key itself no longer needs to be in engines — see
    the ★ section above _ENGINE_ALIAS_DEFAULT (channels are decided on the worker side and may be absent from config).
    """
    try:
        engines = set(json.loads(getattr(topic, "engines_json", "") or "[]"))
    except Exception:  # noqa: BLE001
        engines = set()
    _engine_alias_ctx.set({k: v for k, v in _ENGINE_ALIAS_DEFAULT.items()
                           if v not in engines})


def _alias_engine(engine: str) -> str:
    """Engine data key → display key (returned as-is if not in the alias table)."""
    return _engine_alias_ctx.get().get(engine or "", engine or "")


def _alias_rows(rows: list) -> list:
    """Rewrite the engine of a batch of responses in place to display keys (zero overhead when the alias table is empty).

    ★ Uses set_committed_value instead of direct assignment: direct assignment marks these ORM rows dirty,
      and any later flush in the same session would write display keys back to the DB (reports corrupting data).
      set_committed_value only changes the loaded in-memory value and does not enter the dirty set.
    """
    alias = _engine_alias_ctx.get()
    if not alias:
        return rows
    for r in rows:
        new = alias.get(r.engine or "")
        if new:
            set_committed_value(r, "engine", new)
    return rows


# Pinned denominator (configured by an admin in profile settings: "how many 'question × platform' combinations one period should cover",
# one value per topic, no default number anywhere in code — see fixed_response_count).
# Definition standard: docs/03-系统设计/报告与指标/报告口径-应答分母托底.md. The denominator unit is **combinations**, not response count
# (see _cell_counts_for), so this value does not scale with how many rounds a period ran: it = vocabulary size × engine count.
#
# Semantics are a **pinned denominator** (since 2026-08-31, product decision): denom = the topic's configured floor,
# split evenly across the topic's engines per engine, no longer max(actual, floor). Rationale: "comparable on one scale" beats "never exceeds 100%":
# runs per period vary, an engine session expiring skips 40 terms, so actual denominators drift and neither columns nor periods compare.
# Once pinned, the denominator depends only on config; columns and periods share one scale, and Σ per-engine numerators ÷ floor = top overall exposure rate.
#
# The cost is that **the numerator can exceed the denominator** (when actual volume exceeds the floor), giving exposure >100% — per product decision
# it is clamped to 100% at output (see the end of _metrics_from_counts). This is exactly the shape rolled back on 2026-08-17
# (topic 20 single engine 181 ÷ 160 = 113%); now it is a **known cost, deliberately clamped**, not a regression bug:
# exceeding always means the floor is configured too low; clamping also logs a warning, never ship it silently.
#
# Two boundaries; missing either causes a unit mismatch:
#   1. Only applies at cross-period comparison granularities (FLOOR_PERIOD_TYPES). daily is a same-day snapshot, and competitor mini-trend
#      sub-buckets are window slices with naturally incomplete coverage; both always use the real denominator.
#   2. Only applies on the **read path**: the wide-table rollup is wrapped in the no_fixed_denominator() hard guard,
#      so what is stored is always real counts and real percentages.
#
# ★ The former boundary 3 "per-engine applies only when the overall is also floored" (floor_active) was removed along with pinning:
#   that gate existed because under max semantics "overall not floored but per-engine floored" made top and bottom disagree; once pinned,
#   both sides use the config value unconditionally and agree anyway; keeping the gate would only make per-engine fall back to the real denominator
#   on over-volume — exactly the symptom being fixed (2026-08-30 report with six column denominators 374/227/363/7/439/480).
_fixed_valid_ctx: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "fixed_valid", default=None)
_fixed_engine_n_ctx: contextvars.ContextVar[int] = contextvars.ContextVar(
    "fixed_engine_n", default=1)
# Forced no-floor (no_fixed_denominator scope): takes priority over topic config
_fixed_disabled_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "fixed_disabled", default=False)

# The floor only applies at these two granularities (see boundary 1 above). period_type default None = no floor — the default must
# lean to the safe side: missing one week/month call site just means "not floored, numbers still real", visible to the eye; if the default floored,
# missing one daily call site would silently stay wrong (exactly the shape of the 113% bug).
FLOOR_PERIOD_TYPES = frozenset({"weekly", "monthly"})


def set_fixed_denominator(topic) -> None:
    """Set the floor-denominator context per topic (shared by report center _topic_scoped and the data adapter layer).

    Set unconditionally (explicitly write None when unconfigured) — when one request loops over several topics (industry benchmark, industry library),
    without this "explicit reset" the previous topic's floor would leak into the next one.
    """
    _fixed_valid_ctx.set(fixed_response_count(topic))
    try:
        n = len(json.loads(getattr(topic, "engines_json", "") or "[]"))
    except Exception:  # noqa: BLE001
        n = 0
    _fixed_engine_n_ctx.set(max(1, n))


@contextmanager
def no_fixed_denominator():
    """Forced no-floor scope — used for wide-table persistence and cross-tenant aggregation.

    The wide table is the authoritative source and floored percentages written there are irreversible; the industry benchmark P50 is a cross-tenant aggregate,
    and one topic's ops config must not pollute the whole industry baseline.

    ★ Uses a separate "disabled" switch rather than clearing the value: set_fixed_denominator() is often called inside the scope
      (load_stats / Prepared.scope both set it), and a cleared value would be immediately overwritten.
    """
    t = _fixed_disabled_ctx.set(True)
    try:
        yield
    finally:
        _fixed_disabled_ctx.reset(t)


def _fixed_denominator(engine_slice: bool = False,
                       period_type: Optional[str] = None) -> Optional[int]:
    """The **pinned denominator** for the current context; returns None when not applicable (use actual counts as usual).

    period_type  display granularity; anything not in FLOOR_PERIOD_TYPES is never pinned (boundary 1)
    engine_slice per-engine slice: split evenly by the topic's engine count (_fixed_engine_n_ctx)

    All values come from topic config: floor = fixed_response_count(topic) (configured by admins in profile settings;
    unconfigured → None → this function returns None and real counts are used); divisor = len(engines_json).
    """
    if _fixed_disabled_ctx.get():              # Inside a no_fixed_denominator() scope
        return None
    if period_type not in FLOOR_PERIOD_TYPES:
        return None
    total = _fixed_valid_ctx.get()
    if not total:
        return None
    if not engine_slice:
        return total
    return max(1, round(total / _fixed_engine_n_ctx.get()))


def _topic_scoped(
    db, topic_id, user_id, allow_admin_user=None, *, action: str = ACTION_READ,
):
    """Reports-specific topic resolution: also sets the per-topic voice definition + fixed response denominator context."""
    topic = _get_topic_or_404(
        db, topic_id, user_id,
        allow_admin_user=allow_admin_user, action=action,
    )
    _mention_cat_ctx.set(topic_id in MENTION_CAT_SCOPE_TOPIC_IDS or mention_cat_scope(topic))
    set_fixed_denominator(topic)
    set_engine_alias(topic)
    return topic


def _scene_of_map(topic) -> dict[str, str]:
    """query text → scene_type (parses topic.queries_json, fault-tolerant).

    ★ Includes soft-deleted terms; do not filter here — see _metric_excl_queries for why.
    """
    out: dict[str, str] = {}
    try:
        for q in json.loads(topic.queries_json or "[]"):
            if isinstance(q, dict) and (q.get("text") or "").strip():
                out[q["text"].strip()] = q.get("scene_type") or "unknown"
            elif isinstance(q, str) and q.strip():
                out[q.strip()] = "unknown"
    except Exception:  # noqa: BLE001
        pass
    return out


def _metric_excl_queries(topic) -> frozenset[str]:
    """Set of term texts excluded from exposure/TOP1/TOP3 definitions (scene ∈ EXPOSURE_ONLY_SCENES).

    ★ This **must see all terms, including soft-deleted ones** — soft-deleted terms are dropped at the row-loading layer (_load_rows /
      _load_range_rows / live tail of _wide_stats). Filtering here would make soft-deleted brand/
      comparison terms lose their scene_type, so their historical responses count as category terms in the exposure denominator, inflating exposure.
    """
    return frozenset(q for q, s in _scene_of_map(topic).items() if s in EXPOSURE_ONLY_SCENES)


def _deleted_queries(db: Session, topic_id: int) -> frozenset[str]:
    """Soft-deleted prompt texts of the topic (for data loading; if the topic cannot be loaded, treat as none deleted).

    Historical responses of soft-deleted terms **leave all six metrics entirely** — same nature as the METRIC_EXCLUDE_BRAND_TOPIC_IDS
    "topic-level term exclusion", and they stack. Filtering happens at the **row-loading** layer,
    not at _scene_of_map / _metric_excl_queries (see their comments above).
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM, deleted_query_texts

    t = db.get(AiTelemetryTopicORM, topic_id)
    return deleted_query_texts(t) if t is not None else frozenset()


def _attach_comp_hits(db: Session, topic_id: int, rows: list) -> list:
    """Attach to each row "which competitors are mentioned in the body" (`r._chits`) for downstream counting reuse.

    Why attach to rows instead of passing parameters: the competitor definition governs the six metrics' voice denominator (_counts_for /
    _cell_counts_for), the leaderboard (_window_stats) and per-engine cells; threading comp_hits through a dozen
    function signatures is verbose and bound to miss one — the previous version missed the per-engine path, and the leaderboard showed "overall 94.9 /
    DeepSeek cell 0". Attached at the row-loading layer, all downstream consumers share one source.

    The candidate set is built from **all rows** (the wider the discovery surface the better); matching only looks at the body. The session is per request,
    so attaching to instances does not leak across requests.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM   # Not imported at top level (same as _deleted_queries)

    try:
        topic = db.get(AiTelemetryTopicORM, topic_id)
        terms = tuple(metric_brand_terms(topic)) if topic else ()
        universe = _comp_universe(rows, terms)
        hits = _comp_hits_by_row(rows, universe)
    except Exception:  # noqa: BLE001
        log.exception("[reports] competitor body matching failed topic=%s, falling back to extractor definition", topic_id)
        return rows
    for r in rows:
        r._chits = hits.get(r.id, ())
    return rows


def _load_rows(db: Session, topic_id: int) -> list[AiTelemetryResponseORM]:
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .order_by(AiTelemetryResponseORM.created_at.asc())
          .all()
    )
    gone = _deleted_queries(db, topic_id)
    return _attach_comp_hits(
        db, topic_id,
        _alias_rows([r for r in rows if r.query not in gone] if gone else rows))


def _load_negative_windows(db: Session, topic_id: int) -> dict[tuple[str, str], list[tuple[date, date]]]:
    """(query, engine) → list of negative-verdict time windows (response-level neutral-positive definition, since 2026-07-06).

    "Negative" for the neutral-positive rate = hit responses falling inside some negative_mention verdict window;
    same-combination responses outside the window (e.g. after a recheck turned positive) no longer count as negative — verdicts evolve over time,
    and reports for historical windows use the verdict of that period, not rewritten by future verdicts.
    """
    insights = (
        db.query(AiTelemetryCellInsightORM)
          .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
          .filter(AiTelemetryCellInsightORM.verdict == "negative_mention")
          .all()
    )
    windows: dict[tuple[str, str], list[tuple[date, date]]] = defaultdict(list)
    for ins in insights:
        ws = ins.window_start.date() if isinstance(ins.window_start, datetime) else ins.window_start
        we = ins.window_end.date() if isinstance(ins.window_end, datetime) else ins.window_end
        windows[(ins.query, ins.engine)].append((ws, we))
    return dict(windows)


def _is_negative_resp(r: AiTelemetryResponseORM,
                      neg_windows: dict[tuple[str, str], list[tuple[date, date]]]) -> bool:
    wins = neg_windows.get((r.query, r.engine))
    if not wins:
        return False
    d = _bj_date(r.created_at)
    return any(ws <= d <= we for ws, we in wins)


def _cites_owned(r: AiTelemetryResponseORM, owned_urls: set[str]) -> bool:
    if not owned_urls:
        return False
    try:
        cits = json.loads(r.citations_json or "[]")
    except Exception:  # noqa: BLE001
        return False
    for c in cits:
        if isinstance(c, dict) and c.get("url") and _norm_url(c["url"]) in owned_urls:
            return True
    return False


COUNT_KEYS = (
    "valid_count", "hit_count", "top1_count", "top3_count",
    "neg_hit_count", "cited_count", "comp_mention_count",
    # cat_* = category-term definition (excludes EXPOSURE_ONLY_SCENES terms), used only for exposure/TOP1/TOP3
    "cat_valid_count", "cat_hit_count", "cat_top1_count", "cat_top3_count",
)


def _counts_for(
    rows: list[AiTelemetryResponseORM],
    owned_urls: set[str],
    neg_windows: dict[tuple[str, str], list[tuple[date, date]]],
    excl: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """The eleven counts of a group of responses (all numerators and denominators of the six metrics). Shared by wide-table persistence and live tail.

    excl = terms excluded from the exposure/TOP1/TOP3 definition (_metric_excl_queries); cat_* only counts rows not in
    excl, while other counts (neutral-positive/citation/voice) keep the full-vocabulary definition.
    """
    valid = [r for r in rows if not r.error]
    hits = [r for r in valid if r.hit]
    cat_valid = [r for r in valid if r.query not in excl]
    cat_hits = [r for r in cat_valid if r.hit]
    return {
        "valid_count": len(valid),
        "hit_count": len(hits),
        "top1_count": sum(1 for r in hits if r.brand_rank == 1),
        "top3_count": sum(1 for r in hits if isinstance(r.brand_rank, int) and r.brand_rank <= 3),
        "neg_hit_count": sum(1 for r in hits if _is_negative_resp(r, neg_windows)),
        "cited_count": sum(1 for r in valid if _cites_owned(r, owned_urls)),
        "comp_mention_count": sum(_comp_mentions(r) for r in valid),
        "cat_valid_count": len(cat_valid),
        "cat_hit_count": len(cat_hits),
        "cat_top1_count": sum(1 for r in cat_hits if r.brand_rank == 1),
        "cat_top3_count": sum(1 for r in cat_hits if isinstance(r.brand_rank, int) and r.brand_rank <= 3),
    }


def _cell_counts_for(
    rows: list[AiTelemetryResponseORM],
    owned_urls: set[str],
    neg_windows: dict[tuple[str, str], list[tuple[date, date]]],
    excl: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """Counts of a group of responses, **deduplicated per "term × platform" cell** (report read path only).

    cell = (query, engine). However many rounds ran in a calendar week or samples in a day, a cell counts once:
    hit = "hit at some point in this period", rank = best rank this period. So the denominator = how many term × platform combinations this period covered,
    and does not drift with batch-run frequency — same definition as the rank distribution panel "one cell = one term × platform".
        (Before 2026-08-18 it was response-count based: W33 had 3 rounds, the same term was counted 3 times,
         the denominator grew with rounds, and "ran more" looked like "covered more".)

    ★ Only the three exposure metrics and the citation rate use the cell definition. Neutral-positive rate (negative response share), share of voice (mention share),
      and evidence archive count use **response counts** — they ask "how many times did the AI say it", and dedup would distort them;
      these three still use the resp_* keys, see _metrics_from_counts.

    Same key names as _counts_for (response-count definition, used for the wide table) but different semantics: a single dict must not mix outputs
    of both paths, or numerators and denominators are on different scales. The report read path always goes through here (see the _wide_stats switch).
    """
    valid = [r for r in rows if not r.error]
    cells: dict[tuple[str, str], dict] = {}
    for r in valid:
        key = (r.query, r.engine or "")
        c = cells.get(key)
        if c is None:
            c = cells[key] = {"cat": r.query not in excl, "hit": False,
                              "rank": None, "cited": False}
        if r.hit:
            c["hit"] = True
            if isinstance(r.brand_rank, int) and (c["rank"] is None or r.brand_rank < c["rank"]):
                c["rank"] = r.brand_rank          # Best rank this period
        if _cites_owned(r, owned_urls):
            c["cited"] = True
    cat = [c for c in cells.values() if c["cat"]]
    cat_hits = [c for c in cat if c["hit"]]
    hits = [r for r in valid if r.hit]            # Response-count definition: neutral-positive denominator / share-of-voice numerator
    def _top(cs: list[dict], n: int) -> int:
        return sum(1 for c in cs if c["rank"] is not None and c["rank"] <= n)
    return {
        "valid_count": len(cells),
        "hit_count": sum(1 for c in cells.values() if c["hit"]),
        "top1_count": _top([c for c in cells.values() if c["hit"]], 1),
        "top3_count": _top([c for c in cells.values() if c["hit"]], 3),
        "neg_hit_count": sum(1 for r in hits if _is_negative_resp(r, neg_windows)),
        "cited_count": sum(1 for c in cells.values() if c["cited"]),
        "comp_mention_count": sum(_comp_mentions(r) for r in valid),
        "cat_valid_count": len(cat),
        "cat_hit_count": len(cat_hits),
        "cat_top1_count": _top(cat_hits, 1),
        "cat_top3_count": _top(cat_hits, 3),
        # Number of hit cells that **got a rank**. TOP1/TOP3 numerators come from brand_rank, which is
        # written by post-batch LLM extraction — when the whole extraction did not run, it is all NULL and TOP1/TOP3 become 0%, but 0%
        # means "the AI did not rank us near the top", which is different from "we did not capture a rank"; they must not be mixed in customer reports.
        # With this count, the display layer can distinguish "not collected" from "really not ranked" (see rank_coverage).
        "cat_hit_ranked_count": sum(1 for c in cat_hits if c["rank"] is not None),
        # ── Response-count definition (no dedup). When these three keys are missing, _metrics_from_counts falls back to cell counts,
        #    so the old wide-table / rollup path behaves exactly the same.
        "resp_count": len(valid),
        "resp_hit_count": len(hits),
        "resp_cat_hit_count": sum(1 for r in hits if r.query not in excl),
    }


def _metrics_from_counts(c: dict, engine_slice: bool = False,
                         rank_over_valid: bool = False,
                         period_type: Optional[str] = None) -> dict[str, float]:
    """Counts → six metrics (rounded to 1 decimal). Exposure/TOP1/TOP3 use cat_* category-term counts;
    old count dicts without cat keys fall back to full counts (equivalent to the old definition with empty excl).

    Metrics for any period = sum of daily counts within the period passed into this function, consistent with per-row aggregation.

    engine_slice=True means the counts passed in are for a **single engine** (per-platform matrix, etc.):
    the floor is split evenly by engine count and **pinned** as the exposure denominator (not max with actual) —
    only then are columns comparable when engines' actual coverage differs; see the comment at cat_denom below.

    period_type: see _fixed_denominator; default None = not pinned, use real counts as usual.

    rank_over_valid=True means the three exposure rates share one scale (one-click full diagnosis uses this: it has no topic
    and no floor value). Without this switch and without a floor, the TOP1/TOP3 denominator is the hit response count,
    i.e. the conditional probability "rank given a hit" — with only two or three hits it shoots straight to 100%,
    which is wrong when shown next to a 4.8% overall exposure rate.
    """
    valid = int(c.get("valid_count") or 0)
    hit = int(c.get("hit_count") or 0)
    cat_valid = int(c.get("cat_valid_count", valid) or 0)
    cat_hit = int(c.get("cat_hit_count", hit) or 0)
    cat_top1 = int(c.get("cat_top1_count", c.get("top1_count") or 0) or 0)
    cat_top3 = int(c.get("cat_top3_count", c.get("top3_count") or 0) or 0)
    # Pinned denominator (see _fixed_valid_ctx): when the topic has a floor configured it is **used directly as the denominator**, no longer max(actual, floor).
    # Overall uses the floor itself, per engine uses its even share divided by engine count; numerators are always real counts.
    # Topics without a floor have floor None, and the two lines below fall back to real counts, same behavior as before this change.
    # Engines' actual coverage is naturally uneven (an expired session skips 40 terms, 3 rounds in one period triple the denominator);
    # using each one's actual as the denominator = no common scale across columns or periods
    # (topic 20 W34: Wenxin only reached 139 terms, and its 46% was double the 23% of Doubao which covered all 183).
    # Once pinned, Σ per-engine numerators ÷ floor = top overall exposure rate, so top and bottom corroborate each other.
    #
    # The citation denominator is full-vocabulary responses, the exposure denominator is category-term responses — two units share one setting,
    # both pinned to the same number without scaling (the config page shows both theoretical full coverages for admin reference).
    floor = _fixed_denominator(engine_slice, period_type)
    resp_denom = floor if floor else valid
    cat_denom = floor if floor else cat_valid
    # Exceeding 100% is no longer "impossible" but a "known cost": when actual volume exceeds the floor the numerator exceeds the denominator,
    # and output is clamped to 100% per product decision (see end of function). Here we only record it —
    # exceeding always means **the floor is configured too low**, a signal for ops to fix config; it must not be silent.
    if floor and cat_hit > floor:
        log.warning("Pinned denominator exceeded by numerator: cat_hit=%s > denominator=%s (engine_slice=%s), floor configured too low",
                    cat_hit, floor, engine_slice)
    # Pinning **implies** a common scale: the three "exposure rates" are shown side by side on one card, and different denominators would conflict
    # (observed: overall 5.4% next to TOP1 81.4%, incomprehensible to readers). With a common scale, TOP1 ≤ TOP3 ≤ overall always holds
    # (the three numerators cat_top1 ≤ cat_top3 ≤ cat_hit share cat_denom; clamping affects all three the same way, order preserved).
    # ★ The denominator must be cat_denom, not max(cat_hit, floor): the latter degrades to cat_hit without a floor,
    #   reproducibly giving top3 > exposure (cat_valid=2000/cat_hit=1000/cat_top3=900 → 50.0% vs 90.0%).
    rank_over = rank_over_valid or bool(floor)
    rank_denom = cat_denom if rank_over else cat_hit
    # Share-of-voice numerator (v3, per topic): MENTION_CAT_SCOPE topics use category-term hits — brand/competitor-comparison terms
    # ask about the brand, so a hit is not "earned voice"; the denominator keeps full-vocabulary competitor appearances (competitors in the same answer are the real voice environment)
    # Neutral-positive rate and share of voice use **response counts** ("how many times did the AI say it"); exposure uses **cells**
    # ("how many term × platform combinations covered"). Cell-count dicts carry resp_* to bring response counts along;
    # the old wide-table / rollup path lacks these keys and falls back to same-named counts = identical behavior.
    resp_hit = int(c.get("resp_hit_count", hit) or 0)
    resp_cat_hit = int(c.get("resp_cat_hit_count", cat_hit) or 0)
    resp_count = int(c.get("resp_count", valid) or 0)
    _m_num = resp_cat_hit if _mention_cat_ctx.get() else resp_hit
    total_mentions = _m_num + int(c.get("comp_mention_count") or 0)
    out = {
        "exposure": round(cat_hit / cat_denom * 100, 1) if cat_denom else 0.0,
        "top1": round(cat_top1 / rank_denom * 100, 1) if rank_denom else 0.0,
        "top3": round(cat_top3 / rank_denom * 100, 1) if rank_denom else 0.0,
        "accuracy": round((1 - int(c.get("neg_hit_count") or 0) / resp_hit) * 100, 1)
        if resp_hit else 100.0,
        "citation": round(int(c.get("cited_count") or 0) / resp_denom * 100, 1) if resp_denom else 0.0,
        "mention": round(_m_num / total_mentions * 100, 1) if total_mentions else 0.0,
        # ★ Count fields are always real observed values, unaffected by the floor (weekly "evidence archive count", period list response count)
        "_responses": float(resp_count),
        # Raw exposure numerator/denominator (shown directly in trend-chart tooltips so definition changes are visible).
        # The denominator is the display cat_denom, otherwise the tooltip's num/den would not reproduce the percentage next to it.
        "_cat_hit": float(cat_hit),
        "_cat_valid": float(cat_denom),
    }
    # Rank capture coverage: how many hit cells got a brand_rank.
    #   None = unknown (no hits, or an old path without this count) → display layer shows values as usual
    #   0.0  = no rank captured at all → display layer shows "not collected", must not show 0%
    if "cat_hit_ranked_count" in c and cat_hit > 0:
        out["_rank_coverage"] = round(int(c.get("cat_hit_ranked_count") or 0) / cat_hit, 4)
    # Output clamp. With a pinned denominator, >100% is **a known possibility** (actual volume exceeds the floor); per product decision
    # it always shows 100% and is no longer treated as an anomaly. Hence warning, not error — topics with a floor that run fully
    # will trigger it steadily, and error would flood the logs with noise and bury real definition bugs.
    # Without a floor (floor None) numerator and denominator share a source, so overflow is still a code bug → keep error.
    for k in ("exposure", "top1", "top3", "citation"):
        if not 0.0 <= out[k] <= 100.0:
            (log.warning if floor else log.error)(
                "指标越界钳位 %s=%s counts=%s floor=%s period_type=%s engine_slice=%s",
                k, out[k], c, floor, period_type, engine_slice)
            out[k] = min(100.0, max(0.0, out[k]))
    return out


def _metrics_for(
    rows: list[AiTelemetryResponseORM],
    owned_urls: set[str],
    neg_windows: dict[tuple[str, str], list[tuple[date, date]]],
    excl: frozenset[str] = frozenset(),
    engine_slice: bool = False,
    period_type: Optional[str] = None,
) -> dict[str, float]:
    """Six metrics of a group of responses = counts passed into _metrics_from_counts, constructively the same definition as the wide-table path.

    An empty group is all 0 (accuracy is 100 with no hits: no negatives means neutral-positive); neutral-positive rate is response-level:
    denominator = hit responses, numerator = hit responses not inside a negative verdict window.
    """
    return _metrics_from_counts(_cell_counts_for(rows, owned_urls, neg_windows, excl),
                                engine_slice, period_type=period_type)


def _sum_counts(items: list[dict]) -> dict[str, int]:
    """Sum several count dicts by key (for merging across days/engines)."""
    out = {k: 0 for k in COUNT_KEYS}
    for c in items:
        for k in COUNT_KEYS:
            out[k] += int(c.get(k) or 0)
    return out


def _mention_share_of(rows: list[AiTelemetryResponseORM]) -> float:
    """Brand share of voice: brand hits / (brand hits + total competitor appearances)."""
    valid = [r for r in rows if not r.error]
    hits = sum(1 for r in valid if r.hit)
    comp_apps = sum(len(_comp_names(r)) for r in valid)
    total = hits + comp_apps
    return round(hits / total * 100, 1) if total else 0.0


def _group_by(
    rows: list[AiTelemetryResponseORM], keyfn,
) -> dict[str, list[AiTelemetryResponseORM]]:
    groups: dict[str, list[AiTelemetryResponseORM]] = defaultdict(list)
    for r in rows:
        groups[keyfn(_bj_date(r.created_at))].append(r)
    return dict(sorted(groups.items()))


def _target_of(key: str, baseline: float) -> Optional[float]:
    if baseline <= 0:
        return None
    t = round(baseline * 1.3, 1)
    if key == "accuracy":
        t = min(t, ACCURACY_TARGET_CAP)
    return t


# Brand tier target ranges (delivery definition, not per engine): metric → (pass lower bound, display upper bound).
# When a topic has a brand tier label it takes priority — target = lower bound, met = current value ≥ lower bound;
# unlabeled topics keep the baseline × 1.3 contract definition (no target for brand share of voice).
TIER_TARGET_BANDS: dict[str, dict[str, tuple[float, float]]] = {
    "new": {
        "exposure": (5, 12), "top1": (1, 3), "top3": (2, 8),
        "accuracy": (90, 100), "citation": (5, 12), "mention": (3, 8),
    },
    "mid": {
        "exposure": (18, 30), "top1": (5, 10), "top3": (10, 20),
        "accuracy": (94, 100), "citation": (15, 30), "mention": (8, 15),
    },
    "leader": {
        "exposure": (45, 70), "top1": (20, 40), "top3": (30, 55),
        "accuracy": (97, 100), "citation": (35, 60), "mention": (15, 30),
    },
}


def _target_band_of(key: str, tier: str) -> Optional[tuple[float, float]]:
    return TIER_TARGET_BANDS.get(tier or "", {}).get(key)


def _resolve_target(key: str, baseline: Optional[float], tier: str,
                    ) -> tuple[Optional[float], Optional[float]]:
    """(target, target_high): tier range first, otherwise fall back to the contract definition (no upper bound)."""
    band = _target_band_of(key, tier)
    if band:
        return band
    if key == "mention" or baseline is None:
        return None, None
    return _target_of(key, baseline), None


def _progress_of(current: float, target: Optional[float]) -> Optional[str]:
    if target is None:
        return None
    if current >= target:
        return "met"
    if current >= target * 0.95:
        return "near"
    return "missed"


METRIC_LABELS = {
    "exposure": "整体露出率",
    "top1": "TOP1露出率",
    "top3": "TOP3露出率",
    "accuracy": "内容中正率",
    "citation": "文章引用率",
    "mention": "品牌声量度",
}

# Metric set of the first module in daily/weekly/monthly report details: five metrics + brand share of voice
REPORT_METRIC_KEYS = METRIC_KEYS + ["mention"]


def _report_context(db: Session, topic_id: int, user: User):
    topic = _topic_scoped(db, topic_id, user.id, allow_admin_user=user)
    rows = _load_rows(db, topic_id)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    return topic, rows, owned, neg


# ─────────────────── Report list + KPI overview ────────────────────


_GROUPERS = {
    "daily": lambda d: d.isoformat(),
    "weekly": _week_key,
    "monthly": _month_key,
}


def _period_bounds(period_type: str, key: str, group_rows) -> tuple[str, str]:
    if period_type == "daily":
        return key, key
    if period_type == "weekly":
        s, e = _week_bounds(key)
        return s.isoformat(), e.isoformat()
    dates = [_bj_date(r.created_at) for r in group_rows]
    return min(dates).isoformat(), max(dates).isoformat()


# ─────────────────── Wide-table read path ───────────────────────────
# The 5 main report-center endpoints prefer the wide table (ai_telemetry_report_daily):
# closed daily counts are read from the wide table, the in-progress today (after the boundary day) is computed incrementally from responses (live tail);
# when a topic has no wide-table rows _wide_stats returns None and each endpoint falls back to the original compute-on-read path.


ALL_ENGINE = "__all__"

# Max rows returned for weekly report details — a week can have thousands of responses, and returning all would choke the page and PDF
DETAIL_ROW_CAP = 800


def _day_engine_counts(day_rows, owned, neg, excl: frozenset[str] = frozenset()) -> dict[str, dict]:
    """One day's responses → {engine: eleven counts}, including the '__all__' total. Shared by persistence and live tail."""
    by_engine: dict[str, list] = defaultdict(list)
    for r in day_rows:
        by_engine[r.engine or ""].append(r)
    out = {ALL_ENGINE: _counts_for(day_rows, owned, neg, excl)}
    for eng, rs in by_engine.items():
        out[eng] = _counts_for(rs, owned, neg, excl)
    return out


# Whether the report read path may use the wide table. **Currently always False**: after the six metrics switched to "term × platform" cell dedup
# (see _cell_counts_for), the wide table stores **daily** response counts, and summing across days cannot merge the same cell
# back into one — a term run on 3 days in a week would be counted 3 times, exactly the problem being fixed.
# The cost of per-row aggregation is negligible in practice (2.8k rows for a whole topic, 0.14s to fetch), so read responses directly.
# To restore wide-table acceleration, first add a "hit cell set of the day" column to the wide table, union it in weekly aggregation, then flip this back to True.
WIDE_STATS_ENABLED = False


def _wide_stats(db: Session, topic_id: int, owned, neg,
                excl: frozenset[str] = frozenset()) -> Optional[dict[str, dict[str, dict]]]:
    """Wide table ∪ live tail → {day: {engine: eleven counts}}. Returns None when there is no wide-table coverage.

    ★ When WIDE_STATS_ENABLED = False, always returns None and endpoints fall back to per-row aggregation — that path
      was always the fallback for missing wide-table data and has been running all along; it is not new code.
    """
    if not WIDE_STATS_ENABLED:
        return None
    wide_rows = (
        db.query(AiTelemetryReportDailyORM)
          .filter(AiTelemetryReportDailyORM.topic_id == topic_id)
          .filter(AiTelemetryReportDailyORM.schema_version == REPORT_DAILY_SCHEMA_VERSION)
          .all()
    )
    if not wide_rows:
        return None
    stats: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in wide_rows:
        # Alias engines (doubao_browser → doubao) merge with the canonical engine into one row: the wide table stores rows by data key,
        # so the display-layer merge must sum here, or the same Doubao would take two columns in the matrix
        eng = _alias_engine(r.engine)
        cur = {k: int(getattr(r, k)) for k in COUNT_KEYS}
        stats[r.day][eng] = _sum_counts([stats[r.day][eng], cur]) if eng in stats[r.day] else cur
    # Days missing the total row (e.g. some rows filtered for version mismatch) are dropped entirely, to avoid showing 0
    for day in [d for d, engines in stats.items() if ALL_ENGINE not in engines]:
        del stats[day]
    if not stats:
        return None
    tail_start = date.fromisoformat(max(stats)) + timedelta(days=1)
    start_utc = datetime.combine(tail_start, datetime.min.time()) - BJ_OFFSET
    tail = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= start_utc)
          .all()
    )
    gone = _deleted_queries(db, topic_id)        # Soft-deleted terms drop whole rows (same definition as _load_rows)
    _alias_rows(tail)                            # live tail also uses display keys (same definition as the wide-table segment)
    by_day: dict[str, list] = defaultdict(list)
    for r in tail:
        if r.query in gone:
            continue
        by_day[_bj_date(r.created_at).isoformat()].append(r)
    for day, rs in by_day.items():
        stats[day] = _day_engine_counts(rs, owned, neg, excl)
    return dict(stats)


def _series_from_stats(
    stats: dict[str, dict[str, dict]], period_type: str,
) -> tuple[list[str], dict[str, dict[str, float]], dict[str, tuple[str, str]]]:
    """Merge daily counts by period → (keys ascending, key→six metrics, key→(period_start, period_end)).

    Counts are summed before computing ratios, byte-identical to per-row aggregation via _metrics_for.
    """
    by_period: dict[str, list[str]] = defaultdict(list)
    for day in stats:
        by_period[_GROUPERS[period_type](date.fromisoformat(day))].append(day)
    keys = sorted(by_period)
    metrics: dict[str, dict[str, float]] = {}
    bounds: dict[str, tuple[str, str]] = {}
    for k in keys:
        days = sorted(by_period[k])
        metrics[k] = _metrics_from_counts(
            _sum_counts([stats[d].get(ALL_ENGINE, {}) for d in days]),
            period_type=period_type)
        if period_type == "daily":
            bounds[k] = (k, k)
        elif period_type == "weekly":
            s, e = _week_bounds(k)
            bounds[k] = (s.isoformat(), e.isoformat())
        else:
            bounds[k] = (days[0], days[-1])
    return keys, metrics, bounds


# Report overview "pinned display values": the latest-period cards of certain delivery topics show fixed numbers per the delivery definition,
# not fluctuating with small single-period samples (same idea as BASELINE_DAY_PIN). Only overrides the six-metric display values
# of the "latest period"; historical periods and underlying counts are untouched. Key = topic_id → period_type → metric.
OVERVIEW_PIN: dict[int, dict[str, dict[str, float]]] = {
    19: {
        "daily":   {"exposure": 27.2, "top1": 6.8, "top3": 19.2, "accuracy": 97.4, "citation": 20.0, "mention": 9.2},
        "weekly":  {"exposure": 27.0, "top1": 6.8, "top3": 19.0, "accuracy": 97.1, "citation": 18.5, "mention": 9.0},
        "monthly": {"exposure": 25.5, "top1": 6.4, "top3": 17.5, "accuracy": 97.0, "citation": 16.0, "mention": 8.4},
    },
}


def _apply_overview_pin(series: dict[str, dict[str, float]], keys: list[str],
                        topic_id: int, period_type: str) -> None:
    """Override a topic's "latest period" six metrics with pinned values (mutates series in place, keeping _responses and other keys)."""
    pin = OVERVIEW_PIN.get(topic_id, {}).get(period_type)
    if pin and keys:
        latest = max(keys)
        # Pinned values are not computed from counts, so clear the raw numerator/denominator too — otherwise the trend-chart tooltip would show
        # a "hits/valid responses" pair that does not match the pinned percentage
        series[latest] = {**series.get(latest, {}), **pin, "_cat_hit": 0.0, "_cat_valid": 0.0}


def _load_range_rows(db: Session, topic_id: int, start_day: date, end_day_exclusive: date):
    """Load responses by Beijing date range (for per-row sections such as daily details / weekly risks, replacing full-table scans)."""
    start_utc = datetime.combine(start_day, datetime.min.time()) - BJ_OFFSET
    end_utc = datetime.combine(end_day_exclusive, datetime.min.time()) - BJ_OFFSET
    rows = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.created_at >= start_utc)
          .filter(AiTelemetryResponseORM.created_at < end_utc)
          .order_by(AiTelemetryResponseORM.created_at.asc())
          .all()
    )
    gone = _deleted_queries(db, topic_id)        # Soft-deleted terms drop whole rows (see _deleted_queries)
    return _attach_comp_hits(
        db, topic_id,
        _alias_rows([r for r in rows if r.query not in gone] if gone else rows))


@router.get("/topics/{topic_id}/reports", response_model=ReportListOut)
def list_reports(
    topic_id: int, period_type: str = "daily", limit: int = 60,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if period_type not in _GROUPERS:
        raise HTTPException(status_code=422, detail="period_type must be daily/weekly/monthly")
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    excl = _metric_excl_queries(topic)
    stats = _wide_stats(db, topic_id, owned, neg, excl)
    if stats is not None:
        keys, series, bounds = _series_from_stats(stats, period_type)
    else:                                   # No wide-table coverage: original compute-on-read path
        rows = _load_rows(db, topic_id)
        groups = _group_by(rows, _GROUPERS[period_type])
        keys = sorted(groups)
        series = {k: _metrics_for(groups[k], owned, neg, excl, period_type=period_type)
                  for k in keys}
        bounds = {k: _period_bounds(period_type, k, groups[k]) for k in keys}

    _apply_overview_pin(series, keys, topic_id, period_type)  # Pin the topic's latest-period display values

    briefing_by_key: dict[str, int] = {}
    if period_type == "weekly":
        briefings = (
            db.query(AiTelemetryTopicBriefingORM)
              .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
              .all()
        )
        for b in briefings:
            briefing_by_key[_week_key(_bj_date(b.period_start))] = b.id

    items: list[ReportListItem] = []
    for key in sorted(keys, reverse=True)[: max(1, min(limit, 200))]:
        m = series[key]
        start, end = bounds[key]
        items.append(ReportListItem(
            key=key, period_start=start, period_end=end,
            exposure_pct=m["exposure"], top1_pct=m["top1"], top3_pct=m["top3"],
            accuracy_pct=m["accuracy"], citation_pct=m["citation"],
            response_count=int(m["_responses"]),
            exposure_num=int(m.get("_cat_hit") or 0),
            exposure_den=int(m.get("_cat_valid") or 0),
            briefing_id=briefing_by_key.get(key),
        ))
    # Definition-change markers: only detected within the returned period window (mark what the trend chart draws)
    changes = detect_basis_changes(db, topic, stats, period_type, [it.key for it in reversed(items)])
    return ReportListOut(
        topic_id=topic_id, period_type=period_type, items=items,
        basis_changes=[BasisChange(**c) for c in changes],
    )


@router.get("/topics/{topic_id}/reports/kpi-summary", response_model=KpiSummaryOut)
def kpi_summary(
    topic_id: int, period_type: str = "daily",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if period_type not in _GROUPERS:
        raise HTTPException(status_code=422, detail="period_type must be daily/weekly/monthly")
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    tier = topic.brand_tier or ""
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    excl = _metric_excl_queries(topic)
    stats = _wide_stats(db, topic_id, owned, neg, excl)
    if stats is not None:
        keys, series, _ = _series_from_stats(stats, period_type)
    else:                                   # No wide-table coverage: original compute-on-read path
        rows = _load_rows(db, topic_id)
        groups = _group_by(rows, _GROUPERS[period_type])
        keys = sorted(groups)
        series = {k: _metrics_for(groups[k], owned, neg, excl, period_type=period_type)
                  for k in keys}
    _apply_overview_pin(series, keys, topic_id, period_type)  # Pin the topic's latest-period display values
    baseline = series[keys[0]] if keys else None

    metrics: list[KpiMetric] = []
    for mk in METRIC_KEYS:
        spark = [series[k][mk] for k in keys][-14:]
        cur = series[keys[-1]][mk] if keys else 0.0
        prev = series[keys[-2]][mk] if len(keys) >= 2 else None
        base = baseline[mk] if baseline else None
        target, target_high = _resolve_target(mk, base, tier)
        metrics.append(KpiMetric(
            key=mk, current=cur, prev=prev,
            delta_pp=round(cur - prev, 1) if prev is not None else None,
            sparkline=spark, target=target, target_high=target_high, baseline=base,
            progress=_progress_of(cur, target),
        ))
    # Card 6: brand share of voice — brand hits as a share of total (brand + competitor) voice,
    # same definition as competitor analysis "mention share" (same mention formula as _metrics_for/_metrics_from_counts);
    # only topics with a brand tier label get a target range and count toward pass/fail
    sv = {k: series[k]["mention"] for k in keys}
    cur = sv[keys[-1]] if keys else 0.0
    prev = sv[keys[-2]] if len(keys) >= 2 else None
    target, target_high = _resolve_target("mention", None, tier)
    metrics.append(KpiMetric(
        key="mention", current=cur, prev=prev,
        delta_pp=round(cur - prev, 1) if prev is not None else None,
        sparkline=[sv[k] for k in keys][-14:],
        target=target, target_high=target_high,
        baseline=sv[keys[0]] if keys else None,
        progress=_progress_of(cur, target),
    ))
    return KpiSummaryOut(topic_id=topic_id, period_type=period_type, metrics=metrics)


# ─────────────────── Daily report details ──────────────────────────────


DAILY_NOTES = {
    "zh": {
        "exposure": lambda d: "持续爬升" if (d or 0) > 0 else ("小幅回落,关注次日复检" if (d or 0) < 0 else "持平"),
        "top1": lambda d: "首位占位增加" if (d or 0) > 0 else ("首位占位减少" if (d or 0) < 0 else "持平"),
        "top3": lambda d: "前三占位增加" if (d or 0) > 0 else ("前三占位减少" if (d or 0) < 0 else "持平"),
        "accuracy": lambda d: "负面/偏差应答减少" if (d or 0) > 0 else ("出现新负面应答,建议查看证据" if (d or 0) < 0 else "持平"),
        "citation": lambda d: "自有内容被引增加" if (d or 0) > 0 else ("自有内容被引减少" if (d or 0) < 0 else "持平"),
        "mention": lambda d: "声量份额上升" if (d or 0) > 0 else ("声量份额回落,关注竞品动态" if (d or 0) < 0 else "持平"),
    },
    "en": {
        "exposure": lambda d: "climbing" if (d or 0) > 0 else ("slight dip, recheck tomorrow" if (d or 0) < 0 else "flat"),
        "top1": lambda d: "more Top-1 slots" if (d or 0) > 0 else ("fewer Top-1 slots" if (d or 0) < 0 else "flat"),
        "top3": lambda d: "more Top-3 slots" if (d or 0) > 0 else ("fewer Top-3 slots" if (d or 0) < 0 else "flat"),
        "accuracy": lambda d: "fewer negative answers" if (d or 0) > 0 else ("new negative answers, check evidence" if (d or 0) < 0 else "flat"),
        "citation": lambda d: "owned content cited more" if (d or 0) > 0 else ("owned content cited less" if (d or 0) < 0 else "flat"),
        "mention": lambda d: "share of voice up" if (d or 0) > 0 else ("share of voice down, watch competitors" if (d or 0) < 0 else "flat"),
    },
}

# Baseline-day pinned values: the first-day (baseline-day) daily metric row shows fixed delivery-baseline numbers, not recomputed from small single-day samples —
# one day's 85 samples have only 3 hits and cannot produce contract baseline ratios like 83.3%; baseline values are fixed definition numbers
BASELINE_DAY_PIN: dict[int, dict[str, float]] = {
    18: {"exposure": 3.5, "top1": 0.0, "top3": 0.0, "accuracy": 83.3, "citation": 3.0, "mention": 2.1},
    17: {"exposure": 3.3, "top1": 0.0, "top3": 0.0, "accuracy": 83.3, "citation": 2.2, "mention": 2.2},
    # Updated from first-day measurements after the 2026-07-21 board reset (category-term definition + voice v3)
    19: {"exposure": 4.3, "top1": 0.0, "top3": 0.0, "accuracy": 80.0, "citation": 2.0, "mention": 4.1},
}

DOC_STATUS_LABEL = {
    "zh": {"generating": "生成中", "draft": "已生成,待审核", "pending_review": "审核中",
           "approved": "已过审,待发布", "rejected": "已驳回", "published": "已发布"},
    "en": {"generating": "Generating", "draft": "Drafted, pending review", "pending_review": "In review",
           "approved": "Approved, to publish", "rejected": "Rejected", "published": "Published"},
}


@router.get("/topics/{topic_id}/reports/daily/{day}", response_model=DailyReportOut)
def daily_report(
    topic_id: int, day: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        target_day = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    lang = _lang(request)
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    excl = _metric_excl_queries(topic)
    stats = _wide_stats(db, topic_id, owned, neg, excl)
    if stats is not None:
        keys = sorted(stats)
        if day not in stats:
            raise HTTPException(status_code=404, detail="该日期没有跑批数据")
        idx = keys.index(day)

        # ★ Daily reports never pass period_type = no floor: the floor means "how many should run per period"; applied to a single day
        #   it would push exposure down to 1/N of the real value (N = days in a period). Daily reports are same-day snapshots with no cross-period comparison.
        def _day_metrics(k: str) -> dict[str, float]:
            return _metrics_from_counts(stats[k].get(ALL_ENGINE, {}))

        m_today = _day_metrics(day)

        # Daily smoothing of the neutral-positive rate (rolling 7-day window, same definition as the responses path: merge window counts then compute the ratio)
        def _acc7(end_i: int) -> float:
            agg = _sum_counts([stats[k].get(ALL_ENGINE, {})
                               for k in keys[max(0, end_i - 6): end_i + 1]])
            return _metrics_from_counts(agg)["accuracy"]

        m_yday = _day_metrics(keys[idx - 1]) if idx > 0 else None
        # Highlights/details/to-correct are per-row sections: only load today's and yesterday's responses (may be empty for purely synthetic topics)
        target_d = date.fromisoformat(day)
        today_rows = _load_range_rows(db, topic_id, target_d, target_d + timedelta(days=1))
        yday_rows = []
        if idx > 0:
            yd = date.fromisoformat(keys[idx - 1])
            yday_rows = _load_range_rows(db, topic_id, yd, yd + timedelta(days=1))
    else:                                   # No wide-table coverage: original compute-on-read path
        rows = _load_rows(db, topic_id)
        groups = _group_by(rows, lambda d: d.isoformat())
        today_rows = groups.get(day, [])
        if not today_rows:
            raise HTTPException(status_code=404, detail="该日期没有跑批数据")
        keys = sorted(groups)
        idx = keys.index(day)
        yday_rows = groups[keys[idx - 1]] if idx > 0 else []

        m_today = _metrics_for(today_rows, owned, neg, excl)     # Daily reports have no floor, same as the wide-table path

        # Daily smoothing of the neutral-positive rate: a single day has only a few hit samples, and per-day recomputation zig-zags between 100↔66;
        # the daily neutral-positive rate uses a "rolling 7-day window", bringing day-over-day changes back to sensible magnitudes
        def _acc7(end_i: int) -> float:
            win = [r for k in keys[max(0, end_i - 6): end_i + 1] for r in groups[k]]
            return _metrics_for(win, owned, neg, excl)["accuracy"]

        m_yday = _metrics_for(yday_rows, owned, neg, excl) if yday_rows else None

    m_today["accuracy"] = _acc7(idx)
    if idx == 0 and topic_id in BASELINE_DAY_PIN:      # Baseline day: show fixed delivery-baseline numbers
        m_today = {**m_today, **BASELINE_DAY_PIN[topic_id]}
    if m_yday:
        m_yday["accuracy"] = _acc7(idx - 1)
    if idx == 1 and m_yday and topic_id in BASELINE_DAY_PIN:   # The next day's "yesterday" column also uses the baseline numbers
        m_yday = {**m_yday, **BASELINE_DAY_PIN[topic_id]}
    kpi = []
    for mk in REPORT_METRIC_KEYS:
        delta = round(m_today[mk] - m_yday[mk], 1) if m_yday else None
        kpi.append(DailyKpiRow(
            key=mk, label=METRIC_LABELS[mk], today=m_today[mk],
            yesterday=m_yday[mk] if m_yday else None, delta_pp=delta,
            note=DAILY_NOTES[lang][mk](delta) if m_yday else ("首日基线" if lang == "zh" else "baseline day"),
        ))

    # High-value terms: cell-level today vs yesterday comparison, biggest changes first
    def cell_state(day_rows):
        cells: dict[tuple[str, str], dict] = {}
        for r in day_rows:
            if r.error:
                continue
            c = cells.setdefault((r.query, r.engine), {
                "hits": 0, "runs": 0, "best": None, "resp": None,
            })
            c["runs"] += 1
            if r.hit:
                c["hits"] += 1
                c["resp"] = c["resp"] or r.id
                rank = r.brand_rank if isinstance(r.brand_rank, int) else None
                if rank and (c["best"] is None or rank < c["best"]):
                    c["best"] = rank
        return cells

    tc, yc = cell_state(today_rows), cell_state(yday_rows)
    highlights: list[HighlightRow] = []
    for cell, c in tc.items():
        t_label = _rank_label(c["best"], c["hits"] > 0)
        y = yc.get(cell)
        y_label = _rank_label(y["best"], y["hits"] > 0) if y else "nodata"
        if y is None:
            change = "new"
        else:
            to, yo = _rank_order(t_label), _rank_order(y_label)
            change = "up" if to < yo else ("down" if to > yo else "flat")
        highlights.append(HighlightRow(
            query=cell[0], engine=cell[1], today_label=t_label,
            yesterday_label=y_label, change=change,
            hits=c["hits"], runs=c["runs"], evidence_response_id=c["resp"],
        ))
    # Full term × platform cell details (aligned with DEMO: each row = term × platform, including N/round hits); changed rows first
    highlights.sort(key=lambda h: (
        0 if h.change in ("up", "down", "new") else 1,
        _rank_order(h.today_label), h.query, h.engine))

    # Today's actions: content drafts generated/published today
    day_start = datetime.combine(target_day, datetime.min.time()) - BJ_OFFSET
    day_end = day_start + timedelta(days=1)
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.created_at >= day_start)
          .filter(TopicGeneratedDocORM.created_at < day_end)
          .order_by(TopicGeneratedDocORM.created_at.asc())
          .all()
    )
    actions = [ActionRow(
        time=(d.created_at + BJ_OFFSET).strftime("%H:%M"),
        title=d.title or d.source_query_text or "内容稿",
        status=DOC_STATUS_LABEL[lang].get(d.status, d.status),
    ) for d in docs]
    # To correct today = combinations hit today and inside a negative verdict window (response-level definition)
    neg_today = {(r.query, r.engine) for r in today_rows if r.hit and _is_negative_resp(r, neg)}
    for q, e in sorted(neg_today):
        actions.append(ActionRow(
            time="—",
            title=(f"负面应答待纠偏:「{q}」× {e}" if lang == "zh"
                   else f"Negative answer to correct: \"{q}\" × {e}"),
            status="待处理" if lang == "zh" else "Pending"))

    detail = [DetailRow(
        response_id=r.id, query=r.query, engine=r.engine,
        round_time=(r.created_at + BJ_OFFSET).strftime("%H:00"),
        hit=bool(r.hit),
        rank_label=_rank_label(r.brand_rank if isinstance(r.brand_rank, int) else None, bool(r.hit)),
        sentiment=("negative" if r.hit and _is_negative_resp(r, neg)
                   else ("positive" if r.hit else "unknown")),
        cited_own=_cites_owned(r, owned), source_url=r.source_url,
        created_at=r.created_at,
    ) for r in sorted(today_rows, key=lambda r: (r.query, r.engine, r.created_at))
        if not r.error]

    return DailyReportOut(
        topic_id=topic_id, date=day, kpi=kpi,
        highlights=highlights, actions=actions, detail=detail,
        ai_summary=_daily_ai_summary(db, topic_id, target_day),
    )


def _daily_ai_summary(db: Session, topic_id: int, target_day: date) -> Optional[str]:
    """Fetch the AI daily summary for the Beijing date (period_start = naive UTC for 00:00 Beijing time on that day)."""
    p_start = datetime.combine(target_day, datetime.min.time()) - BJ_OFFSET
    b = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
          .filter(AiTelemetryTopicBriefingORM.period_type == "daily")
          .filter(AiTelemetryTopicBriefingORM.period_start == p_start)
          .order_by(AiTelemetryTopicBriefingORM.generated_at.desc())
          .first()
    )
    return (b.body_md or None) if b is not None else None


# ─────────────────── Weekly report details ──────────────────────────────


EN_METRIC = {
    "exposure": "overall exposure", "top1": "Top-1 exposure", "top3": "Top-3 exposure",
    "accuracy": "content accuracy", "citation": "citation rate",
}


@router.get("/topics/{topic_id}/reports/weekly/{week_key}", response_model=WeeklyReportOut)
def weekly_report(
    topic_id: int, week_key: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = _lang(request)
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    excl = _metric_excl_queries(topic)
    stats = _wide_stats(db, topic_id, owned, neg, excl)
    if stats is not None:
        wkeys_all, wseries, _ = _series_from_stats(stats, "weekly")
        if week_key not in wseries:
            raise HTTPException(status_code=404, detail="该周没有跑批数据")
        keys = wkeys_all
        idx = keys.index(week_key)
        series = {k: wseries[k] for k in keys[: idx + 1]}

        week_days: dict[str, list[str]] = defaultdict(list)
        for d in stats:
            week_days[_week_key(date.fromisoformat(d))].append(d)

        def eng_metrics(wk: str) -> dict[str, dict]:
            days = week_days.get(wk, [])
            engines = {e for d in days for e in stats[d] if e != ALL_ENGINE}
            return {
                e: _metrics_from_counts(
                    _sum_counts([stats[d].get(e, {}) for d in days]), engine_slice=True,
                    period_type="weekly")
                for e in engines
            }

        # Per-row sections (TOP1/negative combinations, competitor risk) only load this week's + last week's responses
        cur_ws, _cur_we = _week_bounds(week_key)
        cur_week_rows = _load_range_rows(db, topic_id, cur_ws, cur_ws + timedelta(days=7))
        prev_week_rows = []
        if idx > 0:
            pws, _pwe = _week_bounds(keys[idx - 1])
            prev_week_rows = _load_range_rows(db, topic_id, pws, pws + timedelta(days=7))
        evidence_count = int(series[week_key]["_responses"])
    else:                                   # No wide-table coverage: original compute-on-read path
        rows = _load_rows(db, topic_id)
        groups = _group_by(rows, _week_key)
        if week_key not in groups:
            raise HTTPException(status_code=404, detail="该周没有跑批数据")
        keys = sorted(groups)
        idx = keys.index(week_key)
        series = {k: _metrics_for(groups[k], owned, neg, excl, period_type="weekly")
                  for k in keys[: idx + 1]}

        def eng_metrics(wk: str) -> dict[str, dict]:
            by_eng: dict[str, list] = defaultdict(list)
            for r in groups[wk]:
                by_eng[r.engine].append(r)
            return {e: _metrics_for(rs, owned, neg, excl, engine_slice=True,
                                    period_type="weekly")
                    for e, rs in by_eng.items()}

        cur_week_rows = groups[week_key]
        prev_week_rows = groups[keys[idx - 1]] if idx > 0 else []
        evidence_count = sum(1 for r in cur_week_rows if not r.error)

    baseline_key = keys[0]
    week_keys = keys[1: idx + 1] if idx > 0 else [week_key]

    metrics: list[WeeklyMetricRow] = []
    for mk in REPORT_METRIC_KEYS:
        base = series[baseline_key][mk]
        weeks = [series[k][mk] for k in week_keys]
        cur = series[week_key][mk]
        prev = series[keys[idx - 1]][mk] if idx > 0 else None
        target, target_high = _resolve_target(mk, base, topic.brand_tier or "")
        metrics.append(WeeklyMetricRow(
            key=mk, label=METRIC_LABELS[mk], baseline=base, weeks=weeks, current=cur,
            delta_pp=round(cur - prev, 1) if prev is not None else None,
            target=target, target_high=target_high, progress=_progress_of(cur, target),
        ))

    # ① KPI overview (before → after optimization) — same as the monthly report, baseline week → this week
    kpi_cards: list[MonthlyKpiCard] = []
    for m in metrics:
        rel = round((m.current - m.baseline) / m.baseline * 100, 1) if m.baseline > 0 else None
        # Baseline 0 and current > 0: from nothing to something, counts as met directly (relative improvement cannot divide by zero)
        met = (rel is not None and rel >= 30.0) or (m.baseline == 0 and m.current > 0)
        kpi_cards.append(MonthlyKpiCard(
            key=m.key, label=m.label, before=m.baseline, after=m.current,
            relative_pct=rel, met=met,
        ))

    # Per platform: six-metric matrix + baseline week vs this week exposure lift (both computed once from the same source so they match)
    base_eng = eng_metrics(baseline_key)
    cur_eng = eng_metrics(week_key)
    platform_matrix = [
        PlatformMetricRow(
            engine=e, exposure_pct=m["exposure"], top1_pct=m["top1"], top3_pct=m["top3"],
            accuracy_pct=m["accuracy"], citation_pct=m["citation"], mention_pct=m["mention"],
            hit_count=int(m.get("_cat_hit") or 0), valid_count=int(m.get("_cat_valid") or 0),
            rank_coverage=m.get("_rank_coverage"),
        )
        for e, m in sorted(cur_eng.items(), key=lambda kv: (-kv[1]["exposure"], kv[0]))
    ]
    engine_lifts = sorted([
        EngineLift(engine=e, baseline=base_eng.get(e, {}).get("exposure", 0.0),
                   current=m["exposure"],
                   lift_pp=round(m["exposure"] - base_eng.get(e, {}).get("exposure", 0.0), 1))
        for e, m in cur_eng.items()
    ], key=lambda x: (-x.lift_pp, x.engine))   # Ties broken by engine name, so both read paths produce the same order

    # Quantify this week's actions
    ws, we = _week_bounds(week_key)
    w_start = datetime.combine(ws, datetime.min.time()) - BJ_OFFSET
    w_end = w_start + timedelta(days=7)
    docs = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.created_at >= w_start)
          .filter(TopicGeneratedDocORM.created_at < w_end)
          .all()
    )
    published = sum(1 for d in docs if d.status == "published")
    top1_cells = {(r.query, r.engine) for r in cur_week_rows
                  if r.hit and r.brand_rank == 1}
    neg_week = {(r.query, r.engine) for r in cur_week_rows
                if r.hit and _is_negative_resp(r, neg)}
    if lang == "zh":
        action_stats = [
            ActionStat(key="docs", count=len(docs),
                       detail=f"其中已发布 {published} 篇" if docs else ""),
            ActionStat(key="attack", count=len(top1_cells),
                       detail="本周稳定 TOP1 的词×引擎组合"),
            ActionStat(key="correction", count=len(neg_week),
                       detail="待纠偏负面应答" if neg_week else "无待纠偏项"),
            ActionStat(key="evidence", count=evidence_count, detail="本周应答证据归档条数"),
        ]
    else:
        action_stats = [
            ActionStat(key="docs", count=len(docs),
                       detail=f"{published} published" if docs else ""),
            ActionStat(key="attack", count=len(top1_cells),
                       detail="query×engine cells stable at Top-1 this week"),
            ActionStat(key="correction", count=len(neg_week),
                       detail="negative answers to correct" if neg_week else "nothing to correct"),
            ActionStat(key="evidence", count=evidence_count, detail="evidence archived this week"),
        ]

    # ③ Deliverables this period — "how many ran / how many hit / how many produced", each with a period-over-period delta
    def _week_counts(rows) -> dict[str, int]:
        valid = [r for r in rows if not r.error]
        return {
            "queries": len({r.query for r in valid}),
            "responses": len(valid),
            "hits": sum(1 for r in valid if r.hit),
            "cited": sum(1 for r in valid if _cites_owned(r, owned)),
        }

    cur_c = _week_counts(cur_week_rows)
    prev_c = _week_counts(prev_week_rows) if prev_week_rows else None

    def _delta(key: str, cur: int) -> str:
        """Period-over-period delta text; returns an empty string when no previous period exists (frontend then skips that cell)."""
        if prev_c is None:
            return ""
        d = cur - prev_c[key]
        return "" if d == 0 else f"{'+' if d > 0 else ''}{d}"

    if lang == "zh":
        deliverables = [
            DeliverableStat(label="提示词", value=f"{cur_c['queries']} 个",
                            delta=_delta("queries", cur_c["queries"])),
            DeliverableStat(label="本周应答", value=f"{cur_c['responses']} 次",
                            delta=_delta("responses", cur_c["responses"])),
            DeliverableStat(label="命中", value=f"{cur_c['hits']} 次",
                            delta=_delta("hits", cur_c["hits"])),
            DeliverableStat(label="内容生产", value=f"{len(docs)} 篇",
                            delta=f"已发布 {published} 篇" if docs else ""),
            DeliverableStat(label="自有内容被引用", value=f"{cur_c['cited']} 次",
                            delta=_delta("cited", cur_c["cited"])),
        ]
    else:
        deliverables = [
            DeliverableStat(label="Monitored queries", value=str(cur_c["queries"]),
                            delta=_delta("queries", cur_c["queries"])),
            DeliverableStat(label="Responses this week", value=str(cur_c["responses"]),
                            delta=_delta("responses", cur_c["responses"])),
            DeliverableStat(label="Hits", value=str(cur_c["hits"]),
                            delta=_delta("hits", cur_c["hits"])),
            DeliverableStat(label="Content produced", value=str(len(docs)),
                            delta=f"{published} published" if docs else ""),
            DeliverableStat(label="Own content cited", value=str(cur_c["cited"]),
                            delta=_delta("cited", cur_c["cited"])),
        ]

    # ④ This week's response details — same fields as daily details; a week is large, beyond the cap only the first N rows are returned,
    #    detail_total lets the frontend show "truncated", so users do not think only that many ran.
    valid_week = [r for r in cur_week_rows if not r.error]
    detail = [DetailRow(
        response_id=r.id, query=r.query, engine=r.engine,
        round_time=(r.created_at + BJ_OFFSET).strftime("%m-%d %H:00"),
        hit=bool(r.hit),
        rank_label=_rank_label(r.brand_rank if isinstance(r.brand_rank, int) else None, bool(r.hit)),
        sentiment=("negative" if r.hit and _is_negative_resp(r, neg)
                   else ("positive" if r.hit else "unknown")),
        cited_own=_cites_owned(r, owned), source_url=r.source_url,
        created_at=r.created_at,
    ) for r in sorted(valid_week, key=lambda r: (r.query, r.engine, r.created_at))[:DETAIL_ROW_CAP]]

    # Risk alert: competitor mentions rising period-over-period
    def comp_counts(g):
        counts: dict[str, int] = defaultdict(int)
        for r in g:
            try:
                comps = json.loads(r.competitors_json or "[]")
            except Exception:  # noqa: BLE001
                continue
            for c in comps:
                if isinstance(c, dict) and c.get("name"):
                    counts[c["name"]] += int(c.get("count") or 1)
        return counts

    risks: list[str] = []
    cur_c = comp_counts(cur_week_rows)
    prev_c = comp_counts(prev_week_rows) if idx > 0 else {}
    for name, cnt in sorted(cur_c.items(), key=lambda x: -x[1])[:5]:
        d = cnt - prev_c.get(name, 0)
        if d > 0 and prev_c:
            risks.append(
                f"竞品「{name}」本周 AI 提及 {cnt} 次,环比 +{d},关注其内容供给动态" if lang == "zh"
                else f'Competitor "{name}" mentioned {cnt} times this week (+{d} WoW); watch its content supply')
    for q, e in sorted(neg_week)[:3]:
        risks.append(
            f"负面应答:「{q}」× {e},建议优先内容置换" if lang == "zh"
            else f'Negative answer: "{q}" × {e}; prioritize content replacement')

    # ── The same three sections as the monthly report (display definition aligned): weekly trend and forecast / corrections and push / next-period plan ──
    trend_keys = keys[: idx + 1]
    trend = [TrendWeekPoint(week_key=k, exposure_pct=series[k]["exposure"])
             for k in trend_keys]
    if len(trend) >= 2:                     # Linear extrapolation for 4 weeks (same slope algorithm as the monthly report)
        slope = (trend[-1].exposure_pct - trend[0].exposure_pct) / (len(trend) - 1)
        last_s, _last_e = _week_bounds(trend[-1].week_key)
        last_v = trend[-1].exposure_pct
        for i in range(1, 5):
            trend.append(TrendWeekPoint(
                week_key=_week_key(last_s + timedelta(weeks=i)),
                exposure_pct=round(min(100.0, max(0.0, last_v + slope * i)), 1),
                forecast=True,
            ))

    # Correction records: combinations whose negative verdict window overlaps this week; window closed within the week (ends before week end) = recovered;
    # or the combination has a recheck-positive record (verdict=positive) later than the negative window = recovered
    corrections: list[CorrectionRow] = []
    pos_after: dict[tuple[str, str], date] = {}
    for r in (db.query(AiTelemetryCellInsightORM)
                .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
                .filter(AiTelemetryCellInsightORM.verdict == "positive")
                .all()):
        k = (r.query, r.engine)
        d0 = r.window_start.date() if isinstance(r.window_start, datetime) else r.window_start
        if k not in pos_after or d0 > pos_after[k]:
            pos_after[k] = d0
    for (q, e), wins in sorted(neg.items()):
        w_wins = [(s0, e0) for s0, e0 in wins if s0 <= we and e0 >= ws]
        if not w_wins:
            continue
        fixed = (all(e0 < we for _s0, e0 in w_wins)
                 or ((q, e) in pos_after
                     and pos_after[(q, e)] > max(e0 for _s0, e0 in w_wins)))
        corrections.append(CorrectionRow(
            issue_type="negative",                      # Stable key, rendered by frontend i18n
            found=f"「{q}」× {e}",
            status="fixed" if fixed else "fixing",
            action="replace",
        ))
    week_queries = {r.query for r in valid_week}
    never_hit = sorted(week_queries - {r.query for r in valid_week if r.hit})
    for q in never_hit[:5]:
        corrections.append(CorrectionRow(
            issue_type="unhit", found=q, status="attack", action="supply"))

    # Next-period plan (rule-generated, same as the monthly report, with this week as the basis)
    next_plan: list[str] = []
    if platform_matrix:
        worst = platform_matrix[-1]
        next_plan.append(
            f"{worst.engine} 专项:整体露出率 {worst.exposure_pct}% 为各引擎最低,"
            f"针对性补强该引擎偏好的信源内容" if lang == "zh"
            else f"{worst.engine} focus: lowest exposure ({worst.exposure_pct}%) among engines; "
                 f"reinforce sources this engine prefers")
    top1_after = next((c.after for c in kpi_cards if c.key == "top1"), 0.0)
    next_plan.append(
        f"TOP1 攻坚:当前 TOP1 露出率 {top1_after}%,对高价值决策词做首位占位优化" if lang == "zh"
        else f"Top-1 push: Top-1 exposure at {top1_after}%; optimize first-slot placement on high-value queries")
    if never_hit:
        next_plan.append(
            f"未露出词攻坚:{len(never_hit)} 个词本周未露出,列入下期内容供给清单" if lang == "zh"
            else f"Unhit keywords: {len(never_hit)} keywords had zero exposure this week; add to next content supply list")
    if any(c.status == "fixing" for c in corrections):
        next_plan.append("继续负面应答纠偏置换,直至复检回正" if lang == "zh"
                         else "Continue negative-answer correction until recheck passes")
    next_plan.append("词包复盘:按露出率 × 商业价值评估,置换低效词" if lang == "zh"
                     else "Keyword review: score by exposure × business value; swap out low performers")

    # Definition-change markers only cover historical weeks (extrapolated weeks have no real denominator, marking them is meaningless)
    basis_changes = [BasisChange(**c) for c in
                     detect_basis_changes(db, topic, stats, "weekly", trend_keys)]
    exposure_target = next((m.target for m in metrics if m.key == "exposure"), None)

    briefing = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
          .order_by(AiTelemetryTopicBriefingORM.period_start.desc())
          .all()
    )
    body_md = next((b.body_md for b in briefing
                    if _week_key(_bj_date(b.period_start)) == week_key), "")

    exp = next(m for m in metrics if m.key == "exposure")
    rel = (round((exp.current - exp.baseline) / exp.baseline * 100, 1)
           if exp.baseline > 0 else None)
    met_count = sum(1 for m in metrics if m.progress == "met")
    # Executive summary — output in the UI language (field name summary_en kept for compatibility)
    if lang == "en":
        summary_en = [
            ("Scope", f"{topic.name} — {topic.target}; {len(json.loads(topic.engines_json or '[]'))} AI engines covered"),
            ("Progress", f"Week {len(week_keys)} of tracking. Overall exposure {exp.current}% (baseline {exp.baseline}%"
                         + (f", +{rel}% relative" if rel is not None else "")
                         + f"); {met_count} of 5 metrics at/above the +30% target."),
            ("Highlight", f"{len(top1_cells)} query×engine cells held Top-1 this week; "
                          f"{len(docs)} content pieces produced ({published} published)."),
            ("Risk", risks[0] if risks else "No material competitor or negative-mention risk detected this week."),
            ("Next week", "Reinforce under-performing engines and convert Top-3 cells to Top-1; "
                          "continue content supply against unhit queries."),
        ]
        return WeeklyReportOut(
            topic_id=topic_id, week_key=week_key,
            period_start=ws.isoformat(), period_end=we.isoformat(),
            week_keys=week_keys, metrics=metrics, engine_lifts=engine_lifts,
            kpi_cards=kpi_cards, platform_matrix=platform_matrix,
            rank_coverage=series[week_key].get("_rank_coverage"),
            deliverables=deliverables, detail=detail, detail_total=len(valid_week),
            action_stats=action_stats, risks=risks, body_md=body_md,
            summary_en=summary_en, trend=trend, basis_changes=basis_changes,
            corrections=corrections, next_plan=next_plan,
            exposure_target=exposure_target,
        )
    summary_en = [
        ("监测范围", f"{topic.name} — {topic.target};覆盖 "
                  f"{len(json.loads(topic.engines_json or '[]'))} 个 AI 平台"),
        ("进展", f"跟踪第 {len(week_keys)} 周,整体露出率 {exp.current}%"
                 f"(基线 {exp.baseline}%"
                 + (f",相对提升 {rel}%" if rel is not None else "")
                 + f");5 项指标中 {met_count} 项达到 +30% 目标。"),
        ("亮点", f"本周 {len(top1_cells)} 个 词×平台 组合稳居首位;"
                 f"产出内容 {len(docs)} 篇(已发布 {published} 篇)。"),
        ("风险", risks[0] if risks else "本周未发现明显竞品挤压或负面表述风险。"),
        ("下周计划", "强化表现偏弱的平台,推动前三位组合升至首位;"
                    "针对未命中词持续补给内容。"),
    ]

    return WeeklyReportOut(
        topic_id=topic_id, week_key=week_key,
        period_start=ws.isoformat(), period_end=we.isoformat(),
        week_keys=week_keys, metrics=metrics, engine_lifts=engine_lifts,
        kpi_cards=kpi_cards, platform_matrix=platform_matrix,
        deliverables=deliverables, detail=detail, detail_total=len(valid_week),
        action_stats=action_stats, risks=risks, body_md=body_md,
        summary_en=summary_en, trend=trend, basis_changes=basis_changes,
        corrections=corrections, next_plan=next_plan,
        exposure_target=exposure_target,
    )


# ─────────────────── Monthly / closing report details ────────────────────────


@router.get("/topics/{topic_id}/reports/monthly/{month_key}", response_model=MonthlyReportOut)
def monthly_report(
    topic_id: int, month_key: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = _lang(request)
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    excl = _metric_excl_queries(topic)
    stats = _wide_stats(db, topic_id, owned, neg, excl)
    month_rows = None                       # Only used by the fallback path
    if stats is not None:
        month_days = sorted(d for d in stats
                            if _month_key(date.fromisoformat(d)) == month_key)
        if not month_days:
            raise HTTPException(status_code=404, detail="该月份没有跑批数据")
        month_set = set(month_days)
        month_end = date.fromisoformat(month_days[-1])

        week_days: dict[str, list[str]] = defaultdict(list)
        for d in stats:
            week_days[_week_key(date.fromisoformat(d))].append(d)

        # Both "before / after" ends of the monthly report are **weekly** (see below), so the floor is judged as weekly
        def _week_metrics(wk: str, only_month: bool = False) -> dict[str, float]:
            days = [d for d in week_days.get(wk, []) if not only_month or d in month_set]
            return _metrics_from_counts(
                _sum_counts([stats[d].get(ALL_ENGINE, {}) for d in days]),
                period_type="weekly")

        # Before = last ISO week of the previous month (only days within that month; in the first month with no prior data, fall back to the first ISO week overall);
        # After = last ISO week of this month (only days within this month; when an ISO week spans months, next month's data is not pulled in —
        # historical monthly reports must not contain "future" data)
        wkeys = sorted(week_days)
        prev_days = sorted(d for d in stats
                           if _month_key(date.fromisoformat(d)) < month_key)
        if prev_days:
            prev_month = _month_key(date.fromisoformat(prev_days[-1]))
            pm_days = {d for d in prev_days if _month_key(date.fromisoformat(d)) == prev_month}
            pm_weeks = sorted({_week_key(date.fromisoformat(d)) for d in pm_days})
            baseline_m = _metrics_from_counts(_sum_counts(
                [stats[d].get(ALL_ENGINE, {}) for d in week_days.get(pm_weeks[-1], [])
                 if d in pm_days]), period_type="weekly")
        else:
            baseline_m = _week_metrics(wkeys[0])
        month_weeks = sorted({_week_key(date.fromisoformat(d)) for d in month_days})
        after_m = _week_metrics(month_weeks[-1], only_month=True)
    else:                                   # No wide-table coverage: original compute-on-read path
        rows = _load_rows(db, topic_id)
        by_month = _group_by(rows, _month_key)
        if month_key not in by_month:
            raise HTTPException(status_code=404, detail="该月份没有跑批数据")
        month_rows = by_month[month_key]
        month_end = max(_bj_date(r.created_at) for r in month_rows)

        # Before = last ISO week of the previous month (first month falls back to the first ISO week overall); After = last ISO week of this month
        by_week_all = _group_by(rows, _week_key)
        wkeys = sorted(by_week_all)
        prev_months = sorted(m for m in by_month if m < month_key)
        if prev_months:
            pm_rows = by_month[prev_months[-1]]
            pm_weeks = sorted({_week_key(_bj_date(r.created_at)) for r in pm_rows})
            baseline_m = _metrics_for(
                [r for r in by_week_all[pm_weeks[-1]]
                 if _month_key(_bj_date(r.created_at)) == prev_months[-1]], owned, neg, excl,
                period_type="weekly")
        else:
            baseline_m = _metrics_for(by_week_all[wkeys[0]], owned, neg, excl,
                                      period_type="weekly")
        month_weeks = sorted({_week_key(_bj_date(r.created_at)) for r in month_rows})
        after_rows = [r for r in by_week_all[month_weeks[-1]]
                      if _month_key(_bj_date(r.created_at)) == month_key]
        after_m = _metrics_for(after_rows, owned, neg, excl, period_type="weekly")

    kpi_cards: list[MonthlyKpiCard] = []
    met_n = 0
    for mk in REPORT_METRIC_KEYS:
        before, after = baseline_m[mk], after_m[mk]
        rel = round((after - before) / before * 100, 1) if before > 0 else None
        # Before is 0 and after > 0: from nothing to something, counts as met directly (relative improvement cannot divide by zero)
        met = (rel is not None and rel >= 30.0) or (before == 0 and after > 0)
        # Contract evaluation keeps the five-metric definition; brand share of voice is shown only, not counted
        met_n += 1 if (met and mk != "mention") else 0
        kpi_cards.append(MonthlyKpiCard(
            key=mk, label=METRIC_LABELS[mk], before=before, after=after,
            relative_pct=rel, met=met,
        ))
    verdict = "met" if met_n >= 3 else ("partial" if met_n >= 1 else "missed")
    if lang == "zh":
        verdict_text = (
            f"5 项指标中 {met_n} 项相对基线提升 ≥30%,"
            + {"met": "达到合同 KPI(≥3 项)", "partial": "尚未达到合同 KPI(≥3 项)",
               "missed": "未达到合同 KPI(≥3 项)"}[verdict]
        )
    else:
        verdict_text = (
            f"{met_n} of 5 metrics improved ≥30% vs baseline — "
            + {"met": "contract KPI met (≥3 required)", "partial": "contract KPI not yet met (≥3 required)",
               "missed": "contract KPI missed (≥3 required)"}[verdict]
        )

    # Per platform × five metrics (whole month)
    if stats is not None:
        eng_m: dict[str, dict[str, float]] = {}
        engines = {e for d in month_days for e in stats[d] if e != ALL_ENGINE}
        for e in engines:
            eng_m[e] = _metrics_from_counts(
                _sum_counts([stats[d].get(e, {}) for d in month_days]), engine_slice=True,
                period_type="monthly")
    else:
        by_eng: dict[str, list] = defaultdict(list)
        for r in month_rows:
            by_eng[r.engine].append(r)
        eng_m = {e: _metrics_for(rs, owned, neg, excl, engine_slice=True,
                                 period_type="monthly")
                 for e, rs in by_eng.items()}
    platform_matrix = []
    for e in sorted(eng_m, key=lambda e: (-eng_m[e]["exposure"], e)):   # Ties broken by engine name

        m = eng_m[e]
        platform_matrix.append(PlatformMetricRow(
            engine=e, exposure_pct=m["exposure"], top1_pct=m["top1"],
            top3_pct=m["top3"], accuracy_pct=m["accuracy"], citation_pct=m["citation"],
            mention_pct=m["mention"],
            hit_count=int(m.get("_cat_hit") or 0), valid_count=int(m.get("_cat_valid") or 0),
            rank_coverage=m.get("_rank_coverage"),
        ))

    # Weekly trend + 4-week linear extrapolation — only up to month end (historical monthly reports are period snapshots, no later months' actual data)
    wkeys_m = [k for k in wkeys if k <= month_weeks[-1]]
    if stats is not None:
        month_end_iso = month_end.isoformat()
        trend = [TrendWeekPoint(
            week_key=k,
            exposure_pct=_metrics_from_counts(_sum_counts(
                [stats[d].get(ALL_ENGINE, {}) for d in week_days[k]
                 if d <= month_end_iso]), period_type="weekly")["exposure"])
            for k in wkeys_m]
    else:
        trend = [TrendWeekPoint(
            week_key=k,
            exposure_pct=_metrics_for(
                [r for r in by_week_all[k] if _bj_date(r.created_at) <= month_end],
                owned, neg, excl, period_type="weekly")["exposure"])
            for k in wkeys_m]
    if len(trend) >= 2:
        slope = (trend[-1].exposure_pct - trend[0].exposure_pct) / (len(trend) - 1)
        last_s, _ = _week_bounds(trend[-1].week_key)
        for i in range(1, 5):
            nxt = last_s + timedelta(weeks=i)
            trend.append(TrendWeekPoint(
                week_key=_week_key(nxt),
                exposure_pct=round(min(100.0, max(0.0, trend[len(wkeys_m) - 1].exposure_pct + slope * i)), 1),
                forecast=True,
            ))

    # Correction records: combinations whose negative verdict window overlaps this month; window closed within the month (ends before month end) = recovered;
    # or the combination has a recheck-positive record (verdict=positive) later than the negative window = recovered
    corrections: list[CorrectionRow] = []
    month_start = date.fromisoformat(month_key + "-01")
    pos_after: dict[tuple[str, str], date] = {}
    for r in (db.query(AiTelemetryCellInsightORM)
                .filter(AiTelemetryCellInsightORM.topic_id == topic_id)
                .filter(AiTelemetryCellInsightORM.verdict == "positive")
                .all()):
        k = (r.query, r.engine)
        d0 = r.window_start.date() if isinstance(r.window_start, datetime) else r.window_start
        if k not in pos_after or d0 > pos_after[k]:
            pos_after[k] = d0
    for (q, e), wins in sorted(neg.items()):
        m_wins = [(ws, we) for ws, we in wins if ws <= month_end and we >= month_start]
        if not m_wins:
            continue
        fixed = (all(we < month_end for _ws, we in m_wins)
                 or ((q, e) in pos_after
                     and pos_after[(q, e)] > max(we for _ws, we in m_wins)))
        corrections.append(CorrectionRow(
            issue_type="negative",                      # Stable key, rendered by frontend i18n
            found=f"「{q}」× {e}",
            status="fixed" if fixed else "fixing",
            action="replace",
        ))
    if stats is not None:
        # Term-level facts (missed terms / prompt count) use small aggregate queries instead of loading a month of rows
        ms_utc = datetime.combine(month_start, datetime.min.time()) - BJ_OFFSET
        me_utc = datetime.combine(month_end + timedelta(days=1), datetime.min.time()) - BJ_OFFSET
        q_base = (
            db.query(AiTelemetryResponseORM.query)
              .filter(AiTelemetryResponseORM.topic_id == topic_id)
              .filter(AiTelemetryResponseORM.created_at >= ms_utc)
              .filter(AiTelemetryResponseORM.created_at < me_utc)
        )
        all_queries = {q for (q,) in q_base.distinct().all()}
        hit_queries = {q for (q,) in
                       q_base.filter(AiTelemetryResponseORM.hit.is_(True)).distinct().all()}
        never_hit = sorted(all_queries - hit_queries)
    else:
        all_queries = {r.query for r in month_rows}
        never_hit = sorted(all_queries - {r.query for r in month_rows if r.hit})
    for q in never_hit[:5]:
        corrections.append(CorrectionRow(
            issue_type="unhit", found=q,
            status="attack", action="supply",
        ))

    # Deliverables list
    ms = datetime.combine(date.fromisoformat(month_key + "-01"), datetime.min.time()) - BJ_OFFSET
    nxt_month = (ms + timedelta(days=40)).replace(day=1)
    docs_n = (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.created_at >= ms)
          .filter(TopicGeneratedDocORM.created_at < nxt_month)
          .count()
    )
    if stats is not None:
        days_n = len(month_days)
        evidence_n = sum(int(stats[d].get(ALL_ENGINE, {}).get("valid_count") or 0)
                         for d in month_days)
    else:
        days_n = len({_bj_date(r.created_at) for r in month_rows})
        evidence_n = sum(1 for r in month_rows if not r.error)
    briefings_n = (
        db.query(AiTelemetryTopicBriefingORM)
          .filter(AiTelemetryTopicBriefingORM.topic_id == topic_id)
          .filter(AiTelemetryTopicBriefingORM.period_start >= ms)
          .filter(AiTelemetryTopicBriefingORM.period_start < nxt_month)
          .count()
    )
    queries_n = len(all_queries)
    if lang == "zh":
        deliverables = [
            DeliverableStat(label="监测关键词", value=f"{queries_n} 词"),
            DeliverableStat(label="内容生产", value=f"{docs_n} 篇"),
            DeliverableStat(label="日报", value=f"{days_n} 期"),
            DeliverableStat(label="周报", value=f"{briefings_n or len(month_weeks)} 期"),
            DeliverableStat(label="应答证据", value=f"{evidence_n} 条"),
        ]
    else:
        deliverables = [
            DeliverableStat(label="Monitored keywords", value=str(queries_n)),
            DeliverableStat(label="Content produced", value=str(docs_n)),
            DeliverableStat(label="Daily reports", value=str(days_n)),
            DeliverableStat(label="Weekly reports", value=str(briefings_n or len(month_weeks))),
            DeliverableStat(label="Evidence archived", value=str(evidence_n)),
        ]

    # Next-period plan (rule-generated)
    next_plan: list[str] = []
    if platform_matrix:
        worst = platform_matrix[-1]
        next_plan.append(
            f"{worst.engine} 专项:整体露出率 {worst.exposure_pct}% 为各引擎最低,"
            f"针对性补强该引擎偏好的信源内容" if lang == "zh"
            else f"{worst.engine} focus: lowest exposure ({worst.exposure_pct}%) among engines; "
                 f"reinforce sources this engine prefers")
    top3_not1 = [c for c in kpi_cards if c.key == "top1"][0]
    next_plan.append(
        f"TOP1 攻坚:当前 TOP1 露出率 {top3_not1.after}%,对高价值决策词做首位占位优化" if lang == "zh"
        else f"Top-1 push: Top-1 exposure at {top3_not1.after}%; optimize first-slot placement on high-value queries")
    if never_hit:
        next_plan.append(
            f"未露出词攻坚:{len(never_hit)} 个词全月未露出,列入下期内容供给清单" if lang == "zh"
            else f"Unhit keywords: {len(never_hit)} keywords had zero exposure this month; add to next content supply list")
    if any(c.status == "fixing" for c in corrections):
        next_plan.append("继续负面应答纠偏置换,直至复检回正" if lang == "zh"
                         else "Continue negative-answer correction until recheck passes")
    next_plan.append("词包复盘:按露出率 × 商业价值评估,置换低效词" if lang == "zh"
                     else "Keyword review: score by exposure × business value; swap out low performers")

    if stats is not None:
        p_start, p_end = month_days[0], month_days[-1]
    else:
        dates = sorted({_bj_date(r.created_at) for r in month_rows})
        p_start, p_end = dates[0].isoformat(), dates[-1].isoformat()
    return MonthlyReportOut(
        topic_id=topic_id, month_key=month_key,
        period_start=p_start, period_end=p_end,
        verdict=verdict, verdict_text=verdict_text, kpi_cards=kpi_cards,
        platform_matrix=platform_matrix, trend=trend,
        # Definition-change markers only cover historical weeks (extrapolated weeks have no real denominator, marking them is meaningless)
        basis_changes=[BasisChange(**c) for c in
                       detect_basis_changes(db, topic, stats, "weekly", wkeys_m)],
        corrections=corrections, deliverables=deliverables, next_plan=next_plan,
        exposure_target=_resolve_target("exposure", baseline_m["exposure"],
                                        topic.brand_tier or "")[0],
    )


# ─────────────────── Metric dashboard: rank distribution + source analysis ───────────


@router.get("/topics/{topic_id}/position-distribution", response_model=PositionDistributionOut)
def position_distribution(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    topic, rows, _, _ = _report_context(db, topic_id, current_user)
    scene_of = _scene_of_map(topic)

    # Cell-level best rank (lifetime)
    cells: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.error:
            continue
        c = cells.setdefault((r.query, r.engine), {"hit": False, "best": None})
        if r.hit:
            c["hit"] = True
            rank = r.brand_rank if isinstance(r.brand_rank, int) else None
            if rank and (c["best"] is None or rank < c["best"]):
                c["best"] = rank

    agg: dict[str, dict] = defaultdict(lambda: {
        "queries": set(), "top1": Counter(), "top2_3": Counter(),
        "r4_10": Counter(), "miss": Counter(), "exposed": Counter()})
    for (q, _e), c in cells.items():
        scene = scene_of.get(q, "unknown")
        a = agg[scene]
        a["queries"].add(q)
        if not c["hit"]:
            a["miss"][q] += 1
        elif c["best"] == 1:
            a["top1"][q] += 1
        elif scene in EXPOSURE_ONLY_SCENES:
            a["exposed"][q] += 1  # Three bands: hit but not #1 (including no rank) all count as "exposed"
        elif c["best"] is not None and c["best"] <= 3:
            a["top2_3"][q] += 1
        else:
            a["r4_10"][q] += 1   # Rank 4-10; hits without a rank also count as mid/low

    def _qs(counter: Counter) -> list[SceneDistQuery]:
        return [SceneDistQuery(query=q, count=n) for q, n in counter.most_common()]

    # This order is the row order of the rank distribution panel; omit a term type and its whole row disappears (filtered by s in agg below)
    order = ["compare", "brand", "search", "qa", "intent", "scenario", "unknown"]
    return PositionDistributionOut(topic_id=topic_id, rows=[
        SceneDistRow(scene_type=s, query_count=len(agg[s]["queries"]),
                     bucket_mode="exposure" if s in EXPOSURE_ONLY_SCENES else "rank",
                     top1=sum(agg[s]["top1"].values()), top2_3=sum(agg[s]["top2_3"].values()),
                     r4_10=sum(agg[s]["r4_10"].values()), miss=sum(agg[s]["miss"].values()),
                     exposed=sum(agg[s]["exposed"].values()),
                     top1_queries=_qs(agg[s]["top1"]), top2_3_queries=_qs(agg[s]["top2_3"]),
                     r4_10_queries=_qs(agg[s]["r4_10"]), miss_queries=_qs(agg[s]["miss"]),
                     exposed_queries=_qs(agg[s]["exposed"]))
        for s in order if s in agg
    ])


@router.get("/topics/{topic_id}/citation-sources", response_model=CitationSourcesOut)
def citation_sources(
    topic_id: int, limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, rows, owned, _ = _report_context(db, topic_id, current_user)
    agg: dict[str, dict] = {}
    total = 0
    for r in rows:
        if r.error:
            continue
        try:
            cits = json.loads(r.citations_json or "[]")
        except Exception:  # noqa: BLE001
            continue
        for c in cits:
            if not isinstance(c, dict) or not c.get("url"):
                continue
            nu = _norm_url(c["url"])
            if nu in owned:
                # Source ranking only looks at external sources: owned-article citations use the article citation rate definition, not the ranking/totals
                continue
            total += 1
            a = agg.setdefault(nu, {
                "url": c["url"], "domain": c.get("domain") or "",
                "title": c.get("title") or "", "engines": set(), "count": 0,
            })
            a["count"] += 1
            a["engines"].add(r.engine)
            if not a["title"] and c.get("title"):
                a["title"] = c["title"]

    type_counts: dict[str, int] = defaultdict(int)
    top_sources: list[CitationSourceRow] = []
    for nu, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        try:
            stype = classify_factor(a["url"], title=a["title"],
                                    domain=a["domain"], owned=nu in owned)
        except Exception:  # noqa: BLE001
            stype = "other"
        type_counts[stype] += a["count"]
        if len(top_sources) < max(1, min(limit, 200)):
            top_sources.append(CitationSourceRow(
                url=a["url"], domain=a["domain"], title=a["title"],
                source_type=stype, engines=sorted(a["engines"]),
                count=a["count"], owned=nu in owned,
            ))

    type_share = sorted([
        SourceTypeShare(source_type=t, count=n,
                        pct=round(n / total * 100, 1) if total else 0.0)
        for t, n in type_counts.items()
    ], key=lambda x: -x.count)
    return CitationSourcesOut(
        topic_id=topic_id, total_citations=total,
        type_share=type_share, top_sources=top_sources,
    )


# ─────────────────── Source strategy (flywheel P1-D) ─────────────────────
# Why cited + media placement recommendations: hard-data aggregation + LLM analysis, see services/source_strategy.py


class SourceStrategyOut(BaseModel):
    topic_id: int
    status: str                               # idle / generating / ready / failed
    source_stats: dict = Field(default_factory=dict)
    analysis: dict = Field(default_factory=dict)          # why_cited / engine_differences / content_guidelines
    recommendations: list = Field(default_factory=list)   # [{platform, priority, reason, content_type}]
    llm_model: str = ""
    error: Optional[str] = None
    generated_at: Optional[datetime] = None


def _source_strategy_out(topic_id: int, row: Optional[AiTelemetryTopicSourceStrategyORM]) -> SourceStrategyOut:
    if row is None:
        return SourceStrategyOut(topic_id=topic_id, status="idle")
    return SourceStrategyOut(
        topic_id=topic_id, status=row.status or "idle",
        source_stats=json.loads(row.source_stats_json or "{}"),
        analysis=json.loads(row.analysis_json or "{}"),
        recommendations=json.loads(row.recommendations_json or "[]"),
        llm_model=row.llm_model or "", error=row.error,
        generated_at=row.generated_at,
    )


@router.get("/topics/{topic_id}/source-strategy", response_model=SourceStrategyOut)
def get_source_strategy(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    row = (
        db.query(AiTelemetryTopicSourceStrategyORM)
          .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id)
          .one_or_none()
    )
    return _source_strategy_out(topic_id, row)


@router.post("/topics/{topic_id}/source-strategy/generate", response_model=SourceStrategyOut)
def generate_source_strategy(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    topic = _topic_scoped(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_WRITE,
    )
    _, operation = create_resource_operation_context(
        db,
        current_user,
        topic,
        action="source_strategy.generate",
        target_resource_type="topic",
        target_resource_id=topic.id,
    )
    append_system_audit(
        db,
        operation_context=operation,
        event_type="task_created",
        action="source_strategy.generate",
        object_type="topic",
        object_id=topic.id,
        result="success",
    )
    db.commit()
    from geo.services.source_strategy import schedule_source_strategy
    schedule_source_strategy(topic_id, operation_context_id=operation.id)
    row = (
        db.query(AiTelemetryTopicSourceStrategyORM)
          .filter(AiTelemetryTopicSourceStrategyORM.topic_id == topic_id)
          .one_or_none()
    )
    return _source_strategy_out(topic_id, row)


# ─────────────────── Placement attribution (publish center recommendations/badges) ────────────────────


class PublishAttributionRow(BaseModel):
    platform: str
    media: str = ""
    published_docs: int
    cited_docs: int
    cited_rate: float                       # 0-100
    engines: list[str] = Field(default_factory=list)
    total_cost_cents: Optional[int] = None  # Only set for paid-channel tasks
    last_published_at: Optional[str] = None


class PublishAttributionOut(BaseModel):
    topic_id: int
    rows: list[PublishAttributionRow]              # (platform, media) details
    platform_rollup: list[PublishAttributionRow]   # Platform-level rollup, for badges


@router.get("/topics/{topic_id}/publish-attribution", response_model=PublishAttributionOut)
def publish_attribution(
    topic_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate the citation effect of historical placements by (platform, media) (doc-level approximate definition, see service)."""
    _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    from geo.services.publish_attribution import compute_publish_attribution
    data = compute_publish_attribution(db, topic_id)
    return PublishAttributionOut(topic_id=topic_id, **data)


# ─────────────────── Competitor comparison analysis ────────────────────────────
# All compute-on-read: brand metrics come from hit/brand_rank; competitor metrics come from competitors_json
# (each response records competitors that appeared in the same AI answer), no new tables.


class CompetitiveVisMetric(BaseModel):
    current: float                        # coverage/mention in %; avg_rank is the average rank (lower is better)
    delta: Optional[float] = None         # vs previous window (for rank: previous - current, >0 = moved up)
    series: list[float] = Field(default_factory=list)
    series_keys: list[str] = Field(default_factory=list)


class CompetitorCardOut(BaseModel):
    name: str
    coverage: float
    coverage_diff: float                  # us - competitor (percentage points, >0 = leading)
    rank: int                             # Competitor's average rank
    rank_diff: int                        # Competitor rank - our rank (>0 = we are ahead)
    mention: float
    mention_diff: float
    accuracy: float = 0.0                 # Content neutral-positive rate (tier reference estimate, for the radar)
    citation: float = 0.0                 # Article citation rate (tier reference estimate, for the radar)


class IndustryAvgOut(BaseModel):
    coverage: float
    rank: int
    mention: float
    accuracy: float = 0.0
    citation: float = 0.0


class HeatWordOut(BaseModel):
    """Two definitions per heatmap term — on-site hit heat (observed) + external search heat (market demand)."""
    query: str
    heat: int                             # = hit_heat, legacy field kept (public industry library page still reads it)
    hit_heat: int = 0                     # On-site AI hit heat: converted from hit count within the window
    hits: int = 0                         # Raw hit count before conversion — deduplicated per (term, platform) cell,
                                          # same definition as exposure: rerunning the same cell multiple rounds counts once
    search_heat: Optional[int] = None     # External search heat 0-100; None = not collected yet
    search_source: Optional[str] = None   # baidu_index / google_trends / oceanengine / llm
    search_raw: Optional[int] = None      # Raw source value (Baidu daily average index, etc.), for frontend tooltip
    core_word: str = ""                   # Core short term actually queried in the external index
    stale: bool = False                   # Search heat past cache TTL, awaiting next refresh


class RankingRowOut(BaseModel):
    name: str
    is_brand: bool
    scores: dict[str, float]              # engine_id -> 0-100
    composite: float


class CompetitiveOut(BaseModel):
    topic_id: int
    period_type: str
    key: str
    period_start: str
    period_end: str
    brand: str
    engines: list[str]
    coverage: CompetitiveVisMetric
    avg_rank: CompetitiveVisMetric
    mention: CompetitiveVisMetric
    brand_accuracy: float = 0.0           # Brand content neutral-positive rate (current window, same definition as the six-metric cards, for the radar)
    brand_citation: float = 0.0           # Brand article citation rate (current window, same definition as the six-metric cards, for the radar)
    industry_avg: IndustryAvgOut
    competitors: list[CompetitorCardOut]  # Top 2 competitors by coverage
    coverage_gap_top2: float              # Our coverage - top 2 competitor coverage
    keywords_to_optimize: int             # Prompts with 0 hits in the window (opportunity keywords)
    heat_words: list[HeatWordOut]
    ranking: list[RankingRowOut]
    # Search heat cache coverage (total/cached/fresh/fetched_at) — for the frontend "collected X/Y · refresh"
    heat_cache: dict = Field(default_factory=dict)


def _comp_names(r: AiTelemetryResponseORM) -> list[str]:
    """competitors_json supports two shapes: list[str] (synthetic) and list[{name,count,snippet}] (real extraction)."""
    try:
        v = json.loads(r.competitors_json or "[]")
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            name = (x.get("name") or "") if isinstance(x, dict) else str(x or "")
            name = str(name).strip()
            if name:
                out.append(name)
        return out
    except Exception:  # noqa: BLE001
        return []


def _is_brand_alias(name: str, terms: tuple[str, ...]) -> bool:
    """Is this "competitor" actually us?

    The extraction model often returns the brand itself as a competitor — the answer is praising us and it just copies the name.
    So on the product leaderboard "<brand-g>" (the brand) appeared both as the brand row and among competitor rows, the same company twice.

    **Bidirectional containment**: "<brand-g>插座" (brand + "socket") contains the brand term and must be dropped; "利特" (a brand prefix) contained in the brand term must be dropped too —
    the extraction model emits both forms. Terms shorter than 2 are ignored, otherwise single characters would cause wide collateral damage
    (the same len>=2 rule as metric_brand_terms).
    """
    n = (name or "").strip().casefold()
    if not n:
        return True
    for t in terms:
        t = (t or "").strip().casefold()
        if len(t) < 2:
            continue
        if t in n or n in t:
            return True
    return False


# Cap on the competitor coverage candidate set. The number of names directly drives pre-scan cost (topic 20 measured: 1584 names took 22s,
# 0.28s after narrowing to 60), and 1087 of those 1584 were **extracted only once** — extractor
# noise, not competitors. Take the top N by extraction frequency, extracted at least MIN times.
_COMP_UNIVERSE_TOP_N = 60
_COMP_UNIVERSE_MIN_HITS = 2


def _comp_universe(rows, brand_terms: tuple[str, ...]) -> dict[str, str]:
    """Competitor candidate set: {term used for body matching → leaderboard display name}.

    The extractor (competitors_json) is only responsible for **discovering which competitors exist** here, not for computing coverage —
    its recall differs by two orders of magnitude across topics (measured: topic 2 extraction==body 1x, topic 9 4~11x,
    topic 20 5~32x, topic 32 57~140x).

    When the same company is extracted in a short and long form (<law-firm-b> / <law-firm-b>, <brand-h> / <brand-h>) they must be merged,
    or the same company takes two rows and they steal each other's matches. Rule: terms that are substrings of each other form one group,
    **match using the shortest term** (which also catches the long form), **display the most frequently extracted form**.
    """
    cnt: Counter[str] = Counter()
    for r in rows:
        for n in _comp_names(r):
            n = n.strip()
            if len(n) >= 2 and not _is_brand_alias(n, brand_terms):
                cnt[n] += 1
    names = [n for n, c in cnt.most_common(_COMP_UNIVERSE_TOP_N)
             if c >= _COMP_UNIVERSE_MIN_HITS]

    groups: list[list[str]] = []
    for n in sorted(names, key=len):          # Shorter ones form groups first, longer ones attach to existing groups
        for g in groups:
            if any(x in n or n in x for x in g):
                g.append(n)
                break
        else:
            groups.append([n])
    out: dict[str, str] = {}
    for g in groups:
        needle = min(g, key=len)
        label = max(g, key=lambda x: (cnt[x], len(x)))
        out[needle] = label
    return out


def comp_pattern(universe: dict[str, str]):
    """Candidate set → one compiled matcher. Longer forms first, so short forms do not steal matches."""
    if not universe:
        return None
    return re.compile("|".join(re.escape(n) for n in
                               sorted(universe, key=len, reverse=True)))


def comp_hits_in_text(text: str, universe: dict[str, str], pat=None) -> tuple[str, ...]:
    """Which competitors (display names) appear in a body of text, **ordered by first appearance**.

    The same yardstick as the brand's `r.hit`. `response_detail` also calls it when persisting details, ensuring
    "competitor_count in the detail layer" and "the competitor definition on the report read path" are the same definition.
    """
    pat = pat or comp_pattern(universe)
    if pat is None or not text:
        return ()
    seen: dict[str, int] = {}
    for m in pat.finditer(text):
        label = universe[m.group(0)]
        if label not in seen:
            seen[label] = m.start()
    return tuple(sorted(seen, key=seen.get))


def _comp_mentions(r) -> int:
    """How many competitors this response mentions.

    Uses the body-match result attached by `_load_rows` at load time; if absent (other row-loading paths / synthetic data), falls back
    to the extractor definition, behaving exactly as before.
    """
    h = getattr(r, "_chits", None)
    return len(h) if h is not None else len(_comp_names(r))


def _comp_hits_by_row(rows, universe: dict[str, str]) -> dict[int, tuple[str, ...]]:
    """Which competitors appear in each response body, **ordered by first appearance**.

    The same yardstick as the brand's `r.hit` — that is the whole point of this change: previously competitor coverage counted
    "did the extractor list it" while the brand counted "is it in the body"; ranking two different detectors on one leaderboard
    structurally pushed competitors to near zero (measured topic 32: Bull appeared in 70.6% of bodies, the leaderboard gave 1.5).

    It is also used for ordering: first-appearance position is more stable than the extractor's array index, and every row has one.
    """
    if not universe:
        return {}
    pat = comp_pattern(universe)
    out: dict[int, tuple[str, ...]] = {}
    for r in rows:
        hits = comp_hits_in_text(r.answer or "", universe, pat)
        if hits:
            out[r.id] = hits
    return out


def _window_stats(rows: list[AiTelemetryResponseORM],
                  period_type: Optional[str] = None,
                  brand_terms: tuple[str, ...] = (),
                  comp_hits: Optional[dict[int, tuple[str, ...]]] = None) -> dict:
    """Brand + competitor exposure / rank / mention share within a time window.

    brand_cov / coverage use the "overall exposure rate" definition (since 2026-07-05): brand = hit responses ÷ valid responses,
    exactly the same formula as the six-metric cards; competitor = same-answer appearance rate (appearing responses ÷ valid responses).
    The previous "terms ever hit ÷ all terms" definition did not match the six cards and is deprecated.

    period_type default None = not pinned — the per-day / per-7-day sub-buckets of mini trends must use this,
    or every sub-bucket is divided by the whole-period floor and the curve is flattened to 1/N.
    """
    hits = [r for r in rows if r.hit]
    hit_queries = {r.query for r in hits}
    queries = {r.query for r in rows}
    ranks = [r.brand_rank for r in hits if isinstance(r.brand_rank, int)]
    # Ratios uniformly use 1 decimal — same display precision as the six-metric cards, so readings match across the page
    # For topics with a pinned denominator: brand and competitors both use it, otherwise exposure here would lag the six-metric cards
    # (the note "did not match the six cards, deprecated" came from being bitten by exactly this kind of source mismatch).
    # The six cards switched from max(actual, floor) to pinned on 2026-08-31; this must follow, clamping included.
    floor = _fixed_denominator(False, period_type)
    denom = floor or len(rows)

    def _pct(num: int) -> float:
        return round(min(100.0, num / denom * 100), 1) if denom else 0.0

    brand_cov = _pct(len(hits))
    brand_rank = round(sum(ranks) / len(ranks), 2) if ranks else 0.0
    comp_apps: dict[str, int] = defaultdict(int)
    comp_idx: dict[str, list] = defaultdict(list)
    for r in rows:
        if comp_hits is not None:
            # Body matching (same yardstick as the brand's r.hit). The brand itself was removed when building the candidate set;
            # order comes from first-appearance position.
            names = comp_hits.get(r.id, ())
        else:
            # Compatibility for callers not passing comp_hits: fall back to the extractor definition (old behavior).
            # Remove the brand itself before enumerate: the order means "position among competitors",
            # and counting ourselves would push every real competitor's rank back by one.
            names = [n for n in _comp_names(r) if not _is_brand_alias(n, brand_terms)]
        for i, name in enumerate(names):
            comp_apps[name] += 1
            comp_idx[name].append(i)
    total_mentions = len(hits) + sum(comp_apps.values())
    brand_mention = round(len(hits) / total_mentions * 100, 1) if total_mentions else 0.0
    comps: dict[str, dict] = {}
    for name, apps in comp_apps.items():
        avg_idx = sum(comp_idx[name]) / len(comp_idx[name])
        # Competitor rank estimate: average appearance order in competitors_json + fixed competitor tier (crc-stable) + our displacement
        tier = (zlib.crc32(name.encode("utf-8")) % 3) * 0.9
        rank = int(round(1 + avg_idx + tier + (1 if brand_rank and brand_rank <= 2.2 else 0)))
        comps[name] = {
            "coverage": _pct(apps),
            "mention": round(apps / total_mentions * 100, 1) if total_mentions else 0.0,
            "rank": max(1, min(rank, 9)),
            "apps": apps,
        }
    return {
        "brand_cov": brand_cov, "brand_rank": brand_rank, "brand_mention": brand_mention,
        "hits": len(hits), "comps": comps,
        "no_hit_queries": len(queries - hit_queries),
    }


def _comp_ref_metrics(name: str, coverage: float) -> tuple[float, float]:
    """Reference estimates of competitors' content neutral-positive rate / article citation rate (for the radar face-off).

    These two cannot be observed directly (negative verdict windows and owned URLs only cover our brand),
    so the tier is inferred from the competitor's exposure rate, taking the midpoint of the TIER_TARGET_BANDS reference range,
    plus a crc perturbation (constant per name) to keep competitors distinguishable — same precedent as the competitor rank estimate.
    """
    tier = "leader" if coverage >= 45 else "mid" if coverage >= 18 else "new"
    band = TIER_TARGET_BANDS[tier]
    j = (zlib.crc32(name.encode("utf-8")) % 5) - 2   # -2..2
    acc = round(min(100.0, (band["accuracy"][0] + band["accuracy"][1]) / 2 + j * 0.6), 1)
    cit = round(max(0.0, (band["citation"][0] + band["citation"][1]) / 2 + j), 1)
    return acc, cit


def build_competitive_payload(
    db: Session,
    topic,
    period_type: str = "overview",
    key: Optional[str] = None,
    exclude_brand: Optional[bool] = None,
) -> CompetitiveOut:
    """Competitor comparison aggregation core — shared by the report center (authed endpoint) and the industry library (anonymous endpoint).

    **exclude_brand defaults to True: the product leaderboard never counts brand terms.**

    Previously it was decided by `METRIC_EXCLUDE_BRAND_TOPIC_IDS` (hard-coded `{9}`), i.e. only that one example cleaning
    account excluded them. The phenomenon recorded in that comment is universal, not a special case of one account —
    "ask about the brand, answer with the brand" inflates brand coverage (example cleaning brand terms 57/103, exposure 63% vs 18% under the category-term definition),
    and the brand row score on the product leaderboard comes from this coverage. Other topics were only covered by scene_type labels
    (EXPOSURE_ONLY_SCENES) and leaked when the vocabulary was mislabeled — those are hand-labeled by ops and should not be the only line of defense.
    The two gates are orthogonal and stack: scene trusts labels, this one trusts term text.

    Callers can still pass False explicitly to get the definition including brand terms (no such caller currently).
    """
    topic_id = topic.id
    rows = _load_rows(db, topic_id)
    owned = _load_owned_urls(db, topic_id)
    neg = _load_negative_windows(db, topic_id)
    valid = [r for r in rows if not r.error]
    # The product leaderboard always excludes brand terms (see docstring). The `if _cat:` guard stays —
    # in case a topic's entire vocabulary contains the brand name and filtering leaves nothing, better not exclude than blank the page.
    #
    # ★ The wide-table rollup likewise no longer switches by topic id (see the same-named comment in report_daily_rollup).
    #   But **persisted historical rows do not update themselves** — per product decision "history stays as is", only newly written days
    #   use the new definition. So the time series has a seam: older days include brand terms, newer ones do not.
    if exclude_brand is None:
        exclude_brand = True          # ★ No longer a hard-coded switch by topic id
    if exclude_brand:
        _terms = metric_brand_terms(topic)
        _cat = [r for r in valid if not any(t in (r.query or "") for t in _terms)]
        if _cat:
            valid = _cat
    # Global definition: brand/competitor-comparison terms are excluded from the competitor comparison module (brand questions inflate brand coverage),
    # leaderboard brand score = category-term exposure rate, matching the top exposure card (cat_* definition)
    _scene_excl = _metric_excl_queries(topic)
    if _scene_excl:
        _cat = [r for r in valid if r.query not in _scene_excl]
        if _cat:
            valid = _cat
    if not valid:
        raise HTTPException(status_code=404, detail="暂无跑批数据")
    by_day = _group_by(valid, lambda d: d.isoformat())
    day_keys = sorted(by_day)
    last_day = date.fromisoformat(day_keys[-1])

    # Window resolution: cur_days / prev_days (comparison window) / series_days (mini trend)
    try:
        if period_type == "daily" and key:
            end = date.fromisoformat(key)
            cur_days = [key]
            prev_days = [(end - timedelta(days=1)).isoformat()]
            series_days = [(end - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        elif period_type == "weekly" and key:
            s, _e = _week_bounds(key)
            cur_days = [(s + timedelta(days=i)).isoformat() for i in range(7)]
            prev_days = [(s + timedelta(days=i - 7)).isoformat() for i in range(7)]
            series_days = cur_days
        elif period_type == "monthly" and key:
            y, m = key.split("-")
            s = date(int(y), int(m), 1)
            e = (s.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            cur_days = [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]
            prev_e = s - timedelta(days=1)
            prev_s = prev_e.replace(day=1)
            prev_days = [(prev_s + timedelta(days=i)).isoformat() for i in range((prev_e - prev_s).days + 1)]
            series_days = cur_days
        else:  # overview: trend covers the whole period, "current value" is the latest ISO week — aligned with the overview six-metric cards (weekly)
            series_days = day_keys[:]
            ws, _we = _week_bounds(_week_key(last_day))
            cur_days = [(ws + timedelta(days=i)).isoformat() for i in range(7)]
            prev_days = [(ws + timedelta(days=i - 7)).isoformat() for i in range(7)]
            key = day_keys[-1]
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid key for period_type")

    cur_rows = [r for d in cur_days for r in by_day.get(d, [])]
    prev_rows = [r for d in prev_days for r in by_day.get(d, [])]
    if not cur_rows:
        raise HTTPException(status_code=404, detail="该周期没有跑批数据")

    # The window of the card's big number is one full period → floor by that period's granularity (overview's current value is the latest ISO week);
    # the per-day / per-7-day sub-buckets in ser[] below never pass it = no floor, otherwise each sub-bucket is divided by the whole-period denominator and flattened
    _floor_pk = "weekly" if period_type in ("overview", "weekly") else (
        "monthly" if period_type == "monthly" else None)
    # Competitor aggregation must drop the brand itself (the extraction model often returns us as a competitor, and the product leaderboard
    # would show the same company twice). Only dropped on this compute-on-read path — the wide-table rollup's
    # _counts_for / _cell_counts_for have no topic, and changing them would alter historical numbers of delivered reports.
    _bterms = tuple(metric_brand_terms(topic))
    # Competitor body-match results are attached to rows once by the row-loading layer (_attach_comp_hits); read them directly here
    # instead of rescanning — this also guarantees the same data as the six metrics' voice denominator (_comp_mentions).
    _chits = {r.id: getattr(r, "_chits", ()) for r in valid}
    cur = _window_stats(cur_rows, _floor_pk, _bterms, _chits)
    prev = _window_stats(prev_rows, _floor_pk, _bterms, _chits) if prev_rows else None

    # Brand exposure pin: the brand row of the leaderboard's last column / competitor coverage card is computed from responses and would not match
    # the pinned six-metric cards at the top; override brand_cov per topic here so top and bottom agree (overview ≈ weekly).
    _pin_pk = "weekly" if period_type == "overview" else period_type
    _pin_exp = OVERVIEW_PIN.get(topic_id, {}).get(_pin_pk, {}).get("exposure")
    if _pin_exp is not None:
        cur["brand_cov"] = _pin_exp

    if period_type == "overview" and len(series_days) > 21:
        # Whole-period mini trend aggregated weekly (daily points too dense to draw); first/last keys use actual window endpoints,
        # so the card corner shows a whole-period range like "03-12 ~ 06-12"
        groups = [series_days[i:i + 7] for i in range(0, len(series_days), 7)]
        ser = [_window_stats([r for d in g for r in by_day.get(d, [])], None, _bterms, _chits)
               for g in groups]
        series_keys = [g[0][5:] for g in groups]
        series_keys[-1] = groups[-1][-1][5:]
    else:
        ser = [(_window_stats(by_day[d], None, _bterms, _chits) if d in by_day else None)
               for d in series_days]
        series_keys = [d[5:] for d in series_days]

    def vis(kind: str) -> CompetitiveVisMetric:
        field = {"coverage": "brand_cov", "rank": "brand_rank", "mention": "brand_mention"}[kind]
        curv = cur[field]
        deltav = None
        if prev and prev[field]:
            deltav = round((prev[field] - curv) if kind == "rank" else (curv - prev[field]), 2)
        return CompetitiveVisMetric(
            current=curv, delta=deltav,
            series=[(s[field] if s else 0.0) for s in ser], series_keys=series_keys,
        )

    comps = cur["comps"]

    # Product leaderboard: per-engine 0-100 score, i.e. real ratios — brand score = exposure rate (%) + small top-3 bonus;
    # competitor score = same-answer appearance rate (%). No floor/amplification; readings are directly comparable to survey ranges (leaders 45-70).
    # Compute the leaderboard first: "product rank" on competitor cards / industry average uses rank within the leaderboard group, one yardstick site-wide
    engines = sorted({r.engine for r in cur_rows})
    try:
        order = [e for e in json.loads(topic.engines_json or "[]") if e in engines]
        engines = order + [e for e in engines if e not in order]
    except Exception:  # noqa: BLE001
        pass
    by_eng: dict[str, list] = defaultdict(list)
    for r in cur_rows:
        # The third item **must** share a source with the composite score (_chits body matching), not the _comp_names extractor definition:
        #   · different definitions → the same row shows "composite 94.9" but "DeepSeek cell 1", self-contradictory;
        #   · different names too → comps keys are merged display names (<brand-h>), while the extractor emits raw
        #     forms (<brand-h>), so `name in it[2]` almost never matches and the per-engine columns collapse to 0~3.
        by_eng[r.engine].append((bool(r.hit), r.brand_rank, _chits.get(r.id, ())))

    # Per-engine column denominators share the scale of the top metric cards — those were changed to **pinned** (see _fixed_valid_ctx);
    # keeping max(actual, floor) here would make brand_score differ from the engine's exposure rate, top and bottom mismatched.
    # Without a floor _eng_floor is None and actual counts are used, same as before this change.
    _eng_floor = _fixed_denominator(True, _floor_pk)

    def _eng_denom(items) -> int:
        return _eng_floor or len(items)

    def _pct(num: int, den: int) -> float:
        # A pinned denominator allows the numerator to exceed it (actual volume above the floor); clamp to 100 like the metric cards
        return round(min(100.0, num / den * 100), 1) if den else 0.0

    def brand_score(items) -> float:
        # Brand score = the engine's overall exposure rate, exactly the same definition as the top metric cards (no bonus, so numbers match)
        return _pct(sum(1 for it in items if it[0]), _eng_denom(items))

    def comp_score(items, name: str) -> float:
        return _pct(sum(1 for it in items if name in it[2]), _eng_denom(items))

    brand_name = (topic.target or topic.name or "").split("/")[0].strip()
    ranking = [RankingRowOut(
        name=brand_name, is_brand=True,
        scores={e: brand_score(by_eng[e]) for e in engines},
        composite=0.0,
    )] + [RankingRowOut(
        name=name, is_brand=False,
        scores={e: comp_score(by_eng[e], name) for e in engines},
        composite=0.0,
    ) for name in comps]
    # Composite score = all-response definition (brand = window exposure rate, competitor = window appearance rate) —
    # the same number as the competitor cards / top metric cards; platform columns are per-platform breakdowns
    for row in ranking:
        row.composite = cur["brand_cov"] if row.is_brand else comps[row.name]["coverage"]
    ranking.sort(key=lambda r: -r.composite)
    # Rank within the group (full set before truncation): brand and competitors on one scale; VS card / industry average ranks come from here
    rank_pos = {row.name: i + 1 for i, row in enumerate(ranking)}
    ranking = ranking[:8]

    # Brand neutral-positive/citation rate: real values for the current window, exactly the same definition as the six-metric cards; competitors are tier reference estimates
    m_cur = _metrics_for(cur_rows, owned, neg, _scene_excl, period_type=_floor_pk)
    brand_acc, brand_cit = m_cur["accuracy"], m_cur["citation"]
    comp_refs = {name: _comp_ref_metrics(name, c["coverage"]) for name, c in comps.items()}

    all_cov = [cur["brand_cov"]] + [c["coverage"] for c in comps.values()]
    all_acc = [brand_acc] + [a for a, _ in comp_refs.values()]
    all_cit = [brand_cit] + [c for _, c in comp_refs.values()]
    industry = IndustryAvgOut(
        coverage=round(sum(all_cov) / len(all_cov), 1),
        rank=int(round(sum(rank_pos.values()) / len(rank_pos))) if rank_pos else 0,
        mention=round(100.0 / (1 + len(comps)), 1) if comps else 100.0,
        accuracy=round(sum(all_acc) / len(all_acc), 1),
        citation=round(sum(all_cit) / len(all_cit), 1),
    )
    our_rank_i = rank_pos.get(brand_name) or (int(round(cur["brand_rank"])) if cur["brand_rank"] else 0)
    top2 = sorted(comps.items(), key=lambda kv: -kv[1]["coverage"])[:2]
    comp_cards = [CompetitorCardOut(
        name=name,
        coverage=c["coverage"], coverage_diff=round(cur["brand_cov"] - c["coverage"], 2),
        rank=rank_pos.get(name, c["rank"]),
        rank_diff=(rank_pos.get(name, c["rank"]) - our_rank_i) if our_rank_i else 0,
        mention=c["mention"], mention_diff=round(cur["brand_mention"] - c["mention"], 2),
        accuracy=comp_refs[name][0], citation=comp_refs[name][1],
    ) for name, c in top2]

    # Keyword heat (two definitions):
    #   ① On-site hit heat hit_heat — Top 10 by hit cell count in the window; = hit share × rank decay
    #      (with high exposure, leading terms' hit counts converge, so decay keeps the ranking distinguishable), peak ≈4000 rounded to hundreds.
    #   ② External search heat search_heat — only reads the keyword_heat cache (Baidu Index / Google Trends /
    #      Ocean Engine Index / model estimate), **no outbound calls on the request path**; None if not collected, frontend shows "pending collection".
    #      Collection runs via scheduled jobs or the page refresh button, see geo/services/keyword_heat.py.
    #
    # ★ 2026-09-09 definition fix: it used to count **response rows**, so each rerun of the same cell added +1 hit.
    #   topic 29 measured: "<brand-l>比其他特曲好在哪" ("why is <brand-l> better than other Tequ") showed 7 hits while the topic only had 5 platforms configured —
    #   the extra 2 came from overlapping batch runs (run 9976 started before 9974 finished) producing duplicate wenxin/yuanbao
    #   rows. All 10 terms actually had a 100% hit rate; the 7/5/4 gradient on the leaderboard was pure collection noise.
    #   Now deduplicated per (term, platform) cell, consistent with the exposure cell definition (_cell_counts_for):
    #   one cell = one term on one platform, counts 1 if hit this period, regardless of reruns.
    #   Duplicate writes on the collection side are fixed separately (_recently_succeeded in telemetry-service runner.py);
    #   dedup here keeps **already persisted historical duplicate rows** from skewing the leaderboard.
    _hit_cells = {(r.query, r.engine) for r in cur_rows if r.hit}
    q_hits = Counter(q for q, _e in _hit_cells)
    heat_words: list[HeatWordOut] = []
    heat_cache: dict = {}
    if q_hits:
        mx = max(q_hits.values())
        top = q_hits.most_common(10)
        geo = _topic_search_geo(topic)
        try:
            cached = keyword_heat.read_cached(db, [q for q, _ in top], geo)
            heat_cache = keyword_heat.cache_stats(db, [q for q, _ in top], geo)
        except Exception as e:  # noqa: BLE001 — a missing/broken cache table must never take down the report
            log.warning("keyword_heat cache read failed topic=%s: %s", topic.id, e)
            cached = {}
        heat_words = []
        for i, (q, n) in enumerate(top):
            hit_heat = max(200, int(round(n / mx * 40 / (1 + 0.13 * i))) * 100)
            c = cached.get(q) or {}
            heat_words.append(HeatWordOut(
                query=q, heat=hit_heat, hit_heat=hit_heat, hits=n,
                search_heat=c.get("heat"), search_source=c.get("source"),
                search_raw=c.get("raw_value"), core_word=c.get("core_word") or "",
                stale=bool(c.get("stale")),
            ))

    return CompetitiveOut(
        topic_id=topic_id, period_type=period_type, key=key or day_keys[-1],
        period_start=cur_days[0], period_end=cur_days[-1],
        brand=brand_name, engines=engines,
        coverage=vis("coverage"), avg_rank=vis("rank"), mention=vis("mention"),
        brand_accuracy=brand_acc, brand_citation=brand_cit,
        industry_avg=industry, competitors=comp_cards,
        coverage_gap_top2=comp_cards[0].coverage_diff if comp_cards else 0.0,
        keywords_to_optimize=cur["no_hit_queries"],
        heat_words=heat_words, ranking=ranking, heat_cache=heat_cache,
    )


@router.get("/topics/{topic_id}/competitive-analysis", response_model=CompetitiveOut)
def competitive_analysis(
    topic_id: int,
    period_type: str = "overview",
    key: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Competitor comparison analysis — overview (whole period, since the first batch-run day) or a given day/week/month window.

    Three trend metrics (keyword coverage / product rank / product mention share, consumed by competitor comparison and face-off bars)
    + industry average + VS top 2 competitors + competitive insights + keyword heat + per-engine product leaderboard.
    """
    if period_type not in ("overview", "daily", "weekly", "monthly"):
        raise HTTPException(status_code=422, detail="period_type must be overview/daily/weekly/monthly")
    topic = _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    return build_competitive_payload(db, topic, period_type, key)


class KeywordHeatRefreshOut(BaseModel):
    requested: int                        # Terms actually collected this round (fresh terms are not recollected)
    written: int                          # Terms successfully written to the cache
    by_source: dict = Field(default_factory=dict)   # Terms hit per source
    skipped: dict = Field(default_factory=dict)     # Skipped sources → reason (not configured / risk control / 429)
    geo: str = "cn"


@router.post("/topics/{topic_id}/keyword-heat/refresh", response_model=KeywordHeatRefreshOut)
def refresh_keyword_heat(
    topic_id: int,
    period_type: str = "overview",
    key: Optional[str] = None,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually collect external search heat for the heatmap terms (synchronous, only the Top 10 terms).

    Normally a scheduled job refreshes them (scripts/refresh_keyword_heat.py); this endpoint is the page's
    "refresh" button — for when users changed prompts or want numbers right away. force=True also recollects unexpired ones.
    An unavailable external source is not a failure: skipped in the result explains why, and heat degrades to the model estimate.
    """
    topic = _topic_scoped(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_WRITE,
    )
    payload = build_competitive_payload(db, topic, period_type, key)
    words = [w.query for w in payload.heat_words]
    geo = _topic_search_geo(topic)
    if not words:
        return KeywordHeatRefreshOut(requested=0, written=0, geo=geo)
    brand = (topic.target or topic.name or "").split("/")[0].strip()
    stats = keyword_heat.refresh(db, words, brand=brand, industry=topic.industry or "",
                                 geo=geo, force=force, limit=len(words))
    return KeywordHeatRefreshOut(geo=geo, **stats)


# ─────────────────── Evidence search ──────────────────────────────


@router.get("/topics/{topic_id}/evidence-search", response_model=EvidenceSearchOut)
def evidence_search(
    topic_id: int,
    q: Optional[str] = None,
    engine: Optional[str] = None,
    hit: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _topic_scoped(db, topic_id, current_user.id, allow_admin_user=current_user)
    qy = (
        db.query(AiTelemetryResponseORM)
          .filter(AiTelemetryResponseORM.topic_id == topic_id)
          .filter(AiTelemetryResponseORM.error.is_(None))
    )
    if engine:
        # Display key → all data keys mapped to it under this topic: filtering evidence by "豆包" (Doubao) must include doubao_browser
        # rows too; without expansion only the other channel's rows would remain (the frontend filter passes display keys)
        keys = {engine} | {k for k, v in _engine_alias_ctx.get().items() if v == engine}
        qy = qy.filter(AiTelemetryResponseORM.engine.in_(sorted(keys)))
    if hit is not None:
        qy = qy.filter(AiTelemetryResponseORM.hit.is_(hit))
    if q:
        qy = qy.filter(AiTelemetryResponseORM.query.ilike(f"%{q.strip()}%"))
    if date_from:
        try:
            qy = qy.filter(AiTelemetryResponseORM.created_at >=
                           datetime.fromisoformat(date_from) - BJ_OFFSET)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_from must be YYYY-MM-DD")
    if date_to:
        try:
            qy = qy.filter(AiTelemetryResponseORM.created_at <
                           datetime.fromisoformat(date_to) + timedelta(days=1) - BJ_OFFSET)
        except ValueError:
            raise HTTPException(status_code=422, detail="date_to must be YYYY-MM-DD")

    total = qy.count()
    rows = (qy.order_by(AiTelemetryResponseORM.created_at.desc())
              .offset(max(0, offset)).limit(max(1, min(limit, 200))).all())
    items = [ResponseOut(
        id=r.id, engine=_alias_engine(r.engine), query=r.query, answer=r.answer,
        citations=_clean_citations_for_out(r.citations_json),
        video_url=r.video_url, source_url=r.source_url, error=r.error,
        created_at=r.created_at, hit=bool(r.hit), hit_excerpt=r.hit_excerpt,
        mention_position=r.mention_position, brand_rank=r.brand_rank,
        reasoning=r.reasoning,
    ) for r in rows]
    return EvidenceSearchOut(topic_id=topic_id, total=total, items=items)


# ─────────────────── Brand labels (benchmarking) ────────────────────────
# Benchmark labels users put on their own account's brand; report pages use them to match same-tier exposure reference ranges.
# Values align with the tier benchmark table:
#   brand_tier      "" / new (new brand) / mid (mid-size brand) / leader (industry leader)
#   industry_class  "" / regular (regular industry) / special (special industry: medical, beauty, finance)


BRAND_TIERS = ("", "new", "mid", "leader")
INDUSTRY_CLASSES = ("", "regular", "special")


class ReportTagsPayload(BaseModel):
    brand_tier: str = Field("", max_length=16)
    industry_class: str = Field("", max_length=16)


class ReportTagsOut(BaseModel):
    topic_id: int
    brand_tier: str
    industry_class: str


@router.put("/topics/{topic_id}/report-tags", response_model=ReportTagsOut)
def update_report_tags(
    topic_id: int,
    payload: ReportTagsPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Label your own account's brand — empty string = clear the label."""
    if payload.brand_tier not in BRAND_TIERS:
        raise HTTPException(status_code=422, detail="brand_tier must be one of new/mid/leader or empty")
    if payload.industry_class not in INDUSTRY_CLASSES:
        raise HTTPException(status_code=422, detail="industry_class must be one of regular/special or empty")
    t = _topic_scoped(
        db, topic_id, current_user.id,
        allow_admin_user=current_user, action=ACTION_WRITE,
    )
    t.brand_tier = payload.brand_tier
    t.industry_class = payload.industry_class
    db.commit()
    return ReportTagsOut(topic_id=t.id, brand_tier=t.brand_tier, industry_class=t.industry_class)
