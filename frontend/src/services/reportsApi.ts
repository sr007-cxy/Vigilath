// Report center API — connects to backend geo/api/ai_telemetry_reports.py
// Independent of aiTelemetryApi.ts (old file untouched), keeping the same request pattern.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

async function request<T>(path: string, token: string): Promise<T> {
  const resp = await fetch(`${API_BASE}/ai-telemetry${path}`, {
    method: 'GET',
    headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
  });
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request GET ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

async function requestPost<T>(path: string, token: string): Promise<T> {
  const resp = await fetch(`${API_BASE}/ai-telemetry${path}`, {
    method: 'POST',
    headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
  });
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request POST ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

async function requestPut<T>(path: string, token: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}/ai-telemetry${path}`, {
    method: 'PUT',
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    }),
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request PUT ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export type PeriodType = 'daily' | 'weekly' | 'monthly';
export type MetricKey = 'exposure' | 'top1' | 'top3' | 'accuracy' | 'citation' | 'mention';
export type Progress = 'met' | 'near' | 'missed';

export interface KpiMetric {
  key: MetricKey;
  current: number;
  prev: number | null;
  delta_pp: number | null;
  sparkline: number[];
  target: number | null;
  target_high: number | null;
  baseline: number | null;
  progress: Progress | null;
}

export interface KpiSummary {
  topic_id: number;
  period_type: PeriodType;
  metrics: KpiMetric[];
}

export interface ReportListItem {
  key: string;
  period_start: string;
  period_end: string;
  exposure_pct: number;
  top1_pct: number;
  top3_pct: number;
  accuracy_pct: number;
  citation_pct: number;
  response_count: number;
  /** Numerator/denominator of the exposure rate (category query metric definition); 0 means this period shows an anchored display value with no raw counts */
  exposure_num: number;
  exposure_den: number;
  briefing_id: number | null;
}

/** Trend chart metric definition change marker -- the numerator/denominator base or definition changed in this period, so the two sides shouldn't be compared directly */
/* There was once a fifth kind 'floor' (response denominator floor switch), removed with the 2026-08-31 pinned denominator:
   the denominator always equals the configured value, so the "crossing the floor" switch point no longer exists. Backend KINDS synced. */
export type BasisChangeKind = 'queries' | 'scope' | 'engines' | 'denom';

export interface BasisChange {
  key: string;
  day: string;
  kind: BasisChangeKind;
  before: number | null;
  after: number | null;
  engines_added: string[];
  engines_removed: string[];
}

export interface ReportList {
  topic_id: number;
  period_type: PeriodType;
  items: ReportListItem[];
  basis_changes: BasisChange[];
}

export interface DailyKpiRow {
  key: MetricKey;
  label: string;
  today: number;
  yesterday: number | null;
  delta_pp: number | null;
  note: string;
}

export interface HighlightRow {
  query: string;
  engine: string;
  today_label: string;
  yesterday_label: string;
  change: 'up' | 'down' | 'flat' | 'new';
  hits: number;
  runs: number;
  evidence_response_id: number | null;
}

export interface ActionRow {
  time: string;
  title: string;
  status: string;
}

export interface DetailRow {
  response_id: number;
  query: string;
  engine: string;
  round_time: string;
  hit: boolean;
  rank_label: string;
  sentiment: 'positive' | 'neutral' | 'negative' | 'unknown';
  cited_own: boolean;
  source_url: string | null;
  created_at: string;
}

export interface DailyReport {
  topic_id: number;
  date: string;
  kpi: DailyKpiRow[];
  highlights: HighlightRow[];
  actions: ActionRow[];
  detail: DetailRow[];
  // Flywheel P1-A: the day's AI daily summary (generated daily by telemetry; null if not generated, shown progressively)
  ai_summary?: string | null;
}

export interface WeeklyMetricRow {
  key: MetricKey;
  label: string;
  baseline: number;
  weeks: number[];
  current: number;
  delta_pp: number | null;
  target: number | null;
  target_high: number | null;
  progress: Progress | null;
}

export interface EngineLift {
  engine: string;
  baseline: number;
  current: number;
  lift_pp: number;
}

export interface ActionStat {
  key: 'docs' | 'attack' | 'correction' | 'evidence';
  count: number;
  detail: string;
}

export interface WeeklyReport {
  topic_id: number;
  week_key: string;
  period_start: string;
  period_end: string;
  week_keys: string[];
  metrics: WeeklyMetricRow[];
  /** Overall rank collection coverage for this period; see PlatformMetricRow.rank_coverage for the metric definition */
  rank_coverage?: number | null;
  /** Same as the monthly report: before optimization (baseline week) -> after optimization (this week) */
  kpi_cards: MonthlyKpiCard[];
  platform_matrix: PlatformMetricRow[];
  deliverables: DeliverableStat[];
  /** This week's response details; over the cap only the first N are returned, see detail_total for the total */
  detail: DetailRow[];
  detail_total: number;
  engine_lifts: EngineLift[];
  action_stats: ActionStat[];
  risks: string[];
  body_md: string;
  summary_en: [string, string][];
  /** The same three sections as the monthly report: weekly trend + extrapolation / correction and breakthrough / next period plan */
  trend: TrendWeekPoint[];
  basis_changes: BasisChange[];
  corrections: CorrectionRow[];
  next_plan: string[];
  exposure_target: number | null;
}

export interface MonthlyKpiCard {
  key: MetricKey;
  label: string;
  before: number;
  after: number;
  relative_pct: number | null;
  met: boolean;
}

export interface PlatformMetricRow {
  engine: string;
  exposure_pct: number;
  top1_pct: number;
  top3_pct: number;
  accuracy_pct: number;
  citation_pct: number;
  mention_pct: number;
  /** Raw numerator and denominator of the exposure rate: hit_count ÷ valid_count = exposure_pct */
  hit_count: number;
  valid_count: number;
  /**
   * Rank collection coverage (share of hit cells that got a brand_rank).
   * 0 = not a single rank extracted this period -> TOP1/TOP3 must show "not collected" rather than 0%
   *     (0% means "AI didn't rank us near the top", which is different from "not collected");
   * null/undefined = can't tell (no hits, or an old path without this count) -> show the value as usual.
   */
  rank_coverage?: number | null;
}

export interface TrendWeekPoint {
  week_key: string;
  exposure_pct: number;
  forecast: boolean;
}

export interface CorrectionRow {
  issue_type: string;
  found: string;
  status: string;
  action: string;
}

export interface DeliverableStat {
  label: string;
  value: string;
  /** Increment vs. the previous period; empty string = no previous period to compare, not rendered by the frontend */
  delta: string;
}

export interface MonthlyReport {
  topic_id: number;
  month_key: string;
  period_start: string;
  period_end: string;
  verdict: 'met' | 'partial' | 'missed';
  verdict_text: string;
  kpi_cards: MonthlyKpiCard[];
  platform_matrix: PlatformMetricRow[];
  trend: TrendWeekPoint[];
  basis_changes: BasisChange[];
  corrections: CorrectionRow[];
  deliverables: DeliverableStat[];
  next_plan: string[];
  exposure_target: number | null;
}

export interface SceneDistQuery {
  query: string;
  count: number;
}

export interface SceneDistRow {
  scene_type: string;
  query_count: number;
  // "rank" has four tiers (top1/top2_3/r4_10/miss); "exposure" has three (top1/exposed/miss, for brand/competitor comparison queries)
  bucket_mode?: string;
  top1: number;
  top2_3: number;
  r4_10: number;
  miss: number;
  exposed?: number;
  top1_queries: SceneDistQuery[];
  top2_3_queries: SceneDistQuery[];
  r4_10_queries: SceneDistQuery[];
  miss_queries: SceneDistQuery[];
  exposed_queries?: SceneDistQuery[];
}

export interface PositionDistribution {
  topic_id: number;
  rows: SceneDistRow[];
}

export interface CitationSourceRow {
  url: string;
  domain: string;
  title: string;
  source_type: string;
  engines: string[];
  count: number;
  owned: boolean;
}

export interface SourceTypeShare {
  source_type: string;
  count: number;
  pct: number;
}

export interface CitationSources {
  topic_id: number;
  total_citations: number;
  type_share: SourceTypeShare[];
  top_sources: CitationSourceRow[];
}

// ── Placement attribution (publish center recommendations/badges) ──────────────────────────────
export interface PublishAttributionRow {
  platform: string;
  media: string;
  published_docs: number;
  cited_docs: number;
  cited_rate: number;                    // 0-100
  engines: string[];
  total_cost_cents?: number | null;      // Only paid channel tasks have a value
  last_published_at?: string | null;
}

export interface PublishAttribution {
  topic_id: number;
  rows: PublishAttributionRow[];
  platform_rollup: PublishAttributionRow[];
}

export interface EvidenceItem {
  id: number;
  engine: string;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  video_url: string | null;
  source_url: string | null;
  error: string | null;
  created_at: string;
  hit: boolean;
  hit_excerpt: string | null;
  mention_position: string | null;
  brand_rank: number | null;
  reasoning?: string | null;
}

export interface EvidenceSearch {
  topic_id: number;
  total: number;
  items: EvidenceItem[];
}

// ── Competitor comparison analysis ──
export interface CompetitiveVisMetric {
  current: number;
  delta: number | null;
  series: number[];
  series_keys: string[];
}

export interface CompetitorCard {
  name: string;
  coverage: number;
  coverage_diff: number;
  rank: number;
  rank_diff: number;
  mention: number;
  mention_diff: number;
  accuracy?: number;   // Content neutral-positive rate (tier reference estimate, for the radar)
  citation?: number;   // Article citation rate (tier reference estimate, for the radar)
}

export interface RankingRow {
  name: string;
  is_brand: boolean;
  scores: Record<string, number>;
  composite: number;
}

export interface CompetitiveAnalysis {
  topic_id: number;
  period_type: string;
  key: string;
  period_start: string;
  period_end: string;
  brand: string;
  engines: string[];
  coverage: CompetitiveVisMetric;
  avg_rank: CompetitiveVisMetric;
  mention: CompetitiveVisMetric;
  brand_accuracy?: number;   // Brand content neutral-positive rate (current window, same metric definition as the six metric cards)
  brand_citation?: number;   // Brand article citation rate (current window, same metric definition as the six metric cards)
  industry_avg: { coverage: number; rank: number; mention: number; accuracy?: number; citation?: number };
  competitors: CompetitorCard[];
  coverage_gap_top2: number;
  keywords_to_optimize: number;
  heat_words: HeatWord[];
  ranking: RankingRow[];
  heat_cache?: HeatCacheInfo;
}

// Collection sources for search heat -- label text is in heatSourceLabels of pages/Reports/lang.ts
export type HeatSource = 'baidu_index' | 'google_trends' | 'oceanengine' | 'llm';

// Dual metric definitions per heat map term: on-site AI hit heat (observed) + external search heat (market demand, read from cache)
export interface HeatWord {
  query: string;
  heat: number;                    // = hit_heat, old field (the industry library public page still reads it)
  hit_heat?: number;
  hits?: number;                   // Raw hit count before conversion
  search_heat?: number | null;     // 0-100; null/undefined = not collected yet
  search_source?: HeatSource | null;
  search_raw?: number | null;      // Raw value on the source side (Baidu daily average index etc.)
  core_word?: string;              // Core short term actually queried in the external index
  stale?: boolean;                 // Past the cache validity, awaiting re-collection next round
}

export interface HeatCacheInfo {
  total: number;
  cached: number;
  fresh: number;
  fetched_at?: string | null;
}

export interface KeywordHeatRefreshResult {
  requested: number;
  written: number;
  by_source: Record<string, number>;
  skipped: Record<string, string>;   // Source -> skip reason
  geo: string;
}

// ── Brand tags (benchmark) ──
export type BrandTier = '' | 'new' | 'mid' | 'leader';
export type IndustryClass = '' | 'regular' | 'special';

export interface ReportTags {
  topic_id: number;
  brand_tier: BrandTier;
  industry_class: IndustryClass;
}

export interface EvidenceFilters {
  q?: string;
  engine?: string;
  hit?: boolean;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export const reportsApi = {
  listReports(topicId: number, token: string, periodType: PeriodType, limit = 60) {
    return request<ReportList>(
      `/topics/${topicId}/reports?period_type=${periodType}&limit=${limit}`, token);
  },
  kpiSummary(topicId: number, token: string, periodType: PeriodType) {
    return request<KpiSummary>(
      `/topics/${topicId}/reports/kpi-summary?period_type=${periodType}`, token);
  },
  dailyReport(topicId: number, token: string, day: string) {
    return request<DailyReport>(`/topics/${topicId}/reports/daily/${day}`, token);
  },
  weeklyReport(topicId: number, token: string, weekKey: string) {
    return request<WeeklyReport>(`/topics/${topicId}/reports/weekly/${weekKey}`, token);
  },
  monthlyReport(topicId: number, token: string, monthKey: string) {
    return request<MonthlyReport>(`/topics/${topicId}/reports/monthly/${monthKey}`, token);
  },
  positionDistribution(topicId: number, token: string) {
    return request<PositionDistribution>(`/topics/${topicId}/position-distribution`, token);
  },
  citationSources(topicId: number, token: string, limit = 50) {
    return request<CitationSources>(
      `/topics/${topicId}/citation-sources?limit=${limit}`, token);
  },
  publishAttribution(topicId: number, token: string) {
    return request<PublishAttribution>(`/topics/${topicId}/publish-attribution`, token);
  },
  competitiveAnalysis(topicId: number, token: string, periodType = 'overview', key?: string) {
    const p = new URLSearchParams({ period_type: periodType });
    if (key) p.set('key', key);
    return request<CompetitiveAnalysis>(`/topics/${topicId}/competitive-analysis?${p}`, token);
  },
  // Manually collect search heat for this heat map's terms (synchronous, Top10 only; normally re-collected by a scheduled job)
  refreshKeywordHeat(topicId: number, token: string, force = false) {
    const p = new URLSearchParams({ period_type: 'overview' });
    if (force) p.set('force', 'true');
    return requestPost<KeywordHeatRefreshResult>(
      `/topics/${topicId}/keyword-heat/refresh?${p}`, token);
  },
  evidenceSearch(topicId: number, token: string, f: EvidenceFilters = {}) {
    const p = new URLSearchParams();
    if (f.q) p.set('q', f.q);
    if (f.engine) p.set('engine', f.engine);
    if (f.hit !== undefined) p.set('hit', String(f.hit));
    if (f.date_from) p.set('date_from', f.date_from);
    if (f.date_to) p.set('date_to', f.date_to);
    p.set('limit', String(f.limit ?? 50));
    p.set('offset', String(f.offset ?? 0));
    return request<EvidenceSearch>(`/topics/${topicId}/evidence-search?${p}`, token);
  },
  updateReportTags(topicId: number, token: string, tags: { brand_tier: BrandTier; industry_class: IndustryClass }) {
    return requestPut<ReportTags>(`/topics/${topicId}/report-tags`, token, tags);
  },
};
